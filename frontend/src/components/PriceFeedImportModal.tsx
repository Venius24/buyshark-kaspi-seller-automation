import { useTranslation } from 'react-i18next';
import { X, AlertTriangle, RefreshCw, Check } from 'lucide-react';
import type { PriceFeedImportStep } from '../hooks/usePriceFeedImport';
import type { PriceFeedMerchant, PriceFeedPreviewResult, PriceFeedApplyResult } from '../types';

interface PriceFeedImportModalProps {
  step: PriceFeedImportStep;
  phone: string;
  onPhoneChange: (value: string) => void;
  password: string;
  onPasswordChange: (value: string) => void;
  merchants: PriceFeedMerchant[];
  preview: PriceFeedPreviewResult | null;
  applyResult: PriceFeedApplyResult | null;
  overwrite: boolean;
  onOverwriteChange: (value: boolean) => void;
  activate: boolean;
  onActivateChange: (value: boolean) => void;
  error: string;
  onClose: () => void;
  onSubmitCredentials: () => void;
  onChooseMerchant: (merchantId: string) => void;
  onApply: () => void;
  onFinish: () => void;
}

export function PriceFeedImportModal({
  step,
  phone,
  onPhoneChange,
  password,
  onPasswordChange,
  merchants,
  preview,
  applyResult,
  overwrite,
  onOverwriteChange,
  activate,
  onActivateChange,
  error,
  onClose,
  onSubmitCredentials,
  onChooseMerchant,
  onApply,
  onFinish,
}: PriceFeedImportModalProps) {
  const { t } = useTranslation('modal_pricefeed_import');

  const SKIP_REASON_KEYS: Record<string, string> = {
    algorithm_off_no_limits: 'algorithm_off',
    no_min_or_max: 'no_min_max',
    min_gte_max: 'min_gte_max',
    not_in_catalog: 'not_in_catalog',
    duplicate_sku: 'duplicate_sku',
    unknown_algorithm: 'unknown_algorithm',
  };

  if (step === 'idle') return null;

  return (
    <div className="modal-overlay">
      {/* maxHeight/overflow — страховка: что бы ни пришло с бэка (сотни предупреждений,
          длинный список пропусков), модалка обязана остаться в пределах экрана и листаться,
          а не растянуть страницу так, что до кнопок не добраться. */}
      <div
        className="glass-card modal-content"
        style={{ maxWidth: '520px', width: '100%', padding: '30px', maxHeight: '85vh', overflowY: 'auto' }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
          <h3 style={{ fontSize: '18px', fontWeight: 700 }}>{t('title')}</h3>
          <button
            onClick={step === 'done' ? onFinish : onClose}
            style={{ background: 'transparent', border: 'none', color: '#fff', fontSize: '20px', cursor: 'pointer', display: 'flex' }}
          >
            <X size={20} />
          </button>
        </div>

        {error && (
          <div style={{ padding: '10px 12px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239, 68, 68, 0.3)', color: '#fca5a5', borderRadius: '8px', marginBottom: '16px', fontSize: '13px' }}>
            {error}
          </div>
        )}

        {step === 'credentials' && (
          <div>
            <p style={{ fontSize: '13px', marginBottom: '16px', color: 'var(--text-muted)' }}>
              {t('credentials_description')}
            </p>
            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>{t('label_phone')}</label>
              <input
                type="tel"
                className="form-input"
                placeholder={t('placeholder_phone')}
                value={phone}
                autoComplete="off"
                onChange={(e) => onPhoneChange(e.target.value)}
              />
            </div>
            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>{t('label_password')}</label>
              <input
                type="password"
                className="form-input"
                placeholder={t('placeholder_password')}
                value={password}
                autoComplete="current-password"
                onChange={(e) => onPasswordChange(e.target.value)}
              />
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '16px' }}>
              <input type="checkbox" required /> {t('confirmation')}
            </label>
            <button className="btn btn-primary" style={{ width: '100%' }} onClick={onSubmitCredentials} disabled={!phone || !password}>
              {t('btn_start_import')}
            </button>
          </div>
        )}

        {step === 'loading' && (
          <div style={{ textAlign: 'center', padding: '20px 0' }}>
            <div style={{ width: '32px', height: '32px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 16px auto' }} />
            <p style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
              {t('loading_message')}
            </p>
          </div>
        )}

        {step === 'choose_merchant' && (
          <div>
            <p style={{ fontSize: '13px', marginBottom: '16px', color: 'var(--text-muted)' }}>
              {t('merchant_choice_description')}
            </p>
            {merchants.map((m) => (
              <button
                key={m.id}
                className="btn btn-secondary"
                style={{ width: '100%', marginBottom: '8px', textAlign: 'left' }}
                onClick={() => onChooseMerchant(m.id)}
              >
                {m.name}
              </button>
            ))}
          </div>
        )}

        {step === 'preview' && preview && (
          <div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', marginBottom: '16px' }}>
              <div style={{ background: 'rgba(255,255,255,0.04)', borderRadius: '8px', padding: '10px 12px' }}>
                <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{t('preview_fetched')}</div>
                <div style={{ fontSize: '20px', fontWeight: 700 }}>{preview.total_fetched}</div>
              </div>
              <div style={{ background: 'rgba(16, 185, 129, 0.08)', borderRadius: '8px', padding: '10px 12px' }}>
                <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{t('preview_importable')}</div>
                <div style={{ fontSize: '20px', fontWeight: 700, color: 'var(--success)' }}>{preview.importable}</div>
              </div>
            </div>

            {!!preview.already_configured && (
              <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '10px' }}>
                {t('already_configured').replace('%count%', String(preview.already_configured))}
              </div>
            )}

            {!!preview.unmatched && (
              <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '10px' }}>
                {t('unmatched').replace('%count%', String(preview.unmatched))}
              </div>
            )}

            {preview.skipped && Object.keys(preview.skipped).length > 0 && (
              <details style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '12px' }}>
                <summary style={{ cursor: 'pointer' }}>{t('skipped_summary')}</summary>
                <ul style={{ marginTop: '6px', paddingLeft: '18px' }}>
                  {Object.entries(preview.skipped).map(([reason, count]) => (
                    <li key={reason}>{count} — {t(SKIP_REASON_KEYS[reason] || reason)}</li>
                  ))}
                </ul>
              </details>
            )}

            {!!preview.warnings?.length && (
              <div
                style={{
                  fontSize: '12px',
                  color: '#fbbf24',
                  marginBottom: '12px',
                  maxHeight: '96px',
                  overflowY: 'auto',
                  wordBreak: 'break-word',
                }}
              >
                {preview.warnings.map((w, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <AlertTriangle size={14} style={{ flexShrink: 0 }} /> {w}
                  </div>
                ))}
              </div>
            )}

            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '12px' }}>
              {t('transfer_note')}
            </div>

            {!!preview.sample?.length && (
              <div style={{ maxHeight: '180px', overflowY: 'auto', border: '1px solid rgba(255,255,255,0.06)', borderRadius: '8px', marginBottom: '16px' }}>
                <table style={{ width: '100%', fontSize: '12px', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ textAlign: 'left', color: 'var(--text-muted)' }}>
                      <th style={{ padding: '6px 8px' }}>SKU</th>
                      <th style={{ padding: '6px 8px' }}>Min</th>
                      <th style={{ padding: '6px 8px' }}>Max</th>
                      <th style={{ padding: '6px 8px' }}>Режим</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.sample.map((row) => (
                      <tr key={row.sku} style={{ borderTop: '1px solid rgba(255,255,255,0.04)' }}>
                        <td style={{ padding: '6px 8px' }}>
                          {row.sku}
                          {row.already_configured ? <RefreshCw size={11} style={{ display: 'inline', marginLeft: '4px', verticalAlign: 'middle' }} /> : ''}
                        </td>
                        <td style={{ padding: '6px 8px' }}>{row.min_price}</td>
                        <td style={{ padding: '6px 8px' }}>{row.max_price}</td>
                        <td style={{ padding: '6px 8px' }}>{row.mode}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <label style={{
              display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', marginBottom: '16px',
              opacity: preview.already_configured ? 1 : 0.5,
            }}>
              <input
                type="checkbox"
                checked={overwrite}
                disabled={!preview.already_configured}
                onChange={(e) => onOverwriteChange(e.target.checked)}
              />
              {t('overwrite_label').replace('%count%', String(preview.already_configured || 0))}
            </label>

            <label style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', fontSize: '13px', marginBottom: '8px' }}>
              <input
                type="checkbox"
                checked={activate}
                onChange={(e) => onActivateChange(e.target.checked)}
                style={{ marginTop: '3px' }}
              />
              <span>
                {t('activate_label')}
                <span style={{ display: 'block', color: 'var(--text-muted)', fontSize: '12px', marginTop: '2px' }}>
                  {t('activate_note').replace('%count%', String(preview.importable))}
                </span>
              </span>
            </label>

            <button className="btn btn-primary" style={{ width: '100%', marginTop: '8px' }} onClick={onApply} disabled={!preview.importable}>
              {t('btn_import').replace('%count%', String(preview.importable))}
            </button>
          </div>
        )}

        {step === 'applying' && (
          <div style={{ textAlign: 'center', padding: '20px 0' }}>
            <div style={{ width: '32px', height: '32px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 16px auto' }} />
            <p style={{ fontSize: '13px', color: 'var(--text-muted)' }}>{t('applying_message')}</p>
          </div>
        )}

        {step === 'done' && applyResult && (
          <div style={{ textAlign: 'center', padding: '10px 0' }}>
            <div style={{
              width: '48px', height: '48px',
              background: 'rgba(16, 185, 129, 0.1)',
              border: '2px solid var(--success)',
              borderRadius: '50%', display: 'inline-flex',
              justifyContent: 'center', alignItems: 'center',
              color: 'var(--success)', fontSize: '24px',
              margin: '0 auto 16px auto'
            }}><Check size={24} /></div>
            <h4 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '8px' }}>{t('done_title')}</h4>
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '20px' }}>
              {t('done_message')
                .replace('%created%', String(applyResult.created))
                .replace('%updated%', String(applyResult.updated))
                .replace('%skipped_text%', applyResult.skipped_existing
                  ? t('skipped_existing').replace('%count%', String(applyResult.skipped_existing))
                  : '')}
            </p>
            {!applyResult.activated && (
              <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '20px' }}>
                {t('not_activated_message')}
              </p>
            )}
            <button className="btn btn-primary" style={{ width: '100%' }} onClick={onFinish}>
              {t('btn_close')}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
