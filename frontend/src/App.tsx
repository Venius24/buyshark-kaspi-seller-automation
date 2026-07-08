import React, { useState, useEffect } from 'react';

interface Account {
  phone: string;
  status: 'valid' | 'invalid' | 'checking';
  lastChecked?: string;
}

interface Product {
  id: string;
  name: string;
  sku: string;
  price: string;
  status: string;
  url: string;
}

export default function App() {
  const [isLoaded, setIsLoaded] = useState(false);
  const [view, setView] = useState<'login' | 'dashboard'>('login');
  
  const [accounts, setAccounts] = useState<Account[]>([]);
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

  // Modals state
  const [selectedProductForEdit, setSelectedProductForEdit] = useState<Product | null>(null);
  const [selectedProductForPublish, setSelectedProductForPublish] = useState<Product | null>(null);
  
  const [newPrice, setNewPrice] = useState('');
  const [newStock, setNewStock] = useState('20');
  const [updatingProduct, setUpdatingProduct] = useState(false);

  // Tabs navigation, packaging and transfer states
  const [activeTab, setActiveTab] = useState<'products' | 'packaging' | 'transfer'>('products');
  const [orders, setOrders] = useState<any[]>([]);
  const [fetchingOrders, setFetchingOrders] = useState(false);
  const [orderStatusFilter, setOrderStatusFilter] = useState<'NEW' | 'KASPI_DELIVERY_CARGO_ASSEMBLY'>('KASPI_DELIVERY_CARGO_ASSEMBLY');
  const [orderLimit, setOrderLimit] = useState(10);
  const [selectedOrderIds, setSelectedOrderIds] = useState<Set<string>>(new Set());
  const [packingAll, setPackingAll] = useState(false);

  const [transfers, setTransfers] = useState<any[]>([]);
  const [fetchingTransfers, setFetchingTransfers] = useState(false);
  const [transferFilter, setTransferFilter] = useState<'ALL' | 'EXPRESS' | 'TODAY'>('ALL');

  const [logs, setLogs] = useState<string[]>([
    'Система автоматизации инициализирована.',
    'Ожидание подключения Kaspi Shop аккаунта...',
  ]);

  const [phoneInput, setPhoneInput] = useState('+7');
  const [smsInput, setSmsInput] = useState('');
  const [loginStep, setLoginStep] = useState<'phone' | 'sms' | 'success' | 'loading'>('phone');
  const [errorMsg, setErrorMsg] = useState('');
  const [loadingMsg, setLoadingMsg] = useState('');

  const API_URL = 'http://localhost:5000';

  const addLog = (message: string) => {
    const timestamp = new Date().toLocaleTimeString();
    setLogs((prev) => [`[${timestamp}] ${message}`, ...prev.slice(0, 49)]);
  };

  // Check initial account status
  useEffect(() => {
    const savedPhones = localStorage.getItem('kaspi_saved_phones');
    if (savedPhones) {
      try {
        const parsed = JSON.parse(savedPhones) as string[];
        if (parsed.length > 0) {
          const loadedAccounts = parsed.map(phone => ({
            phone,
            status: 'checking' as const
          }));
          setAccounts(loadedAccounts);
          setView('dashboard');
          loadedAccounts.forEach(acc => verifyAccount(acc.phone));
          // Load products for the first account
          loadProducts(parsed[0], 1, 'active', '', 10);
        }
      } catch (e) {
        // ignore
      }
    }
    setIsLoaded(true);
  }, []);

  const savePhones = (updated: Account[]) => {
    localStorage.setItem('kaspi_saved_phones', JSON.stringify(updated.map(a => a.phone)));
  };

  const verifyAccount = async (phone: string) => {
    try {
      const res = await fetch(`${API_URL}/api/verify-session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone }),
      });
      const data = await res.json();
      
      setAccounts(prev => {
        const next = prev.map(acc => {
          if (acc.phone === phone) {
            return {
              ...acc,
              status: data.valid ? ('valid' as const) : ('invalid' as const),
              lastChecked: new Date().toLocaleTimeString(),
            };
          }
          return acc;
        });
        savePhones(next);
        return next;
      });
      
      addLog(`Проверена сессия для ${phone}: ${data.valid ? 'АКТИВНА' : 'ТРЕБУЕТСЯ АВТОРИЗАЦИЯ'}`);
    } catch (e) {
      addLog(`Не удалось проверить сессию для ${phone}.`);
      setAccounts(prev => prev.map(acc => acc.phone === phone ? { ...acc, status: 'invalid' } : acc));
    }
  };

  const loadProducts = async (
    phone: string,
    pageNum: number = currentPage,
    status: 'active' | 'archive' = statusFilter,
    search: string = searchQuery,
    pSize: number = pageSize
  ) => {
    setFetchingProducts(true);
    addLog(`Загрузка товаров для ${phone} (Страница: ${pageNum}, Лимит: ${pSize}, Статус: ${status}, Поиск: "${search}")...`);
    try {
      const res = await fetch(
        `${API_URL}/api/products?phone=${phone}&page=${pageNum}&status=${status}&search=${encodeURIComponent(search)}&page_size=${pSize}`
      );
      const data = await res.json();
      if (res.ok && data.success) {
        setProducts(data.products);
        setTotalPages(data.total_pages || 1);
        setTotalCount(data.total_count || 0);
        addLog(`Успешно загружено ${data.products.length} товаров (Всего: ${data.total_count}, Страниц: ${data.total_pages}).`);
      } else {
        addLog(`Не удалось загрузить товары: ${data.detail || 'Ошибка бэкенда'}`);
      }
    } catch (err) {
      addLog('Ошибка при получении товаров с сервера автоматизации.');
    } finally {
      setFetchingProducts(false);
    }
  };

  const loadOrders = async (phone: string, status = orderStatusFilter, limit = orderLimit) => {
    setFetchingOrders(true);
    setSelectedOrderIds(new Set());
    addLog(`Загрузка заказов для ${phone} (Статус: ${status === 'NEW' ? 'Новые' : 'На сборке'}, Лимит: ${limit})...`);
    try {
      const res = await fetch(`${API_URL}/api/orders?phone=${phone}&status=${status}&limit=${limit}`);
      const data = await res.json();
      if (res.ok && data.success) {
        setOrders(data.orders);
        addLog(`Успешно загружено ${data.orders.length} заказов.`);
      } else {
        addLog(`Не удалось загрузить заказы: ${data.detail || 'Ошибка бэкенда'}`);
      }
    } catch (err) {
      addLog('Ошибка при получении списка заказов с сервера автоматизации.');
    } finally {
      setFetchingOrders(false);
    }
  };

  const packOrder = async (phone: string, orderId: string, pageNum: number = 1) => {
    addLog(`Запуск автоматической сборки заказа #${orderId} (Стр: ${pageNum})...`);
    try {
      const res = await fetch(`${API_URL}/api/orders/pack`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone, order_id: orderId, page_num: pageNum }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        addLog(`Заказ #${orderId} успешно собран, накладные сформированы.`);
        loadOrders(phone, orderStatusFilter, orderLimit);
      } else {
        addLog(`Не удалось собрать заказ #${orderId}: ${data.detail || 'Ошибка бэкенда'}`);
      }
    } catch (err) {
      addLog(`Ошибка автоматизации сборки для заказа #${orderId}.`);
    }
  };

  const packAllOrders = async (phone: string) => {
    setPackingAll(true);
    addLog(`Запуск массовой сборки всех заказов...`);
    try {
      const res = await fetch(`${API_URL}/api/orders/pack-all`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        addLog(`${data.message}`);
        setSelectedOrderIds(new Set());
        loadOrders(phone, orderStatusFilter, orderLimit);
      } else {
        addLog(`Ошибка массовой сборки: ${data.detail || 'Ошибка бэкенда'}`);
      }
    } catch (err) {
      addLog('Ошибка при массовой сборке заказов.');
    } finally {
      setPackingAll(false);
    }
  };

  const toggleOrderSelection = (orderId: string) => {
    setSelectedOrderIds(prev => {
      const next = new Set(prev);
      if (next.has(orderId)) {
        next.delete(orderId);
      } else {
        next.add(orderId);
      }
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

  const loadTransfers = async (phone: string, filter = transferFilter) => {
    setFetchingTransfers(true);
    addLog(`Загрузка передач для ${phone} (Фильтр: ${filter === 'ALL' ? 'Все' : filter === 'EXPRESS' ? 'Экспресс' : 'Сегодня до 20:00'})...`);
    try {
      const res = await fetch(`${API_URL}/api/transfers?phone=${phone}&filter=${filter}`);
      const data = await res.json();
      if (res.ok && data.success) {
        setTransfers(data.transfers);
        addLog(`Успешно загружено ${data.transfers.length} записей передачи.`);
      } else {
        addLog(`Не удалось загрузить список передач: ${data.detail || 'Ошибка бэкенда'}`);
      }
    } catch (err) {
      addLog('Ошибка при получении списка передач.');
    } finally {
      setFetchingTransfers(false);
    }
  };

  const handleProductAction = async (productSku: string, actionType: 'edit' | 'archive' | 'publish', priceVal = 0, stockVal = 100) => {
    const activeAccount = accounts.find(a => a.status === 'valid') || accounts[0];
    if (!activeAccount) {
      addLog('Нет активного кабинета для выполнения действия.');
      return;
    }

    setUpdatingProduct(true);
    addLog(`Запуск команды автоматизации [${actionType}] для SKU ${productSku}...`);

    try {
      const res = await fetch(`${API_URL}/api/product-action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone: activeAccount.phone,
          sku: productSku,
          action: actionType,
          price: priceVal,
          stock: stockVal
        }),
      });
      const data = await res.json();

      if (res.ok && data.success) {
        addLog(`Действие [${actionType}] для SKU ${productSku} выполнено успешно.`);
        setSelectedProductForEdit(null);
        setSelectedProductForPublish(null);
        loadProducts(activeAccount.phone, currentPage, statusFilter, searchQuery, pageSize);
      } else {
        addLog(`Ошибка автоматизации: ${data.detail || 'Неизвестная ошибка'}`);
      }
    } catch (err) {
      addLog('Не удалось передать команду серверу автоматизации.');
    } finally {
      setUpdatingProduct(false);
    }
  };

  const handleStartLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    const rawDigits = phoneInput.replace(/\D/g, '');
    if (rawDigits.length !== 11) {
      setErrorMsg('Номер телефона должен состоять ровно из 11 цифр (включая код страны +7).');
      return;
    }

    setErrorMsg('');
    setLoginStep('loading');
    setLoadingMsg('Инициализация сессии браузера Playwright...');
    addLog(`Запуск авторизации для ${phoneInput}...`);

    try {
      const res = await fetch(`${API_URL}/api/start-login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: rawDigits }),
      });
      const data = await res.json();

      if (res.ok && data.success) {
        setLoginStep('sms');
        addLog(`SMS отправлен на номер ${phoneInput}`);
      } else {
        setLoginStep('phone');
        setErrorMsg(data.detail || 'Ошибка при отправке запроса.');
      }
    } catch (err) {
      setLoginStep('phone');
      setErrorMsg('Не удалось связаться с сервером автоматизации.');
    }
  };

  const handleConfirmSms = async (e: React.FormEvent) => {
    e.preventDefault();
    if (smsInput.length !== 6) {
      setErrorMsg('Введите 6-значный код.');
      return;
    }

    setErrorMsg('');
    setLoginStep('loading');
    setLoadingMsg('Отправка SMS-кода в Playwright браузер...');
    addLog(`Подтверждение кода для ${phoneInput}...`);

    try {
      const res = await fetch(`${API_URL}/api/submit-sms`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: phoneInput, sms: smsInput }),
      });
      const data = await res.json();

      if (res.ok && data.success) {
        setLoginStep('success');
        addLog(`Успешный вход в личный кабинет Kaspi для ${phoneInput}`);
        
        setAccounts(prev => {
          const exists = prev.some(a => a.phone === phoneInput);
          let next;
          if (exists) {
            next = prev.map(a => a.phone === phoneInput ? { ...a, status: 'valid' as const, lastChecked: new Date().toLocaleTimeString() } : a);
          } else {
            next = [...prev, { phone: phoneInput, status: 'valid' as const, lastChecked: new Date().toLocaleTimeString() }];
          }
          savePhones(next);
          return next;
        });
      } else {
        setLoginStep('sms');
        setErrorMsg(data.detail || 'Неверный код или ошибка сессии.');
      }
    } catch (err) {
      setLoginStep('sms');
      setErrorMsg('Ошибка связи при отправке кода подтверждения.');
    }
  };

  const removeAccount = (phone: string) => {
    const updated = accounts.filter(acc => acc.phone !== phone);
    setAccounts(updated);
    savePhones(updated);
    addLog(`Удален аккаунт ${phone} из панели управления.`);
    if (updated.length === 0) {
      setView('login');
      setLoginStep('phone');
    }
  };

  const formatPhone = (val: string) => {
    const raw = val.replace(/\D/g, '');
    if (raw.length <= 1) return raw;
    return `+7 (${raw.slice(1, 4)}) ${raw.slice(4, 7)}-${raw.slice(7, 9)}-${raw.slice(9, 11)}`;
  };

  const handlePhoneInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    let value = e.target.value;
    if (!value || value.length < 2) {
      setPhoneInput('+7');
      return;
    }

    let digits = value.replace(/\D/g, '');
    if (digits.length >= 10) {
      const localNumber = digits.slice(-10);
      digits = '7' + localNumber;
    }

    const truncated = digits.slice(0, 11);
    
    let formatted = '+7';
    if (truncated.length > 1) {
      formatted += ' (' + truncated.substring(1, 4);
    }
    if (truncated.length > 4) {
      formatted += ') ' + truncated.substring(4, 7);
    }
    if (truncated.length > 7) {
      formatted += '-' + truncated.substring(7, 9);
    }
    if (truncated.length > 9) {
      formatted += '-' + truncated.substring(9, 11);
    }
    setPhoneInput(formatted);
  };

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const activeAcc = accounts.find(a => a.status === 'valid') || accounts[0];
    if (activeAcc) {
      setCurrentPage(1);
      setPageInputText('1');
      loadProducts(activeAcc.phone, 1, statusFilter, searchQuery, pageSize);
    }
  };

  const handleStatusFilterChange = (status: 'active' | 'archive') => {
    setStatusFilter(status);
    setCurrentPage(1);
    setPageInputText('1');
    const activeAcc = accounts.find(a => a.status === 'valid') || accounts[0];
    if (activeAcc) {
      loadProducts(activeAcc.phone, 1, status, searchQuery, pageSize);
    }
  };

  const handlePageChange = (page: number) => {
    if (page < 1 || page > totalPages) return;
    setCurrentPage(page);
    setPageInputText(String(page));
    const activeAcc = accounts.find(a => a.status === 'valid') || accounts[0];
    if (activeAcc) {
      loadProducts(activeAcc.phone, page, statusFilter, searchQuery, pageSize);
    }
  };

  // Loading Screen
  if (!isLoaded) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh' }}>
        <div style={{ width: '40px', height: '40px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite' }} />
      </div>
    );
  }

  // Centered SMS Login Screen
  if (view === 'login') {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh', padding: '20px' }}>
        <div className="glass-card" style={{ maxWidth: '440px', width: '100%', padding: '40px', position: 'relative' }}>
          
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
            <h1 style={{ fontSize: '24px', fontWeight: 700 }}>Kaspi MC Login</h1>
            <p style={{ fontSize: '14px', color: 'var(--text-muted)', marginTop: '6px' }}>
              {loginStep === 'phone' && 'Авторизация для управления заказами'}
              {loginStep === 'sms' && `Код отправлен на номер ${formatPhone(phoneInput)}`}
              {loginStep === 'loading' && 'Пожалуйста, подождите'}
              {loginStep === 'success' && 'Авторизация выполнена'}
            </p>
          </div>

          {errorMsg && (
            <div style={{ padding: '14px 16px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239, 68, 68, 0.3)', color: '#fca5a5', borderRadius: '12px', marginBottom: '20px', fontSize: '14px' }}>
              {errorMsg}
            </div>
          )}

          {loginStep === 'phone' && (
            <form onSubmit={handleStartLogin}>
              <div style={{ marginBottom: '24px' }}>
                <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--text-muted)', marginBottom: '8px' }}>Номер телефона</label>
                <input 
                  type="text" 
                  className="form-input" 
                  placeholder="+7 (7xx) xxx-xx-xx" 
                  value={phoneInput}
                  onChange={handlePhoneInputChange}
                  required
                  autoFocus
                />
              </div>
              <button type="submit" className="btn btn-primary" style={{ width: '100%', padding: '16px' }}>
                <span>Продолжить</span>
              </button>
              
              {accounts.length > 0 && (
                <button type="button" className="btn btn-secondary" style={{ width: '100%', padding: '16px', marginTop: '12px' }} onClick={() => setView('dashboard')}>
                  Отмена
                </button>
              )}
            </form>
          )}

          {loginStep === 'sms' && (
            <form onSubmit={handleConfirmSms}>
              <div style={{ marginBottom: '24px' }}>
                <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--text-muted)', marginBottom: '8px' }}>Код подтверждения из SMS</label>
                <input 
                  type="text" 
                  className="form-input" 
                  placeholder="Введите 6 цифр" 
                  value={smsInput}
                  onChange={(e) => setSmsInput(e.target.value.replace(/\D/g, '').slice(0, 6))}
                  maxLength={6}
                  required
                  autoFocus
                />
              </div>
              <button type="submit" className="btn btn-primary" style={{ width: '100%', padding: '16px' }}>
                <span>Войти</span>
              </button>
              <div style={{ textAlign: 'center', marginTop: '16px' }}>
                <a href="#" style={{ color: 'var(--text-muted)', fontSize: '13px', textDecoration: 'none' }} onClick={(e) => { e.preventDefault(); setLoginStep('phone'); }}>
                  ← Изменить номер телефона
                </a>
              </div>
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
                Магазин {formatPhone(phoneInput)} успешно верифицирован, сессия сохранена.
              </p>
              <button className="btn btn-primary" style={{ width: '100%', padding: '16px' }} onClick={() => setView('dashboard')}>
                Перейти в панель управления
              </button>
            </div>
          )}

        </div>
      </div>
    );
  }

  // Dashboard Screen
  return (
    <div className="dashboard-container">
      {/* Header */}
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '32px' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ fontSize: '32px' }}>🦊</span>
            <h1>Kaspi SaaS Wrapper</h1>
          </div>
          <p style={{ marginTop: '4px' }}>Панель автоматического извлечения товаров и изменения цен</p>
        </div>
        <button 
          className="btn btn-primary" 
          onClick={() => {
            setPhoneInput('+7');
            setSmsInput('');
            setLoginStep('phone');
            setErrorMsg('');
            setView('login');
          }}
        >
          <span>+ Подключить магазин</span>
        </button>
      </header>

      <div className="grid-2">
        {/* Left Side: Accounts & Control Panel */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          <div className="glass-card">
            <h2 style={{ marginBottom: '16px' }}>Подключенные аккаунты Kaspi</h2>
            
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              {accounts.map((acc) => (
                <div 
                  key={acc.phone} 
                  style={{ 
                    padding: '16px', 
                    background: 'rgba(255, 255, 255, 0.03)', 
                    borderRadius: '12px',
                    border: '1px solid rgba(255, 255, 255, 0.05)',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center'
                  }}
                >
                  <div>
                    <h3 style={{ fontSize: '15px' }}>{formatPhone(acc.phone)}</h3>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '6px' }}>
                      {acc.status === 'valid' && <span className="badge badge-success">Сессия активна</span>}
                      {acc.status === 'invalid' && <span className="badge badge-error">Требуется вход</span>}
                      {acc.status === 'checking' && <span className="badge badge-warning">Проверка...</span>}
                      {acc.lastChecked && (
                        <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                          Проверен в {acc.lastChecked}
                        </span>
                      )}
                    </div>
                  </div>
                  
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <button className="btn btn-secondary" style={{ padding: '8px 12px' }} onClick={() => verifyAccount(acc.phone)}>
                      🔄
                    </button>
                    <button className="btn btn-secondary" style={{ padding: '8px 12px', color: 'var(--error)' }} onClick={() => removeAccount(acc.phone)}>
                      🗑️
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Logs */}
          <div className="glass-card" style={{ flexGrow: 1 }}>
            <h2 style={{ marginBottom: '12px' }}>Журнал событий (Logs)</h2>
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
                <div key={index} style={{ color: log.includes('Успешный') || log.includes('АКТИВНА') ? 'var(--success)' : log.includes('ошибка') || log.includes('Ошибка') || log.includes('automation failed') ? 'var(--error)' : 'var(--text-muted)' }}>
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
                  const activeAcc = accounts.find(a => a.status === 'valid') || accounts[0];
                  if (activeAcc) loadProducts(activeAcc.phone, 1, statusFilter, searchQuery, pageSize);
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
                  const activeAcc = accounts.find(a => a.status === 'valid') || accounts[0];
                  if (activeAcc) loadOrders(activeAcc.phone, orderStatusFilter, orderLimit);
                }}
              >
                📦 Упаковка
              </button>
              <button 
                className="btn" 
                style={{ 
                  background: activeTab === 'transfer' ? 'var(--primary)' : 'rgba(255,255,255,0.02)',
                  border: '1px solid rgba(255,255,255,0.05)', color: '#fff', padding: '12px 18px', fontSize: '13px',
                  borderBottomLeftRadius: 0, borderBottomRightRadius: 0, borderTopLeftRadius: '10px', borderTopRightRadius: '10px',
                  fontWeight: 600, cursor: 'pointer', transition: 'all 0.3s ease'
                }}
                onClick={() => {
                  setActiveTab('transfer');
                  const activeAcc = accounts.find(a => a.status === 'valid') || accounts[0];
                  if (activeAcc) loadTransfers(activeAcc.phone, transferFilter);
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
                            const activeAcc = accounts.find(a => a.status === 'valid') || accounts[0];
                            if (activeAcc) {
                              loadProducts(activeAcc.phone, 1, statusFilter, searchQuery, pSize);
                            }
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
                      onClick={() => {
                        const activeAcc = accounts.find(a => a.status === 'valid') || accounts[0];
                        if (activeAcc) loadProducts(activeAcc.phone, currentPage, statusFilter, searchQuery, pageSize);
                      }}
                      disabled={fetchingProducts}
                    >
                      {fetchingProducts ? '🔄' : 'Refresh'}
                    </button>
                  </form>
                </div>

                {/* Products List */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', flexGrow: 1 }}>
                  {fetchingProducts && products.length === 0 ? (
                    <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
                      <div style={{ width: '32px', height: '32px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 16px auto' }} />
                      <p>Синхронизация товаров через Playwright...</p>
                    </div>
                  ) : products.length === 0 && !fetchingProducts ? (
                    <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
                      <p>Товары не найдены. Попробуйте изменить параметры поиска/фильтра или нажмите Refresh.</p>
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
                            
                            {/* Options Menu */}
                            <div style={{ position: 'relative' }}>
                              <button 
                                className="btn btn-secondary" 
                                style={{ padding: '6px 12px', fontSize: '12px' }}
                                onClick={() => setOpenMenuId(openMenuId === product.id ? null : product.id)}
                              >
                                Действия ⋮
                              </button>
                              
                              {/* Dropdown Menu */}
                              {openMenuId === product.id && (
                                <div 
                                  style={{ 
                                    position: 'absolute', right: 0, top: '100%', marginTop: '6px',
                                    background: '#111827', border: '1px solid var(--card-border)',
                                    borderRadius: '8px', boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
                                    zIndex: 10, width: '180px', display: 'flex', flexDirection: 'column'
                                  }}
                                >
                                  <button 
                                    style={{ background: 'none', border: 'none', color: '#fff', padding: '10px 12px', textAlign: 'left', cursor: 'pointer', fontSize: '12px' }}
                                    onClick={() => {
                                      setSelectedProductForEdit(product);
                                      setNewPrice(product.price.replace(/\D/g, ''));
                                      setNewStock('100');
                                      setOpenMenuId(null);
                                    }}
                                  >
                                    ✏️ Изменить цену
                                  </button>
                                  {product.status === 'Активен' ? (
                                    <button 
                                      style={{ background: 'none', border: 'none', color: '#ef4444', padding: '10px 12px', textAlign: 'left', cursor: 'pointer', fontSize: '12px' }}
                                      onClick={() => {
                                        handleProductAction(product.sku, 'archive');
                                        setOpenMenuId(null);
                                      }}
                                    >
                                      📦 Снять с продажи
                                    </button>
                                  ) : (
                                    <button 
                                      style={{ background: 'none', border: 'none', color: '#10b981', padding: '10px 12px', textAlign: 'left', cursor: 'pointer', fontSize: '12px' }}
                                      onClick={() => {
                                        setSelectedProductForPublish(product);
                                        setNewStock('20');
                                        setOpenMenuId(null);
                                      }}
                                    >
                                      🚀 Выставить на продажу
                                    </button>
                                  )}
                                </div>
                              )}
                            </div>
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
                            const activeAcc = accounts.find(a => a.status === 'valid') || accounts[0];
                            if (activeAcc) loadOrders(activeAcc.phone, orderStatusFilter, newLimit);
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
                            background: orderStatusFilter === 'KASPI_DELIVERY_CARGO_ASSEMBLY' ? 'var(--primary)' : 'transparent', 
                            padding: '5px 12px', fontSize: '11px', borderRadius: '6px'
                          }}
                          onClick={() => {
                            setOrderStatusFilter('KASPI_DELIVERY_CARGO_ASSEMBLY');
                            const activeAcc = accounts.find(a => a.status === 'valid') || accounts[0];
                            if (activeAcc) loadOrders(activeAcc.phone, 'KASPI_DELIVERY_CARGO_ASSEMBLY', orderLimit);
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
                            const activeAcc = accounts.find(a => a.status === 'valid') || accounts[0];
                            if (activeAcc) loadOrders(activeAcc.phone, 'NEW', orderLimit);
                          }}
                        >
                          Новые
                        </button>
                      </div>

                      <button 
                        type="button" 
                        className="btn btn-secondary" 
                        style={{ padding: '8px 12px' }}
                        onClick={() => {
                          const activeAcc = accounts.find(a => a.status === 'valid') || accounts[0];
                          if (activeAcc) loadOrders(activeAcc.phone, orderStatusFilter, orderLimit);
                        }}
                        disabled={fetchingOrders}
                      >
                        {fetchingOrders ? '🔄' : 'Refresh'}
                      </button>
                    </div>
                  </div>

                  {/* Select All + Bulk Action Bar */}
                  {orders.length > 0 && orderStatusFilter === 'KASPI_DELIVERY_CARGO_ASSEMBLY' && (
                    <div style={{ 
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      padding: '10px 14px', background: 'rgba(255,255,255,0.03)', borderRadius: '10px',
                      border: '1px solid rgba(255,255,255,0.06)'
                    }}>
                      <label style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer', fontSize: '13px' }}>
                        <input 
                          type="checkbox" 
                          checked={selectedOrderIds.size === orders.length && orders.length > 0}
                          onChange={toggleSelectAll}
                          style={{ width: '18px', height: '18px', accentColor: 'var(--primary)', cursor: 'pointer' }}
                        />
                        <span style={{ color: '#fff', fontWeight: 500 }}>
                          Выбрать все ({selectedOrderIds.size}/{orders.length})
                        </span>
                      </label>

                      <button 
                        className="btn btn-primary" 
                        style={{ 
                          padding: '8px 16px', fontSize: '12px', fontWeight: 600,
                          opacity: packingAll ? 0.7 : 1
                        }}
                        onClick={() => {
                          const activeAcc = accounts.find(a => a.status === 'valid') || accounts[0];
                          if (activeAcc) packAllOrders(activeAcc.phone);
                        }}
                        disabled={packingAll || orders.length === 0}
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
                      <p>Синхронизация заказов через Playwright (лимит: {orderLimit})...</p>
                    </div>
                  ) : orders.length === 0 ? (
                    <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
                      <p>Заказов в данном статусе не найдено.</p>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                      {orders.map((order) => (
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
                            {orderStatusFilter === 'KASPI_DELIVERY_CARGO_ASSEMBLY' && (
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
                                <span style={{ fontSize: '12px', color: 'var(--error)' }}>⏱️ {order.deadline}</span>
                              </div>
                              <p style={{ fontSize: '13px', color: '#fff', marginTop: '8px' }}>
                                {order.productName} (x{order.quantity})
                              </p>
                            </div>
                          </div>

                          <div style={{ textAlign: 'right', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '8px' }}>
                            <div style={{ fontSize: '16px', fontWeight: 700, color: 'var(--success)' }}>{order.price}</div>
                            
                            {orderStatusFilter === 'KASPI_DELIVERY_CARGO_ASSEMBLY' && (
                              <button 
                                className="btn btn-primary" 
                                style={{ padding: '6px 12px', fontSize: '12px' }}
                                onClick={() => {
                                  const activeAcc = accounts.find(a => a.status === 'valid') || accounts[0];
                                  if (activeAcc) packOrder(activeAcc.phone, order.id, order.pageNumber || 1);
                                }}
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

            {/* TAB 3: TRANSFER / HANDOVER */}
            {activeTab === 'transfer' && (
              <>
                {/* Header with filters */}
                <div style={{ borderBottom: '1px solid rgba(255,255,255,0.06)', paddingBottom: '16px', marginBottom: '20px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <h2 style={{ fontSize: '16px' }}>
                      🚚 Передача заказов курьеру ({transfers.length})
                    </h2>
                    
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                      {/* Handover filters */}
                      <div style={{ display: 'flex', background: 'rgba(255,255,255,0.04)', padding: '3px', borderRadius: '8px' }}>
                        <button 
                          className="btn" 
                          style={{ 
                            background: transferFilter === 'ALL' ? 'var(--primary)' : 'transparent', 
                            padding: '5px 10px', fontSize: '10px', borderRadius: '5px'
                          }}
                          onClick={() => {
                            setTransferFilter('ALL');
                            const activeAcc = accounts.find(a => a.status === 'valid') || accounts[0];
                            if (activeAcc) loadTransfers(activeAcc.phone, 'ALL');
                          }}
                        >
                          Все
                        </button>
                        <button 
                          className="btn" 
                          style={{ 
                            background: transferFilter === 'EXPRESS' ? 'var(--primary)' : 'transparent', 
                            padding: '5px 10px', fontSize: '10px', borderRadius: '5px'
                          }}
                          onClick={() => {
                            setTransferFilter('EXPRESS');
                            const activeAcc = accounts.find(a => a.status === 'valid') || accounts[0];
                            if (activeAcc) loadTransfers(activeAcc.phone, 'EXPRESS');
                          }}
                        >
                          Экспресс
                        </button>
                        <button 
                          className="btn" 
                          style={{ 
                            background: transferFilter === 'TODAY' ? 'var(--primary)' : 'transparent', 
                            padding: '5px 10px', fontSize: '10px', borderRadius: '5px'
                          }}
                          onClick={() => {
                            setTransferFilter('TODAY');
                            const activeAcc = accounts.find(a => a.status === 'valid') || accounts[0];
                            if (activeAcc) loadTransfers(activeAcc.phone, 'TODAY');
                          }}
                        >
                          Сегодня
                        </button>
                      </div>

                      <button 
                        type="button" 
                        className="btn btn-secondary" 
                        style={{ padding: '8px 12px' }}
                        onClick={() => {
                          const activeAcc = accounts.find(a => a.status === 'valid') || accounts[0];
                          if (activeAcc) loadTransfers(activeAcc.phone, transferFilter);
                        }}
                        disabled={fetchingTransfers}
                      >
                        {fetchingTransfers ? '🔄' : 'Refresh'}
                      </button>
                    </div>
                  </div>
                </div>

                {/* Transfers List */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', flexGrow: 1 }}>
                  {fetchingTransfers ? (
                    <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
                      <div style={{ width: '32px', height: '32px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 16px auto' }} />
                      <p>Синхронизация передач через Playwright...</p>
                    </div>
                  ) : transfers.length === 0 ? (
                    <div style={{ textAlign: 'center', padding: '40px 0', flexGrow: 1 }}>
                      <p>Заказов к передаче не найдено.</p>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                      {transfers.map((trans) => (
                        <div 
                          key={trans.id} 
                          style={{ 
                            padding: '16px', 
                            background: 'rgba(255, 255, 255, 0.02)', 
                            borderRadius: '12px',
                            border: '1px solid rgba(255, 255, 255, 0.04)',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: '8px'
                          }}
                        >
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                              <h3 style={{ fontSize: '15px', fontWeight: 700, color: '#fff' }}>Заказ #{trans.id}</h3>
                              <span 
                                className="badge" 
                                style={{ 
                                  background: trans.deliveryType === 'Express' ? 'linear-gradient(90deg, #c92c3c, #f43f5e)' : 'rgba(255,255,255,0.05)',
                                  color: '#fff', border: '1px solid rgba(255,255,255,0.08)'
                                }}
                              >
                                {trans.deliveryType}
                              </span>
                            </div>
                            <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Срок: {trans.deadline}</span>
                          </div>
                          
                          <div style={{ fontSize: '13px', color: '#9ca3af', display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <span>📍 Адрес: </span>
                            <span style={{ color: '#fff' }}>{trans.address}</span>
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

      {/* Edit Price Modal */}
      {selectedProductForEdit && (
        <div 
          style={{ 
            position: 'fixed', 
            top: 0, 
            left: 0, 
            right: 0, 
            bottom: 0, 
            background: 'rgba(0, 0, 0, 0.8)', 
            backdropFilter: 'blur(4px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 100
          }}
        >
          <div className="glass-card" style={{ maxWidth: '440px', width: '100%', margin: '20px', padding: '32px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
              <h2>Изменить цену и остатки</h2>
              <button 
                onClick={() => setSelectedProductForEdit(null)}
                style={{ background: 'none', border: 'none', color: '#fff', fontSize: '20px', cursor: 'pointer' }}
                disabled={updatingProduct}
              >
                ×
              </button>
            </div>

            <p style={{ fontSize: '13px', marginBottom: '20px', color: 'var(--text-muted)' }}>
              Редактирование для: <strong>{selectedProductForEdit.name}</strong>
            </p>

            {updatingProduct ? (
              <div style={{ textAlign: 'center', padding: '32px 0' }}>
                <div style={{ width: '32px', height: '32px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 16px auto' }} />
                <p>Запуск автоматизации Playwright. Изменение цены в кабинете продавца...</p>
              </div>
            ) : (
              <form 
                onSubmit={(e) => {
                  e.preventDefault();
                  handleProductAction(
                    selectedProductForEdit.sku, 
                    'edit', 
                    parseInt(newPrice), 
                    parseInt(newStock)
                  );
                }}
              >
                <div style={{ marginBottom: '16px' }}>
                  <label style={{ display: 'block', fontSize: '12px', textTransform: 'uppercase', marginBottom: '6px', color: 'var(--text-muted)' }}>Цена, ₸</label>
                  <input 
                    type="text" 
                    className="form-input" 
                    value={newPrice}
                    onChange={(e) => setNewPrice(e.target.value.replace(/\D/g, ''))}
                    required
                  />
                </div>

                <div style={{ marginBottom: '24px' }}>
                  <label style={{ display: 'block', fontSize: '12px', textTransform: 'uppercase', marginBottom: '6px', color: 'var(--text-muted)' }}>Остатки (шт.)</label>
                  <input 
                    type="text" 
                    className="form-input" 
                    value={newStock}
                    onChange={(e) => setNewStock(e.target.value.replace(/\D/g, ''))}
                    required
                  />
                </div>

                <div style={{ display: 'flex', gap: '12px' }}>
                  <button type="button" className="btn btn-secondary" style={{ flex: 1 }} onClick={() => setSelectedProductForEdit(null)}>
                    Отмена
                  </button>
                  <button type="submit" className="btn btn-primary" style={{ flex: 1 }}>
                    Сохранить
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}

      {/* Publish Stock Modal */}
      {selectedProductForPublish && (
        <div 
          style={{ 
            position: 'fixed', 
            top: 0, 
            left: 0, 
            right: 0, 
            bottom: 0, 
            background: 'rgba(0, 0, 0, 0.8)', 
            backdropFilter: 'blur(4px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 100
          }}
        >
          <div className="glass-card" style={{ maxWidth: '440px', width: '100%', margin: '20px', padding: '32px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
              <h2>Выставить товар на продажу</h2>
              <button 
                onClick={() => setSelectedProductForPublish(null)}
                style={{ background: 'none', border: 'none', color: '#fff', fontSize: '20px', cursor: 'pointer' }}
                disabled={updatingProduct}
              >
                ×
              </button>
            </div>

            <p style={{ fontSize: '13px', marginBottom: '20px', color: 'var(--text-muted)' }}>
              Активация товара: <strong>{selectedProductForPublish.name}</strong>
            </p>

            {updatingProduct ? (
              <div style={{ textAlign: 'center', padding: '32px 0' }}>
                <div style={{ width: '32px', height: '32px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 16px auto' }} />
                <p>Запуск автоматизации Playwright. Выставление товара на продажу...</p>
              </div>
            ) : (
              <form 
                onSubmit={(e) => {
                  e.preventDefault();
                  handleProductAction(
                    selectedProductForPublish.sku, 
                    'publish', 
                    0, 
                    parseInt(newStock)
                  );
                }}
              >
                <div style={{ marginBottom: '24px' }}>
                  <label style={{ display: 'block', fontSize: '12px', textTransform: 'uppercase', marginBottom: '6px', color: 'var(--text-muted)' }}>Остатки от 0 до 10000 шт</label>
                  <input 
                    type="text" 
                    className="form-input" 
                    value={newStock}
                    onChange={(e) => setNewStock(e.target.value.replace(/\D/g, ''))}
                    required
                  />
                </div>

                <div style={{ display: 'flex', gap: '12px' }}>
                  <button type="button" className="btn btn-secondary" style={{ flex: 1 }} onClick={() => setSelectedProductForPublish(null)}>
                    Отмена
                  </button>
                  <button type="submit" className="btn btn-primary" style={{ flex: 1 }}>
                    Выставить на продажу
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
