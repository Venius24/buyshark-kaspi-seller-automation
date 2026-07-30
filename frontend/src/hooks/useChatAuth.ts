import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';

type ChatAuthStep = 'idle' | 'phone' | 'code' | 'loading' | 'success';

export function useChatAuth() {
  const queryClient = useQueryClient();
  const [phoneInput, setPhoneInput] = useState('');
  const [codeInput, setCodeInput] = useState('');
  const [chatAuthStep, setChatAuthStep] = useState<ChatAuthStep>('idle');
  const [chatAuthError, setChatAuthError] = useState('');

  const openModal = () => setChatAuthStep('phone');
  const closeModal = () => setChatAuthStep('idle');

  const submitPhone = async () => {
    setChatAuthError('');
    setChatAuthStep('loading');
    try {
      const res = await apiFetch(`${API_URL}/api/chatbot/mobile-login/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: phoneInput }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        setChatAuthStep('code');
      } else {
        setChatAuthError(data.detail || 'Не удалось отправить SMS. Проверьте номер телефона.');
        setChatAuthStep('phone');
      }
    } catch {
      setChatAuthError('Ошибка сети.');
      setChatAuthStep('phone');
    }
  };

  const submitCode = async () => {
    setChatAuthError('');
    setChatAuthStep('loading');
    try {
      const res = await apiFetch(`${API_URL}/api/chatbot/mobile-login/submit-code`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: codeInput }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        setChatAuthStep('success');
        queryClient.invalidateQueries({ queryKey: ['chatbot-status'] });
      } else {
        setChatAuthError(data.detail || 'Неверный код из SMS.');
        setChatAuthStep('code');
      }
    } catch {
      setChatAuthError('Ошибка сети.');
      setChatAuthStep('code');
    }
  };

  return {
    phoneInput,
    setPhoneInput,
    codeInput,
    setCodeInput,
    chatAuthStep,
    chatAuthError,
    openModal,
    closeModal,
    submitPhone,
    submitCode,
  };
}
