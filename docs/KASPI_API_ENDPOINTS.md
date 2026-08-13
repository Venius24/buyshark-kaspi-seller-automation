## 2. Эндпоинт: Получение списка заказов (GET /orders)

### 2.1 Описание
Основной метод для мониторинга, фильтрации и выгрузки заказов магазина. Использует стандарт **JSON:API**. Позволяет получать как новые заказы для обработки, так и архивные данные для аналитики и репрайсинга.

### 2.2 HTTP Сигнатура
* **Метод:** `GET`
* **URL:** `https://kaspi.kz/shop/api/v2/orders`

### 2.3 Обязательные Заголовки (Headers)
* `Content-Type: application/vnd.api+json`
* `X-Auth-Token: {{merchant_api_token}}`
* `User-Agent: Mozilla/5.0` (Рекомендуется для обхода жестких TLS-дропов со стороны файрвола Kaspi)

### 2.4 Параметры запроса (Query Parameters)

| Параметр | Тип | Обязательный | Описание | Пример |
| :--- | :--- | :--- | :--- | :--- |
| `page[size]` | `int` | Нет | Количество заказов на одну страницу (макс. 100) | `1` |
| `page[number]` | `int` | Нет | Номер запрашиваемой страницы (индексация с 0) | `0` |
| `filter[orders][creationDate][$ge]` | `long` | **Да** | Временная метка "ОТ" (Unix Timestamp в миллисекундах, 13 знаков) | `1783300000000` |
| `filter[orders][creationDate][$le]` | `long` | Нет | Временная метка "ДО" (Unix Timestamp в миллисекундах, 13 знаков) | `1783700000000` |
| `filter[orders][state]` | `string` | Нет | Состояние заказа (`NEW`, `ARCHIVE`, `DELIVERY`, `PICKUP`) | `ARCHIVE` |
| `filter[orders][status]` | `string` | Нет | Статус банка/мерчанта (`APPROVED_BY_BANK`, `COMPLETED`, `CANCELLED`) | `COMPLETED` |

---

### 2.4-bis ⚠️ Определение вида доставки — наши прежние обозначения были НЕВЕРНЫ

Реверс на живых данных Бархана 2026-08-13 (60 заказов в `state=PICKUP`, 600 в `KASPI_DELIVERY`). Счёт совпал с тем, что владелец видит в кабинете, — это и есть подтверждение.

**Ключевая ошибка:** поле `deliveryMode` **не различает** постамат и самовывоз — у обоих оно равно `DELIVERY_PICKUP`. Поле `pickupPointId` тоже не различитель: оно заполнено у обоих и содержит точку продавца (`20186986_001`), а не идентификатор постамата.

**Настоящий различитель — `attributes.isKaspiDelivery`** (дублируется наличием блока `attributes.kaspiDelivery`):

| `deliveryMode` | `isKaspiDelivery` | `kaspiDelivery` | Смысл | Замер 13.08 |
| :--- | :--- | :--- | :--- | :--- |
| `DELIVERY_PICKUP` | `true` | объект | Kaspi везёт до постамата/пункта выдачи | 51 в `PICKUP`, 478 в `KASPI_DELIVERY` |
| `DELIVERY_PICKUP` | `false` | `null` | **Самовывоз клиентом из точки продавца** | **8** в `PICKUP` |
| `DELIVERY_REGIONAL_TODOOR` | `true` | объект | Межгородская курьерская до двери | 121 |
| `DELIVERY_LOCAL` | `true` | объект | Локальная доставка по городу | 1 |

Логика в `backend/kaspi_wrapper.py::_parse_delivery_type` до 2026-08-13 возвращала `"Postomat"` для любого `DELIVERY_PICKUP` (строка `dm == "DELIVERY_PICKUP"` в проверке на постамат). Следствие: **самовывоз не существовал в системе как класс**, а кампания «Самовывоз» не могла сработать ни при каких условиях.

Самовывозные заказы живут в `state=PICKUP` со `status=ACCEPTED_BY_MERCHANT`. Состояние `DELIVERY` у этого магазина всегда пустое.

---

