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
      return data.messages as AssistantMessage[];
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
