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

## ИИ: дашборд и сравнение недель

После применения конфигурации выше и деплоя кода скопировать
`provisioning/dashboards/json/buyshark-ai.json` в
`/root/job_hunter_bot/monitoring/grafana/dashboards/buyshark-ai.json`.
Grafana подхватит файл тем же provisioning, что и `buyshark-ops.json`.
Изменений конфигурации Prometheus и Grafana для нового дашборда не требуется.

Для сравнения двух полных недель выбрать точку оценки в конце второй недели
и выполнить в Prometheus/Grafana пары запросов. Например, число отправленных
ИИ-ответов за текущие и предыдущие семь суток:

```promql
sum(increase(chatbot_replies_total{job="kaspi-saas",kind="inbound_reply"}[7d]))
sum(increase(chatbot_replies_total{job="kaspi-saas",kind="inbound_reply"}[7d] offset 7d))
```

Для запусков ассистента заменить имя на `assistant_runs_total` и убрать
`kind`. По тому же принципу сравниваются токены, стоимость и ошибки. Долю
эскалаций считать как сумму `inbound_escalated_ai` и
`inbound_escalated_trigger`, делённую на сумму всех шести исходов
`chatbot_replies_total` за ту же неделю. В знаменатель входят также
шаблоны, пропуски из-за кредитов и неудачные отправки: это доля исходов
обработки, а не доля доставленных сообщений.

**Хранение Prometheus на VPS сейчас 1 неделя** (`--storage.tsdb.retention.time=1w`, проверено 2026-10-16).
Запросы с `[7d] offset 7d` при этом пусты: прошлой недели в Prometheus нет. Для сравнения двух недель
увеличить хранение до 30 дней: в `/root/job_hunter_bot/docker-compose.monitoring.yml` в команде сервиса
`prometheus` заменить значение на `30d` и пересоздать только контейнер Prometheus (данные остаются в volume).
До этого сравнивай по БД: `scripts/ai_baseline.py` и таблицы `chat_response_logs`, `assistant_runs`.

Prometheus хранит данные только с первого успешного скрейпа; предыдущую
неделю нельзя восстановить из метрик задним числом. Магазин сейчас один,
поэтому это сравнение по времени, не A/B-тест. Для исторической базы из БД
до установки метрик можно запустить `python scripts/ai_baseline.py --days 14`
с `DATABASE_URL` в окружении. Скрипт выполняет только SELECT и выводит
дневные агрегаты без текстов сообщений.
