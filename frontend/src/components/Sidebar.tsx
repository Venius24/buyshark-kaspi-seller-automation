import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from './shared/LanguageSwitcher';
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
  isSuperadmin?: boolean;
  isReferrer?: boolean;
  isBetaTester?: boolean;
  billingBlocked?: boolean;
  /** Мобильный drawer: открыт ли он сейчас. На десктопе класс ни на что не влияет. */
  isOpen?: boolean;
  onOpenPriceFeedImport: () => void;
  onOpenTour: () => void;
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
  isSuperadmin,
  isReferrer,
  isBetaTester,
  billingBlocked,
  isOpen,
  onOpenPriceFeedImport,
  onOpenTour,
}: SidebarProps) {
  const { t } = useTranslation('sidebar');

  return (
    <aside className={`sidebar ${isOpen ? 'sidebar-open' : ''}`}>
      <div className="sidebar-header">
        <img src="/buyshark-logo.svg" alt="BuyShark" className="sidebar-logo-icon" style={{ objectFit: 'cover' }} />
        <div>
          <div className="sidebar-brand-title">BuyShark</div>
          <div className="sidebar-brand-subtitle">{t('subtitle')}</div>
        </div>
      </div>

      <div className="sidebar-nav">
        <div className="sidebar-group">
          <div className="sidebar-group-title">{t('section_main')}</div>

          <button
            data-tour="stores"
            className={`sidebar-item ${activeTab === 'stores' ? 'active' : ''}`}
            onClick={() => onSelectTab('stores')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">🏪</span>
              <span>{t('stores')}</span>
            </div>
            <span className="sidebar-pill">Multi-Store</span>
          </button>

          <button
            className={`sidebar-item ${activeTab === 'overview' ? 'active' : ''}`}
            onClick={() => onSelectTab('overview')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">📊</span>
              <span>{t('overview')}</span>
            </div>
            <span className="sidebar-pill">Dashboard</span>
          </button>

          <button
            data-tour="products"
            className={`sidebar-item ${activeTab === 'products' ? 'active' : ''}`}
            onClick={() => onSelectTab('products')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">🏷️</span>
              <span>{t('products')}</span>
            </div>
            {totalCount > 0 && <span className="sidebar-pill">{totalCount}</span>}
          </button>

          <button
            className={`sidebar-item ${activeTab === 'dumping' ? 'active' : ''}`}
            onClick={() => onSelectTab('dumping')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">⚡</span>
              <span>{t('auto_dumping')}</span>
            </div>
            <span className="sidebar-pill">Auto</span>
          </button>

          <button
            className={`sidebar-item ${activeTab === 'chatbot' ? 'active' : ''}`}
            onClick={() => onSelectTab('chatbot')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">🤖</span>
              <span>{t('ai_answerer')}</span>
            </div>
            <span className="sidebar-pill" style={{ color: chatbotEnabled ? '#34d399' : '#888' }}>
              {chatbotEnabled ? 'ON' : 'OFF'}
            </span>
          </button>

          <button
            data-tour="chats"
            className={`sidebar-item ${activeTab === 'chats' ? 'active' : ''}`}
            onClick={() => onSelectTab('chats')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">💬</span>
              <span>{t('chats')}</span>
            </div>
          </button>

          <button
            className={`sidebar-item ${activeTab === 'stock-groups' ? 'active' : ''}`}
            onClick={() => onSelectTab('stock-groups')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">🔗</span>
              <span>{t('stock_binding')}</span>
            </div>
            <span className="sidebar-pill">Master</span>
          </button>

          {isBetaTester && (
            <button
              className={`sidebar-item ${activeTab === 'autolist' ? 'active' : ''}`}
              onClick={() => onSelectTab('autolist')}
            >
              <div className="sidebar-item-left">
                <span className="sidebar-icon">✨</span>
                <span>{t('autolist')}</span>
              </div>
              <span className="sidebar-pill">AI</span>
            </button>
          )}

          {isBetaTester && (
            <button
              className={`sidebar-item ${activeTab === 'public-api' ? 'active' : ''}`}
              onClick={() => onSelectTab('public-api')}
            >
              <div className="sidebar-item-left">
                <span className="sidebar-icon">💻</span>
                <span>{t('for_developers')}</span>
              </div>
              <span className="sidebar-pill">API</span>
            </button>
          )}

          <button
            className={`sidebar-item ${activeTab === 'billing' ? 'active' : ''}`}
            onClick={() => onSelectTab('billing')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">💳</span>
              <span>{t('billing_and_balance')}</span>
            </div>
            {billingBlocked && <span className="sidebar-pill" style={{ background: 'rgba(239, 68, 68, 0.2)', color: '#f87171' }}>{t('billing_blocked')}</span>}
          </button>

          {isSuperadmin && (
            <button
              className={`sidebar-item ${activeTab === 'admin' ? 'active' : ''}`}
              onClick={() => onSelectTab('admin')}
            >
              <div className="sidebar-item-left">
                <span className="sidebar-icon">🛡️</span>
                <span>{t('admin_panel')}</span>
              </div>
            </button>
          )}

          {isReferrer && (
            <button
              className={`sidebar-item ${activeTab === 'referral' ? 'active' : ''}`}
              onClick={() => onSelectTab('referral')}
            >
              <div className="sidebar-item-left">
                <span className="sidebar-icon">🤝</span>
                <span>{t('referrals')}</span>
              </div>
            </button>
          )}
        </div>

        <div className="sidebar-group">
          <div className="sidebar-group-title">{t('section_orders')}</div>

          <button
            data-tour="print-batcher"
            className={`sidebar-item ${activeTab === 'print-batcher' ? 'active' : ''}`}
            onClick={() => onSelectTab('print-batcher')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">🖨️</span>
              <span>{t('waybill_joining')}</span>
            </div>
          </button>

          <button
            data-tour="packaging"
            className={`sidebar-item ${activeTab === 'packaging' ? 'active' : ''}`}
            onClick={() => onSelectTab('packaging')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">📦</span>
              <span>{t('packaging')}</span>
            </div>
            {ordersCount > 0 && <span className="sidebar-pill">{ordersCount}</span>}
          </button>

          <button
            className={`sidebar-item ${activeTab === 'auto-assembly' ? 'active' : ''}`}
            onClick={() => onSelectTab('auto-assembly')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">🤖</span>
              <span>{t('auto_assembly')}</span>
            </div>
            <span className="sidebar-pill">Auto</span>
          </button>

          <button
            className={`sidebar-item ${activeTab === 'transfer' ? 'active' : ''}`}
            onClick={() => onSelectTab('transfer')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">🚚</span>
              <span>{t('transfer')}</span>
            </div>
            {transfersCount > 0 && <span className="sidebar-pill">{transfersCount}</span>}
          </button>
        </div>

        <div className="sidebar-group">
          <div className="sidebar-group-title">{t('section_system')}</div>

          <button
            className={`sidebar-item ${activeTab === 'logs' ? 'active' : ''}`}
            onClick={() => onSelectTab('logs')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">📜</span>
              <span>{t('logs_terminal')}</span>
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
              <span>{t('connections_session')}</span>
            </div>
            <span className="sidebar-pill">API</span>
          </button>

          <button
            data-tour="help"
            className={`sidebar-item ${activeTab === 'help' ? 'active' : ''}`}
            onClick={() => onSelectTab('help')}
          >
            <div className="sidebar-item-left">
              <span className="sidebar-icon">❓</span>
              <span>{t('help')}</span>
            </div>
          </button>
        </div>
      </div>

      <div className="sidebar-footer">
        <LanguageSwitcher />
        <button
          type="button"
          onClick={onOpenPriceFeedImport}
          title={t('import_from_pricefeed')}
          style={{
            display: 'flex', alignItems: 'center', gap: '6px', width: '100%',
            padding: '4px 6px', marginBottom: '6px', background: 'transparent',
            border: 'none', borderRadius: '6px', cursor: 'pointer',
            color: 'var(--text-muted)', fontSize: '11px', opacity: 0.6,
            transition: 'opacity 0.15s ease',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.opacity = '1'; }}
          onMouseLeave={(e) => { e.currentTarget.style.opacity = '0.6'; }}
        >
          <span style={{ fontSize: '12px' }}>📥</span>
          <span>{t('import_from_pricefeed')}</span>
        </button>
        <button
          type="button"
          onClick={onOpenTour}
          title={t('show_tour')}
          style={{
            display: 'flex', alignItems: 'center', gap: '6px', width: '100%',
            padding: '4px 6px', marginBottom: '6px', background: 'transparent',
            border: 'none', borderRadius: '6px', cursor: 'pointer',
            color: 'var(--text-muted)', fontSize: '11px', opacity: 0.6,
            transition: 'opacity 0.15s ease',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.opacity = '1'; }}
          onMouseLeave={(e) => { e.currentTarget.style.opacity = '0.6'; }}
        >
          <span style={{ fontSize: '12px' }}>🧭</span>
          <span>{t('show_tour')}</span>
        </button>
        <div className="sidebar-user-card">
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span className="glow-dot glow-dot-success"></span>
            <span style={{ fontSize: '12px', fontWeight: 600, color: '#e5e7eb' }}>{t('kaspi_api_online')}</span>
          </div>
          <button
            className="btn btn-secondary"
            style={{ padding: '4px 8px', fontSize: '11px', color: 'var(--error)' }}
            onClick={onLogout}
            title={t('logout_tooltip')}
          >
            🚪
          </button>
        </div>
      </div>
    </aside>
  );
}
