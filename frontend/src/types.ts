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
  /** Сигналы атаки на последнем цикле: weak_rival | ladder | farm | shadow. */
  attack_signals?: string[];
  /** Что защита от оттеснения сделала на последнем цикле. null — товар не сканировался. */
  guard_state?: {
    enabled: boolean;
    limit: string | null;
    ladder_active: boolean;
    ignored_count: number;
  } | null;
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
  /** Переопределение защиты от оттеснения на этом товаре. null/нет — наследовать. */
  guard?: GuardOverride;
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
  /** UNDER_ATTACK | LADDER | GUARDED */
  attackState: string;
  minCompetitors: number | null;
  maxCompetitors: number | null;
}

export const EMPTY_DUMPING_FILTERS: DumpingFilters = {
  status: '',
  dumpingState: '',
  priceState: '',
  rankState: '',
  attackState: '',
  minCompetitors: null,
  maxCompetitors: null,
};

/** Защита демпинга от оттеснения — поля backend/dumper/guard.py::GuardConfig. */
export interface GuardSettings {
  enabled: boolean;
  /** Наблюдение: защита всё считает и показывает в отчёте, но цены не трогает. */
  observe_only: boolean;
  /** Порог отзывов — только для продавцов с рейтингом. */
  min_reviews: number | null;
  /** Продавец без рейтинга: ASK — учитывать и спросить менеджера; IGNORE; CONSIDER. */
  no_rating_action: 'ASK' | 'IGNORE' | 'CONSIDER';
  min_rating: number | null;
  min_merchant_age_days: number | null;
  ignore_intercity: boolean;
  max_extra_delivery_days: number | null;
  ignore_preorder: boolean;
  blacklist: string[];
  whitelist: string[];
  /** HOLD — все соперники отсеяны: держим цену; MAX — ставим max_price. */
  all_ignored_action: 'HOLD' | 'MAX';
  floor_below_next_pct: number | null;
  /** Цена упала на X% за сутки — пауза в демпинге этого товара. */
  max_daily_drop_pct: number | null;
  drop_pause_hours: number;
  drop_alert: boolean;
  ladder_min_moves: number;
  ladder_window_hours: number;
  /** Лесенка — только если за серию цену утянули вниз хотя бы на X%. */
  ladder_min_slide_pct: number | null;
  /** FLAG — только отметить; FREEZE — не снижать; RETREAT — перестать отвечать этому сопернику;
   *  RESET_MAX — мы на 1–2 месте: раз в сутки цена возвращается к началу лесенки (не выше max_price). */
  ladder_response: 'FLAG' | 'FREEZE' | 'RETREAT' | 'RESET_MAX';
  ladder_cooldown_hours: number;
}

/** Переопределение на товаре / Мастер-Группе. null — наследовать. */
export type GuardOverride =
  | null
  | { mode: 'OFF' }
  | ({ mode: 'CUSTOM' } & Partial<Omit<GuardSettings, 'enabled'>>);

export interface AttackSuspect {
  merchant_id: string;
  name: string;
  price: number;
  reviews: number | null;
  rating: number | null;
  intercity: boolean | null;
  age_days: number | null;
  marks: string[];
  adjacent: boolean;
}

export interface AttackItem {
  sku: string;
  name: string | null;
  url: string | null;
  our_price: number;
  our_rank: number | null;
  peak_price_30d: number | null;
  lost_per_unit: number;
  lost_pct: number;
  down_moves_30d: number;
  signals: string[];
  suspects: AttackSuspect[];
  ladder: {
    rival_id: string;
    rival_name: string;
    moves: number;
    first_at: string;
    last_at: string;
    price_from: number;
    price_to: number;
    active_until: string | null;
  } | null;
  ladder_active: boolean;
  ignored: Array<{ merchant_id: string; name: string; price: number; reasons: string[] }>;
  guard_limit: string | null;
  guard_enabled: boolean;
  observe: boolean;
  /** Режим наблюдения: какую цену поставила бы защита (null — ту же). */
  would: { price: number; action: string } | null;
  asks: number;
  pause_until: string | null;
  last_reset_at: string | null;
  ladder_resets_30d: number;
  best_competitor_name: string | null;
  best_competitor_price: number | null;
  checked_at: string | null;
}

export interface AttackRival {
  merchant_id: string;
  name: string | null;
  skus: string[];
  sku_count: number;
  kinds: string[];
  reviews?: number | null;
  rating?: number | null;
  age_days?: number | null;
}

export interface AttackReport {
  items: AttackItem[];
  rivals: AttackRival[];
  summary: {
    under_attack: number;
    ladders: number;
    weak_rivals: number;
    lost_per_unit_total: number;
    guarded: number;
    would_change: number;
    ladder_resets_30d: number;
    paused: number;
    pending_asks: number;
  };
  signal_labels: Record<string, string>;
  pending: GuardDecision[];
}

/** Продавец без рейтинга рядом с нами на конкретном товаре — решает менеджер. */
export interface GuardDecision {
  id: number;
  sku: string;
  merchant_id: string;
  merchant_name: string | null;
  product_name: string | null;
  status: 'ASK' | 'IGNORE' | 'CONSIDER';
  price: number | null;
  our_price: number | null;
  /** Наша цена, если этого продавца не учитывать. */
  price_without: number | null;
  url: string | null;
  first_seen: string | null;
  last_seen: string | null;
  decided_at: string | null;
  decided_by: string | null;
}

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
