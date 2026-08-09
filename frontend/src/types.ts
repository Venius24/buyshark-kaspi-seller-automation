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
  dumping_config?: any;
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
  prompt_tier: 'base' | 'plus' | 'pro' | 'max';
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
  | 'logs'
  | 'settings';
