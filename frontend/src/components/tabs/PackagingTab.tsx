import { useTranslation } from 'react-i18next';
import { RefreshCw, Package } from 'lucide-react';
import type { Order } from '../../types';
import { openImageLightbox } from '../shared/ImageLightbox';
import { HelpHint } from '../shared/HelpHint';
import { ParcelLoader } from '../shared/ParcelLoader';

interface PackagingTabProps {
  filteredOrders: Order[];
  fetchingOrders: boolean;
  orderLimit: number;
  onOrderLimitChange: (limit: number) => void;
  orderStatusFilter: 'NEW' | 'ASSEMBLY';
  onOrderStatusFilterChange: (status: 'NEW' | 'ASSEMBLY') => void;
  pkgDeliveryFilter: 'ALL' | 'EXPRESS' | 'POSTOMAT' | 'STANDARD';
  onPkgDeliveryFilterChange: (filter: 'ALL' | 'EXPRESS' | 'POSTOMAT' | 'STANDARD') => void;
  selectedOrderIds: Set<string>;
  onToggleSelectAll: () => void;
  onToggleOrderSelection: (orderId: string) => void;
  onPackSelectedOrders: () => void;
  onPackAllOrders: () => void;
  onPackOrder: (orderId: string, internalId: string) => void;
  packingAll: boolean;
  onRefresh: () => void;
}

