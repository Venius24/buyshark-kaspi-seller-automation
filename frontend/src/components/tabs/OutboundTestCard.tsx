import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Clock, CheckCircle2, SkipForward, XCircle, Construction, FlaskConical } from 'lucide-react';
import type { ReactNode } from 'react';
import { fetchOutboundQueue, testSendOutbound, testFirstContact } from '../../queries';
import { useActiveStoreId } from '../../storeContext';

const STATUS_LABELS: Record<string, ReactNode> = {
  pending: <><Clock size={13} /> В очереди</>,
  sent: <><CheckCircle2 size={13} /> Отправлено</>,
  skipped: <><SkipForward size={13} /> Пропущено</>,
  failed: <><XCircle size={13} /> Ошибка</>,
  needs_first_contact: <><Construction size={13} /> Нужен первый контакт</>,
};

export function OutboundTestCard() {
  const activeStoreId = useActiveStoreId();
  const queryClient = useQueryClient();
  const { data: items = [] } = useQuery({
    queryKey: ['outbound-queue', activeStoreId],
    queryFn: fetchOutboundQueue,
    refetchInterval: 15_000,
  });

  const [orderId, setOrderId] = useState('');
  const [messageText, setMessageText] = useState('Тестовое сообщение каркаса рассылок — можно игнорировать.');
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const handleSend = async () => {
    if (!orderId.trim() || !messageText.trim()) return;
    setSending(true);
    setResult(null);
    try {
      const res = await testSendOutbound(orderId.trim(), messageText.trim());
      setResult(res.message);
      queryClient.invalidateQueries({ queryKey: ['outbound-queue', activeStoreId] });
    } finally {
      setSending(false);
    }
  };

  const [fcOrderId, setFcOrderId] = useState('');
  const [fcMessageText, setFcMessageText] = useState('');
  const [fcConfirmed, setFcConfirmed] = useState(false);
  const [fcSending, setFcSending] = useState(false);
  const [fcResult, setFcResult] = useState<string | null>(null);

  const handleFirstContact = async () => {
    if (!fcOrderId.trim() || !fcMessageText.trim() || !fcConfirmed) return;
    setFcSending(true);
    setFcResult(null);
    try {
      const res = await testFirstContact(fcOrderId.trim(), fcMessageText.trim());
      setFcResult(res.message);
    } finally {
      setFcSending(false);
    }
  };

  return (
    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h3 style={{ fontSize: '14px', color: '#fff', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '8px' }}><FlaskConical size={17} /> Каркас рассылок — тест отправки</h3>
        <span className="badge badge-warning">Фаза 3</span>
      </div>

      <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
        Проверка очереди/воркера перед подключением реальных кампаний (самовывоз, отзывы).
        Требует, чтобы по заказу УЖЕ была переписка — автоматический первый контакт ещё не подключён.
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr auto', gap: '8px', alignItems: 'start' }}>
        <input className="form-input" placeholder="order_id заказа" value={orderId} onChange={(e) => setOrderId(e.target.value)} />
        <input className="form-input" placeholder="Текст сообщения" value={messageText} onChange={(e) => setMessageText(e.target.value)} />
        <button className="btn btn-primary" style={{ padding: '10px 16px' }} onClick={handleSend} disabled={sending}>
          {sending ? 'Отправка...' : 'Отправить'}
        </button>
      </div>

      {result && <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{result}</p>}

      <div style={{ borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: '14px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h4 style={{ fontSize: '13px', color: '#fff', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '8px' }}><Construction size={16} /> Тест первого контакта (GUI-автоматизация)</h4>
          <span className="badge badge-warning">Не проверено вживую</span>
        </div>
        <p style={{ fontSize: '11px', color: 'var(--warning)' }}>
          Открывает реальный браузер, находит заказ в категории «Самовывоз» и пытается отправить сообщение через виджет кабинета —
          реальному клиенту. Сама отправка (fill+Enter) не была живьём подтверждена при разработке. Используйте только на заказе,
          где вы готовы, что сообщение реально уйдёт покупателю.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr auto', gap: '8px', alignItems: 'start' }}>
          <input className="form-input" placeholder="order_id заказа (самовывоз)" value={fcOrderId} onChange={(e) => setFcOrderId(e.target.value)} />
          <input className="form-input" placeholder="Текст сообщения клиенту" value={fcMessageText} onChange={(e) => setFcMessageText(e.target.value)} />
          <button className="btn" style={{ padding: '10px 16px', background: 'var(--warning)', color: '#000' }} onClick={handleFirstContact} disabled={fcSending || !fcConfirmed}>
            {fcSending ? 'Отправка...' : 'Отправить'}
          </button>
        </div>
        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: 'var(--text-muted)' }}>
          <input type="checkbox" checked={fcConfirmed} onChange={(e) => setFcConfirmed(e.target.checked)} />
          Понимаю, что это реальная отправка реальному покупателю по указанному заказу.
        </label>
        {fcResult && <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{fcResult}</p>}
      </div>

      {items.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '260px', overflowY: 'auto' }}>
          {items.map((item) => (
            <div key={item.id} style={{ background: 'rgba(255,255,255,0.03)', padding: '10px 14px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.06)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px' }}>
              <div>
                <div style={{ fontSize: '12px', fontWeight: 600, color: '#fff' }}>{item.campaign} · заказ {item.order_id}</div>
                {item.skip_reason && <div style={{ fontSize: '11px', color: 'var(--warning)', marginTop: '2px' }}>{item.skip_reason}</div>}
              </div>
              <span className="badge" style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>{STATUS_LABELS[item.status] || item.status}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
