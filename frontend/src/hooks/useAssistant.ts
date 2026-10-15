import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';
import { useActiveStoreId } from '../storeContext';

export interface AssistantToolInfo {
  name: string;
  description: string;
  read_only: boolean;
  min_role: string;
}

export interface AssistantStatus {
  enabled: boolean;
  provider: string;
  model: string;
  has_api_key: boolean;
  /** Модель не подключена — ответы формирует заглушка (см. backend/assistant/provider.py). */
  stub_mode: boolean;
  allow_write_tools: boolean;
  tools: AssistantToolInfo[];
}

export interface AssistantToolTraceItem {
  name: string;
  arguments: Record<string, unknown>;
  result: { ok?: boolean; error?: string; result?: unknown };
}

export interface AssistantMessage {
  id: string;
  role: 'user' | 'assistant' | 'tool' | 'system';
  content: string;
  tool_trace: AssistantToolTraceItem[];
  provider: string | null;
  model: string | null;
  created_at: string | null;
}

export interface AssistantConversation {
  id: string;
  title: string;
  created_at: string | null;
  updated_at: string | null;
}

const EMPTY_STATUS: AssistantStatus = {
  enabled: false,
  provider: 'stub',
  model: '',
  has_api_key: false,
  stub_mode: true,
  allow_write_tools: false,
  tools: [],
};

const BASE = () => `${API_URL}/api/v1/assistant`;

async function getJson(url: string) {
  const res = await apiFetch(url);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
  return data;
}

export function useAssistantStatus() {
  const storeId = useActiveStoreId();
  const { data, isLoading } = useQuery({
    queryKey: ['assistant-status', storeId],
    queryFn: async () => {
      try {
        return (await getJson(`${BASE()}/status`)) as AssistantStatus;
      } catch {
        return EMPTY_STATUS;
      }
    },
    staleTime: 60_000,
  });
  return { status: data ?? EMPTY_STATUS, isLoading };
}

export function useAssistantConversations() {
  const storeId = useActiveStoreId();
  const { data, isFetching } = useQuery({
    queryKey: ['assistant-conversations', storeId],
    queryFn: async () => {
      const data = await getJson(`${BASE()}/conversations`);
      return (data.conversations || []) as AssistantConversation[];
    },
  });
  return { conversations: data ?? [], isFetching };
}

export function useAssistantMessages(conversationId: string | null) {
  const storeId = useActiveStoreId();
  const { data, isFetching } = useQuery({
    queryKey: ['assistant-messages', storeId, conversationId],
    queryFn: async () => {
      if (!conversationId) return [] as AssistantMessage[];
      const data = await getJson(`${BASE()}/conversations/${conversationId}/messages`);
      return (data.messages || []) as AssistantMessage[];
    },
    enabled: !!conversationId,
  });
  return { messages: data ?? [], isFetching };
}

export function useAssistantActions() {
  const queryClient = useQueryClient();
  const storeId = useActiveStoreId();

  const invalidateConversations = () =>
    queryClient.invalidateQueries({ queryKey: ['assistant-conversations', storeId] });

  const createConversation = useMutation({
    mutationFn: async (title?: string) => {
      const res = await apiFetch(`${BASE()}/conversations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: title || null }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
      return data.conversation as AssistantConversation;
    },
    onSuccess: invalidateConversations,
  });

  const sendMessage = useMutation({
    mutationFn: async ({ conversationId, text, confirmed }: { conversationId: string; text: string; confirmed?: boolean }) => {
      const res = await apiFetch(`${BASE()}/conversations/${conversationId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, confirmed: !!confirmed }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : `HTTP ${res.status}`);
      // truncated = модель упёрлась в лимит обращений к данным, и ответ
      // собран из того, что успела получить. Флаг живёт только в этом
      // ответе: в БД он не сохраняется, после перезагрузки предупреждения
      // не будет (см. HANDOFF §3.93).
      return { messages: data.messages as AssistantMessage[], truncated: !!data.truncated };
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['assistant-messages', storeId, variables.conversationId] });
      invalidateConversations();
    },
  });

  const deleteConversation = useMutation({
    mutationFn: async (conversationId: string) => {
      const res = await apiFetch(`${BASE()}/conversations/${conversationId}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return conversationId;
    },
    onSuccess: invalidateConversations,
  });

  return { createConversation, sendMessage, deleteConversation };
}

export type AssistantProposalStatus = 'pending' | 'approved' | 'rejected' | 'failed';

export interface AssistantProposal {
  id: string;
  store_id: string;
  conversation_id: string | null;
  sku: string;
  old_price: number | null;
  new_price: number;
  min_price_at_creation: number | null;
  max_price_at_creation: number | null;
  reason: string;
  status: AssistantProposalStatus;
  created_by_user_id: string | null;
  created_at: string | null;
  decided_by_user_id: string | null;
  decided_at: string | null;
  apply_result: Record<string, unknown> | null;
}

export class AssistantProposalError extends Error {
  readonly status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

async function proposalJson(url: string, init?: RequestInit) {
  const res = await apiFetch(url, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new AssistantProposalError(res.status, typeof data?.detail === 'string' ? data.detail : `HTTP ${res.status}`);
  }
  return data;
}

export function useAssistantProposals(status?: AssistantProposalStatus) {
  const storeId = useActiveStoreId();
  const query = useQuery({
    queryKey: ['assistant-proposals', storeId, status],
    queryFn: async () => {
      const data = await proposalJson(`${BASE()}/proposals${status ? `?status=${status}` : ''}`);
      return (data.proposals || []) as AssistantProposal[];
    },
    refetchInterval: (query) => query.state.error instanceof AssistantProposalError && query.state.error.status === 403 ? false : 15_000,
    refetchIntervalInBackground: false,
    retry: (count, error) => !(error instanceof AssistantProposalError && error.status < 500) && count < 2,
  });
  return { ...query, proposals: query.data ?? [] };
}

function useDecideProposal(action: 'approve' | 'reject') {
  const queryClient = useQueryClient();
  const storeId = useActiveStoreId();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['assistant-proposals', storeId] });
  return useMutation({
    mutationFn: async (id: string) => {
      const data = await proposalJson(`${BASE()}/proposals/${encodeURIComponent(id)}/${action}`, { method: 'POST' });
      return data.proposal as AssistantProposal;
    },
    onSuccess: invalidate,
    onError: (error) => {
      if (error instanceof AssistantProposalError && error.status === 409) return invalidate();
    },
  });
}

export function useApproveProposal() {
  return useDecideProposal('approve');
}

export function useRejectProposal() {
  return useDecideProposal('reject');
}