### 2.5 Пример эталонного cURL-запроса (CLI)
```bash
curl -g -i -m 10 '[https://kaspi.kz/shop/api/v2/orders?page](https://kaspi.kz/shop/api/v2/orders?page)[size]=1&page[number]=0&filter[orders][creationDate][$ge]=1783300000000&filter[orders][creationDate][$le]=1783700000000' \
  -H "User-Agent: Mozilla/5.0" \
  -H "X-Auth-Token: YOUR_API_TOKEN" \
  -H "Content-Type: application/vnd.api+json"


  ### ⚠️ Валидация и ограничения фильтров (Лимиты на интервалы дат)

При фильтрации по датам (`filter[orders][creationDate]`) действует строгое серверное ограничение на диапазон поиска:
* **Максимальный интервал:** **14 дней** между стартовой точкой (`$ge`) и текущим моментом (или конечной точкой `$le`).
* **Пример ошибки при превышении лимита (например, при попытке запросить за месяц):**
  ```json
  {"errors":[{"title":"Exceeded the maximum difference for the filter[orders][creationDate] (in days). Actual [31], max [14]."}]}



## 3. Эндпоинт: Получение состава позиций заказа (GET /orders/{id}/entries)

### 3.1 Описание
Используется для получения детальной информации о товарах внутри конкретного заказа. Этот шаг критически важен на этапе сборки (`KASPI_DELIVERY_CARGO_ASSEMBLY`), так как позволяет ИИ-агенту или WMS-системе склада узнать внутренний артикул (Merchant SKU), название товара, категорию и количество штук для фактической упаковки.

### 3.2 HTTP Сигнатура
* **Метод:** `GET`
* **URL:** `https://kaspi.kz/shop/api/v2/orders/{order_id}/entries`
* *Здесь `{order_id}` — это строковый Base64 ID заказа (например, `OTgwNzEwNzQy`), полученный из корневого запроса `/orders`.*

### 3.3 Обязательные Заголовки (Headers)
* `Content-Type: application/vnd.api+json`
* `X-Auth-Token: {{merchant_api_token}}`
* `User-Agent: Mozilla/5.0`

---

### 3.4 Пример cURL-запроса
```bash
curl -g -i -m 10 '[https://kaspi.kz/shop/api/v2/orders/OTgwNzEwNzQy/entries](https://kaspi.kz/shop/api/v2/orders/OTgwNzEwNzQy/entries)' \
  -H "User-Agent: Mozilla/5.0" \
  -H "X-Auth-Token: YOUR_API_TOKEN" \
  -H "Content-Type: application/vnd.api+json"



Перевод заказа в «Передачу» (Сборка)
1. Спецификация HTTP-запроса
Чтобы перевести заказ во вкладку «Передача курьеру» (Ожидает курьера), необходимо отправить POST запрос на внутренний эндпоинт Kaspi.

Метод: POST

URL: https://mc.shop.kaspi.kz/mc/api/order/cargo/assembled?_m=20186986

2. Обязательные Headers (Заголовки)
Бэкенд Kaspi сбросит запрос, если не передать эти заголовки:

content-type: application/json

x-auth-version: 3

user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 OPR/132.0.0.0

origin: https://kaspi.kz

referer: https://kaspi.kz/

3. Авторизация (Cookies)
Запрос авторизуется через ключ -b (Cookie). Значения берутся из активной сессии браузера:

HTTP
mc-session=1783691051.759.66858.503399|825e5f3659dba1ed7b5d7b2cbf5f1012; mc-sid=115c2a20-df81-4277-b3fc-f3a0e07f7110;
⚠️ Важно для автоматизации: Эти куки временные. Если скрипт возвращает ошибку авторизации, их нужно обновить через Playwright.

4. Структура Payload (Тело запроса)
Данные отправляются в формате JSON через параметр --data-raw.

Шаблон JSON:
JSON
{
  "cargos": [
    {
      "orderCode": "929499993",
      "newCargoSpace": 1,
      "quantity": 1
    }
  ]
}
Справочник полей:
orderCode (строка) — Номер заказа из личного кабинета (например, "929499993").

newCargoSpace (число) — Количество мест/коробок, в которые упакован заказ. Всегда ставим 1.

quantity (число) — Общее количество единиц товара в этом заказе (если заказано 2 бутылки, ставим 2).

5. Шаблон для масштабирования (Пакетный перевод)
Если нужно перевести в передачу сразу несколько заказов одним махом, просто добавляй их объектами в массив "cargos":

JSON
{
  "cargos": [
    {"orderCode": "929499993", "newCargoSpace": 1, "quantity": 1},
    {"orderCode": "929416620", "newCargoSpace": 1, "quantity": 2},
    {"orderCode": "929430168", "newCargoSpace": 1, "quantity": 1}
  ]
}
6. Результат выполнения
Успех: Сервер возвращает статус 200 OK. Заказ мгновенно пропадает из вкладки «На сборке» и появляется во вкладке «Ожидает курьера».

Ошибка 500: Неверно указано поле quantity, либо этот заказ уже был собран ранее.




# Регламент интеграции и автоматизации Kaspi Shop API (v2 & MC)

Данный документ описывает проверенную архитектуру взаимодействия с бэкендом Kaspi.kz для автоматизации полного жизненного цикла заказов (Kaspi Доставка и Постаматы).

---

## 1. Сбор данных: Получение списка заказов «В сборке»

Для мониторинга и получения структуры заказов используется официальный публичный REST API. 

* **Метод:** `GET`
* **Эндпоинт:** `https://kaspi.kz/shop/api/v2/orders`
* **Авторизация:** Заголовок `X-Auth-Token: {{API_TOKEN}}` (Токен бессрочный)

