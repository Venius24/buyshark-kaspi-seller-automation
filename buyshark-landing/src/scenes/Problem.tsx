import styles from './Problem.module.css';

const PROBLEMS = [
  { icon: '⏱', title: 'Ручной демпинг', text: 'Часы каждый день на пересчёт цен по конкурентам вручную.' },
  { icon: '💬', title: 'Потерянные чаты', text: 'Покупатели ждут ответ часами — заказ уходит конкуренту.' },
  { icon: '📦', title: 'Хаос с остатками', text: 'Продажи «в минус» из-за рассинхрона склада и витрины.' },
  { icon: '📊', title: 'Нет аналитики', text: 'Решения вслепую — непонятно, что реально приносит деньги.' },
];

export function Problem() {
  return (
    <section className={styles.section}>
      <div className={styles.head}>
        <h2 className={styles.title}>Пока вы спите — рынок не спит</h2>
        <p className={styles.subtitle}>
          Конкуренты меняют цены каждую минуту. Ручное управление магазином на Kaspi.kz съедает часы — и всё равно
          опаздывает.
        </p>
      </div>
      <div className={styles.grid}>
        {PROBLEMS.map((p) => (
          <div key={p.title} className={styles.card}>
            <div className={styles.icon}>{p.icon}</div>
            <h3 className={styles.cardTitle}>{p.title}</h3>
            <p className={styles.cardText}>{p.text}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
