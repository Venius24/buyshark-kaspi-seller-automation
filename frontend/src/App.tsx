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
  assembled?: boolean;
}

interface Transfer {
  id: string;
  deliveryType: string;
  deadline: string;
  address: string;
  waybillNumber?: string;
  waybillUrl?: string;
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
  const [activeTab, setActiveTab] = useState<'products' | 'dumping' | 'packaging' | 'transfer'>('products');
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

  const [phoneInput, setPhoneInput] = useState('+7');
  const [smsInput, setSmsInput] = useState('');
  const [mcAuthStep, setMcAuthStep] = useState<'idle' | 'phone' | 'sms' | 'loading' | 'success'>('idle');
  const [mcHasSession, setMcHasSession] = useState(false);
  const [mcAuthError, setMcAuthError] = useState('');

  // Dumping States
  const [dumpingConfigs, setDumpingConfigs] = useState<Record<string, { min_price: number; max_price: number; dump_step: number; enabled: boolean }>>({});
  const [expandedSkus, setExpandedSkus] = useState<Set<string>>(new Set());
  const [isRunningDumping, setIsRunningDumping] = useState(false);

  const API_URL = 'http://localhost:5000';

  const checkMcStatus = async (verify = false) => {
    try {
      const res = await fetch(`${API_URL}/api/mc-status?verify=${verify}`);
      const data = await res.json();
      if (res.ok && data.success) {
        setMcHasSession(data.has_session && data.valid);
        return data.has_session && data.valid;
      }
    } catch {
      // Ignore
    }
    return false;
  };

  const loadDumpingConfigs = async () => {
    try {
      const res = await fetch(`${API_URL}/api/dumping/configs`);
      const data = await res.json();
      if (res.ok && data.success) {
        setDumpingConfigs(data.configs || {});
      }
    } catch {
      // Ignore
    }
  };