export function PackagingTab({
  filteredOrders,
  fetchingOrders,
  orderLimit,
  onOrderLimitChange,
  orderStatusFilter,
  onOrderStatusFilterChange,
  pkgDeliveryFilter,
  onPkgDeliveryFilterChange,
  selectedOrderIds,
  onToggleSelectAll,
  onToggleOrderSelection,
  onPackSelectedOrders,
  onPackAllOrders,
  onPackOrder,
  packingAll,
  onRefresh,
}: PackagingTabProps) {
  const { t } = useTranslation('tab_packaging');
  return (
    <>
      {/* Header with Search, Filter & Limit */}
      <div style={{ borderBottom: '1px solid rgba(255,255,255,0.06)', paddingBottom: '16px', marginBottom: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px', marginBottom: '16px' }}>
          <h2 style={{ fontSize: '16px', display: 'flex', alignItems: 'center', gap: '6px' }}>
            {filteredOrders.length ? t('title').replace('%count%', String(filteredOrders.length)) : t('title_no_orders')}
            <HelpHint text="Заказы, которые нужно собрать. Отмечайте товар собранным по одному или пакетом — статус в Kaspi обновится автоматически." />
          </h2>

          <div style={{ display: 'flex', alignItems: 'center', gap: '16px', flexWrap: 'wrap' }}>
            {/* Limit Selector */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{t('limit_label')}</span>
              <select
                value={orderLimit}
                onChange={(e) => onOrderLimitChange(parseInt(e.target.value))}
                className="form-input"
                style={{ width: '80px', padding: '6px 10px', background: 'var(--input-bg)', border: '1px solid var(--input-border)', color: '#fff', borderRadius: '8px', cursor: 'pointer' }}
              >
                <option value="10">10</option>
                <option value="20">20</option>
                <option value="50">50</option>
                <option value="100">100</option>
              </select>
            </div>

            {/* Order Status Toggle (New vs Assembly) */}
            <div style={{ display: 'flex', background: 'rgba(255,255,255,0.04)', padding: '3px', borderRadius: '8px' }}>
              <button
                className="btn"
                style={{
                  background: orderStatusFilter === 'NEW' ? 'var(--primary)' : 'transparent',
                  padding: '5px 12px', fontSize: '11px', borderRadius: '6px'
                }}
                onClick={() => onOrderStatusFilterChange('NEW')}
              >
                {t('status_new')}
              </button>
              <button
                className="btn"
                style={{
                  background: orderStatusFilter === 'ASSEMBLY' ? 'var(--primary)' : 'transparent',
                  padding: '5px 12px', fontSize: '11px', borderRadius: '6px'
                }}
                onClick={() => onOrderStatusFilterChange('ASSEMBLY')}
              >
                {t('status_assembly')}
              </button>
            </div>

            {/* Delivery Mode Filter */}
            <div style={{ display: 'flex', background: 'rgba(255,255,255,0.04)', padding: '3px', borderRadius: '8px' }}>
              {(['ALL', 'EXPRESS', 'POSTOMAT', 'STANDARD'] as const).map((filterVal) => (
                <button
                  key={filterVal}
                  className="btn"
                  style={{
                    background: pkgDeliveryFilter === filterVal ? 'var(--primary)' : 'transparent',
                    padding: '5px 10px', fontSize: '11px', borderRadius: '6px'
                  }}
                  onClick={() => onPkgDeliveryFilterChange(filterVal)}
                >
                  {filterVal === 'ALL' && t('delivery_all')}
                  {filterVal === 'EXPRESS' && t('delivery_express')}
                  {filterVal === 'POSTOMAT' && t('delivery_postomat')}
                  {filterVal === 'STANDARD' && t('delivery_standard')}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Mass Actions Controls */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <button
              type="button"
              className="btn btn-secondary"
              style={{ padding: '8px 14px', fontSize: '13px' }}
              onClick={onToggleSelectAll}
              disabled={filteredOrders.length === 0}
            >
              {selectedOrderIds.size === filteredOrders.length && filteredOrders.length > 0 ? t('btn_deselect_all') : t('btn_select_all')}
            </button>

            {selectedOrderIds.size > 0 && (
              <button
                type="button"
                className="btn btn-primary"
                style={{ padding: '8px 14px', fontSize: '13px', background: 'linear-gradient(100deg, var(--secondary), var(--secondary-hover))', boxShadow: '0 4px 12px var(--secondary-glow)' }}
                onClick={onPackSelectedOrders}
              >
                {t('btn_pack_selected').replace('%count%', String(selectedOrderIds.size))}
              </button>
            )}
          </div>

          <div style={{ display: 'flex', gap: '12px' }}>
            <button
              type="button"
              className="btn btn-secondary"
              style={{ padding: '8px 14px', fontSize: '13px', border: '1px solid var(--success)' }}
              onClick={onPackAllOrders}
              disabled={packingAll || filteredOrders.length === 0}
            >
              {packingAll ? t('btn_loading') : t('btn_form_all')}
            </button>

            <button
              type="button"
              className="btn btn-secondary"
              style={{ padding: '8px 12px' }}
              onClick={onRefresh}
              disabled={fetchingOrders}
            >
              {fetchingOrders ? <RefreshCw size={14} style={{ animation: 'spin 1s linear infinite' }} /> : t('btn_refresh')}
            </button>
          </div>
        </div>
      </div>

      {/* Orders List */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', flexGrow: 1 }}>
        {fetchingOrders && filteredOrders.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
            <div style={{ marginBottom: '16px' }}>
              <ParcelLoader />
            </div>
            <p>{t('loading_message')}</p>
          </div>
        ) : filteredOrders.length === 0 && !fetchingOrders ? (
          <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
            <p>{t('no_orders')}</p>
          </div>
        ) : (
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '16px',
            opacity: fetchingOrders ? 0.6 : 1,
            pointerEvents: fetchingOrders ? 'none' : 'auto',
            transition: 'opacity 0.2s ease-in-out'
          }}>
            {filteredOrders.map((order) => {
              const isSelected = selectedOrderIds.has(order.id);
              return (
                <div
                  key={order.id}
                  className={`order-card ${isSelected ? 'selected' : ''}`}
                >
                  {/* Card Top Row */}
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => onToggleOrderSelection(order.id)}
                        style={{ width: '18px', height: '18px', cursor: 'pointer', accentColor: 'var(--primary)' }}
                      />
                      <div>
                        <h3 style={{ fontSize: '16px', fontWeight: 700, color: '#fff', display: 'flex', alignItems: 'center', gap: '8px' }}>
                          {t('order_title').replace('%id%', order.id)}
                          <span style={{ fontSize: '11px', fontWeight: 500, color: 'var(--text-muted)' }}>({order.createdAt})</span>
                        </h3>
                        <div style={{ display: 'flex', gap: '8px', marginTop: '6px' }}>
                          {order.deliveryType === 'Express' ? (
                            <span className="badge badge-error">{t('delivery_express_badge')}</span>
                          ) : order.deliveryType === 'Today' ? (
                            <span className="badge badge-warning">{t('delivery_today_badge')}</span>
                          ) : order.deliveryType === 'Tomorrow' ? (
                            <span className="badge badge-warning" style={{ background: 'rgba(59, 130, 246, 0.1)', color: '#3b82f6', border: '1px solid rgba(59, 130, 246, 0.2)' }}>{t('delivery_tomorrow_badge')}</span>
                          ) : (order.deliveryType === 'Самовывоз' || order.deliveryType === 'Postomat') ? (
                            <span className="badge" style={{ background: 'rgba(139, 92, 246, 0.1)', color: '#a78bfa', border: '1px solid rgba(139, 92, 246, 0.2)' }}>{t('delivery_postomat_badge')}</span>
                          ) : (
                            <span className="badge" style={{ background: 'rgba(255, 255, 255, 0.05)', color: 'var(--text-muted)', border: '1px solid rgba(255, 255, 255, 0.08)', display: 'inline-flex', alignItems: 'center', gap: '6px' }}><Package size={14} /> {order.deliveryType}</span>
                          )}
                          <span className="badge" style={{ background: order.status === 'APPROVED_BY_BANK' ? 'rgba(59, 130, 246, 0.1)' : 'rgba(16, 185, 129, 0.1)', color: order.status === 'APPROVED_BY_BANK' ? '#3b82f6' : 'var(--success)' }}>
                            {order.status === 'APPROVED_BY_BANK' ? t('status_new') : t('status_approved')}
                          </span>
                        </div>
                      </div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div className="order-price">{order.price}</div>
                    </div>
                  </div>

                  {/* Card Content (Grid) */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '16px', padding: '12px 0', borderTop: '1px solid rgba(255,255,255,0.04)', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: '8px', fontSize: '13px' }}>
                      <span style={{ color: 'var(--text-muted)' }}>{t('recipient_label')}:</span>
                      <span style={{ color: '#fff', fontWeight: 500 }}>
                        {order.customer.name} {order.customer.phone && `(${order.customer.phone})`}
                      </span>

                      <span style={{ color: 'var(--text-muted)' }}>{t('delivery_address_label')}:</span>
                      <span style={{ color: '#fff' }}>{order.address}</span>
                    </div>

                    {/* Items List */}
                    <div>
                      <h4 style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '8px' }}>{t('items_header')}:</h4>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        {order.entries && order.entries.map((item, idx) => (
                          <div key={idx} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'rgba(255,255,255,0.01)', padding: '8px 12px', borderRadius: '8px', fontSize: '13px', border: '1px solid rgba(255,255,255,0.02)' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexGrow: 1, marginRight: '16px' }}>
                              {item.image ? (
                                <img
                                  src={item.image}
                                  alt=""
                                  loading="lazy"
                                  style={{ width: '44px', height: '44px', borderRadius: '8px', objectFit: 'cover', flexShrink: 0, background: 'rgba(255,255,255,0.04)', cursor: 'zoom-in' }}
                                  onClick={(e) => { e.stopPropagation(); openImageLightbox(item.image!); }}
                                  onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = 'hidden'; }}
                                />
                              ) : (
                                <div style={{ width: '44px', height: '44px', borderRadius: '8px', background: 'rgba(255,255,255,0.04)', flexShrink: 0 }} />
                              )}
                              <div>
                                <div style={{ color: '#fff', fontWeight: 500 }}>{item.productName}</div>
                                <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>SKU: {item.sku}</div>
                              </div>
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '24px', flexShrink: 0 }}>
                              <div style={{ color: 'var(--text-muted)' }}>x{item.quantity}</div>
                              <div style={{ color: '#fff', fontWeight: 600, minWidth: '70px', textAlign: 'right' }}>
                                {item.basePrice ? Math.round(item.basePrice).toLocaleString('ru-RU') : '0'} ₸
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>

                  {/* Card Actions */}
                  <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px' }}>
                    <button
                      className="btn btn-secondary"
                      style={{ padding: '8px 14px', fontSize: '13px' }}
                      onClick={() => onToggleOrderSelection(order.id)}
                    >
                      {isSelected ? t('btn_deselect') : t('btn_select')}
                    </button>
                    <button
                      className="btn btn-primary"
                      style={{ padding: '8px 16px', fontSize: '13px' }}
                      onClick={() => onPackOrder(order.id, order.internalId)}
                    >
                      {order.status === 'APPROVED_BY_BANK' ? t('btn_accept_order') : t('btn_pack_order')}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}
