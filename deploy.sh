#!/bin/bash
# ================================================================
# deploy.sh — Скрипт деплоя Kaspi SaaS на VPS
#
# Запуск вручную на сервере:
#   BRANCH=main DB_PASSWORD=*** bash deploy.sh
#
# Запуск из CI с уже собранным фронтендом (пропускает bun build):
#   BRANCH=main DB_PASSWORD=*** PREBUILT_DIST_DIR=/root/incoming-dist bash deploy.sh
#
# Откатывается автоматически на предыдущий рабочий коммит, если после
# деплоя health-check не проходит.
# ================================================================
set -e

APP_DIR="/root/buyshark"
SERVICE_NAME="kaspi-saas"
VENV_DIR="$APP_DIR/venv"
FRONTEND_DIR="$APP_DIR/frontend"
RELEASES_DIR="/var/www/kaspi-saas-releases"
WEB_ROOT="/var/www/kaspi-saas"
LANDING_DIR="$APP_DIR/buyshark-landing"
LANDING_RELEASES_DIR="/var/www/buyshark-landing-releases"
LANDING_WEB_ROOT="/var/www/buyshark-landing"
LANDING_TECH_DIR="$APP_DIR/buyshark-landing-tech"
LANDING_TECH_RELEASES_DIR="/var/www/buyshark-landing-tech-releases"
LANDING_TECH_WEB_ROOT="/var/www/buyshark-landing-tech"
HEALTH_URL="http://127.0.0.1:5000/"
LAST_GOOD_FILE="$APP_DIR/.last_good_sha"
HEALTH_RETRIES=45  # старт uvicorn (Playwright init и т.д.) занимает до ~30s

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚀 [$(date '+%Y-%m-%d %H:%M:%S')] Начало деплоя Kaspi SaaS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 0. Настройка SWAP (если не включен) ──────────────────────
if [ $(swapon --show | wc -l) -eq 0 ]; then
  echo "💾 SWAP не обнаружен. Настраиваем 2GB SWAP под Playwright..."
  sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  echo "✅ SWAP 2GB успешно подключен."
fi

# ── 0.1. Автоматическая настройка PostgreSQL 16 ────────────────
if ! command -v psql &> /dev/null; then
  echo "🐘 PostgreSQL не найден. Устанавливаем PostgreSQL 16..."
  sudo apt-get update -q
  sudo apt-get install -y -q postgresql postgresql-contrib
  sudo systemctl enable --now postgresql
fi

if ! sudo -u postgres psql -lqt | cut -d \| -f 1 | grep -qw kaspi_saas; then
  if [ -z "$DB_PASSWORD" ]; then
    echo "❌ DB_PASSWORD env var is required to bootstrap a fresh kaspi_saas database. Aborting." >&2
    exit 1
  fi
  echo "🐘 Создание базы данных PostgreSQL kaspi_saas..."
  sudo -u postgres psql -c "CREATE USER kaspi_user WITH PASSWORD '${DB_PASSWORD}';" || true
  sudo -u postgres psql -c "CREATE DATABASE kaspi_saas OWNER kaspi_user;" || true
  sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE kaspi_saas TO kaspi_user;" || true
  echo "✅ База данных kaspi_saas создана."
fi

if ! grep -q "DATABASE_URL" "$APP_DIR/.env" 2>/dev/null; then
  if [ -z "$DB_PASSWORD" ]; then
    echo "❌ DB_PASSWORD env var is required to write DATABASE_URL. Aborting." >&2
    exit 1
  fi
  echo "DATABASE_URL=postgresql+asyncpg://kaspi_user:${DB_PASSWORD}@localhost:5432/kaspi_saas" >> "$APP_DIR/.env"
fi

# ── 0.2. Подхватываем .env в окружение — нужен для alembic ──────
set -a
# shellcheck source=/dev/null
source "$APP_DIR/.env"
set +a

