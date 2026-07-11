import React, { useState, useEffect } from 'react';

interface Product {
  id: string;
  name: string;
  sku: string;
  price: string;
  status: string;
  url: string;
}

interface OrderEntry {
  entryId: string;
  productName: string;
  sku: string;
  quantity: number;
  basePrice: number;
  totalPrice: number;
}

interface Order {
  id: string;
  internalId: string;
  deliveryType: string;
  createdAt: string;
  price: string;
  productName: string;
  quantity: number;
  status: string;
  state: string;
  entries: OrderEntry[];
  customer: { name: string; phone: string };
  address: string;
}

interface Transfer {
  id: string;
  deliveryType: string;
  deadline: string;
  address: string;
}

export default function App() {
  const [isLoaded, setIsLoaded] = useState(false);
  const [view, setView] = useState<'login' | 'dashboard'>('login');
  const [isAuthed, setIsAuthed] = useState(false);

  const [products, setProducts] = useState<Product[]>([]);
  const [fetchingProducts, setFetchingProducts] = useState(false);

  // Search, filter and pagination states
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<'active' | 'archive'>('active');
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [totalPages, setTotalPages] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [pageInputText, setPageInputText] = useState('1');

  // Dropdown menu state
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);

  // Tabs navigation, packaging and transfer states
  const [activeTab, setActiveTab] = useState<'products' | 'packaging' | 'transfer'>('products');
  const [orders, setOrders] = useState<Order[]>([]);
  const [fetchingOrders, setFetchingOrders] = useState(false);
  const [orderStatusFilter, setOrderStatusFilter] = useState<'NEW' | 'ASSEMBLY'>('ASSEMBLY');
  const [orderLimit, setOrderLimit] = useState(10);
  const [selectedOrderIds, setSelectedOrderIds] = useState<Set<string>>(new Set());
  const [packingAll, setPackingAll] = useState(false);

  const [transfers, setTransfers] = useState<Transfer[]>([]);
  const [fetchingTransfers, setFetchingTransfers] = useState(false);
  const [transferFilter, setTransferFilter] = useState<'ALL' | 'EXPRESS' | 'TODAY'>('ALL');

  const [logs, setLogs] = useState<string[]>([
    'Система автоматизации инициализирована.',
    'Ожидание подключения Kaspi API...',
  ]);

  // Token-first auth
  const [tokenInput, setTokenInput] = useState('');
  const [loginStep, setLoginStep] = useState<'token' | 'loading' | 'success'>('token');
  const [errorMsg, setErrorMsg] = useState('');
  const [loadingMsg, setLoadingMsg] = useState('');

  /* ── LEGACY: Phone-based auth (disabled, kept for future restoration) ──
  const [phoneInput, setPhoneInput] = useState('+7');
  const [smsInput, setSmsInput] = useState('');
  const [loginStep, setLoginStep] = useState<'phone' | 'sms' | 'success' | 'loading'>('phone');
  */

  const API_URL = 'http://localhost:5000';

  const addLog = (message: string) => {
    const timestamp = new Date().toLocaleTimeString();
    setLogs((prev) => [`[${timestamp}] ${message}`, ...prev.slice(0, 49)]);
  };

  // Check initial session on mount
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${API_URL}/api/verify-session`, { method: 'POST' });
        const data = await res.json();
        if (data.valid) {
          setIsAuthed(true);
          setView('dashboard');
          addLog('Сессия активна, токен подтвержден.');
          loadProducts(1, 'active', '', 10);
        }
      } catch {
        // Backend not available
      }
      setIsLoaded(true);
    })();
  }, []);

  // ── Data loaders (no phone parameter) ──────────────────

  const loadProducts = async (
    pageNum: number = currentPage,
    status: 'active' | 'archive' = statusFilter,
    search: string = searchQuery,
    pSize: number = pageSize
  ) => {
    setFetchingProducts(true);
    addLog(`Загрузка товаров (Стр: ${pageNum}, Лимит: ${pSize}, Статус: ${status})...`);
    try {
      const res = await fetch(
        `${API_URL}/api/products?page=${pageNum}&status=${status}&search=${encodeURIComponent(search)}&page_size=${pSize}`
      );
      const data = await res.json();
      if (res.ok && data.success) {
        setProducts(data.products);
        setTotalPages(data.total_pages || 1);
        setTotalCount(data.total_count || 0);
        addLog(`Загружено ${data.products.length} товаров (Всего: ${data.total_count}).`);
      } else {
        addLog(`Ошибка загрузки товаров: ${data.detail || 'Ошибка бэкенда'}`);
      }
    } catch {
      addLog('Ошибка сети при загрузке товаров.');
    } finally {
      setFetchingProducts(false);
    }
  };

  const refreshProducts = async () => {
    setFetchingProducts(true);
    addLog('Принудительное обновление кэша товаров из Kaspi API...');
    try {
      const res = await fetch(`${API_URL}/api/products/refresh`, { method: 'POST' });
      const data = await res.json();
      if (res.ok && data.success) {
        addLog(`Кэш обновлен: ${data.count} товаров.`);
        loadProducts(1, statusFilter, searchQuery, pageSize);
      } else {
        addLog(`Ошибка обновления: ${data.detail || 'Ошибка бэкенда'}`);
      }
    } catch {
      addLog('Ошибка сети при обновлении кэша товаров.');
    } finally {
      setFetchingProducts(false);
    }
  };

  const getOrderParams = (statusFilter: 'NEW' | 'ASSEMBLY') => {
    if (statusFilter === 'NEW') {
      return { state: 'KASPI_DELIVERY', status: 'APPROVED_BY_BANK', assembled: undefined };
    }
    return { state: 'KASPI_DELIVERY', status: 'ACCEPTED_BY_MERCHANT', assembled: false };
  };

  const loadOrders = async (statusF = orderStatusFilter, limit = orderLimit) => {
    setFetchingOrders(true);
    setSelectedOrderIds(new Set());
    const { state, status, assembled } = getOrderParams(statusF);
    addLog(`Загрузка заказов (${statusF === 'NEW' ? 'Новые' : 'На сборке'}, Лимит: ${limit})...`);
    try {
      let url = `${API_URL}/api/orders?state=${state}&status=${status}&limit=${limit}`;
      if (assembled !== undefined) url += `&assembled=${assembled}`;
      const res = await fetch(url);
      const data = await res.json();
      if (res.ok && data.success) {
        setOrders(data.orders);
        const total = data.orders.length;
        const assembledCount = data.orders.filter(o => o.assembled).length;
        const transmittedCount = data.orders.filter(o => o.courierTransmissionDate !== null).length;
        const types = Array.from(new Set(data.orders.map(o => o.deliveryType))).join(', ');
        addLog(`Загружено ${total} заказов. Из них собранных (assembled): ${assembledCount}, переданных курьеру: ${transmittedCount}. Типы доставки: [${types}]`);
      } else {
        addLog(`Ошибка загрузки заказов: ${data.detail || 'Ошибка бэкенда'}`);
      }
    } catch (err: any) {
      addLog(`Ошибка сети при загрузке заказов: ${err.message || err}`);
    } finally {
      setFetchingOrders(false);
    }
  };

  const packOrder = async (orderId: string, internalId: string) => {
    addLog(`Сборка заказа #${orderId}...`);
    try {
      const res = await fetch(`${API_URL}/api/orders/pack`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ order_id: internalId, new_status: 'ACCEPTED_BY_MERCHANT' }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        addLog(`Заказ #${orderId} успешно обработан.`);
        loadOrders(orderStatusFilter, orderLimit);
      } else {
        addLog(`Ошибка сборки #${orderId}: ${data.detail || 'Ошибка'}`);
      }
    } catch {
      addLog(`Ошибка сети при сборке заказа #${orderId}.`);
    }
  };

  const packAllOrders = async () => {
    setPackingAll(true);
    addLog('Массовая сборка всех заказов...');
    try {
      const res = await fetch(`${API_URL}/api/orders/pack-all`, { method: 'POST' });
      const data = await res.json();
      if (res.ok && data.success) {
        addLog(data.message);
        setSelectedOrderIds(new Set());
        loadOrders(orderStatusFilter, orderLimit);
      } else {
        addLog(`Ошибка массовой сборки: ${data.detail || 'Ошибка'}`);
      }
    } catch {
      addLog('Ошибка сети при массовой сборке.');
    } finally {
      setPackingAll(false);
    }
  };

  const toggleOrderSelection = (orderId: string) => {
    setSelectedOrderIds(prev => {
      const next = new Set(prev);
      if (next.has(orderId)) next.delete(orderId);
      else next.add(orderId);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedOrderIds.size === orders.length) {
      setSelectedOrderIds(new Set());
    } else {
      setSelectedOrderIds(new Set(orders.map(o => o.id)));
    }
  };

  const loadTransfers = async (filter = transferFilter) => {
    setFetchingTransfers(true);
    addLog(`Загрузка передач (Фильтр: ${filter === 'ALL' ? 'Все' : filter === 'EXPRESS' ? 'Экспресс' : 'Сегодня'})...`);
    try {
      const res = await fetch(`${API_URL}/api/transfers?filter=${filter}`);
      const data = await res.json();
      if (res.ok && data.success) {
        setTransfers(data.transfers);
        addLog(`Загружено ${data.transfers.length} передач.`);
      } else {
        addLog(`Ошибка загрузки передач: ${data.detail || 'Ошибка бэкенда'}`);
      }
    } catch {
      addLog('Ошибка сети при загрузке передач.');
    } finally {
      setFetchingTransfers(false);
    }
  };

  // ── Auth handler ───────────────────────────────────────

  const handleAuth = async (e: React.FormEvent) => {
    e.preventDefault();
    const token = tokenInput.trim();
    if (!token) {
      setErrorMsg('Токен не может быть пустым.');
      return;
    }

    setErrorMsg('');
    setLoginStep('loading');
    setLoadingMsg('Верификация API токена...');
    addLog('Проверка API токена...');

    try {
      const res = await fetch(`${API_URL}/api/auth`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      });
      const data = await res.json();

      if (res.ok && data.success) {
        setLoginStep('success');
        setIsAuthed(true);
        addLog(data.verified ? 'Токен верифицирован через Kaspi API.' : `Токен сохранен (оффлайн-режим).`);
      } else {
        setLoginStep('token');
        setErrorMsg(data.detail || 'Неверный токен API.');
      }
    } catch {
      setLoginStep('token');
      setErrorMsg('Ошибка связи с бэкендом.');
    }
  };

  const handleLogout = async () => {
    try {
      await fetch(`${API_URL}/api/logout`, { method: 'POST' });
    } catch { /* ignore */ }
    setIsAuthed(false);
    setView('login');
    setLoginStep('token');
    setTokenInput('');
    setProducts([]);
    setOrders([]);
    setTransfers([]);
    addLog('Сессия завершена.');
  };

  // ── UI helpers ─────────────────────────────────────────

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setCurrentPage(1);
    setPageInputText('1');
    loadProducts(1, statusFilter, searchQuery, pageSize);
  };

  const handleStatusFilterChange = (status: 'active' | 'archive') => {
    setStatusFilter(status);
    setCurrentPage(1);
    setPageInputText('1');
    loadProducts(1, status, searchQuery, pageSize);
  };

  const handlePageChange = (page: number) => {
    if (page < 1 || page > totalPages) return;
    setCurrentPage(page);
    setPageInputText(String(page));
    loadProducts(page, statusFilter, searchQuery, pageSize);
  };

  const [pkgDeliveryFilter, setPkgDeliveryFilter] = useState<'ALL' | 'EXPRESS' | 'PICKUP' | 'STANDARD'>('ALL');

  const filteredOrders = orders.filter(order => {
    if (pkgDeliveryFilter === 'ALL') return true;
    if (pkgDeliveryFilter === 'EXPRESS') return order.deliveryType === 'Express';
    if (pkgDeliveryFilter === 'PICKUP') return order.deliveryType === 'Самовывоз';
    if (pkgDeliveryFilter === 'STANDARD') return order.deliveryType === 'Kaspi Доставка' || order.deliveryType === 'Postomat';
    return true;
  });

  // Loading Screen
  if (!isLoaded) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh' }}>
        <div style={{ width: '40px', height: '40px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite' }} />
      </div>
    );
  }

  // ── Token Login Screen ─────────────────────────────────
  if (view === 'login') {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh', padding: '20px' }}>
        <div className="glass-card" style={{ maxWidth: '480px', width: '100%', padding: '40px', position: 'relative' }}>
          
          {/* Header */}
          <div style={{ textAlign: 'center', marginBottom: '30px' }}>
            <div style={{ 
              width: '64px', height: '64px', 
              background: 'linear-gradient(135deg, var(--primary), #e5394b)', 
              borderRadius: '18px', display: 'inline-flex', 
              justifyContent: 'center', alignItems: 'center', 
              fontWeight: 700, fontSize: '28px', color: '#fff', 
              boxShadow: '0 8px 16px var(--primary-glow)', marginBottom: '16px' 
            }}>K</div>
            <h1 style={{ fontSize: '24px', fontWeight: 700 }}>Kaspi Merchant API</h1>
            <p style={{ fontSize: '14px', color: 'var(--text-muted)', marginTop: '6px' }}>
              {loginStep === 'token' && 'Введите ваш API-токен продавца для подключения'}
              {loginStep === 'loading' && 'Пожалуйста, подождите'}
              {loginStep === 'success' && 'Авторизация выполнена'}
            </p>
          </div>

          {errorMsg && (
            <div style={{ padding: '14px 16px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239, 68, 68, 0.3)', color: '#fca5a5', borderRadius: '12px', marginBottom: '20px', fontSize: '14px' }}>
              {errorMsg}
            </div>
          )}

          {loginStep === 'token' && (
            <form onSubmit={handleAuth}>
              <div style={{ marginBottom: '24px' }}>
                <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--text-muted)', marginBottom: '8px' }}>API Токен (X-Auth-Token)</label>
                <input 
                  type="text" 
                  className="form-input" 
                  placeholder="Вставьте ваш токен из кабинета Kaspi" 
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  required
                  autoFocus
                  style={{ fontFamily: 'ui-monospace, Consolas, monospace', fontSize: '13px' }}
                />
                <p style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '8px' }}>
                  Токен можно найти в кабинете продавца Kaspi → Настройки → API
                </p>
              </div>
              <button type="submit" className="btn btn-primary" style={{ width: '100%', padding: '16px' }}>
                <span>Подключить по API</span>
              </button>
            </form>
          )}

          {loginStep === 'loading' && (
            <div style={{ textAlign: 'center', padding: '40px 0' }}>
              <div style={{ width: '40px', height: '40px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 20px auto' }} />
              <p style={{ color: 'var(--text-muted)' }}>{loadingMsg}</p>
            </div>
          )}

          {loginStep === 'success' && (
            <div style={{ textAlign: 'center', padding: '20px 0' }}>
              <div style={{ 
                width: '80px', height: '80px', 
                background: 'rgba(16, 185, 129, 0.1)', 
                border: '2px solid var(--success)', 
                borderRadius: '50%', display: 'inline-flex', 
                justifyContent: 'center', alignItems: 'center', 
                color: 'var(--success)', fontSize: '40px',
                boxShadow: '0 0 20px var(--success-glow)',
                margin: '0 auto 20px auto'
              }}>✓</div>
              <h2 style={{ fontSize: '22px', fontWeight: 600, marginBottom: '10px' }}>Успешный вход!</h2>
              <p style={{ fontSize: '14px', color: 'var(--text-muted)', marginBottom: '24px' }}>
                API токен подтверждён и сохранён.
              </p>
              <button className="btn btn-primary" style={{ width: '100%', padding: '16px' }} onClick={() => { setView('dashboard'); loadProducts(1, 'active', '', 10); }}>
                Перейти в панель управления
              </button>
            </div>
          )}

        </div>
      </div>
    );
  }

  // ── Dashboard Screen ───────────────────────────────────
  return (
    <div className="dashboard-container">
      {/* Header */}
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '32px' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ fontSize: '32px' }}>🦊</span>
            <h1>Kaspi SaaS Wrapper</h1>
          </div>
          <p style={{ marginTop: '4px' }}>Панель управления заказами и товарами через Kaspi API</p>
        </div>
        <div style={{ display: 'flex', gap: '12px' }}>
          <button 
            className="btn btn-secondary" 
            style={{ padding: '10px 16px', color: 'var(--error)' }}
            onClick={handleLogout}
          >
            🚪 Выйти
          </button>
        </div>
      </header>

      <div className="grid-2">
        {/* Left Side: Status & Logs */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          <div className="glass-card">
            <h2 style={{ marginBottom: '16px' }}>Статус подключения</h2>
            <div style={{ 
              padding: '16px', 
              background: 'rgba(255, 255, 255, 0.03)', 
              borderRadius: '12px',
              border: '1px solid rgba(255, 255, 255, 0.05)',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center'
            }}>
              <div>
                <h3 style={{ fontSize: '15px' }}>Kaspi Merchant API</h3>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '6px' }}>
                  <span className="badge badge-success">Сессия активна</span>
                </div>
              </div>
              <button className="btn btn-secondary" style={{ padding: '8px 12px' }} onClick={() => {
                addLog('Проверка состояния сессии...');
                fetch(`${API_URL}/api/verify-session`, { method: 'POST' })
                  .then(r => r.json())
                  .then(d => addLog(d.valid ? 'Сессия активна ✓' : 'Сессия невалидна ✗'))
                  .catch(() => addLog('Ошибка проверки сессии'));
              }}>
                🔄
              </button>
            </div>
          </div>

          {/* Logs */}
          <div className="glass-card" style={{ flexGrow: 1 }}>
            <h2 style={{ marginBottom: '12px' }}>Журнал событий</h2>
            <div 
              style={{ 
                background: 'rgba(0, 0, 0, 0.4)', 
                border: '1px solid var(--card-border)', 
                borderRadius: '8px',
                padding: '16px',
                fontFamily: 'ui-monospace, Consolas, monospace',
                fontSize: '13px',
                height: '220px',
                overflowY: 'auto',
                display: 'flex',
                flexDirection: 'column',
                gap: '6px'
              }}
            >
              {logs.map((log, index) => (
                <div key={index} style={{ color: log.includes('Успеш') || log.includes('активна') || log.includes('✓') ? 'var(--success)' : log.includes('ошибка') || log.includes('Ошибка') || log.includes('✗') ? 'var(--error)' : 'var(--text-muted)' }}>
                  {log}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Right Side: Control Tabs & Panels */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          <div className="glass-card" style={{ flexGrow: 1, display: 'flex', flexDirection: 'column' }}>
            
            {/* Tabs Header */}
            <div style={{ display: 'flex', borderBottom: '1px solid rgba(255,255,255,0.06)', marginBottom: '20px', gap: '8px' }}>
              <button 
                className="btn" 
                style={{ 
                  background: activeTab === 'products' ? 'var(--primary)' : 'rgba(255,255,255,0.02)',
                  border: '1px solid rgba(255,255,255,0.05)', color: '#fff', padding: '12px 18px', fontSize: '13px',
                  borderBottomLeftRadius: 0, borderBottomRightRadius: 0, borderTopLeftRadius: '10px', borderTopRightRadius: '10px',
                  fontWeight: 600, cursor: 'pointer', transition: 'all 0.3s ease'
                }}
                onClick={() => {
                  setActiveTab('products');
                  loadProducts(1, statusFilter, searchQuery, pageSize);
                }}
              >
                🏷️ Товары
              </button>
              <button 
                className="btn" 
                style={{ 
                  background: activeTab === 'packaging' ? 'var(--primary)' : 'rgba(255,255,255,0.02)',
                  border: '1px solid rgba(255,255,255,0.05)', color: '#fff', padding: '12px 18px', fontSize: '13px',
                  borderBottomLeftRadius: 0, borderBottomRightRadius: 0, borderTopLeftRadius: '10px', borderTopRightRadius: '10px',
                  fontWeight: 600, cursor: 'pointer', transition: 'all 0.3s ease'
                }}
                onClick={() => {
                  setActiveTab('packaging');
                  loadOrders(orderStatusFilter, orderLimit);
                }}
              >
                📦 Упаковка
              </button>
            </div>
            {/* TAB 1: PRODUCT MANAGEMENT */}
            {activeTab === 'products' && (
              <>
                {/* Header with Search, Filter & Page Size */}
                <div style={{ borderBottom: '1px solid rgba(255,255,255,0.06)', paddingBottom: '16px', marginBottom: '20px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                    <h2 style={{ fontSize: '16px' }}>
                      🏷️ Управление товарами ({totalCount ? `${totalCount} поз.` : '0 поз.'})
                    </h2>
                    
                    <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
                      {/* Page Size Selector */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Показывать по:</span>
                        <select 
                          value={pageSize} 
                          onChange={(e) => {
                            const pSize = parseInt(e.target.value);
                            setPageSize(pSize);
                            setCurrentPage(1);
                            setPageInputText('1');
                            loadProducts(1, statusFilter, searchQuery, pSize);
                          }}
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
                          onClick={() => handleStatusFilterChange('active')}
                        >
                          В продаже
                        </button>
                        <button 
                          className="btn" 
                          style={{ 
                            background: statusFilter === 'archive' ? 'var(--primary)' : 'transparent', 
                            padding: '5px 12px', fontSize: '11px', borderRadius: '6px'
                          }}
                          onClick={() => handleStatusFilterChange('archive')}
                        >
                          Сняты
                        </button>
                      </div>
                    </div>
                  </div>

                  {/* Search Bar */}
                  <form onSubmit={handleSearchSubmit} style={{ display: 'flex', gap: '12px' }}>
                    <input 
                      type="text" 
                      className="form-input" 
                      placeholder="Поиск по артикулу / названию..."
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      style={{ flexGrow: 1 }}
                    />
                    <button type="submit" className="btn btn-primary" style={{ padding: '10px 16px' }}>
                      Найти
                    </button>
                    <button 
                      type="button" 
                      className="btn btn-secondary" 
                      style={{ padding: '10px 14px' }}
                      onClick={refreshProducts}
                      disabled={fetchingProducts}
                    >
                      {fetchingProducts ? '🔄' : '♻️ Обновить'}
                    </button>
                  </form>
                </div>

                {/* Products List */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', flexGrow: 1 }}>
                  {fetchingProducts && products.length === 0 ? (
                    <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
                      <div style={{ width: '32px', height: '32px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 16px auto' }} />
                      <p>Загрузка товаров через Kaspi API...</p>
                    </div>
                  ) : products.length === 0 && !fetchingProducts ? (
                    <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
                      <p>Товары не найдены. Нажмите «♻️ Обновить» для загрузки из Kaspi API.</p>
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
                      {products.map((product) => (
                        <div 
                          key={product.id} 
                          style={{ 
                            padding: '16px', 
                            background: 'rgba(255, 255, 255, 0.02)', 
                            borderRadius: '12px',
                            border: '1px solid rgba(255, 255, 255, 0.04)',
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            position: 'relative'
                          }}
                        >
                          <div style={{ maxWidth: '75%' }}>
                            <h3 style={{ fontSize: '14px', fontWeight: 600, color: '#fff' }}>{product.name}</h3>
                            <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>{product.sku}</p>
                            
                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginTop: '8px' }}>
                              <span 
                                className="badge" 
                                style={{ 
                                  background: product.status === 'Активен' ? 'rgba(16, 185, 129, 0.1)' : 'rgba(255,255,255,0.05)',
                                  color: product.status === 'Активен' ? 'var(--success)' : '#aaa',
                                  border: '1px solid rgba(255,255,255,0.08)'
                                }}
                              >
                                {product.status === 'Активен' ? 'В продаже' : 'Снят с продажи'}
                              </span>
                              
                              {product.url && (
                                <a 
                                  href={product.url} 
                                  target="_blank" 
                                  rel="noreferrer" 
                                  style={{ fontSize: '12px', color: '#3b82f6', textDecoration: 'none' }}
                                >
                                  🔗 Открыть на Kaspi.kz
                                </a>
                              )}
                            </div>
                          </div>

                          <div style={{ textAlign: 'right', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '8px' }}>
                            <div style={{ fontSize: '16px', fontWeight: 700, color: 'var(--primary)' }}>{product.price}</div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Pagination Controls */}
                <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '16px', borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: '16px', marginTop: '20px' }}>
                  <button 
                    className="btn btn-secondary" 
                    style={{ padding: '6px 12px', fontSize: '12px' }}
                    onClick={() => handlePageChange(currentPage - 1)}
                    disabled={currentPage === 1 || fetchingProducts}
                  >
                    ◀ Назад
                  </button>
                  
                  <span style={{ fontSize: '13px', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    Страница 
                    <input 
                      type="text" 
                      value={pageInputText}
                      onChange={(e) => setPageInputText(e.target.value.replace(/\D/g, ''))}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          const pageNum = parseInt(pageInputText);
                          if (pageNum >= 1 && pageNum <= totalPages) {
                            handlePageChange(pageNum);
                          } else {
                            setPageInputText(String(currentPage));
                          }
                        }
                      }}
                      onBlur={() => {
                        const pageNum = parseInt(pageInputText);
                        if (pageNum >= 1 && pageNum <= totalPages) {
                          handlePageChange(pageNum);
                        } else {
                          setPageInputText(String(currentPage));
                        }
                      }}
                      className="form-input"
                      style={{ width: '45px', padding: '4px 8px', textAlign: 'center', display: 'inline-block' }}
                    />
                    из <strong>{totalPages}</strong>
                  </span>

                  <button 
                    className="btn btn-secondary" 
                    style={{ padding: '6px 12px', fontSize: '12px' }}
                    onClick={() => handlePageChange(currentPage + 1)}
                    disabled={currentPage === totalPages || fetchingProducts}
                  >
                    Вперед ▶
                  </button>
                </div>
              </>
            )}

            {/* TAB 2: PACKAGING / ORDERS ASSEMBLY */}
            {activeTab === 'packaging' && (
              <>
                {/* Header with filters */}
                <div style={{ borderBottom: '1px solid rgba(255,255,255,0.06)', paddingBottom: '16px', marginBottom: '20px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                    <h2 style={{ fontSize: '16px' }}>
                      📦 Сборка и упаковка заказов ({orders.length})
                    </h2>
                    
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                      {/* Order limit selector */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Показывать:</span>
                        <select 
                          value={orderLimit} 
                          onChange={(e) => {
                            const newLimit = parseInt(e.target.value);
                            setOrderLimit(newLimit);
                            loadOrders(orderStatusFilter, newLimit);
                          }}
                          className="form-input"
                          style={{ width: '75px', padding: '6px 10px', background: 'var(--input-bg)', border: '1px solid var(--input-border)', color: '#fff', borderRadius: '8px', cursor: 'pointer' }}
                        >
                          <option value="10">10</option>
                          <option value="20">20</option>
                          <option value="50">50</option>
                          <option value="100">100</option>
                        </select>
                      </div>

                      {/* Order status filter */}
                      <div style={{ display: 'flex', background: 'rgba(255,255,255,0.04)', padding: '3px', borderRadius: '8px' }}>
                        <button 
                          className="btn" 
                          style={{ 
                            background: orderStatusFilter === 'ASSEMBLY' ? 'var(--primary)' : 'transparent', 
                            padding: '5px 12px', fontSize: '11px', borderRadius: '6px'
                          }}
                          onClick={() => {
                            setOrderStatusFilter('ASSEMBLY');
                            loadOrders('ASSEMBLY', orderLimit);
                          }}
                        >
                          На сборке
                        </button>
                        <button 
                          className="btn" 
                          style={{ 
                            background: orderStatusFilter === 'NEW' ? 'var(--primary)' : 'transparent', 
                            padding: '5px 12px', fontSize: '11px', borderRadius: '6px'
                          }}
                          onClick={() => {
                            setOrderStatusFilter('NEW');
                            loadOrders('NEW', orderLimit);
                          }}
                        >
                          Новые
                        </button>
                      </div>

                      <button 
                        type="button" 
                        className="btn btn-secondary" 
                        style={{ padding: '8px 12px' }}
                        onClick={() => loadOrders(orderStatusFilter, orderLimit)}
                        disabled={fetchingOrders}
                      >
                        {fetchingOrders ? '🔄' : 'Refresh'}
                      </button>
                    </div>
                  </div>

                  {/* Delivery Type Sub-tabs */}
                  <div style={{ display: 'flex', gap: '8px', marginBottom: '16px', background: 'rgba(255,255,255,0.02)', padding: '4px', borderRadius: '8px', width: 'fit-content', border: '1px solid rgba(255,255,255,0.05)' }}>
                    <button 
                      className="btn" 
                      style={{ 
                        background: pkgDeliveryFilter === 'ALL' ? 'var(--primary)' : 'transparent', 
                        padding: '6px 14px', fontSize: '11px', borderRadius: '6px', cursor: 'pointer', transition: 'all 0.2s',
                        height: 'auto', border: 'none', fontWeight: 600, color: '#fff'
                      }}
                      onClick={() => {
                        setPkgDeliveryFilter('ALL');
                        setSelectedOrderIds(new Set());
                      }}
                    >
                      Все
                    </button>
                    <button 
                      className="btn" 
                      style={{ 
                        background: pkgDeliveryFilter === 'EXPRESS' ? 'var(--primary)' : 'transparent', 
                        padding: '6px 14px', fontSize: '11px', borderRadius: '6px', cursor: 'pointer', transition: 'all 0.2s',
                        height: 'auto', border: 'none', fontWeight: 600, color: '#fff'
                      }}
                      onClick={() => {
                        setPkgDeliveryFilter('EXPRESS');
                        setSelectedOrderIds(new Set());
                      }}
                    >
                      🏃‍♂️ Экспресс
                    </button>
                    <button 
                      className="btn" 
                      style={{ 
                        background: pkgDeliveryFilter === 'PICKUP' ? 'var(--primary)' : 'transparent', 
                        padding: '6px 14px', fontSize: '11px', borderRadius: '6px', cursor: 'pointer', transition: 'all 0.2s',
                        height: 'auto', border: 'none', fontWeight: 600, color: '#fff'
                      }}
                      onClick={() => {
                        setPkgDeliveryFilter('PICKUP');
                        setSelectedOrderIds(new Set());
                      }}
                    >
                      🏪 Самовывоз
                    </button>
                    <button 
                      className="btn" 
                      style={{ 
                        background: pkgDeliveryFilter === 'STANDARD' ? 'var(--primary)' : 'transparent', 
                        padding: '6px 14px', fontSize: '11px', borderRadius: '6px', cursor: 'pointer', transition: 'all 0.2s',
                        height: 'auto', border: 'none', fontWeight: 600, color: '#fff'
                      }}
                      onClick={() => {
                        setPkgDeliveryFilter('STANDARD');
                        setSelectedOrderIds(new Set());
                      }}
                    >
                      🚚 Доставка / Постамат
                    </button>
                  </div>

                  {/* Select All + Bulk Action Bar */}
                  {filteredOrders.length > 0 && orderStatusFilter === 'ASSEMBLY' && (
                    <div style={{ 
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      padding: '10px 14px', background: 'rgba(255,255,255,0.03)', borderRadius: '10px',
                      border: '1px solid rgba(255,255,255,0.06)'
                    }}>
                      <label style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer', fontSize: '13px' }}>
                        <input 
                          type="checkbox" 
                          checked={selectedOrderIds.size === filteredOrders.length && filteredOrders.length > 0}
                          onChange={toggleSelectAll}
                          style={{ width: '18px', height: '18px', accentColor: 'var(--primary)', cursor: 'pointer' }}
                        />
                        <span style={{ color: '#fff', fontWeight: 500 }}>
                          Выбрать все ({selectedOrderIds.size}/{filteredOrders.length})
                        </span>
                      </label>

                      <button 
                        className="btn btn-primary" 
                        style={{ 
                          padding: '8px 16px', fontSize: '12px', fontWeight: 600,
                          opacity: packingAll ? 0.7 : 1
                        }}
                        onClick={packAllOrders}
                        disabled={packingAll || filteredOrders.length === 0}
                      >
                        {packingAll ? '⏳ Формирование...' : '📋 Сформировать все накладные'}
                      </button>
                    </div>
                  )}
                </div>

                {/* Orders List */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', flexGrow: 1 }}>
                  {fetchingOrders ? (
                    <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
                      <div style={{ width: '32px', height: '32px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 16px auto' }} />
                      <p>Загрузка заказов через Kaspi API...</p>
                    </div>
                  ) : filteredOrders.length === 0 ? (
                    <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
                      <p>Заказов в данном статусе не найдено.</p>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                      {filteredOrders.map((order) => (
                        <div 
                          key={order.id} 
                          style={{ 
                            padding: '16px', 
                            background: selectedOrderIds.has(order.id) ? 'rgba(239, 68, 68, 0.06)' : 'rgba(255, 255, 255, 0.02)', 
                            borderRadius: '12px',
                            border: selectedOrderIds.has(order.id) ? '1px solid rgba(239, 68, 68, 0.2)' : '1px solid rgba(255, 255, 255, 0.04)',
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            transition: 'all 0.2s ease'
                          }}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                            {/* Checkbox */}
                            {orderStatusFilter === 'ASSEMBLY' && (
                              <input 
                                type="checkbox" 
                                checked={selectedOrderIds.has(order.id)}
                                onChange={() => toggleOrderSelection(order.id)}
                                style={{ width: '18px', height: '18px', accentColor: 'var(--primary)', cursor: 'pointer', flexShrink: 0 }}
                              />
                            )}
                            <div>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                <h3 style={{ fontSize: '15px', fontWeight: 700, color: '#fff' }}>Заказ #{order.id}</h3>
                                <span 
                                  className="badge" 
                                  style={{ 
                                    background: order.deliveryType === 'Express' ? 'linear-gradient(90deg, #c92c3c, #f43f5e)' : 'rgba(255,255,255,0.05)',
                                    color: '#fff', border: '1px solid rgba(255,255,255,0.08)'
                                  }}
                                >
                                  {order.deliveryType}
                                </span>
                                <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>📅 {order.createdAt}</span>
                              </div>
                              <div style={{ marginTop: '12px', display: 'flex', flexDirection: 'column', gap: '6px', background: 'rgba(0,0,0,0.2)', padding: '10px', borderRadius: '8px' }}>
                                {order.entries && order.entries.length > 0 ? (
                                  order.entries.map((entry, idx) => (
                                    <p key={idx} style={{ fontSize: '12px', color: '#e2e8f0', margin: 0 }}>
                                      <span style={{ color: 'var(--text-muted)' }}>└─ SKU: {entry.sku} |</span> {entry.productName} <span style={{ color: 'var(--primary)' }}>| Кол-во: {entry.quantity} шт.</span>
                                    </p>
                                  ))
                                ) : (
                                  <p style={{ fontSize: '13px', color: '#fff', margin: 0 }}>
                                    {order.productName} {order.quantity > 1 && `(x${order.quantity})`}
                                  </p>
                                )}
                              </div>
                              {order.customer?.name && (
                                <p style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>
                                  👤 {order.customer.name} {order.address && `• 📍 ${order.address}`}
                                </p>
                              )}
                            </div>
                          </div>

                          <div style={{ textAlign: 'right', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '8px' }}>
                            <div style={{ fontSize: '16px', fontWeight: 700, color: 'var(--success)' }}>{order.price}</div>
                            
                            {orderStatusFilter === 'ASSEMBLY' && (
                              <button 
                                className="btn btn-primary" 
                                style={{ padding: '6px 12px', fontSize: '12px' }}
                                onClick={() => packOrder(order.id, order.internalId)}
                              >
                                📦 Собрать заказ
                              </button>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            )}



          </div>
        </div>
      </div>
    </div>
  );
}
