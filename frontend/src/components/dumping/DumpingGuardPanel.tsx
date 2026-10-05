import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AttackItem, AttackRival, GuardDecision, GuardSettings } from '../../types';
import {
  DEFAULT_GUARD, useAttackReport, useDecideRival, useGuardDecisions, useGuardSettings, usePriceMoves,
  useSaveGuardSettings, type DecisionChoice,
} from '../../hooks/useDumpingGuard';
import { GuardSettingsForm } from './GuardSettingsForm';
import { HelpHint } from '../shared/HelpHint';

/**
 * Защита демпинга от оттеснения — шапка вкладки «Демпинг»:
 * общие настройки магазина (нижний слой «магазин → группа → товар») и отчёт
 * «Под атакой» (backend/services/dumping_attacks.py).
 */

const card: React.CSSProperties = {
  background: 'rgba(255,255,255,0.02)',
  border: '1px solid rgba(255,255,255,0.06)',
  borderRadius: 12,
  padding: 16,
};

const chip = (color: string): React.CSSProperties => ({
  display: 'inline-flex',
  alignItems: 'center',
  gap: 4,
  fontSize: 11,
  padding: '2px 8px',
  borderRadius: 999,
  background: `${color}22`,
  color,
  border: `1px solid ${color}55`,
  whiteSpace: 'nowrap',
});

export const SIGNAL_COLORS: Record<string, string> = {
  ladder: '#ef4444',
  weak_rival: '#f59e0b',
  farm: '#f97316',
  shadow: '#a855f7',
};

const money = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : `${Math.round(v).toLocaleString('ru-RU')} ₸`;

/** Время с бэкенда — naive UTC без суффикса. */
const fmtTime = (iso: string | null | undefined) =>
  iso ? new Date(iso.endsWith('Z') ? iso : iso + 'Z').toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';

type GuardMode = 'off' | 'observe' | 'on';
const modeOf = (s: GuardSettings): GuardMode => (!s.enabled ? 'off' : s.observe_only ? 'observe' : 'on');
const MODE_COLORS: Record<GuardMode, string> = { off: '#9ca3af', observe: '#60a5fa', on: '#10b981' };

interface Props {
  canEdit: boolean;
  /** Показать товар в списке ниже (подставляет артикул в поиск). */
  onFocusSku?: (sku: string) => void;
}

