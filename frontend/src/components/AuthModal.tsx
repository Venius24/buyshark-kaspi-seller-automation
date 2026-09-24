import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, CheckCircle2 } from 'lucide-react';
import { LanguageSwitcher } from './shared/LanguageSwitcher';
import { API_URL } from '../api';
import { clearPersistedQueryCache } from '../queryClient';
import { waLink } from '../data/contacts';

interface AuthModalProps {
  onSuccess: () => void;
}

// Ссылка из письма «Восстановление пароля» ведёт на /?reset_token=… —
// читаем один раз при монтировании экрана входа.
const readResetToken = (): string | null => {
  try {
    return new URLSearchParams(window.location.search).get('reset_token');
  } catch {
    return null;
  }
};

const clearResetTokenFromUrl = () => {
  const url = new URL(window.location.href);
  url.searchParams.delete('reset_token');
  window.history.replaceState(null, '', url.pathname + url.search + url.hash);
};

type Mode = 'login' | 'register' | 'forgot' | 'reset';

export const AuthModal: React.FC<AuthModalProps> = ({ onSuccess }) => {
  const { t } = useTranslation('modal_auth');
  const [resetToken] = useState<string | null>(readResetToken);
  const [mode, setMode] = useState<Mode>(resetToken ? 'reset' : 'login');
  const isRegister = mode === 'register';
  const [info, setInfo] = useState<string | null>(null);
  const [password2, setPassword2] = useState<string>('');
  const [email, setEmail] = useState<string>('');
  const [password, setPassword] = useState<string>('');
  const [fullName, setFullName] = useState<string>('');
  const [phone, setPhone] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);


  const switchMode = (next: Mode) => {
    setMode(next);
    setError(null);
    setInfo(null);
  };

  const readError = async (res: Response): Promise<string> => {
    try {
      const errJson = await res.json();
      if (Array.isArray(errJson.detail)) return errJson.detail.map((d: any) => d.msg || String(d)).join('; ');
      return errJson.detail || errJson.message || t('error_server', { code: res.status });
    } catch {
      return t('error_server', { code: res.status });
    }
  };

  const handleForgot = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/api/v1/auth/forgot-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
      if (!res.ok) throw new Error(await readError(res));
      setInfo(t('forgot_sent', { email }));
    } catch (err: any) {
      setError(err.message || t('error_network'));
    } finally {
      setLoading(false);
    }
  };

  const handleReset = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (password.length < 8) {
      setError(t('reset_too_short'));
      return;
    }
    if (password !== password2) {
      setError(t('reset_mismatch'));
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/api/v1/auth/reset-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: resetToken, new_password: password }),
      });
      if (!res.ok) throw new Error(await readError(res));
      clearResetTokenFromUrl();
      setPassword('');
      setPassword2('');
      setMode('login');
      setInfo(t('reset_done'));
    } catch (err: any) {
      setError(err.message || t('error_network'));
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setInfo(null);

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
          {mode === 'register' ? t('title_register')
            : mode === 'forgot' ? t('title_forgot')
            : mode === 'reset' ? t('title_reset')
            : t('title_login')}
        </h2>

        {info && (
          <div style={{ ...styles.infoBox, display: 'flex', alignItems: 'center', gap: '8px' }}>
            <CheckCircle2 size={16} style={{ flexShrink: 0 }} /> {info}
          </div>
        )}

        {error && (
          <div style={{ ...styles.errorBox, display: 'flex', alignItems: 'center', gap: '8px' }}>
            <AlertTriangle size={16} style={{ flexShrink: 0 }} /> {error}
          </div>
        )}

        {mode === 'forgot' && (
          <form onSubmit={handleForgot} style={styles.form}>
            <p style={styles.hint}>{t('forgot_hint')}</p>
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
            <button type="submit" disabled={loading || !!info} className="btn btn-primary" style={styles.submitBtn}>
              {loading ? t('btn_loading') : t('btn_send_link')}
            </button>
          </form>
        )}

        {mode === 'reset' && (
          <form onSubmit={handleReset} style={styles.form}>
            <div style={styles.group}>
              <label style={styles.label}>{t('label_new_password')}</label>
              <input
                type="password"
                autoComplete="new-password"
                placeholder={t('placeholder_password')}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="form-input"
                required
              />
            </div>
            <div style={styles.group}>
              <label style={styles.label}>{t('label_new_password2')}</label>
              <input
                type="password"
                autoComplete="new-password"
                placeholder={t('placeholder_password')}
                value={password2}
                onChange={(e) => setPassword2(e.target.value)}
                className="form-input"
                required
              />
            </div>
            <button type="submit" disabled={loading} className="btn btn-primary" style={styles.submitBtn}>
              {loading ? t('btn_loading') : t('btn_save_password')}
            </button>
          </form>
        )}

        {(mode === 'login' || mode === 'register') && (
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
          {mode === 'login' && (
            <button type="button" onClick={() => switchMode('forgot')} style={styles.forgotBtn}>
              {t('forgot_link')}
            </button>
          )}
        </form>
        )}

        {mode === 'forgot' || mode === 'reset' ? (
          <div style={styles.toggleRow}>
            <button onClick={() => { if (mode === 'reset') clearResetTokenFromUrl(); switchMode('login'); }} style={styles.toggleBtn}>
              {t('back_to_login')}
            </button>
          </div>
        ) : (
          <div style={styles.toggleRow}>
            {isRegister ? t('toggle_has_account') : t('toggle_create_account')}
            <button onClick={() => switchMode(isRegister ? 'login' : 'register')} style={styles.toggleBtn}>
              {isRegister ? t('toggle_btn_login') : t('toggle_btn_register')}
            </button>
          </div>
        )}

        <div style={styles.helpRow}>
          <a
            href={waLink('auth', 'Здравствуйте! Нужна помощь со входом в BuyShark.')}
            target="_blank"
            rel="noopener noreferrer"
            style={styles.helpLink}
          >
            {t('need_help')}
          </a>
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
  infoBox: {
    background: 'rgba(34, 197, 94, 0.12)',
    border: '1px solid rgba(34, 197, 94, 0.35)',
    color: '#86efac',
    padding: '10px 14px',
    borderRadius: '8px',
    fontSize: '13px',
    marginBottom: '16px',
  },
  hint: {
    margin: 0,
    fontSize: '13px',
    color: 'var(--text-muted)',
    lineHeight: 1.5,
  },
  forgotBtn: {
    background: 'transparent',
    border: 'none',
    color: 'var(--text-muted)',
    fontSize: '13px',
    cursor: 'pointer',
    textDecoration: 'underline',
    alignSelf: 'center',
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
  helpRow: {
    marginTop: '10px',
    textAlign: 'center',
  },
  helpLink: {
    fontSize: '12px',
    color: 'var(--text-muted)',
    textDecoration: 'underline',
  },
};
