import React, { useState, useEffect } from 'react';
import { apiFetch, setUnauthorizedHandler, API_URL, getWsUrl } from './api';
import { useWebSocket } from './hooks/useWebSocket';
import { BuyBoxGauge } from './components/BuyBoxGauge';
import { SavedMarginCard } from './components/SavedMarginCard';
import { LiveTerminalStream } from './components/LiveTerminalStream';
import { PriceHistoryChart } from './components/PriceHistoryChart';
import { PriceRadarWidget } from './components/PriceRadarWidget';
import { AiChatSentimentWidget } from './components/AiChatSentimentWidget';
import { MasterStockGroups } from './components/MasterStockGroups';
import { IntegrationsPublicApi } from './components/IntegrationsPublicApi';
import { PrintBatcher } from './components/PrintBatcher';
import { AuthModal } from './components/AuthModal';
import { StoreHub } from './components/StoreHub';




interface Product {
  id: string;
  name: string;
  sku: string;
  merchantSku?: string;
  kaspiSku?: string;
  price: string;
  status: string;
  url: string;
  price_num?: number;
  stock?: number;
  brand?: string;
  dumping_status?: string;
  dumping_config?: any;
}

interface OrderEntry {
  entryId: string;
  productName: string;
  sku: string;
  merchantSku?: string;
  kaspiSku?: string;
  productUrl?: string;
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
  const [pageInputText, setPageInputText] = useState('1');

