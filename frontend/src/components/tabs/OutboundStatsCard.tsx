import { useQuery } from '@tanstack/react-query';
import { MapPin, Star, FlaskConical, BarChart3, CheckCircle2, Clock, Construction, SkipForward, XCircle, MessageSquare } from 'lucide-react';
import type { ReactNode } from 'react';
import { fetchOutboundStats } from '../../queries';
import { useActiveStoreId } from '../../storeContext';

const CAMPAIGN_LABELS: Record<string, ReactNode> = {
  pickup: <><MapPin size={13} /> Самовывоз</>,
  review: <><Star size={13} /> Отзыв</>,
  test: <><FlaskConical size={13} /> Тест</>,
};

export function OutboundStatsCard() {
  const activeStoreId = useActiveStoreId();
  const { data } = useQuery({
    queryKey: ['outbound-stats', activeStoreId],
    queryFn: () => fetchOutboundStats(30),
    refetchInterval: 60_000,
  });

  const campaigns = Object.entries(data?.by_campaign || {}).filter(([campaign]) => campaign !== 'test');

  return (
    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h3 style={{ fontSize: '14px', color: '#fff', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '8px' }}><BarChart3 size={17} /> Статистика рассылок</h3>
        <span className="badge badge-warning">Фаза 6 · {data?.period_days ?? 30} дн.</span>
      </div>

      {campaigns.length === 0 && (
        <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
          Пока нет ни одной рассылки за период — появится после первых сообщений кампаний «Самовывоз»/«Отзыв».
        </p>
      )}

      {campaigns.map(([campaign, stats]) => (
        <div key={campaign} style={{ background: 'rgba(255,255,255,0.03)', padding: '12px 14px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.06)', display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: '13px', fontWeight: 600, color: '#fff', display: 'flex', alignItems: 'center', gap: '6px' }}>{CAMPAIGN_LABELS[campaign] || campaign}</span>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{stats.total} всего</span>
          </div>

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', fontSize: '11px' }}>
            <span className="badge" style={{ display: 'flex', alignItems: 'center', gap: '5px' }}><CheckCircle2 size={13} /> Отправлено: {stats.sent}</span>
            <span className="badge" style={{ display: 'flex', alignItems: 'center', gap: '5px' }}><Clock size={13} /> В очереди: {stats.pending}</span>
            {stats.needs_first_contact > 0 && <span className="badge badge-warning" style={{ display: 'flex', alignItems: 'center', gap: '5px' }}><Construction size={13} /> Нужен первый контакт: {stats.needs_first_contact}</span>}
            {stats.skipped > 0 && <span className="badge" style={{ display: 'flex', alignItems: 'center', gap: '5px' }}><SkipForward size={13} /> Пропущено: {stats.skipped}</span>}
            {stats.failed > 0 && <span className="badge" style={{ background: 'var(--danger, #e5484d)', color: '#fff', display: 'flex', alignItems: 'center', gap: '5px' }}><XCircle size={13} /> Ошибка: {stats.failed}</span>}
          </div>

          <div style={{ fontSize: '12px', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '6px' }}>
            <MessageSquare size={14} /> Доля ответивших: <strong style={{ color: '#fff' }}>{Math.round(stats.reply_rate * 100)}%</strong>
            {' '}({stats.replied} из {stats.sent} отправленных ответили в чате)
          </div>

          {Object.keys(stats.skip_reasons).length > 0 && (
            <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
              Причины пропуска: {Object.entries(stats.skip_reasons).map(([reason, cnt]) => `${reason} (${cnt})`).join(', ')}
            </div>
          )}
        </div>
      ))}

      <p style={{ fontSize: '10px', color: 'var(--text-muted)', opacity: 0.7 }}>
        «Доля ответивших» — не конверсия в отзывы: Kaspi не даёт связку между отзывом и конкретным заказом/чатом,
        это просто факт ответа клиента в переписке после нашего сообщения.
      </p>
    </div>
  );
}
