import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { apiFetch, API_URL } from '../../api';
import { useActiveStoreId } from '../../storeContext';
import type { AutoAssemblyLogEntry, AutoAssemblyRule, AutoAssemblySpoolItem } from '../../types';

const DELIVERY_TYPE_META: Record<string, { label: string; icon: string }> = {
  Express: { label: 'Express', icon: '⚡' },
  Postomat: { label: 'Постамат (Kaspi Box)', icon: '📮' },
  'Самовывоз': { label: 'Самовывоз', icon: '🏬' },
  Today: { label: 'Kaspi Доставка — сегодня', icon: '🕐' },
  Tomorrow: { label: 'Kaspi Доставка — завтра', icon: '📅' },
  'Kaspi Доставка': { label: 'Kaspi Доставка — стандарт', icon: '🚚' },
};

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']; // TODO: use t() from component

function decisionColor(decision: string): string {
  if (decision === 'DONE') return '#34d399';
  if (decision === 'FAILED') return '#f87171';
  return '#fbbf24';
}

async function fetchRules(): Promise<AutoAssemblyRule[]> {
  const res = await apiFetch(`${API_URL}/api/auto-assembly/rules`);
  const data = await res.json();
  return data.success ? data.rules : [];
}

async function fetchLog(): Promise<AutoAssemblyLogEntry[]> {
  const res = await apiFetch(`${API_URL}/api/auto-assembly/log?limit=50`);
  const data = await res.json();
  return data.success ? data.entries : [];
}

async function fetchSpool(): Promise<{ count: number; items: AutoAssemblySpoolItem[] }> {
  const res = await apiFetch(`${API_URL}/api/auto-assembly/spool`);
  const data = await res.json();
  return data.success ? { count: data.count, items: data.items } : { count: 0, items: [] };
}

interface RuleCardProps {
  rule: AutoAssemblyRule;
  onSave: (rule: AutoAssemblyRule) => void;
  saving: boolean;
}

