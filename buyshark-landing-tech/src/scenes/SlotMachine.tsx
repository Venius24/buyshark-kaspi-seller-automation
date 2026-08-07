import { useEffect, useRef } from 'react';
import { ScrollTrigger } from '../lib/gsap';
import styles from './SlotMachine.module.css';

// height of one reel item, in vh — shared between the CSS item boxes and the JS transform math
const ITEM_H = 26;
const BUBBLES_PER_ROLE = 5;

// the "problem" roles the drum cycles through before landing on the final payoff line —
// data-driven so adding/removing a role doesn't mean hand-wiring another near-duplicate
// block of refs and JSX.
const PROBLEM_ROLES = [
  { text: 'МЕНЕДЖЕРОВ ПО ПРОДАЖАМ', colorClass: 'roleYellow', glow: [255, 184, 0] as const },
  { text: 'РУЧНОГО МОНИТОРИНГА ЦЕН', colorClass: 'roleRed', glow: [225, 0, 0] as const },
  { text: 'РУЧНОЙ ПРАВКИ ОСТАТКОВ', colorClass: 'roleOrange', glow: [255, 122, 0] as const },
  { text: 'НОЧНЫХ ДЕЖУРСТВ У КОМПЬЮТЕРА', colorClass: 'roleFog', glow: [142, 124, 255] as const },
];
const FINAL_GLOW = [0, 240, 255] as const; // cyan — BuyShark payoff
// one glow stop per role (problem roles + the final one), blended smoothly as the drum
// spins — drives the frame's ambient glow and each role's backlight so the whole screen
// reacts to what's centered, instead of sitting static cyan the whole time.
const ROLE_GLOW: readonly (readonly [number, number, number])[] = [...PROBLEM_ROLES.map((r) => r.glow), FINAL_GLOW];

const ROLE_COUNT = PROBLEM_ROLES.length + 1; // + the final payoff role
const ROLE_ITEM_INDEX = Array.from({ length: ROLE_COUNT }, (_, i) => i + 1); // 0 and last are blank leaders/trailers
const TRANSITIONS = ROLE_COUNT - 1; // N roles → N-1 hand-offs between them
const HOLD = 0.25; // fraction of each transition spent holding still on either side
const PUNCH_START = 0.9; // last stretch of the pin: final line rushes at the viewer as a transition out

function ease(t: number) {
  return t * t * (3 - 2 * t);
}

function clamp01(n: number) {
  return Math.min(1, Math.max(0, n));
}

function segmentProgress(progress: number, index: number, segments: number) {
  const start = index / segments;
  const end = (index + 1) / segments;
  return Math.min(1, Math.max(0, (progress - start) / (end - start)));
}

function lerpColor(a: readonly [number, number, number], b: readonly [number, number, number], t: number) {
  const r = Math.round(a[0] + (b[0] - a[0]) * t);
  const g = Math.round(a[1] + (b[1] - a[1]) * t);
  const bl = Math.round(a[2] + (b[2] - a[2]) * t);
  return `${r}, ${g}, ${bl}`;
}

