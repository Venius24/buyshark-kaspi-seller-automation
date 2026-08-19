import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from './shared/LanguageSwitcher';
import { API_URL } from '../api';
import { clearPersistedQueryCache } from '../queryClient';

interface AuthModalProps {
  onSuccess: () => void;
}

export const AuthModal: React.FC<AuthModalProps> = ({ onSuccess }) => {
  const { t } = useTranslation('modal_auth');
  const [isRegister, setIsRegister] = useState<boolean>(false);
  const [email, setEmail] = useState<string>('');
  const [password, setPassword] = useState<string>('');
  const [fullName, setFullName] = useState<string>('');
  const [phone, setPhone] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);


  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    const endpoint = isRegister ? `${API_URL}/api/v1/auth/register` : `${API_URL}/api/v1/auth/login`;
    const payload = isRegister
      ? { email, password, full_name: fullName, phone }
      : { email, password };

    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        let msg = t('error_server', { code: res.status });
        try {
          const errJson = await res.json();
          if (Array.isArray(errJson.detail)) {
            msg = errJson.detail.map((d: any) => d.msg || String(d)).join('; ');
          } else {
            msg = errJson.detail || errJson.message || msg;
          }
        } catch {
          if (res.status === 502) msg = t('error_502');
          else if (res.status === 500) msg = t('error_500');
          else if (res.status === 404) msg = t('error_404');
        }
        throw new Error(msg);
      }

      let data;
      try {
        data = await res.json();
      } catch {
        throw new Error(t('error_invalid_response'));
      }
      if (data.access_token) {
        // Кэш react-query персистится в общий localStorage-ключ, не привязан
        // к пользователю -- без сброса новый логин мог унаследовать чужой/
        // устаревший снимок (напр. "магазинов нет" от прошлой сессии на
        // этом устройстве) до фонового refetch.
        clearPersistedQueryCache();
        localStorage.setItem('access_token', data.access_token);
        onSuccess();
      }
    } catch (err: any) {
      setError(err.message || t('error_network'));
    } finally {
      setLoading(false);
    }
  };

  return (
    // auth-screen: это не поверх-модалка, а весь экран входа — на мобильном
    // он не должен превращаться в «шторку» снизу (см. mobile.css).
    <div className="modal-overlay auth-screen">
      <div style={{ position: 'fixed', top: '20px', right: '20px', zIndex: 1000 }}>
        <LanguageSwitcher />
      </div>
      <div className="glass-card" style={styles.card}>
        <div style={styles.logoRow}>
          <img src="/buyshark-logo.svg" alt="BuyShark" style={styles.logoIcon} />
        </div>
        <h2 style={styles.title}>
          {isRegister ? t('title_register') : t('title_login')}
        </h2>

        {error && <div style={styles.errorBox}>⚠️ {error}</div>}

        <form onSubmit={handleSubmit} style={styles.form}>
          {isRegister && (
            <>
              <div style={styles.group}>
                <label style={styles.label}>{t('label_fio')}</label>
                <input
                  type="text"
                  placeholder={t('placeholder_fio')}
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  className="form-input"
                  required
                />
              </div>

              <div style={styles.group}>
                <label style={styles.label}>{t('label_phone')}</label>
                <input
                  type="text"
                  placeholder={t('placeholder_phone')}
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  className="form-input"
                />
              </div>
            </>
          )}

          <div style={styles.group}>
            <label style={styles.label}>{t('label_email')}</label>
            <input
              type="email"
              placeholder={t('placeholder_email')}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="form-input"
              required
            />
          </div>

          <div style={styles.group}>
            <label style={styles.label}>{t('label_password')}</label>
            <input
              type="password"
              placeholder={t('placeholder_password')}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="form-input"
              required
            />
          </div>

          <button type="submit" disabled={loading} className="btn btn-primary" style={styles.submitBtn}>
            {loading ? t('btn_loading') : isRegister ? t('btn_register') : t('btn_login')}
          </button>
        </form>

        <div style={styles.toggleRow}>
          {isRegister ? t('toggle_has_account') : t('toggle_create_account')}
          <button onClick={() => setIsRegister(!isRegister)} style={styles.toggleBtn}>
            {isRegister ? t('toggle_btn_login') : t('toggle_btn_register')}
          </button>
        </div>
      </div>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  card: {
    padding: '32px',
    width: '400px',
    maxWidth: '90%',
    boxShadow: '0 20px 40px rgba(0,0,0,0.5)',
  },
  logoRow: {
    display: 'flex',
    justifyContent: 'center',
    marginBottom: '16px',
  },
  logoIcon: {
    width: '44px',
    height: '44px',
    borderRadius: '12px',
    objectFit: 'cover',
  },
  title: {
    margin: '0 0 20px 0',
    fontSize: '20px',
    fontWeight: 700,
    color: '#ffffff',
    textAlign: 'center',
  },
  errorBox: {
    background: 'rgba(226, 84, 95, 0.12)',
    border: '1px solid rgba(226, 84, 95, 0.35)',
    color: '#f3a2a8',
    padding: '10px 14px',
    borderRadius: '8px',
    fontSize: '13px',
    marginBottom: '16px',
  },
  form: {
    display: 'flex',
    flexDirection: 'column',
    gap: '14px',
  },
  group: {
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
  },
  label: {
    fontSize: '13px',
    color: '#cbd5e1',
  },
  submitBtn: {
    marginTop: '10px',
    width: '100%',
    padding: '12px',
    fontSize: '15px',
  },
  toggleRow: {
    marginTop: '20px',
    fontSize: '13px',
    color: 'var(--text-muted)',
    textAlign: 'center',
  },
  toggleBtn: {
    background: 'transparent',
    border: 'none',
    color: 'var(--secondary)',
    fontWeight: 700,
    marginLeft: '6px',
    cursor: 'pointer',
  },
};
