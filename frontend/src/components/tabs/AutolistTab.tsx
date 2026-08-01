import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../../api';
import type { AutolistDraft } from '../../types';

const STATUS_LABEL: Record<string, string> = {
  draft: 'Черновик',
  needs_review: 'Нужна проверка',
  approved: 'Подтверждено',
  submitting: 'Отправка…',
  submitted: 'Отправлено на модерацию',
  error: 'Ошибка',
};

const STATUS_COLOR: Record<string, string> = {
  draft: '#888',
  needs_review: '#fbbf24',
  approved: '#34d399',
  submitting: '#818cf8',
  submitted: '#34d399',
  error: '#f87171',
};

const MISSING_LABEL: Record<string, string> = {
  __sku__: 'Артикул',
  __category__: 'Категория',
  __price__: 'Цена',
  __stock__: 'Остаток',
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
}

function FeatureField({ meta, schema, value, onChange }: FeatureFieldProps) {
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
          <option value="yes">Да</option>
          <option value="no">Нет</option>
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

function DraftEditor({ draft, onClose }: { draft: AutolistDraft; onClose: () => void }) {
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
      if (!res.ok) throw new Error('Не удалось сохранить');
      return res.json();
    },
    onSuccess: invalidate,
    onError: () => setActionError('Не удалось сохранить изменения'),
  });

  const approveMutation = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`${API_URL}/api/autolist/drafts/${draft.id}/approve`, { method: 'POST' });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Не заполнены обязательные поля');
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
        throw new Error(err.detail || 'Не удалось отправить в Kaspi');
      }
      return res.json();
    },
    onSuccess: invalidate,
    onError: (e: any) => setActionError(e.message),
  });

  const deleteMutation = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`${API_URL}/api/autolist/drafts/${draft.id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Не удалось удалить');
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
              <h3 style={{ fontSize: '16px', fontWeight: 700, marginBottom: '4px' }}>{payload.category_name || 'Категория не определена'}</h3>
              <p style={{ fontSize: '12px', color: 'var(--text-muted)', maxWidth: '480px' }}>{draft.raw_title}</p>
              <span className="badge" style={{ marginTop: '6px', display: 'inline-block', color: STATUS_COLOR[draft.status] }}>
                {STATUS_LABEL[draft.status] || draft.status}
              </span>
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', color: '#fff', fontSize: '20px', cursor: 'pointer' }}>✕</button>
        </div>

        {!payload.category_code && (
          <div style={{ padding: '10px 12px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239, 68, 68, 0.3)', color: '#fca5a5', borderRadius: '8px', marginBottom: '16px', fontSize: '13px' }}>
            Kaspi не нашёл категорию по запросу «{payload.category_search_query}». Автозаполнение характеристик недоступно для этого черновика — выберите категорию вручную через кабинет Kaspi, либо удалите черновик и попробуйте с более точным названием.
          </div>
        )}

        {draft.error_message && (
          <div style={{ padding: '10px 12px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239, 68, 68, 0.3)', color: '#fca5a5', borderRadius: '8px', marginBottom: '16px', fontSize: '13px' }}>
            {draft.error_message}
          </div>
        )}

        {draft.status === 'submitted' && draft.kaspi_result && (
          <div style={{ padding: '10px 12px', background: 'rgba(52, 211, 153, 0.12)', border: '1px solid rgba(52, 211, 153, 0.3)', color: '#6ee7b7', borderRadius: '8px', marginBottom: '16px', fontSize: '13px' }}>
            ✅ Отправлено на модерацию Kaspi (артикул {draft.kaspi_result.sku}). Проверка занимает до 3 дней. Цену/остаток Kaspi не принимает на этом шаге — выставите их после одобрения модерации через обычное управление товарами.
          </div>
        )}

        {payload.existing_candidates && payload.existing_candidates.length > 0 && (
          <details style={{ marginBottom: '16px', fontSize: '12px', color: 'var(--text-muted)' }}>
            <summary style={{ cursor: 'pointer' }}>Похожие товары уже есть на Kaspi ({payload.existing_candidates.length}) — возможно не стоит плодить дубликат</summary>
            <ul style={{ marginTop: '8px', paddingLeft: '18px' }}>
              {payload.existing_candidates.slice(0, 5).map((c) => (
                <li key={c.id}><a href={`https://kaspi.kz${c.shopLink}`} target="_blank" rel="noreferrer" style={{ color: 'var(--primary)' }}>{c.title}</a></li>
              ))}
            </ul>
          </details>
        )}

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '14px', marginBottom: '10px' }}>
          <div>
            <label style={{ display: 'block', fontSize: '12px', color: '#fbbf24', marginBottom: '6px' }}>Артикул *</label>
            <input className="form-input" value={sku} onChange={(e) => setSku(e.target.value)} />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '12px', color: '#fbbf24', marginBottom: '6px' }}>Цена, ₸ *</label>
            <input type="number" className="form-input" value={price} onChange={(e) => setPrice(e.target.value === '' ? '' : Number(e.target.value))} />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '12px', color: '#fbbf24', marginBottom: '6px' }}>Остаток, шт *</label>
            <input type="number" className="form-input" value={stock} onChange={(e) => setStock(e.target.value === '' ? '' : Number(e.target.value))} />
          </div>
        </div>

        {Object.keys(payload.feature_meta).length > 0 && (
          <>
            <h4 style={{ fontSize: '13px', margin: '18px 0 10px', color: 'var(--text-muted)' }}>Характеристики Kaspi</h4>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 16px' }}>
              {Object.entries(payload.feature_meta).map(([code, meta]) => (
                <FeatureField
                  key={code}
                  attributeCode={code}
                  meta={meta}
                  schema={featureSchema[code] || { defaultValues: [] }}
                  value={features[code]}
                  onChange={(v) => setFeatures((prev) => ({ ...prev, [code]: v }))}
                />
              ))}
            </div>
          </>
        )}

        <div style={{ marginBottom: '10px' }}>
          <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>Описание</label>
          <textarea className="form-input" style={{ minHeight: '70px', resize: 'vertical' }} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={7000} />
        </div>

        {draft.missing_mandatory.length > 0 && (
          <div style={{ padding: '10px 12px', background: 'rgba(251, 191, 36, 0.12)', border: '1px solid rgba(251, 191, 36, 0.3)', color: '#fcd34d', borderRadius: '8px', marginBottom: '16px', fontSize: '12px' }}>
            Не заполнено: {draft.missing_mandatory.map((c) => MISSING_LABEL[c] || payload.feature_meta[c]?.name || c).join(', ')}
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
            onClick={() => { if (confirm('Удалить черновик безвозвратно?')) deleteMutation.mutate(); }}
          >
            🗑 Удалить
          </button>

          <div style={{ display: 'flex', gap: '10px' }}>
            <button className="btn btn-secondary" disabled={busy || draft.status === 'submitted'} onClick={() => { setActionError(''); saveMutation.mutate(); }}>
              {saveMutation.isPending ? 'Сохранение…' : '💾 Сохранить'}
            </button>
            {canApprove && (
              <button className="btn btn-primary" disabled={busy} onClick={() => { setActionError(''); approveMutation.mutate(); }}>
                {approveMutation.isPending ? 'Проверка…' : '✅ Подтвердить'}
              </button>
            )}
            {canSubmit && (
              <button className="btn btn-primary" disabled={busy} onClick={() => { setActionError(''); submitMutation.mutate(); }}>
                {submitMutation.isPending ? 'Отправка…' : '🚀 Отправить в Kaspi'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export function AutolistTab() {
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
      if (!photoFile || !titleInput.trim()) throw new Error('Загрузите фото и укажите название товара');
      const form = new FormData();
      form.append('photo', photoFile);
      form.append('title', titleInput.trim());
      const res = await apiFetch(`${API_URL}/api/autolist/drafts`, { method: 'POST', body: form });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Не удалось обработать товар');
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
    onError: (e: any) => setUploadError(e.message || 'Ошибка загрузки'),
  });

  const selectedDraft = drafts.find((d) => d.id === selectedId) || null;

  return (
    <>
      <div className="glass-card" style={{ padding: '20px', marginBottom: '20px' }}>
        <h3 style={{ fontSize: '14px', marginBottom: '12px' }}>Загрузить новый товар</h3>
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
            placeholder="Название товара от поставщика (можно неполное, ИИ дозаполнит)"
            value={titleInput}
            onChange={(e) => setTitleInput(e.target.value)}
            style={{ flexGrow: 1, minWidth: '260px' }}
          />
          <button
            className="btn btn-primary"
            disabled={createMutation.isPending || !photoFile || !titleInput.trim()}
            onClick={() => createMutation.mutate()}
          >
            {createMutation.isPending ? '🤖 Анализирую фото…' : '✨ Распознать'}
          </button>
        </div>
        {uploadError && <p style={{ color: '#f87171', fontSize: '12px', marginTop: '10px' }}>{uploadError}</p>}
        <p style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '10px' }}>
          ИИ заполняет категорию и характеристики по фото и названию. Проверьте каждое поле перед подтверждением — модель иногда ошибается, особенно на цвете и мелких деталях.
        </p>
      </div>

      {isLoading ? (
        <p style={{ color: 'var(--text-muted)' }}>Загрузка…</p>
      ) : drafts.length === 0 ? (
        <p style={{ color: 'var(--text-muted)' }}>Черновиков пока нет — загрузите первое фото выше.</p>
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
                {d.draft_payload.sku || 'без артикула'}
              </p>
              <span className="badge" style={{ color: STATUS_COLOR[d.status] }}>
                {STATUS_LABEL[d.status] || d.status}
              </span>
              {d.missing_mandatory.length > 0 && d.status === 'needs_review' && (
                <span style={{ fontSize: '11px', color: '#fbbf24', marginLeft: '8px' }}>· {d.missing_mandatory.length} не заполнено</span>
              )}
            </div>
          ))}
        </div>
      )}

      {selectedDraft && <DraftEditor draft={selectedDraft} onClose={() => setSelectedId(null)} />}
    </>
  );
}
