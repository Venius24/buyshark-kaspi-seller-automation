import { useEffect, useRef, useState } from 'react';
import { gsap, ScrollTrigger } from '../lib/gsap';
import styles from './PriceBiteGame.module.css';

const PRODUCTS = [
  { name: 'Термокружка 500мл', base: '150 000', next: '147 990' },
  { name: 'Наушники TWS Pro', base: '148 500', next: '144 250' },
  { name: 'Кабель USB-C 2м', base: '152 000', next: '149 490' },
];

const SHARD_COUNT = 6;

/** Scene 3 — top-down sea, click a holographic card and it falls away, replaced by your price. */
export function PriceBiteGame() {
  const sectionRef = useRef<HTMLElement>(null);
  const cardRefs = useRef<(HTMLDivElement | null)[]>([]);
  const chipRefs = useRef<(HTMLDivElement | null)[]>([]);
  const strikeRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const shardRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const flashRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const overlayRef = useRef<HTMLDivElement>(null);
  const overlayTextRef = useRef<HTMLDivElement>(null);
  const [fallen, setFallen] = useState<boolean[]>(() => PRODUCTS.map(() => false));
  const [hit, setHit] = useState<boolean[]>(() => PRODUCTS.map(() => false));

  // "wow" entrance — cards materialize as holograms powering on (blurred/scaled
  // in + a bright flash-on flicker) once the section scrolls into view, staggered
  // per card, instead of just being statically present when you arrive.
  useEffect(() => {
    const section = sectionRef.current;
    const cards = cardRefs.current.filter((el): el is HTMLDivElement => el !== null);
    const flashes = flashRefs.current.filter((el): el is HTMLSpanElement => el !== null);
    if (!section || cards.length === 0) return;

    gsap.set(cards, { opacity: 0, y: 70, scale: 0.5, filter: 'blur(16px)' });

    const trigger = ScrollTrigger.create({
      trigger: section,
      start: 'top 75%',
      once: true,
      onEnter: () => {
        const tl = gsap.timeline();
        cards.forEach((card, i) => {
          const at = i * 0.18;
          tl.to(card, { opacity: 1, y: 0, scale: 1.06, filter: 'blur(0px)', duration: 0.5, ease: 'power3.out' }, at)
            .to(card, { scale: 1, duration: 0.35, ease: 'elastic.out(1, 0.55)' }, at + 0.5);
          const flash = flashes[i];
          if (flash) {
            tl.fromTo(flash, { opacity: 0.95 }, { opacity: 0, duration: 0.45, ease: 'power2.out' }, at + 0.15);
          }
        });
      },
    });

    return () => trigger.kill();
  }, []);

  const handleClick = (i: number) => {
    if (fallen[i]) return; // one-way — a fallen card doesn't come back
    setFallen((prev) => prev.map((v, idx) => (idx === i ? true : v)));
    setHit((prev) => prev.map((v, idx) => (idx === i ? true : v)));
    window.setTimeout(() => setHit((prev) => prev.map((v, idx) => (idx === i ? false : v))), 320);

    const card = cardRefs.current[i];
    const strike = strikeRefs.current[i];
    const chip = chipRefs.current[i];

    // neon streak — a single fast slash across the card, right on impact
    if (strike) {
      gsap.killTweensOf(strike);
      gsap.set(strike, { opacity: 1, xPercent: -30 });
      gsap.to(strike, { xPercent: 30, duration: 0.18, ease: 'power2.in' });
      gsap.to(strike, { opacity: 0, duration: 0.12, delay: 0.08 });
    }

    // fake shard burst — small decorative fragments flying outward, not real
    // slices of the card's own DOM (cheap, no per-shard text-clipping risk)
    for (let s = 0; s < SHARD_COUNT; s++) {
      const shard = shardRefs.current[i * SHARD_COUNT + s];
      if (!shard) continue;
      const angle = (s / SHARD_COUNT) * Math.PI * 2 + Math.random() * 0.6;
      const dist = 46 + Math.random() * 28;
      const dx = Math.cos(angle) * dist;
      const dy = Math.sin(angle) * dist;
      const rot = (Math.random() - 0.5) * 200;
      gsap.killTweensOf(shard);
      gsap.set(shard, { x: 0, y: 0, rotate: 0, opacity: 1, scale: 1 });
      gsap.to(shard, { x: dx, y: dy, rotate: rot, opacity: 0, scale: 0.4, duration: 0.4, ease: 'power2.out' });
    }

    // impact jolt, then the card drops away and fades — defeated, not just dented
    if (card) {
      const dir = i % 2 === 0 ? 1 : -1;
      gsap.killTweensOf(card);
      gsap
        .timeline()
        .to(card, { x: -6 * dir, rotate: -1.2 * dir, duration: 0.05, ease: 'power1.inOut' })
        .to(card, { x: 5 * dir, rotate: 1 * dir, duration: 0.06, ease: 'power1.inOut' })
        .to(card, { x: 0, rotate: 0, duration: 0.06, ease: 'power1.inOut' })
        .to(card, {
          y: 150,
          rotate: 22 * dir,
          scale: 0.8,
          opacity: 0,
          duration: 0.5,
          ease: 'power2.in',
        });
    }

    // the won-chip floats up in the same slot as the card falls away
    if (chip) {
      gsap.killTweensOf(chip);
      gsap.set(chip, { opacity: 0, y: 12, scale: 0.7 });
      gsap.to(chip, { opacity: 1, y: 0, scale: 1, duration: 0.45, delay: 0.22, ease: 'back.out(1.6)' });
    }
  };

  const allFallen = fallen.every(Boolean);

  useEffect(() => {
    if (!allFallen) return;
    const overlay = overlayRef.current;
    const text = overlayTextRef.current;
    if (!overlay || !text) return;

    gsap.killTweensOf([overlay, text]);
    gsap.to(overlay, { opacity: 1, duration: 0.6, ease: 'power2.out', delay: 0.3 });
    gsap.fromTo(
      text,
      { opacity: 0, y: 20, scale: 0.7 },
      { opacity: 1, y: 0, scale: 1, duration: 0.5, delay: 0.5, ease: 'back.out(1.7)' },
    );
  }, [allFallen]);

  return (
    <section ref={sectionRef} className={styles.section}>
      <video className={styles.bgVideo} src="/media/scene3-bg.mp4" autoPlay loop muted playsInline />
      <div className={styles.scrim} />

      <div className={styles.copy}>
        <h2 className={styles.title}>Съешь маржу конкурента</h2>

        <div className={styles.gridWrap}>
          <div className={styles.grid}>
            {PRODUCTS.map((p, i) => (
              <div
                key={p.name}
                className={styles.crateFloat}
                data-fallen={fallen[i]}
                style={{ animationDelay: `${i * 0.7}s` }}
              >
                <span className={styles.ripple} />

                <div className={styles.wonChip}>
                  <div
                    ref={(node) => {
                      chipRefs.current[i] = node;
                    }}
                    className={styles.wonChipInner}
                  >
                    <span className={styles.wonPrice}>{p.next} ₸</span>
                    <span className={styles.wonBadge}>ВАШ МАГАЗИН</span>
                  </div>
                </div>

                <div
                  ref={(node) => {
                    cardRefs.current[i] = node;
                  }}
                  className={`${styles.crate} ${hit[i] ? styles.hit : ''}`}
                  data-fallen={fallen[i]}
                  onClick={() => handleClick(i)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => e.key === 'Enter' && handleClick(i)}
                >
                  <span className={styles.chromeBar} />
                  <span className={styles.chromeDot} />
                  <span className={styles.shimmer} />
                  <span
                    ref={(node) => {
                      flashRefs.current[i] = node;
                    }}
                    className={styles.powerFlash}
                    style={{ opacity: 0 }}
                  />

                  <span
                    ref={(node) => {
                      strikeRefs.current[i] = node;
                    }}
                    className={styles.strike}
                    style={{ opacity: 0 }}
                  />

                  <div className={styles.shardWrap}>
                    {Array.from({ length: SHARD_COUNT }, (_, s) => (
                      <span
                        key={s}
                        ref={(node) => {
                          shardRefs.current[i * SHARD_COUNT + s] = node;
                        }}
                        className={styles.shard}
                        style={{ opacity: 0 }}
                      />
                    ))}
                  </div>

                  <p className={styles.name}>{p.name}</p>
                  <div className={styles.priceStack}>
                    <span className={styles.priceRed}>{p.base} ₸</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div ref={overlayRef} className={styles.marketOverlay} style={{ opacity: 0 }}>
        <div ref={overlayTextRef} className={styles.marketText}>
          РЫНОК ВАШ
        </div>
      </div>

      <p className={styles.caption}>Алгоритм BuyShark делает это за 0.2 секунды 24/7</p>
    </section>
  );
}
