import React, { useState, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch, setUnauthorizedHandler, API_URL, getWsUrl } from './api';
import { clearPersistedQueryCache } from './queryClient';
import { useActiveStoreId } from './storeContext';
import { useWebSocket } from './hooks/useWebSocket';
import { useLogs } from './hooks/useLogs';
import { useAnalyticsWidgets } from './hooks/useAnalyticsWidgets';
import { useMcAuth } from './hooks/useMcAuth';
import { usePriceFeedImport } from './hooks/usePriceFeedImport';
import { LogsTab } from './components/tabs/LogsTab';
import { OverviewTab } from './components/tabs/OverviewTab';
import { SettingsTab } from './components/tabs/SettingsTab';
import { AutolistTab } from './components/tabs/AutolistTab';
import { MasterStockGroups } from './components/MasterStockGroups';
import { IntegrationsPublicApi } from './components/IntegrationsPublicApi';
import { PrintBatcher } from './components/PrintBatcher';
import { AuthModal } from './components/AuthModal';
import { StoreHub } from './components/StoreHub';
import { BackgroundSync } from './components/BackgroundSync';
import { McAuthModal } from './components/McAuthModal';
import { PriceFeedImportModal } from './components/PriceFeedImportModal';
import { ImageLightbox } from './components/shared/ImageLightbox';
import { Sidebar } from './components/Sidebar';
import { ContentHeader } from './components/ContentHeader';
import { PackagingTab } from './components/tabs/PackagingTab';
import { TransferTab } from './components/tabs/TransferTab';
import { ChatbotTab } from './components/tabs/ChatbotTab';
import { ChatsTab } from './components/tabs/ChatsTab';
import { ProductsTab } from './components/tabs/ProductsTab';
import { DumpingTab } from './components/tabs/DumpingTab';
import { AutoAssemblyTab } from './components/tabs/AutoAssemblyTab';
import { BillingTab } from './components/tabs/BillingTab';
import { AdminPanel } from './components/admin/AdminPanel';
import { ReferralTab } from './components/tabs/ReferralTab';
import { fetchProfile, fetchBillingSummary } from './queries';
import type { ActiveTab, Product, Order, Transfer, ChatbotConfig, ChatbotStatus, DumpingConfig, KaspiQuotaStatus } from './types';

// Сырые фичи (Автодобавление, Для разработчиков) — доступны только этим email,
// пока не будут готовы к общему релизу. См. isBetaTester ниже.
const BETA_TESTER_EMAILS = ['owner@example.com'];

