import React, { useState, useEffect } from 'react';
import { apiFetch, setUnauthorizedHandler, API_URL, getWsUrl } from './api';
import { useWebSocket } from './hooks/useWebSocket';
import { useLogs } from './hooks/useLogs';
import { useAnalyticsWidgets } from './hooks/useAnalyticsWidgets';
import { LogsTab } from './components/tabs/LogsTab';
import { OverviewTab } from './components/tabs/OverviewTab';
import { SettingsTab } from './components/tabs/SettingsTab';
import { PriceHistoryChart } from './components/PriceHistoryChart';
import { MasterStockGroups } from './components/MasterStockGroups';
import { IntegrationsPublicApi } from './components/IntegrationsPublicApi';
import { PrintBatcher } from './components/PrintBatcher';
import { AuthModal } from './components/AuthModal';
import { StoreHub } from './components/StoreHub';
import { Sidebar } from './components/Sidebar';
import { ContentHeader } from './components/ContentHeader';
import { Pagination } from './components/shared/Pagination';
import { ProductCardHeader } from './components/shared/ProductCardHeader';
import { PackagingTab } from './components/tabs/PackagingTab';
import { TransferTab } from './components/tabs/TransferTab';
import { ChatbotTab } from './components/tabs/ChatbotTab';
import { ProductsTab } from './components/tabs/ProductsTab';
import type { ActiveTab, Product, Order, Transfer, ChatbotConfig, ChatbotStatus } from './types';

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

  // Dropdown menu state
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);

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

  const [emailInput, setEmailInput] = useState('');
  const [passwordInput, setPasswordInput] = useState('');
  const [mcAuthStep, setMcAuthStep] = useState<'idle' | 'credentials' | 'loading' | 'success'>('idle');
  const [mcHasSession, setMcHasSession] = useState(false);
  const [mcAuthError, setMcAuthError] = useState('');

  // Dumping & Analytics States
  const [dumpingConfigs, setDumpingConfigs] = useState<Record<string, {
    min_price: number;
    max_price: number;
    dump_step: number;
    enabled: boolean;
    mode?: string;
    target_position?: number;
    max_delivery_days?: number | null;
    min_seller_rating?: number | null;
    same_city_only?: boolean;
    blacklisted_merchants?: string[];
  }>>({});
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
            onOpenMcAuth={() => setMcAuthStep('credentials')}
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
            onOpenMcAuth={() => setMcAuthStep('credentials')}
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
              <>
                <div style={{ marginBottom: '20px' }}>
                  <PriceHistoryChart
                    data={[
                      { time: '10:00', myPrice: 1650, bestCompetitor: 1640 },
                      { time: '11:00', myPrice: 1630, bestCompetitor: 1630 },
                      { time: '12:00', myPrice: 1600, bestCompetitor: 1590 },
                      { time: '13:00', myPrice: 1580, bestCompetitor: 1580 },
                      { time: '14:00', myPrice: 1550, bestCompetitor: 1540 },
                      { time: '15:00', myPrice: 1480, bestCompetitor: 1470 },
                    ]}
                  />
                </div>

                {/* Header with Search, Filter & Page Size */}
                <div style={{ borderBottom: '1px solid rgba(255,255,255,0.06)', paddingBottom: '16px', marginBottom: '20px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                    <h2 style={{ fontSize: '16px' }}>
                      ⚡ Настройка авто-демпинга ({totalCount ? `${totalCount} поз.` : '0 поз.'})
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
                          Следующий запуск через: <strong style={{ color: 'var(--success)', fontFamily: 'monospace' }}>{formatTime(nextRunSeconds)}</strong>
                        </div>
                      )}
                      
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
                  </div>

                  {/* Search Bar */}
                  <form onSubmit={handleSearchSubmit} style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                    <input 
                      type="text" 
                      className="form-input" 
                      placeholder="Поиск по артикулу / названию..."
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
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
                        onClick={() => {
                          setDumpingOnlyFilter(false);
                          setCurrentPage(1);
                          loadProducts(1, statusFilter, searchQuery, pageSize, false, dumpingStatusFilter);
                        }}
                      >
                        Все товары
                      </button>
                      <button 
                        type="button"
                        className="btn" 
                        style={{ 
                          background: dumpingOnlyFilter ? 'var(--primary)' : 'transparent', 
                          padding: '6px 14px', fontSize: '12px', borderRadius: '6px',
                          color: '#fff', border: 'none', cursor: 'pointer', transition: 'all 0.2s ease'
                        }}
                        onClick={() => {
                          setDumpingOnlyFilter(true);
                          setCurrentPage(1);
                          loadProducts(1, statusFilter, searchQuery, pageSize, true, dumpingStatusFilter);
                        }}
                      >
                        Активный демпинг
                      </button>
                    </div>

                    {/* Dumping Status Filter Selector */}
                    <select
                      value={dumpingStatusFilter}
                      onChange={(e) => {
                        const val = e.target.value;
                        setDumpingStatusFilter(val);
                        setCurrentPage(1);
                        loadProducts(1, statusFilter, searchQuery, pageSize, dumpingOnlyFilter, val);
                      }}
                      className="form-input"
                      style={{ background: 'var(--input-bg)', color: '#fff', border: '1px solid var(--input-border)', height: '38px', borderRadius: '8px', cursor: 'pointer', fontSize: '12px' }}
                    >
                      <option value="">Все статусы</option>
                      <option value="WINNING_1ST">👑 1-е место</option>
                      <option value="HOLDING_2ND">🥈 2-е место</option>
                      <option value="TARGET_POS_REACHED">🎯 На целевой позиции</option>
                      <option value="AT_MIN_PRICE">⚠️ Достигнут минималки</option>
                      <option value="MAX_PRICE_SINGLE">💎 Max Price (Единственный)</option>
                      <option value="OFF">⏸️ Отключен</option>
                    </select>

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
                        const isFiltersExpanded = expandedFilterSkus.has(sku);
                        const cfg = dumpingConfigs[sku] || { 
                          min_price: 0, max_price: 0, dump_step: 10, enabled: false,
                          mode: 'TOP_1', target_position: 1, max_delivery_days: null,
                          min_seller_rating: null, same_city_only: false, blacklisted_merchants: []
                        };
                        const hasConfig = !!dumpingConfigs[sku];
                        const isDumpingActive = hasConfig && cfg.enabled && cfg.mode !== 'OFF';
                        const dStatus = product.dumping_status || (isDumpingActive ? 'WINNING_1ST' : 'OFF');

                        const getStatusBadge = (status: string) => {
                          switch (status) {
                            case 'WINNING_1ST':
                              return <span className="badge" style={{ background: 'rgba(16, 185, 129, 0.15)', color: '#10b981', border: '1px solid rgba(16, 185, 129, 0.3)' }}>👑 1-е место</span>;
                            case 'HOLDING_2ND':
                              return <span className="badge" style={{ background: 'rgba(59, 130, 246, 0.15)', color: '#3b82f6', border: '1px solid rgba(59, 130, 246, 0.3)' }}>🥈 2-е место</span>;
                            case 'TARGET_POS_REACHED':
                              return <span className="badge" style={{ background: 'rgba(168, 85, 247, 0.15)', color: '#a855f7', border: '1px solid rgba(168, 85, 247, 0.3)' }}>🎯 На целевой позиции</span>;
                            case 'AT_MIN_PRICE':
                              return <span className="badge" style={{ background: 'rgba(245, 158, 11, 0.15)', color: '#f59e0b', border: '1px solid rgba(245, 158, 11, 0.3)' }}>⚠️ Достигнут минималки</span>;
                            case 'MAX_PRICE_SINGLE':
                              return <span className="badge" style={{ background: 'rgba(236, 72, 153, 0.15)', color: '#ec4899', border: '1px solid rgba(236, 72, 153, 0.3)' }}>💎 Max Price (Единственный)</span>;
                            default:
                              return <span className="badge" style={{ background: 'rgba(255, 255, 255, 0.05)', color: '#888', border: '1px solid rgba(255, 255, 255, 0.1)' }}>⏸️ Отключен</span>;
                          }
                        };

                        return (
                          <div 
                            key={product.id} 
                            className="order-card"
                            style={{ padding: '16px', margin: 0 }}
                          >
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                              <div style={{ maxWidth: '70%' }}>
                                <ProductCardHeader name={product.name} sku={product.sku} kaspiSku={product.kaspiSku} merchantSku={product.merchantSku} />

                                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginTop: '8px' }}>
                                  {getStatusBadge(dStatus)}
                                  
                                  <span className="badge" style={{ background: 'rgba(255,255,255,0.05)', color: '#ccc', border: '1px solid rgba(255,255,255,0.08)' }}>
                                    Режим: {cfg.mode || 'TOP_1'}
                                  </span>
                                </div>
                              </div>

                              <div style={{ textAlign: 'right', display: 'flex', alignItems: 'center', gap: '16px' }}>
                                <div>
                                  <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Текущая цена:</div>
                                  <div style={{ fontSize: '16px', fontWeight: 700, color: '#fff' }}>
                                    {product.price && product.price !== "0 ₸" ? product.price : (product.price_num ? `${product.price_num} ₸` : '—')}
                                  </div>
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
                                  ⚙️ Стратегия демпинга и Лимиты цен
                                </h4>
                                
                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '12px' }}>
                                  <div>
                                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Режим демпинга</label>
                                    <select 
                                      id={`mode-${sku}`}
                                      className="form-input"
                                      value={selectedModes[sku] ?? (cfg.mode || 'TOP_1')}
                                      onChange={(e) => {
                                        const newMode = e.target.value;
                                        setSelectedModes(prev => ({ ...prev, [sku]: newMode }));
                                      }}
                                      style={{ background: 'var(--input-bg)', color: '#fff', border: '1px solid var(--input-border)', height: '38px', borderRadius: '8px' }}
                                    >
                                      <option value="TOP_1">🏆 1-е место (Демпинг с шагом)</option>
                                      <option value="TOP_2">🥈 2-е место (Демпинг от 2-й цены)</option>
                                      <option value="MATCH_FIRST">🎯 Прижиматься к 1-му месту (-1 ₸)</option>
                                      <option value="TARGET_POSITION">📍 N-е место (Целевая позиция)</option>
                                      <option value="OFF">⏸️ Отключен</option>
                                    </select>
                                  </div>

                                  {(selectedModes[sku] ?? (cfg.mode || 'TOP_1')) === 'TARGET_POSITION' && (
                                    <div>
                                      <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Целевая позиция (N)</label>
                                      <input 
                                        type="number" 
                                        id={`target_pos-${sku}`} 
                                        className="form-input" 
                                        defaultValue={cfg.target_position || 1}
                                        placeholder="Позиция 1..N"
                                      />
                                    </div>
                                  )}

                                  <div>
                                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Минимальная цена (₸)</label>
                                    <input 
                                      type="number" 
                                      id={`min-${sku}`} 
                                      className="form-input" 
                                      defaultValue={cfg.min_price || ''}
                                      placeholder="Стоп-цена"
                                    />
                                  </div>
                                  <div>
                                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Максимальная цена (₸)</label>
                                    <input 
                                      type="number" 
                                      id={`max-${sku}`} 
                                      className="form-input" 
                                      defaultValue={cfg.max_price || ''}
                                      placeholder="Базовая / RRP"
                                    />
                                  </div>
                                  <div>
                                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Шаг демпинга (₸)</label>
                                    <input 
                                      type="number" 
                                      id={`step-${sku}`} 
                                      className="form-input" 
                                      defaultValue={cfg.dump_step ?? 10}
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
                                    onClick={() => {
                                      const next = new Set(expandedFilterSkus);
                                      if (next.has(sku)) {
                                        next.delete(sku);
                                      } else {
                                        next.add(sku);
                                      }
                                      setExpandedFilterSkus(next);
                                    }}
                                  >
                                    <span>🛡️ Фильтры конкурентов (доставка, рейтинг, блэклист)</span>
                                    <span style={{ fontSize: '12px', fontWeight: 'bold' }}>{isFiltersExpanded ? '▲' : '•••'}</span>
                                  </button>

                                  {isFiltersExpanded && (
                                    <div style={{ marginTop: '12px', display: 'flex', flexDirection: 'column', gap: '12px', background: 'rgba(0,0,0,0.2)', padding: '12px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.04)' }}>
                                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '12px' }}>
                                        <div>
                                          <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Макс. дней доставки (предзаказ)</label>
                                          <input 
                                            type="number" 
                                            id={`max_deliv-${sku}`} 
                                            className="form-input" 
                                            defaultValue={cfg.max_delivery_days ?? ''}
                                            placeholder="Например, 5 дней"
                                          />
                                        </div>

                                        <div>
                                          <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Мин. рейтинг конкурента</label>
                                          <input 
                                            type="number" 
                                            step="0.1"
                                            id={`min_rating-${sku}`} 
                                            className="form-input" 
                                            defaultValue={cfg.min_seller_rating ?? ''}
                                            placeholder="Например, 4.0"
                                          />
                                        </div>

                                        <div style={{ gridColumn: 'span 2' }}>
                                          <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Черный список магазинов (через запятую)</label>
                                          <input 
                                            type="text" 
                                            id={`blacklist-${sku}`} 
                                            className="form-input" 
                                            defaultValue={(cfg.blacklisted_merchants || []).join(', ')}
                                            placeholder="ID или имя продавца"
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
                                        Учитывать только свой город
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
                                    <strong>Включить демпинг для этого SKU</strong>
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
                                      const stepVal = parseFloat((document.getElementById(`step-${sku}`) as HTMLInputElement).value) || 10;
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

                                      saveDumpingConfig(
                                        sku, minVal, maxVal, stepVal, enabledVal,
                                        modeVal, targetPosVal, maxDelivVal, minRatingVal, sameCityVal, blacklistArr
                                      );
                                    }}
                                  >
                                    💾 Сохранить параметры демпинга
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
                  onPageChange={handlePageChange}
                />
              </>
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



        {/* Dummy statement to reference unused variables for strict TypeScript compliance */}
        {(() => {
          if (false as boolean) {
            console.log(isAuthed, openMenuId, setOpenMenuId, setFetchingTransfers, setFetchingOrders, setPackingAll);
          }
          return null;
        })()}
      </main>
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

            {mcAuthStep === 'credentials' && (
              <div>
                <p style={{ fontSize: '13px', marginBottom: '16px', color: 'var(--text-muted)' }}>
                  Введите email и пароль от вашего кабинета Kaspi Merchant. Мы запустим браузер для входа.
                </p>
                <div style={{ marginBottom: '16px' }}>
                  <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>Email</label>
                  <input
                    type="email"
                    className="form-input"
                    placeholder="you@example.com"
                    value={emailInput}
                    onChange={(e) => setEmailInput(e.target.value)}
                  />
                </div>
                <div style={{ marginBottom: '16px' }}>
                  <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>Пароль</label>
                  <input
                    type="password"
                    className="form-input"
                    placeholder="••••••••"
                    value={passwordInput}
                    onChange={(e) => setPasswordInput(e.target.value)}
                  />
                </div>
                <button
                  className="btn btn-primary"
                  style={{ width: '100%' }}
                  onClick={async () => {
                    setMcAuthError('');
                    setMcAuthStep('loading');
                    addLog('Запуск авторизации Kaspi MC для ' + emailInput);
                    try {
                      const res = await fetch(`${API_URL}/api/login`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ email: emailInput, password: passwordInput })
                      });
                      const data = await res.json();
                      if (res.ok && data.success) {
                        setMcAuthStep('success');
                        setMcHasSession(true);
                        addLog('Авторизация Kaspi MC успешна! Сессионные куки сохранены.');
                      } else {
                        setMcAuthError(data.detail || 'Не удалось войти. Проверьте email и пароль.');
                        setMcAuthStep('credentials');
                      }
                    } catch {
                      setMcAuthError('Ошибка сети.');
                      setMcAuthStep('credentials');
                    }
                  }}
                >
                  Войти
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
