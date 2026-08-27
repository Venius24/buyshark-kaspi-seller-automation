import { useState, useRef, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Image, Video, Mic } from 'lucide-react';
import { apiFetch, API_URL } from '../../api';
import { useActiveStoreId } from '../../storeContext';

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
  analytics?: { order_id?: string };
  is_human_override?: boolean;
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

// Стартовый набор — легко расширить/отредактировать под реальные частые вопросы.
const QUICK_REPLIES: Record<'ru' | 'kk', string[]> = {
  ru: [
    'Здравствуйте! Уточните, пожалуйста, детали вашего вопроса.',
    'Наш адрес: ЖК City Plus, Толе би 285/8, цокольный этаж. Работаем по будням до 16:00.',
    'Извините за неудобства, разберёмся и решим вопрос.',
    'Спасибо за обращение! Хорошего дня.',
    'Один момент, уточняю информацию.',
  ],
  kk: [
    'Сәлеметсіз бе! Сұрағыңыздың егжей-тегжейін нақтылаңызшы.',
    'Мекен-жайымыз: ЖК City Plus, Төле би 285/8, цокольный қабат. Жұмыс күндері сағат 16:00-ге дейін жұмыс істейміз.',
    'Ыңғайсыздық үшін кешірім сұраймыз, мәселені шешеміз.',
    'Хабарласқаныңыз үшін рахмет! Қайырлы күн.',
    'Бір сәт, ақпаратты нақтылап жатырмын.',
  ],
};

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
  const { t } = useTranslation('tab_chats');
  const Icon = attachment.type === 'image' ? Image : attachment.type === 'video' ? Video : Mic;
  return (
    <a
      href={attachment.url}
      target="_blank"
      rel="noreferrer"
      style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'inherit', textDecoration: 'underline' }}
    >
      <span style={{ display: 'flex' }}><Icon size={15} /></span>
      <span>{attachment.fileName || t('attachment_file', { type: attachment.type })}</span>
    </a>
  );
}

