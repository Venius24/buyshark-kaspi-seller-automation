export interface Product {
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
  dumping_config?: DumpingConfig | null;
  /** "individual" — свой конфиг товара, "group" — унаследован от активной Мастер-Группы. */
  dumping_config_source?: 'individual' | 'group' | null;
  /** Состояние самой настройки демпинга (эффективной, с учётом Мастер-Групп). */
  dumping_state?: 'ON' | 'OFF' | 'NO_CONFIG';
  /** Где цена относительно коридора min/max из конфига. */
  price_state?: 'AT_MIN' | 'AT_MAX' | 'IN_RANGE' | 'NO_LIMITS';
  /** Наша позиция по последнему скану радара. */
  rank_state?: 'LEADER' | 'NOT_LEADER' | 'NO_DATA';
  /** null — товар ни разу не сканировался (это НЕ ноль конкурентов). */
  competitors_count?: number | null;
  price_war?: {
    our_rank: number | null;
    competitors_count: number;
    best_competitor_price: number | null;
    best_competitor_name: string | null;
    /** Момент последнего скана — расклад конкурентов это снимок, а не онлайн-истина. */
    checked_at: string | null;
  } | null;
  /** Превью с CDN Kaspi (resources.cdn-kaspi.kz), размер "small". */
  image?: string;
  /** Метки товара — печатаются на этикетке рядом со штампом Мастер-Группы. */
  tags?: string[];
}

export interface OrderEntry {
  entryId: string;
  productName: string;
  sku: string;
  merchantSku?: string;
  kaspiSku?: string;
  productUrl?: string;
  quantity: number;
  basePrice: number;
  totalPrice: number;
  /** Подставляется бэкендом из каталога товаров по sku/merchantSku, т.к. orders API Kaspi картинок не отдаёт. */
  image?: string;
}

