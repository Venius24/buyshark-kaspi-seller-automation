import styles from './Features.module.css';

const FEATURES = [
  { num: '1', accent: 'numRed', title: 'Авто-демпинг с защитой маржи', text: 'Цена держится на грани конкурентов, но никогда не уходит ниже вашего минимума.' },
  { num: '2', accent: 'numCyan', title: 'ИИ чат-бот 24/7', text: 'Отвечает покупателям мгновенно, эскалирует сложные вопросы вам лично.' },
  { num: '3', accent: 'numGold', title: 'Автопринятие заказов', text: 'Упаковка, накладные, передача — заказы обрабатываются без вашего клика.' },
  { num: '4', accent: 'numGreen', title: 'Единые остатки', text: 'Склад и витрина синхронизированы — никаких продаж «в минус».' },
  { num: '5', accent: 'numRed', title: 'Ежедневная аналитика', text: 'Каждое утро — сводка по продажам, марже и позициям в рынке.' },
] as const;

export function Features() {
  return (
    <section id="features" className={styles.section}>
      <div className={styles.head}>
        <span className={styles.eyebrow}>Как это работает</span>
        <h2 className={styles.title}>Один хищник — вместо целого отдела</h2>
      </div>
      <div className={styles.grid}>
        {FEATURES.map((f) => (
          <div key={f.title} className={styles.card}>
            <div className={styles[f.accent]}>{f.num}</div>
            <h3 className={styles.cardTitle}>{f.title}</h3>
            <p className={styles.cardText}>{f.text}</p>
          </div>
        ))}
        <div className={styles.cardAccent}>
          <h3 className={styles.cardTitle}>Настраивается под вас</h3>
          <p className={styles.cardText}>Правила демпинга, лимиты и автоматизации — гибко под ваш магазин.</p>
        </div>
      </div>
    </section>
  );
}
