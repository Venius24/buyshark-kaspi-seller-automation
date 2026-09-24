import React, { lazy, Suspense } from 'react';
import type { TFunction } from 'i18next';
import { RefreshCw } from 'lucide-react';
import { apiFetch, API_URL } from '../api';
import { ScoreRing } from '../components/ScoreRing';
import { SavedMarginCard } from '../components/SavedMarginCard';
import { PriceLeadGauge } from '../components/PriceLeadGauge';
import { RevenueHeatmap } from '../components/RevenueHeatmap';
import { PriceRadarWidget } from '../components/PriceRadarWidget';
import { AbcXyzWidget } from '../components/AbcXyzWidget';
import { StockForecastWidget } from '../components/StockForecastWidget';
import { CompetitorLeaderboard } from '../components/CompetitorLeaderboard';
import { CatalogCoverageGauge } from '../components/CatalogCoverageGauge';
import { HourlyHeatmap } from '../components/HourlyHeatmap';
import { ChatActivityHeatmap } from '../components/ChatActivityHeatmap';
import { HelpHint } from '../components/shared/HelpHint';

// Оба виджета тянут recharts (~380 КБ, почти треть сборки) — грузим их отдельным
// куском, только когда «Обзор» реально отрисован.
const SalesStatsWidget = lazy(() =>
  import('../components/SalesStatsWidget').then((m) => ({ default: m.SalesStatsWidget })),
);
const ReviewCampaignWidget = lazy(() =>
  import('../components/ReviewCampaignWidget').then((m) => ({ default: m.ReviewCampaignWidget })),
);

const ChartWidgetFallback: React.FC = () => (
  <div className="glass-card" style={{ minHeight: '180px' }} aria-busy="true" />
);

export type ChartType = 'bar' | 'line' | 'area' | 'pie' | 'donut';
export const PERIOD_OPTIONS = [7, 14, 30, 90] as const;
export type Period = (typeof PERIOD_OPTIONS)[number];

// Оверрайды поверх дефолта — то, что реально хранится в БД на пользователя.
export interface WidgetConfig {
  visible?: boolean;
  order?: number;
  chartType?: ChartType;
  metrics?: string[];
  period?: Period;
}

// Всё, что нужно рендер-функциям виджетов — данные аналитики и колбэки
// шапки, собранные один раз в OverviewTab и прокинутые в реестр.
export interface OverviewRenderCtx {
  t: TFunction;
  analytics: {
    best_price_share: number;
    winning_count: number;
    total_scanned: number;
    total_saved_margin: number;
  };
  widgetData: any;
  scoreStats: any;
  qualityMetrics: any;
  abcXyzStats: any;
  insightsStats: any;
  addLog: (message: string) => void;
  mcHasSession: boolean;
  onOpenMcAuth: () => void;
  onOpenProducts: () => void;
  onOpenDumping: () => void;
  onOpenPackaging: () => void;
  onOpenLogs: () => void;
}

export interface WidgetDef {
  id: string;
  title: string;
  defaultVisible: boolean;
  defaultOrder: number;
  supportsChartType?: ChartType[];
  supportsMetrics?: { key: string; label: string }[];
  supportsPeriod?: boolean;
  render: (ctx: OverviewRenderCtx, config: WidgetConfig) => React.ReactNode;
}

