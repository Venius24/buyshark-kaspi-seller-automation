import type { FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { RefreshCw, Tag } from 'lucide-react';
import type { Product } from '../../types';
import { Pagination } from '../shared/Pagination';
import { ProductCardHeader } from '../shared/ProductCardHeader';
import { ProductTagsEditor } from '../shared/ProductTagsEditor';
import { HelpHint } from '../shared/HelpHint';
import { ParcelLoader } from '../shared/ParcelLoader';

interface ProductsTabProps {
  totalCount: number;
  pageSize: number;
  onPageSizeChange: (size: number) => void;
  statusFilter: 'active' | 'archive';
  onStatusFilterChange: (status: 'active' | 'archive') => void;
  searchQuery: string;
  onSearchQueryChange: (query: string) => void;
  onSearchSubmit: (e: FormEvent) => void;
  onRefresh: () => void;
  fetchingProducts: boolean;
  products: Product[];
  expandedEditSkus: Set<string>;
  onToggleEdit: (sku: string) => void;
  onSaveProductUpdate: (sku: string, price: number, stock: number, brand: string | undefined, status: string | undefined) => void;
  currentPage: number;
  totalPages: number;
  onPageChange: (page: number) => void;
}

export function ProductsTab({
  totalCount,
  pageSize,
  onPageSizeChange,
  statusFilter,
  onStatusFilterChange,
  searchQuery,
  onSearchQueryChange,
  onSearchSubmit,
  onRefresh,
  fetchingProducts,
  products,
  expandedEditSkus,
  onToggleEdit,
  onSaveProductUpdate,
  currentPage,
  totalPages,
  onPageChange,
}: ProductsTabProps) {
  const { t } = useTranslation('tab_products');

  return (
    <>
      {/* Header with Search, Filter & Page Size */}
      <div style={{ borderBottom: '1px solid rgba(255,255,255,0.06)', paddingBottom: '16px', marginBottom: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <h2 style={{ fontSize: '16px', display: 'flex', alignItems: 'center', gap: '6px' }}>
            {totalCount ? t('title').replace('%count%', String(totalCount)) : t('title_no_products')}
            <HelpHint text="Каталог товаров вашего магазина Kaspi — цены, остатки, статус активности. Можно редактировать прямо здесь, изменения сразу уходят в Kaspi." />
          </h2>

          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
            {/* Page Size Selector */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{t('show_per_page')}</span>
              <select
                value={pageSize}
                onChange={(e) => onPageSizeChange(parseInt(e.target.value))}
                className="form-input"
                style={{ width: '70px', padding: '6px 10px', background: 'var(--input-bg)', border: '1px solid var(--input-border)', color: '#fff', borderRadius: '8px', cursor: 'pointer' }}
              >
                <option value="5">5</option>
                <option value="10">10</option>
                <option value="20">20</option>
                <option value="50">50</option>
              </select>
            </div>

            {/* Status Toggle */}
            <div style={{ display: 'flex', background: 'rgba(255,255,255,0.04)', padding: '3px', borderRadius: '8px' }}>
              <button
                className="btn"
                style={{
                  background: statusFilter === 'active' ? 'var(--primary)' : 'transparent',
                  padding: '5px 12px', fontSize: '11px', borderRadius: '6px'
                }}
                onClick={() => onStatusFilterChange('active')}
              >
                {t('status_active')}
              </button>
              <button
                className="btn"
                style={{
                  background: statusFilter === 'archive' ? 'var(--primary)' : 'transparent',
                  padding: '5px 12px', fontSize: '11px', borderRadius: '6px'
                }}
                onClick={() => onStatusFilterChange('archive')}
              >
                {t('status_archive')}
              </button>
            </div>
          </div>
        </div>

        {/* Search Bar */}
        <form onSubmit={onSearchSubmit} style={{ display: 'flex', gap: '12px' }}>
          <input
            type="text"
            className="form-input"
            placeholder={t('search_placeholder')}
            value={searchQuery}
            onChange={(e) => onSearchQueryChange(e.target.value)}
            style={{ flexGrow: 1 }}
          />
          <button type="submit" className="btn btn-primary" style={{ padding: '10px 16px' }}>
            {t('btn_search')}
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            style={{ padding: '10px 14px' }}
            onClick={onRefresh}
            disabled={fetchingProducts}
          >
            {fetchingProducts ? <RefreshCw size={14} style={{ animation: 'spin 1s linear infinite' }} /> : t('btn_refresh')}
          </button>
        </form>
      </div>

      {/* Products List */}
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
              const isEditing = expandedEditSkus.has(product.sku);
              return (
                <div
                  key={product.id}
                  className="order-card"
                  style={{
                    padding: '16px',
                    display: 'flex',
                    flexDirection: 'column',
                    margin: 0
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div style={{ maxWidth: '70%' }}>
                      <ProductCardHeader name={product.name} sku={product.sku} kaspiSku={product.kaspiSku} merchantSku={product.merchantSku} image={product.image} />

                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginTop: '8px' }}>
                        <span
                          className="badge"
                          style={{
                            background: product.status === 'Активен' ? 'var(--success-glow)' : 'rgba(255,255,255,0.05)',
                            color: product.status === 'Активен' ? 'var(--success)' : '#aaa',
                            border: '1px solid rgba(255,255,255,0.08)'
                          }}
                        >
                          {product.status === 'Активен' ? t('status_for_sale') : t('status_removed')}
                        </span>

                        <span
                          className="badge"
                          style={{
                            background: 'rgba(255,255,255,0.05)',
                            color: '#eee',
                            border: '1px solid rgba(255,255,255,0.08)'
                          }}
                        >
                          {t('stock_label')} {product.stock ?? 0} {t('unit_pcs')}
                        </span>

                        {product.url && (
                          <a
                            href={product.url}
                            target="_blank"
                            rel="noreferrer"
                            style={{ fontSize: '12px', color: 'var(--secondary)', textDecoration: 'none' }}
                          >
                            {t('link_product_card')}
                          </a>
                        )}

                        {(product.tags ?? []).map((tag) => (
                          <span
                            key={tag}
                            className="badge"
                            style={{
                              background: 'rgba(255,255,255,0.05)',
                              color: 'var(--secondary)',
                              border: '1px solid rgba(255,255,255,0.08)',
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '4px',
                            }}
                          >
                            <Tag size={11} />
                            {tag}
                          </span>
                        ))}
                      </div>
                    </div>

                    <div style={{ textAlign: 'right', display: 'flex', alignItems: 'center', gap: '16px' }}>
                      <div>
                        <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{t('price_label')}:</div>
                        <div style={{ fontSize: '16px', fontWeight: 700, color: 'var(--primary)' }}>{product.price}</div>
                      </div>
                      <button
                        type="button"
                        className="btn btn-secondary"
                        style={{ padding: '8px 12px', fontSize: '12px', borderColor: isEditing ? 'var(--secondary)' : 'transparent' }}
                        onClick={() => onToggleEdit(product.sku)}
                      >
                        {isEditing ? t('btn_close_edit') : t('btn_edit')}
                      </button>
                    </div>
                  </div>

                  {isEditing && (
                    <div
                      style={{
                        marginTop: '16px',
                        paddingTop: '16px',
                        borderTop: '1px solid rgba(255,255,255,0.05)',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '12px'
                      }}
                    >
                      <h4 style={{ fontSize: '13px', fontWeight: 600, color: 'var(--secondary)' }}>
                        {t('edit_form_title')}
                      </h4>

                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: '12px' }}>
                        <div>
                          <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>{t('edit_price_label')}</label>
                          <input
                            type="number"
                            id={`edit-price-${product.sku}`}
                            className="form-input"
                            defaultValue={product.price_num || ''}
                            placeholder={t('edit_price_placeholder')}
                          />
                        </div>
                        <div>
                          <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>{t('edit_stock_label')}</label>
                          <input
                            type="number"
                            id={`edit-stock-${product.sku}`}
                            className="form-input"
                            defaultValue={product.stock ?? 10}
                            placeholder={t('edit_stock_placeholder')}
                          />
                        </div>
                        <div>
                          <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>{t('edit_brand_label')}</label>
                          <input
                            type="text"
                            id={`edit-brand-${product.sku}`}
                            className="form-input"
                            defaultValue={product.brand || ''}
                            placeholder={t('edit_brand_placeholder')}
                          />
                        </div>
                        <div>
                          <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>{t('edit_status_label')}</label>
                          <select
                            id={`edit-status-${product.sku}`}
                            className="form-input"
                            defaultValue={product.status || 'Активен'}
                            style={{ background: 'var(--input-bg)', color: '#fff', border: '1px solid var(--input-border)', height: '38px', borderRadius: '8px' }}
                          >
                            <option value="Активен">{t('edit_status_option_active')}</option>
                            <option value="Архив">{t('edit_status_option_archive')}</option>
                          </select>
                        </div>
                      </div>

                      <div style={{ paddingTop: '4px', borderTop: '1px solid rgba(255,255,255,0.05)' }}>
                        <ProductTagsEditor
                          sku={product.merchantSku || product.sku}
                          tags={product.tags ?? []}
                        />
                      </div>

                      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '4px' }}>
                        <button
                          type="button"
                          className="btn btn-primary"
                          style={{ padding: '6px 12px', fontSize: '12px' }}
                          onClick={() => {
                            const priceVal = parseFloat((document.getElementById(`edit-price-${product.sku}`) as HTMLInputElement).value) || 0;
                            const stockVal = parseInt((document.getElementById(`edit-stock-${product.sku}`) as HTMLInputElement).value) || 0;
                            const brandVal = (document.getElementById(`edit-brand-${product.sku}`) as HTMLInputElement).value || undefined;
                            const statusVal = (document.getElementById(`edit-status-${product.sku}`) as HTMLSelectElement).value || undefined;
                            onSaveProductUpdate(product.sku, priceVal, stockVal, brandVal, statusVal);
                          }}
                        >
                          {t('btn_save_changes')}
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