health_check() {
  local i
  for i in $(seq 1 "$HEALTH_RETRIES"); do
    if curl -sf "$HEALTH_URL" -o /dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

# ── 1. Обновление кода (с запоминанием точки отката) ────────────
mkdir -p "$APP_DIR"
cd "$APP_DIR"
if [ ! -d ".git" ]; then
  echo "📦 Первичная инициализация git-репозитория (свежий сервер)..."
  git init
  git remote add origin "https://github.com/Venius24/sbx_plwrght.git" || git remote set-url origin "https://github.com/Venius24/sbx_plwrght.git"
fi
PREV_SHA=$(git rev-parse HEAD 2>/dev/null || echo "")
BRANCH="${BRANCH:-${1:-$(git rev-parse --abbrev-ref HEAD)}}"
git fetch origin "$BRANCH"
NEW_SHA=$(git rev-parse "origin/$BRANCH")
echo "📦 Деплой $NEW_SHA (предыдущий: ${PREV_SHA:-нет — первый деплой})"
git reset --hard "origin/$BRANCH"
echo "✅ Код обновлён."

# ── 2. Python зависимости + миграции ─────────────────────────────
echo "🐍 Шаг 2: Обновление Python зависимостей..."
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r requirements.txt -q
echo "🎭 Установка бинарных файлов браузера Patchright Chromium..."
"$VENV_DIR/bin/patchright" install --with-deps chromium
echo "✅ Python зависимости и Patchright Chromium установлены."

echo "🗄️  Применение миграций Alembic..."
"$VENV_DIR/bin/alembic" upgrade head
echo "✅ Миграции применены."
# Откат ниже не делает alembic downgrade — миграции должны быть
# additive/обратно совместимыми (см. план Phase 4a), иначе откат кода
# при несовместимой схеме БД не спасёт.

# ── 3. Сборка фронтенда (или использование готового dist из CI) ──
if [ -n "$PREBUILT_DIST_DIR" ] && [ -d "$PREBUILT_DIST_DIR" ]; then
  echo "💻 Шаг 3: Используем уже собранный фронтенд из $PREBUILT_DIST_DIR"
  NEW_DIST_SRC="$PREBUILT_DIST_DIR"
else
  echo "💻 Шаг 3: Сборка React Frontend (bun)..."
  cd "$FRONTEND_DIR"
  bun install --frozen-lockfile
  bun run build
  cd "$APP_DIR"
  NEW_DIST_SRC="$FRONTEND_DIR/dist"
  echo "✅ Фронтенд собран в frontend/dist/"
fi

# ── 4. Атомная публикация фронтенда (версионированные релизы + symlink) ──
echo "🌐 Шаг 4: Публикация фронтенда..."
mkdir -p "$RELEASES_DIR"
NEW_RELEASE="$RELEASES_DIR/$NEW_SHA"
rm -rf "$NEW_RELEASE"
cp -r "$NEW_DIST_SRC" "$NEW_RELEASE"

# На первом запуске после этого изменения WEB_ROOT ещё может быть настоящей
# директорией (старый layout), а не symlink — переносим один раз.
PREV_RELEASE_TARGET=""
if [ -L "$WEB_ROOT" ]; then
  PREV_RELEASE_TARGET=$(readlink -f "$WEB_ROOT" 2>/dev/null || echo "")
elif [ -e "$WEB_ROOT" ]; then
  echo "⚠️  $WEB_ROOT — обычная директория (старый layout), переносим на symlink-схему."
  rm -rf "$WEB_ROOT"
fi

ln -sfn "$NEW_RELEASE" "$WEB_ROOT"
echo "✅ Symlink $WEB_ROOT → $NEW_RELEASE (без даунтайма на статике)."

# ── 4.5. Сборка + атомная публикация лендинга (buyshark-landing) ─
# Чисто статический сайт, не завязан на backend/health-check/откат —
# независимая публикация, не должна блокировать деплой кабинета при сбое.
if [ -n "$PREBUILT_LANDING_DIST_DIR" ] && [ -d "$PREBUILT_LANDING_DIST_DIR" ]; then
  echo "🦈 Шаг 4.5: Используем уже собранный лендинг из $PREBUILT_LANDING_DIST_DIR"
  NEW_LANDING_DIST_SRC="$PREBUILT_LANDING_DIST_DIR"
elif [ -d "$LANDING_DIR" ]; then
  echo "🦈 Шаг 4.5: Сборка лендинга buyshark-landing (bun)..."
  cd "$LANDING_DIR"
  bun install --frozen-lockfile
  bun run build
  cd "$APP_DIR"
  NEW_LANDING_DIST_SRC="$LANDING_DIR/dist"
  echo "✅ Лендинг собран в buyshark-landing/dist/"
else
  NEW_LANDING_DIST_SRC=""
fi

if [ -n "$NEW_LANDING_DIST_SRC" ]; then
  mkdir -p "$LANDING_RELEASES_DIR"
  NEW_LANDING_RELEASE="$LANDING_RELEASES_DIR/$NEW_SHA"
  rm -rf "$NEW_LANDING_RELEASE"
  cp -r "$NEW_LANDING_DIST_SRC" "$NEW_LANDING_RELEASE"

  # buyshark-landing/public/media/ (~43MB видео) в .gitignore — ни в git,
  # ни в CI-чекауте его нет, поэтому и CI-собранный, и локально-собранный
  # dist приезжают сюда БЕЗ media/. Донакладываем его с постоянного
  # каталога на VPS, который не трогает git/деплой. Обновлять новые
  # ассеты — scp'ом в /root/buyshark-landing-media/ на сервере.
  LANDING_MEDIA_PERSIST_DIR="/root/buyshark-landing-media"
  if [ -d "$LANDING_MEDIA_PERSIST_DIR" ]; then
    mkdir -p "$NEW_LANDING_RELEASE/media"
    cp -r "$LANDING_MEDIA_PERSIST_DIR"/. "$NEW_LANDING_RELEASE/media/"
    echo "✅ media/ донакоплен из $LANDING_MEDIA_PERSIST_DIR."
  else
    echo "⚠️  $LANDING_MEDIA_PERSIST_DIR не найден — лендинг будет опубликован БЕЗ видео/media (404 на все сцены)."
  fi

  ln -sfn "$NEW_LANDING_RELEASE" "$LANDING_WEB_ROOT"
  ls -1dt "$LANDING_RELEASES_DIR"/*/ 2>/dev/null | tail -n +6 | xargs -r rm -rf
  echo "✅ Symlink $LANDING_WEB_ROOT → $NEW_LANDING_RELEASE."
else
  echo "⚠️  Пропускаем публикацию лендинга — нет ни $LANDING_DIR, ни PREBUILT_LANDING_DIST_DIR."
fi

# ── 4.6. Сборка + атомная публикация старого лендинга (buyshark-landing-tech,
# tech.buyshark.kz) — та же логика, что и 4.5, независимая публикация.
if [ -n "$PREBUILT_LANDING_TECH_DIST_DIR" ] && [ -d "$PREBUILT_LANDING_TECH_DIST_DIR" ]; then
  echo "🦈 Шаг 4.6: Используем уже собранный лендинг-tech из $PREBUILT_LANDING_TECH_DIST_DIR"
  NEW_LANDING_TECH_DIST_SRC="$PREBUILT_LANDING_TECH_DIST_DIR"
elif [ -d "$LANDING_TECH_DIR" ]; then
  echo "🦈 Шаг 4.6: Сборка лендинга buyshark-landing-tech (bun)..."
  cd "$LANDING_TECH_DIR"
  bun install --frozen-lockfile
  bun run build
  cd "$APP_DIR"
  NEW_LANDING_TECH_DIST_SRC="$LANDING_TECH_DIR/dist"
  echo "✅ Лендинг-tech собран в buyshark-landing-tech/dist/"
else
  NEW_LANDING_TECH_DIST_SRC=""
fi

if [ -n "$NEW_LANDING_TECH_DIST_SRC" ]; then
  mkdir -p "$LANDING_TECH_RELEASES_DIR"
  NEW_LANDING_TECH_RELEASE="$LANDING_TECH_RELEASES_DIR/$NEW_SHA"
  rm -rf "$NEW_LANDING_TECH_RELEASE"
  cp -r "$NEW_LANDING_TECH_DIST_SRC" "$NEW_LANDING_TECH_RELEASE"

  # Тот же трюк с media/, что и для buyshark-landing — своя постоянная
  # директория на VPS, не пересекается с /root/buyshark-landing-media.
  LANDING_TECH_MEDIA_PERSIST_DIR="/root/buyshark-landing-tech-media"
  if [ -d "$LANDING_TECH_MEDIA_PERSIST_DIR" ]; then
    mkdir -p "$NEW_LANDING_TECH_RELEASE/media"
    cp -r "$LANDING_TECH_MEDIA_PERSIST_DIR"/. "$NEW_LANDING_TECH_RELEASE/media/"
    echo "✅ media/ донакоплен из $LANDING_TECH_MEDIA_PERSIST_DIR."
  else
    echo "⚠️  $LANDING_TECH_MEDIA_PERSIST_DIR не найден — лендинг-tech будет опубликован БЕЗ видео/media (404 на все сцены)."
  fi

  ln -sfn "$NEW_LANDING_TECH_RELEASE" "$LANDING_TECH_WEB_ROOT"
  ls -1dt "$LANDING_TECH_RELEASES_DIR"/*/ 2>/dev/null | tail -n +6 | xargs -r rm -rf
  echo "✅ Symlink $LANDING_TECH_WEB_ROOT → $NEW_LANDING_TECH_RELEASE."
else
  echo "⚠️  Пропускаем публикацию лендинга-tech — нет ни $LANDING_TECH_DIR, ни PREBUILT_LANDING_TECH_DIST_DIR."
fi

# ── 5. Перезапуск бекенда через systemd ──────────────────────────
echo "⚙️  Шаг 5: Настройка и перезапуск systemd сервиса $SERVICE_NAME..."
sudo cp "$APP_DIR/deploy/kaspi-saas.service" /etc/systemd/system/kaspi-saas.service
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

# ── 6. Health-check с автоматическим откатом ─────────────────────
echo "🩺 Шаг 6: Health-check ($HEALTH_URL, до ${HEALTH_RETRIES}s)..."
if health_check; then
  echo "✅ Health-check пройден."
  echo "$NEW_SHA" > "$LAST_GOOD_FILE"
  # Оставляем последние 5 релизов фронтенда, остальные подчищаем
  ls -1dt "$RELEASES_DIR"/*/ 2>/dev/null | tail -n +6 | xargs -r rm -rf
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "🎉 [$(date '+%Y-%m-%d %H:%M:%S')] Деплой $NEW_SHA завершён успешно!"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
else
  echo "❌ Health-check НЕ пройден для $NEW_SHA."
  sudo systemctl status "$SERVICE_NAME" --no-pager || true

  if [ -z "$PREV_SHA" ]; then
    echo "🔥 Нет точки отката (это был первый деплой) — требуется ручное вмешательство." >&2
    exit 1
  fi

  echo "↩️  Откат кода на $PREV_SHA..."
  git reset --hard "$PREV_SHA"
  "$VENV_DIR/bin/pip" install -r requirements.txt -q
  "$VENV_DIR/bin/alembic" upgrade head || true

  if [ -n "$PREV_RELEASE_TARGET" ] && [ -d "$PREV_RELEASE_TARGET" ]; then
    ln -sfn "$PREV_RELEASE_TARGET" "$WEB_ROOT"
    echo "↩️  Фронтенд откачен на $PREV_RELEASE_TARGET."
  fi

  sudo systemctl restart "$SERVICE_NAME"

  if health_check; then
    echo "⚠️  Откат на $PREV_SHA прошёл успешно. Деплой $NEW_SHA ОТКЛОНЁН — проверьте код перед повторной попыткой." >&2
    exit 1
  else
    echo "🔥 КРИТИЧНО: откат тоже не прошёл health-check. Нужно ручное вмешательство немедленно." >&2
    sudo systemctl status "$SERVICE_NAME" --no-pager || true
    exit 1
  fi
fi
