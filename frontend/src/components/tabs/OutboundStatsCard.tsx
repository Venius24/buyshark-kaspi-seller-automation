import { useQuery } from '@tanstack/react-query';
import { fetchOutboundStats } from '../../queries';
import { useActiveStoreId } from '../../storeContext';

const CAMPAIGN_LABELS: Record<string, string> = {
  pickup: '📍 Самовывоз',
  review: '⭐ Отзыв',
  test: '🧪 Тест',
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
        <h3 style={{ fontSize: '14px', color: '#fff', fontWeight: 600 }}>📊 Статистика рассылок</h3>
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
            <span style={{ fontSize: '13px', fontWeight: 600, color: '#fff' }}>{CAMPAIGN_LABELS[campaign] || campaign}</span>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{stats.total} всего</span>
          </div>

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', fontSize: '11px' }}>
            <span className="badge">✅ Отправлено: {stats.sent}</span>
            <span className="badge">⏳ В очереди: {stats.pending}</span>
            {stats.needs_first_contact > 0 && <span className="badge badge-warning">🚧 Нужен первый контакт: {stats.needs_first_contact}</span>}
            {stats.skipped > 0 && <span className="badge">⏭️ Пропущено: {stats.skipped}</span>}
            {stats.failed > 0 && <span className="badge" style={{ background: 'var(--danger, #e5484d)', color: '#fff' }}>❌ Ошибка: {stats.failed}</span>}
          </div>

          <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            💬 Доля ответивших: <strong style={{ color: '#fff' }}>{Math.round(stats.reply_rate * 100)}%</strong>
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
