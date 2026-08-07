import { apiFetch, API_URL } from '../../api';
import { PriceLeadGauge } from '../PriceLeadGauge';
import { SavedMarginCard } from '../SavedMarginCard';
import { PriceRadarWidget } from '../PriceRadarWidget';
import { SalesStatsWidget } from '../SalesStatsWidget';
import { ScoreRing } from '../ScoreRing';
import { RevenueHeatmap } from '../RevenueHeatmap';
import { useSalesStats } from '../../hooks/useSalesStats';
import { useScoreStats } from '../../hooks/useScoreStats';

interface OverviewTabProps {
  analytics: {
    best_price_share: number;
    winning_count: number;
    total_scanned: number;
    total_saved_margin: number;
  };
  widgetData: any;
  addLog: (message: string) => void;
  mcHasSession: boolean;
  onOpenMcAuth: () => void;
  onOpenProducts: () => void;
  onOpenDumping: () => void;
  onOpenPackaging: () => void;
  onOpenLogs: () => void;
}

export function OverviewTab({
  analytics,
  widgetData,
  addLog,
  mcHasSession,
  onOpenMcAuth,
  onOpenProducts,
  onOpenDumping,
  onOpenPackaging,
  onOpenLogs,
}: OverviewTabProps) {
  const salesStats = useSalesStats();
  const scoreStats = useScoreStats();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
      {/* 🎯 COMPOSITE SCORE RING — "здоровье" магазина одним числом, разворачивается в разбивку */}
      <ScoreRing data={scoreStats} />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '20px' }}>
        <PriceLeadGauge
          percentage={analytics.best_price_share}
          winningCount={analytics.winning_count}
          totalScanned={analytics.total_scanned}
        />
        <SavedMarginCard
          totalSavedMargin={analytics.total_saved_margin}
        />
      </div>

      {/* 🗓️ GITHUB-STYLE КАРТА АКТИВНОСТИ ПРОДАЖ */}
      <RevenueHeatmap heatmap={scoreStats.heatmap} />

      {/* 💰 REAL SALES STATS (from sales_orders, backfilled by sales_sync_worker) */}
      <SalesStatsWidget data={salesStats} />

      {/* ⚔️ LIVE PRICE RADAR (REAL-TIME PRICE WAR MONITOR) */}
      <PriceRadarWidget
        topProduct={widgetData?.price_radar?.top_product}
        events={widgetData?.price_radar?.recent_events}
      />

      <div className="grid-2">
        <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <h2>Статус системы</h2>
          <div className="status-widget">
            <div>
              <h3 style={{ fontSize: '14px', fontWeight: 600 }}>Kaspi Merchant API (REST)</h3>
              <div style={{ display: 'flex', alignItems: 'center', marginTop: '6px' }}>
                <span className="glow-dot glow-dot-success"></span>
                <span className="badge badge-success">Сессия активна</span>
              </div>
            </div>
            <button className="btn btn-secondary" style={{ padding: '6px 10px', fontSize: '12px' }} onClick={() => {
              addLog('Проверка состояния сессии REST API...');
              apiFetch(`${API_URL}/api/verify-session`, { method: 'POST' })
                .then(r => r.json())
                .then(d => addLog(d.valid ? 'Сессия REST API активна ✓' : 'Сессия REST API невалидна ✗'))
                .catch(() => addLog('Ошибка проверки сессии REST API'));
            }}>
              🔄
            </button>
          </div>

          <div className="status-widget">
            <div>
              <h3 style={{ fontSize: '14px', fontWeight: 600 }}>Kaspi MC (сессионные куки)</h3>
              <div style={{ display: 'flex', alignItems: 'center', marginTop: '6px' }}>
                <span className={`glow-dot ${mcHasSession ? 'glow-dot-success' : 'glow-dot-warning'}`}></span>
                {mcHasSession ? (
                  <span className="badge badge-success">Активны</span>
                ) : (
                  <span className="badge badge-warning">Требуется вход</span>
                )}
              </div>
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button className="btn btn-primary" style={{ padding: '6px 10px', fontSize: '12px' }} onClick={onOpenMcAuth}>
                🔑 {mcHasSession ? 'Войти заново' : 'Войти'}
              </button>
            </div>
          </div>
        </div>

        <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h2>Быстрые действия</h2>
            <span className="badge badge-success">Lumina Engine</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginTop: '8px' }}>
            <button className="btn btn-primary" style={{ padding: '14px' }} onClick={onOpenProducts}>
              🏷️ Открыть каталог
            </button>
            <button className="btn btn-secondary" style={{ padding: '14px' }} onClick={onOpenDumping}>
              ⚡ Настроить демпинг
            </button>
            <button className="btn btn-secondary" style={{ padding: '14px' }} onClick={onOpenPackaging}>
              📦 Сборка заказов
            </button>
            <button className="btn btn-secondary" style={{ padding: '14px' }} onClick={onOpenLogs}>
              📜 Открыть консоль
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
