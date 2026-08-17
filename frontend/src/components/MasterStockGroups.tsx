import React, { useState, useEffect, useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Layers,
  Plus,
  Search,
  RefreshCw,
  Edit2,
  Trash2,
  CheckCircle2,
  AlertTriangle,
  Clock,
  Package,
  Check,
  X,
  ShieldAlert,
  Boxes,
  Zap,
  FileText,
  ShoppingCart,
  ExternalLink
} from 'lucide-react';
import { useWebSocket } from '../hooks/useWebSocket';
import { LiveTerminalStream } from './LiveTerminalStream';
import { apiFetch, API_URL, getWsUrl } from '../api';
import { fetchGroupsQuery, fetchProductsQuery } from '../queries';
import { useActiveStoreId } from '../storeContext';

interface GroupDumpingConfig {
  enabled: boolean;
  min_price: number;
  max_price: number;
  dump_step?: number;
  mode?: string;
  target_position?: number;
  max_delivery_days?: number | null;
  min_seller_rating?: number | null;
  same_city_only?: boolean;
  blacklisted_merchants?: string[];
  city_code?: string;
}

export interface MasterStockGroup {
  group_id: string;
  group_name: string;
  shared_stock: number;
  /** Долг: продано сверх остатка. Остаток при этом обрезан нулём. */
  oversold?: number;
  /** Сколько штук остатка съедает одна проданная единица (комплект). */
  deduct_per_unit?: number;
  linked_skus: string[];
  external_code?: string;
  is_active: boolean;
  stop_qty: number;
  status: 'synced' | 'updating' | 'deducting' | 'warning' | 'error';
  created_at: string;
  updated_at: string;

  last_synced_at?: string;
  last_sync_log?: string;
  sync_logs?: string[];
  dumping_config?: GroupDumpingConfig | null;
  dumping_excluded_skus?: string[];
}


/** Чип привязанного товара: одна карточка Kaspi, а не один идентификатор. */
interface LinkedCard {
  key: string;
  /** Артикул продавца — то, чем товар зовут в заказах и в 1С. */
  label: string;
  name?: string;
  /** Числовой SKU Kaspi, по нему строится ссылка на карточку. */
  kaspiSku?: string;
  /** Идентификаторы, схлопнутые в этот чип (для подсказки). */
  aliases: string[];
  /** Карточка снята с продажи в кабинете. */
  archived?: boolean;
}

const KASPI_CARD_URL = (kaspiSku: string) =>
  `https://kaspi.kz/shop/p/-${kaspiSku}/?c=750000000`;

/**
 * Схлопывает linked_skus в список карточек товара.
 *
 * Бэкенд намеренно хранит для каждого товара ВСЕ его идентификаторы сразу
 * (артикул продавца + числовой SKU Kaspi) — иначе списание по заказу не
 * находит группу. Но в интерфейсе это выглядит как дубли: один товар занимал
 * два-три чипа. Здесь идентификаторы одного товара сводятся в один чип.
 */
function buildLinkedCards(linkedSkus: string[], products: ProductItem[]): LinkedCard[] {
  const byKey = new Map<string, LinkedCard>();

  for (const raw of linkedSkus) {
    const s = String(raw || '').trim();
    if (!s) continue;
    const p = products.find(x => x.sku === s || x.merchantSku === s || x.kaspiSku === s);
    const key = p ? (p.sku || p.merchantSku || s) : s;
    const numeric = [p?.kaspiSku, p?.sku, s].find(v => v && /^\d+$/.test(v));

    const existing = byKey.get(key);
    if (existing) {
      if (!existing.aliases.includes(s)) existing.aliases.push(s);
      if (!existing.kaspiSku && numeric) existing.kaspiSku = numeric;
      continue;
    }
    byKey.set(key, {
      key,
      // Артикул читаем глазами, числовой SKU — нет. Если товара нет в кэше
      // каталога, показываем то, что привязано.
      label: p?.merchantSku || (/^\d+$/.test(s) ? s : s),
      name: p?.name,
      kaspiSku: numeric,
      aliases: [s],
      archived: isArchived(p),
    });
  }

  return Array.from(byKey.values());
}

type StockFilter = 'all' | 'critical' | 'low' | 'ok' | 'debt' | 'pack' | 'paused';

export interface ProductItem {
  sku: string;
  name: string;
  price_num?: number;
  price?: string;
  stock?: number;
  brand?: string;
  merchantSku?: string;
  kaspiSku?: string;
  /** «Активен» / «Архив» — приходит из кэша ассортимента (см. sync_merchant_assortment). */
  status?: string;
  image?: string;
}

/** Превью первого привязанного товара группы — одна картинка на группу, не на чип. */
function firstGroupImage(linkedSkus: string[], products: ProductItem[]): string | undefined {
  for (const raw of linkedSkus) {
    const s = String(raw || '').trim();
    if (!s) continue;
    const p = products.find(x => x.sku === s || x.merchantSku === s || x.kaspiSku === s);
    if (p?.image) return p.image;
  }
  return undefined;
}

/** Снятая с продажи карточка: у неё title=null, price=0 и пустой availabilities. */
const isArchived = (p?: { status?: string } | null): boolean =>
  String(p?.status || '').trim() === 'Архив';

