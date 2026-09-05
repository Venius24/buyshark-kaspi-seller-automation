import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Plus, KeyRound, Trash2, Pencil, Copy, Check } from 'lucide-react';
import { apiFetch, API_URL } from '../../api';
import type { ActiveTab } from '../../types';

// Сотрудники и роли (2026-09-05). Права читаются сервером из двух осей
// (backend/security/sections.py): role — глубина (VIEWER/MANAGER), sections —
// ширина (какие вкладки видны). Этот экран — единственное место в кабинете,
// где владелец их назначает; сам экран доступен только OWNER (см. Sidebar.tsx
// и require_store_owner на каждой ручке /api/v1/employees).

interface CatalogSection {
  key: string;
  title: string;
  owner_only: boolean;
}

interface CatalogPreset {
  code: string;
  title: string;
  role: 'MANAGER' | 'VIEWER';
  sections: string[];
  hint: string;
}

interface Catalog {
  sections: CatalogSection[];
  presets: CatalogPreset[];
  roles: { code: string; title: string }[];
}

interface Employee {
  user_id: string;
  email: string;
  full_name: string | null;
  role: string;
  job_preset: string | null;
  job_title: string | null;
  sections: string[];
  is_active: boolean;
  must_change_password: boolean;
  is_telegram_only: boolean;
  created_at: string | null;
}

interface EmployeesResponse {
  employees: Employee[];
  used: number;
  limit: number | null;
}

const fetchCatalog = async (): Promise<Catalog> => {
  const res = await apiFetch(`${API_URL}/api/v1/employees/catalog`);
  if (!res.ok) throw new Error('catalog failed');
  return res.json();
};

const fetchEmployees = async (): Promise<EmployeesResponse> => {
  const res = await apiFetch(`${API_URL}/api/v1/employees`);
  if (!res.ok) throw new Error('list failed');
  return res.json();
};

async function readError(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => ({}));
  return typeof body.detail === 'string' ? body.detail : fallback;
}

// Показ одноразового пароля — владелец видел его один раз и должен либо
// скопировать, либо продиктовать сотруднику. Закрывается только руками,
// никогда не таймаутом: пропустить его — значит потерять пароль насовсем
// (сброс — единственный путь назад).
const TempPasswordBox: React.FC<{ email: string; password: string; onClose: () => void }> = ({ email, password, onClose }) => {
  const { t } = useTranslation('tab_employees');
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(password);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div className="glass-card" style={{ width: '380px', padding: '20px' }}>
        <h2 style={{ fontSize: '16px', fontWeight: 700, marginBottom: '10px' }}>{t('password_title')}</h2>
        <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '12px' }}>
          {t('password_hint', { email })}
        </p>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '14px' }}>
          <code style={{ flex: 1, fontSize: '15px', padding: '8px 10px', background: 'rgba(255,255,255,0.06)', borderRadius: '6px', letterSpacing: '0.03em' }}>{password}</code>
          <button className="btn btn-secondary" style={{ padding: '8px 10px' }} onClick={copy} title={t('btn_copy')}>
            {copied ? <Check size={14} /> : <Copy size={14} />}
          </button>
        </div>
        <p style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '14px' }}>{t('password_warning')}</p>
        <button className="btn btn-primary" style={{ width: '100%' }} onClick={onClose}>{t('btn_done')}</button>
      </div>
    </div>
  );
};

interface FormState {
  email: string;
  full_name: string;
  job_title: string;
  preset: string; // код пресета либо '__custom__'
  role: 'MANAGER' | 'VIEWER';
  sections: Set<string>;
}

function presetToForm(preset: CatalogPreset): Pick<FormState, 'preset' | 'role' | 'sections'> {
  return { preset: preset.code, role: preset.role, sections: new Set(preset.sections) };
}