### ⚠️ Критические ограничения фильтрации:
1. **Лимит 14 дней:** Параметр `filter[orders][creationDate][$ge]` обязан передавать Timestamp (в миллисекундах) **не старше 14 дней** от текущей даты. В противном случае бэкенд возвращает ошибку `400 Bad Request`.
2. **Стейт-машина:** Для поиска заказов, ожидающих упаковки на складе, необходимо жестко комбинировать параметры состояния.

### Пример cURL-запроса для мониторинга:
```bash
curl -g -X GET '[https://kaspi.kz/shop/api/v2/orders](https://kaspi.kz/shop/api/v2/orders)?\
page[size]=20&\
page[number]=0&\
filter[orders][state]=KASPI_DELIVERY&\
filter[orders][status]=ACCEPTED_BY_MERCHANT&\
filter[orders][creationDate][$ge]=1782432000000' \
  -H "X-Auth-Token: YOUR_PUBLIC_TOKEN" \
  -H "Content-Type: application/vnd.api+json"





## 6. Получение актуальных накладных из вкладки «Передача»

Для скачивания накладных и маркировки заказов, находящихся в статусе «Ожидает курьера», используется официальный API.

* **Метод:** `GET`
* **URL:** `https://kaspi.kz/shop/api/v2/orders?filter[orders][state]=KASPI_DELIVERY&filter[orders][status]=ACCEPTED_BY_MERCHANT&filter[orders][assembled]=true&filter[orders][creationDate][$ge]={{TODAY_TIMESTAMP}}`

### 💡 Логика фильтрации на стороне скрипта (Отсечение архива):
Из полученного списка необходимо программно отбирать только те объекты, у которых:
`attributes.kaspiDelivery.courierTransmissionDate == null`

### Ценность данных для воркера:
Для каждого отфильтрованного заказа скрипт забирает:
1. `attributes.kaspiDelivery.waybillNumber` — для генерации штрихкода внутри системы.
2. `attributes.kaspiDelivery.waybill` — прямая ссылка на PDF-документ для автоматической отправки на термопринтер.



## 7. Синхронизация цен и остатков (XML-фид)

REST API Kaspi v2 не поддерживает точечное изменение цен. Любое обновление прайса и остатков выполняется пакетно через XML-фид.

* **Тип интеграции:** Pull-модель (Kaspi запрашивает файл по URL мерчанта).
* **Формат даты в корне:** `DD.MM.YYYY HH:MM:SS`
* **Периодичность парсинга роботом Kaspi:** Каждые 15–30 минут.