function RuleCard({ rule, onSave, saving }: RuleCardProps) {
  const { t } = useTranslation('tab_auto_assembly');
  const [draft, setDraft] = useState<AutoAssemblyRule>(rule);
  const [blacklistText, setBlacklistText] = useState((rule.sku_blacklist || []).join(', '));
  const [scheduleOn, setScheduleOn] = useState(!!rule.schedule);

  useEffect(() => {
    setDraft(rule);
    setBlacklistText((rule.sku_blacklist || []).join(', '));
    setScheduleOn(!!rule.schedule);
  }, [rule]);

  const meta = DELIVERY_TYPE_META[rule.delivery_type] || { label: rule.delivery_type, icon: '📦' };
  const days = draft.schedule?.days || [];

  const toggleDay = (d: number) => {
    const next = days.includes(d) ? days.filter((x) => x !== d) : [...days, d].sort();
    setDraft({ ...draft, schedule: { ...draft.schedule, days: next } });
  };

  const handleSave = () => {
    const blacklist = blacklistText.split(',').map((s) => s.trim()).filter(Boolean);
    onSave({
      ...draft,
      sku_blacklist: blacklist,
      schedule: scheduleOn ? (draft.schedule || {}) : null,
    });
  };

  return (
    <div className="glass-card" style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '18px' }}>{meta.icon}</span>
          <strong style={{ fontSize: '14px', color: '#fff' }}>{meta.label}</strong>
        </div>
        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer', fontSize: '12px' }}>
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
            style={{ width: '16px', height: '16px', cursor: 'pointer' }}
          />
          <strong style={{ color: draft.enabled ? '#34d399' : '#888' }}>{draft.enabled ? t('enabled') : t('disabled')}</strong>
        </label>
      </div>

      {draft.enabled && (
        <>
          <label
            style={{
              display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', cursor: 'pointer',
              background: draft.dry_run ? 'rgba(251, 191, 36, 0.1)' : 'transparent',
              padding: '6px 8px', borderRadius: '6px',
              border: draft.dry_run ? '1px solid rgba(251, 191, 36, 0.3)' : '1px solid transparent',
            }}
          >
            <input
              type="checkbox"
              checked={draft.dry_run}
              onChange={(e) => setDraft({ ...draft, dry_run: e.target.checked })}
              style={{ width: '16px', height: '16px', cursor: 'pointer' }}
            />
            🧪 Сухой прогон (только лог, без реальных действий)
          </label>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Стадии конвейера</div>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', cursor: 'pointer' }}>
              <input type="checkbox" checked={draft.stage_accept} onChange={(e) => setDraft({ ...draft, stage_accept: e.target.checked })} style={{ width: '16px', height: '16px' }} />
              1. Автопринятие заказов
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', cursor: 'pointer' }}>
              <input type="checkbox" checked={draft.stage_assemble} onChange={(e) => setDraft({ ...draft, stage_assemble: e.target.checked })} style={{ width: '16px', height: '16px' }} />
              2. Автоупаковка
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', cursor: 'pointer' }}>
              <input type="checkbox" checked={draft.stage_waybill} onChange={(e) => setDraft({ ...draft, stage_waybill: e.target.checked })} style={{ width: '16px', height: '16px' }} />
              3. Скачивать накладную в пачку на печать
            </label>
          </div>

          <div style={{ borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '10px' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', cursor: 'pointer', marginBottom: scheduleOn ? '8px' : 0 }}>
              <input
                type="checkbox"
                checked={scheduleOn}
                onChange={(e) => {
                  setScheduleOn(e.target.checked);
                  if (e.target.checked && !draft.schedule?.start) {
                    setDraft({ ...draft, schedule: { ...draft.schedule, start: '09:00', end: '19:00' } });
                  }
                }}
                style={{ width: '16px', height: '16px' }}
              />
              Ограничить по времени (иначе круглосуточно)
            </label>
            {scheduleOn && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                  {DAY_LABELS.map((label, idx) => (
                    <button
                      key={idx}
                      type="button"
                      onClick={() => toggleDay(idx)}
                      style={{
                        padding: '4px 8px', fontSize: '11px', borderRadius: '6px', cursor: 'pointer',
                        border: '1px solid rgba(255,255,255,0.1)',
                        background: days.includes(idx) ? 'var(--primary)' : 'transparent',
                        color: days.includes(idx) ? '#fff' : '#888',
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <input
                    type="time"
                    className="form-input"
                    value={draft.schedule?.start || '09:00'}
                    onChange={(e) => setDraft({ ...draft, schedule: { ...draft.schedule, start: e.target.value } })}
                    style={{ background: 'var(--input-bg)', color: '#fff', border: '1px solid var(--input-border)', height: '32px' }}
                  />
                  <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>—</span>
                  <input
                    type="time"
                    className="form-input"
                    value={draft.schedule?.end || '19:00'}
                    onChange={(e) => setDraft({ ...draft, schedule: { ...draft.schedule, end: e.target.value } })}
                    style={{ background: 'var(--input-bg)', color: '#fff', border: '1px solid var(--input-border)', height: '32px' }}
                  />
                </div>
              </div>
            )}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
            <div>
              <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Макс. сумма заказа (₸)</label>
              <input
                type="number"
                className="form-input"
                value={draft.max_order_price ?? ''}
                placeholder="Без лимита"
                onChange={(e) => setDraft({ ...draft, max_order_price: e.target.value ? parseFloat(e.target.value) : null })}
              />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Лимит заказов за цикл</label>
              <input
                type="number"
                className="form-input"
                value={draft.max_per_cycle ?? ''}
                placeholder="Без лимита"
                onChange={(e) => setDraft({ ...draft, max_per_cycle: e.target.value ? parseInt(e.target.value, 10) : null })}
              />
            </div>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Чёрный список SKU (через запятую)</label>
            <input
              type="text"
              className="form-input"
              value={blacklistText}
              placeholder="SKU-1, SKU-2"
              onChange={(e) => setBlacklistText(e.target.value)}
            />
          </div>

          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={draft.respect_stock_groups}
              onChange={(e) => setDraft({ ...draft, respect_stock_groups: e.target.checked })}
              style={{ width: '16px', height: '16px' }}
            />
            Не трогать заказ, если остаток по Мастер-Группе равен 0
          </label>
        </>
      )}

      <button
        type="button"
        className="btn btn-primary"
        style={{ padding: '8px 16px', fontSize: '12px', alignSelf: 'flex-end' }}
        onClick={handleSave}
        disabled={saving}
      >
        {saving ? '⏳ Сохранение...' : '💾 Сохранить'}
      </button>
    </div>
  );
}

export function AutoAssemblyTab() {
  const queryClient = useQueryClient();
  const activeStoreId = useActiveStoreId();
  const [savingType, setSavingType] = useState<string | null>(null);
  const [runningOnce, setRunningOnce] = useState(false);
  const [runMessage, setRunMessage] = useState('');
  const [downloadedManifest, setDownloadedManifest] = useState<any | null>(null);
  const [downloadingSpool, setDownloadingSpool] = useState(false);
  const [markingPrinted, setMarkingPrinted] = useState(false);

  const { data: rules = [], isLoading: rulesLoading } = useQuery({
    queryKey: ['auto-assembly', activeStoreId, 'rules'],
    queryFn: fetchRules,
  });

  const { data: logEntries = [] } = useQuery({
    queryKey: ['auto-assembly', activeStoreId, 'log'],
    queryFn: fetchLog,
    refetchInterval: 15_000,
  });

  const { data: spool = { count: 0, items: [] } } = useQuery({
    queryKey: ['auto-assembly', activeStoreId, 'spool'],
    queryFn: fetchSpool,
    refetchInterval: 15_000,
  });

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ['auto-assembly', activeStoreId] });
  };

  const handleSaveRule = async (rule: AutoAssemblyRule) => {
    setSavingType(rule.delivery_type);
    try {
      await apiFetch(`${API_URL}/api/auto-assembly/rules`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(rule),
      });
      invalidateAll();
    } finally {
      setSavingType(null);
    }
  };

  const handleRunOnce = async () => {
    setRunningOnce(true);
    setRunMessage('');
    try {
      const res = await apiFetch(`${API_URL}/api/auto-assembly/run-once`, { method: 'POST' });
      const data = await res.json();
      setRunMessage(res.ok ? '✅ Цикл выполнен.' : `❌ ${data.detail || 'Ошибка'}`);
      invalidateAll();
    } catch {
      setRunMessage('❌ Ошибка сети.');
    } finally {
      setRunningOnce(false);
    }
  };

  const handleDownloadSpool = async () => {
    setDownloadingSpool(true);
    setDownloadedManifest(null);
    try {
      const res = await apiFetch(`${API_URL}/api/auto-assembly/spool/download`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        setDownloadedManifest(data);
      } else {
        setRunMessage(`❌ ${data.detail || 'Ошибка склейки'}`);
      }
    } catch {
      setRunMessage('❌ Ошибка сети при склейке.');
    } finally {
      setDownloadingSpool(false);
    }
  };

  const handleOpenBatch = async (downloadUrl: string) => {
    const res = await apiFetch(`${API_URL}${downloadUrl}`);
    if (!res.ok) return;
    const blob = await res.blob();
    const blobUrl = URL.createObjectURL(blob);
    window.open(blobUrl, '_blank');
  };

  const handleMarkPrinted = async () => {
    setMarkingPrinted(true);
    try {
      await apiFetch(`${API_URL}/api/auto-assembly/spool/mark-printed`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      setDownloadedManifest(null);
      invalidateAll();
    } finally {
      setMarkingPrinted(false);
    }
  };

  const batches = useMemo(() => Object.values(downloadedManifest?.batches || {}) as any[], [downloadedManifest]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h2 style={{ fontSize: '16px', margin: 0 }}>🤖 Автосборка</h2>
          <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: '4px 0 0' }}>
            Автопринятие, автоупаковка и накопление накладных — раздельно по видам доставки.
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {runMessage && <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{runMessage}</span>}
          <button type="button" className="btn btn-secondary" style={{ padding: '8px 14px', fontSize: '12px' }} onClick={handleRunOnce} disabled={runningOnce}>
            {runningOnce ? '⏳ Выполняется...' : '▶️ Проверить сейчас'}
          </button>
        </div>
      </div>

      {rulesLoading ? (
        <div style={{ textAlign: 'center', padding: '40px 0' }}>Загрузка правил...</div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '16px' }}>
          {rules.map((rule) => (
            <RuleCard key={rule.delivery_type} rule={rule} onSave={handleSaveRule} saving={savingType === rule.delivery_type} />
          ))}
        </div>
      )}

      <div className="glass-card" style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
          <h3 style={{ fontSize: '14px', margin: 0 }}>🖨️ Живая пачка накладных</h3>
          <span
            className="badge"
            style={{
              background: spool.count > 0 ? 'rgba(16, 185, 129, 0.15)' : 'rgba(255,255,255,0.05)',
              color: spool.count > 0 ? '#34d399' : '#888',
              border: '1px solid rgba(255,255,255,0.08)',
            }}
          >
            {spool.count > 0 ? `Готово: ${spool.count}` : 'Пока пусто'}
          </span>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              type="button"
              className="btn btn-primary"
              style={{ padding: '8px 14px', fontSize: '12px' }}
              onClick={handleDownloadSpool}
              disabled={spool.count === 0 || downloadingSpool}
            >
              {downloadingSpool ? '⏳ Склейка...' : '📥 Скачать пачку'}
            </button>
            {downloadedManifest && (
              <button
                type="button"
                className="btn btn-secondary"
                style={{ padding: '8px 14px', fontSize: '12px' }}
                onClick={handleMarkPrinted}
                disabled={markingPrinted}
              >
                {markingPrinted ? '⏳...' : '✅ Отметить напечатанным'}
              </button>
            )}
          </div>
        </div>

        {batches.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {batches.map((b) => (
              <button
                key={b.category}
                type="button"
                onClick={() => handleOpenBatch(b.download_url)}
                style={{
                  fontSize: '12px', color: '#60a5fa', textDecoration: 'none', display: 'flex', justifyContent: 'space-between',
                  padding: '6px 10px', background: 'rgba(255,255,255,0.03)', borderRadius: '6px', border: 'none', cursor: 'pointer',
                }}
              >
                <span>{b.icon} {b.title}</span>
                <span>{b.total_pages} стр. — скачать</span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="glass-card" style={{ padding: '16px' }}>
        <h3 style={{ fontSize: '14px', margin: '0 0 12px' }}>📜 Журнал решений</h3>
        {logEntries.length === 0 ? (
          <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Пока нет записей.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', maxHeight: '320px', overflowY: 'auto' }}>
            {logEntries.map((entry, idx) => (
              <div key={idx} style={{ display: 'flex', justifyContent: 'space-between', gap: '8px', fontSize: '11px', padding: '6px 8px', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                <span style={{ color: '#888', minWidth: '70px' }}>{entry.stage}</span>
                <span style={{ color: '#ccc', flex: 1 }}>#{entry.order_code} — {entry.reason}</span>
                <span style={{ color: decisionColor(entry.decision), fontWeight: 600 }}>{entry.decision}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
