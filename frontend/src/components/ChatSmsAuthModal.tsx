import { useTranslation } from 'react-i18next';
import { X, Check } from 'lucide-react';
import { ParcelLoader } from './shared/ParcelLoader';

interface ChatSmsAuthModalProps {
  chatAuthStep: 'idle' | 'phone' | 'code' | 'loading' | 'success';
  chatAuthError: string;
  phoneInput: string;
  onPhoneChange: (value: string) => void;
  codeInput: string;
  onCodeChange: (value: string) => void;
  onClose: () => void;
  onSubmitPhone: () => void;
  onSubmitCode: () => void;
}

export function ChatSmsAuthModal({
  chatAuthStep,
  chatAuthError,
  phoneInput,
  onPhoneChange,
  codeInput,
  onCodeChange,
  onClose,
  onSubmitPhone,
  onSubmitCode,
}: ChatSmsAuthModalProps) {
  const { t } = useTranslation('modal_chat_sms_auth');

  if (chatAuthStep === 'idle') return null;

  return (
    <div className="modal-overlay">
      <div className="glass-card modal-content" style={{ maxWidth: '400px', width: '100%', padding: '30px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
          <h3 style={{ fontSize: '18px', fontWeight: 700 }}>{t('title')}</h3>
          <button
            onClick={onClose}
            style={{ background: 'transparent', border: 'none', color: '#fff', fontSize: '20px', cursor: 'pointer', display: 'flex' }}
          >
            <X size={20} />
          </button>
        </div>

        {chatAuthError && (
          <div style={{ padding: '10px 12px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239, 68, 68, 0.3)', color: '#fca5a5', borderRadius: '8px', marginBottom: '16px', fontSize: '13px' }}>
            {chatAuthError}
          </div>
        )}

        {chatAuthStep === 'phone' && (
          <div>
            <p style={{ fontSize: '13px', marginBottom: '16px', color: 'var(--text-muted)' }}>
              {t('description')}
            </p>
            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>{t('label_phone')}</label>
              <input
                type="tel"
                className="form-input"
                placeholder={t('placeholder_phone')}
                value={phoneInput}
                onChange={(e) => onPhoneChange(e.target.value)}
              />
            </div>
            <button
              className="btn btn-primary"
              style={{ width: '100%' }}
              onClick={onSubmitPhone}
            >
              {t('btn_send_sms')}
            </button>
          </div>
        )}

        {chatAuthStep === 'code' && (
          <div>
            <p style={{ fontSize: '13px', marginBottom: '16px', color: 'var(--text-muted)' }}>
              {t('code_description', { phone: phoneInput })}
            </p>
            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>{t('label_code')}</label>
              <input
                type="text"
                inputMode="numeric"
                className="form-input"
                placeholder={t('placeholder_code')}
                value={codeInput}
                onChange={(e) => onCodeChange(e.target.value)}
              />
            </div>
            <button
              className="btn btn-primary"
              style={{ width: '100%' }}
              onClick={onSubmitCode}
            >
              {t('btn_confirm')}
            </button>
          </div>
        )}

        {chatAuthStep === 'loading' && (
          <div style={{ textAlign: 'center', padding: '20px 0' }}>
            <div style={{ marginBottom: '16px' }}>
              <ParcelLoader />
            </div>
            <p style={{ fontSize: '13px', color: 'var(--text-muted)' }}>{t('loading_message')}</p>
          </div>
        )}

        {chatAuthStep === 'success' && (
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
