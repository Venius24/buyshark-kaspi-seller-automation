// Единственное место с ценами на лендинге — при изменении тарифов меняем
// только тут. Источник правды: backend/billing/catalog.py (PLANS) в
// основном репозитории sbx. Эти два файла не связаны программно (лендинг —
// отдельный статический сайт), так что при изменении цен в catalog.py нужно
// руками поправить и это. TRIAL_DAYS должен совпадать с тем, что реально
// применяет POST /api/v1/stores/connect-full-store (plan_code -> 7-дневный
// триал, backend/routers/stores_v1.py).

export const TRIAL_DAYS = 7;

export interface PricingTier {
  name: string;
  limit: string;
  priceMinor: number | null; // null = договорная цена
  features: string[];
  ribbon?: string;
  variant?: 'popular' | 'premium';
}

export const PRICING_TIERS: PricingTier[] = [
  {
    name: 'Бодрый старт',
    limit: 'до 50 товаров',
    priceMinor: 640_000,
    features: [
      'Листинг и массовое обновление цен',
      'Упаковка заказов, трансферы, накладные и этикетки',
      '1 магазин, email-авторизация',
      'Базовый дашборд',
    ],
  },
  {
    name: 'Охотник',
    limit: 'до 200 товаров',
    priceMinor: 1_130_000,
    features: [
      'Всё из «Бодрый старт»',
      'Авто-демпинг с защитой маржи',
      'Динамический price-feed для Kaspi',
      'Периодическая синхронизация с Kaspi API',
    ],
  },
  {
    name: 'Хищник',
    limit: 'до 1000 товаров',
    priceMinor: 1_790_000,
    features: [
      'Всё из «Охотник»',
      'Синхронизация остатков между складами',
      'Чат с покупателями + авточатбот',
      'Публичный API-доступ + вебхуки',
    ],
    ribbon: 'ХИТ ПРОДАЖ',
    variant: 'popular',
  },
];

// Хищник+ (PREDATOR_PLUS) и ГигаПредприятие (GIGA) скрыты из публичной
// витрины лендинга 2026-09-12 по решению владельца — сами тарифы в
// backend/billing/catalog.py не тронуты, существующие подписчики на них
// продолжают работать как есть, просто новых через сайт не продаём.

export function formatTenge(minor: number): string {
  const whole = Math.round(minor / 100);
  return `${whole.toLocaleString('ru-RU')} ₸`;
}
