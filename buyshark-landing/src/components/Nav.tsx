import { waLink } from '../data/contacts';
import styles from './Nav.module.css';

const APP_URL = 'https://app.buyshark.kz/';

export function Nav() {
  return (
    <nav className={styles.nav}>
      <div className={styles.brand}>
        <img className={styles.logo} src="/media/buyshark-logo.svg" alt="BuyShark" />
        <span className={styles.wordmark}>BuyShark</span>
      </div>
      <div className={styles.links}>
        <a href="#features">Возможности</a>
        <a href="#pricing">Тарифы</a>
        <a href="/help/">Помощь</a>
        <a href={APP_URL}>Кабинет</a>
      </div>
      <div className={styles.actions}>
        <a
          className={styles.btnText}
          href={waLink('nav', 'Здравствуйте! У меня вопрос про BuyShark.')}
          target="_blank"
          rel="noopener noreferrer"
        >
          Написать
        </a>
        <a className={styles.btnGhost} href={APP_URL}>
          Войти
        </a>
        <a className={styles.btnPrimary} href={APP_URL}>
          Начать бесплатно
        </a>
      </div>
    </nav>
  );
}
