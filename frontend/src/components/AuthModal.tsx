import React, { useState } from 'react';
import { API_URL } from '../api';
import { clearPersistedQueryCache } from '../queryClient';

interface AuthModalProps {
  onSuccess: () => void;
}

export const AuthModal: React.FC<AuthModalProps> = ({ onSuccess }) => {
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
        let msg = `Ошибка сервера (${res.status})`;
        try {
          const errJson = await res.json();
          msg = errJson.detail || errJson.message || msg;
        } catch {
          if (res.status === 502) msg = 'Сервер недоступен (502 Bad Gateway). Проверьте статус бэкенда!';
          else if (res.status === 500) msg = 'Ошибка на бэкенде (500 Internal Server Error)';
          else if (res.status === 404) msg = 'Маршрут API не найден (404 Not Found)';
        }
        throw new Error(msg);
      }

      let data;
      try {
        data = await res.json();
      } catch {
        throw new Error('Некорректный ответ от сервера (не JSON)');
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
      setError(err.message || 'Ошибка сети');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay">
      <div className="glass-card" style={styles.card}>
        <div style={styles.logoRow}>
          <img src="/buyshark-logo.svg" alt="BuyShark" style={styles.logoIcon} />
        </div>
        <h2 style={styles.title}>
          {isRegister ? 'Регистрация в BuyShark' : 'Вход в личный кабинет'}
        </h2>

        {error && <div style={styles.errorBox}>⚠️ {error}</div>}

        <form onSubmit={handleSubmit} style={styles.form}>
          {isRegister && (
            <>
              <div style={styles.group}>
                <label style={styles.label}>ФИО / Название ИП:</label>
                <input
                  type="text"
                  placeholder="Иван Иванов"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  className="form-input"
                  required
                />
              </div>

              <div style={styles.group}>
                <label style={styles.label}>Телефон (+7):</label>
                <input
                  type="text"
                  placeholder="+7 (707) 000-0000"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  className="form-input"
                />
              </div>
            </>
          )}

          <div style={styles.group}>
            <label style={styles.label}>Email адрес:</label>
            <input
              type="email"
              placeholder="name@company.kz"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="form-input"
              required
            />
          </div>

          <div style={styles.group}>
            <label style={styles.label}>Пароль:</label>
            <input
              type="password"
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="form-input"
              required
            />
          </div>

          <button type="submit" disabled={loading} className="btn btn-primary" style={styles.submitBtn}>
            {loading ? '⏳ Проверка...' : isRegister ? 'Зарегистрироваться' : 'Войти в кабинет'}
          </button>
        </form>

        <div style={styles.toggleRow}>
          {isRegister ? 'Уже есть аккаунт?' : 'Впервые в системе?'}
          <button onClick={() => setIsRegister(!isRegister)} style={styles.toggleBtn}>
            {isRegister ? 'Войти' : 'Создать аккаунт'}
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
