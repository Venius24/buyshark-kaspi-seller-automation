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

> [!IMPORTANT]
> Перед настройкой nginx замените `yourdomain.com` в `deploy/nginx.conf` на ваш реальный домен!

---

## 🌐 Домен + SSL (HTTPS бесплатно через Let's Encrypt)

### Шаг 1: DNS запись
В панели вашего DNS-регистратора добавьте A-запись:
```
yourdomain.com     →  <IP вашего VPS>
www.yourdomain.com →  <IP вашего VPS>
```
Подождите ~5 минут для распространения DNS.

### Шаг 2: Установить Certbot
```bash
sudo apt install -y certbot python3-certbot-nginx
```

### Шаг 3: Получить SSL-сертификат (один раз)
```bash
# Сначала задеплойте nginx БЕЗ ssl (http only) и проверьте что домен открывается
# Затем запустите certbot — он сам добавит SSL в nginx.conf
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
```
Certbot спросит email (для уведомлений об истечении) и автоматически:
- Получит сертификат от Let's Encrypt
- Подправит `/etc/nginx/sites-available/kaspi-saas`
- Настроит автоматическое обновление сертификата раз в 90 дней

### Шаг 4: Проверить автообновление
```bash
sudo certbot renew --dry-run
```

### Шаг 5: Обновить `nginx.conf` в репозитории
Замените `yourdomain.com` в [`deploy/nginx.conf`](file:///f:/Dev/sbx/deploy/nginx.conf) на свой реальный домен и закоммитьте.

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
