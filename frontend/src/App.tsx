import React, { useState, useEffect } from 'react';
import { apiFetch, setUnauthorizedHandler, API_URL, getWsUrl } from './api';
import { useWebSocket } from './hooks/useWebSocket';
import { useLogs } from './hooks/useLogs';
import { useAnalyticsWidgets } from './hooks/useAnalyticsWidgets';
import { useMcAuth } from './hooks/useMcAuth';
import { LogsTab } from './components/tabs/LogsTab';
import { OverviewTab } from './components/tabs/OverviewTab';
import { SettingsTab } from './components/tabs/SettingsTab';
import { MasterStockGroups } from './components/MasterStockGroups';
import { IntegrationsPublicApi } from './components/IntegrationsPublicApi';
import { PrintBatcher } from './components/PrintBatcher';
import { AuthModal } from './components/AuthModal';
import { StoreHub } from './components/StoreHub';
import { McAuthModal } from './components/McAuthModal';
import { Sidebar } from './components/Sidebar';
import { ContentHeader } from './components/ContentHeader';
import { PackagingTab } from './components/tabs/PackagingTab';
import { TransferTab } from './components/tabs/TransferTab';
import { ChatbotTab } from './components/tabs/ChatbotTab';
import { ProductsTab } from './components/tabs/ProductsTab';
import { DumpingTab } from './components/tabs/DumpingTab';
import type { ActiveTab, Product, Order, Transfer, ChatbotConfig, ChatbotStatus, DumpingConfig } from './types';

