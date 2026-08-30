import { CONTACTS, tgLink, waLink } from '../data/contacts';
import styles from './Footer.module.css';

export function Footer() {
  return (
    <footer className={styles.footer}>
      <div className={styles.top}>
        <div className={styles.brand}>
          <img className={styles.logo} src="/media/buyshark-logo.svg" alt="" />
          <span className={styles.copy}>© 2026 BuyShark. Автопилот для продавцов Kaspi.kz</span>
        </div>
        <div className={styles.links}>
          <a href="https://app.buyshark.kz/">Кабинет</a>
          <a href="/help/">Помощь</a>
          <a href="/privacy.html">Политика конфиденциальности</a>
        </div>
      </div>
      <div className={styles.contacts}>
        <a href={waLink('footer', 'Здравствуйте! У меня вопрос про BuyShark.')} target="_blank" rel="noopener noreferrer">
          WhatsApp: {CONTACTS.whatsappDisplay}
        </a>
        <a href={tgLink()} target="_blank" rel="noopener noreferrer">
          Telegram: @{CONTACTS.telegram}
        </a>
        <span className={styles.hours}>{CONTACTS.workHours}</span>
      </div>
    </footer>
  );
}
