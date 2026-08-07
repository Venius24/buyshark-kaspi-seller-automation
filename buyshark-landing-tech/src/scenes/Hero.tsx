import { useRef } from 'react';
import { ScrollScrubVideo } from '../components/ScrollScrubVideo';
import styles from './Hero.module.css';

const LOOP_IN_AT = 0.985; // scrub progress past which the frozen last frame hands off to the ambient loop

/** Scene 1 — BuyShark hyperdrive race, scrubbed by scroll. */
export function Hero() {
  const titleRef = useRef<HTMLDivElement>(null);
  const hintRef = useRef<HTMLParagraphElement>(null);
  const loopRef = useRef<HTMLVideoElement>(null);

  const handleProgress = (progress: number) => {
    const title = titleRef.current;
    if (title) {
      const t = Math.min(1, Math.max(0, (progress - 0.72) / 0.28));
      title.style.opacity = String(t);
      title.style.transform = `translateY(${(1 - t) * 24}px)`;
    }
    const hint = hintRef.current;
    if (hint) {
      hint.style.opacity = String(Math.max(0, 1 - progress / 0.08));
    }
    const loop = loopRef.current;
    if (loop) {
      loop.style.opacity = progress >= LOOP_IN_AT ? '1' : '0';
    }
  };

  return (
    <ScrollScrubVideo
      src="/media/hero.mp4"
      poster="/media/hero-poster.jpg"
      scrollMultiplier={2.5}
      onProgress={handleProgress}
    >
      <video
        ref={loopRef}
        className={styles.loopVideo}
        src="/media/hero-loop.mp4"
        autoPlay
        loop
        muted
        playsInline
        style={{ opacity: 0 }}
      />
      <div className={styles.vignette} />
      <div className={styles.copy}>
        <div ref={titleRef} className={styles.titleBlock} style={{ opacity: 0 }}>
          <h1 className={styles.title}>
            BuyShark: Управляй ценами.
            <br />
            Доминируй в выдаче.
          </h1>
        </div>
      </div>
      <p ref={hintRef} className={styles.hint}>
        скролль вниз ↓
      </p>
    </ScrollScrubVideo>
  );
}
