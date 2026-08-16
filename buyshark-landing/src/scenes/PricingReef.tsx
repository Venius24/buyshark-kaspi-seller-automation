import { useEffect, useRef, type CSSProperties } from 'react';
import { gsap, ScrollTrigger } from '../lib/gsap';
import { PRICING_TIERS, TRIAL_DAYS, formatTenge, type PricingTier } from '../data/pricing';
import styles from './PricingReef.module.css';

// each tier's feature list is additive on top of the previous one — the copy
// below spells that out explicitly ("всё из ...") rather than leaving it implied
function ctaLabel(tier: PricingTier): string {
  return tier.priceMinor === null ? 'Оставить заявку' : `Попробовать ${TRIAL_DAYS} дней`;
}

function priceLabel(tier: PricingTier): { price: string; priceNote?: string } {
  if (tier.priceMinor === null) return { price: 'от 45 000 ₸', priceNote: 'по предзаказу' };
  return { price: formatTenge(tier.priceMinor), priceNote: '/ мес' };
}

const TIERS = PRICING_TIERS;

function scrollToCta() {
  document.getElementById('final-cta')?.scrollIntoView({ behavior: 'smooth' });
}

/** Pricing section — 5 tiers laid out left-to-right, escalating in glow/saturation as price climbs. */
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
    <section id="pricing" ref={sectionRef} className={styles.section}>
      <video className={styles.bgVideo} src="/media/scene5-bg.mp4" autoPlay loop muted playsInline />
      <div className={styles.scrim} />
      <div className={styles.absurdityGradient} />

      <div className={styles.copy}>
        <h2 className={styles.title}>Тариф растёт вместе с вами</h2>
        <p className={styles.subtitle}>Чем больше товаров — тем больше хищник берёт на себя</p>

        <div className={styles.grid}>
          {TIERS.map((tier, i) => {
            const { price, priceNote } = priceLabel(tier);
            return (
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
                  <span className={styles.price}>{price}</span>
                  {priceNote && <span className={styles.priceNote}>{priceNote}</span>}
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
                  {ctaLabel(tier)}
                </button>
              </div>
            );
          })}
        </div>

        {/* Виден только на телефоне (см. .swipeHint в модуле стилей) — на
            узком экране карточки едут каруселью, и без подписи не все
            догадываются, что справа есть ещё тарифы. */}
        <p className={styles.swipeHint}>Листайте вбок, чтобы сравнить тарифы →</p>
      </div>
    </section>
  );
}
