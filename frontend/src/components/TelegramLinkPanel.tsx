import { useEffect, useState } from 'react';
import { Smartphone, Link2, Mail, Unplug } from 'lucide-react';
import { apiFetch, API_URL } from '../api';

interface TelegramMember {
  tg_user_id: number;
  tg_username: string | null;
  tg_first_name: string | null;
  role: string;
  linked_at: string | null;
}

const ROLE_TITLES: Record<string, string> = {
  OWNER: 'Владелец',
  MANAGER: 'Менеджер',
  VIEWER: 'Наблюдатель',
};

export function TelegramLinkPanel() {
  const [members, setMembers] = useState<TelegramMember[]>([]);
  const [yourRole, setYourRole] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selfLink, setSelfLink] = useState<string | null>(null);
  const [inviteLink, setInviteLink] = useState<string | null>(null);
  const [inviteRole, setInviteRole] = useState<'MANAGER' | 'VIEWER'>('MANAGER');
  const [error, setError] = useState<string | null>(null);

  const loadMembers = () => {
    setLoading(true);
    apiFetch(`${API_URL}/api/v1/telegram/members`)
      .then(r => r.json())
      .then(d => {
        if (d.success) {
          setMembers(d.members || []);
          setYourRole(d.your_role);
        }
      })
      .catch(() => setError('Не удалось загрузить список привязанных Telegram-аккаунтов.'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadMembers();
  }, []);

  const createSelfLink = () => {
    setError(null);
    setSelfLink(null);
    apiFetch(`${API_URL}/api/v1/telegram/link-token`, { method: 'POST' })
      .then(r => r.json())
      .then(d => {
        if (d.deep_link) setSelfLink(d.deep_link);
        else setError('Бот сейчас недоступен, попробуйте позже.');
      })
      .catch(() => setError('Не удалось создать ссылку привязки.'));
  };

  const createInviteLink = () => {
    setError(null);
    setInviteLink(null);
    apiFetch(`${API_URL}/api/v1/telegram/invite-token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role: inviteRole, max_uses: 1 }),
    })
      .then(r => r.json())
      .then(d => {
        if (d.deep_link) setInviteLink(d.deep_link);
        else setError('Бот сейчас недоступен, попробуйте позже.');
      })
      .catch(() => setError('Не удалось создать ссылку приглашения.'));
  };

  const removeMember = (tgUserId: number) => {
    if (!confirm('Отвязать этого сотрудника от магазина?')) return;
    apiFetch(`${API_URL}/api/v1/telegram/members/${tgUserId}`, { method: 'DELETE' })
      .then(r => r.json())
      .then(d => {
        if (d.success) loadMembers();
        else setError('Не удалось отвязать сотрудника.');
      })
      .catch(() => setError('Не удалось отвязать сотрудника.'));
  };

  const isOwner = yourRole === 'OWNER';

  return (
    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div>
        <h2>Telegram-бот</h2>
        <p style={{ marginTop: '4px', fontSize: '12px', color: 'var(--text-muted)' }}>
          Управляйте магазином с телефона: автосборка, накопленная печать, уведомления о чатах и заказах.
        </p>
      </div>

      {error && <div className="badge badge-warning" style={{ width: 'fit-content' }}>{error}</div>}

      <div className="status-widget" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: '10px' }}>
        <h3 style={{ fontSize: '15px', fontWeight: 600 }}>Привязать свой Telegram</h3>
        <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
          Ссылка одноразовая, действует 15 минут. Откройте её с телефона, на котором установлен Telegram.
        </p>
        {selfLink ? (
          <a href={selfLink} target="_blank" rel="noreferrer" className="btn btn-primary" style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
            <Smartphone size={16} /> Открыть в Telegram
          </a>
        ) : (
          <button className="btn btn-primary" onClick={createSelfLink} style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}><Link2 size={16} /> Создать ссылку привязки</button>
        )}
      </div>

      {isOwner && (
        <div className="status-widget" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: '10px' }}>
          <h3 style={{ fontSize: '15px', fontWeight: 600 }}>Пригласить сотрудника</h3>
          <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            Сотруднику не нужен аккаунт в кабинете — он присоединится сразу через Telegram по вашей ссылке.
          </p>
          <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
            <select
              value={inviteRole}
              onChange={e => setInviteRole(e.target.value as 'MANAGER' | 'VIEWER')}
              className="form-input"
              style={{ width: 'auto' }}
            >
              <option value="MANAGER">Менеджер</option>
              <option value="VIEWER">Наблюдатель</option>
            </select>
            <button className="btn btn-secondary" onClick={createInviteLink} style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}><Mail size={16} /> Создать ссылку-приглашение</button>
          </div>
          {inviteLink && (
            <a href={inviteLink} target="_blank" rel="noreferrer" className="btn btn-primary" style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
              <Smartphone size={16} /> Открыть / переслать сотруднику
            </a>
          )}
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        <h3 style={{ fontSize: '15px', fontWeight: 600 }}>Привязанные аккаунты</h3>
        {loading && <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Загрузка...</p>}
        {!loading && members.length === 0 && (
          <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Пока никто не привязан.</p>
        )}
        {members.map(m => (
          <div key={m.tg_user_id} className="status-widget">
            <div>
              <span style={{ fontWeight: 600 }}>
                {m.tg_first_name || 'Без имени'} {m.tg_username ? `(@${m.tg_username})` : ''}
              </span>
              <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                {ROLE_TITLES[m.role] || m.role}
              </div>
            </div>
            {isOwner && (
              <button className="btn btn-secondary" onClick={() => removeMember(m.tg_user_id)} style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
                <Unplug size={15} /> Отвязать
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
