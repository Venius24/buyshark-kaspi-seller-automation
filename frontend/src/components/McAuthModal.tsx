import { useTranslation } from 'react-i18next';

interface McAuthModalProps {
  mcAuthStep: 'idle' | 'credentials' | 'loading' | 'success';
  mcAuthError: string;
  emailInput: string;
  onEmailChange: (value: string) => void;
  passwordInput: string;
  onPasswordChange: (value: string) => void;
  onClose: () => void;
  onSubmit: () => void;
}

export function McAuthModal({
  mcAuthStep,
  mcAuthError,
  emailInput,
  onEmailChange,
  passwordInput,
  onPasswordChange,
  onClose,
  onSubmit,
}: McAuthModalProps) {
  const { t } = useTranslation('modal_mc_auth');

  if (mcAuthStep === 'idle') return null;

  return (
    <div className="modal-overlay">
      <div className="glass-card modal-content" style={{ maxWidth: '400px', width: '100%', padding: '30px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
          <h3 style={{ fontSize: '18px', fontWeight: 700 }}>{t('title')}</h3>
          <button
            onClick={onClose}
            style={{ background: 'transparent', border: 'none', color: '#fff', fontSize: '20px', cursor: 'pointer' }}
          >
            ✕
          </button>
        </div>

        {mcAuthError && (
          <div style={{ padding: '10px 12px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239, 68, 68, 0.3)', color: '#fca5a5', borderRadius: '8px', marginBottom: '16px', fontSize: '13px' }}>
            {mcAuthError}
          </div>
        )}

        {mcAuthStep === 'credentials' && (
          <div>
            <p style={{ fontSize: '13px', marginBottom: '16px', color: 'var(--text-muted)' }}>
              {t('description')}
            </p>
            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>{t('label_email')}</label>
              <input
                type="email"
                className="form-input"
                placeholder={t('placeholder_email')}
                value={emailInput}
                onChange={(e) => onEmailChange(e.target.value)}
              />
            </div>
            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>{t('label_password')}</label>
              <input
                type="password"
                className="form-input"
                placeholder={t('placeholder_password')}
                value={passwordInput}
                onChange={(e) => onPasswordChange(e.target.value)}
              />
            </div>
            <button
              className="btn btn-primary"
              style={{ width: '100%' }}
              onClick={onSubmit}
            >
              {t('btn_login')}
            </button>
          </div>
        )}

        {mcAuthStep === 'loading' && (
          <div style={{ textAlign: 'center', padding: '20px 0' }}>
            <div style={{ width: '32px', height: '32px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 16px auto' }} />
            <p style={{ fontSize: '13px', color: 'var(--text-muted)' }}>{t('loading_message')}</p>
          </div>
        )}

        {mcAuthStep === 'success' && (
          <div style={{ textAlign: 'center', padding: '10px 0' }}>
            <div style={{
              width: '48px', height: '48px',
              background: 'rgba(16, 185, 129, 0.1)',
              border: '2px solid var(--success)',
              borderRadius: '50%', display: 'inline-flex',
              justifyContent: 'center', alignItems: 'center',
              color: 'var(--success)', fontSize: '24px',
              margin: '0 auto 16px auto'
            }}>✓</div>
            <h4 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '8px' }}>{t('success_title')}</h4>
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '20px' }}>
              {t('success_message')}
            </p>
            <button className="btn btn-primary" style={{ width: '100%' }} onClick={onClose}>
              {t('btn_close')}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