  // Dropdown menu state
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);

  // Tabs navigation, packaging and transfer states
  const [activeTab, setActiveTab] = useState<'stores' | 'overview' | 'products' | 'dumping' | 'chatbot' | 'stock-groups' | 'public-api' | 'print-batcher' | 'packaging' | 'transfer' | 'logs' | 'settings'>('stores');



  
  // Chatbot states
  const [chatbotConfig, setChatbotConfig] = useState<{
    enabled: boolean;
    deepseek_api_key: string;
    tg_bot_token: string;
    admin_ids: string[];
    use_tg_proxy: boolean;
    tg_proxy: string;
    merchant_id: string;
  }>({
    enabled: true,
    deepseek_api_key: '',
    tg_bot_token: '',
    admin_ids: [],
    use_tg_proxy: false,
    tg_proxy: '',
    merchant_id: ''
  });
  const [chatbotStatus, setChatbotStatus] = useState<{
    enabled: boolean;
    has_session: boolean;
    cookie_alert_sent: boolean;
    blocked_chats_count: number;
    blocked_chats: { group_id: string; reason: string }[];
    processed_chats_count: number;
  }>({
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

  const [logs, setLogs] = useState<string[]>([
    'Система автоматизации инициализирована.',
    'Ожидание подключения Kaspi API...',
  ]);

  const [phoneInput, setPhoneInput] = useState('+7');
  const [smsInput, setSmsInput] = useState('');
  const [mcAuthStep, setMcAuthStep] = useState<'idle' | 'phone' | 'sms' | 'loading' | 'success'>('idle');
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
  const [analytics, setAnalytics] = useState<{
    buy_box_share: number;
    winning_count: number;
    total_scanned: number;
    total_saved_margin: number;
  }>({
    buy_box_share: 80.0,
    winning_count: 8,
    total_scanned: 10,
    total_saved_margin: 145000
  });

  const [widgetData, setWidgetData] = useState<any>(null);
  const [dumpingOnlyFilter, setDumpingOnlyFilter] = useState(false);
  const [dumpingStatusFilter, setDumpingStatusFilter] = useState<string>('');

  const { isConnected: isWsConnected, lastEvent: wsEvent } = useWebSocket(getWsUrl());

  useEffect(() => {
    const fetchWidgets = async () => {
      try {
        const res = await apiFetch(`${API_URL}/api/analytics/widgets`);
        const data = await res.json();
        if (res.ok && data.success) {
          setWidgetData(data);
          if (data.analytics) {
            setAnalytics(prev => ({
              ...prev,
              buy_box_share: data.analytics.buy_box_share ?? prev.buy_box_share,
              winning_count: data.analytics.winning_count ?? prev.winning_count,
              total_scanned: data.analytics.total_scanned ?? prev.total_scanned,
              total_saved_margin: data.analytics.total_saved_margin ?? prev.total_saved_margin,
            }));
          }
        }
      } catch { /* ignore */ }
    };
    fetchWidgets();
    const interval = setInterval(fetchWidgets, 10000);
    return () => clearInterval(interval);
  }, []);

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

  const addLog = (message: string) => {
    const timestamp = new Date().toLocaleTimeString();
    setLogs((prev) => [`[${timestamp}] ${message}`, ...prev.slice(0, 49)]);
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
                addLog('⚠️ Куки Kaspi MC недействительны или отсутствуют. Требуется SMS-авторизация.');
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
    } else if (wsEvent.type === 'analytics_update' && wsEvent.data) {
      setAnalytics(wsEvent.data);
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


  // ── UI helpers ─────────────────────────────────────────

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setCurrentPage(1);
    setPageInputText('1');
    loadProducts(1, statusFilter, searchQuery, pageSize, activeTab === 'dumping' ? dumpingOnlyFilter : false);
  };

  const handleStatusFilterChange = (status: 'active' | 'archive') => {
    setStatusFilter(status);
    setCurrentPage(1);
    setPageInputText('1');
    loadProducts(1, status, searchQuery, pageSize, activeTab === 'dumping' ? dumpingOnlyFilter : false);
  };

  const handlePageChange = (page: number) => {
    if (page < 1 || page > totalPages) return;
    setCurrentPage(page);
    setPageInputText(String(page));
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
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="sidebar-logo-icon">K</div>
          <div>
            <div className="sidebar-brand-title">Kaspi SaaS</div>
            <div className="sidebar-brand-subtitle">Lumina Automation</div>
          </div>
        </div>

        <div className="sidebar-nav">
          <div className="sidebar-group">
            <div className="sidebar-group-title">Главное</div>
            
            <button 
              className={`sidebar-item ${activeTab === 'stores' ? 'active' : ''}`}
              onClick={() => setActiveTab('stores')}
            >
              <div className="sidebar-item-left">
                <span className="sidebar-icon">🏪</span>
                <span>Магазины Kaspi</span>
              </div>
              <span className="sidebar-pill" style={{ color: '#34d399' }}>Multi-Store</span>
            </button>

            <button 
              className={`sidebar-item ${activeTab === 'overview' ? 'active' : ''}`}
              onClick={() => setActiveTab('overview')}
            >
              <div className="sidebar-item-left">
                <span className="sidebar-icon">📊</span>
                <span>Обзор</span>
              </div>
              <span className="sidebar-pill">Dashboard</span>
            </button>

            <button 
              className={`sidebar-item ${activeTab === 'products' ? 'active' : ''}`}
              onClick={() => {
                setActiveTab('products');
                loadProducts(1, statusFilter, searchQuery, pageSize, false);
                loadDumpingConfigs();
              }}
            >
              <div className="sidebar-item-left">
                <span className="sidebar-icon">🏷️</span>
                <span>Товары</span>
              </div>
              {totalCount > 0 && <span className="sidebar-pill">{totalCount}</span>}
            </button>

            <button 
              className={`sidebar-item ${activeTab === 'dumping' ? 'active' : ''}`}
              onClick={() => {
                setActiveTab('dumping');
                loadProducts(1, statusFilter, searchQuery, pageSize, dumpingOnlyFilter);
                loadDumpingConfigs();
              }}
            >
              <div className="sidebar-item-left">
                <span className="sidebar-icon">⚡</span>
                <span>Авто-Демпинг</span>
              </div>
              <span className="sidebar-pill" style={{ color: 'var(--secondary)' }}>Auto</span>
            </button>

            <button 
              className={`sidebar-item ${activeTab === 'chatbot' ? 'active' : ''}`}
              onClick={() => {
                setActiveTab('chatbot');
                loadChatbotConfig();
                loadChatbotStatus();
              }}
            >
              <div className="sidebar-item-left">
                <span className="sidebar-icon">🤖</span>
                <span>ИИ-Автоответчик</span>
              </div>
              <span className="sidebar-pill" style={{ color: chatbotStatus.enabled ? '#34d399' : '#888' }}>
                {chatbotStatus.enabled ? 'ON' : 'OFF'}
              </span>
            </button>

            <button 
              className={`sidebar-item ${activeTab === 'stock-groups' ? 'active' : ''}`}
              onClick={() => setActiveTab('stock-groups')}
            >
              <div className="sidebar-item-left">
                <span className="sidebar-icon">🔗</span>
                <span>Связка остатков</span>
              </div>
              <span className="sidebar-pill" style={{ color: '#818cf8' }}>Master</span>
            </button>

            <button 
              className={`sidebar-item ${activeTab === 'public-api' ? 'active' : ''}`}
              onClick={() => setActiveTab('public-api')}
            >
              <div className="sidebar-item-left">
                <span className="sidebar-icon">💻</span>
                <span>Для разработчиков</span>
              </div>
              <span className="sidebar-pill" style={{ color: '#38bdf8' }}>API</span>
            </button>
          </div>




          <div className="sidebar-group">
            <div className="sidebar-group-title">Заказы & Логистика</div>

            <button 
              className={`sidebar-item ${activeTab === 'print-batcher' ? 'active' : ''}`}
              onClick={() => setActiveTab('print-batcher')}
            >
              <div className="sidebar-item-left">
                <span className="sidebar-icon">🖨️</span>
                <span>Автосборщик печати</span>
              </div>
              <span className="sidebar-pill" style={{ color: '#10b981' }}>KSTAT</span>
            </button>

            <button 
              className={`sidebar-item ${activeTab === 'packaging' ? 'active' : ''}`}
              onClick={() => {
                setActiveTab('packaging');
                loadOrders(orderStatusFilter, orderLimit);
              }}
            >
              <div className="sidebar-item-left">
                <span className="sidebar-icon">📦</span>
                <span>Упаковка</span>
              </div>
              {orders.length > 0 && <span className="sidebar-pill">{orders.length}</span>}
            </button>

            <button 
              className={`sidebar-item ${activeTab === 'transfer' ? 'active' : ''}`}
              onClick={() => {
                setActiveTab('transfer');
                loadTransfers(transferFilter);
              }}
            >
              <div className="sidebar-item-left">
                <span className="sidebar-icon">🚚</span>
                <span>Передача</span>
              </div>
              {transfers.length > 0 && <span className="sidebar-pill">{transfers.length}</span>}
            </button>
          </div>

          <div className="sidebar-group">
            <div className="sidebar-group-title">Система</div>

            <button 
              className={`sidebar-item ${activeTab === 'logs' ? 'active' : ''}`}
              onClick={() => setActiveTab('logs')}
            >
              <div className="sidebar-item-left">
                <span className="sidebar-icon">📜</span>
                <span>Терминал логов</span>
              </div>
              <span className="sidebar-pill" style={{ background: isWsConnected ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)', color: isWsConnected ? '#34d399' : '#f87171' }}>
                {isWsConnected ? 'LIVE' : 'OFF'}
              </span>
            </button>

            <button 
              className={`sidebar-item ${activeTab === 'settings' ? 'active' : ''}`}
              onClick={() => setActiveTab('settings')}
            >
              <div className="sidebar-item-left">
                <span className="sidebar-icon">⚙️</span>
                <span>Подключения & Сессия</span>
              </div>
              <span className="sidebar-pill">API</span>
            </button>
          </div>
        </div>

        <div className="sidebar-footer">
          <div className="sidebar-user-card">
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span className="glow-dot glow-dot-success"></span>
              <span style={{ fontSize: '12px', fontWeight: 600, color: '#e5e7eb' }}>Kaspi API Online</span>
            </div>
            <button 
              className="btn btn-secondary"
              style={{ padding: '4px 8px', fontSize: '11px', color: 'var(--error)' }}
              onClick={handleLogout}
              title="Выйти из аккаунта"
            >
              🚪
            </button>
          </div>
        </div>
      </aside>

      {/* ── MAIN CONTENT AREA ─────────────────────────────────── */}
      <main className="main-content">
        {/* TOP CONTENT HEADER */}
        <header className="content-header">
          <div className="content-title-area">
            <span className="content-title-icon">
              {activeTab === 'stores' && '🏪'}
              {activeTab === 'overview' && '📊'}
              {activeTab === 'products' && '🏷️'}
              {activeTab === 'dumping' && '⚡'}
              {activeTab === 'chatbot' && '🤖'}
              {activeTab === 'stock-groups' && '🔗'}
              {activeTab === 'public-api' && '💻'}
              {activeTab === 'print-batcher' && '🖨️'}
              {activeTab === 'packaging' && '📦'}
              {activeTab === 'transfer' && '🚚'}
              {activeTab === 'logs' && '📜'}
              {activeTab === 'settings' && '⚙️'}
            </span>
            <div className="content-title-text">
              <h1>
                {activeTab === 'stores' && 'Управление Магазинами Kaspi'}
                {activeTab === 'overview' && 'Аналитический Обзор'}
                {activeTab === 'products' && 'Управление Товарами'}
                {activeTab === 'dumping' && 'Автоматический Демпинг'}
                {activeTab === 'chatbot' && 'ИИ-Автоответчик Kaspi'}
                {activeTab === 'stock-groups' && 'Связка Остатков (Master Stock Sync)'}
                {activeTab === 'public-api' && 'Раздел для разработчиков (API & Webhooks)'}
                {activeTab === 'print-batcher' && 'Автосборщик & Сортировщик Печати (Print-Batcher Engine)'}
                {activeTab === 'packaging' && 'Упаковка Заказов'}
                {activeTab === 'transfer' && 'Передача Курьеру'}
                {activeTab === 'logs' && 'Терминал Системных Событий'}
                {activeTab === 'settings' && 'Статус Сессии & Настройки'}
              </h1>
              <p>
                {activeTab === 'stores' && 'Управление магазинами Kaspi, безопасное AES-256 шифрование куков и модальное окно Onboarding Wizard'}
                {activeTab === 'overview' && 'Ключевая аналитика доли BuyBox, сохранение маржи и поток активности'}
                {activeTab === 'products' && 'Каталог товаров, остатки, цены и статус продажи на Kaspi'}
                {activeTab === 'dumping' && 'Управление стратегиями демпинга, шагом и фильтрами конкурентов'}
                {activeTab === 'chatbot' && 'Автоматические умные ответы покупателям, курьерские шаблоны и Telegram эскалация'}
                {activeTab === 'stock-groups' && 'Объединение карточек товаров Kaspi в единые складские группы с автоматическим веерным списанием остатков'}
                {activeTab === 'public-api' && 'Управление API-ключами, настройка исходящих Webhooks и документация для интеграции с 1С/ERP'}
                {activeTab === 'print-batcher' && 'Прием ZIP-архивов, распознавание размеров страниц и генерация единых PDF для мгновенной печати по типам принтеров'}
                {activeTab === 'packaging' && 'Формирование и печать упаковочных актов и маркировок'}
                {activeTab === 'transfer' && 'Управление актами приема-сдачи и курьерскими передачами'}
                {activeTab === 'logs' && 'Поток событий системы в реальном времени через WebSocket'}
                {activeTab === 'settings' && 'Состояние авторизации Kaspi REST API и SMS-сессии Kaspi MC'}
              </p>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div className="status-widget" style={{ padding: '6px 14px', borderRadius: '20px' }}>
              <span className="glow-dot glow-dot-success"></span>
              <span style={{ fontSize: '12px', fontWeight: 600 }}>REST API Active</span>
            </div>
            <button className="btn btn-secondary" style={{ padding: '8px 14px' }} onClick={handleLogout}>
              🚪 Выйти
            </button>
          </div>
        </header>

        {/* 🏪 TAB: STORES */}
        {activeTab === 'stores' && <StoreHub />}

        {/* 📊 TAB: OVERVIEW */}
        {activeTab === 'overview' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '20px' }}>
              <BuyBoxGauge
                percentage={analytics.buy_box_share}
                winningCount={analytics.winning_count}
                totalScanned={analytics.total_scanned}
              />
              <SavedMarginCard
                totalSavedMargin={analytics.total_saved_margin}
              />
              <LiveTerminalStream
                logs={logs}
                isConnected={isWsConnected}
              />
            </div>

            {/* ⚔️ LIVE PRICE RADAR (REAL-TIME PRICE WAR MONITOR) */}
            <PriceRadarWidget
              topProduct={widgetData?.price_radar?.top_product}
              events={widgetData?.price_radar?.recent_events}
            />

            {/* 🧠 AI CHAT SENTIMENT & STORE PULSE SHELL */}
            <AiChatSentimentWidget
              statusLabel={widgetData?.ai_sentiment_shell?.status_label}
              categories={widgetData?.ai_sentiment_shell?.categories}
              hourlyHeatmap={widgetData?.ai_sentiment_shell?.hourly_heatmap}
              pulseStatus={widgetData?.ai_sentiment_shell?.store_pulse}
            />

            <div className="grid-2">
              <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <h2>Статус системы</h2>
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
                    apiFetch(`${API_URL}/api/verify-session`, { method: 'POST' })
                      .then(r => r.json())
                      .then(d => addLog(d.valid ? 'Сессия REST API активна ✓' : 'Сессия REST API невалидна ✗'))
                      .catch(() => addLog('Ошибка проверки сессии REST API'));
                  }}>
                    🔄
                  </button>
                </div>

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
                  </div>
                </div>
              </div>

              <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <h2>Быстрые действия</h2>
                  <span className="badge badge-success">Lumina Engine</span>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginTop: '8px' }}>
                  <button className="btn btn-primary" style={{ padding: '14px' }} onClick={() => {
                    setActiveTab('products');
                    loadProducts(1, statusFilter, searchQuery, pageSize);
                  }}>
                    🏷️ Открыть каталог
                  </button>
                  <button className="btn btn-secondary" style={{ padding: '14px' }} onClick={() => {
                    setActiveTab('dumping');
                    loadDumpingConfigs();
                  }}>
                    ⚡ Настроить демпинг
                  </button>
                  <button className="btn btn-secondary" style={{ padding: '14px' }} onClick={() => {
                    setActiveTab('packaging');
                    loadOrders('ASSEMBLY', 10);
                  }}>
                    📦 Сборка заказов
                  </button>
                  <button className="btn btn-secondary" style={{ padding: '14px' }} onClick={() => {
                    setActiveTab('logs');
                  }}>
                    📜 Открыть консоль
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* 📜 TAB: LOGS / TERMINAL */}
        {activeTab === 'logs' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
            <LiveTerminalStream
              logs={logs}
              isConnected={isWsConnected}
            />
            <div className="glass-card" style={{ display: 'flex', flexDirection: 'column' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                <h2>Подробный системный журнал</h2>
                <button 
                  className="btn btn-secondary" 
                  style={{ padding: '4px 8px', fontSize: '11px', borderRadius: '6px' }}
                  onClick={() => setLogs(['Журнал событий очищен.'])}
                >
                  Очистить
                </button>
              </div>
              <div className="console-box" style={{ height: '420px' }}>
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
        )}

        {/* ⚙️ TAB: SETTINGS & SESSION */}
        {activeTab === 'settings' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '24px', maxWidth: '800px' }}>
            <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
              <h2>Статус авторизации и сессии</h2>

              <div className="status-widget">
                <div>
                  <h3 style={{ fontSize: '15px', fontWeight: 600 }}>Kaspi Merchant API (REST)</h3>
                  <p style={{ marginTop: '4px', fontSize: '12px' }}>Токен X-Auth-Token проверен и сохранен.</p>
                  <div style={{ display: 'flex', alignItems: 'center', marginTop: '8px' }}>
                    <span className="glow-dot glow-dot-success"></span>
                    <span className="badge badge-success">Сессия активна</span>
                  </div>
                </div>
                <button className="btn btn-secondary" onClick={() => {
                  addLog('Проверка состояния сессии REST API...');
                  apiFetch(`${API_URL}/api/verify-session`, { method: 'POST' })
                    .then(r => r.json())
                    .then(d => addLog(d.valid ? 'Сессия REST API активна ✓' : 'Сессия REST API невалидна ✗'))
                    .catch(() => addLog('Ошибка проверки сессии REST API'));
                }}>
                  🔄 Проверить
                </button>
              </div>

              <div className="status-widget">
                <div>
                  <h3 style={{ fontSize: '15px', fontWeight: 600 }}>Kaspi MC (SMS куки браузера)</h3>
                  <p style={{ marginTop: '4px', fontSize: '12px' }}>Используется для получения цен конкурентов и управления демпингом.</p>
                  <div style={{ display: 'flex', alignItems: 'center', marginTop: '8px' }}>
                    <span className={`glow-dot ${mcHasSession ? 'glow-dot-success' : 'glow-dot-warning'}`}></span>
                    {mcHasSession ? (
                      <span className="badge badge-success">Сессионные куки активны</span>
                    ) : (
                      <span className="badge badge-warning">Требуется повторный вход</span>
                    )}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '10px' }}>
                  <button className="btn btn-primary" onClick={() => setMcAuthStep('phone')}>
                    🔑 {mcHasSession ? 'Авторизоваться заново' : 'Войти через SMS'}
                  </button>
                  <button className="btn btn-secondary" onClick={() => {
                    addLog('Проверка кук Kaspi MC...');
                    checkMcStatus(true).then(() => addLog('Проверка завершена.'));
                  }}>
                    🔄 Проверить
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* 🏷️ TAB 1, ⚡ TAB 2, 📦 TAB 3, 🚚 TAB 4: RENDER IN GLASS CARD */}
        {(activeTab === 'products' || activeTab === 'dumping' || activeTab === 'packaging' || activeTab === 'transfer') && (
          <div className="glass-card" style={{ flexGrow: 1, display: 'flex', flexDirection: 'column' }}>
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
                                <h3 style={{ fontSize: '14px', fontWeight: 600, color: '#fff' }}>{product.name}</h3>
                                <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>
                                  Kaspi SKU: <strong>{product.sku || product.kaspiSku}</strong>
                                  {product.merchantSku && product.merchantSku !== (product.sku || product.kaspiSku) && (
                                    <span style={{ marginLeft: '10px' }}>
                                      • Merchant SKU: <strong style={{ color: '#818cf8' }}>{product.merchantSku}</strong>
                                    </span>
                                  )}
                                </p>

                                
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

                                  <span 
                                    className="badge" 
                                    style={{ 
                                      background: 'rgba(255,255,255,0.05)',
                                      color: '#eee',
                                      border: '1px solid rgba(255,255,255,0.08)'
                                    }}
                                  >
                                    Остаток: {product.stock ?? 0} шт.
                                  </span>
                                  
                                  {product.url && (
                                    <a 
                                      href={product.url} 
                                      target="_blank" 
                                      rel="noreferrer" 
                                      style={{ fontSize: '12px', color: '#3b82f6', textDecoration: 'none' }}
                                    >
                                      🔗 Карточка товара
                                    </a>
                                  )}
                                </div>
                              </div>

                              <div style={{ textAlign: 'right', display: 'flex', alignItems: 'center', gap: '16px' }}>
                                <div>
                                  <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Цена:</div>
                                  <div style={{ fontSize: '16px', fontWeight: 700, color: 'var(--primary)' }}>{product.price}</div>
                                </div>
                                <button
                                  type="button"
                                  className="btn btn-secondary"
                                  style={{ padding: '8px 12px', fontSize: '12px', borderColor: isEditing ? 'var(--secondary)' : 'transparent' }}
                                  onClick={() => {
                                    setExpandedEditSkus(prev => {
                                      const next = new Set(prev);
                                      if (next.has(product.sku)) {
                                        next.delete(product.sku);
                                      } else {
                                        next.add(product.sku);
                                      }
                                      return next;
                                    });
                                  }}
                                >
                                  ✏️ {isEditing ? 'Закрыть' : 'Изменить'}
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
                                  Параметры товара на Kaspi
                                </h4>
                                
                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: '12px' }}>
                                  <div>
                                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Цена (₸)</label>
                                    <input 
                                      type="number" 
                                      id={`edit-price-${product.sku}`} 
                                      className="form-input" 
                                      defaultValue={product.price_num || ''}
                                      placeholder="Цена товара"
                                    />
                                  </div>
                                  <div>
                                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Остаток на складе (шт)</label>
                                    <input 
                                      type="number" 
                                      id={`edit-stock-${product.sku}`} 
                                      className="form-input" 
                                      defaultValue={product.stock ?? 10}
                                      placeholder="Остаток"
                                    />
                                  </div>
                                  <div>
                                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Бренд</label>
                                    <input 
                                      type="text" 
                                      id={`edit-brand-${product.sku}`} 
                                      className="form-input" 
                                      defaultValue={product.brand || ''}
                                      placeholder="Бренд"
                                    />
                                  </div>
                                  <div>
                                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Статус</label>
                                    <select 
                                      id={`edit-status-${product.sku}`} 
                                      className="form-input"
                                      defaultValue={product.status || 'Активен'}
                                      style={{ background: 'var(--input-bg)', color: '#fff', border: '1px solid var(--input-border)', height: '38px', borderRadius: '8px' }}
                                    >
                                      <option value="Активен">В продаже (Активен)</option>
                                      <option value="Архив">Снят с продажи (Архив)</option>
                                    </select>
                                  </div>
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
                                      saveProductUpdate(product.sku, priceVal, stockVal, brandVal, statusVal);
                                    }}
                                  >
                                    Сохранить изменения
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
                          setPageInputText('1');
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
                          setPageInputText('1');
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
                        setPageInputText('1');
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
                                <h3 style={{ fontSize: '14px', fontWeight: 600, color: '#fff' }}>{product.name}</h3>
                                <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>
                                  Kaspi SKU: <strong>{product.sku || product.kaspiSku}</strong>
                                  {product.merchantSku && product.merchantSku !== (product.sku || product.kaspiSku) && (
                                    <span style={{ marginLeft: '10px' }}>
                                      • Merchant SKU: <strong style={{ color: '#818cf8' }}>{product.merchantSku}</strong>
                                    </span>
                                  )}
                                </p>

                                
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
          </div>
        )}

        {activeTab === 'chatbot' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
            {/* Top Header Card */}
            <div className="glass-card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <h2 style={{ fontSize: '18px', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '8px', color: '#fff' }}>
                  🤖 Kaspi ИИ-Автоответчик (DeepSeek + Telegram)
                </h2>
                <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>
                  Автоматические умные ответы клиентам Kaspi, курьерские шаблоны логистики и эскалация менеджеру через Telegram.
                </p>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Статус автоответчика:</span>
                  <span className={`badge ${chatbotStatus.enabled ? 'badge-success' : 'badge-warning'}`}>
                    {chatbotStatus.enabled ? '🟢 Автоответчик Активен' : '⏸️ Приостановлен'}
                  </span>
                </div>

                <button
                  className="btn"
                  style={{
                    background: chatbotStatus.enabled ? 'linear-gradient(135deg, #ef4444, #dc2626)' : 'linear-gradient(135deg, #10b981, #059669)',
                    color: '#fff',
                    padding: '10px 20px',
                    fontSize: '13px',
                    fontWeight: 600,
                    borderRadius: '8px'
                  }}
                  onClick={() => {
                    saveChatbotConfig({ enabled: !chatbotStatus.enabled });
                  }}
                >
                  {chatbotStatus.enabled ? '⏸️ Приостановить бота' : '▶️ Включить автоответчик'}
                </button>
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '20px' }}>
              {/* 📢 Настройки рассылки Telegram Admin IDs */}
              <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <h3 style={{ fontSize: '14px', color: '#fff', fontWeight: 600 }}>📢 Telegram Админы для рассылки</h3>
                  <span className="badge badge-success">Telegram CRM</span>
                </div>

                <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                  Укажите Telegram ID администраторов, которые будут получать алерты при вызове менеджера и сбоях сессии.
                </p>

                <form onSubmit={(e) => {
                  e.preventDefault();
                  const adminIdsRaw = (document.getElementById('admin-ids-input') as HTMLInputElement).value;
                  const adminIds = adminIdsRaw.split(',').map(s => s.trim()).filter(Boolean);

                  saveChatbotConfig({
                    admin_ids: adminIds
                  });
                }} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                  <div>
                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>
                      Telegram Admin IDs (через запятую)
                    </label>
                    <input
                      type="text"
                      id="admin-ids-input"
                      className="form-input"
                      defaultValue={(chatbotConfig.admin_ids || []).join(', ')}
                      placeholder="7119137844, 8661441552"
                    />
                  </div>

                  <button type="submit" className="btn btn-primary" style={{ padding: '10px', marginTop: '8px' }}>
                    💾 Сохранить параметры рассылки
                  </button>
                </form>
              </div>

              {/* 🔒 Передача менеджеру (Human Override) */}
              <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <h3 style={{ fontSize: '14px', color: '#fff', fontWeight: 600 }}>🔒 Заблокированные чаты (Вызов менеджера)</h3>
                  <span className="badge badge-warning">{chatbotStatus.blocked_chats_count} чатов</span>
                </div>

                <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                  Диалоги, в которых покупатель попросил менеджера/возврат/брак. ИИ приостановлен по этим чатам до разблокировки.
                </p>

                {chatbotStatus.blocked_chats.length === 0 ? (
                  <div style={{ padding: '24px 0', textAlign: 'center', color: '#888', fontSize: '13px' }}>
                    🎉 Нет заблокированных диалогов. ИИ отвечает на все входящие!
                  </div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', maxHeight: '320px', overflowY: 'auto' }}>
                    {chatbotStatus.blocked_chats.map((item) => (
                      <div key={item.group_id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'rgba(255,255,255,0.03)', padding: '10px 14px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.06)' }}>
                        <div>
                          <div style={{ fontSize: '13px', fontWeight: 600, color: '#fff' }}>Чат #{item.group_id}</div>
                          <div style={{ fontSize: '11px', color: 'var(--warning)', marginTop: '2px' }}>Причина: {item.reason}</div>
                        </div>

                        <button
                          className="btn btn-secondary"
                          style={{ padding: '6px 12px', fontSize: '11px' }}
                          onClick={() => unblockChatbotGroup(item.group_id)}
                        >
                          ✅ Разблокировать для ИИ
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
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
