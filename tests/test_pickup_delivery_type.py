"""
Самовывоз против постамата (2026-08-13, HANDOFF 3.46).

Владелец сообщил, что у него 8 самовывозов, а система показывала ноль и 59
«постаматов». Причина: `deliveryMode` у обоих равен DELIVERY_PICKUP, и разбор
считал постаматом ВСЁ. Настоящий различитель — `isKaspiDelivery`.

Наборы атрибутов ниже сняты с живых заказов Бархана.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.append(os.getcwd())

from backend.kaspi_wrapper import KaspiAPI
from backend.services import stock_sync_worker as worker

STORE = "store-test"


def dtype(attr, kaspi_deliv=None):
    return KaspiAPI._parse_delivery_type(attr, kaspi_deliv or {})


class TestDeliveryTypeParsing(unittest.TestCase):
    def test_self_pickup_has_no_kaspi_delivery_block(self):
        """8 заказов из 60 в state=PICKUP: Kaspi не везёт, клиент забирает сам."""
        attr = {"deliveryMode": "DELIVERY_PICKUP", "isKaspiDelivery": False,
                "pickupPointId": "20186986_001", "state": "PICKUP"}
        self.assertEqual(dtype(attr, None), "Самовывоз")

    def test_postomat_is_carried_by_kaspi(self):
        """51 заказ из тех же 60: тот же deliveryMode, но везёт Kaspi."""
        attr = {"deliveryMode": "DELIVERY_PICKUP", "isKaspiDelivery": True,
                "pickupPointId": "20186986_001", "state": "PICKUP"}
        self.assertEqual(dtype(attr, {"waybillNumber": "123"}), "Postomat")

    def test_pickup_point_id_alone_proves_nothing(self):
        """pickupPointId заполнен у обоих — это точка продавца, не постамат."""
        same_point = "20186986_001"
        pickup = {"deliveryMode": "DELIVERY_PICKUP", "isKaspiDelivery": False, "pickupPointId": same_point}
        postomat = {"deliveryMode": "DELIVERY_PICKUP", "isKaspiDelivery": True, "pickupPointId": same_point}
        self.assertNotEqual(dtype(pickup, None), dtype(postomat, {"waybill": "x"}))

    def test_regional_todoor_is_kaspi_delivery(self):
        attr = {"deliveryMode": "DELIVERY_REGIONAL_TODOOR", "isKaspiDelivery": True}
        self.assertEqual(dtype(attr, {"waybill": "x"}), "Kaspi Доставка")

    def test_local_delivery(self):
        attr = {"deliveryMode": "DELIVERY_LOCAL", "isKaspiDelivery": True}
        self.assertEqual(dtype(attr, {"waybill": "x"}), "Kaspi Доставка")

    def test_express_still_wins(self):
        attr = {"deliveryMode": "DELIVERY_PICKUP", "isKaspiDelivery": False, "express": True}
        self.assertEqual(dtype(attr, None), "Express")

    def test_explicit_postomat_flag_is_respected(self):
        attr = {"deliveryMode": "DELIVERY_PICKUP", "isKaspiDelivery": False}
        self.assertEqual(dtype(attr, {"deliveryType": "POSTOMAT"}), "Postomat")


class TestPickupCampaignPass(unittest.TestCase):
    """Триггер кампании переехал с мёртвой стадии NEW на PICKUP."""

    def setUp(self):
        self.triggered = []
        self.saved = {}
        self.profile = {"pickup_points": [{"name": "Основная", "address": "Толе би 285/8"}]}

        async def record(store_id, token, order):
            self.triggered.append(order.get("id"))

        patches = [
            patch.object(worker, "maybe_trigger_pickup_campaign", side_effect=record),
            patch("backend.chatbot.prompt.get_profile", side_effect=lambda sid: self.profile),
            patch.object(worker, "_load_processed_orders", side_effect=lambda sid: dict(self.saved)),
            patch.object(worker, "_save_processed_orders", side_effect=lambda d, sid: self.saved.update(d)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _run(self, orders):
        with patch("backend.kaspi_wrapper.KaspiAPI.get_orders_with_products", return_value=orders) as m:
            asyncio.run(worker._run_pickup_campaign_pass("tok", STORE))
            return m

    def test_queries_pickup_state_not_new(self):
        m = self._run([])
        self.assertEqual(m.call_args.kwargs["state"], "PICKUP")

    def test_fires_for_self_pickup_only(self):
        self._run([
            {"id": "1", "deliveryType": "Самовывоз"},
            {"id": "2", "deliveryType": "Postomat"},
            {"id": "3", "deliveryType": "Kaspi Доставка"},
        ])
        # Задачи кампании создаются через create_task — дать им отработать.
        self.assertEqual(self.triggered, ["1"])

    def test_no_pickup_points_means_no_kaspi_request(self):
        """Звать клиента некуда — ни одного запроса к Kaspi."""
        self.profile = {"pickup_points": []}
        m = self._run([{"id": "1", "deliveryType": "Самовывоз"}])
        m.assert_not_called()

    def test_same_order_is_not_triggered_twice(self):
        orders = [{"id": "1", "deliveryType": "Самовывоз"}]
        self._run(orders)
        self._run(orders)
        self.assertEqual(self.triggered, ["1"])

    def test_postomat_orders_are_not_marked_processed(self):
        """Их незачем помечать: кампания их не касается вовсе."""
        self._run([{"id": "2", "deliveryType": "Postomat"}])
        self.assertNotIn("2", self.saved)

    def test_kaspi_failure_does_not_raise(self):
        with patch("backend.kaspi_wrapper.KaspiAPI.get_orders_with_products", side_effect=Exception("500")):
            asyncio.run(worker._run_pickup_campaign_pass("tok", STORE))
        self.assertEqual(self.triggered, [])


if __name__ == "__main__":
    unittest.main()
