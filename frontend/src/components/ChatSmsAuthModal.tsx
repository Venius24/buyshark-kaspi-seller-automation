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
  if (chatAuthStep === 'idle') return null;

  return (
    <div className="modal-overlay">
      <div className="glass-card modal-content" style={{ maxWidth: '400px', width: '100%', padding: '30px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
          <h3 style={{ fontSize: '18px', fontWeight: 700 }}>Вход в чат Kaspi (SMS)</h3>
          <button
            onClick={onClose}
            style={{ background: 'transparent', border: 'none', color: '#fff', fontSize: '20px', cursor: 'pointer' }}
          >
            ✕
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
              Чат Kaspi работает только с сессией, полученной через телефон+SMS — это отдельный вход от основного кабинета (email/пароль). Введите номер телефона от аккаунта Kaspi Merchant.
            </p>
            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>Телефон</label>
              <input
                type="tel"
                className="form-input"
                placeholder="+7 (707) 000-0000"
                value={phoneInput}
                onChange={(e) => onPhoneChange(e.target.value)}
              />
            </div>
            <button
              className="btn btn-primary"
              style={{ width: '100%' }}
              onClick={onSubmitPhone}
            >
              Отправить SMS
            </button>
          </div>
        )}

        {chatAuthStep === 'code' && (
          <div>
            <p style={{ fontSize: '13px', marginBottom: '16px', color: 'var(--text-muted)' }}>
              Введите код из SMS, отправленного на {phoneInput}.
            </p>
            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>Код из SMS</label>
              <input
                type="text"
                inputMode="numeric"
                className="form-input"
                placeholder="123456"
                value={codeInput}
                onChange={(e) => onCodeChange(e.target.value)}
              />
            </div>
            <button
              className="btn btn-primary"
              style={{ width: '100%' }}
              onClick={onSubmitCode}
            >
              Подтвердить
            </button>
          </div>
        )}

        {chatAuthStep === 'loading' && (
          <div style={{ textAlign: 'center', padding: '20px 0' }}>
            <div style={{ width: '32px', height: '32px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 16px auto' }} />
            <p style={{ fontSize: '13px', color: 'var(--text-muted)' }}>Выполняется действие в браузере...</p>
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
            }}>✓</div>
            <h4 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '8px' }}>Вход выполнен!</h4>
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '20px' }}>
              Сессия чата Kaspi сохранена. Автоответчик снова сможет отвечать клиентам.
            </p>
            <button className="btn btn-primary" style={{ width: '100%' }} onClick={onClose}>
              Закрыть
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
