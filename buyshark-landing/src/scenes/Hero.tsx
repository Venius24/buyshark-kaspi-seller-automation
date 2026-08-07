import { useRef, useState } from 'react';
import styles from './Hero.module.css';

function scrollToPricing() {
  document.getElementById('pricing')?.scrollIntoView({ behavior: 'smooth' });
}

export function Hero() {
  const loopRef = useRef<HTMLVideoElement>(null);
  const [loopActive, setLoopActive] = useState(false);

  const handleIntroEnded = () => {
    loopRef.current?.play();
    setLoopActive(true);
  };

  return (
    <section className={styles.section}>
      <video
        className={styles.video}
        style={{ opacity: loopActive ? 0 : 1 }}
        src="/media/hero.mp4"
        poster="/media/hero-poster.jpg"
        autoPlay
        muted
        playsInline
        onEnded={handleIntroEnded}
      />
      <video
        ref={loopRef}
        className={styles.video}
        style={{ opacity: loopActive ? 1 : 0 }}
        src="/media/hero-loop.mp4"
        muted
        loop
        playsInline
      />
      <div className={styles.scrim} />
      <div className={styles.copy}>
        <span className={styles.badge}>
          <span className={styles.dot} />
          №1 автопилот для продавцов Kaspi.kz
        </span>
        <h1 className={styles.title}>
          Вы продаёте.
          <br />
          Акула держит цену.
        </h1>
        <p className={styles.subtitle}>
          Авто-демпинг с защитой маржи, ИИ чат-бот, синхронизация остатков и упаковка заказов — 24/7, без вашего
          участия. Снимаем с вас до 80% рутины на Kaspi.kz.
        </p>
        <div className={styles.actions}>
          <a className={styles.btnPrimary} href="https://app.buyshark.kz/">
            Начать бесплатно на 3 дня
          </a>
          <button className={styles.btnSecondary} onClick={scrollToPricing}>
            Смотреть тарифы ↓
          </button>
        </div>
        <div className={styles.stats}>
          <div>
            <div className={styles.statValue}>-80%</div>
            <div className={styles.statLabel}>ручной работы</div>
          </div>
          <div>
            <div className={styles.statValue}>24/7</div>
            <div className={styles.statLabel}>автопилот цен</div>
          </div>
          <div>
            <div className={styles.statValue}>10 000+</div>
            <div className={styles.statLabel}>проверок цен/сек</div>
          </div>
        </div>
      </div>
    </section>
  );
}