const EmployeeFormModal: React.FC<{
  catalog: Catalog;
  editing: Employee | null;
  onClose: () => void;
  onSubmit: (form: FormState) => void;
  submitting: boolean;
  error: string;
}> = ({ catalog, editing, onClose, onSubmit, submitting, error }) => {
  const { t } = useTranslation('tab_employees');
  const employeeSections = useMemo(() => catalog.sections.filter((s) => !s.owner_only), [catalog.sections]);
  const defaultPreset = catalog.presets[0];

  const [form, setForm] = useState<FormState>(() => {
    if (editing) {
      const matchingPreset = editing.job_preset ? catalog.presets.find((p) => p.code === editing.job_preset) : null;
      return {
        email: editing.email,
        full_name: editing.full_name || '',
        job_title: editing.job_title || '',
        preset: matchingPreset ? matchingPreset.code : '__custom__',
        role: (editing.role as 'MANAGER' | 'VIEWER') || 'MANAGER',
        sections: new Set(editing.sections),
      };
    }
    return {
      email: '',
      full_name: '',
      job_title: '',
      ...(defaultPreset ? presetToForm(defaultPreset) : { preset: '__custom__', role: 'MANAGER' as const, sections: new Set<string>() }),
    };
  });

  const isCustom = form.preset === '__custom__';

  const selectPreset = (code: string) => {
    if (code === '__custom__') {
      setForm((f) => ({ ...f, preset: code }));
      return;
    }
    const preset = catalog.presets.find((p) => p.code === code);
    if (!preset) return;
    setForm((f) => ({ ...f, ...presetToForm(preset) }));
  };

  const toggleSection = (key: string) => {
    setForm((f) => {
      const next = new Set(f.sections);
      if (next.has(key)) next.delete(key); else next.add(key);
      return { ...f, sections: next };
    });
  };

  const activePreset = catalog.presets.find((p) => p.code === form.preset);

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 100, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
      <div className="glass-card" style={{ width: '460px', maxHeight: '86vh', overflowY: 'auto', padding: '20px' }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
          <h2 style={{ fontSize: '16px', fontWeight: 700 }}>{editing ? t('edit_title') : t('add_title')}</h2>
          <button className="btn btn-secondary" style={{ padding: '4px 8px' }} onClick={onClose}><X size={14} /></button>
        </div>

        {!editing && (
          <div style={{ marginBottom: '12px' }}>
            <label style={labelStyle}>{t('field_email')}</label>
            <input
              type="email"
              className="input"
              style={inputStyle}
              value={form.email}
              onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
              placeholder="sborshik@example.com"
            />
          </div>
        )}

        <div style={{ marginBottom: '12px' }}>
          <label style={labelStyle}>{t('field_job_title')}</label>
          <input
            type="text"
            className="input"
            style={inputStyle}
            value={form.job_title}
            onChange={(e) => setForm((f) => ({ ...f, job_title: e.target.value }))}
            placeholder={activePreset?.title || t('field_job_title')}
          />
        </div>

        <div style={{ marginBottom: '12px' }}>
          <label style={labelStyle}>{t('field_preset')}</label>
          <select className="input" style={inputStyle} value={form.preset} onChange={(e) => selectPreset(e.target.value)}>
            {catalog.presets.map((p) => (
              <option key={p.code} value={p.code}>{p.title}</option>
            ))}
            <option value="__custom__">{t('preset_custom')}</option>
          </select>
          {!isCustom && activePreset && (
            <p style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>{activePreset.hint}</p>
          )}
        </div>

        {isCustom && (
          <div style={{ marginBottom: '12px' }}>
            <label style={labelStyle}>{t('field_role')}</label>
            <div style={{ display: 'flex', gap: '6px' }}>
              {catalog.roles.map((r) => (
                <button
                  key={r.code}
                  type="button"
                  className="btn btn-secondary"
                  style={{ padding: '6px 12px', fontSize: '12px', ...(form.role === r.code ? { background: 'var(--secondary)', color: '#000', fontWeight: 700 } : {}) }}
                  onClick={() => setForm((f) => ({ ...f, role: r.code as 'MANAGER' | 'VIEWER' }))}
                >
                  {r.title}
                </button>
              ))}
            </div>
          </div>
        )}

        <div style={{ marginBottom: '4px' }}>
          <label style={labelStyle}>{t('field_sections')}</label>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', maxHeight: '220px', overflowY: 'auto', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '6px', padding: '6px' }}>
            {employeeSections.map((s) => (
              <label key={s.key} style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '6px 4px', fontSize: '13px', cursor: isCustom ? 'pointer' : 'default', opacity: isCustom ? 1 : 0.7 }}>
                <input
                  type="checkbox"
                  disabled={!isCustom}
                  checked={form.sections.has(s.key)}
                  onChange={() => toggleSection(s.key)}
                />
                {s.title}
              </label>
            ))}
          </div>
          {!isCustom && <p style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>{t('sections_from_preset_hint')}</p>}
        </div>

        {error && <p style={{ fontSize: '12px', color: '#f87171', margin: '10px 0 0' }}>{error}</p>}

        <button
          className="btn btn-primary"
          style={{ width: '100%', marginTop: '16px' }}
          disabled={submitting || (!editing && !form.email.trim())}
          onClick={() => onSubmit(form)}
        >
          {submitting ? t('btn_saving') : editing ? t('btn_save') : t('btn_add')}
        </button>
      </div>
    </div>
  );
};

