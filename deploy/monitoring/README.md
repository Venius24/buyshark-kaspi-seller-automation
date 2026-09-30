# Мониторинг BuyShark

На VPS уже запущены Prometheus и Grafana в проекте `job_hunter_bot`.
BuyShark добавляется в них как второй источник метрик. Новые контейнеры,
образы, volumes, базы данных и открытые порты не нужны.

FastAPI отдаёт `/metrics` на `127.0.0.1:5000`. Nginx проксирует этот путь
только для существующей Docker-сети `172.19.0.0/16`. В Prometheus контейнере
`app.buyshark.kz` направлен на Docker host gateway, поэтому запрос идёт
напрямую к Nginx без Cloudflare и остаётся с корректным HTTPS host name.

## Конфигурация VPS

1. Применить изменение из `deploy/nginx.conf` к активному
   `/etc/nginx/sites-enabled/kaspi-saas`, затем `nginx -t` и reload.
2. В сервис `prometheus` файла
   `/root/job_hunter_bot/docker-compose.monitoring.yml` добавить
   `extra_hosts: ["app.buyshark.kz:host-gateway"]`.
3. В `/root/job_hunter_bot/monitoring/prometheus.yml` добавить job из
   `prometheus-job.yml`. После проверки конфигурации пересоздать только
   контейнер Prometheus; его данные останутся в существующем volume.
4. Скопировать `provisioning/dashboards/json/buyshark-ops.json` в
   `/root/job_hunter_bot/monitoring/grafana/dashboards/buyshark-ops.json`.
   Grafana подхватит файл без нового контейнера.

Проверка: `up{job="kaspi-saas"}` в Grafana должен стать `1` в течение минуты.
Дашборд находится в папке `Job Hunter` существующей Grafana на VPS.
Для доступа с компьютера: `ssh -L 3001:127.0.0.1:3001 USER@VPS_HOST`, затем
`http://127.0.0.1:3001`.

Docker-сеть может получить другой subnet после полного удаления и создания
Compose-проекта. В таком случае нужно обновить `allow` в Nginx, проверив
`docker network inspect job_hunter_bot_default`.
