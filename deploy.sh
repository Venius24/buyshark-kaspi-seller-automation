#!/bin/bash
# ================================================================
# deploy.sh — Скрипт деплоя Kaspi SaaS на VPS
# Запускается на сервере из /opt/kaspi-saas после git pull
# ================================================================
set -e  # Останавливаемся при любой ошибке

APP_DIR="/opt/kaspi-saas"
SERVICE_NAME="kaspi-saas"
VENV_DIR="$APP_DIR/venv"
FRONTEND_DIR="$APP_DIR/frontend"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚀 [$(date '+%Y-%m-%d %H:%M:%S')] Начало деплоя Kaspi SaaS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

cd "$APP_DIR"

# ── 1. Обновление кода ──────────────────────────────────────────
echo "📦 Шаг 1: Получение последнего кода..."
git fetch origin
git pull origin main
echo "✅ Код обновлен."

# ── 2. Обновление Python зависимостей ───────────────────────────
echo "🐍 Шаг 2: Обновление Python зависимостей..."
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r requirements.txt -q
echo "✅ Python зависимости установлены."

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
echo "⚙️  Шаг 5: Перезапуск systemd сервиса $SERVICE_NAME..."
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