export const EmployeesTab: React.FC<{ onSelectTab?: (tab: ActiveTab) => void }> = ({ onSelectTab }) => {
  const { t } = useTranslation('tab_employees');
  const queryClient = useQueryClient();

  const { data: catalog } = useQuery({ queryKey: ['employees', 'catalog'], queryFn: fetchCatalog });
  const { data, isLoading } = useQuery({ queryKey: ['employees', 'list'], queryFn: fetchEmployees });

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Employee | null>(null);
  const [formError, setFormError] = useState('');
  const [tempPassword, setTempPassword] = useState<{ email: string; password: string } | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<Employee | null>(null);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['employees'] });

  const createMutation = useMutation({
    mutationFn: async (form: FormState) => {
      const res = await apiFetch(`${API_URL}/api/v1/employees`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: form.email.trim(),
          full_name: form.full_name.trim() || null,
          preset: form.preset === '__custom__' ? undefined : form.preset,
          sections: form.preset === '__custom__' ? Array.from(form.sections) : undefined,
          job_title: form.job_title.trim() || null,
        }),
      });
      if (!res.ok) throw new Error(await readError(res, t('error_add')));
      return res.json();
    },
    onSuccess: (data) => {
      invalidate();
      setModalOpen(false);
      setFormError('');
      if (data.temp_password) setTempPassword({ email: data.employee.email, password: data.temp_password });
    },
    onError: (e: any) => setFormError(e.message || t('error_add')),
  });

  const updateMutation = useMutation({
    mutationFn: async ({ userId, form }: { userId: string; form: FormState }) => {
      const res = await apiFetch(`${API_URL}/api/v1/employees/${userId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          preset: form.preset === '__custom__' ? undefined : form.preset,
          role: form.preset === '__custom__' ? form.role : undefined,
          sections: form.preset === '__custom__' ? Array.from(form.sections) : undefined,
          job_title: form.job_title.trim() || null,
        }),
      });
      if (!res.ok) throw new Error(await readError(res, t('error_save')));
      return res.json();
    },
    onSuccess: () => {
      invalidate();
      setModalOpen(false);
      setEditing(null);
      setFormError('');
    },
    onError: (e: any) => setFormError(e.message || t('error_save')),
  });

  const toggleActiveMutation = useMutation({
    mutationFn: async (emp: Employee) => {
      const res = await apiFetch(`${API_URL}/api/v1/employees/${emp.user_id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: !emp.is_active }),
      });
      if (!res.ok) throw new Error(await readError(res, t('error_save')));
    },
    onSuccess: invalidate,
  });

  const resetPasswordMutation = useMutation({
    mutationFn: async (emp: Employee) => {
      const res = await apiFetch(`${API_URL}/api/v1/employees/${emp.user_id}/reset-password`, { method: 'POST' });
      if (!res.ok) throw new Error(await readError(res, t('error_reset')));
      return { email: emp.email, ...(await res.json()) };
    },
    onSuccess: (data) => setTempPassword({ email: data.email, password: data.temp_password }),
  });

  const removeMutation = useMutation({
    mutationFn: async (emp: Employee) => {
      const res = await apiFetch(`${API_URL}/api/v1/employees/${emp.user_id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(await readError(res, t('error_remove')));
    },
    onSuccess: () => {
      invalidate();
      setConfirmRemove(null);
    },
  });

  if (!catalog || isLoading || !data) {
    return <div style={styles.wrap}><div className="glass-card" style={styles.card}>{t('loading')}</div></div>;
  }

  const seatLimitReached = data.limit !== null && data.used >= data.limit;

  return (
    <div style={styles.wrap}>
      <div className="glass-card" style={{ ...styles.card, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h3 style={styles.sectionTitle}>{t('title')}</h3>
          <p style={styles.cardSub}>
            {data.limit === null ? t('seats_unlimited', { used: data.used }) : t('seats_used', { used: data.used, limit: data.limit })}
          </p>
        </div>
        <button
          className="btn btn-primary"
          style={{ display: 'flex', alignItems: 'center', gap: '6px' }}
          disabled={seatLimitReached}
          title={seatLimitReached ? t('seat_limit_reached') : undefined}
          onClick={() => { setEditing(null); setFormError(''); setModalOpen(true); }}
        >
          <Plus size={14} /> {t('btn_add_employee')}
        </button>
      </div>

      {seatLimitReached && (
        <div className="glass-card" style={{ ...styles.card, borderLeft: '3px solid #e0a33e' }}>
          <p style={{ fontSize: '13px', margin: 0 }}>
            {t('seat_limit_message')}{' '}
            {onSelectTab && (
              <button className="btn btn-secondary" style={{ padding: '4px 10px', fontSize: '12px', marginLeft: '6px' }} onClick={() => onSelectTab('billing')}>
                {t('btn_go_billing')}
              </button>
            )}
          </p>
        </div>
      )}

      <div className="glass-card" style={styles.card}>
        {data.employees.length === 0 ? (
          <div style={styles.cardSub}>{t('no_employees')}</div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={styles.table}>
              <thead>
                <tr>
                  <th style={styles.th}>{t('table_person')}</th>
                  <th style={styles.th}>{t('table_job_title')}</th>
                  <th style={styles.th}>{t('table_role')}</th>
                  <th style={styles.th}>{t('table_sections')}</th>
                  <th style={styles.th}>{t('table_status')}</th>
                  <th style={styles.th}>{t('table_actions')}</th>
                </tr>
              </thead>
              <tbody>
                {data.employees.map((emp) => (
                  <tr key={emp.user_id}>
                    <td style={styles.td}>
                      <div>{emp.full_name || emp.email}</div>
                      {emp.full_name && <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{emp.email}</div>}
                      {emp.must_change_password && (
                        <span className="badge badge-warning" style={{ fontSize: '10px', marginTop: '4px', display: 'inline-block' }}>{t('badge_pending_first_login')}</span>
                      )}
                    </td>
                    <td style={styles.td}>{emp.job_title || '—'}</td>
                    <td style={styles.td}>
                      <span className={`badge ${emp.role === 'VIEWER' ? 'badge-warning' : 'badge-success'}`}>
                        {emp.role === 'VIEWER' ? t('role_viewer') : t('role_manager')}
                      </span>
                    </td>
                    <td style={{ ...styles.td, maxWidth: '220px' }}>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                        {emp.sections.filter((s) => s !== 'overview' && s !== 'help').map((s) => {
                          const title = catalog.sections.find((cs) => cs.key === s)?.title || s;
                          return <span key={s} style={styles.chip}>{title}</span>;
                        })}
                      </div>
                    </td>
                    <td style={styles.td}>
                      <button
                        className={`badge ${emp.is_active ? 'badge-success' : 'badge-error'}`}
                        style={{ border: 'none', cursor: 'pointer' }}
                        onClick={() => toggleActiveMutation.mutate(emp)}
                        title={t('toggle_active_hint')}
                      >
                        {emp.is_active ? t('status_active') : t('status_suspended')}
                      </button>
                    </td>
                    <td style={styles.td}>
                      <div style={{ display: 'flex', gap: '4px' }}>
                        <button className="btn btn-secondary" style={styles.iconBtn} title={t('btn_edit')} onClick={() => { setEditing(emp); setFormError(''); setModalOpen(true); }}>
                          <Pencil size={13} />
                        </button>
                        {!emp.is_telegram_only && (
                          <button className="btn btn-secondary" style={styles.iconBtn} title={t('btn_reset_password')} onClick={() => resetPasswordMutation.mutate(emp)}>
                            <KeyRound size={13} />
                          </button>
                        )}
                        <button className="btn btn-secondary" style={styles.iconBtn} title={t('btn_remove')} onClick={() => setConfirmRemove(emp)}>
                          <Trash2 size={13} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {modalOpen && (
        <EmployeeFormModal
          catalog={catalog}
          editing={editing}
          submitting={createMutation.isPending || updateMutation.isPending}
          error={formError}
          onClose={() => { setModalOpen(false); setEditing(null); setFormError(''); }}
          onSubmit={(form) => {
            if (editing) updateMutation.mutate({ userId: editing.user_id, form });
            else createMutation.mutate(form);
          }}
        />
      )}

      {tempPassword && (
        <TempPasswordBox email={tempPassword.email} password={tempPassword.password} onClose={() => setTempPassword(null)} />
      )}

      {confirmRemove && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 100, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setConfirmRemove(null)}>
          <div className="glass-card" style={{ width: '360px', padding: '20px' }} onClick={(e) => e.stopPropagation()}>
            <h2 style={{ fontSize: '15px', fontWeight: 700, marginBottom: '10px' }}>{t('confirm_remove_title')}</h2>
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '16px' }}>
              {t('confirm_remove_body', { name: confirmRemove.full_name || confirmRemove.email })}
            </p>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button className="btn btn-secondary" style={{ flex: 1 }} onClick={() => setConfirmRemove(null)}>{t('btn_cancel')}</button>
              <button className="btn btn-primary" style={{ flex: 1, background: '#ef4444' }} onClick={() => removeMutation.mutate(confirmRemove)}>{t('btn_remove')}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

const labelStyle: React.CSSProperties = { display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '4px' };
const inputStyle: React.CSSProperties = { width: '100%' };

const styles: Record<string, React.CSSProperties> = {
  wrap: { display: 'flex', flexDirection: 'column', gap: '16px', padding: '24px' },
  card: { padding: '20px' },
  sectionTitle: { margin: 0, fontSize: '16px', color: '#f3f4f6' },
  cardSub: { fontSize: '12px', color: '#9ca3af', marginTop: '4px' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: '13px' },
  th: { textAlign: 'left', padding: '8px', color: '#9ca3af', borderBottom: '1px solid rgba(255,255,255,0.08)' },
  td: { padding: '8px', borderBottom: '1px solid rgba(255,255,255,0.04)', color: '#e5e7eb', verticalAlign: 'top' },
  chip: { fontSize: '10px', padding: '2px 6px', borderRadius: '4px', background: 'rgba(255,255,255,0.08)', color: '#d1d5db' },
  iconBtn: { padding: '6px 8px' },
};
