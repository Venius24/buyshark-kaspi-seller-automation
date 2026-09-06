import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Sparkles, Send, Plus, Trash2, Wrench, AlertTriangle } from 'lucide-react';
import { MarkdownLite } from '../MarkdownLite';
import {
  useAssistantActions,
  useAssistantConversations,
  useAssistantMessages,
  useAssistantStatus,
  type AssistantMessage,
} from '../../hooks/useAssistant';

/**
 * Каркас вкладки «ИИ-помощник».
 *
 * Реальной модели за этим пока нет: бэкенд работает на заглушке, пока не
 * задан ASSISTANT_API_KEY (backend/assistant/provider.py). UI это не
 * прячет, а честно показывает баннером — иначе тестировщик решит, что
 * помощник сломан, а не что он ещё не подключён.
 */
export function AssistantTab() {
  const { t } = useTranslation('tab_assistant');
  const { status } = useAssistantStatus();
  const { conversations } = useAssistantConversations();
  const { createConversation, sendMessage, deleteConversation } = useAssistantActions();

  const [activeId, setActiveId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const { messages, isFetching } = useAssistantMessages(activeId);

  // Первый заход: подхватываем последний диалог, чтобы не открывать пустой экран.
  useEffect(() => {
    if (!activeId && conversations.length > 0) setActiveId(conversations[0].id);
  }, [activeId, conversations]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length, sendMessage.isPending]);

  const busy = sendMessage.isPending || createConversation.isPending;

  const handleNew = async () => {
    setError(null);
    try {
      const convo = await createConversation.mutateAsync(undefined);
      setActiveId(convo.id);
    } catch (e) {
      setError(String(e));
    }
  };

  const handleSend = async () => {
    const text = draft.trim();
    if (!text || busy) return;
    setError(null);

    let conversationId = activeId;
    try {
      if (!conversationId) {
        const convo = await createConversation.mutateAsync(undefined);
        conversationId = convo.id;
        setActiveId(convo.id);
      }
      setDraft('');
      await sendMessage.mutateAsync({ conversationId, text });
    } catch (e) {
      setError(String(e));
    }
  };

  const handleDelete = async (id: string) => {
    await deleteConversation.mutateAsync(id).catch(() => undefined);
    if (activeId === id) setActiveId(null);
  };

  const suggestions = useMemo(
    () => [t('suggest_1'), t('suggest_2'), t('suggest_3')],
    [t],
  );

  return (
    <div className="assistant-tab">
      {status.stub_mode && (
        <div className="alert alert-warning" style={bannerStyle}>
          <AlertTriangle size={16} />
          <span>{t('stub_banner')}</span>
        </div>
      )}

      <div style={layoutStyle}>
        {/* Список диалогов */}
        <aside style={sidebarStyle}>
          <button className="btn btn-primary" style={{ width: '100%', marginBottom: 12 }} onClick={handleNew} disabled={busy}>
            <Plus size={15} /> {t('new_chat')}
          </button>

          {conversations.length === 0 && <div style={mutedStyle}>{t('no_chats')}</div>}

          {conversations.map((c) => (
            <div key={c.id} style={{ ...convoRowStyle, background: c.id === activeId ? 'rgba(255,255,255,0.06)' : 'transparent' }}>
              <button style={convoTitleStyle} onClick={() => setActiveId(c.id)} title={c.title}>
                {c.title}
              </button>
              <button style={iconBtnStyle} onClick={() => handleDelete(c.id)} title={t('delete_chat')}>
                <Trash2 size={14} />
              </button>
            </div>
          ))}

          <div style={{ marginTop: 20 }}>
            <div style={sectionTitleStyle}><Wrench size={13} /> {t('tools_title')}</div>
            {status.tools.length === 0 && <div style={mutedStyle}>{t('tools_empty')}</div>}
            {status.tools.map((tool) => (
              <div key={tool.name} style={toolRowStyle} title={tool.description}>
                <code style={{ fontSize: 11 }}>{tool.name}</code>
                {!tool.read_only && <span style={badgeStyle}>{t('tool_write')}</span>}
              </div>
            ))}
          </div>
        </aside>

        {/* Диалог */}
        <section style={chatStyle}>
          <div style={messagesStyle}>
            {messages.length === 0 && !isFetching && (
              <div style={emptyStateStyle}>
                <Sparkles size={30} />
                <h3 style={{ margin: '10px 0 4px' }}>{t('empty_title')}</h3>
                <p style={mutedStyle}>{t('empty_hint')}</p>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center', marginTop: 12 }}>
                  {suggestions.map((s) => (
                    <button key={s} className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setDraft(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((m) => (
              <MessageBubble key={m.id} message={m} toolsLabel={t('used_tools')} />
            ))}

            {sendMessage.isPending && <div style={mutedStyle}>{t('thinking')}</div>}
            <div ref={bottomRef} />
          </div>

          {error && <div className="alert alert-danger" style={{ margin: '0 12px 8px' }}>{error}</div>}

          <div style={composerStyle}>
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              placeholder={t('placeholder')}
              rows={2}
              style={textareaStyle}
              disabled={busy || !status.enabled}
            />
            <button className="btn btn-primary" onClick={handleSend} disabled={busy || !draft.trim() || !status.enabled}>
              <Send size={15} />
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}

function MessageBubble({ message, toolsLabel }: { message: AssistantMessage; toolsLabel: string }) {
  const isUser = message.role === 'user';
  return (
    <div style={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start', marginBottom: 10 }}>
      <div style={{ ...bubbleStyle, background: isUser ? 'rgba(52,211,153,0.12)' : 'rgba(255,255,255,0.05)' }}>
        {/* Свой вопрос показываем как есть: пользователь не пишет markdown, а
            вот сломать ему звёздочками собственный текст — легко. */}
        {isUser
          ? <div style={{ whiteSpace: 'pre-wrap' }}>{message.content}</div>
          : <MarkdownLite text={message.content} />}
        {message.tool_trace.length > 0 && (
          <div style={traceStyle}>
            <div style={{ marginBottom: 4 }}>{toolsLabel}</div>
            {message.tool_trace.map((call, i) => (
              <div key={`${call.name}-${i}`}>
                <code>{call.name}</code>
                {call.result?.ok === false && <span style={{ color: '#f87171' }}> — {call.result.error}</span>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// Инлайн-стили: вкладка каркасная, отдельный CSS-раздел заводить рано —
// когда UI устоится, переедет в App.css рядом с остальными вкладками.
const bannerStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 };
const layoutStyle: React.CSSProperties = { display: 'flex', gap: 16, alignItems: 'stretch', minHeight: 520 };
const sidebarStyle: React.CSSProperties = { width: 240, flexShrink: 0, borderRight: '1px solid rgba(255,255,255,0.08)', paddingRight: 12 };
const chatStyle: React.CSSProperties = { flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 };
const messagesStyle: React.CSSProperties = { flex: 1, overflowY: 'auto', padding: 12, maxHeight: 520 };
const composerStyle: React.CSSProperties = { display: 'flex', gap: 8, padding: 12, borderTop: '1px solid rgba(255,255,255,0.08)' };
const textareaStyle: React.CSSProperties = { flex: 1, resize: 'vertical', padding: 8, borderRadius: 8 };
const bubbleStyle: React.CSSProperties = { maxWidth: '80%', padding: '10px 12px', borderRadius: 12, fontSize: 14, lineHeight: 1.5 };
const traceStyle: React.CSSProperties = { marginTop: 8, paddingTop: 6, borderTop: '1px dashed rgba(255,255,255,0.15)', fontSize: 11, opacity: 0.75 };
const emptyStateStyle: React.CSSProperties = { textAlign: 'center', padding: '60px 20px', opacity: 0.85 };
const mutedStyle: React.CSSProperties = { fontSize: 12, opacity: 0.6 };
const sectionTitleStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, opacity: 0.7, marginBottom: 6 };
const convoRowStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', borderRadius: 8, marginBottom: 2 };
const convoTitleStyle: React.CSSProperties = { flex: 1, textAlign: 'left', background: 'none', border: 'none', color: 'inherit', padding: '7px 8px', fontSize: 13, cursor: 'pointer', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' };
const iconBtnStyle: React.CSSProperties = { background: 'none', border: 'none', color: 'inherit', opacity: 0.5, cursor: 'pointer', padding: 6 };
const toolRowStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 6, padding: '3px 0', opacity: 0.8 };
const badgeStyle: React.CSSProperties = { fontSize: 10, padding: '1px 5px', borderRadius: 4, background: 'rgba(224,163,62,0.2)', color: '#e0a33e' };
