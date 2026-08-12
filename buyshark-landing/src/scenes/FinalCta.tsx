import { useRef, useState } from 'react';
import { TRIAL_DAYS } from '../data/pricing';
import styles from './FinalCta.module.css';

export function FinalCta() {
  const loopRef = useRef<HTMLVideoElement>(null);
  const [loopActive, setLoopActive] = useState(false);

  const handleIntroEnded = () => {
    loopRef.current?.play();
    setLoopActive(true);
  };

  return (
    <section id="final-cta" className={styles.section}>
      <video
        className={styles.video}
        style={{ opacity: loopActive ? 0 : 1 }}
        src="/media/scene6-bg.mp4"
        autoPlay
        muted
        playsInline
        onEnded={handleIntroEnded}
      />
      <video
        ref={loopRef}
        className={styles.video}
        style={{ opacity: loopActive ? 1 : 0 }}
        src="/media/scene6-loop.mp4"
        muted
        loop
        playsInline
      />
      <div className={styles.scrim} />
      <div className={styles.panel}>
        <h2 className={styles.title}>Пора выпустить акулу на ваш рынок</h2>
        <p className={styles.subtitle}>{TRIAL_DAYS} дней бесплатно. Без карты. Отменить в любой момент.</p>
        <a className={styles.cta} href="https://app.buyshark.kz/">
          Начать бесплатно
        </a>
      </div>
    </section>
  );
}