/** Scene 2 — a real spinning reel (like a lottery drum), pinned by scroll. */
export function SlotMachine() {
  const sectionRef = useRef<HTMLDivElement>(null);
  const stripRef = useRef<HTMLDivElement>(null);
  const roleRefs = useRef<(HTMLDivElement | null)[]>([]);
  const backlightRefs = useRef<(HTMLDivElement | null)[]>([]);
  const bubbleRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const frameRef = useRef<HTMLDivElement>(null);
  const punchRef = useRef<HTMLDivElement>(null);
  const bloomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const section = sectionRef.current;
    const strip = stripRef.current;
    if (!section || !strip) return;

    const trigger = ScrollTrigger.create({
      trigger: section,
      start: 'top top',
      end: () => `+=${window.innerHeight * 4}`,
      pin: true,
      scrub: 0.4,
      invalidateOnRefresh: true,
      onUpdate: (self) => {
        const p = self.progress;
        const k = Math.min(TRANSITIONS - 1, Math.floor(p * TRANSITIONS));
        const s = segmentProgress(p, k, TRANSITIONS);
        const t = ease(clamp01((s - HOLD) / (1 - 2 * HOLD)));
        const floatIndex = k + 1 + t; // 1 → ROLE_COUNT across the whole pin

        strip.style.transform = `translateY(calc(50% - ${(floatIndex + 0.5) * ITEM_H}vh))`;

        // one shared appear/disappear treatment for every role: fade + blur with distance,
        // plus a "climbing out toward the viewer" scale bump once it settles in the center —
        // same formula for all of them, so nothing gets a bespoke entrance/exit feel.
        roleRefs.current.forEach((el, roleIdx) => {
          if (!el) return;
          const itemIndex = ROLE_ITEM_INDEX[roleIdx];
          const dist = Math.abs(itemIndex - floatIndex);
          const pop = ease(clamp01(1 - dist / 0.55));
          el.style.opacity = String(Math.max(0.05, 1 - clamp01(dist - 0.15) * 0.85));
          el.style.filter = `blur(${clamp01(dist - 0.2) * 9}px)`;
          el.style.transform = `scale(${1 + pop * 0.16})`;

          const backlight = backlightRefs.current[roleIdx];
          if (backlight) backlight.style.opacity = String(pop * 0.9);
        });

        // shared exit flourish — a clip-path "bite" wipe + rising bubbles — applied
        // identically to whichever problem role is currently being spun away (k)
        for (let roleIdx = 0; roleIdx < PROBLEM_ROLES.length; roleIdx++) {
          const el = roleRefs.current[roleIdx];
          if (!el) continue;
          const active = k === roleIdx ? t : 0;
          el.style.clipPath = `inset(0 ${active * 100}% 0 0)`;
          for (let i = 0; i < BUBBLES_PER_ROLE; i++) {
            const bubble = bubbleRefs.current[roleIdx * BUBBLES_PER_ROLE + i];
            if (!bubble) continue;
            const local = clamp01(active - i * 0.12);
            bubble.style.opacity = String(local > 0 ? (1 - local) * 0.85 : 0);
            bubble.style.transform = `translateY(${-local * 90}px)`;
          }
        }

        // ambient color reacts to whatever's centered — blends smoothly between the two
        // nearest role colors instead of snapping, so the whole frame breathes color as it spins
        const colorPos = clamp01(floatIndex - 1); // 0..ROLE_COUNT-1
        const loIdx = Math.min(ROLE_GLOW.length - 2, Math.floor(colorPos));
        const hiIdx = Math.min(ROLE_GLOW.length - 1, loIdx + 1);
        const colorT = clamp01(colorPos - loIdx);
        const glow = lerpColor(ROLE_GLOW[loIdx], ROLE_GLOW[hiIdx], colorT);
        section.style.setProperty('--role-glow', glow);

        // final beat: "чистую прибыль" rushes forward at the viewer as a transition into scene 3
        const punch = ease(clamp01((p - PUNCH_START) / (1 - PUNCH_START)));
        if (punchRef.current) {
          punchRef.current.style.opacity = String(Math.min(1, punch * 1.4));
          punchRef.current.style.transform = `scale(${1 + punch * 1.3})`;
        }
        if (bloomRef.current) {
          bloomRef.current.style.opacity = String(punch * 0.8);
        }
        if (frameRef.current) {
          frameRef.current.style.opacity = String(1 - punch * 0.7);
          frameRef.current.style.filter = `blur(${punch * 6}px)`;
        }
      },
    });

    return () => trigger.kill();
  }, []);

  return (
    <section ref={sectionRef} className={styles.section}>
      <h2 className={styles.headline}>Мы освободим вас от:</h2>

      <div ref={frameRef} className={styles.drumFrame}>
        <span className={styles.bolt} data-pos="tl" />
        <span className={styles.bolt} data-pos="tr" />
        <span className={styles.bolt} data-pos="bl" />
        <span className={styles.bolt} data-pos="br" />

        <div className={styles.ticks} data-side="left">
          {Array.from({ length: 10 }, (_, i) => (
            <span key={i} />
          ))}
        </div>
        <div className={styles.ticks} data-side="right">
          {Array.from({ length: 10 }, (_, i) => (
            <span key={i} />
          ))}
        </div>

        <div className={styles.window}>
          <div className={styles.scanline} />

          <div ref={stripRef} className={styles.strip}>
            <div className={styles.item} style={{ height: `${ITEM_H}vh` }} />

            {PROBLEM_ROLES.map((role, roleIdx) => (
              <div
                key={role.text}
                ref={(node) => {
                  roleRefs.current[roleIdx] = node;
                }}
                className={`${styles.item} ${styles.role} ${styles[role.colorClass]}`}
                style={{ height: `${ITEM_H}vh` }}
              >
                <div
                  ref={(node) => {
                    backlightRefs.current[roleIdx] = node;
                  }}
                  className={styles.backlight}
                  style={{ opacity: 0 }}
                />
                <span className={styles.roleInner}>{role.text}</span>
                {Array.from({ length: BUBBLES_PER_ROLE }, (_, i) => (
                  <span
                    key={i}
                    ref={(node) => {
                      bubbleRefs.current[roleIdx * BUBBLES_PER_ROLE + i] = node;
                    }}
                    className={styles.bubble}
                    style={{ left: `${20 + i * 15}%` }}
                  />
                ))}
              </div>
            ))}

            <div
              ref={(node) => {
                roleRefs.current[PROBLEM_ROLES.length] = node;
              }}
              className={`${styles.item} ${styles.role} ${styles.roleFinal}`}
              style={{ height: `${ITEM_H}vh` }}
            >
              <div
                ref={(node) => {
                  backlightRefs.current[PROBLEM_ROLES.length] = node;
                }}
                className={styles.backlight}
                style={{ opacity: 0 }}
              />
              <span className={styles.roleInner}>...ОСТАВИВ ТОЛЬКО <span className={styles.highlight}>ЧИСТУЮ ПРИБЫЛЬ</span></span>
            </div>

            <div className={styles.item} style={{ height: `${ITEM_H}vh` }} />
          </div>

          <span className={styles.centerMark} data-side="left" />
          <span className={styles.centerMark} data-side="right" />
          <div className={styles.windowShade} data-edge="top" />
          <div className={styles.windowShade} data-edge="bottom" />
        </div>
      </div>

      <div ref={bloomRef} className={styles.bloom} style={{ opacity: 0 }} />
      <div ref={punchRef} className={styles.punch} style={{ opacity: 0 }}>
        ЧИСТУЮ ПРИБЫЛЬ
      </div>
    </section>
  );
}
