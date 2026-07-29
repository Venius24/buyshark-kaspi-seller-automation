import { useState } from 'react';
import { apiFetch, API_URL } from '../api';

type McAuthStep = 'idle' | 'credentials' | 'loading' | 'success';

export function useMcAuth(addLog: (message: string) => void, onLoginSuccess: () => void) {
  const [emailInput, setEmailInput] = useState('');
  const [passwordInput, setPasswordInput] = useState('');
  const [mcAuthStep, setMcAuthStep] = useState<McAuthStep>('idle');
  const [mcAuthError, setMcAuthError] = useState('');

  const openModal = () => setMcAuthStep('credentials');
  const closeModal = () => setMcAuthStep('idle');

  const submitLogin = async () => {
    setMcAuthError('');
    setMcAuthStep('loading');
    addLog('Запуск авторизации Kaspi MC для ' + emailInput);
    try {
      const res = await apiFetch(`${API_URL}/api/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: emailInput, password: passwordInput })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        setMcAuthStep('success');
        onLoginSuccess();
        addLog('Авторизация Kaspi MC успешна! Сессионные куки сохранены.');
      } else {
        setMcAuthError(data.detail || 'Не удалось войти. Проверьте email и пароль.');
        setMcAuthStep('credentials');
      }
    } catch {
      setMcAuthError('Ошибка сети.');
      setMcAuthStep('credentials');
    }
  };

  return {
    emailInput,
    setEmailInput,
    passwordInput,
    setPasswordInput,
    mcAuthStep,
    mcAuthError,
    openModal,
    closeModal,
    submitLogin,
  };
}