export default function App() {
  const queryClient = useQueryClient();
  const [isLoaded, setIsLoaded] = useState(false);
  const [isAuthed, setIsAuthed] = useState<boolean>(() => !!localStorage.getItem('access_token'));
  // Слайс 6: включён в queryKey всех store-специфичных запросов ниже — без
  // этого переключение магазина в StoreHub показывало бы кэш ПРЕДЫДУЩЕГО
  // магазина, пока не придёт ответ на новый запрос (React Query считает
  // одинаковый ключ = один и тот же кэш, независимо от заголовка X-Store-Id).
  const activeStoreId = useActiveStoreId();


  const handleLogout = () => {
    localStorage.removeItem('access_token');
    clearPersistedQueryCache();
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
  // Мобильное меню (drawer). На десктопе состояние ни на что не влияет —
  // .sidebar там статичен, класс sidebar-open просто игнорируется.
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  // Тарификация (Слайс 6/7): роли читаются из того же ['auth','me'] кэша,
  // что уже прогревает BackgroundSync — второго запроса не заводим.
  const { data: profile } = useQuery({ queryKey: ['auth', 'me'], queryFn: fetchProfile, refetchInterval: 30_000 });
  const isSuperadmin = !!profile?.user.is_superadmin;
  const isReferrer = !!profile?.user.is_referrer;
  // Автодобавление и «Для разработчиков» ещё сырые — временно видны только этому
  // списку email. Whitelist, не deployment-флаг: снять ограничение — просто удалить.
  const isBetaTester = !!profile?.user.email && BETA_TESTER_EMAILS.includes(profile.user.email.toLowerCase());

  const { data: billingSummary } = useQuery({
    queryKey: ['billing', 'summary', activeStoreId],
    queryFn: fetchBillingSummary,
    enabled: !!activeStoreId,
    refetchInterval: 60_000,
  });
  const billingBlocked = !!billingSummary && billingSummary.billing_status !== 'ACTIVE';

  // Открытый drawer блокирует скролл фона (иначе под меню уезжает контент).
  useEffect(() => {
    document.body.classList.toggle('sidebar-locked', mobileNavOpen);
    return () => document.body.classList.remove('sidebar-locked');
  }, [mobileNavOpen]);

  useEffect(() => {
    if (activeTab === 'admin' && !isSuperadmin) setActiveTab('stores');
    if (activeTab === 'referral' && !isReferrer) setActiveTab('stores');
    if ((activeTab === 'autolist' || activeTab === 'public-api') && !isBetaTester) setActiveTab('stores');
  }, [activeTab, isSuperadmin, isReferrer, isBetaTester]);



  
  // Chatbot states
  const [chatbotConfig, setChatbotConfig] = useState<ChatbotConfig>({
    enabled: true,
    deepseek_api_key: '',
    tg_bot_token: '',
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
  const [orderStatusFilter, setOrderStatusFilter] = useState<'NEW' | 'ASSEMBLY'>('ASSEMBLY');
  const [orderLimit, setOrderLimit] = useState(10);
  const [selectedOrderIds, setSelectedOrderIds] = useState<Set<string>>(new Set());
  const [packingAll, setPackingAll] = useState(false);

  const [transferFilter, setTransferFilter] = useState<'ALL' | 'EXPRESS' | 'TODAY'>('ALL');

  const { logs, addLog, clearLogs } = useLogs();

  // Фоновый поллинг статуса MC-сессии без verify (дёшево, читает БД) — держит
  // бейдж в Settings живым, не дёргая общий Playwright-воркер каждые 15с.
  // verify=true остаётся только для явных случаев: первичная проверка на
  // монтировании и ручная кнопка «Проверить» (см. checkMcStatus ниже).
  const { data: mcHasSession = false } = useQuery({
    queryKey: ['mc-status', activeStoreId],
    queryFn: async () => {
      try {
        const res = await apiFetch(`${API_URL}/api/mc-status?verify=false`);
        const data = await res.json();
        return res.ok && data.success ? !!(data.has_session && data.valid) : false;
      } catch {
        return false;
      }
    },
    refetchInterval: 15_000,
  });
  const {
    emailInput, setEmailInput,
    passwordInput, setPasswordInput,
    mcAuthStep, mcAuthError,
    openModal: openMcAuthModal,
    closeModal: closeMcAuthModal,
    submitLogin: submitMcAuthLogin,
  } = useMcAuth(addLog, () => queryClient.invalidateQueries({ queryKey: ['mc-status'] }));

  // Dumping & Analytics States
  const [dumpingConfigs, setDumpingConfigs] = useState<Record<string, DumpingConfig>>({});
  const [expandedSkus, setExpandedSkus] = useState<Set<string>>(new Set());
  const [expandedEditSkus, setExpandedEditSkus] = useState<Set<string>>(new Set());
  const [expandedFilterSkus, setExpandedFilterSkus] = useState<Set<string>>(new Set());
  const [selectedModes, setSelectedModes] = useState<Record<string, string>>({});
  const [isRunningDumping, setIsRunningDumping] = useState(false);
  const [nextRunSeconds, setNextRunSeconds] = useState<number | null>(null);
  const [kaspiQuota, setKaspiQuota] = useState<KaspiQuotaStatus | null>(null);
  const [queuePending, setQueuePending] = useState<number>(0);
  const [dumpingOnlyFilter, setDumpingOnlyFilter] = useState(false);
  const [dumpingStatusFilter, setDumpingStatusFilter] = useState<string>('');

  const { isConnected: isWsConnected, lastEvent: wsEvent } = useWebSocket(getWsUrl());
  const { analytics, widgetData } = useAnalyticsWidgets(wsEvent);

  const checkMcStatus = async (verify = false) => {
    try {
      const res = await apiFetch(`${API_URL}/api/mc-status?verify=${verify}`);
      const data = await res.json();
      if (res.ok && data.success) {
        const valid = !!(data.has_session && data.valid);
        queryClient.setQueryData(['mc-status', activeStoreId], valid);
        return valid;
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
        setKaspiQuota(data.quota ?? null);
        setQueuePending(data.queue_pending ?? 0);
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
        // Закрываем панель редактирования
        setExpandedSkus(prev => {
          const next = new Set(prev);
          next.delete(sku);
          return next;
        });
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
      clearPersistedQueryCache();
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
            clearPersistedQueryCache();
            setIsAuthed(false);
          }
        } else {
          localStorage.removeItem('access_token');
          clearPersistedQueryCache();
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

  const priceFeedImport = usePriceFeedImport(() => {
    loadDumpingConfigs();
    loadProducts(currentPage, statusFilter, searchQuery, pageSize, dumpingOnlyFilter, dumpingStatusFilter);
  });

  const getOrderParams = (statusFilter: 'NEW' | 'ASSEMBLY') => {
    if (statusFilter === 'NEW') {
      return { state: 'KASPI_DELIVERY', status: 'APPROVED_BY_BANK', assembled: undefined };
    }
    return { state: 'KASPI_DELIVERY', status: 'ACCEPTED_BY_MERCHANT', assembled: false };
  };

  // Заказы теперь живут в React Query — кэш переживает переключение вкладок,
  // фоновый refetchInterval держит их свежими без клика пользователя. Смена
  // фильтра/лимита меняет queryKey и триггерит перезапрос сама по себе.
  const { data: orders = [], isFetching: fetchingOrders, refetch: refetchOrders } = useQuery({
    queryKey: ['orders', activeStoreId, orderStatusFilter, orderLimit],
    queryFn: async () => {
      const { state, status, assembled } = getOrderParams(orderStatusFilter);
      let url = `${API_URL}/api/orders?state=${state}&status=${status}&limit=${orderLimit}`;
      if (assembled !== undefined) url += `&assembled=${assembled}`;
      const res = await apiFetch(url);
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.detail || 'Ошибка бэкенда');
      }
      return data.orders as Order[];
    },
    refetchInterval: 30_000,
  });

  // Сбрасываем выделение только при явной смене фильтра/лимита, не при
  // каждом фоновом обновлении списка.
  useEffect(() => {
    setSelectedOrderIds(new Set());
  }, [orderStatusFilter, orderLimit]);

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
        refetchOrders();
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
        refetchOrders();
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
    refetchOrders();
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

  const { data: transfers = [], isFetching: fetchingTransfers, refetch: refetchTransfers } = useQuery({
    queryKey: ['transfers', activeStoreId, transferFilter],
    queryFn: async () => {
      const res = await apiFetch(`${API_URL}/api/transfers?filter=${transferFilter}`);
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.detail || 'Ошибка бэкенда');
      }
      return data.transfers as Transfer[];
    },
    refetchInterval: 30_000,
  });

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
    // Мобильный drawer закрывается сразу после выбора вкладки — иначе меню
    // остаётся поверх контента, ради которого его и открывали.
    setMobileNavOpen(false);
    if (tab === 'products') {
      loadProducts(1, statusFilter, searchQuery, pageSize, false);
      loadDumpingConfigs();
    } else if (tab === 'dumping') {
      loadProducts(1, statusFilter, searchQuery, pageSize, dumpingOnlyFilter);
      loadDumpingConfigs();
    } else if (tab === 'chatbot') {
      loadChatbotConfig();
      loadChatbotStatus();
    }
    // packaging/transfer: заказы и передачи теперь на React Query — уже
    // загружены и обновляются в фоне независимо от активной вкладки.
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
      <BackgroundSync />

      {/* ── МОБИЛЬНАЯ ВЕРХНЯЯ ПАНЕЛЬ (только <=900px, см. mobile.css) ── */}
      <div className="mobile-topbar">
        <button
          className="mobile-topbar-burger"
          onClick={() => setMobileNavOpen((v) => !v)}
          aria-label={mobileNavOpen ? 'Закрыть меню' : 'Открыть меню'}
          aria-expanded={mobileNavOpen}
        >
          {mobileNavOpen ? '✕' : '☰'}
        </button>
        <img src="/buyshark-logo.svg" alt="" className="mobile-topbar-logo" />
        <span className="mobile-topbar-title">BuyShark</span>
        <button
          className="btn btn-secondary"
          style={{ padding: '8px 12px', fontSize: '12px' }}
          onClick={handleLogout}
          title="Выйти из аккаунта"
        >
          🚪
        </button>
      </div>

      {mobileNavOpen && (
        <div className="sidebar-scrim" onClick={() => setMobileNavOpen(false)} />
      )}

      {/* ── STITCH MCP SIDEBAR ─────────────────────────────────── */}
      <Sidebar
        isOpen={mobileNavOpen}
        activeTab={activeTab}
        onSelectTab={handleTabClick}
        onLogout={handleLogout}
        totalCount={totalCount}
        chatbotEnabled={chatbotStatus.enabled}
        ordersCount={orders.length}
        transfersCount={transfers.length}
        isWsConnected={isWsConnected}
        isSuperadmin={isSuperadmin}
        isReferrer={isReferrer}
        isBetaTester={isBetaTester}
        billingBlocked={billingBlocked}
      />

      {/* ── MAIN CONTENT AREA ─────────────────────────────────── */}
      <main className="main-content">
        {/* TOP CONTENT HEADER */}
        <ContentHeader activeTab={activeTab} onLogout={handleLogout} />

        {billingBlocked && activeTab !== 'billing' && (
          <div
            style={{
              margin: '16px 24px 0', padding: '12px 16px', borderRadius: '10px',
              background: 'rgba(239, 68, 68, 0.1)', border: '1px solid rgba(239, 68, 68, 0.3)',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px',
            }}
          >
            <span style={{ color: '#f87171', fontSize: '13px' }}>
              🔒 {billingSummary?.blocked_reason || 'Сервис заблокирован по тарифу — оплатите, чтобы продолжить.'}
            </span>
            <button className="btn btn-primary" style={{ padding: '6px 12px', fontSize: '12px' }} onClick={() => setActiveTab('billing')}>
              Перейти к оплате
            </button>
          </div>
        )}

        {/* 🏪 TAB: STORES */}
        {activeTab === 'stores' && <StoreHub />}

        {/* 📊 TAB: OVERVIEW */}
        {activeTab === 'overview' && (
          <OverviewTab
            analytics={analytics}
            widgetData={widgetData}
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
              setOrderStatusFilter('ASSEMBLY');
              setOrderLimit(10);
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
                kaspiQuota={kaspiQuota}
                queuePending={queuePending}
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
                onOpenPriceFeedImport={priceFeedImport.openModal}
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
                onOrderLimitChange={setOrderLimit}
                orderStatusFilter={orderStatusFilter}
                onOrderStatusFilterChange={setOrderStatusFilter}
                pkgDeliveryFilter={pkgDeliveryFilter}
                onPkgDeliveryFilterChange={setPkgDeliveryFilter}
                selectedOrderIds={selectedOrderIds}
                onToggleSelectAll={toggleSelectAll}
                onToggleOrderSelection={toggleOrderSelection}
                onPackSelectedOrders={packSelectedOrders}
                onPackAllOrders={packAllOrders}
                onPackOrder={packOrder}
                packingAll={packingAll}
                onRefresh={refetchOrders}
              />
            )}

            {/* TAB 3: TRANSFER */}
            {activeTab === 'transfer' && (
              <TransferTab
                transfers={transfers}
                fetchingTransfers={fetchingTransfers}
                transferFilter={transferFilter}
                onTransferFilterChange={setTransferFilter}
                onRefresh={refetchTransfers}
              />
            )}
          </div>
        )}

        {activeTab === 'chatbot' && (
          <ChatbotTab
            chatbotStatus={chatbotStatus}
            onSaveChatbotConfig={saveChatbotConfig}
            onUnblockChatbotGroup={unblockChatbotGroup}
          />
        )}

        {activeTab === 'chats' && <ChatsTab />}

        {/* 🤖 TAB: AUTO-ASSEMBLY (auto-accept + auto-pack + waybill spool) */}
        {activeTab === 'auto-assembly' && <AutoAssemblyTab />}

        {/* 🔗 TAB: MASTER STOCK GROUPS */}
        {activeTab === 'stock-groups' && (
          <MasterStockGroups />
        )}

        {/* ✨ TAB: AUTOLIST (AI product auto-add) */}
        {activeTab === 'autolist' && (
          <AutolistTab />
        )}

        {/* 🔑 TAB: PUBLIC API & WEBHOOKS */}
        {activeTab === 'public-api' && (
          <IntegrationsPublicApi />
        )}

        {/* 🖨️ TAB: PRINT-BATCHER ENGINE */}
        {activeTab === 'print-batcher' && (
          <PrintBatcher />
        )}

        {/* 💳 TAB: BILLING */}
        {activeTab === 'billing' && <BillingTab />}

        {/* 🛡️ TAB: ADMIN (только суперадмин) */}
        {activeTab === 'admin' && isSuperadmin && <AdminPanel />}

        {/* 🤝 TAB: REFERRAL (только реферер) */}
        {activeTab === 'referral' && isReferrer && <ReferralTab />}
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
      <PriceFeedImportModal
        step={priceFeedImport.step}
        phone={priceFeedImport.phone}
        onPhoneChange={priceFeedImport.setPhone}
        password={priceFeedImport.password}
        onPasswordChange={priceFeedImport.setPassword}
        merchants={priceFeedImport.merchants}
        preview={priceFeedImport.preview}
        applyResult={priceFeedImport.applyResult}
        overwrite={priceFeedImport.overwrite}
        onOverwriteChange={priceFeedImport.setOverwrite}
        activate={priceFeedImport.activate}
        onActivateChange={priceFeedImport.setActivate}
        error={priceFeedImport.error}
        onClose={priceFeedImport.closeModal}
        onSubmitCredentials={priceFeedImport.submitCredentials}
        onChooseMerchant={priceFeedImport.chooseMerchant}
        onApply={priceFeedImport.applyImport}
        onFinish={priceFeedImport.finish}
      />
      <ImageLightbox />
    </div>
  );
}
