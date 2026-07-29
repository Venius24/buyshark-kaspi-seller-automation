import type { ActiveTab } from '../types';

interface SidebarProps {
  activeTab: ActiveTab;
  onSelectTab: (tab: ActiveTab) => void;
  onLogout: () => void;
  totalCount: number;
  chatbotEnabled: boolean;
  ordersCount: number;
  transfersCount: number;
  isWsConnected: boolean;
}

export function Sidebar({
  activeTab,
  onSelectTab,
  onLogout,
  totalCount,
  chatbotEnabled,
  ordersCount,
  transfersCount,
  isWsConnected,
}: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="sidebar-logo-icon">K</div>
        <div>
          <div className="sidebar-brand-title">Kaspi SaaS</div>
          <div className="sidebar-brand-subtitle">Lumina Automation</div>
        </div>
      </div>

      <div className="sidebar-nav">
        <div className="sidebar-group">
          <div className="sidebar-group-title">Главное</div>

          <button
            className={`sidebar-item ${activeTab === 'stores' ? 'active' : ''}`}
            onClick={() => onSelectTab('stores')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">🏪</span>
              <span>Магазины Kaspi</span>
            </div>
            <span className="sidebar-pill" style={{ color: '#34d399' }}>Multi-Store</span>
          </button>

          <button
            className={`sidebar-item ${activeTab === 'overview' ? 'active' : ''}`}
            onClick={() => onSelectTab('overview')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">📊</span>
              <span>Обзор</span>
            </div>
            <span className="sidebar-pill">Dashboard</span>
          </button>

          <button
            className={`sidebar-item ${activeTab === 'products' ? 'active' : ''}`}
            onClick={() => onSelectTab('products')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">🏷️</span>
              <span>Товары</span>
            </div>
            {totalCount > 0 && <span className="sidebar-pill">{totalCount}</span>}
          </button>

          <button
            className={`sidebar-item ${activeTab === 'dumping' ? 'active' : ''}`}
            onClick={() => onSelectTab('dumping')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">⚡</span>
              <span>Авто-Демпинг</span>
            </div>
            <span className="sidebar-pill" style={{ color: 'var(--secondary)' }}>Auto</span>
          </button>

          <button
            className={`sidebar-item ${activeTab === 'chatbot' ? 'active' : ''}`}
            onClick={() => onSelectTab('chatbot')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">🤖</span>
              <span>ИИ-Автоответчик</span>
            </div>
            <span className="sidebar-pill" style={{ color: chatbotEnabled ? '#34d399' : '#888' }}>
              {chatbotEnabled ? 'ON' : 'OFF'}
            </span>
          </button>

          <button
            className={`sidebar-item ${activeTab === 'stock-groups' ? 'active' : ''}`}
            onClick={() => onSelectTab('stock-groups')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">🔗</span>
              <span>Связка остатков</span>
            </div>
            <span className="sidebar-pill" style={{ color: '#818cf8' }}>Master</span>
          </button>

          <button
            className={`sidebar-item ${activeTab === 'public-api' ? 'active' : ''}`}
            onClick={() => onSelectTab('public-api')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">💻</span>
              <span>Для разработчиков</span>
            </div>
            <span className="sidebar-pill" style={{ color: '#38bdf8' }}>API</span>
          </button>
        </div>

        <div className="sidebar-group">
          <div className="sidebar-group-title">Заказы & Логистика</div>

          <button
            className={`sidebar-item ${activeTab === 'print-batcher' ? 'active' : ''}`}
            onClick={() => onSelectTab('print-batcher')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">🖨️</span>
              <span>Автосборщик печати</span>
            </div>
            <span className="sidebar-pill" style={{ color: '#10b981' }}>KSTAT</span>
          </button>

          <button
            className={`sidebar-item ${activeTab === 'packaging' ? 'active' : ''}`}
            onClick={() => onSelectTab('packaging')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">📦</span>
              <span>Упаковка</span>
            </div>
            {ordersCount > 0 && <span className="sidebar-pill">{ordersCount}</span>}
          </button>

          <button
            className={`sidebar-item ${activeTab === 'transfer' ? 'active' : ''}`}
            onClick={() => onSelectTab('transfer')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">🚚</span>
              <span>Передача</span>
            </div>
            {transfersCount > 0 && <span className="sidebar-pill">{transfersCount}</span>}
          </button>
        </div>

        <div className="sidebar-group">
          <div className="sidebar-group-title">Система</div>

          <button
            className={`sidebar-item ${activeTab === 'logs' ? 'active' : ''}`}
            onClick={() => onSelectTab('logs')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">📜</span>
              <span>Терминал логов</span>
            </div>
            <span className="sidebar-pill" style={{ background: isWsConnected ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)', color: isWsConnected ? '#34d399' : '#f87171' }}>
              {isWsConnected ? 'LIVE' : 'OFF'}
            </span>
          </button>

          <button
            className={`sidebar-item ${activeTab === 'settings' ? 'active' : ''}`}
            onClick={() => onSelectTab('settings')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">⚙️</span>
              <span>Подключения & Сессия</span>
            </div>
            <span className="sidebar-pill">API</span>
          </button>
        </div>
      </div>

      <div className="sidebar-footer">
        <div className="sidebar-user-card">
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span className="glow-dot glow-dot-success"></span>
            <span style={{ fontSize: '12px', fontWeight: 600, color: '#e5e7eb' }}>Kaspi API Online</span>
          </div>
          <button
            className="btn btn-secondary"
            style={{ padding: '4px 8px', fontSize: '11px', color: 'var(--error)' }}
            onClick={onLogout}
            title="Выйти из аккаунта"
          >
            🚪
          </button>
        </div>
      </div>
    </aside>
  );
}