export default function App() {
  const [isLoaded, setIsLoaded] = useState(false);
  const [isAuthed, setIsAuthed] = useState<boolean>(() => !!localStorage.getItem('access_token'));


  const handleLogout = () => {
    localStorage.removeItem('access_token');
    setIsAuthed(false);
  };

  const [products, setProducts] = useState<Product[]>([]);
  const [fetchingProducts, setFetchingProducts] = useState(false);

  // Search, filter and pagination states
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<'active' | 'archive'>('active');
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [totalPages, setTotalPages] = useState(1);
  const [totalCount, setTotalCount] = useState(0);

  // Tabs navigation, packaging and transfer states
  const [activeTab, setActiveTab] = useState<ActiveTab>('stores');



  
  // Chatbot states
  const [chatbotConfig, setChatbotConfig] = useState<ChatbotConfig>({
    enabled: true,
    deepseek_api_key: '',
    tg_bot_token: '',
    admin_ids: [],
    use_tg_proxy: false,
    tg_proxy: '',
    merchant_id: ''
  });
  const [chatbotStatus, setChatbotStatus] = useState<ChatbotStatus>({
    enabled: true,
    has_session: false,
    cookie_alert_sent: false,
    blocked_chats_count: 0,
    blocked_chats: [],
    processed_chats_count: 0
  });
  const [orders, setOrders] = useState<Order[]>([]);
  const [fetchingOrders, setFetchingOrders] = useState(false);
  const [orderStatusFilter, setOrderStatusFilter] = useState<'NEW' | 'ASSEMBLY'>('ASSEMBLY');
  const [orderLimit, setOrderLimit] = useState(10);
  const [selectedOrderIds, setSelectedOrderIds] = useState<Set<string>>(new Set());
  const [packingAll, setPackingAll] = useState(false);

  const [transfers, setTransfers] = useState<Transfer[]>([]);
  const [fetchingTransfers, setFetchingTransfers] = useState(false);
  const [transferFilter, setTransferFilter] = useState<'ALL' | 'EXPRESS' | 'TODAY'>('ALL');

  const { logs, addLog, clearLogs } = useLogs();

  const [mcHasSession, setMcHasSession] = useState(false);
  const {
    emailInput, setEmailInput,
    passwordInput, setPasswordInput,
    mcAuthStep, mcAuthError,
    openModal: openMcAuthModal,
    closeModal: closeMcAuthModal,
    submitLogin: submitMcAuthLogin,
  } = useMcAuth(addLog, () => setMcHasSession(true));

  // Dumping & Analytics States
  const [dumpingConfigs, setDumpingConfigs] = useState<Record<string, DumpingConfig>>({});
  const [expandedSkus, setExpandedSkus] = useState<Set<string>>(new Set());
  const [expandedEditSkus, setExpandedEditSkus] = useState<Set<string>>(new Set());
  const [expandedFilterSkus, setExpandedFilterSkus] = useState<Set<string>>(new Set());
  const [selectedModes, setSelectedModes] = useState<Record<string, string>>({});
  const [isRunningDumping, setIsRunningDumping] = useState(false);
  const [nextRunSeconds, setNextRunSeconds] = useState<number | null>(null);
  const [dumpingOnlyFilter, setDumpingOnlyFilter] = useState(false);
  const [dumpingStatusFilter, setDumpingStatusFilter] = useState<string>('');

  const { isConnected: isWsConnected, lastEvent: wsEvent } = useWebSocket(getWsUrl());
  const { analytics, widgetData } = useAnalyticsWidgets(wsEvent);

  const checkMcStatus = async (verify = false) => {
    try {
      const res = await apiFetch(`${API_URL}/api/mc-status?verify=${verify}`);
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
      const res = await apiFetch(`${API_URL}/api/dumping/configs`);
      const data = await res.json();
      if (res.ok && data.success) {
        setDumpingConfigs(data.configs || {});
      }
    } catch {
      // Ignore
    }
  };

  const loadDumpingStatus = async () => {
    try {
      const res = await apiFetch(`${API_URL}/api/dumping/status`);
      const data = await res.json();
      if (res.ok && data.success) {
        setNextRunSeconds(data.next_run_seconds);
        setIsRunningDumping(data.is_running);
      }
    } catch {
      // Ignore
    }
  };

  const formatTime = (seconds: number | null) => {
    if (seconds === null) return '--:--';
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  };

  const saveDumpingConfig = async (
    sku: string, 
    minPrice: number, 
    maxPrice: number, 
    step: number, 
    enabled: boolean,
    mode: string = "TOP_1",
    targetPosition: number = 1,
    maxDeliveryDays: number | null = null,
    minSellerRating: number | null = null,
    sameCityOnly: boolean = false,
    blacklistedMerchants: string[] = []
  ) => {
    addLog(`Сохранение настроек демпинга для ${sku} (Режим: ${mode})...`);
    try {
      const res = await apiFetch(`${API_URL}/api/dumping/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sku,
          min_price: minPrice,
          max_price: maxPrice,
          dump_step: step,
          enabled,
          mode,
          target_position: targetPosition,
          max_delivery_days: maxDeliveryDays,
          min_seller_rating: minSellerRating,
          same_city_only: sameCityOnly,
          blacklisted_merchants: blacklistedMerchants
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        addLog(`Настройки демпинга для ${sku} успешно сохранены.`);
        loadDumpingConfigs();
        loadProducts(currentPage, statusFilter, searchQuery, pageSize, dumpingOnlyFilter, dumpingStatusFilter);
      } else {
        addLog(`Ошибка сохранения настроек: ${data.detail || 'Неизвестная ошибка'}`);
      }
    } catch {
      addLog('Сбой сети при сохранении настроек демпинга.');
    }
  };

  const saveProductUpdate = async (sku: string, price: number, stock: number, brand: string | undefined, status: string | undefined) => {
    addLog(`Сохранение параметров товара ${sku}...`);
    try {
      const res = await apiFetch(`${API_URL}/api/products/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sku,
          price,
          stock,
          brand,
          status
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        addLog(`Товар ${sku} успешно обновлен.`);
        loadProducts(currentPage, statusFilter, searchQuery, pageSize);
        // Закрываем панель редактирования
        setExpandedEditSkus(prev => {
          const next = new Set(prev);
          next.delete(sku);
          return next;
        });
      } else {
        addLog(`Ошибка обновления товара: ${data.detail || 'Неизвестная ошибка'}`);
      }
    } catch {
      addLog('Сетевая ошибка при обновлении товара.');
    }
  };

  const runDumpingCycle = async () => {
    setIsRunningDumping(true);
    addLog('Запуск автоматического расчета демпинга цен...');
    try {
      const res = await apiFetch(`${API_URL}/api/dumping/run`, { method: 'POST' });
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

  // Register global 401 handler
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setIsAuthed(false);
    });
  }, []);

  // Check initial session on mount
  useEffect(() => {
    (async () => {
      try {
        const res = await apiFetch(`${API_URL}/api/verify-session`, { method: 'POST' });
        if (res.ok) {
          const data = await res.json();
          if (data.valid) {
            setIsAuthed(true);
            addLog('Сессия REST API активна, токен подтвержден.');
            loadProducts(1, 'active', '', 10);
            checkMcStatus(true).then((valid) => {
              if (valid) {
                addLog('Куки Kaspi MC активны ✓');
              } else {
                addLog('⚠️ Куки Kaspi MC недействительны или отсутствуют. Требуется повторный вход.');
              }
            });
            loadDumpingConfigs();
            loadDumpingStatus();
          } else {
            localStorage.removeItem('access_token');
            setIsAuthed(false);
          }
        } else {
          localStorage.removeItem('access_token');
          setIsAuthed(false);
        }
      } catch {
        // Backend not available
      }
      setIsLoaded(true);
    })();
  }, []);

  // Poll dumping status & countdown
  useEffect(() => {
    if (!isAuthed) return;
    
    // Опрашиваем бэкенд каждые 10 секунд
    const syncInterval = setInterval(loadDumpingStatus, 10000);
    
    // Уменьшаем таймер локально каждую секунду
    const countdownInterval = setInterval(() => {
      setNextRunSeconds((prev) => {
        if (prev === null || prev <= 0) return prev;
        return prev - 1;
      });
    }, 1000);
    
    return () => {
      clearInterval(syncInterval);
      clearInterval(countdownInterval);
    };
  }, [isAuthed]);

  // React to WS telemetry events
  useEffect(() => {
    if (!wsEvent) return;
    if (wsEvent.type === 'terminal_log' && wsEvent.data?.message) {
      addLog(wsEvent.data.message);
    }
  }, [wsEvent]);

  // ── Data loaders (no phone parameter) ──────────────────

  const loadProducts = async (
    pageNum: number = currentPage,
    status: 'active' | 'archive' = statusFilter,
    search: string = searchQuery,
    pSize: number = pageSize,
    dumpingOnly: boolean = dumpingOnlyFilter,
    statusFilterParam: string = dumpingStatusFilter
  ) => {
    setFetchingProducts(true);
    addLog(`Загрузка товаров (Стр: ${pageNum}, Лимит: ${pSize}, Статус: ${status}, Демпинг: ${dumpingOnly}, Фильтр статуса: ${statusFilterParam || 'Все'})...`);
    try {
      const res = await apiFetch(
        `${API_URL}/api/products?page=${pageNum}&status=${status}&search=${encodeURIComponent(search)}&page_size=${pSize}&dumping_only=${dumpingOnly}&dumping_status_filter=${encodeURIComponent(statusFilterParam)}`
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
      const res = await apiFetch(`${API_URL}/api/products/refresh`, { method: 'POST' });
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
      const res = await apiFetch(url);
      const data = await res.json();
      if (res.ok && data.success) {
        setOrders(data.orders);
        const total = data.orders.length;
        const assembledCount = data.orders.filter((o: any) => o.assembled).length;
        const transmittedCount = data.orders.filter((o: any) => o.courierTransmissionDate !== null).length;
        const types = Array.from(new Set(data.orders.map((o: any) => o.deliveryType))).join(', ');
        addLog(`Загружено ${total} заказов. Из них собранных (assembled): ${assembledCount}, переданных курьеру: ${transmittedCount}. Типы доставки: [${types}]`);
        // Логируем SKU для каждой позиции заказа
        for (const order of data.orders) {
          if (order.entries && order.entries.length > 0) {
            for (const entry of order.entries) {
              const mSku = entry.merchantSku || '—';
              const kSku = entry.kaspiSku || '—';
              addLog(`  📦 Заказ #${order.id} → ${entry.productName}: Merchant SKU: ${mSku}, Kaspi SKU: ${kSku}`);
            }
          }
        }
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
      const res = await apiFetch(`${API_URL}/api/orders/pack`, {
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
      const res = await apiFetch(`${API_URL}/api/orders/pack-all`, { method: 'POST' });
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
          const res = await apiFetch(`${API_URL}/api/orders/pack`, {
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
      const res = await apiFetch(`${API_URL}/api/transfers?filter=${filter}`);
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

  const loadChatbotConfig = async () => {
    try {
      const res = await apiFetch(`${API_URL}/api/chatbot/config`);
      const data = await res.json();
      if (res.ok && data.success) {
        setChatbotConfig(data.config);
      }
    } catch (err) {
      console.error("Chatbot config fetch error:", err);
    }
  };

  const loadChatbotStatus = async () => {
    try {
      const res = await apiFetch(`${API_URL}/api/chatbot/status`);
      const data = await res.json();
      if (res.ok && data.success) {
        setChatbotStatus(data);
      }
    } catch (err) {
      console.error("Chatbot status fetch error:", err);
    }
  };

  const saveChatbotConfig = async (newCfg: Partial<typeof chatbotConfig>) => {
    addLog('Сохранение конфигурации ИИ-автоответчика...');
    try {
      const res = await apiFetch(`${API_URL}/api/chatbot/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newCfg)
      });
      const data = await res.json();
      if (res.ok && data.success) {
        addLog(data.message);
        loadChatbotConfig();
        loadChatbotStatus();
      } else {
        addLog(`Ошибка сохранения конфигурации чат-бота: ${data.detail || 'Ошибка'}`);
      }
    } catch {
      addLog('Сетевая ошибка при сохранении конфига чат-бота.');
    }
  };

  const unblockChatbotGroup = async (groupId: string) => {
    addLog(`Разблокировка чата ${groupId} для ИИ...`);
    try {
      const res = await apiFetch(`${API_URL}/api/chatbot/unblock`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ group_id: groupId })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        addLog(data.message);
        loadChatbotStatus();
      }
    } catch {
      addLog('Ошибка при разблокировке чата.');
    }
  };


  const handleTabClick = (tab: ActiveTab) => {
    setActiveTab(tab);
    if (tab === 'products') {
      loadProducts(1, statusFilter, searchQuery, pageSize, false);
      loadDumpingConfigs();
    } else if (tab === 'dumping') {
      loadProducts(1, statusFilter, searchQuery, pageSize, dumpingOnlyFilter);
      loadDumpingConfigs();
    } else if (tab === 'chatbot') {
      loadChatbotConfig();
      loadChatbotStatus();
    } else if (tab === 'packaging') {
      loadOrders(orderStatusFilter, orderLimit);
    } else if (tab === 'transfer') {
      loadTransfers(transferFilter);
    }
  };

  // ── UI helpers ─────────────────────────────────────────

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setCurrentPage(1);
    loadProducts(1, statusFilter, searchQuery, pageSize, activeTab === 'dumping' ? dumpingOnlyFilter : false);
  };

  const handleStatusFilterChange = (status: 'active' | 'archive') => {
    setStatusFilter(status);
    setCurrentPage(1);
    loadProducts(1, status, searchQuery, pageSize, activeTab === 'dumping' ? dumpingOnlyFilter : false);
  };

  const handlePageChange = (page: number) => {
    if (page < 1 || page > totalPages) return;
    setCurrentPage(page);
    loadProducts(page, statusFilter, searchQuery, pageSize, activeTab === 'dumping' ? dumpingOnlyFilter : false);
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

  // ── Auth Modal Screen (Login / Register) ───────────────────
  if (!isAuthed) {
    return <AuthModal onSuccess={() => setIsAuthed(true)} />;
  }


  // ── Dashboard Screen ───────────────────────────────────
  // ── Dashboard Screen ───────────────────────────────────
  return (
    <div className="app-shell">
      {/* ── STITCH MCP SIDEBAR ─────────────────────────────────── */}
      <Sidebar
        activeTab={activeTab}
        onSelectTab={handleTabClick}
        onLogout={handleLogout}
        totalCount={totalCount}
        chatbotEnabled={chatbotStatus.enabled}
        ordersCount={orders.length}
        transfersCount={transfers.length}
        isWsConnected={isWsConnected}
      />

      {/* ── MAIN CONTENT AREA ─────────────────────────────────── */}
      <main className="main-content">
        {/* TOP CONTENT HEADER */}
        <ContentHeader activeTab={activeTab} onLogout={handleLogout} />

        {/* 🏪 TAB: STORES */}
        {activeTab === 'stores' && <StoreHub />}

        {/* 📊 TAB: OVERVIEW */}
        {activeTab === 'overview' && (
          <OverviewTab
            analytics={analytics}
            widgetData={widgetData}
            logs={logs}
            isWsConnected={isWsConnected}
            addLog={addLog}
            mcHasSession={mcHasSession}
            onOpenMcAuth={() => openMcAuthModal()}
            onOpenProducts={() => {
              setActiveTab('products');
              loadProducts(1, statusFilter, searchQuery, pageSize);
            }}
            onOpenDumping={() => {
              setActiveTab('dumping');
              loadDumpingConfigs();
            }}
            onOpenPackaging={() => {
              setActiveTab('packaging');
              loadOrders('ASSEMBLY', 10);
            }}
            onOpenLogs={() => setActiveTab('logs')}
          />
        )}

        {/* 📜 TAB: LOGS / TERMINAL */}
        {activeTab === 'logs' && (
          <LogsTab logs={logs} isConnected={isWsConnected} onClear={clearLogs} />
        )}

        {/* ⚙️ TAB: SETTINGS & SESSION */}
        {activeTab === 'settings' && (
          <SettingsTab
            mcHasSession={mcHasSession}
            addLog={addLog}
            onOpenMcAuth={() => openMcAuthModal()}
            onCheckMcStatus={() => checkMcStatus(true)}
          />
        )}

        {/* 🏷️ TAB 1, ⚡ TAB 2, 📦 TAB 3, 🚚 TAB 4: RENDER IN GLASS CARD */}
        {(activeTab === 'products' || activeTab === 'dumping' || activeTab === 'packaging' || activeTab === 'transfer') && (
          <div className="glass-card" style={{ flexGrow: 1, display: 'flex', flexDirection: 'column' }}>
            {/* TAB 1: PRODUCT MANAGEMENT */}
            {activeTab === 'products' && (
              <ProductsTab
                totalCount={totalCount}
                pageSize={pageSize}
                onPageSizeChange={(pSize) => {
                  setPageSize(pSize);
                  setCurrentPage(1);
                  loadProducts(1, statusFilter, searchQuery, pSize);
                }}
                statusFilter={statusFilter}
                onStatusFilterChange={handleStatusFilterChange}
                searchQuery={searchQuery}
                onSearchQueryChange={setSearchQuery}
                onSearchSubmit={handleSearchSubmit}
                onRefresh={refreshProducts}
                fetchingProducts={fetchingProducts}
                products={products}
                expandedEditSkus={expandedEditSkus}
                onToggleEdit={(sku) => {
                  setExpandedEditSkus(prev => {
                    const next = new Set(prev);
                    if (next.has(sku)) {
                      next.delete(sku);
                    } else {
                      next.add(sku);
                    }
                    return next;
                  });
                }}
                onSaveProductUpdate={saveProductUpdate}
                currentPage={currentPage}
                totalPages={totalPages}
                onPageChange={handlePageChange}
              />
            )}

            {/* TAB 4: AUTOMATIC PRICE DUMPING */}
            {activeTab === 'dumping' && (
              <DumpingTab
                products={products}
                fetchingProducts={fetchingProducts}
                totalCount={totalCount}
                nextRunSeconds={nextRunSeconds}
                formatTime={formatTime}
                isRunningDumping={isRunningDumping}
                onRunDumpingCycle={runDumpingCycle}
                searchQuery={searchQuery}
                onSearchQueryChange={setSearchQuery}
                onSearchSubmit={handleSearchSubmit}
                dumpingOnlyFilter={dumpingOnlyFilter}
                onDumpingOnlyFilterChange={(val) => {
                  setDumpingOnlyFilter(val);
                  setCurrentPage(1);
                  loadProducts(1, statusFilter, searchQuery, pageSize, val, dumpingStatusFilter);
                }}
                dumpingStatusFilter={dumpingStatusFilter}
                onDumpingStatusFilterChange={(val) => {
                  setDumpingStatusFilter(val);
                  setCurrentPage(1);
                  loadProducts(1, statusFilter, searchQuery, pageSize, dumpingOnlyFilter, val);
                }}
                onRefreshConfigs={loadDumpingConfigs}
                dumpingConfigs={dumpingConfigs}
                expandedSkus={expandedSkus}
                onToggleExpanded={(sku) => {
                  const next = new Set(expandedSkus);
                  if (next.has(sku)) {
                    next.delete(sku);
                  } else {
                    next.add(sku);
                  }
                  setExpandedSkus(next);
                }}
                expandedFilterSkus={expandedFilterSkus}
                onToggleFiltersExpanded={(sku) => {
                  const next = new Set(expandedFilterSkus);
                  if (next.has(sku)) {
                    next.delete(sku);
                  } else {
                    next.add(sku);
                  }
                  setExpandedFilterSkus(next);
                }}
                selectedModes={selectedModes}
                onModeChange={(sku, mode) => {
                  setSelectedModes(prev => ({ ...prev, [sku]: mode }));
                }}
                onSaveDumpingConfig={saveDumpingConfig}
                currentPage={currentPage}
                totalPages={totalPages}
                onPageChange={handlePageChange}
              />
            )}

            {/* TAB 2: PACKAGING / ORDERS */}
            {activeTab === 'packaging' && (
              <PackagingTab
                filteredOrders={filteredOrders}
                fetchingOrders={fetchingOrders}
                orderLimit={orderLimit}
                onOrderLimitChange={(limit) => {
                  setOrderLimit(limit);
                  loadOrders(orderStatusFilter, limit);
                }}
                orderStatusFilter={orderStatusFilter}
                onOrderStatusFilterChange={(status) => {
                  setOrderStatusFilter(status);
                  loadOrders(status, orderLimit);
                }}
                pkgDeliveryFilter={pkgDeliveryFilter}
                onPkgDeliveryFilterChange={setPkgDeliveryFilter}
                selectedOrderIds={selectedOrderIds}
                onToggleSelectAll={toggleSelectAll}
                onToggleOrderSelection={toggleOrderSelection}
                onPackSelectedOrders={packSelectedOrders}
                onPackAllOrders={packAllOrders}
                onPackOrder={packOrder}
                packingAll={packingAll}
                onRefresh={() => loadOrders(orderStatusFilter, orderLimit)}
              />
            )}

            {/* TAB 3: TRANSFER */}
            {activeTab === 'transfer' && (
              <TransferTab
                transfers={transfers}
                fetchingTransfers={fetchingTransfers}
                transferFilter={transferFilter}
                onTransferFilterChange={(filter) => {
                  setTransferFilter(filter);
                  loadTransfers(filter);
                }}
                onRefresh={() => loadTransfers(transferFilter)}
              />
            )}
          </div>
        )}

        {activeTab === 'chatbot' && (
          <ChatbotTab
            chatbotConfig={chatbotConfig}
            chatbotStatus={chatbotStatus}
            onSaveChatbotConfig={saveChatbotConfig}
            onUnblockChatbotGroup={unblockChatbotGroup}
          />
        )}

        {/* 🔗 TAB: MASTER STOCK GROUPS */}
        {activeTab === 'stock-groups' && (
          <MasterStockGroups />
        )}

        {/* 🔑 TAB: PUBLIC API & WEBHOOKS */}
        {activeTab === 'public-api' && (
          <IntegrationsPublicApi />
        )}

        {/* 🖨️ TAB: PRINT-BATCHER ENGINE */}
        {activeTab === 'print-batcher' && (
          <PrintBatcher />
        )}
      </main>
      <McAuthModal
        mcAuthStep={mcAuthStep}
        mcAuthError={mcAuthError}
        emailInput={emailInput}
        onEmailChange={setEmailInput}
        passwordInput={passwordInput}
        onPasswordChange={setPasswordInput}
        onClose={closeMcAuthModal}
        onSubmit={submitMcAuthLogin}
      />
    </div>
  );
}