### Правила валидации для ИИ-агента / Репрайсера:
1. Цена (`<price>`) передается строго целым числом без дробной части.
2. Идентификатор склада (`storeId`) должен соответствовать системному коду точки продаж мерчанта.
3. Если репрайсер зафиксировал демпинг ниже критического порога (себестоимости), скрипт обязан остановить падение на минимально допустимой цене, чтобы не торговать в минус.





Структура XML-ответа (Шаблон генерации)
Твой эндпоинт должен возвращать валидный XML-документ со строгим типом содержимого application/xml.

Чистый рабочий XML:
XML
<?xml version="1.0" encoding="utf-8"?>
<kaspi_catalog date="11.07.2026 00:50:00" xmlns="http://chat.carstyle.kz/kaspi_catalog" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
    <company>ИП Твой Мерчант</company>
    <merchantid>20186986</merchantid>
    <offers>
        <offer sku="RIDERACE-RR566">
            <model>Педали RIDERACE RR566 Черные</model>
            <brand>RIDERACE</brand>
            <availabilities>
                <availability available="yes" storeId="20186986_001" stock="5"/>
            </availabilities>
            <price>14500</price>
        </offer>
        <offer sku="FIKS-01">
            <model>Грипсы fiks-01</model>
            <brand>FIKS</brand>
            <availabilities>
                <availability available="yes" storeId="20186986_001" stock="12"/>
            </availabilities>
            <price>2900</price>
        </offer>
    </offers>
</kaspi_catalog>
3. Критические требования к полям данных
date в корневом теге: Обязан обновляться при каждом запросе робота Kaspi. Формат строго: ДД.ММ.ГГГГ ЧЧ:ММ:СС. Если Kaspi увидит старую дату, файл посчитается архивным и цены не изменятся.

sku: Твой уникальный артикул товара, привязанный к карточке в Кабинете.

storeId: Идентификатор твоего склада. Из логов твоих заказов мы вытащили твой боевой ID склада: 20186986_001. Передавать нужно именно его.

stock: Остаток товара. Если упал в 0 — автоматически ставится available="no", и товар скрывается с витрины во избежание штрафных отмен.

price: Целое число (цена в тенге). Без копеек, точек и пробелов.




# Kaspi Integration Documentation (v2 & MC API)

## 1. Auth & Strategy
- Public REST API: Used for GET requests (orders, waybills). Auth via X-Auth-Token (Permanent).
- Internal MC API: Used for POST actions (assembly). Auth via Session Cookies (Dynamic via Playwright).

## 2. Order Management Flow
1. Fetch new: GET /shop/api/v2/orders (state=KASPI_DELIVERY, status=ACCEPTED_BY_MERCHANT, date >= 14 days ago).
2. Assemble batch: POST https://mc.shop.kaspi.kz/mc/api/order/cargo/assembled?_m={merchant_id}
   Payload: {"cargos": [{"orderCode": "...", "newCargoSpace": 1, "quantity": X}]}
3. Print Waybills: GET /shop/api/v2/orders (assembled=true) -> Filter courierTransmissionDate == null -> Extract waybill PDF link.

## 3. Inventory & Price Sync
- Method: XML feed endpoint served by backend.
- Format: <kaspi_catalog date="DD.MM.YYYY HH:MM:SS"> with <offer sku="..."> and <price>.


Как теперь раскладывать все типы упаковки (Экспресс / Сегодня / Завтра):
Меняй поля в payload:

🔥 Экспресс-упаковка (5 шт):

JSON
{"express": true, "courierTransmitted": false, "status": "KASPI_DELIVERY_CARGO_ASSEMBLY"}
⚡ Обычная упаковка (Сегодня / Завтра):

JSON
{"express": false, "courierTransmitted": false, "status": "KASPI_DELIVERY_CARGO_ASSEMBLY"}
🏷️ Ожидает курьера (Передача):

JSON
{"courierTransmitted": false, "status": "KASPI_DELIVERY_WAIT_FOR_COURIER"}