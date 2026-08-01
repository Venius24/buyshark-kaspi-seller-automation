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
  admin_ids: string[];
  use_tg_proxy: boolean;
  tg_proxy: string;
  merchant_id: string;
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
  | 'logs'
  | 'settings';
