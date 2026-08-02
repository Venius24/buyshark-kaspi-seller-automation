import { useEffect, useRef, type CSSProperties } from 'react';
import { gsap, ScrollTrigger } from '../lib/gsap';
import styles from './PricingReef.module.css';

interface Tier {
  name: string;
  limit: string;
  price: string;
  priceNote?: string;
  features: string[];
  ribbon?: string;
  variant?: 'popular' | 'premium';
  ctaLabel: string;
}

// each tier's feature list is additive on top of the previous one — the copy
// below spells that out explicitly ("всё из ...") rather than leaving it implied
const TIERS: Tier[] = [
  {
    name: 'Бодрый старт',
    limit: 'до 50 товаров',
    price: '8 900 ₸',
    priceNote: '/ мес',
    features: [
      'Листинг и массовое обновление цен',
      'Упаковка заказов, трансферы, накладные и этикетки',
      '1 магазин, email-авторизация',
      'Базовый дашборд',
    ],
    ctaLabel: 'Попробовать 3 дня',
  },
  {
    name: 'Охотник',
    limit: 'до 200 товаров',
    price: '10 200 ₸',
    priceNote: '/ мес',
    features: [
      'Всё из «Бодрый старт»',
      'Авто-демпинг с защитой маржи',
      'Динамический price-feed для Kaspi',
      'Периодическая синхронизация с Kaspi API',
    ],
    ctaLabel: 'Попробовать 3 дня',
  },
  {
    name: 'Хищник',
    limit: 'до 1000 товаров',
    price: '15 200 ₸',
    priceNote: '/ мес',
    features: [
      'Всё из «Охотник»',
      'Синхронизация остатков между складами',
      'Чат с покупателями + авточатбот',
      'Публичный API-доступ + вебхуки',
    ],
    ribbon: 'ХИТ ПРОДАЖ',
    variant: 'popular',
    ctaLabel: 'Попробовать 3 дня',
  },
  {
    name: 'Хищник+',
    limit: 'до 2000 товаров',
    price: '24 600 ₸',
    priceNote: '/ мес',
    features: [
      'Всё из «Хищник»',
      'Автодобавление товаров (AI, бета)',
      'Мультистор на аккаунт',
      'WebSocket realtime + приоритетная поддержка',
    ],
    ctaLabel: 'Попробовать 3 дня',
  },
  {
    name: 'ГигаПредприятие',
    limit: 'безлимит товаров',
    price: 'от 45 000 ₸',
    priceNote: 'по предзаказу',
    features: [
      'Всё включено',
      'Персональный SLA и выделенная инфраструктура',
      'Кастомные интеграции',
      'Выделенный менеджер',
    ],
    ribbon: 'ENTERPRISE',
    variant: 'premium',
    ctaLabel: 'Оставить заявку',
  },
];

function scrollToCta() {
  document.getElementById('cta')?.scrollIntoView({ behavior: 'smooth' });
}

/** Scene 5 — the pricing reef: 5 tiers laid out left-to-right, escalating in glow/saturation as price climbs. */
export function PricingReef() {
  const sectionRef = useRef<HTMLElement>(null);
  const cardRefs = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    const section = sectionRef.current;
    const cards = cardRefs.current.filter((el): el is HTMLDivElement => el !== null);
    if (!section || cards.length === 0) return;

    gsap.set(cards, { opacity: 0, y: 60, scale: 0.85, filter: 'blur(10px)' });

    const trigger = ScrollTrigger.create({
      trigger: section,
      start: 'top 80%',
      once: true,
      onEnter: () => {
        const tl = gsap.timeline();
        cards.forEach((card, i) => {
          tl.to(
            card,
            { opacity: 1, y: 0, scale: 1, filter: 'blur(0px)', duration: 0.55, ease: 'power3.out' },
            i * 0.12,
          );
        });
      },
    });

    return () => trigger.kill();
  }, []);

  return (
    <section ref={sectionRef} className={styles.section}>
      <video className={styles.bgVideo} src="/media/scene5-bg.mp4" autoPlay loop muted playsInline />
      <div className={styles.scrim} />
      <div className={styles.absurdityGradient} />

      <div className={styles.copy}>
        <h2 className={styles.title}>Тариф растёт вместе с вами</h2>
        <p className={styles.subtitle}>Чем больше товаров — тем больше хищник берёт на себя</p>

        <div className={styles.grid}>
          {TIERS.map((tier, i) => (
            <div
              key={tier.name}
              ref={(node) => {
                cardRefs.current[i] = node;
              }}
              className={styles.card}
              data-variant={tier.variant}
              style={{ '--tier-index': i } as CSSProperties}
            >
              {tier.ribbon && <span className={styles.ribbon}>{tier.ribbon}</span>}

              <p className={styles.tierName}>{tier.name}</p>
              <p className={styles.tierLimit}>{tier.limit}</p>

              <div className={styles.priceRow}>
                <span className={styles.price}>{tier.price}</span>
                {tier.priceNote && <span className={styles.priceNote}>{tier.priceNote}</span>}
              </div>

              <ul className={styles.features}>
                {tier.features.map((f) => (
                  <li key={f}>
                    <span className={styles.check} />
                    {f}
                  </li>
                ))}
              </ul>

              <button className={styles.cta} onClick={scrollToCta}>
                {tier.ctaLabel}
              </button>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
