# 🚀 Руководство по деплою Kaspi SaaS на VPS

## 📋 Стек

| Компонент | Технология |
|---|---|
| Backend | FastAPI + uvicorn |
| Frontend | React (Vite) → собирается в `dist/` |
| Process manager | systemd |
| Reverse proxy | nginx |
| CI/CD | GitHub Actions |

---

## 🖥️ Первичная настройка VPS (делается один раз)

### 1. Устанавливаем зависимости
```bash
sudo apt update && sudo apt install -y git python3.13 python3.13-venv nginx curl unzip
# Устанавливаем bun (для сборки React)
curl -fsSL https://bun.sh/install | bash
source ~/.bashrc
```

### 2. Клонируем репозиторий
```bash
sudo mkdir -p /opt/kaspi-saas
sudo chown $USER:$USER /opt/kaspi-saas
git clone https://github.com/Venius24/sbx_plwrght.git /opt/kaspi-saas
cd /opt/kaspi-saas
git checkout main
```

### 3. Создаём Python venv и устанавливаем зависимости
```bash
python3.13 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

### 4. Создаём файл `.env` с секретами
```bash
cat > /opt/kaspi-saas/.env << 'EOF'
API_TOKEN='ВАШ_KASPI_TOKEN'
TG_BOT_TOKEN='ВАШ_TELEGRAM_BOT_TOKEN'
EOF
chmod 600 /opt/kaspi-saas/.env
```

### 5. Собираем фронтенд
```bash
cd /opt/kaspi-saas/frontend
bun install
bun run build
sudo mkdir -p /var/www/kaspi-saas
sudo cp -r dist/* /var/www/kaspi-saas/
```

### 6. Устанавливаем systemd сервис
```bash
sudo cp /opt/kaspi-saas/deploy/kaspi-saas.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kaspi-saas
sudo systemctl status kaspi-saas
```

### 7. Настраиваем nginx
```bash
sudo cp /opt/kaspi-saas/deploy/nginx.conf /etc/nginx/sites-available/kaspi-saas
sudo ln -sf /etc/nginx/sites-available/kaspi-saas /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## 🔑 Настройка GitHub Actions Secrets

В репозитории GitHub → **Settings → Secrets → Actions** добавить:

| Секрет | Значение |
|---|---|
| `VPS_HOST` | IP или домен вашего VPS |
| `VPS_USER` | Имя SSH-пользователя (напр. `ubuntu`) |
| `VPS_SSH_KEY` | Содержимое `~/.ssh/id_rsa` (приватный SSH-ключ) |
| `VPS_PORT` | SSH порт (обычно `22`) |

> [!IMPORTANT]
> Публичный ключ (`id_rsa.pub`) должен быть в `~/.ssh/authorized_keys` на сервере.

---

## 🔄 Как работает CI/CD

```
git push (любая ветка)
        │
        ▼
  🧪 Job: test
  pytest tests/ -v
        │
  ❌ Fail → pipeline stop, deploy отменяется
  ✅ Pass ↓
        │
  (только при push в main)
        │
        ▼
  🚀 Job: deploy
  SSH → сервер → bash deploy.sh
        │
        ├── git pull origin main
        ├── pip install -r requirements.txt
        ├── bun install && bun run build
        ├── cp dist/ → /var/www/kaspi-saas
        └── systemctl restart kaspi-saas
```

---

## 🔧 Полезные команды на сервере

```bash
# Статус сервиса
sudo systemctl status kaspi-saas

# Логи в реальном времени
sudo journalctl -u kaspi-saas -f

# Ручной перезапуск
sudo systemctl restart kaspi-saas

# Ручной деплой
cd /opt/kaspi-saas && bash deploy.sh
```
