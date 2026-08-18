import styles from './Footer.module.css';

export function Footer() {
  return (
    <footer className={styles.footer}>
      <div className={styles.brand}>
        <img className={styles.logo} src="/media/buyshark-logo.svg" alt="" />
        <span className={styles.copy}>© 2026 BuyShark. Автопилот для продавцов Kaspi.kz</span>
      </div>
      <div className={styles.links}>
        <a href="https://app.buyshark.kz/">Кабинет</a>
        <a href="/help/">Помощь</a>
        <a href="/privacy.html">Политика конфиденциальности</a>
      </div>
    </footer>
  );
}
