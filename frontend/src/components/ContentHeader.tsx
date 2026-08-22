import type { ActiveTab } from '../types';

const TAB_ICON: Record<ActiveTab, string> = {
  stores: '🏪',
  'global-control': '🎛️',
  overview: '📊',
  products: '🏷️',
  dumping: '⚡',
  chatbot: '🤖',
  chats: '💬',
  'stock-groups': '🔗',
  autolist: '✨',
  'public-api': '💻',
  'print-batcher': '🖨️',
  packaging: '📦',
  transfer: '🚚',
  'auto-assembly': '🤖',
  billing: '💳',
  admin: '🛡️',
  referral: '🤝',
  logs: '📜',
  settings: '⚙️',
  help: '❓',
};

const TAB_TITLE: Record<ActiveTab, string> = {
  stores: 'Управление Магазинами Kaspi',
  'global-control': 'Глобальное управление',
  overview: 'Аналитический Обзор',
  products: 'Управление Товарами',
  dumping: 'Автоматический Демпинг',
  chatbot: 'ИИ-Автоответчик Kaspi',
  chats: 'Чаты Kaspi',
  'stock-groups': 'Связка Остатков (Master Stock Sync)',
  autolist: 'Автодобавление Товара',
  'public-api': 'Раздел для разработчиков (API & Webhooks)',
  'print-batcher': 'Склейка накладных',
  packaging: 'Упаковка Заказов',
  transfer: 'Передача Курьеру',
  'auto-assembly': 'Автосборка',
  billing: 'Тариф и Баланс',
  admin: 'Админ-панель',
  referral: 'Реферальная программа',
  logs: 'Терминал Системных Событий',
  settings: 'Статус Сессии & Настройки',
  help: 'Помощь и Энциклопедия',
};

const TAB_SUBTITLE: Record<ActiveTab, string> = {
  stores: 'Управление магазинами Kaspi, безопасное AES-256 шифрование куков и модальное окно Onboarding Wizard',
  'global-control': 'Один экран для всех автоматик магазина — общий рубильник и точечные выключатели демпинга, связки остатков, автосборки и ИИ-автоответчика',
  overview: 'Доля лучшей цены по каталогу, сохранённая маржа и поток активности',
  products: 'Каталог товаров, остатки, цены и статус продажи на Kaspi',
  dumping: 'Управление стратегиями демпинга, шагом и фильтрами конкурентов',
  chatbot: 'Автоматические умные ответы покупателям, курьерские шаблоны и Telegram эскалация',
  chats: 'Полная переписка с покупателями Kaspi — свой интерфейс поверх Kaspi Chat API',
  'stock-groups': 'Объединение карточек товаров Kaspi в единые складские группы с автоматическим веерным списанием остатков',
  autolist: 'Фото + название → AI заполняет категорию и характеристики Kaspi, вы проверяете и подтверждаете перед отправкой',
  'public-api': 'Управление API-ключами, настройка исходящих Webhooks и документация для интеграции с 1С/ERP',
  'print-batcher': 'Приём ZIP-архивов с накладными, автоматическая склейка в единый PDF по размерам страниц для мгновенной печати',
  packaging: 'Формирование и печать упаковочных актов и маркировок',
  transfer: 'Управление актами приема-сдачи и курьерскими передачами',
  'auto-assembly': 'Автопринятие, автоупаковка и накопление накладных по видам доставки — с расписанием и лимитами',
  billing: 'Текущий тариф, баланс, расход дневных кредитов ИИ-ответов и история списаний',
  admin: 'Все подключённые магазины, их тарифы, баланс и статус блокировки',
  referral: 'Ваша реферальная ссылка, приведённые магазины и начисления',
  logs: 'Поток событий системы в реальном времени через WebSocket',
  settings: 'Состояние авторизации Kaspi REST API и сессии Kaspi MC',
  help: 'Как устроены все механики сервиса — простым языком',
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
