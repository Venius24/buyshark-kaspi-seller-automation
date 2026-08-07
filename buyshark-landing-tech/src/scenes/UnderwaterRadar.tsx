import { useRef } from 'react';
import { ScrollScrubVideo } from '../components/ScrollScrubVideo';
import styles from './UnderwaterRadar.module.css';

/** Scene 4 — server on the seabed, scroll-scrubbed; the red-rim pulse and the shark swarm reveal are baked into the video. */
export function UnderwaterRadar() {
  const hudRef = useRef<HTMLSpanElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const subtitleRef = useRef<HTMLParagraphElement>(null);

  const handleProgress = (progress: number) => {
    const hud = hudRef.current;
    if (hud) {
      hud.style.opacity = String(Math.min(1, progress / 0.15) * 0.7);
    }
    const t = Math.min(1, Math.max(0, (progress - 0.68) / 0.32));
    const title = titleRef.current;
    if (title) {
      title.style.opacity = String(t);
      title.style.transform = `translateY(${(1 - t) * 24}px)`;
    }
    const subtitle = subtitleRef.current;
    if (subtitle) {
      subtitle.style.opacity = String(t);
      subtitle.style.transform = `translateY(${(1 - t) * 24}px)`;
    }
  };

  return (
    <ScrollScrubVideo src="/media/scene4-bg.mp4" scrollMultiplier={2.5} onProgress={handleProgress} className={styles.section}>
      <div className={styles.scrim} />
      <span ref={hudRef} className={styles.hud} style={{ opacity: 0 }}>
        async proxy scan · 0 банов · 24/7
      </span>

      <div className={styles.copy}>
        <h2 ref={titleRef} className={styles.title} style={{ opacity: 0 }}>
          10 000 проверок в секунду.
        </h2>
        <p ref={subtitleRef} className={styles.subtitle} style={{ opacity: 0 }}>
          Вы всегда на шаг впереди рынка.
        </p>
      </div>
    </ScrollScrubVideo>
  );
}
