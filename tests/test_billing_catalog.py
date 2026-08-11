import os
import sys
import unittest

sys.path.append(os.getcwd())


class PromptBandThresholdsTest(unittest.TestCase):
    """Границы ступеней доплаты за расширенный system prompt (тарификация,
    2026-08-12): [0,500] -> band0, (500,2000] -> band1, (2000,5000] -> band2,
    >5000 -> band3. Каждая ступень даёт и месячную доплату, и множитель
    кредита за сообщение (решение владельца: 0/1000/2500/5000 ₸ и
    1.0/1.2/1.5/2.0x)."""

    def setUp(self):
        from backend.billing import catalog
        self.catalog = catalog

    def test_band_boundaries(self):
        cases = [
            (0, "band0"), (500, "band0"),
            (501, "band1"), (2000, "band1"),
            (2001, "band2"), (5000, "band2"),
            (5001, "band3"), (50000, "band3"),
        ]
        for chars, expected in cases:
            with self.subTest(chars=chars):
                band = self.catalog.resolve_prompt_band(chars)
                self.assertEqual(band.code, expected)

    def test_band_prices_and_multipliers(self):
        expected = {
            "band0": (0, 1000),
            "band1": (100_000, 1200),
            "band2": (250_000, 1500),
            "band3": (500_000, 2000),
        }
        for code, (price, mult) in expected.items():
            band = self.catalog.PROMPT_BANDS_BY_CODE[code]
            self.assertEqual(band.monthly_price_minor, price)
            self.assertEqual(band.multiplier_milli, mult)
            self.assertEqual(self.catalog.multiplier_milli(code), mult)

    def test_compute_prompt_tier_delegates_to_catalog(self):
        from backend.chatbot.prompt import compute_prompt_tier
        self.assertEqual(compute_prompt_tier(""), "band0")
        self.assertEqual(compute_prompt_tier("x" * 500), "band0")
        self.assertEqual(compute_prompt_tier("x" * 501), "band1")
        self.assertEqual(compute_prompt_tier("x" * 2001), "band2")
        self.assertEqual(compute_prompt_tier("x" * 5001), "band3")


class TariffCatalogTest(unittest.TestCase):
    def setUp(self):
        from backend.billing import catalog
        self.catalog = catalog

    def test_plan_codes_and_prices(self):
        expected = {
            "START": (50, 640_000),
            "HUNTER": (200, 1_130_000),
            "PREDATOR": (1000, 1_790_000),
            "PREDATOR_PLUS": (2000, 2_980_000),
        }
        for code, (max_products, price) in expected.items():
            plan = self.catalog.PLANS[code]
            self.assertEqual(plan.max_products, max_products)
            self.assertEqual(plan.monthly_price_minor, price)

    def test_dev_plan_is_free_and_unlimited_and_not_public(self):
        dev = self.catalog.PLANS["DEV"]
        self.assertTrue(dev.is_free)
        self.assertIsNone(dev.max_products)
        self.assertFalse(dev.is_public)
        self.assertTrue(self.catalog.is_free_plan("DEV"))
        self.assertNotIn(dev, self.catalog.public_plans())

    def test_giga_plan_has_no_catalog_price_and_is_unlimited(self):
        giga = self.catalog.PLANS["GIGA"]
        self.assertIsNone(giga.monthly_price_minor)
        self.assertIsNone(giga.max_products)
        self.assertFalse(giga.is_free)

    def test_credit_tiers_replace_not_stack(self):
        # Одна колонка credit_addon_code на магазин ⇒ структурно возможен
        # только один активный уровень за раз, тарифы не суммируются.
        self.assertEqual(self.catalog.effective_daily_credits("NONE"), 50_000)
        self.assertEqual(self.catalog.effective_daily_credits("C70"), 70_000)
        self.assertEqual(self.catalog.effective_daily_credits("C100"), 100_000)
        self.assertEqual(self.catalog.credit_addon_price_minor("NONE"), 0)
        self.assertEqual(self.catalog.credit_addon_price_minor("C70"), 200_000)
        self.assertEqual(self.catalog.credit_addon_price_minor("C100"), 400_000)

    def test_plan_price_minor_uses_custom_override_when_present(self):
        self.assertEqual(self.catalog.plan_price_minor("START", None), 640_000)
        self.assertEqual(self.catalog.plan_price_minor("GIGA", 5_000_000), 5_000_000)
        self.assertEqual(self.catalog.plan_price_minor("GIGA", None), 0)

    def test_balance_block_threshold_is_minus_3000_tenge(self):
        self.assertEqual(self.catalog.BALANCE_BLOCK_THRESHOLD_MINOR, -300_000)

    def test_billing_timezone_is_almaty(self):
        self.assertEqual(self.catalog.BILLING_TZ_OFFSET_HOURS, 5)


if __name__ == "__main__":
    unittest.main()