export function DumpingGuardPanel({ canEdit, onFocusSku }: Props) {
  const { t } = useTranslation('dumping_guard');
  const guardQ = useGuardSettings();
  const reportQ = useAttackReport();
  const save = useSaveGuardSettings();

  const [open, setOpen] = useState<'none' | 'settings' | 'report'>('none');
  const [draft, setDraft] = useState<GuardSettings>(DEFAULT_GUARD);
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (guardQ.data && !dirty) setDraft(guardQ.data.settings);
  }, [guardQ.data, dirty]);

  const settings = guardQ.data?.settings ?? DEFAULT_GUARD;
  const report = reportQ.data;
  const summary = report?.summary;
  const reasonLabels = guardQ.data?.reasonLabels ?? {};

  const persist = async (next: GuardSettings, okText: string) => {
    setMsg(null);
    try {
      await save.mutateAsync(next);
      setDirty(false);
      setMsg(okText);
    } catch (e) {
      setMsg(`⚠️ ${(e as Error).message}`);
    }
  };

  const addToList = (key: 'blacklist' | 'whitelist', r: AttackRival) => {
    const entry = r.merchant_id || r.name || '';
    if (!entry) return;
    const other = key === 'blacklist' ? 'whitelist' : 'blacklist';
    const next: GuardSettings = {
      ...settings,
      [key]: Array.from(new Set([...(settings[key] || []), entry])),
      [other]: (settings[other] || []).filter((x) => x !== entry && x !== r.name),
    };
    persist(next, key === 'blacklist' ? t('msg_blacklisted', { name: r.name || entry }) : t('msg_whitelisted', { name: r.name || entry }));
  };

  return (
    <div style={{ ...card, marginBottom: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <h3 style={{ fontSize: 15, margin: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
            🛡 {t('title')}
            <HelpHint text={t('title_hint')} />
          </h3>
          <span style={chip(MODE_COLORS[modeOf(settings)])}>{t(`state_${modeOf(settings)}`)}</span>
          {summary && (
            <>
              <span style={chip(summary.under_attack ? '#ef4444' : '#9ca3af')}>
                {t('sum_under_attack', { n: summary.under_attack })}
              </span>
              {summary.ladders > 0 && <span style={chip('#ef4444')}>{t('sum_ladders', { n: summary.ladders })}</span>}
              {summary.lost_per_unit_total > 0 && (
                <span style={chip('#f59e0b')} title={t('sum_lost_hint')}>
                  {t('sum_lost', { v: money(summary.lost_per_unit_total) })}
                </span>
              )}
              {summary.guarded > 0 && <span style={chip('#10b981')}>{t('sum_guarded', { n: summary.guarded })}</span>}
              {summary.would_change > 0 && (
                <span style={chip('#60a5fa')} title={t('sum_would_hint')}>{t('sum_would', { n: summary.would_change })}</span>
              )}
              {summary.paused > 0 && <span style={chip('#f59e0b')}>{t('sum_paused', { n: summary.paused })}</span>}
              {summary.ladder_resets_30d > 0 && (
                <span style={chip('#a855f7')} title={t('sum_resets_hint')}>{t('sum_resets', { n: summary.ladder_resets_30d })}</span>
              )}
              {summary.pending_asks > 0 && (
                <button type="button" onClick={() => setOpen('report')}
                  style={{ ...chip('#ef4444'), cursor: 'pointer', fontWeight: 600 }}>
                  {t('sum_pending', { n: summary.pending_asks })}
                </button>
              )}
            </>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            type="button"
            className="btn btn-secondary"
            style={{ padding: '8px 14px', fontSize: 12, borderColor: open === 'report' ? 'var(--secondary)' : undefined }}
            onClick={() => setOpen(open === 'report' ? 'none' : 'report')}
          >
            {t('btn_report')}
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            style={{ padding: '8px 14px', fontSize: 12, borderColor: open === 'settings' ? 'var(--secondary)' : undefined }}
            onClick={() => setOpen(open === 'settings' ? 'none' : 'settings')}
          >
            {t('btn_settings')}
          </button>
        </div>
      </div>

      {msg && <div style={{ marginTop: 10, fontSize: 12, color: msg.startsWith('⚠️') ? 'var(--danger)' : 'var(--success)' }}>{msg}</div>}

      {open === 'settings' && (
        <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid rgba(255,255,255,0.06)', display: 'flex', flexDirection: 'column', gap: 14 }}>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: 0 }}>{t('settings_intro')}</p>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <strong style={{ fontSize: 13 }}>{t('mode_label')}</strong>
            {(['off', 'observe', 'on'] as GuardMode[]).map((m) => {
              const active = modeOf(draft) === m;
              return (
                <button
                  key={m}
                  type="button"
                  disabled={!canEdit}
                  className="btn btn-secondary"
                  style={{ padding: '6px 12px', fontSize: 12, borderColor: active ? MODE_COLORS[m] : undefined, color: active ? MODE_COLORS[m] : undefined }}
                  onClick={() => { setDraft({ ...draft, enabled: m !== 'off', observe_only: m === 'observe' }); setDirty(true); }}
                >
                  {t(`mode_${m}`)}
                </button>
              );
            })}
            <HelpHint text={t('mode_hint')} />
          </div>
          <GuardSettingsForm
            mode="full"
            disabled={!canEdit}
            value={draft}
            onChange={(next) => { setDraft({ ...DEFAULT_GUARD, ...next } as GuardSettings); setDirty(true); }}
          />
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            {dirty && (
              <button type="button" className="btn btn-secondary" style={{ padding: '8px 14px', fontSize: 12 }}
                onClick={() => { setDraft(settings); setDirty(false); }}>
                {t('btn_cancel')}
              </button>
            )}
            <button
              type="button"
              className="btn btn-primary"
              style={{ padding: '8px 16px', fontSize: 12 }}
              disabled={!canEdit || save.isPending || !dirty}
              onClick={() => persist(draft, t('msg_saved'))}
            >
              {save.isPending ? t('btn_saving') : t('btn_save')}
            </button>
          </div>
        </div>
      )}

      {open === 'report' && (
        <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid rgba(255,255,255,0.06)', display: 'flex', flexDirection: 'column', gap: 18 }}>
          {reportQ.isLoading && <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{t('loading')}</div>}
          {report && report.items.length === 0 && report.pending.length === 0 && (
            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{t('report_empty')}</div>
          )}

          <DecisionsBlock canEdit={canEdit} onFocusSku={onFocusSku} />

          {report && report.rivals.length > 0 && (
            <div>
              <h4 style={{ fontSize: 13, margin: '0 0 8px', display: 'flex', alignItems: 'center', gap: 6 }}>
                {t('rivals_title')}<HelpHint text={t('rivals_hint')} />
              </h4>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ color: 'var(--text-muted)', textAlign: 'left' }}>
                      <th style={{ padding: '6px 8px' }}>{t('col_seller')}</th>
                      <th style={{ padding: '6px 8px' }}>{t('col_what')}</th>
                      <th style={{ padding: '6px 8px' }}>{t('col_skus')}</th>
                      <th style={{ padding: '6px 8px' }}>{t('col_reviews')}</th>
                      <th style={{ padding: '6px 8px' }}>{t('col_age')}</th>
                      <th style={{ padding: '6px 8px' }} />
                    </tr>
                  </thead>
                  <tbody>
                    {report.rivals.map((r) => {
                      const inBlack = settings.blacklist.includes(r.merchant_id) || (!!r.name && settings.blacklist.includes(r.name));
                      const inWhite = settings.whitelist.includes(r.merchant_id) || (!!r.name && settings.whitelist.includes(r.name));
                      return (
                        <tr key={r.merchant_id} style={{ borderTop: '1px solid rgba(255,255,255,0.05)' }}>
                          <td style={{ padding: '6px 8px' }}>
                            <a href={`https://kaspi.kz/shop/info/merchant/${r.merchant_id}/address-tab/`} target="_blank" rel="noreferrer"
                              style={{ color: '#60a5fa', textDecoration: 'none' }}>
                              {r.name || r.merchant_id}
                            </a>
                          </td>
                          <td style={{ padding: '6px 8px' }}>
                            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                              {r.kinds.map((k) => <span key={k} style={chip(SIGNAL_COLORS[k] || '#9ca3af')}>{report.signal_labels[k] || k}</span>)}
                            </div>
                          </td>
                          <td style={{ padding: '6px 8px' }} title={r.skus.join(', ')}>{r.sku_count}</td>
                          <td style={{ padding: '6px 8px' }}>
                            {r.reviews ?? '—'}{r.rating ? ` · ★${r.rating}` : ''}
                          </td>
                          <td style={{ padding: '6px 8px' }}>{r.age_days != null ? t('age_days', { n: r.age_days }) : '—'}</td>
                          <td style={{ padding: '6px 8px', whiteSpace: 'nowrap', textAlign: 'right' }}>
                            {inBlack ? <span style={chip('#ef4444')}>{t('in_blacklist')}</span>
                              : inWhite ? <span style={chip('#10b981')}>{t('in_whitelist')}</span>
                              : canEdit && (
                                <>
                                  <button type="button" className="btn btn-secondary" style={{ padding: '4px 8px', fontSize: 11, marginRight: 6 }}
                                    onClick={() => addToList('blacklist', r)} disabled={save.isPending}>
                                    {t('btn_blacklist')}
                                  </button>
                                  <button type="button" className="btn btn-secondary" style={{ padding: '4px 8px', fontSize: 11 }}
                                    onClick={() => addToList('whitelist', r)} disabled={save.isPending}>
                                    {t('btn_whitelist')}
                                  </button>
                                </>
                              )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {report && report.items.length > 0 && (
            <div>
              <h4 style={{ fontSize: 13, margin: '0 0 8px', display: 'flex', alignItems: 'center', gap: 6 }}>
                {t('items_title')}<HelpHint text={t('items_hint')} />
              </h4>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {report.items.slice(0, 60).map((it) => (
                  <AttackRow key={it.sku} item={it} labels={report.signal_labels} reasonLabels={reasonLabels} onFocusSku={onFocusSku}
                    holdsAfterSkirmish={settings.ladder_response === 'FREEZE' || settings.ladder_response === 'RETREAT'} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Продавцы без рейтинга рядом с нами: менеджер решает, учитывать ли их
 *  на конкретном товаре (тот же вопрос приходит в Телеграм с кнопками). */
function DecisionsBlock({ canEdit, onFocusSku }: { canEdit: boolean; onFocusSku?: (sku: string) => void }) {
  const { t } = useTranslation('dumping_guard');
  const q = useGuardDecisions();
  const decide = useDecideRival();
  const [showDecided, setShowDecided] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const pending = q.data?.pending ?? [];
  const decided = q.data?.decided ?? [];
  if (!pending.length && !decided.length) return null;

  const act = async (id: number, decision: DecisionChoice) => {
    setErr(null);
    try { await decide.mutateAsync({ id, decision }); } catch (e) { setErr((e as Error).message); }
  };
  const btn: React.CSSProperties = { padding: '4px 8px', fontSize: 11 };

  const row = (d: GuardDecision, actions: React.ReactNode) => (
    <div key={d.id} style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', padding: '8px 10px',
      background: 'rgba(0,0,0,0.18)', border: '1px solid rgba(255,255,255,0.05)', borderRadius: 10, fontSize: 12 }}>
      <div style={{ flex: '1 1 260px', minWidth: 0 }}>
        <div style={{ color: '#fff', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          <a href={`https://kaspi.kz/shop/info/merchant/${d.merchant_id}/address-tab/`} target="_blank" rel="noreferrer"
            style={{ color: '#60a5fa', textDecoration: 'none' }}>{d.merchant_name || d.merchant_id}</a>
          {' '}<span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>{money(d.price)} · {t('our_price_short', { v: money(d.our_price) })}</span>
        </div>
        <div style={{ color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {onFocusSku
            ? <button type="button" onClick={() => onFocusSku(d.sku)} style={{ background: 'none', border: 'none', padding: 0, color: 'inherit', cursor: 'pointer', textDecoration: 'underline dotted' }}>
                {d.product_name || d.sku}
              </button>
            : (d.product_name || d.sku)}
          {' · '}{d.sku}
          {d.url && <>{' · '}<a href={d.url} target="_blank" rel="noreferrer" style={{ color: '#60a5fa', textDecoration: 'none' }}>Kaspi ↗</a></>}
        </div>
        {d.status === 'ASK' && d.price_without != null && d.our_price != null && (
          <div style={{ color: '#fbbf24' }}>
            {t('ask_impact', { v: money(d.price_without), d: (d.price_without >= d.our_price ? '+' : '−') + money(Math.abs(d.price_without - d.our_price)) })}
          </div>
        )}
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>{actions}</div>
    </div>
  );

  return (
    <div>
      <h4 style={{ fontSize: 13, margin: '0 0 8px', display: 'flex', alignItems: 'center', gap: 6 }}>
        {t('pending_title', { n: pending.length })}<HelpHint text={t('pending_hint')} />
      </h4>
      {err && <div style={{ fontSize: 12, color: 'var(--danger)', marginBottom: 6 }}>⚠️ {err}</div>}
      {pending.length === 0 && <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{t('pending_empty')}</div>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {pending.map((d) => row(d, canEdit && (
          <>
            <button type="button" className="btn btn-secondary" style={btn} disabled={decide.isPending} onClick={() => act(d.id, 'IGNORE')}>
              {t('btn_ignore_here')}
            </button>
            <button type="button" className="btn btn-secondary" style={btn} disabled={decide.isPending} onClick={() => act(d.id, 'CONSIDER')}>
              {t('btn_consider')}
            </button>
            <button type="button" className="btn btn-secondary" style={btn} disabled={decide.isPending} onClick={() => act(d.id, 'IGNORE_ALL')}
              title={t('btn_ignore_all_hint')}>
              {t('btn_ignore_all')}
            </button>
          </>
        )))}
      </div>
      {decided.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <button type="button" onClick={() => setShowDecided(!showDecided)}
            style={{ background: 'none', border: 'none', color: 'var(--text-muted)', fontSize: 12, cursor: 'pointer', padding: 0 }}>
            {showDecided ? '▲' : '▼'} {t('decided_title', { n: decided.length })}
          </button>
          {showDecided && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 6 }}>
              {decided.map((d) => row(d, (
                <>
                  <span style={chip(d.status === 'IGNORE' ? '#ef4444' : '#10b981')}>
                    {t(`decision_${d.status}`)}{d.decided_at ? ` · ${fmtTime(d.decided_at)}` : ''}
                  </span>
                  {canEdit && (
                    <button type="button" className="btn btn-secondary" style={btn} disabled={decide.isPending} onClick={() => act(d.id, 'ASK')}>
                      {t('btn_undo')}
                    </button>
                  )}
                </>
              )))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AttackRow({ item, labels, reasonLabels, onFocusSku, holdsAfterSkirmish }: {
  item: AttackItem;
  /** «Не снижать» / «Не отвечать» действуют до active_until; при охлаждении и «отметить» срок ничего не значит. */
  holdsAfterSkirmish: boolean;
  labels: Record<string, string>;
  reasonLabels: Record<string, string>;
  onFocusSku?: (sku: string) => void;
}) {
  const { t } = useTranslation('dumping_guard');
  const [open, setOpen] = useState(false);
  return (
    <div style={{ background: 'rgba(0,0,0,0.18)', border: '1px solid rgba(255,255,255,0.05)', borderRadius: 10, padding: '10px 12px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', cursor: 'pointer' }} onClick={() => setOpen(!open)}>
        <span style={{ fontSize: 12, color: '#fff', fontWeight: 600, flex: '1 1 240px', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {item.name || item.sku} <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>· {item.sku}</span>
        </span>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {item.signals.map((s) => <span key={s} style={chip(SIGNAL_COLORS[s] || '#9ca3af')}>{labels[s] || s}</span>)}
          {item.guard_limit && <span style={chip('#10b981')}>{t(`limit_${item.guard_limit}`)}</span>}
          {item.would && (
            <span style={chip('#60a5fa')} title={t(`limit_${item.would.action}`, { defaultValue: item.would.action })}>
              {t('would_price', { v: money(item.would.price) })}
            </span>
          )}
          {item.pause_until && <span style={chip('#f59e0b')}>{t('paused_until', { at: fmtTime(item.pause_until) })}</span>}
          {item.ladder_resets_30d > 0 && <span style={chip('#a855f7')}>{t('resets_n', { n: item.ladder_resets_30d })}</span>}
          {item.asks > 0 && <span style={chip('#ef4444')}>{t('asks_n', { n: item.asks })}</span>}
          {item.ignored.length > 0 && <span style={chip('#10b981')}>{t('ignored_n', { n: item.ignored.length })}</span>}
        </div>
        <span style={{ fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
          {item.peak_price_30d && item.lost_per_unit > 0
            ? <>{money(item.peak_price_30d)} → <strong style={{ color: '#fff' }}>{money(item.our_price)}</strong>{' '}
              <span style={{ color: '#f59e0b' }}>−{item.lost_pct}%</span></>
            : <strong style={{ color: '#fff' }}>{money(item.our_price)}</strong>}
        </span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{open ? '▲' : '▼'}</span>
      </div>

      {open && (
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 10, fontSize: 12 }}>
          {item.ladder && (
            <div>
              <strong style={{ color: '#ef4444' }}>{t('ladder_line', {
                name: item.ladder.rival_name || t('unknown_rival'),
                n: item.ladder.moves,
                from: money(item.ladder.price_from),
                to: money(item.ladder.price_to),
              })}</strong>
              {holdsAfterSkirmish && item.ladder_active && item.ladder.active_until && (
                <span style={{ color: 'var(--text-muted)' }}> · {t('ceasefire_until', { at: new Date(item.ladder.active_until + 'Z').toLocaleString('ru-RU') })}</span>
              )}
            </div>
          )}
          {item.suspects.length > 0 && (
            <div>
              <div style={{ color: 'var(--text-muted)', marginBottom: 4 }}>{t('suspects_title')}</div>
              {item.suspects.map((s) => (
                <div key={s.merchant_id} style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ color: '#fff' }}>{s.name}</span>
                  <span>{money(s.price)}</span>
                  <span style={{ color: 'var(--text-muted)' }}>
                    {s.reviews ? t('reviews_n', { n: s.reviews }) : t('no_reviews')}
                    {s.age_days != null ? ` · ${t('age_days', { n: s.age_days })}` : ''}
                    {s.intercity ? ` · ${t('intercity')}` : ''}
                  </span>
                </div>
              ))}
            </div>
          )}
          {item.ignored.length > 0 && (
            <div>
              <div style={{ color: 'var(--text-muted)', marginBottom: 4 }}>{t('ignored_title')}</div>
              {item.ignored.map((s) => (
                <div key={s.merchant_id} style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ color: '#fff' }}>{s.name}</span>
                  <span>{money(s.price)}</span>
                  <span style={{ color: 'var(--text-muted)' }}>{s.reasons.map((r) => reasonLabels[r] || r).join(', ')}</span>
                </div>
              ))}
            </div>
          )}
          <MovesChart sku={item.sku} />
          <div style={{ display: 'flex', gap: 12 }}>
            {onFocusSku && (
              <button type="button" className="btn btn-secondary" style={{ padding: '4px 10px', fontSize: 11 }} onClick={() => onFocusSku(item.sku)}>
                {t('btn_focus')}
              </button>
            )}
            {item.url && <a href={item.url} target="_blank" rel="noreferrer" style={{ color: '#60a5fa', fontSize: 12 }}>{t('open_card')}</a>}
          </div>
        </div>
      )}
    </div>
  );
}

/** Ступеньки нашей цены за 14 дней — стычка видна глазом. */
function MovesChart({ sku }: { sku: string }) {
  const { t } = useTranslation('dumping_guard');
  const { data: moves = [], isLoading } = usePriceMoves(sku, 14);
  if (isLoading) return <div style={{ color: 'var(--text-muted)' }}>{t('loading')}</div>;
  if (moves.length < 2) return <div style={{ color: 'var(--text-muted)' }}>{t('moves_empty')}</div>;

  const W = 560, H = 90, P = 6;
  const pts = moves.map((m) => ({ x: new Date(m.created_at + 'Z').getTime(), y: m.our_new, y0: m.our_old }));
  const xs = pts.map((p) => p.x);
  const ys = pts.flatMap((p) => [p.y, p.y0]);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const sx = (x: number) => P + ((x - minX) / Math.max(1, maxX - minX)) * (W - 2 * P);
  const sy = (y: number) => H - P - ((y - minY) / Math.max(1, maxY - minY)) * (H - 2 * P);
  let d = `M ${sx(pts[0].x)} ${sy(pts[0].y0)}`;
  for (const p of pts) d += ` H ${sx(p.x)} V ${sy(p.y)}`;
  const downs = moves.filter((m) => m.our_new < m.our_old).length;

  return (
    <div>
      <div style={{ color: 'var(--text-muted)', marginBottom: 4 }}>
        {t('moves_title', { n: moves.length, downs, from: money(moves[0].our_old), to: money(moves[moves.length - 1].our_new) })}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', maxWidth: W, height: H }} role="img" aria-label={t('moves_aria')}>
        <path d={d} fill="none" stroke="#f59e0b" strokeWidth={1.5} />
      </svg>
    </div>
  );
}
