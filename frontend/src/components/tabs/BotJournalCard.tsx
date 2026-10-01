import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ScrollText, Bot, UserRound, Truck, AlertTriangle, CircleSlash } from 'lucide-react';
import type { ReactNode } from 'react';
import { fetchBotJournal } from '../../queries';
import { useActiveStoreId } from '../../storeContext';

const KIND_LABELS: Record<string, ReactNode> = {
  inbound_reply: <><Bot size={13} /> Ответ ИИ</>,
  inbound_logistics_template: <><Truck size={13} /> Шаблон «логистика»</>,
  inbound_escalated_trigger: <><UserRound size={13} /> Менеджеру: слово-триггер</>,
  inbound_escalated_ai: <><UserRound size={13} /> Менеджеру: решила модель</>,
  inbound_skipped_no_credits: <><CircleSlash size={13} /> Пропущено: лимит</>,
  inbound_send_failed: <><AlertTriangle size={13} /> Не отправилось</>,
};

const FILTERS: { value: string; label: string }[] = [
  { value: '', label: 'Все' },
  { value: 'escalated', label: 'Менеджеру' },
  { value: 'inbound_reply', label: 'Ответы ИИ' },
  { value: 'inbound_logistics_template', label: 'Шаблоны' },
];

const textBox = {
  fontSize: '12px',
  whiteSpace: 'pre-wrap' as const,
  wordBreak: 'break-word' as const,
  padding: '8px 10px',
  borderRadius: '6px',
};

export function BotJournalCard() {
  const activeStoreId = useActiveStoreId();
  const [kind, setKind] = useState('');
  const { data } = useQuery({
    queryKey: ['bot-journal', activeStoreId, kind],
    queryFn: () => fetchBotJournal(7, kind),
    refetchInterval: 60_000,
  });

  const items = data?.items || [];

  return (
    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
        <h3 style={{ fontSize: '14px', color: '#fff', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '8px' }}><ScrollText size={17} /> Журнал ответов</h3>
        <span className="badge">{data?.period_days ?? 7} дн.</span>
      </div>

      {data && data.handled > 0 && (
        <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
          Обработано сообщений: <strong style={{ color: '#fff' }}>{data.handled}</strong>, передано менеджеру:{' '}
          <strong style={{ color: '#fff' }}>{data.escalated}</strong> ({Math.round(data.escalation_rate * 100)}%)
        </div>
      )}

      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
        {FILTERS.map(f => (
          <button
            key={f.value}
            type="button"
            className={kind === f.value ? 'btn btn-primary' : 'btn'}
            style={{ fontSize: '11px', padding: '4px 10px' }}
            onClick={() => setKind(f.value)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {items.length === 0 && (
        <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
          За период записей нет. Тексты сообщений журналируются с 02.10.2026 — более ранние ответы здесь без текста.
        </p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', maxHeight: '520px', overflowY: 'auto' }}>
        {items.map(item => (
          <div key={item.id} style={{ background: 'rgba(255,255,255,0.03)', padding: '10px 12px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.06)', display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
              <span className="badge" style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '11px' }}>{KIND_LABELS[item.kind] || item.kind}</span>
              <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                {item.order_code ? `Заказ №${item.order_code} · ` : ''}{new Date(item.created_at).toLocaleString('ru-RU')}
              </span>
            </div>
            {item.client_text && (
              <div style={{ ...textBox, background: 'rgba(255,255,255,0.04)', color: '#fff' }}>{item.client_text}</div>
            )}
            {item.reply_text && (
              <div style={{ ...textBox, background: 'rgba(99,102,241,0.12)', color: '#fff' }}>{item.reply_text}</div>
            )}
            {item.detail && (
              <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{item.detail}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
