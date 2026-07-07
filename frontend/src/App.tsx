import React, { useState, useEffect } from 'react';

interface Account {
  phone: string;
  status: 'valid' | 'invalid' | 'checking';
  lastChecked?: string;
  autoAccept: boolean;
}

interface Order {
  id: string;
  customer: string;
  items: string;
  price: string;
  status: 'NEW' | 'ACCEPTED' | 'COMPLETED' | 'CANCELLED';
  date: string;
}

export default function App() {
  const [isLoaded, setIsLoaded] = useState(false);
  const [view, setView] = useState<'login' | 'dashboard'>('login');
  
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [orders, setOrders] = useState<Order[]>([
    { id: '109348', customer: 'Арман С.', items: 'Смартфон Xiaomi Redmi Note 13 Pro 8/256GB Black', price: '124 990 ₸', status: 'NEW', date: 'Только что' },
    { id: '109345', customer: 'Айгерим М.', items: 'Наушники Apple AirPods 3 с зарядным футляром Lightning', price: '94 990 ₸', status: 'ACCEPTED', date: '10 мин. назад' },
    { id: '109340', customer: 'Дмитрий К.', items: 'Фен Dyson HD07 Supersonic Iron/Fuchsia', price: '229 990 ₸', status: 'COMPLETED', date: '2 ч. назад' },
  ]);

  const [logs, setLogs] = useState<string[]>([
    'Система автоматизации инициализирована.',
    'Ожидание подключения Kaspi Shop аккаунта...',
  ]);

  const [phoneInput, setPhoneInput] = useState('');
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
            status: 'checking' as const,
            autoAccept: localStorage.getItem(`auto_accept_${phone}`) === 'true',
          }));
          setAccounts(loadedAccounts);
          setView('dashboard');
          loadedAccounts.forEach(acc => verifyAccount(acc.phone));
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

  const handleStartLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (phoneInput.length < 10) {
      setErrorMsg('Введите корректный номер телефона.');
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
        body: JSON.stringify({ phone: phoneInput }),
      });
      const data = await res.json();

      if (data.success) {
        setLoginStep('sms');
        addLog(`SMS отправлен на номер ${phoneInput}`);
      } else {
        setLoginStep('phone');
        setErrorMsg(data.error || 'Ошибка при отправке запроса.');
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

      if (data.success) {
        setLoginStep('success');
        addLog(`Успешный вход в личный кабинет Kaspi для ${phoneInput}`);
        
        // Add or update account list
        setAccounts(prev => {
          const exists = prev.some(a => a.phone === phoneInput);
          let next;
          if (exists) {
            next = prev.map(a => a.phone === phoneInput ? { ...a, status: 'valid' as const, lastChecked: new Date().toLocaleTimeString() } : a);
          } else {
            next = [...prev, { phone: phoneInput, status: 'valid' as const, autoAccept: false, lastChecked: new Date().toLocaleTimeString() }];
          }
          savePhones(next);
          return next;
        });
      } else {
        setLoginStep('sms');
        setErrorMsg(data.error || 'Неверный код или ошибка сессии.');
      }
    } catch (err) {
      setLoginStep('sms');
      setErrorMsg('Ошибка связи при отправке кода подтверждения.');
    }
  };

  const toggleAutoAccept = (phone: string) => {
    setAccounts(prev => {
      const next = prev.map(acc => {
        if (acc.phone === phone) {
          const autoVal = !acc.autoAccept;
          localStorage.setItem(`auto_accept_${phone}`, String(autoVal));
          addLog(`Автопринятие заказов для ${phone}: ${autoVal ? 'ВКЛЮЧЕНО' : 'ВЫКЛЮЧЕНО'}`);
          return { ...acc, autoAccept: autoVal };
        }
        return acc;
      });
      return next;
    });
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

  const acceptOrder = (orderId: string) => {
    setOrders(prev => prev.map(o => o.id === orderId ? { ...o, status: 'ACCEPTED' } : o));
    addLog(`Заказ #${orderId} успешно принят вручную.`);
  };

  const formatPhone = (val: string) => {
    const raw = val.replace(/\D/g, '');
    if (raw.length <= 1) return raw;
    return `+7 (${raw.slice(1, 4)}) ${raw.slice(4, 7)}-${raw.slice(7, 9)}-${raw.slice(9, 11)}`;
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
                  placeholder="Например, 77770001122" 
                  value={phoneInput}
                  onChange={(e) => setPhoneInput(e.target.value.replace(/\D/g, ''))}
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
          <p style={{ marginTop: '4px' }}>Панель автоматизации заказов и расширения функций магазина</p>
        </div>
        <button 
          className="btn btn-primary" 
          onClick={() => {
            setPhoneInput('');
            setSmsInput('');
            setLoginStep('phone');
            setErrorMsg('');
            setView('login');
          }}
        >
          <span>+ Подключить магазин</span>
        </button>
      </header>

      {/* Main Grid */}
      <div className="grid-3" style={{ marginBottom: '24px' }}>
        {/* Stats 1 */}
        <div className="glass-card" style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <div style={{ fontSize: '32px', background: 'rgba(201, 44, 60, 0.1)', padding: '12px', borderRadius: '12px' }}>🏪</div>
          <div>
            <p>Активные кабинеты</p>
            <h2 style={{ fontSize: '28px', marginTop: '4px' }}>
              {accounts.filter(a => a.status === 'valid').length} / {accounts.length}
            </h2>
          </div>
        </div>
        {/* Stats 2 */}
        <div className="glass-card" style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <div style={{ fontSize: '32px', background: 'rgba(16, 185, 129, 0.1)', padding: '12px', borderRadius: '12px' }}>📦</div>
          <div>
            <p>Новые заказы сегодня</p>
            <h2 style={{ fontSize: '28px', marginTop: '4px' }}>
              {orders.filter(o => o.status === 'NEW').length}
            </h2>
          </div>
        </div>
        {/* Stats 3 */}
        <div className="glass-card" style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <div style={{ fontSize: '32px', background: 'rgba(245, 158, 11, 0.1)', padding: '12px', borderRadius: '12px' }}>⚡</div>
          <div>
            <p>Статус автопринятия</p>
            <h2 style={{ fontSize: '28px', marginTop: '4px' }}>
              {accounts.some(a => a.autoAccept) ? 'Активно' : 'Выключено'}
            </h2>
          </div>
        </div>
      </div>

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
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '4px' }}>
                      <label style={{ fontSize: '12px', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                        <input 
                          type="checkbox" 
                          checked={acc.autoAccept} 
                          onChange={() => toggleAutoAccept(acc.phone)}
                          style={{ accentColor: 'var(--primary)' }}
                        />
                        Автопринятие
                      </label>
                    </div>
                    
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
                <div key={index} style={{ color: log.includes('Успешный') || log.includes('АКТИВНА') ? 'var(--success)' : log.includes('ошибка') || log.includes('Ошибка') ? 'var(--error)' : 'var(--text-muted)' }}>
                  {log}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Right Side: Orders / Extended SaaS Features */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          <div className="glass-card" style={{ flexGrow: 1 }}>
            <h2 style={{ marginBottom: '16px' }}>Заказы (Kaspi Orders)</h2>
            
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              {orders.map((order) => (
                <div 
                  key={order.id} 
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
                    <span style={{ fontWeight: 600 }}>Заказ #{order.id}</span>
                    <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{order.date}</span>
                  </div>
                  
                  <div style={{ fontSize: '13px' }}>
                    <p style={{ color: '#fff', fontWeight: 500 }}>{order.customer}</p>
                    <p style={{ marginTop: '2px' }}>{order.items}</p>
                  </div>
                  
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '4px' }}>
                    <span style={{ color: 'var(--primary)', fontWeight: 700 }}>{order.price}</span>
                    <div>
                      {order.status === 'NEW' && (
                        <div style={{ display: 'flex', gap: '8px' }}>
                          <button className="btn btn-primary" style={{ padding: '6px 12px', fontSize: '12px' }} onClick={() => acceptOrder(order.id)}>
                            Принять
                          </button>
                        </div>
                      )}
                      {order.status === 'ACCEPTED' && <span className="badge badge-success">Принят</span>}
                      {order.status === 'COMPLETED' && <span className="badge" style={{ background: 'rgba(255,255,255,0.05)', color: '#aaa' }}>Выполнен</span>}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