export interface Order {
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

export interface Transfer {
  id: string;
  deliveryType: string;
  deadline: string;
  address: string;
  waybillNumber?: string;
  waybillUrl?: string;
}

export interface ChatbotConfig {
  enabled: boolean;
  deepseek_api_key: string;
  tg_bot_token: string;
  use_tg_proxy: boolean;
  tg_proxy: string;
  merchant_id: string;
}

export interface PickupPoint {
  name: string;
  address: string;
  map_url: string;
  hours: string;
  notes: string;
}

export interface BotProfile {
  ai_name: string;
  store_display_name: string;
  specialization: string;
  return_policy: string;
  shipping_days: string;
  shipping_cutoff_time: string;
  pickup_points: PickupPoint[];
  logistics_reply_ru: string;
  logistics_reply_kk: string;
  custom_instructions: string;
  custom_enabled: boolean;
  prompt_tier: 'band0' | 'band1' | 'band2' | 'band3';
  review_delay_from_days: number;
  review_delay_to_days: number | null;
  review_send_overdue: boolean;
}

export interface OutboundQueueItem {
  id: number;
  order_id: string;
  campaign: string;
  status: string;
  group_id: string | null;
  message_text: string;
  scheduled_at: string | null;
  sent_at: string | null;
  skip_reason: string | null;
  attempts: number;
}

export interface BotUsageSummary {
  total_billed_units: number;
  by_kind: Record<string, { count: number; units: number }>;
  period_days: number;
}

export interface OutboundCampaignStats {
  total: number;
  sent: number;
  replied: number;
  reply_rate: number;
  pending: number;
  needs_first_contact: number;
  skipped: number;
  failed: number;
  skip_reasons: Record<string, number>;
}

export interface BotJournalItem {
  id: number;
  created_at: string;
  group_id: string;
  kind: string;
  client_text: string | null;
  reply_text: string | null;
  order_code: string | null;
  detail: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
}

export interface BotJournal {
  period_days: number;
  items: BotJournalItem[];
  by_kind: Record<string, number>;
  handled: number;
  escalated: number;
  escalation_rate: number;
}

export interface OutboundStats {
  period_days: number;
  by_campaign: Record<string, OutboundCampaignStats>;
}

export interface ChatbotStatus {
  enabled: boolean;
  has_session: boolean;
  cookie_alert_sent: boolean;
  blocked_chats_count: number;
  blocked_chats: { group_id: string; reason: string }[];
  processed_chats_count: number;
}

export interface DumpingConfig {
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
}

/** Срезы вкладки авто-демпинга. Пустая строка / null = фильтр не применён. */
export interface DumpingFilters {
  /** Рабочий статус из последнего расчёта движка: WINNING_1ST, AT_MIN_PRICE, ... */
  status: string;
  /** ON | OFF | NO_CONFIG | OFF_WITH_RIVALS */
  dumpingState: string;
  /** AT_MIN | AT_MAX | IN_RANGE | NO_LIMITS */
  priceState: string;
  /** LEADER | NOT_LEADER | NO_DATA */
  rankState: string;
  minCompetitors: number | null;
  maxCompetitors: number | null;
}

export const EMPTY_DUMPING_FILTERS: DumpingFilters = {
  status: '',
  dumpingState: '',
  priceState: '',
  rankState: '',
  minCompetitors: null,
  maxCompetitors: null,
};

/** Сколько товаров попало бы в каждый срез — приходит с /api/products. */
export type DumpingFacets = Record<string, number>;

export interface AutolistFeatureMeta {
  name: string;
  mandatory: boolean;
  multiValued: boolean;
  type: 'enum' | 'string' | 'number' | 'boolean';
}

export interface AutolistDraftPayload {
  category_code: string | null;
  category_name: string | null;
  brand_name: string | null;
  sku: string | null;
  price: number | null;
  stock: number | null;
  features: Record<string, any>;
  feature_meta: Record<string, AutolistFeatureMeta>;
  classifications: Array<{ name: string; features: Array<{ attributeCode: string; name: string; minValue?: number | null; maxValue?: number | null; unit?: { name: string } | null; defaultValues?: Array<{ code: string; name: string }> }> }>;
  description: string;
  existing_candidates?: Array<{ id: string; title: string; shopLink: string }>;
  category_search_query?: string;
}

export interface AutolistDraft {
  id: string;
  store_id: string;
  photo_path: string;
  raw_title: string;
  status: 'draft' | 'needs_review' | 'approved' | 'submitting' | 'submitted' | 'error';
  ai_raw: any;
  draft_payload: AutolistDraftPayload;
  missing_mandatory: string[];
  kaspi_result: any;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  warning?: string;
}

export interface AutoAssemblySchedule {
  days?: number[];
  start?: string;
  end?: string;
}

export interface AutoAssemblyRule {
  delivery_type: string;
  enabled: boolean;
  dry_run: boolean;
  stage_accept: boolean;
  stage_assemble: boolean;
  stage_waybill: boolean;
  schedule: AutoAssemblySchedule | null;
  max_order_price: number | null;
  sku_blacklist: string[];
  respect_stock_groups: boolean;
  max_per_cycle: number | null;
}

export interface AutoAssemblyLogEntry {
  order_code: string;
  stage: string;
  decision: string;
  reason: string | null;
  created_at: string | null;
}

export interface AutoAssemblySpoolItem {
  order_code: string;
  date: string;
}

export type ActiveTab =
  | 'stores'
  | 'overview'
  | 'products'
  | 'dumping'
  | 'chatbot'
  | 'chats'
  | 'stock-groups'
  | 'autolist'
  | 'public-api'
  | 'print-batcher'
  | 'packaging'
  | 'transfer'
  | 'auto-assembly'
  | 'billing'
  | 'employees'
  | 'admin'
  | 'referral'
  | 'logs'
  | 'settings'
  | 'help'
  | 'assistant'
  | 'global-control';

export interface FeatureFlags {
  dumping_enabled: boolean;
  stock_groups_enabled: boolean;
  autoassembly_enabled: boolean;
  chatbot_enabled: boolean;
}

export interface BillingSummary {
  plan_code: string;
  plan_name: string;
  max_products: number | null;
  monthly_price_minor: number;
  billing_status: 'ACTIVE' | 'BLOCKED_LIMIT' | 'BLOCKED_BALANCE';
  blocked_reason: string | null;
  balance_minor: number;
  block_threshold_minor: number;
  period_end: string | null;
  prompt_band: string;
  prompt_multiplier_milli: number;
  credits_used_milli: number;
  credits_limit_milli: number;
}

export interface BillingPlan {
  code: string;
  name: string;
  max_products: number | null;
  monthly_price_minor: number | null;
  daily_credit_limit_milli: number | null;
  badge: string | null;
}

export interface BillingTransaction {
  id: string;
  amount_minor: number;
  balance_after_minor: number | null;
  kind: string;
  status: string;
  comment: string | null;
  created_at: string | null;
}

export interface PriceFeedMerchant {
  id: string;
  name: string;
}

export interface PriceFeedPreviewSampleRow {
  sku: string;
  title: string | null;
  min_price: number;
  max_price: number;
  dump_step: number;
  mode: string;
  already_configured: boolean;
}

export interface PriceFeedPreviewResult {
  status: 'ready' | 'choose_merchant';
  import_id?: string;
  expires_in_seconds?: number;
  merchant?: PriceFeedMerchant;
  merchants?: PriceFeedMerchant[];
  total_fetched?: number;
  importable?: number;
  matched?: number;
  unmatched?: number;
  unmatched_sample?: string[];
  already_configured?: number;
  skipped?: Record<string, number>;
  warnings?: string[];
  sample?: PriceFeedPreviewSampleRow[];
  truncated?: boolean;
}

export interface PriceFeedApplyResult {
  success: boolean;
  created: number;
  updated: number;
  skipped_existing: number;
  total: number;
  /** Импортированные конфиги сразу включены в бой (иначе легли спящими). */
  activated?: boolean;
}

/**
 * Квота Kaspi на обновление офферов: 250 штук за фиксированное 30-минутное
 * окно на весь магазин, общая на демпинг и на связку остатков. Приходит из
 * GET /api/dumping/status (кэш бэкенда, не живой запрос к Kaspi).
 */
export interface KaspiQuotaStatus {
  used: number;
  limit: number;
  remaining: number;
  reset_in_seconds: number;
  /** Сколько записей из окна зарезервировано под снятие с продажи. */
  reserved_for_withdraw: number;
  /**
   * Сколько записей окна сделал НАШ сервис. `used` — счётчик самого Kaspi по
   * всему мерчанту, туда попадают и сторонние интеграции того же кабинета.
   */
  used_by_us: number;
}