export const MasterStockGroups: React.FC = () => {
  const queryClient = useQueryClient();
  const activeStoreId = useActiveStoreId();
  const { data: groups = [] } = useQuery({
    queryKey: ['stock-groups', activeStoreId],
    queryFn: fetchGroupsQuery,
    refetchInterval: 5_000,
  });
  const { data: products = [], isLoading: loading } = useQuery({
    queryKey: ['products', 'all-for-stock-groups', activeStoreId],
    queryFn: fetchProductsQuery,
    // Kept in sync with BackgroundSync.tsx's interval for the same key —
    // see the comment there for why 30s was dropped to 300s.
    refetchInterval: 300_000,
  });
  const refetchGroups = () => queryClient.invalidateQueries({ queryKey: ['stock-groups'] });
  const [syncingGroupId, setSyncingGroupId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [stockFilter, setStockFilter] = useState<StockFilter>('all');

  // Modal State
  const [isModalOpen, setIsModalOpen] = useState<boolean>(false);
  const [editingGroupId, setEditingGroupId] = useState<string | null>(null);
  const [formGroupName, setFormGroupName] = useState<string>('');
  const [formSharedStock, setFormSharedStock] = useState<number>(10);
  const [formStopQty, setFormStopQty] = useState<number>(5);
  const [formDeductPerUnit, setFormDeductPerUnit] = useState<number>(1);
  const [formSelectedSkus, setFormSelectedSkus] = useState<string[]>([]);
  const [formExternalCode, setFormExternalCode] = useState<string>('');
  const [formIsActive, setFormIsActive] = useState<boolean>(true);
  const [modalProductSearch, setModalProductSearch] = useState<string>('');
  const [submitting, setSubmitting] = useState<boolean>(false);

  // Group Dumping Modal State
  const [formDumpingEnabled, setFormDumpingEnabled] = useState<boolean>(false);
  const [formMinPrice, setFormMinPrice] = useState<number>(1000);
  const [formMaxPrice, setFormMaxPrice] = useState<number>(5000);
  const [formDumpStep, setFormDumpStep] = useState<number>(2);
  const [formDumpingMode, setFormDumpingMode] = useState<string>('TOP_1');
  const [formTargetPosition, setFormTargetPosition] = useState<number>(1);
  const [formDumpingExcludedSkus, setFormDumpingExcludedSkus] = useState<string[]>([]);

  // Inline Stock Edit State
  const [editingStockId, setEditingStockId] = useState<string | null>(null);
  const [expandedSkusGroupId, setExpandedSkusGroupId] = useState<string | null>(null);
  const [inlineStockVal, setInlineStockVal] = useState<number>(0);

  const { isConnected: isWsConnected, lastEvent: wsEvent } = useWebSocket(getWsUrl());
  const [terminalLogs, setTerminalLogs] = useState<string[]>([]);
  const [viewLogGroup, setViewLogGroup] = useState<MasterStockGroup | null>(null);

  useEffect(() => {
    if (wsEvent) {
      if (wsEvent.type === 'log' && typeof wsEvent.data === 'string') {
        const logLine = wsEvent.data;
        setTerminalLogs(prev => {
          if (prev.length > 0 && prev[prev.length - 1] === logLine) {
            return prev;
          }
          return [...prev.slice(-100), logLine];
        });
      } else if (wsEvent.type === 'stock_group_updated') {
        refetchGroups();
      }
    }
  }, [wsEvent]);

  // Toast / Alert State
  const [toastMessage, setToastMessage] = useState<{ type: 'success' | 'error' | 'warning'; text: string } | null>(null);

  const showToast = (type: 'success' | 'error' | 'warning', text: string) => {
    setToastMessage({ type, text });
    setTimeout(() => setToastMessage(null), 4000);
  };

  // Filtered Groups
  // Пороги считаются от стоп-количества самой группы, а не от общей константы:
  // у «щитков» стоп 2, у «палочек» — 50, единая цифра не сказала бы ничего.
  const matchStockFilter = (g: MasterStockGroup, f: StockFilter): boolean => {
    const stop = g.stop_qty ?? 5;
    switch (f) {
      case 'critical': return g.shared_stock <= stop;
      case 'low': return g.shared_stock > stop && g.shared_stock <= stop * 2;
      case 'ok': return g.shared_stock > stop * 2;
      case 'debt': return (g.oversold ?? 0) > 0;
      case 'pack': return (g.deduct_per_unit ?? 1) > 1;
      case 'paused': return !g.is_active;
      default: return true;
    }
  };

  const filteredGroups = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return groups.filter(g => {
      if (!matchStockFilter(g, stockFilter)) return false;
      if (!q) return true;
      return (
        g.group_name.toLowerCase().includes(q) ||
        g.group_id.toLowerCase().includes(q) ||
        (g.external_code && g.external_code.toLowerCase().includes(q)) ||
        g.linked_skus.some(s => s.toLowerCase().includes(q))
      );
    });
  }, [groups, searchQuery, stockFilter]);

  const filterCounts = useMemo(() => ({
    all: groups.length,
    critical: groups.filter(g => matchStockFilter(g, 'critical')).length,
    low: groups.filter(g => matchStockFilter(g, 'low')).length,
    ok: groups.filter(g => matchStockFilter(g, 'ok')).length,
    debt: groups.filter(g => matchStockFilter(g, 'debt')).length,
    pack: groups.filter(g => matchStockFilter(g, 'pack')).length,
    paused: groups.filter(g => matchStockFilter(g, 'paused')).length,
  }), [groups]);

  const FILTER_CHIPS: { id: StockFilter; label: string; color: string }[] = [
    { id: 'all', label: 'Все группы', color: 'var(--primary-hover)' },
    { id: 'critical', label: 'Снято с продажи', color: 'var(--error)' },
    { id: 'low', label: 'Мало остатка', color: 'var(--warning)' },
    { id: 'ok', label: 'Запас в норме', color: 'var(--success)' },
    { id: 'debt', label: 'С долгом', color: 'var(--error)' },
    { id: 'pack', label: 'Комплект > 1', color: '#c084fc' },
    { id: 'paused', label: 'На паузе', color: 'var(--text-muted)' },
  ];

  // Metrics
  const totalGroups = groups.length;
  const totalLinkedSkus = useMemo(() => {
    const skuSet = new Set<string>();
    groups.forEach(g => g.linked_skus.forEach(s => skuSet.add(s)));
    return skuSet.size;
  }, [groups]);
  const zeroStockGroupsCount = useMemo(() => groups.filter(g => g.shared_stock <= (g.stop_qty ?? 5)).length, [groups]);
  const activeGroupsCount = useMemo(() => groups.filter(g => g.is_active).length, [groups]);

  // Modal Handlers
  const handleOpenCreateModal = () => {
    setEditingGroupId(null);
    setFormGroupName('');
    setFormSharedStock(10);
    setFormStopQty(5);
    setFormDeductPerUnit(1);
    setFormSelectedSkus([]);
    setFormExternalCode('');
    setFormIsActive(true);
    setModalProductSearch('');
    setFormDumpingEnabled(false);
    setFormMinPrice(1000);
    setFormMaxPrice(5000);
    setFormDumpStep(2);
    setFormDumpingMode('TOP_1');
    setFormTargetPosition(1);
    setFormDumpingExcludedSkus([]);
    setIsModalOpen(true);
  };

  const handleOpenEditModal = (group: MasterStockGroup) => {
    setEditingGroupId(group.group_id);
    setFormGroupName(group.group_name);
    setFormSharedStock(group.shared_stock);
    setFormStopQty(group.stop_qty ?? 5);
    setFormDeductPerUnit(group.deduct_per_unit ?? 1);
    setFormSelectedSkus([...group.linked_skus]);
    setFormExternalCode(group.external_code || '');
    setFormIsActive(group.is_active);
    setModalProductSearch('');
    if (group.dumping_config) {
      setFormDumpingEnabled(group.dumping_config.enabled ?? false);
      setFormMinPrice(group.dumping_config.min_price || 1000);
      setFormMaxPrice(group.dumping_config.max_price || 5000);
      setFormDumpStep(group.dumping_config.dump_step ?? 2);
      setFormDumpingMode(group.dumping_config.mode || 'TOP_1');
      setFormTargetPosition(group.dumping_config.target_position || 1);
    } else {
      setFormDumpingEnabled(false);
      setFormMinPrice(1000);
      setFormMaxPrice(5000);
      setFormDumpStep(2);
      setFormDumpingMode('TOP_1');
      setFormTargetPosition(1);
    }
    setFormDumpingExcludedSkus([...(group.dumping_excluded_skus || [])]);
    setIsModalOpen(true);
  };

  const handleToggleSkuSelection = (sku: string, archived: boolean = false) => {
    // Синхронизация группы шлёт в Kaspi available = (остаток > стоп-кол-ва),
    // то есть привязка снятой карточки к «живой» группе ВЕРНЁТ её в продажу.
    // Не запрещаем — владелец может именно этого и хотеть, — но предупреждаем.
    if (archived && !formSelectedSkus.includes(sku)) {
      showToast('warning', 'Карточка снята с продажи. Если остаток группы выше стоп-количества, синхронизация вернёт её в продажу на Kaspi.');
    }
    setFormSelectedSkus(prev =>
      prev.includes(sku) ? prev.filter(s => s !== sku) : [...prev, sku]
    );
    // SKU, снятый с привязки, не может оставаться исключённым из демпинга.
    setFormDumpingExcludedSkus(prev => prev.filter(s => s !== sku));
  };

  const handleToggleDumpingExclusion = (sku: string) => {
    setFormDumpingExcludedSkus(prev =>
      prev.includes(sku) ? prev.filter(s => s !== sku) : [...prev, sku]
    );
  };

  const handleSubmitModal = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formGroupName.trim()) {
      showToast('error', 'Введите название группы остатка.');
      return;
    }
    if (formSelectedSkus.length === 0) {
      showToast('error', 'Выберите хотя бы один товар (SKU) для связывания.');
      return;
    }

    setSubmitting(true);
    try {
      const isEdit = !!editingGroupId;
      const url = isEdit ? `${API_URL}/api/stock-groups/${editingGroupId}` : `${API_URL}/api/stock-groups`;
      const method = isEdit ? 'PUT' : 'POST';

      const res = await apiFetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          group_name: formGroupName.trim(),
          shared_stock: formSharedStock,
          stop_qty: formStopQty,
          deduct_per_unit: formDeductPerUnit,
          linked_skus: formSelectedSkus,
          external_code: formExternalCode.trim() || undefined,
          is_active: formIsActive,
          dumping_config: formDumpingEnabled ? {
            enabled: true,
            min_price: formMinPrice,
            max_price: formMaxPrice,
            dump_step: formDumpStep,
            mode: formDumpingMode,
            target_position: formTargetPosition,
          } : null,
          dumping_excluded_skus: formDumpingEnabled ? formDumpingExcludedSkus : [],
        }),
      });


      const data = await res.json();
      if (data.success) {
        showToast('success', data.message || 'Мастер-Группа сохранена.');
        setIsModalOpen(false);
        refetchGroups();
      } else {
        showToast('error', data.detail || 'Не удалось сохранить группу.');
      }
    } catch (err: any) {
      showToast('error', err.message || 'Ошибка сети при сохранении группы.');
    } finally {
      setSubmitting(false);
    }
  };

  // Inline Stock Edit Handlers
  const handleStartInlineEdit = (group: MasterStockGroup) => {
    setEditingStockId(group.group_id);
    setInlineStockVal(group.shared_stock);
  };

  const handleSaveInlineStock = async (groupId: string) => {
    try {
      const res = await apiFetch(`${API_URL}/api/stock-groups/${groupId}/stock`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shared_stock: inlineStockVal }),
      });
      const data = await res.json();
      if (data.success) {
        showToast('success', data.message || 'Остаток группы обновлен.');
        setEditingStockId(null);
        refetchGroups();
      } else {
        showToast('error', data.detail || 'Не удалось обновить остаток.');
      }
    } catch (err: any) {
      showToast('error', 'Ошибка сервера при изменении остатка.');
    }
  };

  // Force Sync
  const handleManualSync = async (groupId: string) => {
    setSyncingGroupId(groupId);
    try {
      const res = await apiFetch(`${API_URL}/api/stock-groups/${groupId}/sync`, { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        showToast('success', 'Синхронизация запущена на Kaspi.');
        refetchGroups();
      } else {
        showToast('error', data.detail || 'Не удалось запустить синхронизацию.');
      }
    } catch (e) {
      showToast('error', 'Ошибка сети при вызове синхронизации.');
    } finally {
      setSyncingGroupId(null);
    }
  };

  // Delete Group
  const handleDeleteGroup = async (group: MasterStockGroup) => {
    if (!window.confirm(`Вы уверены, что хотите удалить группу "${group.group_name}"?`)) return;
    try {
      const res = await apiFetch(`${API_URL}/api/stock-groups/${group.group_id}`, { method: 'DELETE' });

      const data = await res.json();
      if (data.success) {
        showToast('success', data.message || 'Группа удалена.');
        refetchGroups();
      } else {
        showToast('error', data.detail || 'Не удалось удалить группу.');
      }
    } catch (e) {
      showToast('error', 'Ошибка при удалении группы.');
    }
  };

  // Filtered Products for Modal SKU Picker
  const modalFilteredProducts = useMemo(() => {
    if (!modalProductSearch.trim()) return products;
    const q = modalProductSearch.toLowerCase();
    return products.filter(p =>
      (p.name && p.name.toLowerCase().includes(q)) ||
      (p.sku && p.sku.toLowerCase().includes(q)) ||
      (p.merchantSku && p.merchantSku.toLowerCase().includes(q))
    );
  }, [products, modalProductSearch]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px', color: 'var(--text-color)' }}>
      
      {/* Toast Notification Alert */}
      {toastMessage && (
        <div
          style={{
            position: 'fixed',
            bottom: '24px',
            right: '24px',
            zIndex: 9999,
            display: 'flex',
            alignItems: 'center',
            gap: '12px',
            padding: '14px 20px',
            borderRadius: '12px',
            background:
              toastMessage.type === 'success'
                ? 'linear-gradient(135deg, rgba(16, 185, 129, 0.95), rgba(5, 150, 105, 0.95))'
                : toastMessage.type === 'warning'
                ? 'linear-gradient(135deg, rgba(245, 158, 11, 0.95), rgba(217, 119, 6, 0.95))'
                : 'linear-gradient(135deg, rgba(239, 68, 68, 0.95), rgba(220, 38, 38, 0.95))',
            color: '#ffffff',
            boxShadow: '0 10px 25px -5px rgba(0,0,0,0.5)',
            backdropFilter: 'blur(8px)',
            fontWeight: 500,
            fontSize: '14px',
            animation: 'fadeIn 0.3s ease-out',
          }}
        >
          {toastMessage.type === 'success' && <CheckCircle2 size={20} />}
          {toastMessage.type === 'warning' && <AlertTriangle size={20} />}
          {toastMessage.type === 'error' && <ShieldAlert size={20} />}
          <span>{toastMessage.text}</span>
        </div>
      )}

      {/* Header Title Section */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div style={{
              width: '42px',
              height: '42px',
              borderRadius: '12px',
              background: 'linear-gradient(135deg, var(--primary) 0%, var(--primary-hover) 100%)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              boxShadow: '0 4px 14px var(--primary-glow)'
            }}>
              <Boxes size={24} color="#ffffff" />
            </div>
            <div>
              <h1 style={{ margin: 0, fontSize: '24px', fontWeight: 700, color: 'var(--text-color)', letterSpacing: '-0.02em' }}>
                Единый связный учет остатков (Master Stock Sync)
              </h1>
              <p style={{ margin: '4px 0 0 0', fontSize: '13px', color: 'var(--text-muted)' }}>
                Объединение карточек товаров Kaspi в единые Мастер-Группы с автоматическим веерным списанием остатков
              </p>
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <button
            onClick={() => {
              queryClient.invalidateQueries({ queryKey: ['stock-groups'] });
              queryClient.invalidateQueries({ queryKey: ['products', 'all-for-stock-groups'] });
            }}
            disabled={loading}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '10px 16px',
              borderRadius: '10px',
              background: 'rgba(255,255,255,0.05)',
              border: '1px solid var(--card-border)',
              color: '#cbd5e1',
              fontSize: '14px',
              fontWeight: 500,
              cursor: 'pointer',
              transition: 'all 0.2s',
            }}
          >
            <RefreshCw size={16} className={loading ? 'spin' : ''} />
            <span>Обновить</span>
          </button>

          <button
            onClick={handleOpenCreateModal}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '10px 18px',
              borderRadius: '10px',
              background: 'linear-gradient(135deg, var(--primary-hover) 0%, var(--primary) 100%)',
              border: 'none',
              color: '#ffffff',
              fontSize: '14px',
              fontWeight: 600,
              cursor: 'pointer',
              boxShadow: '0 4px 14px rgba(79, 70, 229, 0.4)',
              transition: 'all 0.2s',
            }}
          >
            <Plus size={18} />
            <span>Создать Мастер-Группу</span>
          </button>
        </div>
      </div>

      {/* Live Telemetry Stream Terminal */}
      <LiveTerminalStream logs={terminalLogs} isConnected={isWsConnected} />

      {/* Summary KPI Cards Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '16px' }}>
        
        <div style={{
          background: 'rgba(30, 41, 59, 0.7)',
          border: '1px solid rgba(51, 65, 85, 0.6)',
          borderRadius: '14px',
          padding: '18px 20px',
          backdropFilter: 'blur(10px)',
          display: 'flex',
          alignItems: 'center',
          gap: '16px'
        }}>
          <div style={{ width: '48px', height: '48px', borderRadius: '12px', background: 'rgba(99, 102, 241, 0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--primary-hover)' }}>
            <Layers size={24} />
          </div>
          <div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', fontWeight: 500 }}>Всего Мастер-Групп</div>
            <div style={{ fontSize: '22px', fontWeight: 700, color: 'var(--text-color)', marginTop: '2px' }}>{totalGroups}</div>
          </div>
        </div>

        <div style={{
          background: 'rgba(30, 41, 59, 0.7)',
          border: '1px solid rgba(51, 65, 85, 0.6)',
          borderRadius: '14px',
          padding: '18px 20px',
          backdropFilter: 'blur(10px)',
          display: 'flex',
          alignItems: 'center',
          gap: '16px'
        }}>
          <div style={{ width: '48px', height: '48px', borderRadius: '12px', background: 'rgba(16, 185, 129, 0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--success)' }}>
            <Package size={24} />
          </div>
          <div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', fontWeight: 500 }}>Привязанных SKU Kaspi</div>
            <div style={{ fontSize: '22px', fontWeight: 700, color: 'var(--text-color)', marginTop: '2px' }}>{totalLinkedSkus}</div>
          </div>
        </div>

        <div style={{
          background: 'rgba(30, 41, 59, 0.7)',
          border: '1px solid rgba(51, 65, 85, 0.6)',
          borderRadius: '14px',
          padding: '18px 20px',
          backdropFilter: 'blur(10px)',
          display: 'flex',
          alignItems: 'center',
          gap: '16px'
        }}>
          <div style={{ width: '48px', height: '48px', borderRadius: '12px', background: 'rgba(245, 158, 11, 0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--warning)' }}>
            <Zap size={24} />
          </div>
          <div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', fontWeight: 500 }}>Активных синхронизаций</div>
            <div style={{ fontSize: '22px', fontWeight: 700, color: 'var(--text-color)', marginTop: '2px' }}>{activeGroupsCount}</div>
          </div>
        </div>

        <div style={{
          background: 'rgba(30, 41, 59, 0.7)',
          border: '1px solid rgba(51, 65, 85, 0.6)',
          borderRadius: '14px',
          padding: '18px 20px',
          backdropFilter: 'blur(10px)',
          display: 'flex',
          alignItems: 'center',
          gap: '16px'
        }}>
          <div style={{ width: '48px', height: '48px', borderRadius: '12px', background: zeroStockGroupsCount > 0 ? 'rgba(239, 68, 68, 0.15)' : 'rgba(100, 116, 139, 0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: zeroStockGroupsCount > 0 ? 'var(--error)' : 'var(--text-muted)' }}>
            <ShieldAlert size={24} />
          </div>
          <div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', fontWeight: 500 }}>Занулено / Снято с продажи</div>
            <div style={{ fontSize: '22px', fontWeight: 700, color: zeroStockGroupsCount > 0 ? 'var(--error)' : 'var(--text-color)', marginTop: '2px' }}>{zeroStockGroupsCount}</div>
          </div>
        </div>

      </div>

      {/* Main Table Container */}
      <div style={{
        background: 'rgba(30, 41, 59, 0.7)',
        border: '1px solid rgba(51, 65, 85, 0.6)',
        borderRadius: '16px',
        overflow: 'hidden',
        boxShadow: '0 10px 30px rgba(0,0,0,0.3)',
        backdropFilter: 'blur(12px)'
      }}>

        {/* Toolbar Filter */}
        <div style={{ padding: '20px 24px', borderBottom: '1px solid var(--card-border)', display: 'flex', flexDirection: 'column', gap: '14px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
          <div style={{ position: 'relative', width: '320px', maxWidth: '100%' }}>
            <Search size={18} style={{ position: 'absolute', left: '14px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
            <input
              type="text"
              placeholder="Поиск по названию, ID или SKU..."
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              style={{
                width: '100%',
                padding: '10px 14px 10px 42px',
                borderRadius: '10px',
                background: 'var(--card-bg)',
                border: '1px solid var(--card-border)',
                color: 'var(--text-color)',
                fontSize: '14px',
                outline: 'none',
                boxSizing: 'border-box'
              }}
            />
          </div>

          <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
            Отображено групп: <strong style={{ color: 'var(--text-color)' }}>{filteredGroups.length}</strong> из {groups.length}
          </div>
        </div>

        {/* Быстрые фильтры по состоянию остатка */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
          {FILTER_CHIPS.map(chip => {
            const isOn = stockFilter === chip.id;
            const count = filterCounts[chip.id];
            return (
              <button
                key={chip.id}
                onClick={() => setStockFilter(chip.id)}
                disabled={count === 0 && chip.id !== 'all'}
                title={
                  chip.id === 'critical' ? 'Остаток ниже или равен стоп-количеству — карточки сняты с продажи'
                  : chip.id === 'low' ? 'Остаток не более двух стоп-количеств — скоро снимется'
                  : chip.id === 'debt' ? 'Продано больше, чем числилось на остатке'
                  : chip.id === 'pack' ? 'За одну продажу списывается несколько штук'
                  : undefined
                }
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '7px',
                  padding: '6px 12px',
                  borderRadius: '20px',
                  fontSize: '12.5px',
                  fontWeight: 600,
                  cursor: count === 0 && chip.id !== 'all' ? 'default' : 'pointer',
                  opacity: count === 0 && chip.id !== 'all' ? 0.4 : 1,
                  color: isOn ? '#ffffff' : chip.color,
                  background: isOn ? chip.color : 'rgba(255,255,255,0.04)',
                  border: `1px solid ${isOn ? chip.color : 'var(--card-border)'}`,
                  transition: 'all 0.15s',
                }}
              >
                <span>{chip.label}</span>
                <span style={{
                  fontSize: '11px',
                  fontWeight: 700,
                  padding: '1px 7px',
                  borderRadius: '10px',
                  background: isOn ? 'rgba(0,0,0,0.25)' : 'rgba(255,255,255,0.07)',
                  color: isOn ? '#ffffff' : 'var(--text-muted)',
                }}>
                  {count}
                </span>
              </button>
            );
          })}
        </div>
        </div>

        {/* Table */}
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '14px' }}>
            <thead>
              <tr style={{ background: 'var(--card-bg)', borderBottom: '1px solid var(--card-border)', color: 'var(--text-muted)', fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                <th style={{ padding: '16px 24px' }}>Мастер-Группа</th>
                <th style={{ padding: '16px 24px' }}>Общий остаток</th>
                <th style={{ padding: '16px 24px' }}>Привязанные SKU (Kaspi)</th>
                <th style={{ padding: '16px 24px' }}>Статус синхронизации</th>
                <th style={{ padding: '16px 24px' }}>Последняя синхронизация</th>
                <th style={{ padding: '16px 24px', textAlign: 'right' }}>Действия</th>
              </tr>
            </thead>
            <tbody>
              {filteredGroups.length === 0 ? (
                <tr>
                  <td colSpan={6} style={{ padding: '48px 24px', textAlign: 'center', color: 'var(--text-muted)' }}>
                    <Layers size={40} style={{ marginBottom: '12px', opacity: 0.5 }} />
                    <div style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-muted)' }}>
                      {searchQuery
                        ? 'Мастер-Группы не найдены по запросу'
                        : stockFilter !== 'all'
                        ? 'В этом фильтре групп нет'
                        : 'Мастер-Группы пока не созданы'}
                    </div>
                    <p style={{ margin: '6px 0 0 0', fontSize: '13px' }}>
                      Нажмите «Создать Мастер-Группу», чтобы объединить карточки товаров на Kaspi в единый склад.
                    </p>
                  </td>
                </tr>
              ) : (
                filteredGroups.map(group => {
                  const isEditingStock = editingStockId === group.group_id;

                  return (
                    <tr key={group.group_id} style={{ borderBottom: '1px solid rgba(51, 65, 85, 0.4)', transition: 'background 0.2s' }}>
                      
                      {/* Group Name & ID */}
                      <td style={{ padding: '18px 24px' }}>
                        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '10px' }}>
                          {(() => {
                            const img = firstGroupImage(group.linked_skus, products);
                            return img ? (
                              <img
                                src={img}
                                alt=""
                                loading="lazy"
                                style={{ width: '48px', height: '48px', borderRadius: '8px', objectFit: 'cover', flexShrink: 0, background: 'rgba(255,255,255,0.04)' }}
                                onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = 'hidden'; }}
                              />
                            ) : (
                              <div style={{ width: '48px', height: '48px', borderRadius: '8px', background: 'rgba(255,255,255,0.04)', flexShrink: 0 }} />
                            );
                          })()}
                          <div>
                            <div style={{ fontWeight: 600, color: 'var(--text-color)', fontSize: '15px' }}>
                              {group.group_name}
                            </div>
                            <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'monospace', marginTop: '3px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <span>ID: {group.group_id}</span>
                              {group.external_code && (
                                <span style={{ color: 'var(--primary-hover)', background: 'rgba(56, 189, 248, 0.12)', padding: '1px 6px', borderRadius: '4px', border: '1px solid rgba(56, 189, 248, 0.3)' }}>
                                  1C: {group.external_code}
                                </span>
                              )}
                            </div>
                            {group.dumping_config?.enabled && (
                              <div style={{ marginTop: '4px' }}>
                                <span style={{
                                  fontSize: '11px',
                                  color: 'var(--warning)',
                                  background: 'rgba(245, 158, 11, 0.12)',
                                  padding: '2px 8px',
                                  borderRadius: '6px',
                                  border: '1px solid rgba(245, 158, 11, 0.3)',
                                  display: 'inline-flex',
                                  alignItems: 'center',
                                  gap: '4px'
                                }}>
                                  <Zap size={11} />
                                  <span>Демпинг: {group.dumping_config.min_price} – {group.dumping_config.max_price} ₸ ({group.dumping_config.mode || 'TOP_1'})</span>
                                </span>
                                {(group.dumping_excluded_skus?.length ?? 0) > 0 && (
                                  <span
                                    title={`Исключены из демпинга: ${group.dumping_excluded_skus!.join(', ')}`}
                                    style={{
                                      fontSize: '11px',
                                      color: 'var(--text-muted)',
                                      background: 'rgba(255,255,255,0.05)',
                                      padding: '2px 8px',
                                      borderRadius: '6px',
                                      border: '1px solid var(--card-border)',
                                      marginLeft: '6px',
                                      display: 'inline-block'
                                    }}
                                  >
                                    Исключено: {group.dumping_excluded_skus!.length}
                                  </span>
                                )}
                              </div>
                            )}
                          </div>
                        </div>
                      </td>

                      {/* Shared Stock (Inline Editable) */}
                      <td style={{ padding: '18px 24px' }}>
                        {isEditingStock ? (
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <input
                              type="number"
                              min={0}
                              value={inlineStockVal}
                              onChange={e => setInlineStockVal(parseInt(e.target.value) || 0)}
                              style={{
                                width: '80px',
                                padding: '6px 10px',
                                borderRadius: '8px',
                                background: 'var(--card-bg)',
                                border: '1px solid var(--primary)',
                                color: '#ffffff',
                                fontSize: '15px',
                                fontWeight: 700,
                                outline: 'none'
                              }}
                              autoFocus
                            />
                            <button
                              onClick={() => handleSaveInlineStock(group.group_id)}
                              title="Сохранить остаток"
                              style={{
                                padding: '6px',
                                borderRadius: '8px',
                                background: 'var(--success)',
                                border: 'none',
                                color: '#ffffff',
                                cursor: 'pointer',
                                display: 'flex',
                                alignItems: 'center'
                              }}
                            >
                              <Check size={16} />
                            </button>
                            <button
                              onClick={() => setEditingStockId(null)}
                              title="Отмена"
                              style={{
                                padding: '6px',
                                borderRadius: '8px',
                                background: 'var(--card-border)',
                                border: 'none',
                                color: '#cbd5e1',
                                cursor: 'pointer',
                                display: 'flex',
                                alignItems: 'center'
                              }}
                            >
                              <X size={16} />
                            </button>
                          </div>
                        ) : (
                          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                            <span
                              title={`Стоп кол-во: ${group.stop_qty ?? 5} шт`}
                              style={{
                                fontSize: '18px',
                                fontWeight: 700,
                                color: group.shared_stock <= (group.stop_qty ?? 5) ? 'var(--error)' : 'var(--primary-hover)',
                                padding: '4px 12px',
                                borderRadius: '8px',
                                background: group.shared_stock <= (group.stop_qty ?? 5) ? 'rgba(239, 68, 68, 0.12)' : 'rgba(56, 189, 248, 0.12)',
                                border: group.shared_stock <= (group.stop_qty ?? 5) ? '1px solid rgba(239, 68, 68, 0.3)' : '1px solid rgba(56, 189, 248, 0.3)'
                              }}>
                              {group.shared_stock} шт
                            </span>
                            {(group.deduct_per_unit ?? 1) > 1 && (
                              // Иначе «заказали 1 — списали 7» снова читается
                              // как поломка движка, а не как настройка группы.
                              <span
                                title={`За одну проданную единицу списывается ${group.deduct_per_unit} шт. остатка`}
                                style={{
                                  fontSize: '12px',
                                  fontWeight: 700,
                                  color: '#c084fc',
                                  padding: '4px 10px',
                                  borderRadius: '8px',
                                  background: 'rgba(168, 85, 247, 0.12)',
                                  border: '1px solid rgba(168, 85, 247, 0.35)',
                                  whiteSpace: 'nowrap'
                                }}>
                                ×{group.deduct_per_unit} за продажу
                              </span>
                            )}
                            {(group.oversold ?? 0) > 0 && (
                              // Остаток обрезан нулём (в Kaspi отрицательное не
                              // уходит), поэтому нехватку показываем отдельно —
                              // иначе перепродажа выглядит как обычный ноль.
                              <span
                                title={`Продано на ${group.oversold} шт. больше, чем числилось. Сбросится при вводе остатка вручную.`}
                                style={{
                                  fontSize: '13px',
                                  fontWeight: 700,
                                  color: 'var(--error)',
                                  padding: '4px 10px',
                                  borderRadius: '8px',
                                  background: 'rgba(239, 68, 68, 0.12)',
                                  border: '1px solid rgba(239, 68, 68, 0.3)',
                                  whiteSpace: 'nowrap'
                                }}>
                                ⚠ долг {group.oversold}
                              </span>
                            )}
                            <button
                              onClick={() => handleStartInlineEdit(group)}
                              title="Быстрое изменение остатка"
                              style={{
                                background: 'none',
                                border: 'none',
                                color: 'var(--text-muted)',
                                cursor: 'pointer',
                                padding: '4px',
                                borderRadius: '6px',
                                display: 'flex',
                                alignItems: 'center'
                              }}
                            >
                              <Edit2 size={15} />
                            </button>
                          </div>
                        )}
                      </td>

                      {/* Linked SKU chips — один чип на КАРТОЧКУ товара */}
                      <td style={{ padding: '18px 24px', maxWidth: '340px' }}>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                          {(() => {
                            const cards = buildLinkedCards(group.linked_skus, products);
                            const visible = expandedSkusGroupId === group.group_id ? cards : cards.slice(0, 6);
                            return (
                              <>
                                {visible.map(card => {
                                  const chipStyle: React.CSSProperties = {
                                    padding: '4px 10px',
                                    borderRadius: '20px',
                                    // Снятая карточка — янтарная: её остаток в группе
                                    // считается, но на витрине товара нет.
                                    background: card.archived ? 'rgba(245, 158, 11, 0.12)' : 'rgba(255,255,255,0.05)',
                                    border: card.archived ? '1px solid rgba(245, 158, 11, 0.45)' : '1px solid var(--card-border)',
                                    color: card.archived ? '#f59e0b' : (card.kaspiSku ? 'var(--primary-hover)' : '#cbd5e1'),
                                    fontSize: '12px',
                                    fontWeight: 500,
                                    fontFamily: 'monospace',
                                    display: 'inline-flex',
                                    alignItems: 'center',
                                    gap: '4px',
                                    textDecoration: 'none',
                                  };
                                  const hint = [
                                    card.archived ? '⚠️ Снята с продажи в кабинете' : null,
                                    card.name,
                                    card.aliases.length > 1 ? `Идентификаторы: ${card.aliases.join(', ')}` : null,
                                    card.kaspiSku ? 'Открыть карточку на Kaspi' : null,
                                  ].filter(Boolean).join('\n');

                                  return card.kaspiSku ? (
                                    <a
                                      key={card.key}
                                      href={KASPI_CARD_URL(card.kaspiSku)}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      title={hint || card.label}
                                      style={chipStyle}
                                    >
                                      <Package size={12} color={card.archived ? '#f59e0b' : 'var(--primary-hover)'} />
                                      {card.label}
                                      <ExternalLink size={11} />
                                    </a>
                                  ) : (
                                    <span key={card.key} title={hint || card.label} style={chipStyle}>
                                      <Package size={12} color="var(--text-muted)" />
                                      {card.label}
                                    </span>
                                  );
                                })}

                                {cards.length > 6 && (
                                  <button
                                    onClick={() => setExpandedSkusGroupId(
                                      expandedSkusGroupId === group.group_id ? null : group.group_id
                                    )}
                                    style={{
                                      padding: '4px 10px',
                                      borderRadius: '20px',
                                      background: 'transparent',
                                      border: '1px dashed var(--card-border)',
                                      color: 'var(--text-muted)',
                                      fontSize: '12px',
                                      cursor: 'pointer',
                                    }}
                                  >
                                    {expandedSkusGroupId === group.group_id ? 'свернуть' : `+ ещё ${cards.length - 6}`}
                                  </button>
                                )}
                              </>
                            );
                          })()}
                        </div>
                      </td>

                      {/* Sync Status Badge */}
                      <td style={{ padding: '18px 24px' }}>
                        {group.status === 'deducting' ? (
                          /* Продажа: списание по заказу + следующая за ним веерная
                             рассылка. Отдельный статус, чтобы отличать продажу от
                             ручной правки остатка. */
                          <div style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', padding: '6px 12px', borderRadius: '20px', background: 'rgba(168, 85, 247, 0.15)', border: '1px solid rgba(168, 85, 247, 0.35)', color: '#c084fc', fontSize: '13px', fontWeight: 600 }}>
                            <ShoppingCart size={14} className="spin" />
                            <span>Идёт списание</span>
                          </div>
                        ) : group.status === 'updating' ? (
                          <div style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', padding: '6px 12px', borderRadius: '20px', background: 'rgba(59, 130, 246, 0.15)', border: '1px solid rgba(59, 130, 246, 0.3)', color: 'var(--primary-hover)', fontSize: '13px', fontWeight: 600 }}>
                            <RefreshCw size={14} className="spin" />
                            <span>Обновление...</span>
                          </div>
                        ) : group.status === 'synced' ? (
                          <div style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', padding: '6px 12px', borderRadius: '20px', background: 'rgba(16, 185, 129, 0.15)', border: '1px solid rgba(16, 185, 129, 0.3)', color: 'var(--success)', fontSize: '13px', fontWeight: 600 }}>
                            <CheckCircle2 size={15} />
                            <span>Синхронизировано</span>
                          </div>
                        ) : group.status === 'warning' ? (
                          <div style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', padding: '6px 12px', borderRadius: '20px', background: 'rgba(245, 158, 11, 0.15)', border: '1px solid rgba(245, 158, 11, 0.3)', color: 'var(--warning)', fontSize: '13px', fontWeight: 600 }}>
                            <AlertTriangle size={15} />
                            <span>Частично</span>
                          </div>
                        ) : (
                          <div style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', padding: '6px 12px', borderRadius: '20px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239, 68, 68, 0.3)', color: 'var(--error)', fontSize: '13px', fontWeight: 600 }}>
                            <ShieldAlert size={15} />
                            <span>Ошибка sync</span>
                          </div>
                        )}
                      </td>

                      {/* Last Sync Log */}
                      <td style={{ padding: '18px 24px', fontSize: '13px', color: 'var(--text-muted)' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <Clock size={14} color="var(--text-muted)" />
                          <span>{group.last_synced_at || 'Еще не запускалась'}</span>
                        </div>
                        {group.last_sync_log && (
                          <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '3px', textOverflow: 'ellipsis', overflow: 'hidden', whiteSpace: 'nowrap', maxWidth: '180px' }}>
                            {group.last_sync_log}
                          </div>
                        )}
                      </td>

                      {/* Actions */}
                      <td style={{ padding: '18px 24px', textAlign: 'right' }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: '8px' }}>
                          <button
                            onClick={() => setViewLogGroup(group)}
                            title="Просмотреть пошаговый лог синхронизации"
                            style={{
                              padding: '8px',
                              borderRadius: '8px',
                              background: 'rgba(255,255,255,0.05)',
                              border: '1px solid var(--card-border)',
                              color: 'var(--primary-hover)',
                              cursor: 'pointer',
                              display: 'flex',
                              alignItems: 'center'
                            }}
                          >
                            <FileText size={16} />
                          </button>

                          <button
                            onClick={() => handleManualSync(group.group_id)}
                            disabled={syncingGroupId === group.group_id}
                            title="Принудительная отправка остатков на Kaspi"
                            style={{
                              padding: '8px',
                              borderRadius: '8px',
                              background: 'rgba(255,255,255,0.05)',
                              border: '1px solid var(--card-border)',
                              color: 'var(--primary)',
                              cursor: 'pointer',
                              display: 'flex',
                              alignItems: 'center'
                            }}
                          >
                            <RefreshCw size={16} className={syncingGroupId === group.group_id ? 'spin' : ''} />
                          </button>

                          <button
                            onClick={() => handleOpenEditModal(group)}
                            title="Редактировать группу"
                            style={{
                              padding: '8px',
                              borderRadius: '8px',
                              background: 'rgba(255,255,255,0.05)',
                              border: '1px solid var(--card-border)',
                              color: '#cbd5e1',
                              cursor: 'pointer',
                              display: 'flex',
                              alignItems: 'center'
                            }}
                          >
                            <Edit2 size={16} />
                          </button>

                          <button
                            onClick={() => handleDeleteGroup(group)}
                            title="Удалить группу"
                            style={{
                              padding: '8px',
                              borderRadius: '8px',
                              background: 'rgba(239, 68, 68, 0.1)',
                              border: '1px solid rgba(239, 68, 68, 0.2)',
                              color: 'var(--error)',
                              cursor: 'pointer',
                              display: 'flex',
                              alignItems: 'center'
                            }}
                          >
                            <Trash2 size={16} />
                          </button>
                        </div>
                      </td>

                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

      </div>

      {/* Modal Dialog: Create / Edit Group */}
      {isModalOpen && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          zIndex: 9000,
          background: 'rgba(15, 23, 42, 0.8)',
          backdropFilter: 'blur(8px)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '24px'
        }}>
          <div style={{
            width: '100%',
            maxWidth: '680px',
            maxHeight: '90vh',
            background: 'rgba(255,255,255,0.05)',
            border: '1px solid var(--card-border)',
            borderRadius: '20px',
            boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)',
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden'
          }}>
            
            {/* Modal Header */}
            <div style={{ padding: '24px 28px', borderBottom: '1px solid var(--card-border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <h2 style={{ margin: 0, fontSize: '20px', fontWeight: 700, color: 'var(--text-color)' }}>
                  {editingGroupId ? 'Редактирование Мастер-Группы' : 'Создание новой Мастер-Группы'}
                </h2>
                <p style={{ margin: '4px 0 0 0', fontSize: '13px', color: 'var(--text-muted)' }}>
                  Укажите название, доступный складской остаток и свяжите SKU товаров Kaspi
                </p>
              </div>
              <button
                onClick={() => setIsModalOpen(false)}
                style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: '6px', borderRadius: '8px' }}
              >
                <X size={20} />
              </button>
            </div>

            {/* Modal Body */}
            <form onSubmit={handleSubmitModal} style={{ padding: '24px 28px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '20px' }}>
              
              {/* Group Name */}
              <div>
                <label style={{ display: 'block', fontSize: '13px', fontWeight: 600, color: '#cbd5e1', marginBottom: '8px' }}>
                  Название группы остатка *
                </label>
                <input
                  type="text"
                  required
                  placeholder="Например: Световая фигура D655 4см"
                  value={formGroupName}
                  onChange={e => setFormGroupName(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '12px 16px',
                    borderRadius: '10px',
                    background: 'var(--card-bg)',
                    border: '1px solid var(--card-border)',
                    color: '#ffffff',
                    fontSize: '14px',
                    outline: 'none',
                    boxSizing: 'border-box'
                  }}
                />
              </div>

              {/* Shared Stock Input */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '16px' }}>
                <div>
                  <label style={{ display: 'block', fontSize: '13px', fontWeight: 600, color: '#cbd5e1', marginBottom: '8px' }}>
                    Общий физический остаток *
                  </label>
                  <input
                    type="number"
                    min="0"
                    value={formSharedStock}
                    onChange={e => setFormSharedStock(parseInt(e.target.value) || 0)}
                    required
                    style={{
                      width: '100%',
                      padding: '10px 14px',
                      borderRadius: '10px',
                      background: 'var(--card-bg)',
                      border: '1px solid var(--card-border)',
                      color: 'var(--text-color)',
                      fontSize: '14px',
                      boxSizing: 'border-box'
                    }}
                  />
                </div>

                {/* Stop Qty Input */}
                <div>
                  <label style={{ display: 'block', fontSize: '13px', fontWeight: 600, color: '#cbd5e1', marginBottom: '8px' }}>
                    Стоп кол-во
                  </label>
                  <input
                    type="number"
                    min="0"
                    value={formStopQty}
                    onChange={e => setFormStopQty(parseInt(e.target.value) || 0)}
                    title="При остатке <= этого значения группа снимается с продажи на Kaspi и приходит уведомление в Telegram"
                    style={{
                      width: '100%',
                      padding: '10px 14px',
                      borderRadius: '10px',
                      background: 'var(--card-bg)',
                      border: '1px solid var(--card-border)',
                      color: 'var(--text-color)',
                      fontSize: '14px',
                      boxSizing: 'border-box'
                    }}
                  />
                </div>

                {/* Deduct Per Unit Input */}
                <div>
                  <label style={{ display: 'block', fontSize: '13px', fontWeight: 600, color: '#cbd5e1', marginBottom: '8px' }}>
                    Списывать за 1 продажу
                  </label>
                  <input
                    type="number"
                    min="1"
                    value={formDeductPerUnit}
                    onChange={e => setFormDeductPerUnit(Math.max(1, parseInt(e.target.value) || 1))}
                    title="Сколько штук общего остатка съедает ОДНА проданная единица. Обычный товар — 1. Комплект (продаётся набором, а остаток считается поштучно) — размер набора."
                    style={{
                      width: '100%',
                      padding: '10px 14px',
                      borderRadius: '10px',
                      background: 'var(--card-bg)',
                      border: formDeductPerUnit > 1 ? '1px solid rgba(168, 85, 247, 0.5)' : '1px solid var(--card-border)',
                      color: formDeductPerUnit > 1 ? '#c084fc' : 'var(--text-color)',
                      fontSize: '14px',
                      fontWeight: formDeductPerUnit > 1 ? 700 : 400,
                      boxSizing: 'border-box'
                    }}
                  />
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '5px', lineHeight: 1.4 }}>
                    {formDeductPerUnit > 1
                      ? `Заказ на 1 шт. уменьшит остаток на ${formDeductPerUnit} шт.`
                      : 'Обычный товар: сколько заказали, столько и спишется.'}
                  </div>
                </div>

                {/* 1C External Code Input */}
                <div>
                  <label style={{ display: 'block', fontSize: '13px', fontWeight: 600, color: '#cbd5e1', marginBottom: '8px' }}>
                    Код товара в 1С / МойСклад (external_code)
                  </label>
                  <input
                    type="text"
                    value={formExternalCode}
                    onChange={e => setFormExternalCode(e.target.value)}
                    placeholder="Например: 1C_ARTICLE_001"
                    style={{
                      width: '100%',
                      padding: '10px 14px',
                      borderRadius: '10px',
                      background: 'var(--card-bg)',
                      border: '1px solid var(--card-border)',
                      color: 'var(--primary-hover)',
                      fontFamily: 'monospace',
                      fontSize: '14px',
                      boxSizing: 'border-box'
                    }}
                  />
                </div>

                {/* Active Toggle */}
                <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
                  <label style={{ display: 'block', fontSize: '13px', fontWeight: 600, color: '#cbd5e1', marginBottom: '8px' }}>
                    Статус активности
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer', background: 'var(--card-bg)', padding: '10px 16px', borderRadius: '10px', border: '1px solid var(--card-border)' }}>
                    <input
                      type="checkbox"
                      checked={formIsActive}
                      onChange={e => setFormIsActive(e.target.checked)}
                      style={{ width: '18px', height: '18px', accentColor: 'var(--primary)', cursor: 'pointer' }}
                    />
                    <span style={{ fontSize: '14px', fontWeight: 500, color: formIsActive ? 'var(--success)' : 'var(--text-muted)' }}>
                      {formIsActive ? 'Активна (Синхронизировать)' : 'Пауза'}
                    </span>
                  </label>
                </div>
              </div>

              {/* Group Dumping Config Section */}
              <div style={{ background: 'var(--bg-color)', padding: '16px', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.05)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer', fontWeight: 600, fontSize: '14px', color: formDumpingEnabled ? 'var(--warning)' : '#cbd5e1' }}>
                    <input
                      type="checkbox"
                      checked={formDumpingEnabled}
                      onChange={e => setFormDumpingEnabled(e.target.checked)}
                      style={{ width: '18px', height: '18px', accentColor: 'var(--warning)', cursor: 'pointer' }}
                    />
                    <Zap size={18} color={formDumpingEnabled ? 'var(--warning)' : 'var(--text-muted)'} />
                    <span>Включить авто-демпинг на уровне группы</span>
                  </label>
                </div>

                {formDumpingEnabled && (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: '12px', marginTop: '14px' }}>
                    <div>
                      <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Режим демпинга</label>
                      <select
                        value={formDumpingMode}
                        onChange={e => setFormDumpingMode(e.target.value)}
                        style={{ width: '100%', padding: '8px 10px', borderRadius: '8px', background: 'var(--card-bg)', border: '1px solid var(--card-border)', color: '#fff', fontSize: '12px' }}
                      >
                        <option value="TOP_1">🏆 1-е место</option>
                        <option value="TOP_2">🥈 2-е место</option>
                        <option value="MATCH_FIRST">🎯 Прижиматься к 1-му место</option>
                        <option value="TARGET_POSITION">📍 N-е место</option>
                      </select>
                    </div>

                    <div>
                      <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Мин. цена (₸)</label>
                      <input
                        type="number"
                        value={formMinPrice}
                        onChange={e => setFormMinPrice(parseFloat(e.target.value) || 0)}
                        style={{ width: '100%', padding: '8px 10px', borderRadius: '8px', background: 'var(--card-bg)', border: '1px solid var(--card-border)', color: '#fff', fontSize: '12px' }}
                      />
                    </div>

                    <div>
                      <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Макс. цена (₸)</label>
                      <input
                        type="number"
                        value={formMaxPrice}
                        onChange={e => setFormMaxPrice(parseFloat(e.target.value) || 0)}
                        style={{ width: '100%', padding: '8px 10px', borderRadius: '8px', background: 'var(--card-bg)', border: '1px solid var(--card-border)', color: '#fff', fontSize: '12px' }}
                      />
                    </div>

                    <div>
                      <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Шаг демпинга (₸)</label>
                      <input
                        type="number"
                        value={formDumpStep}
                        onChange={e => setFormDumpStep(parseFloat(e.target.value) || 2)}
                        style={{ width: '100%', padding: '8px 10px', borderRadius: '8px', background: 'var(--card-bg)', border: '1px solid var(--card-border)', color: '#fff', fontSize: '12px' }}
                      />
                    </div>
                  </div>
                )}

                {formDumpingEnabled && formSelectedSkus.length > 0 && (
                  <div style={{ marginTop: '14px', paddingTop: '14px', borderTop: '1px solid rgba(255,255,255,0.06)' }}>
                    <label style={{ display: 'block', fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '8px' }}>
                      Исключить из группового демпинга (эти товары демпинг группы не тронет — при наличии у них собственного индивидуального демпинга он продолжит работать как обычно)
                    </label>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '160px', overflowY: 'auto' }}>
                      {formSelectedSkus.map(sku => {
                        const pMatch = products.find(p => p.sku === sku || p.merchantSku === sku);
                        const isExcluded = formDumpingExcludedSkus.includes(sku);
                        return (
                          <label
                            key={sku}
                            onClick={() => handleToggleDumpingExclusion(sku)}
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: '10px',
                              padding: '6px 10px',
                              borderRadius: '8px',
                              cursor: 'pointer',
                              background: isExcluded ? 'rgba(239, 68, 68, 0.1)' : 'transparent',
                              border: isExcluded ? '1px solid rgba(239, 68, 68, 0.25)' : '1px solid transparent',
                            }}
                          >
                            <input
                              type="checkbox"
                              checked={isExcluded}
                              onChange={() => {}}
                              style={{ width: '14px', height: '14px', accentColor: 'var(--error)', cursor: 'pointer' }}
                            />
                            <span style={{ fontSize: '12px', color: isExcluded ? 'var(--error)' : '#cbd5e1' }}>
                              {pMatch?.name ? `${pMatch.name} ` : ''}
                              <span style={{ fontFamily: 'monospace', color: 'var(--text-muted)' }}>({sku})</span>
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>

              {/* Multi-Select SKU Section */}
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                  <label style={{ fontSize: '13px', fontWeight: 600, color: '#cbd5e1' }}>
                    Привязать товары Kaspi (Multi-Select SKU) *
                  </label>
                  <span style={{ fontSize: '12px', color: 'var(--primary)', fontWeight: 600 }}>
                    Выбрано: {formSelectedSkus.length} SKU
                  </span>
                </div>

                {/* Modal SKU Search */}
                <div style={{ position: 'relative', marginBottom: '10px' }}>
                  <Search size={16} style={{ position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
                  <input
                    type="text"
                    placeholder="Поиск товара в вашем каталоге по имени или SKU..."
                    value={modalProductSearch}
                    onChange={e => setModalProductSearch(e.target.value)}
                    style={{
                      width: '100%',
                      padding: '8px 12px 8px 36px',
                      borderRadius: '8px',
                      background: 'var(--card-bg)',
                      border: '1px solid var(--card-border)',
                      color: 'var(--text-color)',
                      fontSize: '13px',
                      outline: 'none',
                      boxSizing: 'border-box'
                    }}
                  />
                </div>

                {/* Product List Pick Container */}
                <div style={{
                  maxHeight: '220px',
                  overflowY: 'auto',
                  background: 'var(--card-bg)',
                  border: '1px solid var(--card-border)',
                  borderRadius: '10px',
                  padding: '8px'
                }}>
                  {modalFilteredProducts.length === 0 ? (
                    <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
                      Товары не найдены в кэше ассортимента
                    </div>
                  ) : (
                    modalFilteredProducts.map(p => {
                      const skuVal = p.sku || p.merchantSku || '';
                      const isSelected = formSelectedSkus.includes(skuVal);
                      const archived = isArchived(p);

                      return (
                        <div
                          key={skuVal}
                          onClick={() => handleToggleSkuSelection(skuVal, archived)}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            padding: '10px 12px',
                            borderRadius: '8px',
                            cursor: 'pointer',
                            // Снятые с продажи подсвечиваем янтарным: у них title=null и
                            // price=0, поэтому без метки строка неотличима от активного
                            // товара, у которого просто кончился остаток.
                            background: isSelected
                              ? (archived ? 'rgba(245, 158, 11, 0.18)' : 'rgba(99, 102, 241, 0.15)')
                              : (archived ? 'rgba(245, 158, 11, 0.06)' : 'transparent'),
                            border: isSelected
                              ? (archived ? '1px solid rgba(245, 158, 11, 0.55)' : '1px solid rgba(99, 102, 241, 0.4)')
                              : (archived ? '1px solid rgba(245, 158, 11, 0.22)' : '1px solid transparent'),
                            marginBottom: '4px',
                            transition: 'all 0.15s'
                          }}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => {}} // handled by parent div onClick
                              style={{ width: '16px', height: '16px', accentColor: 'var(--primary)' }}
                            />
                            <div>
                              <div style={{ fontSize: '13px', fontWeight: 600, color: archived ? '#f59e0b' : 'var(--text-color)' }}>
                                {p.name}
                                {archived && (
                                  <span style={{
                                    marginLeft: '8px',
                                    padding: '1px 7px',
                                    borderRadius: '20px',
                                    background: 'rgba(245, 158, 11, 0.15)',
                                    border: '1px solid rgba(245, 158, 11, 0.4)',
                                    fontSize: '10px',
                                    fontWeight: 600,
                                    letterSpacing: '0.02em',
                                  }}>
                                    Снят с продажи
                                  </span>
                                )}
                              </div>
                              <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'monospace', marginTop: '2px' }}>
                                Kaspi SKU: {skuVal}
                                {p.merchantSku && p.merchantSku !== skuVal && (
                                  <span style={{ color: 'var(--primary-hover)', marginLeft: '8px' }}>
                                    • Merchant SKU: {p.merchantSku}
                                  </span>
                                )}
                                {p.brand ? ` • Бренд: ${p.brand}` : ''}
                              </div>

                            </div>
                          </div>

                          <div style={{ textAlign: 'right', fontSize: '12px' }}>
                            <div style={{ fontWeight: 600, color: '#cbd5e1' }}>{p.price || (p.price_num ? `${p.price_num} ₸` : 'N/A')}</div>
                            <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Остаток: {p.stock ?? 10} шт</div>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>

              {/* Submit Buttons */}
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px', marginTop: '12px' }}>
                <button
                  type="button"
                  onClick={() => setIsModalOpen(false)}
                  style={{
                    padding: '10px 20px',
                    borderRadius: '10px',
                    background: 'var(--card-border)',
                    border: 'none',
                    color: '#cbd5e1',
                    fontSize: '14px',
                    fontWeight: 500,
                    cursor: 'pointer'
                  }}
                >
                  Отмена
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  style={{
                    padding: '10px 24px',
                    borderRadius: '10px',
                    background: 'linear-gradient(135deg, var(--primary-hover) 0%, var(--primary) 100%)',
                    border: 'none',
                    color: '#ffffff',
                    fontSize: '14px',
                    fontWeight: 600,
                    cursor: 'pointer',
                    boxShadow: '0 4px 14px rgba(79, 70, 229, 0.4)'
                  }}
                >
                  {submitting ? 'Сохранение...' : editingGroupId ? 'Сохранить изменения' : 'Создать группу'}
                </button>
              </div>

            </form>

          </div>
        </div>
      )}

      {/* Detailed Log Modal */}
      {viewLogGroup && (
        <div style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(0,0,0,0.75)',
          zIndex: 9999,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          backdropFilter: 'blur(8px)',
          padding: '16px'
        }}>
          <div style={{
            background: 'var(--card-bg)',
            border: '1px solid var(--card-border)',
            borderRadius: '16px',
            padding: '24px',
            maxWidth: '680px',
            width: '100%',
            color: 'var(--text-color)',
            boxShadow: '0 20px 40px rgba(0,0,0,0.5)'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px', borderBottom: '1px solid rgba(255,255,255,0.05)', paddingBottom: '12px' }}>
              <div>
                <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <FileText size={20} color="var(--primary-hover)" />
                  <span>Пошаговый лог синхронизации</span>
                </h3>
                <p style={{ margin: '4px 0 0 0', fontSize: '12px', color: 'var(--text-muted)' }}>
                  Группа: <strong style={{ color: 'var(--text-color)' }}>{viewLogGroup.group_name}</strong> (ID: <code>{viewLogGroup.group_id}</code>)
                </p>
              </div>
              <button
                onClick={() => setViewLogGroup(null)}
                style={{ background: 'none', border: 'none', color: 'var(--text-muted)', fontSize: '20px', cursor: 'pointer', padding: '4px' }}
              >
                <X size={20} />
              </button>
            </div>

            <div style={{ marginBottom: '12px', fontSize: '13px', color: 'var(--text-muted)', display: 'flex', justifyContent: 'space-between' }}>
              <span>Время запуска: <strong>{viewLogGroup.last_synced_at || 'Еще не синхронизировалась'}</strong></span>
              <span>Итог: <strong style={{ color: viewLogGroup.status === 'synced' ? 'var(--success)' : 'var(--error)' }}>{viewLogGroup.last_sync_log || viewLogGroup.status}</strong></span>
            </div>

            <div style={{
              height: '320px',
              overflowY: 'auto',
              fontFamily: 'Consolas, Monaco, "Andale Mono", monospace',
              fontSize: '12px',
              background: 'var(--bg-color)',
              padding: '14px',
              borderRadius: '10px',
              border: '1px solid rgba(255,255,255,0.05)',
              lineHeight: 1.6
            }}>
              {viewLogGroup.sync_logs && viewLogGroup.sync_logs.length > 0 ? (
                viewLogGroup.sync_logs.map((line, i) => (
                  <div key={i} style={{
                    color: line.includes('❌') ? 'var(--error)' : line.includes('⚠️') ? 'var(--warning)' : 'var(--success)',
                    marginBottom: '6px',
                    wordBreak: 'break-word'
                  }}>
                    {line}
                  </div>
                ))
              ) : (
                <div style={{ color: 'var(--text-muted)', fontStyle: 'italic', padding: '12px 0' }}>
                  {viewLogGroup.last_sync_log || 'Детальный лог пока пуст.'}
                </div>
              )}
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '16px' }}>
              <button
                onClick={() => setViewLogGroup(null)}
                style={{
                  padding: '8px 20px',
                  borderRadius: '8px',
                  background: 'var(--card-border)',
                  border: 'none',
                  color: '#ffffff',
                  cursor: 'pointer',
                  fontWeight: 600,
                  fontSize: '13px'
                }}
              >
                Закрыть
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
};
export default MasterStockGroups;
