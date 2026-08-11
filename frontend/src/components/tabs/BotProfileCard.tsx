import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchBotProfile, fetchBotUsage, saveBotProfile } from '../../queries';
import { useActiveStoreId } from '../../storeContext';
import type { BotProfile, PickupPoint } from '../../types';

const EMPTY_POINT: PickupPoint = { name: '', address: '', map_url: '', hours: '', notes: '' };

const TIER_LABELS: Record<BotProfile['prompt_tier'], string> = {
  band0: 'Базовый (0-500 символов)',
  band1: 'Plus (500-2000, +1000₸/мес)',
  band2: 'Pro (2000-5000, +2500₸/мес)',
  band3: 'Max (5000+, +5000₸/мес)',
};

export function BotProfileCard() {
  const activeStoreId = useActiveStoreId();
  const queryClient = useQueryClient();
  const { data: profile } = useQuery({
    queryKey: ['bot-profile', activeStoreId],
    queryFn: fetchBotProfile,
  });
  const { data: usage } = useQuery({
    queryKey: ['bot-usage', activeStoreId],
    queryFn: fetchBotUsage,
    refetchInterval: 60_000,
  });

  const [draft, setDraft] = useState<BotProfile | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    if (profile) setDraft(profile);
  }, [profile]);

  if (!draft) {
    return (
      <div className="glass-card">
        <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Загрузка профиля...</p>
      </div>
    );
  }

  const set = <K extends keyof BotProfile>(key: K, value: BotProfile[K]) => {
    setDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
  };

  const updatePoint = (idx: number, field: keyof PickupPoint, value: string) => {
    setDraft((prev) => {
      if (!prev) return prev;
      const points = [...prev.pickup_points];
      points[idx] = { ...points[idx], [field]: value };
      return { ...prev, pickup_points: points };
    });
  };

  const addPoint = () => set('pickup_points', [...draft.pickup_points, { ...EMPTY_POINT }]);
  const removePoint = (idx: number) => set('pickup_points', draft.pickup_points.filter((_, i) => i !== idx));

  const handleSave = async () => {
    setSaving(true);
    try {
      const updated = await saveBotProfile(draft);
      if (updated) {
        setDraft(updated);
        queryClient.setQueryData(['bot-profile', activeStoreId], updated);
        setSavedAt(Date.now());
      }
    } finally {
      setSaving(false);
    }
  };

  const customLen = (draft.custom_instructions || '').length;

  return (
    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
        <h3 style={{ fontSize: '14px', color: '#fff', fontWeight: 600 }}>🧑‍💼 Профиль ИИ-менеджера</h3>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          {usage && (
            <span className="badge" title={`Логистические шаблоны в счётчик не идут`}>
              💬 {usage.total_billed_units} ответов ИИ за {usage.period_days} дн.
            </span>
          )}
          <span className="badge badge-success">Тариф: {TIER_LABELS[draft.prompt_tier]}</span>
        </div>
      </div>

      <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
        Эти поля подставляются в систем-промпт ИИ вместо общего шаблона — имя, название магазина,
        ассортимент, точки самовывоза и график. Заполните их для этого магазина.
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '12px' }}>
        <div>
          <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Имя ИИ-менеджера</label>
          <input className="form-input" value={draft.ai_name} onChange={(e) => set('ai_name', e.target.value)} placeholder="Айгерим" />
        </div>
        <div>
          <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Название магазина</label>
          <input className="form-input" value={draft.store_display_name} onChange={(e) => set('store_display_name', e.target.value)} placeholder="BARKHAN" />
        </div>
      </div>

      <div>
        <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Ассортимент / специализация</label>
        <input className="form-input" value={draft.specialization} onChange={(e) => set('specialization', e.target.value)} placeholder="Хозяйственные товары, спорт, праздник, дача и сад" />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '12px' }}>
        <div>
          <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Дни отгрузки</label>
          <input className="form-input" value={draft.shipping_days} onChange={(e) => set('shipping_days', e.target.value)} placeholder="будние дни (пн-пт)" />
        </div>
        <div>
          <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Отгрузка до (время)</label>
          <input className="form-input" value={draft.shipping_cutoff_time} onChange={(e) => set('shipping_cutoff_time', e.target.value)} placeholder="16:00" />
        </div>
      </div>

      <div>
        <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Политика возвратов</label>
        <textarea className="form-input" rows={2} value={draft.return_policy} onChange={(e) => set('return_policy', e.target.value)} />
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <label style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Точки самовывоза</label>
          <button className="btn btn-secondary" style={{ padding: '4px 10px', fontSize: '11px' }} onClick={addPoint}>+ Добавить точку</button>
        </div>
        {draft.pickup_points.length === 0 && (
          <p style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Точек нет — ИИ ответит клиенту, что уточнит адрес у менеджера.</p>
        )}
        {draft.pickup_points.map((p, idx) => (
          <div key={idx} style={{ background: 'rgba(255,255,255,0.03)', padding: '10px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.06)', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '8px' }}>
            <input className="form-input" placeholder="Название точки" value={p.name} onChange={(e) => updatePoint(idx, 'name', e.target.value)} />
            <input className="form-input" placeholder="Адрес" value={p.address} onChange={(e) => updatePoint(idx, 'address', e.target.value)} />
            <input className="form-input" placeholder="Ссылка на карту (2ГИС)" value={p.map_url} onChange={(e) => updatePoint(idx, 'map_url', e.target.value)} />
            <input className="form-input" placeholder="Часы выдачи" value={p.hours} onChange={(e) => updatePoint(idx, 'hours', e.target.value)} />
            <input className="form-input" placeholder="Заметка (напр. вход со двора)" value={p.notes} onChange={(e) => updatePoint(idx, 'notes', e.target.value)} />
            <button className="btn btn-secondary" style={{ padding: '6px', fontSize: '11px' }} onClick={() => removePoint(idx)}>✕ Удалить</button>
          </div>
        ))}
      </div>

      <div>
        <label style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>
          <span>Доп. инструкции для ИИ (расширяют базовые правила, не отменяют их)</span>
          <span>{customLen} симв. → тариф пересчитается при сохранении</span>
        </label>
        <textarea
          className="form-input"
          rows={4}
          value={draft.custom_instructions}
          onChange={(e) => set('custom_instructions', e.target.value)}
          placeholder="Например: если спрашивают про акцию месяца — отвечай, что сейчас акций нет."
        />
        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: 'var(--text-muted)', marginTop: '6px' }}>
          <input type="checkbox" checked={draft.custom_enabled} onChange={(e) => set('custom_enabled', e.target.checked)} />
          Применять доп. инструкции
        </label>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        <label style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Кампания «Отзыв» — окно отправки</label>
        <p style={{ fontSize: '11px', color: 'var(--text-muted)', margin: 0 }}>
          «От» — через сколько дней после доставки слать просьбу об отзыве. Без «до» шлём РОВНО в этот день.
          С «до» — любой день в диапазоне. Заказы старше окна можно подхватить галочкой ниже.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '12px' }}>
          <div>
            <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>От (дней)</label>
            <input
              className="form-input"
              type="number"
              min={0}
              value={draft.review_delay_from_days}
              onChange={(e) => set('review_delay_from_days', Number(e.target.value))}
            />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>До (дней, необязательно)</label>
            <input
              className="form-input"
              type="number"
              min={0}
              value={draft.review_delay_to_days ?? ''}
              onChange={(e) => set('review_delay_to_days', e.target.value === '' ? null : Number(e.target.value))}
              placeholder="без верхней границы"
            />
          </div>
        </div>
        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: 'var(--text-muted)' }}>
          <input
            type="checkbox"
            checked={draft.review_send_overdue}
            onChange={(e) => set('review_send_overdue', e.target.checked)}
          />
          Также слать тем, кому раньше не отправили (заказы старше «от», пропущенные ранее)
        </label>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <button className="btn btn-primary" style={{ padding: '10px 20px' }} onClick={handleSave} disabled={saving}>
          {saving ? 'Сохранение...' : '💾 Сохранить профиль'}
        </button>
        {savedAt && Date.now() - savedAt < 4000 && (
          <span style={{ fontSize: '11px', color: 'var(--success)' }}>Сохранено</span>
        )}
      </div>
    </div>
  );
}
