import React, { useState, useEffect, useMemo } from 'react';
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
  Zap
} from 'lucide-react';

interface MasterStockGroup {
  group_id: string;
  group_name: string;
  shared_stock: number;
  linked_skus: string[];
  external_code?: string;
  is_active: boolean;
  status: 'synced' | 'updating' | 'error';
  created_at: string;
  updated_at: string;

  last_synced_at?: string;
  last_sync_log?: string;
}


interface ProductItem {
  sku: string;
  name: string;
  price_num?: number;
  price?: string;
  stock?: number;
  brand?: string;
  merchantSku?: string;
  kaspiSku?: string;
}

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:5000';

export const MasterStockGroups: React.FC = () => {
  const [groups, setGroups] = useState<MasterStockGroup[]>([]);
  const [products, setProducts] = useState<ProductItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [syncingGroupId, setSyncingGroupId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState<string>('');

  // Modal State
  const [isModalOpen, setIsModalOpen] = useState<boolean>(false);
  const [editingGroupId, setEditingGroupId] = useState<string | null>(null);
  const [formGroupName, setFormGroupName] = useState<string>('');
  const [formSharedStock, setFormSharedStock] = useState<number>(10);
  const [formSelectedSkus, setFormSelectedSkus] = useState<string[]>([]);
  const [formExternalCode, setFormExternalCode] = useState<string>('');
  const [formIsActive, setFormIsActive] = useState<boolean>(true);
  const [modalProductSearch, setModalProductSearch] = useState<string>('');
  const [submitting, setSubmitting] = useState<boolean>(false);

  // Inline Stock Edit State
  const [editingStockId, setEditingStockId] = useState<string | null>(null);
  const [inlineStockVal, setInlineStockVal] = useState<number>(0);

  // Toast / Alert State
  const [toastMessage, setToastMessage] = useState<{ type: 'success' | 'error' | 'warning'; text: string } | null>(null);

  const showToast = (type: 'success' | 'error' | 'warning', text: string) => {
    setToastMessage({ type, text });
    setTimeout(() => setToastMessage(null), 4000);
  };

  const fetchGroups = async () => {
    try {
      const res = await fetch(`${API_URL}/api/stock-groups`);
      const data = await res.json();
      if (data.success) {
        setGroups(data.groups || []);
      }
    } catch (e) {
      console.error('Error fetching stock groups:', e);
    }
  };

  const fetchProducts = async () => {
    try {
      const res = await fetch(`${API_URL}/api/products?page=1&page_size=10000&status=all`);
      const data = await res.json();
      if (data.success) {
        setProducts(data.products || []);
      }
    } catch (e) {
      console.error('Error fetching products:', e);
    }
  };

  const loadData = async () => {
    setLoading(true);
    await Promise.all([fetchGroups(), fetchProducts()]);
    setLoading(false);
  };

  useEffect(() => {
    loadData();
    // Auto-refresh periodically for real-time status updates
    const interval = setInterval(() => {
      fetchGroups();
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  // Filtered Groups
  const filteredGroups = useMemo(() => {
    if (!searchQuery.trim()) return groups;
    const q = searchQuery.toLowerCase();
    return groups.filter(g =>
      g.group_name.toLowerCase().includes(q) ||
      g.group_id.toLowerCase().includes(q) ||
      (g.external_code && g.external_code.toLowerCase().includes(q)) ||
      g.linked_skus.some(s => s.toLowerCase().includes(q))
    );
  }, [groups, searchQuery]);

  // Metrics
  const totalGroups = groups.length;
  const totalLinkedSkus = useMemo(() => {
    const skuSet = new Set<string>();
    groups.forEach(g => g.linked_skus.forEach(s => skuSet.add(s)));
    return skuSet.size;
  }, [groups]);
  const zeroStockGroupsCount = useMemo(() => groups.filter(g => g.shared_stock === 0).length, [groups]);
  const activeGroupsCount = useMemo(() => groups.filter(g => g.is_active).length, [groups]);

  // Modal Handlers
  const handleOpenCreateModal = () => {
    setEditingGroupId(null);
    setFormGroupName('');
    setFormSharedStock(10);
    setFormSelectedSkus([]);
    setFormExternalCode('');
    setFormIsActive(true);
    setModalProductSearch('');
    setIsModalOpen(true);
  };

  const handleOpenEditModal = (group: MasterStockGroup) => {
    setEditingGroupId(group.group_id);
    setFormGroupName(group.group_name);
    setFormSharedStock(group.shared_stock);
    setFormSelectedSkus([...group.linked_skus]);
    setFormExternalCode(group.external_code || '');
    setFormIsActive(group.is_active);
    setModalProductSearch('');
    setIsModalOpen(true);
  };

  const handleToggleSkuSelection = (sku: string) => {
    setFormSelectedSkus(prev =>
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

      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          group_name: formGroupName.trim(),
          shared_stock: formSharedStock,
          linked_skus: formSelectedSkus,
          external_code: formExternalCode.trim() || undefined,
          is_active: formIsActive,
        }),
      });


      const data = await res.json();
      if (data.success) {
        showToast('success', data.message || 'Мастер-Группа сохранена.');
        setIsModalOpen(false);
        fetchGroups();
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
      const res = await fetch(`${API_URL}/api/stock-groups/${groupId}/stock`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shared_stock: inlineStockVal }),
      });
      const data = await res.json();
      if (data.success) {
        showToast('success', data.message || 'Остаток группы обновлен.');
        setEditingStockId(null);
        fetchGroups();
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
      const res = await fetch(`${API_URL}/api/stock-groups/${groupId}/sync`, { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        showToast('success', 'Синхронизация запущена на Kaspi.');
        fetchGroups();
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
      const res = await fetch(`${API_URL}/api/stock-groups/${group.group_id}`, { method: 'DELETE' });

      const data = await res.json();
      if (data.success) {
        showToast('success', data.message || 'Группа удалена.');
        fetchGroups();
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
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px', color: '#f1f5f9' }}>
      
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
              background: 'linear-gradient(135deg, #6366f1 0%, #4f46e5 100%)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              boxShadow: '0 4px 14px rgba(99, 102, 241, 0.35)'
            }}>
              <Boxes size={24} color="#ffffff" />
            </div>
            <div>
              <h1 style={{ margin: 0, fontSize: '24px', fontWeight: 700, color: '#f8fafc', letterSpacing: '-0.02em' }}>
                Единый связный учет остатков (Master Stock Sync)
              </h1>
              <p style={{ margin: '4px 0 0 0', fontSize: '13px', color: '#94a3b8' }}>
                Объединение карточек товаров Kaspi в единые Мастер-Группы с автоматическим веерным списанием остатков
              </p>
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <button
            onClick={loadData}
            disabled={loading}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '10px 16px',
              borderRadius: '10px',
              background: '#1e293b',
              border: '1px solid #334155',
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
              background: 'linear-gradient(135deg, #4f46e5 0%, #6366f1 100%)',
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
          <div style={{ width: '48px', height: '48px', borderRadius: '12px', background: 'rgba(99, 102, 241, 0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#818cf8' }}>
            <Layers size={24} />
          </div>
          <div>
            <div style={{ fontSize: '12px', color: '#94a3b8', fontWeight: 500 }}>Всего Мастер-Групп</div>
            <div style={{ fontSize: '22px', fontWeight: 700, color: '#f8fafc', marginTop: '2px' }}>{totalGroups}</div>
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
          <div style={{ width: '48px', height: '48px', borderRadius: '12px', background: 'rgba(16, 185, 129, 0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#34d399' }}>
            <Package size={24} />
          </div>
          <div>
            <div style={{ fontSize: '12px', color: '#94a3b8', fontWeight: 500 }}>Привязанных SKU Kaspi</div>
            <div style={{ fontSize: '22px', fontWeight: 700, color: '#f8fafc', marginTop: '2px' }}>{totalLinkedSkus}</div>
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
          <div style={{ width: '48px', height: '48px', borderRadius: '12px', background: 'rgba(245, 158, 11, 0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fbbf24' }}>
            <Zap size={24} />
          </div>
          <div>
            <div style={{ fontSize: '12px', color: '#94a3b8', fontWeight: 500 }}>Активных синхронизаций</div>
            <div style={{ fontSize: '22px', fontWeight: 700, color: '#f8fafc', marginTop: '2px' }}>{activeGroupsCount}</div>
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
          <div style={{ width: '48px', height: '48px', borderRadius: '12px', background: zeroStockGroupsCount > 0 ? 'rgba(239, 68, 68, 0.15)' : 'rgba(100, 116, 139, 0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: zeroStockGroupsCount > 0 ? '#f87171' : '#94a3b8' }}>
            <ShieldAlert size={24} />
          </div>
          <div>
            <div style={{ fontSize: '12px', color: '#94a3b8', fontWeight: 500 }}>Занулено / Снято с продажи</div>
            <div style={{ fontSize: '22px', fontWeight: 700, color: zeroStockGroupsCount > 0 ? '#f87171' : '#f8fafc', marginTop: '2px' }}>{zeroStockGroupsCount}</div>
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
        <div style={{ padding: '20px 24px', borderBottom: '1px solid #334155', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
          <div style={{ position: 'relative', width: '320px', maxWidth: '100%' }}>
            <Search size={18} style={{ position: 'absolute', left: '14px', top: '50%', transform: 'translateY(-50%)', color: '#64748b' }} />
            <input
              type="text"
              placeholder="Поиск по названию, ID или SKU..."
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              style={{
                width: '100%',
                padding: '10px 14px 10px 42px',
                borderRadius: '10px',
                background: '#0f172a',
                border: '1px solid #334155',
                color: '#f8fafc',
                fontSize: '14px',
                outline: 'none',
                boxSizing: 'border-box'
              }}
            />
          </div>

          <div style={{ fontSize: '13px', color: '#94a3b8' }}>
            Отображено групп: <strong style={{ color: '#f8fafc' }}>{filteredGroups.length}</strong> из {groups.length}
          </div>
        </div>

        {/* Table */}
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '14px' }}>
            <thead>
              <tr style={{ background: '#0f172a', borderBottom: '1px solid #334155', color: '#94a3b8', fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
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
                  <td colSpan={6} style={{ padding: '48px 24px', textAlign: 'center', color: '#64748b' }}>
                    <Layers size={40} style={{ marginBottom: '12px', opacity: 0.5 }} />
                    <div style={{ fontSize: '16px', fontWeight: 600, color: '#94a3b8' }}>
                      {searchQuery ? 'Мастер-Группы не найдены по запросу' : 'Мастер-Группы пока не созданы'}
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
                        <div style={{ fontWeight: 600, color: '#f8fafc', fontSize: '15px' }}>
                          {group.group_name}
                        </div>
                        <div style={{ fontSize: '11px', color: '#64748b', fontFamily: 'monospace', marginTop: '3px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <span>ID: {group.group_id}</span>
                          {group.external_code && (
                            <span style={{ color: '#38bdf8', background: 'rgba(56, 189, 248, 0.12)', padding: '1px 6px', borderRadius: '4px', border: '1px solid rgba(56, 189, 248, 0.3)' }}>
                              1C: {group.external_code}
                            </span>
                          )}
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
                                background: '#0f172a',
                                border: '1px solid #6366f1',
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
                                background: '#10b981',
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
                                background: '#334155',
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
                            <span style={{
                              fontSize: '18px',
                              fontWeight: 700,
                              color: group.shared_stock === 0 ? '#ef4444' : '#38bdf8',
                              padding: '4px 12px',
                              borderRadius: '8px',
                              background: group.shared_stock === 0 ? 'rgba(239, 68, 68, 0.12)' : 'rgba(56, 189, 248, 0.12)',
                              border: group.shared_stock === 0 ? '1px solid rgba(239, 68, 68, 0.3)' : '1px solid rgba(56, 189, 248, 0.3)'
                            }}>
                              {group.shared_stock} шт
                            </span>
                            <button
                              onClick={() => handleStartInlineEdit(group)}
                              title="Быстрое изменение остатка"
                              style={{
                                background: 'none',
                                border: 'none',
                                color: '#94a3b8',
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

                      {/* Linked SKU chips */}
                      <td style={{ padding: '18px 24px', maxWidth: '320px' }}>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                          {group.linked_skus.map(sku => {
                            const pMatch = products.find(p => p.sku === sku || p.merchantSku === sku);
                            const mSku = pMatch?.merchantSku;
                            const hasDistinctMSku = mSku && mSku !== sku;
                            return (
                              <span
                                key={sku}
                                title={pMatch ? `${pMatch.name}${hasDistinctMSku ? ` (Merchant SKU: ${mSku})` : ''}` : sku}
                                style={{
                                  padding: '4px 10px',
                                  borderRadius: '20px',
                                  background: '#1e293b',
                                  border: '1px solid #334155',
                                  color: '#cbd5e1',
                                  fontSize: '12px',
                                  fontWeight: 500,
                                  fontFamily: 'monospace',
                                  display: 'inline-flex',
                                  alignItems: 'center',
                                  gap: '4px'
                                }}
                              >
                                <Package size={12} color="#818cf8" />
                                {hasDistinctMSku ? `${sku} (${mSku})` : sku}
                              </span>
                            );
                          })}

                        </div>
                      </td>

                      {/* Sync Status Badge */}
                      <td style={{ padding: '18px 24px' }}>
                        {group.status === 'updating' ? (
                          <div style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', padding: '6px 12px', borderRadius: '20px', background: 'rgba(245, 158, 11, 0.15)', border: '1px solid rgba(245, 158, 11, 0.3)', color: '#fbbf24', fontSize: '13px', fontWeight: 600 }}>
                            <RefreshCw size={14} className="spin" />
                            <span>В процессе обновления...</span>
                          </div>
                        ) : group.status === 'synced' ? (
                          <div style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', padding: '6px 12px', borderRadius: '20px', background: 'rgba(16, 185, 129, 0.15)', border: '1px solid rgba(16, 185, 129, 0.3)', color: '#34d399', fontSize: '13px', fontWeight: 600 }}>
                            <CheckCircle2 size={15} />
                            <span>Синхронизировано</span>
                          </div>
                        ) : (
                          <div style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', padding: '6px 12px', borderRadius: '20px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239, 68, 68, 0.3)', color: '#f87171', fontSize: '13px', fontWeight: 600 }}>
                            <AlertTriangle size={15} />
                            <span>Ошибка авторизации</span>
                          </div>
                        )}
                      </td>

                      {/* Last Sync Log */}
                      <td style={{ padding: '18px 24px', fontSize: '13px', color: '#94a3b8' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <Clock size={14} color="#64748b" />
                          <span>{group.last_synced_at || 'Еще не запускалась'}</span>
                        </div>
                        {group.last_sync_log && (
                          <div style={{ fontSize: '11px', color: '#64748b', marginTop: '3px', textOverflow: 'ellipsis', overflow: 'hidden', whiteSpace: 'nowrap', maxWidth: '180px' }}>
                            {group.last_sync_log}
                          </div>
                        )}
                      </td>

                      {/* Actions */}
                      <td style={{ padding: '18px 24px', textAlign: 'right' }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: '8px' }}>
                          <button
                            onClick={() => handleManualSync(group.group_id)}
                            disabled={syncingGroupId === group.group_id}
                            title="Принудительная отправка остатков на Kaspi"
                            style={{
                              padding: '8px',
                              borderRadius: '8px',
                              background: '#1e293b',
                              border: '1px solid #334155',
                              color: '#6366f1',
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
                              background: '#1e293b',
                              border: '1px solid #334155',
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
                              color: '#f87171',
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
            background: '#1e293b',
            border: '1px solid #334155',
            borderRadius: '20px',
            boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)',
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden'
          }}>
            
            {/* Modal Header */}
            <div style={{ padding: '24px 28px', borderBottom: '1px solid #334155', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <h2 style={{ margin: 0, fontSize: '20px', fontWeight: 700, color: '#f8fafc' }}>
                  {editingGroupId ? 'Редактирование Мастер-Группы' : 'Создание новой Мастер-Группы'}
                </h2>
                <p style={{ margin: '4px 0 0 0', fontSize: '13px', color: '#94a3b8' }}>
                  Укажите название, доступный складской остаток и свяжите SKU товаров Kaspi
                </p>
              </div>
              <button
                onClick={() => setIsModalOpen(false)}
                style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer', padding: '6px', borderRadius: '8px' }}
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
                    background: '#0f172a',
                    border: '1px solid #334155',
                    color: '#ffffff',
                    fontSize: '14px',
                    outline: 'none',
                    boxSizing: 'border-box'
                  }}
                />
              </div>

              {/* Shared Stock Input */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '16px' }}>
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
                      background: '#0f172a',
                      border: '1px solid #334155',
                      color: '#f8fafc',
                      fontSize: '14px',
                      boxSizing: 'border-box'
                    }}
                  />
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
                      background: '#0f172a',
                      border: '1px solid #334155',
                      color: '#38bdf8',
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
                  <label style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer', background: '#0f172a', padding: '10px 16px', borderRadius: '10px', border: '1px solid #334155' }}>
                    <input
                      type="checkbox"
                      checked={formIsActive}
                      onChange={e => setFormIsActive(e.target.checked)}
                      style={{ width: '18px', height: '18px', accentColor: '#6366f1', cursor: 'pointer' }}
                    />
                    <span style={{ fontSize: '14px', fontWeight: 500, color: formIsActive ? '#34d399' : '#94a3b8' }}>
                      {formIsActive ? 'Активна (Синхронизировать)' : 'Пауза'}
                    </span>
                  </label>
                </div>
              </div>

              {/* Multi-Select SKU Section */}
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                  <label style={{ fontSize: '13px', fontWeight: 600, color: '#cbd5e1' }}>
                    Привязать товары Kaspi (Multi-Select SKU) *
                  </label>
                  <span style={{ fontSize: '12px', color: '#6366f1', fontWeight: 600 }}>
                    Выбрано: {formSelectedSkus.length} SKU
                  </span>
                </div>

                {/* Modal SKU Search */}
                <div style={{ position: 'relative', marginBottom: '10px' }}>
                  <Search size={16} style={{ position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)', color: '#64748b' }} />
                  <input
                    type="text"
                    placeholder="Поиск товара в вашем каталоге по имени или SKU..."
                    value={modalProductSearch}
                    onChange={e => setModalProductSearch(e.target.value)}
                    style={{
                      width: '100%',
                      padding: '8px 12px 8px 36px',
                      borderRadius: '8px',
                      background: '#0f172a',
                      border: '1px solid #334155',
                      color: '#f8fafc',
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
                  background: '#0f172a',
                  border: '1px solid #334155',
                  borderRadius: '10px',
                  padding: '8px'
                }}>
                  {modalFilteredProducts.length === 0 ? (
                    <div style={{ padding: '24px', textAlign: 'center', color: '#64748b', fontSize: '13px' }}>
                      Товары не найдены в кэше ассортимента
                    </div>
                  ) : (
                    modalFilteredProducts.map(p => {
                      const skuVal = p.sku || p.merchantSku || '';
                      const isSelected = formSelectedSkus.includes(skuVal);

                      return (
                        <div
                          key={skuVal}
                          onClick={() => handleToggleSkuSelection(skuVal)}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            padding: '10px 12px',
                            borderRadius: '8px',
                            cursor: 'pointer',
                            background: isSelected ? 'rgba(99, 102, 241, 0.15)' : 'transparent',
                            border: isSelected ? '1px solid rgba(99, 102, 241, 0.4)' : '1px solid transparent',
                            marginBottom: '4px',
                            transition: 'all 0.15s'
                          }}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => {}} // handled by parent div onClick
                              style={{ width: '16px', height: '16px', accentColor: '#6366f1' }}
                            />
                            <div>
                              <div style={{ fontSize: '13px', fontWeight: 600, color: '#f8fafc' }}>
                                {p.name}
                              </div>
                              <div style={{ fontSize: '11px', color: '#94a3b8', fontFamily: 'monospace', marginTop: '2px' }}>
                                Kaspi SKU: {skuVal}
                                {p.merchantSku && p.merchantSku !== skuVal && (
                                  <span style={{ color: '#818cf8', marginLeft: '8px' }}>
                                    • Merchant SKU: {p.merchantSku}
                                  </span>
                                )}
                                {p.brand ? ` • Бренд: ${p.brand}` : ''}
                              </div>

                            </div>
                          </div>

                          <div style={{ textAlign: 'right', fontSize: '12px' }}>
                            <div style={{ fontWeight: 600, color: '#cbd5e1' }}>{p.price || (p.price_num ? `${p.price_num} ₸` : 'N/A')}</div>
                            <div style={{ fontSize: '11px', color: '#94a3b8' }}>Остаток: {p.stock ?? 10} шт</div>
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
                    background: '#334155',
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
                    background: 'linear-gradient(135deg, #4f46e5 0%, #6366f1 100%)',
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

    </div>
  );
};
export default MasterStockGroups;
