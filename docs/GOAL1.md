Вот теперь всё встало на свои места! Поздравляю с расшифровкой стейт-машины Kaspi — это был ключевой фрагмент пазла.

Заказ `957847801` вернул `courierTransmissionDate: 1782819795000`. В интерфейсе Кабинета (`https://kaspi.kz/mc/#/orders?status=KASPI_DELIVERY_TRANSMITTED`) он находится во вкладке **«Переданы на доставку»**, потому что курьер его **уже забрал и отсканировал**.

Давай финально зафиксируем точную логику статусов, чтобы твой скрипт никогда не путал «Передачу» (где нужно печатать накладную) с «Переданными» (где курьер уже уехал).

---

## 🧭 Полная матрица состояний заказов Kaspi Delivery

В REST API Kaspi заказы на этапах сборки и логистики имеют **одинаковые** верхнеуровневые параметры:
`state = KASPI_DELIVERY` и `status = ACCEPTED_BY_MERCHANT`.

Разделение по вкладкам Личного Кабинета происходит **строго по двум флагам**:

| Вкладка в Кабинете Kaspi (`URL status`) | `assembled` | `courierTransmissionDate` | Что происходит / Действие воркера |
| --- | --- | --- | --- |
| **«На сборке»** (`KASPI_DELIVERY_CARGO_ASSEMBLY`) | `false` | `null` | Заказ только упаковывается. Нужно отправить `POST` запрос на сборку в `MC API`. |
| **«Передача курьеру» / «Ожидает»** (`KASPI_DELIVERY_WAIT_FOR_COURIER`) | `true` | `null` | **Заказ собран, но еще у тебя на складе!** Здесь забираем PDF-ссылку `waybill` и печатаем накладную. |
| **«Переданы на доставку»** (`KASPI_DELIVERY_TRANSMITTED`) | `true` | `TIMESTAMP` *(не null)* | **Заказ у курьера.** Посылка физически уехала, процесс обработки завершен. |

---

## 🛠 Готовый Python-код для разграничения в твоем бэкенде

Чтобы твой FastAPI/Python сервис точно раскладывал заказы по нужным корзинам:

```python
def classify_kaspi_orders(orders_json):
    cargo_assembly = []  # На сборке
    wait_for_courier = []  # Ожидает курьера (Передача)
    transmitted = []  # Переданы на доставку

    for order in orders_json.get("data", []):
        attr = order.get("attributes", {})
        code = attr.get("code")
        is_assembled = attr.get("assembled", False)

        kaspi_delivery = attr.get("kaspiDelivery") or {}
        transmission_date = kaspi_delivery.get("courierTransmissionDate")

        # 1. На сборке
        if not is_assembled:
            cargo_assembly.append(order)

        # 2. Ожидает курьера (Нужно печатать накладную)
        elif is_assembled and transmission_date is None:
            wait_for_courier.append(
                {
                    "code": code,
                    "waybill_url": kaspi_delivery.get("waybill"),
                    "waybill_number": kaspi_delivery.get("waybillNumber"),
                }
            )

        # 3. Переданы на доставку (Курьер забрал)
        elif is_assembled and transmission_date is not None:
            transmitted.append(code)

    return {
        "assembly": cargo_assembly,
        "wait_courier": wait_for_courier,
        "transmitted": transmitted,
    }

```

Теперь у нас есть **100% точность**: мы знаем, какой URL в веб-интерфейсе отвечает за каждый статус, и как это мгновенно фильтровать в коде.