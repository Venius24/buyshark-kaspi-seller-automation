import { useState, useRef, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../../api';

interface ChatGroup {
  id: string;
  title?: string;
  subtitle?: string;
  lastMessage?: {
    text?: string;
    sender?: string;
    created?: number;
  };
  style?: { iconUrl?: string };
}

interface ChatAttachment {
  type: 'image' | 'video' | 'audio';
  url: string;
  fileName?: string;
  fileSize?: number;
}

interface ChatMessage {
  id: string;
  created: number;
  sender: 'client' | 'messenger'; // "client" = МЫ (магазин), "messenger" = покупатель — см. REAL_IMPORTANT/чаты_kaspi
  senderData?: { name?: string };
  text?: string;
  messageType?: 'TEXT' | 'IMAGE' | 'VIDEO' | 'AUDIO';
  attachment?: ChatAttachment;
}

const fetchChats = async (): Promise<ChatGroup[]> => {
  try {
    const res = await apiFetch(`${API_URL}/api/chats`);
    const data = await res.json();
    return res.ok && data.success ? (data.chats || []) : [];
  } catch {
    return [];
  }
};

const fetchMessages = async (groupId: string): Promise<ChatMessage[]> => {
  try {
    const res = await apiFetch(`${API_URL}/api/chats/${groupId}/messages`);
    const data = await res.json();
    return res.ok && data.success ? (data.messages || []) : [];
  } catch {
    return [];
  }
};

function formatTime(ts?: number): string {
  if (!ts) return '';
  return new Date(ts).toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' });
}

function AttachmentView({ attachment }: { attachment: ChatAttachment }) {
  const icon = attachment.type === 'image' ? '🖼️' : attachment.type === 'video' ? '🎬' : '🎤';
  return (
    <a
      href={attachment.url}
      target="_blank"
      rel="noreferrer"
      style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'inherit', textDecoration: 'underline' }}
    >
      <span>{icon}</span>
      <span>{attachment.fileName || `Вложение (${attachment.type})`}</span>
    </a>
  );
}

export function ChatsTab() {
  const queryClient = useQueryClient();
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [messageInput, setMessageInput] = useState('');
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const { data: chats = [], isLoading: chatsLoading } = useQuery({
    queryKey: ['chats', 'list'],
    queryFn: fetchChats,
    refetchInterval: 10_000,
  });

  const { data: messages = [], isFetching: messagesLoading } = useQuery({
    queryKey: ['chats', selectedGroupId, 'messages'],
    queryFn: () => fetchMessages(selectedGroupId as string),
    enabled: !!selectedGroupId,
    refetchInterval: 5_000,
  });

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length, selectedGroupId]);

  const handleSend = async () => {
    const text = messageInput.trim();
    if (!text || !selectedGroupId || sending) return;
    setSending(true);
    setSendError('');
    try {
      const res = await apiFetch(`${API_URL}/api/chats/${selectedGroupId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        setMessageInput('');
        queryClient.invalidateQueries({ queryKey: ['chats', selectedGroupId, 'messages'] });
        queryClient.invalidateQueries({ queryKey: ['chats', 'list'] });
      } else {
        setSendError(data.detail || 'Не удалось отправить сообщение.');
      }
    } catch {
      setSendError('Ошибка сети при отправке.');
    } finally {
      setSending(false);
    }
  };

  const selectedChat = chats.find((c) => c.id === selectedGroupId);

  return (
    <div className="glass-card" style={{ display: 'flex', height: 'calc(100vh - 160px)', minHeight: '500px', padding: 0, overflow: 'hidden' }}>
      {/* Список диалогов */}
      <div style={{ width: '320px', borderRight: '1px solid rgba(255,255,255,0.08)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ padding: '16px', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
          <h3 style={{ fontSize: '15px', fontWeight: 700, color: '#fff', margin: 0 }}>💬 Чаты Kaspi</h3>
        </div>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {chatsLoading && chats.length === 0 ? (
            <div style={{ padding: '20px', color: 'var(--text-muted)', fontSize: '13px' }}>⏳ Загрузка диалогов...</div>
          ) : chats.length === 0 ? (
            <div style={{ padding: '20px', color: 'var(--text-muted)', fontSize: '13px' }}>
              Нет диалогов. Если это неожиданно — проверьте статус сессии чата во вкладке «ИИ-Автоответчик».
            </div>
          ) : (
            chats.map((chat) => (
              <div
                key={chat.id}
                onClick={() => setSelectedGroupId(chat.id)}
                style={{
                  padding: '12px 16px',
                  cursor: 'pointer',
                  borderBottom: '1px solid rgba(255,255,255,0.04)',
                  background: selectedGroupId === chat.id ? 'rgba(99, 102, 241, 0.12)' : 'transparent',
                }}
              >
                <div style={{ fontSize: '13px', fontWeight: 600, color: '#fff', marginBottom: '2px' }}>
                  {chat.subtitle || chat.title || 'Без имени'}
                </div>
                {chat.subtitle && chat.title && (
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {chat.title}
                  </div>
                )}
                <div style={{ fontSize: '12px', color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {chat.lastMessage?.text || '—'}
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Окно переписки */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {!selectedGroupId ? (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
            Выберите диалог слева
          </div>
        ) : (
          <>
            <div style={{ padding: '16px', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
              <div style={{ fontSize: '14px', fontWeight: 700, color: '#fff' }}>
                {selectedChat?.subtitle || selectedChat?.title || 'Диалог'}
              </div>
              {selectedChat?.subtitle && selectedChat?.title && (
                <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{selectedChat.title}</div>
              )}
            </div>

            <div style={{ flex: 1, overflowY: 'auto', padding: '16px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {messagesLoading && messages.length === 0 ? (
                <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>⏳ Загрузка переписки...</div>
              ) : (
                messages.map((m) => {
                  const isOurs = m.sender === 'client'; // см. предупреждение в типе выше
                  return (
                    <div key={m.id} style={{ display: 'flex', justifyContent: isOurs ? 'flex-end' : 'flex-start' }}>
                      <div
                        style={{
                          maxWidth: '70%',
                          padding: '8px 12px',
                          borderRadius: '12px',
                          background: isOurs ? 'var(--primary)' : 'rgba(255,255,255,0.06)',
                          color: isOurs ? '#fff' : '#e5e7eb',
                        }}
                      >
                        {m.attachment ? <AttachmentView attachment={m.attachment} /> : (
                          <div style={{ fontSize: '13px', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{m.text}</div>
                        )}
                        <div style={{ fontSize: '10px', opacity: 0.6, marginTop: '4px', textAlign: 'right' }}>
                          {formatTime(m.created)}
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
              <div ref={messagesEndRef} />
            </div>

            {sendError && (
              <div style={{ padding: '8px 16px', fontSize: '12px', color: 'var(--error)' }}>{sendError}</div>
            )}

            <div style={{ padding: '12px 16px', borderTop: '1px solid rgba(255,255,255,0.08)', display: 'flex', gap: '8px' }}>
              <input
                type="text"
                className="form-input"
                style={{ flex: 1 }}
                placeholder="Сообщение..."
                value={messageInput}
                onChange={(e) => setMessageInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
              />
              <button className="btn btn-primary" onClick={handleSend} disabled={sending || !messageInput.trim()}>
                {sending ? '...' : 'Отправить'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
