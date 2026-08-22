import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { apiFetch, API_URL } from '../../api';
import { HelpHint } from '../shared/HelpHint';
import type { AutolistDraft } from '../../types';

const STATUS_COLOR: Record<string, string> = {
  draft: '#888',
  needs_review: '#fbbf24',
  approved: '#34d399',
  submitting: '#818cf8',
  submitted: '#34d399',
  error: '#f87171',
};

async function fetchDrafts(): Promise<AutolistDraft[]> {
  const res = await apiFetch(`${API_URL}/api/autolist/drafts`);
  if (!res.ok) return [];
  return res.json();
}

// Плоский словарь attributeCode -> схема поля (допустимые enum-значения,
// min/max, единица измерения) — draft_payload.classifications хранит это в
// исходной форме Kaspi (см. backend/autolist/category_mapper.py), фронтенду
// нужно развернуть в плоскую карту для рендера контролов.
function flattenClassifications(classifications: AutolistDraft['draft_payload']['classifications']) {
  const map: Record<string, { name: string; minValue?: number | null; maxValue?: number | null; unit?: string; defaultValues: Array<{ code: string; name: string }> }> = {};
  for (const block of classifications || []) {
    for (const feat of (block as any).features || []) {
      map[feat.attributeCode] = {
        name: feat.name,
        minValue: feat.minValue,
        maxValue: feat.maxValue,
        unit: feat.unit?.name,
        defaultValues: feat.defaultValues || [],
      };
    }
  }
  return map;
}

