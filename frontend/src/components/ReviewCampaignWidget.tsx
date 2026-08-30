import { useQuery } from '@tanstack/react-query';
import { Star, CheckCircle2, MessageSquare } from 'lucide-react';
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { fetchOutboundStats } from '../queries';
import { useActiveStoreId } from '../storeContext';
import { useRatingHistory } from '../hooks/useRatingHistory';
import { HelpHint } from './shared/HelpHint';

const PERIOD_DAYS = 30;

export function ReviewCampaignWidget() {
  const activeStoreId = useActiveStoreId();
  const { data: outboundStats } = useQuery({
    queryKey: ['outbound-stats', activeStoreId],
    queryFn: () => fetchOutboundStats(PERIOD_DAYS),
    refetchInterval: 60_000,
  });
  const ratingHistory = useRatingHistory(PERIOD_DAYS);

  const review = outboundStats?.by_campaign?.review;
  const replyRatePct = review ? Math.round(review.reply_rate * 100) : 0;
  const hasRatingTrend = ratingHistory.filter((p) => p.rating_percentage != null).length >= 2;

  return (
    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h3 style={{ fontSize: '14px', color: '#fff', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Star size={17} /> Рассылка «Отзыв»
          <HelpHint text="Kaspi не даёт связку между конкретным отзывом и заказом/рассылкой — ссылка в сообщении только открывает форму отзыва с предзаполненной оценкой, дальше клиент сам пишет и отправляет. Поэтому здесь воронка отправки (сколько ушло сообщений и сколько клиентов ответили в чат), а не «конверсия в отзывы», плюс тренд общего рейтинга магазина рядом — как косвенный ориентир, не причинно-следственная связь." />
        </h3>
        <span className="badge badge-warning">{PERIOD_DAYS} дн.</span>
      </div>

      {!review || review.total === 0 ? (
        <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
          Пока нет ни одной рассылки «Отзыв» за период — появится после первых сообщений кампании.
        </p>
      ) : (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', fontSize: '11px' }}>
          <span className="badge" style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
            <CheckCircle2 size={13} /> Отправлено: {review.sent}
          </span>
          <span className="badge" style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
            <MessageSquare size={13} /> Ответили в чат: {review.replied} ({replyRatePct}%)
          </span>
        </div>
      )}

      <div>
        <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontWeight: 600, marginBottom: '6px' }}>
          РЕЙТИНГ МАГАЗИНА, %
        </div>
        {!hasRatingTrend ? (
          <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            Недостаточно ночных снапшотов показателей качества для графика — появится через несколько дней.
          </p>
        ) : (
          <div style={{ height: '90px' }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={ratingHistory} margin={{ top: 4, right: 4, left: 4, bottom: 0 }}>
                <XAxis dataKey="date" hide />
                <YAxis hide domain={['dataMin - 2', 'dataMax + 2']} />
                <Tooltip
                  formatter={(value: number) => `${value}%`}
                  labelFormatter={(label) => String(label)}
                  contentStyle={{ background: 'rgba(10,10,16,0.9)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', fontSize: '12px' }}
                />
                <Line type="monotone" dataKey="rating_percentage" stroke="#e8c15f" strokeWidth={2} dot={false} connectNulls />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  );
}
