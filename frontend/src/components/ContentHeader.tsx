import type { ActiveTab } from '../types';

const TAB_ICON: Record<ActiveTab, string> = {
  stores: '🏪',
  overview: '📊',
  products: '🏷️',
  dumping: '⚡',
  chatbot: '🤖',
  'stock-groups': '🔗',
  'public-api': '💻',
  'print-batcher': '🖨️',
  packaging: '📦',
  transfer: '🚚',
  logs: '📜',
  settings: '⚙️',
};

const TAB_TITLE: Record<ActiveTab, string> = {
  stores: 'Управление Магазинами Kaspi',
  overview: 'Аналитический Обзор',
  products: 'Управление Товарами',
  dumping: 'Автоматический Демпинг',
  chatbot: 'ИИ-Автоответчик Kaspi',
  'stock-groups': 'Связка Остатков (Master Stock Sync)',
  'public-api': 'Раздел для разработчиков (API & Webhooks)',
  'print-batcher': 'Автосборщик & Сортировщик Печати (Print-Batcher Engine)',
  packaging: 'Упаковка Заказов',
  transfer: 'Передача Курьеру',
  logs: 'Терминал Системных Событий',
  settings: 'Статус Сессии & Настройки',
};

const TAB_SUBTITLE: Record<ActiveTab, string> = {
  stores: 'Управление магазинами Kaspi, безопасное AES-256 шифрование куков и модальное окно Onboarding Wizard',
  overview: 'Ключевая аналитика доли BuyBox, сохранение маржи и поток активности',
  products: 'Каталог товаров, остатки, цены и статус продажи на Kaspi',
  dumping: 'Управление стратегиями демпинга, шагом и фильтрами конкурентов',
  chatbot: 'Автоматические умные ответы покупателям, курьерские шаблоны и Telegram эскалация',
  'stock-groups': 'Объединение карточек товаров Kaspi в единые складские группы с автоматическим веерным списанием остатков',
  'public-api': 'Управление API-ключами, настройка исходящих Webhooks и документация для интеграции с 1С/ERP',
  'print-batcher': 'Прием ZIP-архивов, распознавание размеров страниц и генерация единых PDF для мгновенной печати по типам принтеров',
  packaging: 'Формирование и печать упаковочных актов и маркировок',
  transfer: 'Управление актами приема-сдачи и курьерскими передачами',
  logs: 'Поток событий системы в реальном времени через WebSocket',
  settings: 'Состояние авторизации Kaspi REST API и сессии Kaspi MC',
};

interface ContentHeaderProps {
  activeTab: ActiveTab;
  onLogout: () => void;
}

export function ContentHeader({ activeTab, onLogout }: ContentHeaderProps) {
  return (
    <header className="content-header">
      <div className="content-title-area">
        <span className="content-title-icon">{TAB_ICON[activeTab]}</span>
        <div className="content-title-text">
          <h1>{TAB_TITLE[activeTab]}</h1>
          <p>{TAB_SUBTITLE[activeTab]}</p>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <div className="status-widget" style={{ padding: '6px 14px', borderRadius: '20px' }}>
          <span className="glow-dot glow-dot-success"></span>
          <span style={{ fontSize: '12px', fontWeight: 600 }}>REST API Active</span>
        </div>
        <button className="btn btn-secondary" style={{ padding: '8px 14px' }} onClick={onLogout}>
          🚪 Выйти
        </button>
      </div>
    </header>
  );
}