function DraftPhoto({ draftId, style }: { draftId: string; style?: React.CSSProperties }) {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    apiFetch(`${API_URL}/api/autolist/drafts/${draftId}/photo`)
      .then((res) => (res.ok ? res.blob() : null))
      .then((blob) => {
        if (cancelled || !blob) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [draftId]);

  if (!src) {
    return <div style={{ background: 'rgba(255,255,255,0.05)', borderRadius: '8px', ...style }} />;
  }
  return <img src={src} alt="" style={{ objectFit: 'cover', borderRadius: '8px', ...style }} />;
}

interface FeatureFieldProps {
  attributeCode: string;
  meta: { name: string; mandatory: boolean; multiValued: boolean; type: string };
  schema: { minValue?: number | null; maxValue?: number | null; unit?: string; defaultValues: Array<{ code: string; name: string }> };
  value: any;
  onChange: (value: any) => void;
  t: (key: string) => string;
}

function FeatureField({ meta, schema, value, onChange, t }: FeatureFieldProps) {
  const label = `${meta.name}${meta.mandatory ? ' *' : ''}${schema.unit ? ` (${schema.unit})` : ''}`;

  if (meta.type === 'enum' && meta.multiValued) {
    const selected: string[] = Array.isArray(value) ? value : [];
    return (
      <div style={{ marginBottom: '14px' }}>
        <label style={{ display: 'block', fontSize: '12px', color: meta.mandatory ? '#fbbf24' : 'var(--text-muted)', marginBottom: '6px' }}>{label}</label>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
          {schema.defaultValues.map((opt) => {
            const active = selected.includes(opt.code);
            return (
              <button
                type="button"
                key={opt.code}
                onClick={() => onChange(active ? selected.filter((c) => c !== opt.code) : [...selected, opt.code])}
                className="btn"
                style={{
                  padding: '4px 10px',
                  fontSize: '11px',
                  borderRadius: '14px',
                  background: active ? 'var(--primary)' : 'rgba(255,255,255,0.06)',
                }}
              >
                {opt.name}
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  if (meta.type === 'enum') {
    return (
      <div style={{ marginBottom: '14px' }}>
        <label style={{ display: 'block', fontSize: '12px', color: meta.mandatory ? '#fbbf24' : 'var(--text-muted)', marginBottom: '6px' }}>{label}</label>
        <select className="form-input" value={value ?? ''} onChange={(e) => onChange(e.target.value || null)}>
          <option value="">—</option>
          {schema.defaultValues.map((opt) => (
            <option key={opt.code} value={opt.code}>{opt.name}</option>
          ))}
        </select>
      </div>
    );
  }

  if (meta.type === 'boolean') {
    return (
      <div style={{ marginBottom: '14px' }}>
        <label style={{ display: 'block', fontSize: '12px', color: meta.mandatory ? '#fbbf24' : 'var(--text-muted)', marginBottom: '6px' }}>{label}</label>
        <select className="form-input" value={value === true ? 'yes' : value === false ? 'no' : ''} onChange={(e) => onChange(e.target.value === 'yes' ? true : e.target.value === 'no' ? false : null)}>
          <option value="">—</option>
          <option value="yes">{t('yes')}</option>
          <option value="no">{t('no')}</option>
        </select>
      </div>
    );
  }

  if (meta.type === 'number') {
    return (
      <div style={{ marginBottom: '14px' }}>
        <label style={{ display: 'block', fontSize: '12px', color: meta.mandatory ? '#fbbf24' : 'var(--text-muted)', marginBottom: '6px' }}>{label}</label>
        <input
          type="number"
          className="form-input"
          value={value ?? ''}
          min={schema.minValue ?? undefined}
          max={schema.maxValue ?? undefined}
          onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
        />
      </div>
    );
  }

  return (
    <div style={{ marginBottom: '14px' }}>
      <label style={{ display: 'block', fontSize: '12px', color: meta.mandatory ? '#fbbf24' : 'var(--text-muted)', marginBottom: '6px' }}>{label}</label>
      <input type="text" className="form-input" value={value ?? ''} onChange={(e) => onChange(e.target.value || null)} />
    </div>
  );
}

function getStatusLabel(status: string, t: (key: string) => string): string {
  const labels: Record<string, string> = {
    draft: t('status_draft'),
    needs_review: t('status_needs_review'),
    approved: t('status_approved'),
    submitting: t('status_submitting'),
    submitted: t('status_submitted'),
    error: t('status_error'),
  };
  return labels[status] || status;
}

function getMissingLabel(field: string, t: (key: string) => string): string {
  const labels: Record<string, string> = {
    __sku__: t('missing_sku'),
    __category__: t('missing_category'),
    __price__: t('missing_price'),
    __stock__: t('missing_stock'),
  };
  return labels[field] || field;
}

function DraftEditor({ draft, onClose, t }: { draft: AutolistDraft; onClose: () => void; t: (key: string) => string }) {
  const queryClient = useQueryClient();
  const payload = draft.draft_payload;
  const featureSchema = useMemo(() => flattenClassifications(payload.classifications), [payload.classifications]);

  const [sku, setSku] = useState(payload.sku ?? '');
  const [price, setPrice] = useState<number | ''>(payload.price ?? '');
  const [stock, setStock] = useState<number | ''>(payload.stock ?? '');
  const [description, setDescription] = useState(payload.description ?? '');
  const [features, setFeatures] = useState<Record<string, any>>(payload.features ?? {});
  const [actionError, setActionError] = useState('');

  // Черновик мог обновиться извне (например после save) — синхронизируем
  // локальную форму только когда меняется САМ id черновика, не на каждый
  // render, иначе несохранённые правки будут затираться при рефетче.
  useEffect(() => {
    setSku(payload.sku ?? '');
    setPrice(payload.price ?? '');
    setStock(payload.stock ?? '');
    setDescription(payload.description ?? '');
    setFeatures(payload.features ?? {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.id]);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['autolist-drafts'] });

  const saveMutation = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`${API_URL}/api/autolist/drafts/${draft.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ patch: { sku: sku || null, price: price === '' ? null : price, stock: stock === '' ? null : stock, description, features } }),
      });
      if (!res.ok) throw new Error(t('save_error'));
      return res.json();
    },
    onSuccess: invalidate,
    onError: () => setActionError(t('save_changes_error')),
  });

  const approveMutation = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`${API_URL}/api/autolist/drafts/${draft.id}/approve`, { method: 'POST' });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || t('approve_error'));
      }
      return res.json();
    },
    onSuccess: invalidate,
    onError: (e: any) => setActionError(e.message),
  });

  const submitMutation = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`${API_URL}/api/autolist/drafts/${draft.id}/submit`, { method: 'POST' });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || t('submit_error'));
      }
      return res.json();
    },
    onSuccess: invalidate,
    onError: (e: any) => setActionError(e.message),
  });

  const deleteMutation = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`${API_URL}/api/autolist/drafts/${draft.id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(t('delete_error'));
    },
    onSuccess: () => {
      invalidate();
      onClose();
    },
  });

  const canApprove = draft.status === 'needs_review';
  const canSubmit = draft.status === 'approved';
  const busy = saveMutation.isPending || approveMutation.isPending || submitMutation.isPending || deleteMutation.isPending;

  return (
    <div className="modal-overlay">
      <div className="glass-card modal-content" style={{ maxWidth: '760px', width: '100%', padding: '28px', maxHeight: '88vh', overflowY: 'auto' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '18px' }}>
          <div style={{ display: 'flex', gap: '14px' }}>
            <DraftPhoto draftId={draft.id} style={{ width: '84px', height: '84px' }} />
            <div>
              <h3 style={{ fontSize: '16px', fontWeight: 700, marginBottom: '4px' }}>{payload.category_name || t('no_category')}</h3>
              <p style={{ fontSize: '12px', color: 'var(--text-muted)', maxWidth: '480px' }}>{draft.raw_title}</p>
              <span className="badge" style={{ marginTop: '6px', display: 'inline-block', color: STATUS_COLOR[draft.status] }}>
                {getStatusLabel(draft.status, t)}
              </span>
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', color: '#fff', fontSize: '20px', cursor: 'pointer' }}>✕</button>
        </div>

        {!payload.category_code && (
          <div style={{ padding: '10px 12px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239, 68, 68, 0.3)', color: '#fca5a5', borderRadius: '8px', marginBottom: '16px', fontSize: '13px' }}>
            {t('category_not_found').replace('%query%', payload.category_search_query || '')}
          </div>
        )}

        {draft.error_message && (
          <div style={{ padding: '10px 12px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239, 68, 68, 0.3)', color: '#fca5a5', borderRadius: '8px', marginBottom: '16px', fontSize: '13px' }}>
            {draft.error_message}
          </div>
        )}

        {draft.status === 'submitted' && draft.kaspi_result && (
          <div style={{ padding: '10px 12px', background: 'rgba(52, 211, 153, 0.12)', border: '1px solid rgba(52, 211, 153, 0.3)', color: '#6ee7b7', borderRadius: '8px', marginBottom: '16px', fontSize: '13px' }}>
            {t('submitted_success').replace('%sku%', draft.kaspi_result.sku)}
          </div>
        )}

        {payload.existing_candidates && payload.existing_candidates.length > 0 && (
          <details style={{ marginBottom: '16px', fontSize: '12px', color: 'var(--text-muted)' }}>
            <summary style={{ cursor: 'pointer' }}>{t('similar_products').replace('%count%', String(payload.existing_candidates.length))}</summary>
            <ul style={{ marginTop: '8px', paddingLeft: '18px' }}>
              {payload.existing_candidates.slice(0, 5).map((c) => (
                <li key={c.id}><a href={`https://kaspi.kz${c.shopLink}`} target="_blank" rel="noreferrer" style={{ color: 'var(--primary)' }}>{c.title}</a></li>
              ))}
            </ul>
          </details>
        )}

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '14px', marginBottom: '10px' }}>
          <div>
            <label style={{ display: 'block', fontSize: '12px', color: '#fbbf24', marginBottom: '6px' }}>{t('missing_sku')} *</label>
            <input className="form-input" value={sku} onChange={(e) => setSku(e.target.value)} />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '12px', color: '#fbbf24', marginBottom: '6px' }}>{t('missing_price')}, ₸ *</label>
            <input type="number" className="form-input" value={price} onChange={(e) => setPrice(e.target.value === '' ? '' : Number(e.target.value))} />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '12px', color: '#fbbf24', marginBottom: '6px' }}>{t('missing_stock')}, {t('unit_pcs')} *</label>
            <input type="number" className="form-input" value={stock} onChange={(e) => setStock(e.target.value === '' ? '' : Number(e.target.value))} />
          </div>
        </div>

        {Object.keys(payload.feature_meta).length > 0 && (
          <>
            <h4 style={{ fontSize: '13px', margin: '18px 0 10px', color: 'var(--text-muted)' }}>{t('kaspi_features')}</h4>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 16px' }}>
              {Object.entries(payload.feature_meta).map(([code, meta]) => (
                <FeatureField
                  key={code}
                  attributeCode={code}
                  meta={meta}
                  schema={featureSchema[code] || { defaultValues: [] }}
                  value={features[code]}
                  onChange={(v) => setFeatures((prev) => ({ ...prev, [code]: v }))}
                  t={t}
                />
              ))}
            </div>
          </>
        )}

        <div style={{ marginBottom: '10px' }}>
          <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>{t('description')}</label>
          <textarea className="form-input" style={{ minHeight: '70px', resize: 'vertical' }} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={7000} />
        </div>

        {draft.missing_mandatory.length > 0 && (
          <div style={{ padding: '10px 12px', background: 'rgba(251, 191, 36, 0.12)', border: '1px solid rgba(251, 191, 36, 0.3)', color: '#fcd34d', borderRadius: '8px', marginBottom: '16px', fontSize: '12px' }}>
            {t('missing_fields')}: {draft.missing_mandatory.map((c) => getMissingLabel(c, t) || payload.feature_meta[c]?.name || c).join(', ')}
          </div>
        )}

        {actionError && (
          <div style={{ padding: '10px 12px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239, 68, 68, 0.3)', color: '#fca5a5', borderRadius: '8px', marginBottom: '16px', fontSize: '13px' }}>
            {actionError}
          </div>
        )}

        <div style={{ display: 'flex', gap: '10px', justifyContent: 'space-between', marginTop: '20px' }}>
          <button
            className="btn"
            style={{ color: '#f87171', border: '1px solid rgba(248,113,113,0.4)' }}
            disabled={busy}
            onClick={() => { if (confirm(t('confirm_delete'))) deleteMutation.mutate(); }}
          >
            {t('btn_delete')}
          </button>

          <div style={{ display: 'flex', gap: '10px' }}>
            <button className="btn btn-secondary" disabled={busy || draft.status === 'submitted'} onClick={() => { setActionError(''); saveMutation.mutate(); }}>
              {saveMutation.isPending ? t('btn_saving') : t('btn_save')}
            </button>
            {canApprove && (
              <button className="btn btn-primary" disabled={busy} onClick={() => { setActionError(''); approveMutation.mutate(); }}>
                {approveMutation.isPending ? t('btn_checking') : t('btn_confirm')}
              </button>
            )}
            {canSubmit && (
              <button className="btn btn-primary" disabled={busy} onClick={() => { setActionError(''); submitMutation.mutate(); }}>
                {submitMutation.isPending ? t('btn_submitting') : t('btn_submit_kaspi')}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export function AutolistTab() {
  const { t } = useTranslation('tab_autolist');
  const queryClient = useQueryClient();
  const { data: drafts = [], isLoading } = useQuery({
    queryKey: ['autolist-drafts'],
    queryFn: fetchDrafts,
    refetchInterval: 20_000,
  });

  const [titleInput, setTitleInput] = useState('');
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [uploadError, setUploadError] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const createMutation = useMutation({
    mutationFn: async () => {
      if (!photoFile || !titleInput.trim()) throw new Error(t('upload_required'));
      const form = new FormData();
      form.append('photo', photoFile);
      form.append('title', titleInput.trim());
      const res = await apiFetch(`${API_URL}/api/autolist/drafts`, { method: 'POST', body: form });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || t('upload_error'));
      }
      return res.json() as Promise<AutolistDraft>;
    },
    onSuccess: (draft) => {
      queryClient.invalidateQueries({ queryKey: ['autolist-drafts'] });
      setTitleInput('');
      setPhotoFile(null);
      setUploadError('');
      setSelectedId(draft.id);
    },
    onError: (e: any) => setUploadError(e.message || t('upload_failed')),
  });

  const selectedDraft = drafts.find((d) => d.id === selectedId) || null;

  return (
    <>
      <div className="glass-card" style={{ padding: '20px', marginBottom: '20px' }}>
        <h3 style={{ fontSize: '14px', marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '6px' }}>
          {t('upload_new_product')}
          <HelpHint text="Загрузите фото товара и укажите название — ИИ подберёт категорию Kaspi, атрибуты и соберёт черновик карточки. Черновик нужно проверить и отредактировать вручную перед публикацией — это ускоряет заполнение, но не заменяет проверку." />
        </h3>
        <div style={{ display: 'flex', gap: '12px', alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp"
            onChange={(e) => setPhotoFile(e.target.files?.[0] || null)}
            style={{ fontSize: '12px', color: 'var(--text-muted)' }}
          />
          <input
            type="text"
            className="form-input"
            placeholder={t('product_name_placeholder')}
            value={titleInput}
            onChange={(e) => setTitleInput(e.target.value)}
            style={{ flexGrow: 1, minWidth: '260px' }}
          />
          <button
            className="btn btn-primary"
            disabled={createMutation.isPending || !photoFile || !titleInput.trim()}
            onClick={() => createMutation.mutate()}
          >
            {createMutation.isPending ? t('btn_analyzing') : t('btn_recognize')}
          </button>
        </div>
        {uploadError && <p style={{ color: '#f87171', fontSize: '12px', marginTop: '10px' }}>{uploadError}</p>}
        <p style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '10px' }}>
          {t('ai_description')}
        </p>
      </div>

      {isLoading ? (
        <p style={{ color: 'var(--text-muted)' }}>{t('loading')}</p>
      ) : drafts.length === 0 ? (
        <p style={{ color: 'var(--text-muted)' }}>{t('no_drafts')}</p>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '14px' }}>
          {drafts.map((d) => (
            <div
              key={d.id}
              className="glass-card"
              style={{ padding: '14px', cursor: 'pointer' }}
              onClick={() => setSelectedId(d.id)}
            >
              <DraftPhoto draftId={d.id} style={{ width: '100%', height: '140px' }} />
              <p style={{ fontSize: '13px', fontWeight: 600, margin: '10px 0 4px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {d.draft_payload.category_name || d.raw_title}
              </p>
              <p style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '8px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {d.draft_payload.sku || t('no_sku')}
              </p>
              <span className="badge" style={{ color: STATUS_COLOR[d.status] }}>
                {getStatusLabel(d.status, t)}
              </span>
              {d.missing_mandatory.length > 0 && d.status === 'needs_review' && (
                <span style={{ fontSize: '11px', color: '#fbbf24', marginLeft: '8px' }}>· {d.missing_mandatory.length} {t('not_filled')}</span>
              )}
            </div>
          ))}
        </div>
      )}

      {selectedDraft && <DraftEditor draft={selectedDraft} onClose={() => setSelectedId(null)} t={t} />}
    </>
  );
}
