import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Sparkles, Send, Plus, Trash2, Wrench, AlertTriangle } from 'lucide-react';
import { MarkdownLite } from '../MarkdownLite';
import {
  useAssistantActions,
  useAssistantConversations,
  useAssistantMessages,
  useAssistantStatus,
  type AssistantMessage,
  type AssistantToolTraceItem,
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
  // Модель упёрлась в лимит обращений к данным — ответ собран из того, что
  // успела получить. Показываем это явно: неполный ответ, поданный как
  // полный, хуже честного «данных не хватило».
  const [truncated, setTruncated] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);

  // Высота чата считается от его позиции на экране до низа окна.
  //
  // Просто «flex:1 до самого низа» здесь не работает: вся цепочка над нами
  // (#root, .app-shell, .main-content в index.css) задана через min-height,
  // а не height. min-height — это пол, а не потолок, поэтому flex-элемент
  // спокойно вырастает под содержимое, внутренний overflow-y не срабатывает,
  // и вместо прокрутки списка сообщений растёт вся страница. Проверено
  // замером: список раздувался до 8919px при экране 401px.
  //
  // Отсюда же flex:'none' в rootStyle: у флекс-элемента flex-grow перебивает
  // явную height, и с flex:1 измеренная высота просто игнорировалась.
  const [fitHeight, setFitHeight] = useState<number>();
  useLayoutEffect(() => {
    const measure = () => {
      const el = rootRef.current;
      if (!el) return;
      const top = el.getBoundingClientRect().top;
      // 28 — padding-bottom у .main-content (index.css), иначе композер
      // упирается в самый край окна.
      setFitHeight(Math.max(MIN_CHAT_HEIGHT, window.innerHeight - top - 28));
    };
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [status.stub_mode]);

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
    setTruncated(false);

    let conversationId = activeId;
    try {
      if (!conversationId) {
        const convo = await createConversation.mutateAsync(undefined);
        conversationId = convo.id;
        setActiveId(convo.id);
      }
      setDraft('');
      const res = await sendMessage.mutateAsync({ conversationId, text });
      setTruncated(!!res.truncated);
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
    <div className="assistant-tab" ref={rootRef} style={{ ...rootStyle, height: fitHeight }}>
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

          <div style={{ marginTop: 20, flexShrink: 0 }}>
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

          {truncated && (
            <div className="alert alert-warning" style={{ margin: '0 12px 8px', display: 'flex', gap: 8, alignItems: 'center' }}>
              <AlertTriangle size={15} />
              <span style={{ fontSize: 13 }}>{t('truncated')}</span>
            </div>
          )}

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
            {/* Схлопываем повторы: модель может позвать один инструмент
                десяток раз подряд, и десять одинаковых строк в трассе — это
                шум, из которого не видно ни аргументов, ни ошибок. */}
            {collapseTrace(message.tool_trace).map((row, i) => (
              <div key={`${row.name}-${i}`}>
                <code>{row.name}</code>
                {row.count > 1 && <span style={{ opacity: 0.7 }}> ×{row.count}</span>}
                {row.hint && <span style={{ opacity: 0.6 }}> · {row.hint}</span>}
                {row.error && <span style={{ color: '#f87171' }}> — {row.error}</span>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

interface TraceRow {
  name: string;
  count: number;
  hint: string;
  error: string;
}

/** Одинаковые подряд идущие вызовы в одну строку со счётчиком. */
function collapseTrace(trace: AssistantToolTraceItem[]): TraceRow[] {
  const rows: TraceRow[] = [];
  for (const call of trace) {
    const hint = describeArgs(call.arguments);
    const error = call.result?.ok === false ? String(call.result.error || '') : '';
    const last = rows[rows.length - 1];
    if (last && last.name === call.name && last.hint === hint && last.error === error) {
      last.count += 1;
    } else {
      rows.push({ name: call.name, count: 1, hint, error });
    }
  }
  return rows;
}

/** Короткая подпись аргументов: из неё должно быть видно, ЧТО спрашивали. */
function describeArgs(args: Record<string, unknown> | undefined): string {
  if (!args) return '';
  return Object.entries(args)
    .filter(([, v]) => v !== '' && v !== null && v !== undefined)
    .map(([k, v]) => `${k}=${String(v)}`)
    .join(', ')
    .slice(0, 80);
}

// Инлайн-стили: вкладка каркасная, отдельный CSS-раздел заводить рано —
// когда UI устоится, переедет в App.css рядом с остальными вкладками.
const bannerStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 };
// Высота: вкладка тянется на всю оставшуюся высоту экрана, а не живёт в
// коробке на 520px с пустотой под ней. `.main-content` — это флекс-колонка с
// min-height:100vh (index.css), поэтому корню вкладки достаточно flex:1.
//
// minHeight:0 на КАЖДОМ звене цепочки обязателен: у флекс-элемента
// min-height по умолчанию auto, он распирается содержимым, и внутренний
// overflow-y:auto тогда не срабатывает — вместо прокрутки списка сообщений
// растёт вся страница.
const MIN_CHAT_HEIGHT = 360;

const rootStyle: React.CSSProperties = { display: 'flex', flexDirection: 'column', flex: 'none', minHeight: MIN_CHAT_HEIGHT };
const layoutStyle: React.CSSProperties = { display: 'flex', gap: 16, alignItems: 'stretch', flex: 1, minHeight: 0 };
const sidebarStyle: React.CSSProperties = {
  width: 240,
  flexShrink: 0,
  borderRight: '1px solid rgba(255,255,255,0.08)',
  paddingRight: 12,
  display: 'flex',
  flexDirection: 'column',
  overflowY: 'auto',
  minHeight: 0,
};
const chatStyle: React.CSSProperties = { flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0 };
const messagesStyle: React.CSSProperties = { flex: 1, overflowY: 'auto', padding: 12, minHeight: 0 };
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
