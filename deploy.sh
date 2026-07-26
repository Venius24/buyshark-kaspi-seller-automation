#!/bin/bash
# ================================================================
# deploy.sh — Скрипт деплоя Kaspi SaaS на VPS
# Запускается на сервере из /opt/kaspi-saas после git pull
# ================================================================
set -e  # Останавливаемся при любой ошибке

APP_DIR="/root/buyshark"
SERVICE_NAME="kaspi-saas"
VENV_DIR="$APP_DIR/venv"
FRONTEND_DIR="$APP_DIR/frontend"

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

# ── 0.2. Автоматическая настройка PostgreSQL 16 ────────────────
if ! command -v psql &> /dev/null; then
  echo "🐘 PostgreSQL не найден. Устанавливаем PostgreSQL 16..."
  sudo apt-get update -q
  sudo apt-get install -y -q postgresql postgresql-contrib
  sudo systemctl enable --now postgresql
fi

if ! sudo -u postgres psql -lqt | cut -d \| -f 1 | grep -qw kaspi_saas; then
  echo "🐘 Создание базы данных PostgreSQL kaspi_saas..."
  sudo -u postgres psql -c "CREATE USER kaspi_user WITH PASSWORD 'kaspi_pass_2026';" || true
  sudo -u postgres psql -c "CREATE DATABASE kaspi_saas OWNER kaspi_user;" || true
  sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE kaspi_saas TO kaspi_user;" || true
  echo "✅ База данных kaspi_saas создана."
fi

if ! grep -q "DATABASE_URL" "$APP_DIR/.env" 2>/dev/null; then
  echo "DATABASE_URL=postgresql+asyncpg://kaspi_user:kaspi_pass_2026@localhost:5432/kaspi_saas" >> "$APP_DIR/.env"
fi

# ── 1. Обновление кода ──────────────────────────────────────────
BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD)}"
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"
echo "✅ Код обновлен."

# ── 2. Обновление Python зависимостей ───────────────────────────
echo "🐍 Шаг 2: Обновление Python зависимостей..."
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r requirements.txt -q
echo "🎭 Установка бинарных файлов браузера Playwright Chromium..."
"$VENV_DIR/bin/playwright" install --with-deps chromium
echo "✅ Python зависимости и Playwright Chromium установлены."

# ── 3. Сборка фронтенда ─────────────────────────────────────────
echo "💻 Шаг 3: Сборка React Frontend (bun)..."
cd "$FRONTEND_DIR"
bun install --frozen-lockfile
bun run build
cd "$APP_DIR"
echo "✅ Фронтенд собран в frontend/dist/"

# ── 4. Копирование dist в /var/www/ ─────────────────────────────
echo "🌐 Шаг 4: Публикация frontend в /var/www/kaspi-saas..."
sudo rm -rf /var/www/kaspi-saas
sudo cp -r "$FRONTEND_DIR/dist" /var/www/kaspi-saas
echo "✅ Фронтенд скопирован."

# ── 5. Перезапуск бекэнда через systemd ────────────────────────
echo "⚙️  Шаг 5: Настройка и перезапуск systemd сервиса $SERVICE_NAME..."
sudo cp "$APP_DIR/deploy/kaspi-saas.service" /etc/systemd/system/kaspi-saas.service
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
sleep 2

# ── 6. Проверка статуса ──────────────────────────────────────────
if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
  echo "✅ Сервис $SERVICE_NAME успешно запущен."
else
  echo "❌ ОШИБКА: Сервис $SERVICE_NAME не запустился!"
  sudo systemctl status "$SERVICE_NAME" --no-pager
  exit 1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎉 [$(date '+%Y-%m-%d %H:%M:%S')] Деплой завершен успешно!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