export function ChatsTab() {
  const { t } = useTranslation('tab_chats');
  const queryClient = useQueryClient();
  const activeStoreId = useActiveStoreId();
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [messageInput, setMessageInput] = useState('');
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState('');
  const [takingOver, setTakingOver] = useState(false);
  const [quickReplyLang, setQuickReplyLang] = useState<'ru' | 'kk'>('ru');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const { data: chats = [], isLoading: chatsLoading } = useQuery({
    queryKey: ['chats', activeStoreId, 'list'],
    queryFn: fetchChats,
    refetchInterval: 10_000,
  });

  const { data: messages = [], isFetching: messagesLoading } = useQuery({
    queryKey: ['chats', activeStoreId, selectedGroupId, 'messages'],
    queryFn: () => fetchMessages(selectedGroupId as string),
    enabled: !!selectedGroupId,
    refetchInterval: 5_000,
  });

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length, selectedGroupId]);

  const refreshAfterAction = () => {
    queryClient.invalidateQueries({ queryKey: ['chats', activeStoreId, selectedGroupId, 'messages'] });
    queryClient.invalidateQueries({ queryKey: ['chats', activeStoreId, 'list'] });
  };

  const sendText = async (text: string, override: boolean): Promise<boolean> => {
    if (!text.trim() || !selectedGroupId) return false;
    setSending(true);
    setSendError('');
    try {
      const res = await apiFetch(`${API_URL}/api/chats/${selectedGroupId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text.trim(), override }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        refreshAfterAction();
        return true;
      }
      setSendError(data.detail || t('error_send_failed'));
      return false;
    } catch {
      setSendError(t('error_network_send'));
      return false;
    } finally {
      setSending(false);
    }
  };

  const handleSend = async () => {
    const ok = await sendText(messageInput, false);
    if (ok) setMessageInput('');
  };

  const handleQuickReply = async (text: string) => {
    await sendText(text, true);
  };

  const handleTakeOver = async () => {
    if (!selectedGroupId || takingOver) return;
    setTakingOver(true);
    try {
      const res = await apiFetch(`${API_URL}/api/chats/${selectedGroupId}/take-over`, { method: 'POST' });
      const data = await res.json();
      if (res.ok && data.success) {
        refreshAfterAction();
      } else {
        setSendError(data.detail || t('error_takeover_failed'));
      }
    } catch {
      setSendError(t('error_network'));
    } finally {
      setTakingOver(false);
    }
  };

  const selectedChat = chats.find((c) => c.id === selectedGroupId);
  const kaspiWebLink = selectedGroupId ? `https://msg-web.kaspi.kz/#/chats/chat/${selectedGroupId}` : '';
  const kaspiPayLink = selectedGroupId ? `https://pay.kaspi.kz/chat?threadId=${selectedGroupId}&isWeb=true` : '';

  return (
    <div className="glass-card" style={{ display: 'flex', height: 'calc(100vh - 160px)', minHeight: '500px', padding: 0, overflow: 'hidden' }}>
      {/* Список диалогов */}
      <div style={{ width: '320px', borderRight: '1px solid rgba(255,255,255,0.08)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ padding: '16px', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
          <h3 style={{ fontSize: '15px', fontWeight: 700, color: '#fff', margin: 0 }}>{t('list_title')}</h3>
        </div>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {chatsLoading && chats.length === 0 ? (
            <div style={{ padding: '20px', color: 'var(--text-muted)', fontSize: '13px' }}>{t('chats_loading')}</div>
          ) : chats.length === 0 ? (
            <div style={{ padding: '20px', color: 'var(--text-muted)', fontSize: '13px' }}>
              {t('empty_message')}
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
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '2px' }}>
                  <div style={{ fontSize: '13px', fontWeight: 600, color: '#fff' }}>
                    {chat.subtitle || chat.title || t('unnamed_chat')}
                  </div>
                  {chat.is_human_override && (
                    <span className="badge badge-warning" style={{ fontSize: '9px', padding: '1px 6px' }}>{t('override_badge_short')}</span>
                  )}
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
            {t('select_placeholder')}
          </div>
        ) : (
          <>
            <div style={{ padding: '16px', borderBottom: '1px solid rgba(255,255,255,0.08)', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '10px' }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <span style={{ fontSize: '14px', fontWeight: 700, color: '#fff' }}>
                    {selectedChat?.subtitle || selectedChat?.title || t('dialog_title')}
                  </span>
                  {selectedChat?.is_human_override && (
                    <span className="badge badge-warning" style={{ fontSize: '10px' }}>{t('override_badge')}</span>
                  )}
                </div>
                {selectedChat?.title && (
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>
                    {t('product_label')} {selectedChat.title}
                    {selectedChat?.analytics?.order_id && <> · {t('order_label')}{selectedChat.analytics.order_id}</>}
                  </div>
                )}
                <div style={{ display: 'flex', gap: '10px', marginTop: '4px' }}>
                  <a href={kaspiWebLink} target="_blank" rel="noreferrer" style={{ fontSize: '11px', color: 'var(--primary)' }}>
                    {t('link_web')}
                  </a>
                  <a href={kaspiPayLink} target="_blank" rel="noreferrer" style={{ fontSize: '11px', color: 'var(--primary)' }}>
                    {t('link_pay')}
                  </a>
                </div>
              </div>

              <button
                className="btn btn-secondary"
                style={{ fontSize: '12px', padding: '8px 14px', whiteSpace: 'nowrap' }}
                onClick={handleTakeOver}
                disabled={takingOver || selectedChat?.is_human_override}
              >
                {selectedChat?.is_human_override ? t('takeover_button_done') : takingOver ? '...' : t('takeover_button')}
              </button>
            </div>

            <div style={{ flex: 1, overflowY: 'auto', padding: '16px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {messagesLoading && messages.length === 0 ? (
                <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>{t('messages_loading')}</div>
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

            <div style={{ padding: '10px 16px', borderTop: '1px solid rgba(255,255,255,0.08)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{t('quick_replies_label')}</span>
                <div style={{ display: 'flex', gap: '4px' }}>
                  {(['ru', 'kk'] as const).map((lang) => (
                    <button
                      key={lang}
                      onClick={() => setQuickReplyLang(lang)}
                      className="btn btn-secondary"
                      style={{
                        fontSize: '10px',
                        padding: '3px 10px',
                        background: quickReplyLang === lang ? 'var(--primary)' : undefined,
                      }}
                    >
                      {lang.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>
              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '10px' }}>
                {QUICK_REPLIES[quickReplyLang].map((reply, i) => (
                  <button
                    key={i}
                    className="btn btn-secondary"
                    style={{ fontSize: '11px', padding: '5px 10px', maxWidth: '220px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                    title={reply}
                    onClick={() => handleQuickReply(reply)}
                    disabled={sending}
                  >
                    {reply}
                  </button>
                ))}
              </div>

              <div style={{ display: 'flex', gap: '8px' }}>
                <input
                  type="text"
                  className="form-input"
                  style={{ flex: 1 }}
                  placeholder={t('input_placeholder')}
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
                  {sending ? '...' : t('send_button')}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
