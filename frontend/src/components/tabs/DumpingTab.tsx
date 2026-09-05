import type { FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import type { Product, DumpingConfig, DumpingFacets, DumpingFilters, KaspiQuotaStatus } from '../../types';
import { Pagination } from '../shared/Pagination';
import { ProductCardHeader } from '../shared/ProductCardHeader';
import { PriceWarSummary } from '../PriceWarSummary';
import { usePriceWarStats, leaderPrice, type PriceWarProduct } from '../../hooks/usePriceWarStats';
import { HelpHint } from '../shared/HelpHint';
import { ParcelLoader } from '../shared/ParcelLoader';
import { DumpingFilterBar } from './DumpingFilterBar';

const EMPTY_CONFIG: DumpingConfig = {
  min_price: 0, max_price: 0, dump_step: 2, enabled: false,
  mode: 'TOP_1', target_position: 1, max_delivery_days: null,
  min_seller_rating: null, same_city_only: false, blacklisted_merchants: []
};

function getStatusBadge(status: string, t: (key: string) => string) {
  switch (status) {
    case 'WINNING_1ST':
      return <span className="badge" style={{ background: 'rgba(16, 185, 129, 0.15)', color: '#10b981', border: '1px solid rgba(16, 185, 129, 0.3)' }}>{t('status_winning_1st')}</span>;
    case 'HOLDING_2ND':
      return <span className="badge" style={{ background: 'rgba(59, 130, 246, 0.15)', color: '#3b82f6', border: '1px solid rgba(59, 130, 246, 0.3)' }}>{t('status_holding_2nd')}</span>;
    case 'TARGET_POS_REACHED':
      return <span className="badge" style={{ background: 'rgba(168, 85, 247, 0.15)', color: '#a855f7', border: '1px solid rgba(168, 85, 247, 0.3)' }}>{t('status_target_reached')}</span>;
    case 'AT_MIN_PRICE':
      return <span className="badge" style={{ background: 'rgba(245, 158, 11, 0.15)', color: '#f59e0b', border: '1px solid rgba(245, 158, 11, 0.3)' }}>{t('status_at_min_price')}</span>;
    case 'MAX_PRICE_SINGLE':
      return <span className="badge" style={{ background: 'rgba(236, 72, 153, 0.15)', color: '#ec4899', border: '1px solid rgba(236, 72, 153, 0.3)' }}>{t('status_max_price_single')}</span>;
    default:
      return <span className="badge" style={{ background: 'rgba(255, 255, 255, 0.05)', color: '#888', border: '1px solid rgba(255, 255, 255, 0.1)' }}>{t('status_disabled')}</span>;
  }
}

const badgeBase: React.CSSProperties = {
  background: 'rgba(255,255,255,0.05)',
  color: '#ccc',
  border: '1px solid rgba(255,255,255,0.08)',
};

/** "5 конкурентов · мы 2-е · проверено 05.09 14:30" — расклад последнего скана. */
function CompetitorBadge({ product, t }: { product: Product; t: (key: string, opts?: Record<string, unknown>) => string }) {
  const pw = product.price_war;
  // null — товар ни разу не попадал в скан радара. Это НЕ "0 конкурентов",
  // и рисовать здесь ноль значило бы врать продавцу.
  if (!pw) {
    return (
      <span className="badge" style={{ ...badgeBase, opacity: 0.6 }} title={t('badge_no_scan_hint')}>
        {t('badge_no_scan')}
      </span>
    );
  }
  const checked = pw.checked_at ? new Date(pw.checked_at + 'Z') : null;
  const rankPart = pw.our_rank ? t('badge_our_rank', { rank: pw.our_rank }) : t('badge_rank_unknown');
  return (
    <span
      className="badge"
      style={{
        ...badgeBase,
        color: pw.our_rank === 1 ? '#10b981' : '#ccc',
        borderColor: pw.our_rank === 1 ? 'rgba(16,185,129,0.3)' : 'rgba(255,255,255,0.08)',
      }}
      title={checked ? t('badge_checked_at', { at: checked.toLocaleString('ru-RU') }) : ''}
    >
      {t('badge_competitors', { n: pw.competitors_count })} · {rankPart}
    </span>
  );
}

/** Бейдж "цена упёрлась в минимум / стоит на максимуме". */
function PriceStateBadge({ product, t }: { product: Product; t: (key: string) => string }) {
  if (product.price_state === 'AT_MIN') {
    return (
      <span className="badge" style={{ background: 'rgba(245,158,11,0.15)', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.3)' }}>
        {t('badge_at_min')}
      </span>
    );
  }
  if (product.price_state === 'AT_MAX') {
    return (
      <span className="badge" style={{ background: 'rgba(59,130,246,0.15)', color: '#3b82f6', border: '1px solid rgba(59,130,246,0.3)' }}>
        {t('badge_at_max')}
      </span>
    );
  }
  return null;
}

interface DumpingTabProps {
  products: Product[];
  fetchingProducts: boolean;
  totalCount: number;
  nextRunSeconds: number | null;
  kaspiQuota: KaspiQuotaStatus | null;
  queuePending: number;
  formatTime: (seconds: number | null) => string;
  isRunningDumping: boolean;
  onRunDumpingCycle: () => void;
  searchQuery: string;
  onSearchQueryChange: (query: string) => void;
  onSearchSubmit: (e: FormEvent) => void;
  dumpingOnlyFilter: boolean;
  onDumpingOnlyFilterChange: (val: boolean) => void;
  filters: DumpingFilters;
  onFiltersChange: (next: DumpingFilters) => void;
  facets: DumpingFacets | null;
  competitorsRange: { min: number | null; max: number | null } | null;
  onRefreshConfigs: () => void;
  dumpingConfigs: Record<string, DumpingConfig>;
  expandedSkus: Set<string>;
  onToggleExpanded: (sku: string) => void;
  expandedFilterSkus: Set<string>;
  onToggleFiltersExpanded: (sku: string) => void;
  selectedModes: Record<string, string>;
  onModeChange: (sku: string, mode: string) => void;
  onSaveDumpingConfig: (
    sku: string,
    minPrice: number,
    maxPrice: number,
    step: number,
    enabled: boolean,
    mode: string,
    targetPosition: number,
    maxDeliveryDays: number | null,
    minSellerRating: number | null,
    sameCityOnly: boolean,
    blacklistedMerchants: string[],
    displaySku: string
  ) => void;
  currentPage: number;
  totalPages: number;
  onPageChange: (page: number) => void;
}

export function DumpingTab({
  products,
  fetchingProducts,
  totalCount,
  nextRunSeconds,
  kaspiQuota,
  queuePending,
  formatTime,
  isRunningDumping,
  onRunDumpingCycle,
  searchQuery,
  onSearchQueryChange,
  onSearchSubmit,
  dumpingOnlyFilter,
  onDumpingOnlyFilterChange,
  filters,
  onFiltersChange,
  facets,
  competitorsRange,
  onRefreshConfigs,
  dumpingConfigs,
  expandedSkus,
  onToggleExpanded,
  expandedFilterSkus,
  onToggleFiltersExpanded,
  selectedModes,
  onModeChange,
  onSaveDumpingConfig,
  currentPage,
  totalPages,
  onPageChange,
}: DumpingTabProps) {
  const { t } = useTranslation('tab_dumping');
  const priceWarStats = usePriceWarStats();
  const priceWarBySku: Record<string, PriceWarProduct> = {};
  priceWarStats.products.forEach((p) => { priceWarBySku[p.sku] = p; });
  return (
    <>
      <div style={{ marginBottom: '20px' }}>
        <PriceWarSummary data={priceWarStats} />
      </div>

      {/* Header with Search, Filter & Page Size */}
      <div style={{ borderBottom: '1px solid rgba(255,255,255,0.06)', paddingBottom: '16px', marginBottom: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <h2 style={{ fontSize: '16px', display: 'flex', alignItems: 'center', gap: '6px' }}>
            {totalCount ? t('title').replace('%count%', String(totalCount)) : t('title_no_products')}
            <HelpHint text="Автодемпинг сам снижает цену товара шагами до тех пор, пока не займёт нужную позицию в выдаче Kaspi (например, 1-е место или дешевле всех), но не ниже указанного вами минимума. Работает по расписанию — не нужно проверять цены конкурентов вручную." />
          </h2>

          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
            {nextRunSeconds !== null && (
              <div style={{
                fontSize: '13px',
                color: 'var(--text-muted)',
                background: 'rgba(255,255,255,0.04)',
                padding: '6px 12px',
                borderRadius: '8px',
                border: '1px solid rgba(255,255,255,0.06)',
                display: 'flex',
                alignItems: 'center',
                gap: '6px'
              }}>
                <span className="glow-dot glow-dot-success" style={{ display: 'inline-block', position: 'static', marginRight: '4px' }}></span>
                {t('next_run')} <strong style={{ color: 'var(--success)', fontFamily: 'monospace' }}>{formatTime(nextRunSeconds)}</strong>
              </div>
            )}

            {kaspiQuota && (() => {
              // Kaspi разрешает 250 обновлений товара за фиксированное
              // 30-минутное окно на ВЕСЬ магазин — одна касса на демпинг и на
              // связку остатков. Из них reserved_for_withdraw держится под
              // снятие с продажи, поэтому обычные записи упираются раньше:
              // именно этот, а не полный лимит показываем как «потолок».
              const usableLimit = Math.max(0, kaspiQuota.limit - kaspiQuota.reserved_for_withdraw);
              const usedRatio = usableLimit > 0 ? kaspiQuota.used / usableLimit : 1;
              const color = usedRatio >= 1 ? 'var(--danger)'
                : usedRatio >= 0.75 ? 'var(--warning)'
                : 'var(--success)';
              // used — счётчик самого Kaspi, общий на весь кабинет: туда
              // попадают записи любых других интеграций, работающих с тем же
              // магазином. Раньше показывали только его, и чужой расход
              // читался как наш (замер 2026-08-13: 46 записей в окне при нуле
              // наших). Поэтому наш собственный расход — первым и крупно.
              const usedByOthers = Math.max(0, kaspiQuota.used - kaspiQuota.used_by_us);
              return (
                <div
                  title={
                    `Лимит Kaspi: ${kaspiQuota.limit} обновлений товара за 30 минут на весь магазин.\n\n` +
                    `Записал наш сервис: ${kaspiQuota.used_by_us}.\n` +
                    `Записали другие интеграции этого же кабинета: ${usedByOthers}.\n` +
                    `Итого израсходовано: ${kaspiQuota.used}. Осталось: ${kaspiQuota.remaining}.\n\n` +
                    `Из лимита ${kaspiQuota.reserved_for_withdraw} зарезервировано под снятие с продажи — ` +
                    `обычные изменения цены и остатка останавливаются на ${usableLimit}.\n` +
                    `Счётчик обнулится через ${formatTime(kaspiQuota.reset_in_seconds)}.\n` +
                    `Наш счёт переживает перезапуск сервиса — восстанавливается по истории отправок.` +
                    (queuePending > 0 ? `\n\nЖдут отправки: ${queuePending}.` : '')
                  }
                  style={{
                    fontSize: '13px',
                    color: 'var(--text-muted)',
                    background: 'rgba(255,255,255,0.04)',
                    padding: '6px 12px',
                    borderRadius: '8px',
                    border: '1px solid rgba(255,255,255,0.06)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    cursor: 'help',
                  }}
                >
                  <span>Записали мы:</span>
                  <strong style={{ color: 'var(--success)', fontFamily: 'monospace' }}>
                    {kaspiQuota.used_by_us}
                  </strong>
                  <span style={{ opacity: 0.6, borderLeft: '1px solid rgba(255,255,255,0.12)', paddingLeft: '8px' }}>
                    окно Kaspi{' '}
                    <strong style={{ color, fontFamily: 'monospace' }}>
                      {kaspiQuota.used}/{usableLimit}
                    </strong>
                  </span>
                  <span style={{ opacity: 0.6 }}>сброс {formatTime(kaspiQuota.reset_in_seconds)}</span>
                  {queuePending > 0 && (
                    <span
                      style={{
                        color: 'var(--warning)',
                        fontFamily: 'monospace',
                        borderLeft: '1px solid rgba(255,255,255,0.12)',
                        paddingLeft: '8px',
                      }}
                    >
                      ⏳ {queuePending}
                    </span>
                  )}
                </div>
              );
            })()}

            <button
              type="button"
              className="btn"
              style={{
                background: 'linear-gradient(135deg, #8b5cf6, #6d28d9)',
                boxShadow: '0 4px 12px rgba(139, 92, 246, 0.25)',
                padding: '10px 20px',
                color: '#fff',
                fontWeight: 600,
                borderRadius: '8px'
              }}
              onClick={onRunDumpingCycle}
              disabled={isRunningDumping || products.length === 0}
            >
              {isRunningDumping ? t('btn_calculating') : t('btn_run_dumping')}
            </button>
          </div>
        </div>

        {/* Search Bar */}
        <form onSubmit={onSearchSubmit} style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
          <input
            type="text"
            className="form-input"
            placeholder={t('search_placeholder')}
            value={searchQuery}
            onChange={(e) => onSearchQueryChange(e.target.value)}
            style={{ flexGrow: 1 }}
          />

          {/* Dumping Only Filter Toggle */}
          <div style={{ display: 'flex', background: 'rgba(255,255,255,0.04)', padding: '3px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.06)' }}>
            <button
              type="button"
              className="btn"
              style={{
                background: !dumpingOnlyFilter ? 'var(--primary)' : 'transparent',
                padding: '6px 14px', fontSize: '12px', borderRadius: '6px',
                color: '#fff', border: 'none', cursor: 'pointer', transition: 'all 0.2s ease'
              }}
              onClick={() => onDumpingOnlyFilterChange(false)}
            >
              {t('filter_all_products')}
            </button>
            <button
              type="button"
              className="btn"
              style={{
                background: dumpingOnlyFilter ? 'var(--primary)' : 'transparent',
                padding: '6px 14px', fontSize: '12px', borderRadius: '6px',
                color: '#fff', border: 'none', cursor: 'pointer', transition: 'all 0.2s ease'
              }}
              onClick={() => onDumpingOnlyFilterChange(true)}
            >
              {t('filter_active_dumping')}
            </button>
          </div>

          <button type="submit" className="btn btn-primary" style={{ padding: '10px 16px' }}>
            {t('btn_search')}
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            style={{ padding: '10px 14px' }}
            onClick={onRefreshConfigs}
          >
            {t('btn_refresh_config')}
          </button>
        </form>

        <DumpingFilterBar
          filters={filters}
          onChange={onFiltersChange}
          facets={facets}
          competitorsRange={competitorsRange}
        />
      </div>

      {/* Products List with Dumping Controls */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', flexGrow: 1 }}>
        {fetchingProducts && products.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
            <div style={{ marginBottom: '16px' }}>
              <ParcelLoader />
            </div>
            <p>{t('loading_message')}</p>
          </div>
        ) : products.length === 0 && !fetchingProducts ? (
          <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
            <p>{t('no_products')}</p>
          </div>
        ) : (
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '16px',
            opacity: fetchingProducts ? 0.6 : 1,
            pointerEvents: fetchingProducts ? 'none' : 'auto',
            transition: 'opacity 0.2s ease-in-out'
          }}>
            {products.map((product) => {
              const sku = product.sku;
              // Демпинг-движок (backend/dumper/radar.py) матчит конфиг сперва по
              // merchantSku и только потом по Kaspi sku — импорт из PriceFeed
              // всегда пишет по merchantSku. Читаем/пишем конфиг по тому же
              // приоритету, иначе для товаров с merchantSku !== sku панель
              // показывает пустые поля, а ручное сохранение создаёт
              // осиротевший дубль-конфиг, который движок не увидит.
              const cfgKey = (product.merchantSku && dumpingConfigs[product.merchantSku])
                ? product.merchantSku
                : (product.merchantSku || sku);
              const isExpanded = expandedSkus.has(sku);
              const isFiltersExpanded = expandedFilterSkus.has(sku);
              const cfg = dumpingConfigs[cfgKey] || EMPTY_CONFIG;
              const hasConfig = !!dumpingConfigs[cfgKey];
              const isDumpingActive = hasConfig && cfg.enabled && cfg.mode !== 'OFF';
              const dStatus = product.dumping_status || (isDumpingActive ? 'WINNING_1ST' : 'OFF');
              const selectedMode = selectedModes[sku] ?? (cfg.mode || 'TOP_1');
              const pw = priceWarBySku[product.merchantSku || ''] || priceWarBySku[sku] || priceWarBySku[product.kaspiSku || ''];
              const pwLeaderPrice = pw ? leaderPrice(pw) : null;

              return (
                <div
                  key={product.id}
                  className="order-card"
                  style={{ padding: '16px', margin: 0 }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div style={{ maxWidth: '70%' }}>
                      <ProductCardHeader name={product.name} sku={product.sku} kaspiSku={product.kaspiSku} merchantSku={product.merchantSku} image={product.image} />

                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginTop: '8px' }}>
                        {getStatusBadge(dStatus, t)}

                        <span className="badge" style={badgeBase}>
                          {t('mode_label')} {cfg.mode || 'TOP_1'}
                        </span>

                        {product.dumping_config_source === 'group' && (
                          <span className="badge" style={badgeBase} title={t('badge_from_group_hint')}>
                            {t('badge_from_group')}
                          </span>
                        )}

                        <PriceStateBadge product={product} t={t} />
                        <CompetitorBadge product={product} t={t} />

                        {product.url && (
                          <a
                            href={product.url}
                            target="_blank"
                            rel="noreferrer"
                            style={{ fontSize: '12px', color: '#3b82f6', textDecoration: 'none' }}
                          >
                            {t('link_product_card')}
                          </a>
                        )}
                      </div>
                    </div>

                    <div style={{ textAlign: 'right', display: 'flex', alignItems: 'center', gap: '16px' }}>
                      <div>
                        <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{t('leader_price_label')}:</div>
                        <div style={{ fontSize: '16px', fontWeight: 700, color: '#9ca3af' }}>
                          {pwLeaderPrice !== null ? `${pwLeaderPrice.toLocaleString('ru-RU')} ₸` : '—'}
                        </div>
                      </div>
                      <div>
                        <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{t('current_price_label')}:</div>
                        <div style={{ fontSize: '16px', fontWeight: 700, color: '#fff' }}>
                          {product.price && product.price !== "0 ₸" ? product.price : (product.price_num ? `${product.price_num} ₸` : '—')}
                        </div>
                      </div>
                      <button
                        type="button"
                        className="btn btn-secondary"
                        style={{ padding: '8px 12px', fontSize: '12px', borderColor: isExpanded ? 'var(--secondary)' : 'transparent' }}
                        onClick={() => onToggleExpanded(sku)}
                      >
                        {isExpanded ? t('btn_close_config') : t('btn_configure')}
                      </button>
                    </div>
                  </div>

                  {isExpanded && (
                    <div
                      key={`${sku}-${cfg.min_price}-${cfg.max_price}-${cfg.dump_step}-${cfg.enabled}-${cfg.mode}`}
                      style={{
                        marginTop: '16px',
                        paddingTop: '16px',
                        borderTop: '1px solid rgba(255,255,255,0.05)',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '14px'
                      }}
                    >
                      <h4 style={{ fontSize: '13px', fontWeight: 600, color: 'var(--secondary)' }}>
                        {t('dumping_strategy_title')}
                      </h4>

                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '12px' }}>
                        <div>
                          <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>{t('dumping_mode_label')}</label>
                          <select
                            id={`mode-${sku}`}
                            className="form-input"
                            value={selectedMode}
                            onChange={(e) => onModeChange(sku, e.target.value)}
                            style={{ background: 'var(--input-bg)', color: '#fff', border: '1px solid var(--input-border)', height: '38px', borderRadius: '8px' }}
                          >
                            <option value="TOP_1">{t('mode_top1')}</option>
                            <option value="TOP_2">{t('mode_top2')}</option>
                            <option value="MATCH_FIRST">{t('mode_match_first')}</option>
                            <option value="TARGET_POSITION">{t('mode_target_position')}</option>
                            <option value="OFF">{t('mode_off')}</option>
                          </select>
                        </div>

                        {selectedMode === 'TARGET_POSITION' && (
                          <div>
                            <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>{t('target_position_label')}</label>
                            <input
                              type="number"
                              id={`target_pos-${sku}`}
                              className="form-input"
                              defaultValue={cfg.target_position || 1}
                              placeholder={t('target_position_placeholder')}
                            />
                          </div>
                        )}

                        <div>
                          <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>{t('min_price_label')}</label>
                          <input
                            type="number"
                            id={`min-${sku}`}
                            className="form-input"
                            defaultValue={cfg.min_price || ''}
                            placeholder={t('min_price_placeholder')}
                          />
                        </div>
                        <div>
                          <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>{t('max_price_label')}</label>
                          <input
                            type="number"
                            id={`max-${sku}`}
                            className="form-input"
                            defaultValue={cfg.max_price || ''}
                            placeholder={t('max_price_placeholder')}
                          />
                        </div>
                        <div>
                          <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>{t('dump_step_label')}</label>
                          <input
                            type="number"
                            id={`step-${sku}`}
                            className="form-input"
                            defaultValue={cfg.dump_step ?? 2}
                          />
                        </div>
                      </div>

                      {/* Дополнительные фильтры конкурентов (Скрыты под кнопкой "...") */}
                      <div style={{ borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '10px', marginTop: '4px' }}>
                        <button
                          type="button"
                          className="btn btn-secondary"
                          style={{
                            padding: '6px 12px',
                            fontSize: '11px',
                            borderRadius: '6px',
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: '8px',
                            background: isFiltersExpanded ? 'rgba(59, 130, 246, 0.12)' : 'rgba(255, 255, 255, 0.03)',
                            borderColor: isFiltersExpanded ? 'rgba(59, 130, 246, 0.3)' : 'rgba(255, 255, 255, 0.08)',
                            color: isFiltersExpanded ? '#60a5fa' : '#ccc'
                          }}
                          onClick={() => onToggleFiltersExpanded(sku)}
                        >
                          <span>{t('competitor_filters_title')}</span>
                          <span style={{ fontSize: '12px', fontWeight: 'bold' }}>{isFiltersExpanded ? '▲' : '•••'}</span>
                        </button>

                        {isFiltersExpanded && (
                          <div style={{ marginTop: '12px', display: 'flex', flexDirection: 'column', gap: '12px', background: 'rgba(0,0,0,0.2)', padding: '12px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.04)' }}>
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '12px' }}>
                              <div>
                                <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>{t('max_delivery_days_label')}</label>
                                <input
                                  type="number"
                                  id={`max_deliv-${sku}`}
                                  className="form-input"
                                  defaultValue={cfg.max_delivery_days ?? ''}
                                  placeholder={t('max_delivery_days_placeholder')}
                                />
                              </div>

                              <div>
                                <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>{t('min_seller_rating_label')}</label>
                                <input
                                  type="number"
                                  step="0.1"
                                  id={`min_rating-${sku}`}
                                  className="form-input"
                                  defaultValue={cfg.min_seller_rating ?? ''}
                                  placeholder={t('min_seller_rating_placeholder')}
                                />
                              </div>

                              <div style={{ gridColumn: 'span 2' }}>
                                <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>{t('blacklist_label')}</label>
                                <input
                                  type="text"
                                  id={`blacklist-${sku}`}
                                  className="form-input"
                                  defaultValue={(cfg.blacklisted_merchants || []).join(', ')}
                                  placeholder={t('blacklist_placeholder')}
                                />
                              </div>
                            </div>

                            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: '12px', color: '#fff' }}>
                              <input
                                type="checkbox"
                                id={`same_city-${sku}`}
                                defaultChecked={cfg.same_city_only || false}
                                style={{ width: '16px', height: '16px', cursor: 'pointer' }}
                              />
                              {t('same_city_only_label')}
                            </label>
                          </div>
                        )}
                      </div>

                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '8px' }}>
                        <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: '12px', color: '#fff' }}>
                          <input
                            type="checkbox"
                            id={`enabled-${sku}`}
                            defaultChecked={cfg.enabled ?? true}
                            style={{ width: '16px', height: '16px', cursor: 'pointer' }}
                          />
                          <strong>{t('enable_dumping_label')}</strong>
                        </label>
                      </div>

                      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '8px' }}>
                        <button
                          type="button"
                          className="btn btn-primary"
                          style={{ padding: '8px 16px', fontSize: '12px' }}
                          onClick={() => {
                            const minVal = parseFloat((document.getElementById(`min-${sku}`) as HTMLInputElement).value) || 0;
                            const maxVal = parseFloat((document.getElementById(`max-${sku}`) as HTMLInputElement).value) || 0;
                            const stepVal = parseFloat((document.getElementById(`step-${sku}`) as HTMLInputElement).value) || 1;
                            const enabledVal = (document.getElementById(`enabled-${sku}`) as HTMLInputElement).checked;
                            const modeVal = (document.getElementById(`mode-${sku}`) as HTMLSelectElement)?.value || 'TOP_1';
                            const targetPosEl = document.getElementById(`target_pos-${sku}`) as HTMLInputElement | null;
                            const targetPosVal = targetPosEl ? (parseInt(targetPosEl.value) || 1) : 1;
                            const maxDelivEl = document.getElementById(`max_deliv-${sku}`) as HTMLInputElement | null;
                            const maxDelivVal = maxDelivEl?.value ? parseInt(maxDelivEl.value) : null;
                            const minRatingEl = document.getElementById(`min_rating-${sku}`) as HTMLInputElement | null;
                            const minRatingVal = minRatingEl?.value ? parseFloat(minRatingEl.value) : null;
                            const sameCityEl = document.getElementById(`same_city-${sku}`) as HTMLInputElement | null;
                            const sameCityVal = sameCityEl ? sameCityEl.checked : false;
                            const blacklistEl = document.getElementById(`blacklist-${sku}`) as HTMLInputElement | null;
                            const blacklistRaw = blacklistEl ? blacklistEl.value : '';
                            const blacklistArr = blacklistRaw ? blacklistRaw.split(',').map(s => s.trim()).filter(Boolean) : [];

                            onSaveDumpingConfig(
                              cfgKey, minVal, maxVal, stepVal, enabledVal,
                              modeVal, targetPosVal, maxDelivVal, minRatingVal, sameCityVal, blacklistArr,
                              sku
                            );
                          }}
                        >
                          {t('btn_save_dumping_config')}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Pagination Controls */}
      <Pagination
        currentPage={currentPage}
        totalPages={totalPages}
        disabled={fetchingProducts}
        onPageChange={onPageChange}
      />
    </>
  );
}
