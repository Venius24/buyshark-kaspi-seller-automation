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
    <div style={styles.overlay}>
      <div style={styles.card}>
        <h2 style={styles.title}>
          {isRegister ? '🚀 Регистрация в Kaspi SaaS' : '🔐 Вход в личный кабинет'}
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
                  style={styles.input}
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
                  style={styles.input}
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
              style={styles.input}
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
              style={styles.input}
              required
            />
          </div>

          <button type="submit" disabled={loading} style={styles.submitBtn}>
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
  overlay: {
    position: 'fixed',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    background: 'rgba(15, 23, 42, 0.95)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 10000,
  },
  card: {
    background: '#1e293b',
    border: '1px solid #334155',
    borderRadius: '16px',
    padding: '32px',
    width: '400px',
    maxWidth: '90%',
    boxShadow: '0 20px 40px rgba(0,0,0,0.5)',
  },
  title: {
    margin: '0 0 20px 0',
    fontSize: '20px',
    fontWeight: 700,
    color: '#ffffff',
    textAlign: 'center',
  },
  errorBox: {
    background: '#451a1a',
    border: '1px solid #991b1b',
    color: '#fca5a5',
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
  input: {
    background: '#0f172a',
    border: '1px solid #334155',
    borderRadius: '8px',
    padding: '10px 14px',
    color: '#ffffff',
    fontSize: '14px',
    outline: 'none',
  },
  submitBtn: {
    marginTop: '10px',
    background: 'linear-gradient(135deg, #6366f1 0%, #4f46e5 100%)',
    color: '#ffffff',
    border: 'none',
    borderRadius: '10px',
    padding: '12px',
    fontSize: '15px',
    fontWeight: 700,
    cursor: 'pointer',
  },
  toggleRow: {
    marginTop: '20px',
    fontSize: '13px',
    color: '#94a3b8',
    textAlign: 'center',
  },
  toggleBtn: {
    background: 'transparent',
    border: 'none',
    color: '#6366f1',
    fontWeight: 700,
    marginLeft: '6px',
    cursor: 'pointer',
  },
};