  const saveDumpingConfig = async (sku: string, minPrice: number, maxPrice: number, step: number, enabled: boolean) => {
    addLog(`Сохранение настроек демпинга для ${sku}...`);
    try {
      const res = await fetch(`${API_URL}/api/dumping/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sku,
          min_price: minPrice,
          max_price: maxPrice,
          dump_step: step,
          enabled
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        addLog(`Настройки для ${sku} успешно сохранены.`);
        loadDumpingConfigs();
      } else {
        addLog(`Ошибка сохранения настроек: ${data.detail || 'Неизвестная ошибка'}`);
      }
    } catch {
      addLog('Сбой сети при сохранении настроек демпинга.');
    }
  };

  const runDumpingCycle = async () => {
    setIsRunningDumping(true);
    addLog('Запуск автоматического расчета демпинга цен...');
    try {
      const res = await fetch(`${API_URL}/api/dumping/run`, { method: 'POST' });
      const data = await res.json();
      if (res.ok && data.success) {
        addLog(data.message);
        if (data.logs && data.logs.length > 0) {
          data.logs.forEach((logMsg: string) => addLog(logMsg));
        } else {
          addLog('Цены всех товаров соответствуют оптимальным, изменений нет.');
        }
        loadProducts(currentPage, statusFilter, searchQuery, pageSize);
      } else {
        addLog(`Сбой демпинга: ${data.detail || 'Неизвестная ошибка'}`);
      }
    } catch {
      addLog('Сетевая ошибка при выполнении расчета демпинга.');
    } finally {
      setIsRunningDumping(false);
    }
  };

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
          addLog('Сессия REST API активна, токен подтвержден.');
          loadProducts(1, 'active', '', 10);
          checkMcStatus(false);
          loadDumpingConfigs();
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
        const assembledCount = data.orders.filter((o: any) => o.assembled).length;
        const transmittedCount = data.orders.filter((o: any) => o.courierTransmissionDate !== null).length;
        const types = Array.from(new Set(data.orders.map((o: any) => o.deliveryType))).join(', ');
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

  const packSelectedOrders = async () => {
    if (selectedOrderIds.size === 0) return;
    const idsToPack = Array.from(selectedOrderIds);
    setSelectedOrderIds(new Set());
    addLog(`Запуск сборки для ${idsToPack.length} выбранных заказов...`);
    for (const id of idsToPack) {
      const order = orders.find(o => o.id === id);
      if (order) {
        addLog(`Сборка заказа #${order.id}...`);
        try {
          const res = await fetch(`${API_URL}/api/orders/pack`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ order_id: order.internalId, new_status: 'ACCEPTED_BY_MERCHANT' }),
          });
          const data = await res.json();
          if (res.ok && data.success) {
            addLog(`Заказ #${order.id} успешно обработан.`);
          } else {
            addLog(`Ошибка сборки #${order.id}: ${data.detail || 'Ошибка'}`);
          }
        } catch {
          addLog(`Ошибка сети при сборке заказа #${order.id}.`);
        }
      }
    }
    loadOrders(orderStatusFilter, orderLimit);
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

  const [pkgDeliveryFilter, setPkgDeliveryFilter] = useState<'ALL' | 'EXPRESS' | 'POSTOMAT' | 'STANDARD'>('ALL');

  const filteredOrders = orders.filter(order => {
    if (pkgDeliveryFilter === 'ALL') return true;
    if (pkgDeliveryFilter === 'EXPRESS') return order.deliveryType === 'Express';
    if (pkgDeliveryFilter === 'POSTOMAT') return order.deliveryType === 'Postomat';
    if (pkgDeliveryFilter === 'STANDARD') return order.deliveryType === 'Kaspi Доставка' || order.deliveryType === 'Today' || order.deliveryType === 'Tomorrow' || order.deliveryType === 'Самовывоз';
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
          <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <h2>Статус подключения</h2>
            
            {/* Item 1: REST API */}
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
                fetch(`${API_URL}/api/verify-session`, { method: 'POST' })
                  .then(r => r.json())
                  .then(d => addLog(d.valid ? 'Сессия REST API активна ✓' : 'Сессия REST API невалидна ✗'))
                  .catch(() => addLog('Ошибка проверки сессии REST API'));
              }}>
                🔄
              </button>
            </div>

            {/* Item 2: MC (SMS cookies) */}
            <div className="status-widget">
              <div>
                <h3 style={{ fontSize: '14px', fontWeight: 600 }}>Kaspi MC (SMS куки)</h3>
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
                <button className="btn btn-primary" style={{ padding: '6px 10px', fontSize: '12px' }} onClick={() => setMcAuthStep('phone')}>
                  🔑 {mcHasSession ? 'Войти заново' : 'Войти'}
                </button>
                <button className="btn btn-secondary" style={{ padding: '6px 10px', fontSize: '12px' }} onClick={() => {
                  addLog('Проверка кук Kaspi MC...');
                  checkMcStatus(true).then(() => addLog('Проверка завершена.'));
                }}>
                  🔄
                </button>
              </div>
            </div>
          </div>

          {/* Logs */}
          <div className="glass-card" style={{ flexGrow: 1, display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
              <h2>Журнал событий</h2>
              <button 
                className="btn btn-secondary" 
                style={{ padding: '4px 8px', fontSize: '11px', borderRadius: '6px' }}
                onClick={() => setLogs(['Журнал событий очищен.'])}
              >
                Очистить
              </button>
            </div>
            <div className="console-box">
              {logs.map((log, index) => {
                const timeMatch = log.match(/^\[([0-9:]+)\]\s*(.*)$/);
                const isError = log.includes('Ошибка') || log.includes('✗');
                const isWarning = log.includes('Предупреждение') || log.includes('WARN');
                const isSuccess = log.includes('успешно') || log.includes('✓') || log.includes('активна') || log.includes('обработан');
                
                let textColor = '#e5e7eb';
                if (isError) textColor = 'var(--error)';
                else if (isWarning) textColor = 'var(--warning)';
                else if (isSuccess) textColor = '#34d399';

                if (timeMatch) {
                  return (
                    <div key={index} className="console-line" style={{ color: textColor }}>
                      <span className="console-timestamp">[{timeMatch[1]}]</span>
                      <span>{timeMatch[2]}</span>
                    </div>
                  );
                }
                return (
                  <div key={index} className="console-line" style={{ color: textColor }}>
                    {log}
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Right Side: Control Tabs & Panels */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          <div className="glass-card" style={{ flexGrow: 1, display: 'flex', flexDirection: 'column' }}>
            
            {/* Tabs Header */}
            <div className="tab-nav">
              <button 
                className={`tab-btn ${activeTab === 'products' ? 'active' : ''}`}
                onClick={() => {
                  setActiveTab('products');
                  loadProducts(1, statusFilter, searchQuery, pageSize);
                  loadDumpingConfigs();
                }}
              >
                🏷️ Товары
              </button>
              <button 
                className={`tab-btn ${activeTab === 'dumping' ? 'active' : ''}`}
                onClick={() => {
                  setActiveTab('dumping');
                  loadProducts(1, statusFilter, searchQuery, pageSize);
                  loadDumpingConfigs();
                }}
              >
                ⚡ Демпинг
              </button>
              <button 
                className={`tab-btn ${activeTab === 'packaging' ? 'active' : ''}`}
                onClick={() => {
                  setActiveTab('packaging');
                  loadOrders(orderStatusFilter, orderLimit);
                }}
              >
                📦 Упаковка
              </button>
              <button 
                className={`tab-btn ${activeTab === 'transfer' ? 'active' : ''}`}
                onClick={() => {
                  setActiveTab('transfer');
                  loadTransfers(transferFilter);
                }}
              >
                🚚 Передача
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
                          className="order-card"
                          style={{ 
                            padding: '16px', 
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            margin: 0
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

            {/* TAB 4: AUTOMATIC PRICE DUMPING */}
            {activeTab === 'dumping' && (
              <>
                {/* Header with Search, Filter & Page Size */}
                <div style={{ borderBottom: '1px solid rgba(255,255,255,0.06)', paddingBottom: '16px', marginBottom: '20px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                    <h2 style={{ fontSize: '16px' }}>
                      ⚡ Настройка авто-демпинга ({totalCount ? `${totalCount} поз.` : '0 поз.'})
                    </h2>
                    
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
                      onClick={runDumpingCycle}
                      disabled={isRunningDumping || products.length === 0}
                    >
                      {isRunningDumping ? '⏳ Расчет выполняется...' : '⚡ Запустить авто-демпинг'}
                    </button>
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
                      onClick={loadDumpingConfigs}
                    >
                      🔄 Обновить настройки
                    </button>
                  </form>
                </div>

                {/* Products List with Dumping Controls */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', flexGrow: 1 }}>
                  {fetchingProducts && products.length === 0 ? (
                    <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
                      <div style={{ width: '32px', height: '32px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 16px auto' }} />
                      <p>Загрузка товаров...</p>
                    </div>
                  ) : products.length === 0 && !fetchingProducts ? (
                    <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
                      <p>Товары не найдены. Смените фильтр поиска.</p>
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
                        const isExpanded = expandedSkus.has(sku);
                        const cfg = dumpingConfigs[sku] || { min_price: 0, max_price: 0, dump_step: 10, enabled: false };
                        const hasConfig = !!dumpingConfigs[sku];
                        const isDumpingActive = hasConfig && dumpingConfigs[sku].enabled;

                        return (
                          <div 
                            key={product.id} 
                            className="order-card"
                            style={{ padding: '16px', margin: 0 }}
                          >
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                              <div style={{ maxWidth: '70%' }}>
                                <h3 style={{ fontSize: '14px', fontWeight: 600, color: '#fff' }}>{product.name}</h3>
                                <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>{product.sku}</p>
                                
                                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginTop: '8px' }}>
                                  <span 
                                    className="badge" 
                                    style={{ 
                                      background: isDumpingActive ? 'rgba(16, 185, 129, 0.1)' : 'rgba(255,255,255,0.05)',
                                      color: isDumpingActive ? 'var(--success)' : '#aaa',
                                      border: '1px solid rgba(255,255,255,0.08)'
                                    }}
                                  >
                                    {isDumpingActive ? 'Демпинг активен' : 'Демпинг выключен'}
                                  </span>
                                </div>
                              </div>

                              <div style={{ textAlign: 'right', display: 'flex', alignItems: 'center', gap: '16px' }}>
                                <div>
                                  <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Текущая цена:</div>
                                  <div style={{ fontSize: '16px', fontWeight: 700, color: '#fff' }}>{product.price}</div>
                                </div>
                                <button
                                  type="button"
                                  className="btn btn-secondary"
                                  style={{ padding: '8px 12px', fontSize: '12px', borderColor: isExpanded ? 'var(--secondary)' : 'transparent' }}
                                  onClick={() => {
                                    const next = new Set(expandedSkus);
                                    if (next.has(sku)) {
                                      next.delete(sku);
                                    } else {
                                      next.add(sku);
                                    }
                                    setExpandedSkus(next);
                                  }}
                                >
                                  ⚙️ {isExpanded ? 'Закрыть' : 'Настроить'}
                                </button>
                              </div>
                            </div>

                            {isExpanded && (
                              <div style={{ 
                                marginTop: '16px', 
                                paddingTop: '16px', 
                                borderTop: '1px solid rgba(255,255,255,0.05)',
                                display: 'flex',
                                flexDirection: 'column',
                                gap: '12px'
                              }}>
                                <h4 style={{ fontSize: '13px', fontWeight: 600, color: 'var(--secondary)' }}>
                                  Параметры авто-демпинга цен
                                </h4>
                                
                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: '12px' }}>
                                  <div>
                                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Минимальная цена (₸)</label>
                                    <input 
                                      type="number" 
                                      id={`min-${sku}`} 
                                      className="form-input" 
                                      defaultValue={cfg.min_price || ''}
                                      placeholder="Себестоимость"
                                    />
                                  </div>
                                  <div>
                                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Максимальная цена (₸)</label>
                                    <input 
                                      type="number" 
                                      id={`max-${sku}`} 
                                      className="form-input" 
                                      defaultValue={cfg.max_price || ''}
                                      placeholder="МРЦ / RRP"
                                    />
                                  </div>
                                  <div>
                                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Шаг снижения (₸)</label>
                                    <input 
                                      type="number" 
                                      id={`step-${sku}`} 
                                      className="form-input" 
                                      defaultValue={cfg.dump_step ?? 10}
                                    />
                                  </div>
                                  <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
                                    <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: '12px', color: '#fff', marginTop: '12px' }}>
                                      <input 
                                        type="checkbox" 
                                        id={`enabled-${sku}`} 
                                        defaultChecked={cfg.enabled}
                                        style={{ width: '16px', height: '16px', cursor: 'pointer' }}
                                      />
                                      Демпинг активен
                                    </label>
                                  </div>
                                </div>

                                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '4px' }}>
                                  <button
                                    type="button"
                                    className="btn btn-primary"
                                    style={{ padding: '6px 12px', fontSize: '12px' }}
                                    onClick={() => {
                                      const minVal = parseFloat((document.getElementById(`min-${sku}`) as HTMLInputElement).value) || 0;
                                      const maxVal = parseFloat((document.getElementById(`max-${sku}`) as HTMLInputElement).value) || 0;
                                      const stepVal = parseFloat((document.getElementById(`step-${sku}`) as HTMLInputElement).value) || 10;
                                      const enabledVal = (document.getElementById(`enabled-${sku}`) as HTMLInputElement).checked;
                                      saveDumpingConfig(sku, minVal, maxVal, stepVal, enabledVal);
                                    }}
                                  >
                                    Сохранить
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

            {/* TAB 2: PACKAGING / ORDERS */}
            {activeTab === 'packaging' && (
              <>
                {/* Header with Search, Filter & Limit */}
                <div style={{ borderBottom: '1px solid rgba(255,255,255,0.06)', paddingBottom: '16px', marginBottom: '20px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px', marginBottom: '16px' }}>
                    <h2 style={{ fontSize: '16px' }}>
                      📦 Упаковка заказов ({filteredOrders.length ? `${filteredOrders.length} поз.` : '0 поз.'})
                    </h2>
                    
                    <div style={{ display: 'flex', alignItems: 'center', gap: '16px', flexWrap: 'wrap' }}>
                      {/* Limit Selector */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Лимит:</span>
                        <select 
                          value={orderLimit} 
                          onChange={(e) => {
                            const limit = parseInt(e.target.value);
                            setOrderLimit(limit);
                            loadOrders(orderStatusFilter, limit);
                          }}
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
                          onClick={() => {
                            setOrderStatusFilter('NEW');
                            loadOrders('NEW', orderLimit);
                          }}
                        >
                          Новые
                        </button>
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
                            onClick={() => setPkgDeliveryFilter(filterVal)}
                          >
                            {filterVal === 'ALL' && 'Все'}
                            {filterVal === 'EXPRESS' && 'Экспресс'}
                            {filterVal === 'POSTOMAT' && 'Постамат'}
                            {filterVal === 'STANDARD' && 'Обычные'}
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
                        onClick={toggleSelectAll}
                        disabled={filteredOrders.length === 0}
                      >
                        {selectedOrderIds.size === filteredOrders.length && filteredOrders.length > 0 ? '🚫 Снять выбор' : '☑️ Выбрать все'}
                      </button>

                      {selectedOrderIds.size > 0 && (
                        <button 
                          type="button" 
                          className="btn btn-primary" 
                          style={{ padding: '8px 14px', fontSize: '13px', background: 'linear-gradient(135deg, #10b981, #059669)', boxShadow: '0 4px 12px rgba(16, 185, 129, 0.2)' }}
                          onClick={packSelectedOrders}
                        >
                          📦 Собрать выбранные ({selectedOrderIds.size})
                        </button>
                      )}
                    </div>

                    <div style={{ display: 'flex', gap: '12px' }}>
                      <button 
                        type="button" 
                        className="btn btn-secondary" 
                        style={{ padding: '8px 14px', fontSize: '13px', border: '1px solid var(--success)' }}
                        onClick={packAllOrders}
                        disabled={packingAll || filteredOrders.length === 0}
                      >
                        {packingAll ? '⏳ Выполняется...' : '🚀 Сформировать все накладные'}
                      </button>

                      <button 
                        type="button" 
                        className="btn btn-secondary" 
                        style={{ padding: '8px 12px' }}
                        onClick={() => loadOrders(orderStatusFilter, orderLimit)}
                        disabled={fetchingOrders}
                      >
                        {fetchingOrders ? '🔄' : '♻️ Обновить'}
                      </button>
                    </div>
                  </div>
                </div>

                {/* Orders List */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', flexGrow: 1 }}>
                  {fetchingOrders && filteredOrders.length === 0 ? (
                    <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
                      <div style={{ width: '32px', height: '32px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 16px auto' }} />
                      <p>Загрузка заказов через Kaspi API...</p>
                    </div>
                  ) : filteredOrders.length === 0 && !fetchingOrders ? (
                    <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
                      <p>Заказы не найдены. Нажмите «♻️ Обновить» или смените фильтр.</p>
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
                                  onChange={() => toggleOrderSelection(order.id)}
                                  style={{ width: '18px', height: '18px', cursor: 'pointer', accentColor: 'var(--primary)' }}
                                />
                                <div>
                                  <h3 style={{ fontSize: '16px', fontWeight: 700, color: '#fff', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                    Заказ #{order.id}
                                    <span style={{ fontSize: '11px', fontWeight: 500, color: 'var(--text-muted)' }}>({order.createdAt})</span>
                                  </h3>
                                  <div style={{ display: 'flex', gap: '8px', marginTop: '6px' }}>
                                    {order.deliveryType === 'Express' ? (
                                      <span className="badge badge-error">⚡ Экспресс</span>
                                    ) : order.deliveryType === 'Today' ? (
                                      <span className="badge badge-warning">🕒 Сегодня</span>
                                    ) : order.deliveryType === 'Tomorrow' ? (
                                      <span className="badge badge-warning" style={{ background: 'rgba(59, 130, 246, 0.1)', color: '#3b82f6', border: '1px solid rgba(59, 130, 246, 0.2)' }}>📅 Завтра</span>
                                    ) : (order.deliveryType === 'Самовывоз' || order.deliveryType === 'Postomat') ? (
                                      <span className="badge" style={{ background: 'rgba(139, 92, 246, 0.1)', color: '#a78bfa', border: '1px solid rgba(139, 92, 246, 0.2)' }}>📦 Постомат</span>
                                    ) : (
                                      <span className="badge" style={{ background: 'rgba(255, 255, 255, 0.05)', color: 'var(--text-muted)', border: '1px solid rgba(255, 255, 255, 0.08)' }}>📦 {order.deliveryType}</span>
                                    )}
                                    <span className="badge" style={{ background: order.status === 'APPROVED_BY_BANK' ? 'rgba(59, 130, 246, 0.1)' : 'rgba(16, 185, 129, 0.1)', color: order.status === 'APPROVED_BY_BANK' ? '#3b82f6' : 'var(--success)' }}>
                                      {order.status === 'APPROVED_BY_BANK' ? 'Новый' : 'Одобрен'}
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
                                <span style={{ color: 'var(--text-muted)' }}>Получатель:</span>
                                <span style={{ color: '#fff', fontWeight: 500 }}>
                                  {order.customer.name} {order.customer.phone && `(${order.customer.phone})`}
                                </span>

                                <span style={{ color: 'var(--text-muted)' }}>Адрес доставки:</span>
                                <span style={{ color: '#fff' }}>{order.address}</span>
                              </div>

                              {/* Items List */}
                              <div>
                                <h4 style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '8px' }}>Товары:</h4>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                  {order.entries && order.entries.map((item, idx) => (
                                    <div key={idx} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'rgba(255,255,255,0.01)', padding: '8px 12px', borderRadius: '8px', fontSize: '13px', border: '1px solid rgba(255,255,255,0.02)' }}>
                                      <div style={{ flexGrow: 1, marginRight: '16px' }}>
                                        <div style={{ color: '#fff', fontWeight: 500 }}>{item.productName}</div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>SKU: {item.sku}</div>
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
                                onClick={() => toggleOrderSelection(order.id)}
                              >
                                {isSelected ? 'Снять выбор' : 'Выбрать'}
                              </button>
                              <button 
                                className="btn btn-primary"
                                style={{ padding: '8px 16px', fontSize: '13px' }}
                                onClick={() => packOrder(order.id, order.internalId)}
                              >
                                {order.status === 'APPROVED_BY_BANK' ? '🚀 Принять заказ' : '📦 Упаковать заказ'}
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </>
            )}

            {/* TAB 3: TRANSFER */}
            {activeTab === 'transfer' && (
              <>
                {/* Header with Search, Filter & Limit */}
                <div style={{ borderBottom: '1px solid rgba(255,255,255,0.06)', paddingBottom: '16px', marginBottom: '20px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
                    <h2 style={{ fontSize: '16px' }}>
                      🚚 Передача заказов курьеру ({transfers.length ? `${transfers.length} поз.` : '0 поз.'})
                    </h2>
                    
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                      {/* Transfer Filter */}
                      <div style={{ display: 'flex', background: 'rgba(255,255,255,0.04)', padding: '3px', borderRadius: '8px' }}>
                        {(['ALL', 'EXPRESS', 'TODAY'] as const).map((filterVal) => (
                          <button 
                            key={filterVal}
                            className="btn" 
                            style={{ 
                              background: transferFilter === filterVal ? 'var(--primary)' : 'transparent', 
                              padding: '5px 12px', fontSize: '11px', borderRadius: '6px'
                            }}
                            onClick={() => {
                              setTransferFilter(filterVal);
                              loadTransfers(filterVal);
                            }}
                          >
                            {filterVal === 'ALL' && 'Все'}
                            {filterVal === 'EXPRESS' && 'Экспресс'}
                            {filterVal === 'TODAY' && 'Сегодня'}
                          </button>
                        ))}
                      </div>

                      <button 
                        type="button" 
                        className="btn btn-secondary" 
                        style={{ padding: '8px 12px' }}
                        onClick={() => loadTransfers(transferFilter)}
                        disabled={fetchingTransfers}
                      >
                        {fetchingTransfers ? '🔄' : '♻️ Обновить'}
                      </button>
                    </div>
                  </div>
                </div>

                {/* Transfers List */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', flexGrow: 1 }}>
                  {fetchingTransfers && transfers.length === 0 ? (
                    <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
                      <div style={{ width: '32px', height: '32px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 16px auto' }} />
                      <p>Загрузка передач через Kaspi API...</p>
                    </div>
                  ) : transfers.length === 0 && !fetchingTransfers ? (
                    <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
                      <p>Передачи не найдены. Нажмите «♻️ Обновить» или смените фильтр.</p>
                    </div>
                  ) : (
                    <div style={{
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '16px',
                      opacity: fetchingTransfers ? 0.6 : 1,
                      pointerEvents: fetchingTransfers ? 'none' : 'auto',
                      transition: 'opacity 0.2s ease-in-out'
                    }}>
                      {transfers.map((transfer) => (
                        <div 
                          key={transfer.id} 
                          style={{ 
                            padding: '20px', 
                            background: 'rgba(255, 255, 255, 0.02)', 
                            borderRadius: '16px',
                            border: '1px solid rgba(255, 255, 255, 0.05)',
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            flexWrap: 'wrap',
                            gap: '16px'
                          }}
                        >
                          <div>
                            <h3 style={{ fontSize: '16px', fontWeight: 700, color: '#fff', display: 'flex', alignItems: 'center', gap: '12px' }}>
                              Заказ #{transfer.id}
                              <span style={{ fontSize: '12px', fontWeight: 500, color: 'var(--error)' }}>
                                {transfer.deadline}
                              </span>
                            </h3>
                            
                            <div style={{ display: 'flex', gap: '8px', marginTop: '6px', marginBottom: '8px' }}>
                              {transfer.deliveryType === 'Express' ? (
                                <span className="badge badge-error">⚡ Экспресс</span>
                              ) : transfer.deliveryType === 'Today' ? (
                                <span className="badge badge-warning">🕒 Сегодня</span>
                              ) : transfer.deliveryType === 'Tomorrow' ? (
                                <span className="badge badge-warning" style={{ background: 'rgba(59, 130, 246, 0.1)', color: '#3b82f6', border: '1px solid rgba(59, 130, 246, 0.2)' }}>📅 Завтра</span>
                              ) : (
                                <span className="badge" style={{ background: 'rgba(255, 255, 255, 0.05)', color: 'var(--text-muted)', border: '1px solid rgba(255, 255, 255, 0.08)' }}>🚚 {transfer.deliveryType}</span>
                              )}
                            </div>

                            <p style={{ fontSize: '13px', color: '#fff' }}>
                              <strong style={{ color: 'var(--text-muted)' }}>Адрес:</strong> {transfer.address}
                            </p>
                          </div>

                          <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                            {transfer.waybillUrl ? (
                              <a 
                                href={transfer.waybillUrl} 
                                target="_blank" 
                                rel="noreferrer" 
                                className="btn btn-primary"
                                style={{ padding: '10px 16px', fontSize: '13px', textDecoration: 'none', background: 'linear-gradient(135deg, #3b82f6, #2563eb)', boxShadow: '0 4px 12px rgba(59, 130, 246, 0.2)' }}
                              >
                                🖨️ Накладная ({transfer.waybillNumber || 'Открыть'})
                              </a>
                            ) : (
                              <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Накладная отсутствует</span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            )}

            {/* Dummy statement to reference unused variables for strict TypeScript compliance */}
            {(() => {
              if (false as boolean) {
                console.log(isAuthed, openMenuId, setOpenMenuId, setFetchingTransfers, setFetchingOrders, setPackingAll);
              }
              return null;
            })()}



          </div>
        </div>
      </div>
      {mcAuthStep !== 'idle' && (
        <div className="modal-overlay">
          <div className="glass-card modal-content" style={{ maxWidth: '400px', width: '100%', padding: '30px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
              <h3 style={{ fontSize: '18px', fontWeight: 700 }}>Авторизация Kaspi MC</h3>
              <button 
                onClick={() => setMcAuthStep('idle')} 
                style={{ background: 'transparent', border: 'none', color: '#fff', fontSize: '20px', cursor: 'pointer' }}
              >
                ✕
              </button>
            </div>

            {mcAuthError && (
              <div style={{ padding: '10px 12px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239, 68, 68, 0.3)', color: '#fca5a5', borderRadius: '8px', marginBottom: '16px', fontSize: '13px' }}>
                {mcAuthError}
              </div>
            )}

            {mcAuthStep === 'phone' && (
              <div>
                <p style={{ fontSize: '13px', marginBottom: '16px', color: 'var(--text-muted)' }}>
                  Введите номер телефона, привязанный к вашему кабинету Kaspi Merchant. Мы запустим браузер для входа.
                </p>
                <div style={{ marginBottom: '16px' }}>
                  <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>Номер телефона</label>
                  <input 
                    type="text" 
                    className="form-input" 
                    placeholder="+77771234567" 
                    value={phoneInput}
                    onChange={(e) => setPhoneInput(e.target.value)}
                  />
                </div>
                <button 
                  className="btn btn-primary" 
                  style={{ width: '100%' }}
                  onClick={async () => {
                    setMcAuthError('');
                    setMcAuthStep('loading');
                    addLog('Запуск SMS авторизации для ' + phoneInput);
                    try {
                      const res = await fetch(`${API_URL}/api/start-login`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ phone: phoneInput })
                      });
                      const data = await res.json();
                      if (res.ok && data.success) {
                        setMcAuthStep('sms');
                        addLog('SMS код отправлен на ' + phoneInput);
                      } else {
                        setMcAuthError(data.detail || 'Не удалось начать авторизацию.');
                        setMcAuthStep('phone');
                      }
                    } catch {
                      setMcAuthError('Ошибка сети.');
                      setMcAuthStep('phone');
                    }
                  }}
                >
                  Отправить SMS-код
                </button>
              </div>
            )}

            {mcAuthStep === 'sms' && (
              <div>
                <p style={{ fontSize: '13px', marginBottom: '16px', color: 'var(--text-muted)' }}>
                  На ваш номер телефона отправлен код подтверждения. Введите его ниже.
                </p>
                <div style={{ marginBottom: '16px' }}>
                  <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>Код из SMS</label>
                  <input 
                    type="text" 
                    className="form-input" 
                    placeholder="123456" 
                    value={smsInput}
                    onChange={(e) => setSmsInput(e.target.value)}
                  />
                </div>
                <button 
                  className="btn btn-primary" 
                  style={{ width: '100%' }}
                  onClick={async () => {
                    setMcAuthError('');
                    setMcAuthStep('loading');
                    addLog('Отправка SMS-кода...');
                    try {
                      const res = await fetch(`${API_URL}/api/submit-sms`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ sms: smsInput })
                      });
                      const data = await res.json();
                      if (res.ok && data.success) {
                        setMcAuthStep('success');
                        setMcHasSession(true);
                        addLog('Авторизация Kaspi MC успешна! Сессионные куки сохранены.');
                      } else {
                        setMcAuthError(data.detail || 'Неверный код.');
                        setMcAuthStep('sms');
                      }
                    } catch {
                      setMcAuthError('Ошибка сети.');
                      setMcAuthStep('sms');
                    }
                  }}
                >
                  Подтвердить код
                </button>
              </div>
            )}

            {mcAuthStep === 'loading' && (
              <div style={{ textAlign: 'center', padding: '20px 0' }}>
                <div style={{ width: '32px', height: '32px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 16px auto' }} />
                <p style={{ fontSize: '13px', color: 'var(--text-muted)' }}>Выполняется действие в браузере...</p>
              </div>
            )}

            {mcAuthStep === 'success' && (
              <div style={{ textAlign: 'center', padding: '10px 0' }}>
                <div style={{ 
                  width: '48px', height: '48px', 
                  background: 'rgba(16, 185, 129, 0.1)', 
                  border: '2px solid var(--success)', 
                  borderRadius: '50%', display: 'inline-flex', 
                  justifyContent: 'center', alignItems: 'center', 
                  color: 'var(--success)', fontSize: '24px',
                  margin: '0 auto 16px auto'
                }}>✓</div>
                <h4 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '8px' }}>Авторизовано!</h4>
                <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '20px' }}>
                  Сессия Kaspi MC успешно настроена.
                </p>
                <button className="btn btn-primary" style={{ width: '100%' }} onClick={() => setMcAuthStep('idle')}>
                  Закрыть
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