export const WIDGET_REGISTRY: WidgetDef[] = [
  {
    id: 'score_ring',
    title: 'Общий рейтинг',
    defaultVisible: true,
    defaultOrder: 0,
    render: (ctx) => (
      <div className="grid-hero" key="score_ring">
        <ScoreRing data={ctx.scoreStats} quality={ctx.qualityMetrics} />
        <SavedMarginCard totalSavedMargin={ctx.analytics.total_saved_margin} />
      </div>
    ),
  },
  {
    id: 'price_lead_gauge',
    title: 'Доля лучшей цены и карта активности',
    defaultVisible: true,
    defaultOrder: 1,
    render: (ctx) => (
      <div className="grid-hero grid-hero-rev" key="price_lead_gauge">
        <PriceLeadGauge
          percentage={ctx.analytics.best_price_share}
          winningCount={ctx.analytics.winning_count}
          totalScanned={ctx.analytics.total_scanned}
        />
        <RevenueHeatmap heatmap={ctx.scoreStats.heatmap} />
      </div>
    ),
  },
  {
    id: 'sales_stats',
    title: 'Реальные продажи',
    defaultVisible: true,
    defaultOrder: 2,
    supportsChartType: ['bar', 'line', 'area', 'pie', 'donut'],
    supportsMetrics: [
      { key: 'revenue', label: 'Выручка' },
      { key: 'orders', label: 'Заказы' },
    ],
    supportsPeriod: true,
    render: (_ctx, config) => (
      <Suspense key="sales_stats" fallback={<ChartWidgetFallback />}>
        <SalesStatsWidget
          period={config.period || 30}
          chartType={config.chartType || 'bar'}
          metric={((config.metrics && config.metrics[0]) || 'revenue') as 'revenue' | 'orders'}
        />
      </Suspense>
    ),
  },
  {
    id: 'price_radar',
    title: 'Живой мониторинг цен',
    defaultVisible: true,
    defaultOrder: 3,
    render: (ctx) => (
      <PriceRadarWidget
        key="price_radar"
        topProduct={ctx.widgetData?.price_radar?.top_product}
        events={ctx.widgetData?.price_radar?.recent_events}
      />
    ),
  },
  {
    id: 'abc_xyz',
    title: 'ABC/XYZ-анализ товаров',
    defaultVisible: true,
    defaultOrder: 4,
    render: (ctx) => <AbcXyzWidget key="abc_xyz" data={ctx.abcXyzStats} />,
  },
  {
    id: 'stock_forecast',
    title: 'Прогноз остатков и закуп',
    defaultVisible: true,
    // Дробный порядок намеренно: виджет должен встать сразу после ABC/XYZ, но
    // перенумеровать соседей нельзя — у пользователей, которые уже двигали
    // дашборд, в БД лежат СВОИ order, и сдвиг дефолтов перетасовал бы им
    // ленту. sort по числу с 4.5 справляется, а первый же ручной reorder
    // всё равно перезапишет порядок целыми числами.
    defaultOrder: 4.5,
    render: () => <StockForecastWidget key="stock_forecast" />,
  },
  {
    id: 'competitor_leaderboard',
    title: 'Лидерборд конкурентов и покрытие каталога',
    defaultVisible: true,
    defaultOrder: 5,
    render: (ctx) => (
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '20px' }} key="competitor_leaderboard">
        <CompetitorLeaderboard data={ctx.insightsStats.competitor_leaderboard} />
        <CatalogCoverageGauge data={ctx.insightsStats.catalog_coverage} />
      </div>
    ),
  },
  {
    id: 'hourly_heatmap',
    title: 'Продажи по часам и дням недели',
    defaultVisible: true,
    defaultOrder: 6,
    render: (ctx) => <HourlyHeatmap key="hourly_heatmap" grid={ctx.insightsStats.hourly_heatmap} />,
  },
  {
    id: 'chat_activity',
    title: 'Активность авто-чата',
    defaultVisible: true,
    defaultOrder: 7,
    render: (ctx) => <ChatActivityHeatmap key="chat_activity" data={ctx.insightsStats.chat_activity} />,
  },
  {
    id: 'review_campaign',
    title: 'Рассылка «Отзыв»: воронка и рейтинг',
    defaultVisible: true,
    defaultOrder: 8,
    render: () => (
      <Suspense key="review_campaign" fallback={<ChartWidgetFallback />}>
        <ReviewCampaignWidget />
      </Suspense>
    ),
  },
  {
    id: 'system_status',
    title: 'Статус подключений',
    defaultVisible: true,
    defaultOrder: 9,
    render: (ctx) => (
      <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }} key="system_status">
        <h2 style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          {ctx.t('system_status')}
          <HelpHint text="Статус двух независимых подключений: REST API — сессия входа в сам кабинет (BuyShark), MC-cookies — доступ к личному кабинету Kaspi (нужен для загрузки товаров, заказов, изменения цен). Если MC истёк — жмите «Войти» и подтвердите вход в Kaspi заново." />
        </h2>
        <div className="status-widget">
          <div>
            <h3 style={{ fontSize: '14px', fontWeight: 600 }}>{ctx.t('rest_api_title')}</h3>
            <div style={{ display: 'flex', alignItems: 'center', marginTop: '6px' }}>
              <span className="glow-dot glow-dot-success"></span>
              <span className="badge badge-success">{ctx.t('badge_active')}</span>
            </div>
          </div>
          <button
            className="btn btn-secondary"
            style={{ padding: '6px 10px', fontSize: '12px' }}
            onClick={() => {
              ctx.addLog(ctx.t('log_checking'));
              apiFetch(`${API_URL}/api/verify-session`, { method: 'POST' })
                .then((r) => r.json())
                .then((d) => ctx.addLog(d.valid ? ctx.t('log_active') : ctx.t('log_invalid')))
                .catch(() => ctx.addLog(ctx.t('log_error')));
            }}
          >
            <RefreshCw size={14} />
          </button>
        </div>

        <div className="status-widget">
          <div>
            <h3 style={{ fontSize: '14px', fontWeight: 600 }}>{ctx.t('mc_cookies_title')}</h3>
            <div style={{ display: 'flex', alignItems: 'center', marginTop: '6px' }}>
              <span className={`glow-dot ${ctx.mcHasSession ? 'glow-dot-success' : 'glow-dot-warning'}`}></span>
              {ctx.mcHasSession ? (
                <span className="badge badge-success">{ctx.t('badge_mc_active')}</span>
              ) : (
                <span className="badge badge-warning">{ctx.t('badge_mc_required')}</span>
              )}
            </div>
          </div>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button className="btn btn-primary" style={{ padding: '6px 10px', fontSize: '12px' }} onClick={ctx.onOpenMcAuth}>
              {ctx.mcHasSession ? ctx.t('btn_mc_login_again') : ctx.t('btn_mc_login')}
            </button>
          </div>
        </div>
      </div>
    ),
  },
  {
    id: 'quick_actions',
    title: 'Быстрые действия',
    defaultVisible: true,
    defaultOrder: 10,
    render: (ctx) => (
      <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '12px' }} key="quick_actions">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2>{ctx.t('quick_actions')}</h2>
          <span className="badge badge-success">Lumina Engine</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginTop: '8px' }}>
          <button className="btn btn-primary" style={{ padding: '14px' }} onClick={ctx.onOpenProducts}>
            {ctx.t('btn_open_catalog')}
          </button>
          <button className="btn btn-secondary" style={{ padding: '14px' }} onClick={ctx.onOpenDumping}>
            {ctx.t('btn_setup_dumping')}
          </button>
          <button className="btn btn-secondary" style={{ padding: '14px' }} onClick={ctx.onOpenPackaging}>
            {ctx.t('btn_pack_orders')}
          </button>
          <button className="btn btn-secondary" style={{ padding: '14px' }} onClick={ctx.onOpenLogs}>
            {ctx.t('btn_open_logs')}
          </button>
        </div>
      </div>
    ),
  },
];

export const WIDGET_IDS = WIDGET_REGISTRY.map((w) => w.id);
