import React, { useState, useEffect } from 'react';
import {
  Key,
  Webhook,
  Plus,
  Trash2,
  Copy,
  Check,
  Send,
  ExternalLink,
  ShieldCheck,
  AlertCircle,
  FileCode,
  Code,
  Clock
} from 'lucide-react';


interface ApiKeyItem {
  key_id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used?: string | null;
  active: boolean;
}

interface WebhookConfig {
  url: string;
  secret: string;
  secret_masked?: string;
  enabled: boolean;
}

const API_URL = import.meta.env.VITE_API_URL || '';

export const IntegrationsPublicApi: React.FC = () => {
  const [keys, setKeys] = useState<ApiKeyItem[]>([]);
  const [webhookConfig, setWebhookConfig] = useState<WebhookConfig>({
    url: '',
    secret: '',
    enabled: true
  });

  const [newKeyName, setNewKeyName] = useState<string>('');
  const [isKeyModalOpen, setIsKeyModalOpen] = useState<boolean>(false);
  const [createdSecret, setCreatedSecret] = useState<string | null>(null);
  const [copied, setCopied] = useState<boolean>(false);


  const [savingWebhook, setSavingWebhook] = useState<boolean>(false);
  const [testingWebhook, setTestingWebhook] = useState<boolean>(false);
  const [toast, setToast] = useState<{ type: 'success' | 'error' | 'info'; text: string } | null>(null);

  const showToast = (type: 'success' | 'error' | 'info', text: string) => {
    setToast({ type, text });
    setTimeout(() => setToast(null), 4000);
  };

  const fetchKeys = async () => {
    try {
      const res = await fetch(`${API_URL}/api/integrations/keys`);
      const data = await res.json();
      if (data.success) {
        setKeys(data.keys || []);
      }
    } catch (e) {
      console.error('Error fetching API keys:', e);
    }
  };

  const fetchWebhookConfig = async () => {
    try {
      const res = await fetch(`${API_URL}/api/integrations/webhook`);
      const data = await res.json();
      if (data.success && data.config) {
        setWebhookConfig({
          url: data.config.url || '',
          secret: data.config.secret || '',
          secret_masked: data.config.secret_masked || '',
          enabled: data.config.enabled ?? true
        });
      }
    } catch (e) {
      console.error('Error fetching webhook config:', e);
    }
  };

  const loadData = async () => {
    await Promise.all([fetchKeys(), fetchWebhookConfig()]);
  };


  useEffect(() => {
    loadData();
  }, []);

  const handleCreateKey = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newKeyName.trim()) {
      showToast('error', 'Укажите название API-ключа.');
      return;
    }

    try {
      const res = await fetch(`${API_URL}/api/integrations/keys`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newKeyName.trim() })
      });
      const data = await res.json();
      if (data.success) {
        setCreatedSecret(data.api_secret);
        setNewKeyName('');
        fetchKeys();
        showToast('success', 'API-ключ сгенерирован.');
      } else {
        showToast('error', data.detail || 'Ошибка создания API-ключа.');
      }
    } catch (e) {
      showToast('error', 'Ошибка сети при создании ключа.');
    }
  };

  const handleRevokeKey = async (keyId: string, name: string) => {
    if (!window.confirm(`Вы уверены, что хотите отозвать API-ключ "${name}"?`)) return;
    try {
      const res = await fetch(`${API_URL}/api/integrations/keys/${keyId}`, { method: 'DELETE' });
      const data = await res.json();
      if (data.success) {
        showToast('success', 'API-ключ отозван.');
        fetchKeys();
      } else {
        showToast('error', data.detail || 'Не удалось отозвать ключ.');
      }
    } catch (e) {
      showToast('error', 'Ошибка сервера.');
    }
  };

  const handleSaveWebhook = async (e: React.FormEvent) => {
    e.preventDefault();
    setSavingWebhook(true);
    try {
      const res = await fetch(`${API_URL}/api/integrations/webhook`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(webhookConfig)
      });
      const data = await res.json();
      if (data.success) {
        showToast('success', 'Настройки Webhook сохранены.');
        fetchWebhookConfig();
      } else {
        showToast('error', data.detail || 'Не удалось сохранить Webhook.');
      }
    } catch (e) {
      showToast('error', 'Ошибка сети.');
    } finally {
      setSavingWebhook(false);
    }
  };

  const handleTestWebhook = async () => {
    setTestingWebhook(true);
    try {
      const res = await fetch(`${API_URL}/api/integrations/webhook/test`, { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        showToast('info', data.message || 'Тестовый Webhook отправлен в 1С!');
      } else {
        showToast('error', data.detail || 'Ошибка отправки тестового Webhook.');
      }
    } catch (e) {
      showToast('error', 'Ошибка сети при вызове Webhook.');
    } finally {
      setTestingWebhook(false);
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2500);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px', color: '#f1f5f9' }}>

      {/* Toast Alert */}
      {toast && (
        <div style={{
          position: 'fixed',
          bottom: '24px',
          right: '24px',
          zIndex: 9999,
          padding: '12px 20px',
          borderRadius: '10px',
          background: toast.type === 'success' ? '#059669' : toast.type === 'error' ? '#dc2626' : '#2563eb',
          color: '#ffffff',
          fontWeight: 600,
          boxShadow: '0 10px 25px rgba(0,0,0,0.3)',
          display: 'flex',
          alignItems: 'center',
          gap: '10px'
        }}>
          {toast.type === 'success' ? <ShieldCheck size={18} /> : <AlertCircle size={18} />}
          {toast.text}
        </div>
      )}

      {/* Header & Title */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h2 style={{ fontSize: '22px', fontWeight: 700, margin: 0, color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '10px' }}>
            <Code size={26} color="#6366f1" />
            Раздел для разработчиков (API & Webhooks)
          </h2>
          <p style={{ margin: '4px 0 0 0', fontSize: '13px', color: '#94a3b8' }}>
            Управление ключами доступа (X-API-Key), входящими запросами и исходящими Webhooks для интеграции 1С, МойСклад и внешней ERP
          </p>
        </div>


        <a
          href={`${API_URL}/docs`}
          target="_blank"
          rel="noopener noreferrer"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '8px',
            padding: '10px 18px',
            borderRadius: '10px',
            background: 'rgba(99, 102, 241, 0.15)',
            border: '1px solid rgba(99, 102, 241, 0.4)',
            color: '#818cf8',
            fontSize: '13px',
            fontWeight: 600,
            textDecoration: 'none',
            transition: 'all 0.2s'
          }}
        >
          <FileCode size={16} />
          OpenAPI / Swagger Docs
          <ExternalLink size={14} />
        </a>
      </div>

      {/* Grid: API Keys + Webhooks */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))', gap: '24px' }}>

        {/* API Keys Panel */}
        <div style={{
          background: 'rgba(30, 41, 59, 0.7)',
          border: '1px solid rgba(51, 65, 85, 0.6)',
          borderRadius: '16px',
          padding: '24px',
          backdropFilter: 'blur(10px)'
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
            <h3 style={{ fontSize: '16px', fontWeight: 600, margin: 0, color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Key size={20} color="#818cf8" />
              Публичные API-Ключи (X-API-Key)
            </h3>
            <button
              type="button"
              onClick={() => setIsKeyModalOpen(true)}
              style={{
                padding: '8px 14px',
                borderRadius: '8px',
                background: 'linear-gradient(135deg, #4f46e5 0%, #6366f1 100%)',
                border: 'none',
                color: '#fff',
                fontSize: '13px',
                fontWeight: 600,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '6px'
              }}
            >
              <Plus size={16} />
              Сгенерировать ключ
            </button>
          </div>

          <p style={{ fontSize: '12px', color: '#94a3b8', marginBottom: '20px' }}>
            Используйте API-ключ в заголовке <code style={{ color: '#818cf8', background: '#0f172a', padding: '2px 6px', borderRadius: '4px' }}>X-API-Key</code> при вызове <code style={{ color: '#38bdf8' }}>POST /api/v1/public/stocks/update</code> из 1С.
          </p>

          {/* Key List */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {keys.length === 0 ? (
              <div style={{ padding: '24px', textAlign: 'center', color: '#64748b', background: 'rgba(15, 23, 42, 0.4)', borderRadius: '10px' }}>
                Активных API-ключей не найдено. Нажмите «Сгенерировать ключ».
              </div>
            ) : (
              keys.map(k => (
                <div
                  key={k.key_id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '12px 16px',
                    borderRadius: '10px',
                    background: '#0f172a',
                    border: '1px solid #1e293b'
                  }}
                >
                  <div>
                    <div style={{ fontSize: '13px', fontWeight: 600, color: '#f8fafc' }}>{k.name}</div>
                    <div style={{ fontSize: '11px', color: '#818cf8', fontFamily: 'monospace', marginTop: '2px' }}>
                      {k.prefix}
                    </div>
                    <div style={{ fontSize: '11px', color: '#64748b', marginTop: '4px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <Clock size={12} />
                      Создан: {k.created_at} {k.last_used ? `• Посл. вход: ${k.last_used}` : ''}
                    </div>
                  </div>

                  <button
                    onClick={() => handleRevokeKey(k.key_id, k.name)}
                    title="Отозвать ключ"
                    style={{
                      padding: '8px',
                      borderRadius: '8px',
                      background: 'rgba(239, 68, 68, 0.12)',
                      border: '1px solid rgba(239, 68, 68, 0.3)',
                      color: '#f87171',
                      cursor: 'pointer'
                    }}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Webhook Panel */}
        <div style={{
          background: 'rgba(30, 41, 59, 0.7)',
          border: '1px solid rgba(51, 65, 85, 0.6)',
          borderRadius: '16px',
          padding: '24px',
          backdropFilter: 'blur(10px)'
        }}>
          <h3 style={{ fontSize: '16px', fontWeight: 600, margin: '0 0 16px 0', color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Webhook size={20} color="#38bdf8" />
            Исходящие Webhooks (SaaS → 1С/ERP)
          </h3>

          <p style={{ fontSize: '12px', color: '#94a3b8', marginBottom: '20px' }}>
            SaaS отправляет HTTP POST запросы на ваш сервер при заказах на Kaspi (<code style={{ color: '#38bdf8' }}>order.created</code>) или списании остатков.
          </p>

          <form onSubmit={handleSaveWebhook} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <div>
              <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, color: '#cbd5e1', marginBottom: '6px' }}>
                Webhook Endpoint URL (HTTP-сервис 1С / МойСклад)
              </label>
              <input
                type="url"
                value={webhookConfig.url}
                onChange={e => setWebhookConfig({ ...webhookConfig, url: e.target.value })}
                placeholder="https://1c.seller-store.kz/hs/kaspi_sync/webhook"
                style={{
                  width: '100%',
                  padding: '10px 14px',
                  borderRadius: '8px',
                  background: '#0f172a',
                  border: '1px solid #334155',
                  color: '#f8fafc',
                  fontSize: '13px',
                  boxSizing: 'border-box'
                }}
              />
            </div>

            <div>
              <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, color: '#cbd5e1', marginBottom: '6px' }}>
                HMAC Secret Key (для проверки подписи X-Webhook-Signature)
              </label>
              <input
                type="password"
                value={webhookConfig.secret}
                onChange={e => setWebhookConfig({ ...webhookConfig, secret: e.target.value })}
                placeholder="Ввести секретный ключ подписи..."
                style={{
                  width: '100%',
                  padding: '10px 14px',
                  borderRadius: '8px',
                  background: '#0f172a',
                  border: '1px solid #334155',
                  color: '#f8fafc',
                  fontSize: '13px',
                  boxSizing: 'border-box'
                }}
              />
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '8px' }}>
              <button
                type="button"
                onClick={handleTestWebhook}
                disabled={testingWebhook || !webhookConfig.url}
                style={{
                  padding: '10px 16px',
                  borderRadius: '8px',
                  background: 'rgba(56, 189, 248, 0.15)',
                  border: '1px solid rgba(56, 189, 248, 0.4)',
                  color: '#38bdf8',
                  fontSize: '13px',
                  fontWeight: 600,
                  cursor: testingWebhook || !webhookConfig.url ? 'not-allowed' : 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px'
                }}
              >
                <Send size={15} />
                {testingWebhook ? 'Отправка...' : 'Отправить тест 1С'}
              </button>

              <button
                type="submit"
                disabled={savingWebhook}
                style={{
                  padding: '10px 20px',
                  borderRadius: '8px',
                  background: 'linear-gradient(135deg, #059669 0%, #10b981 100%)',
                  border: 'none',
                  color: '#fff',
                  fontSize: '13px',
                  fontWeight: 600,
                  cursor: 'pointer'
                }}
              >
                {savingWebhook ? 'Сохранение...' : 'Сохранить настройки'}
              </button>
            </div>
          </form>
        </div>

      </div>

      {/* Generate API Key Modal */}
      {isKeyModalOpen && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: 'rgba(15, 23, 42, 0.85)',
          backdropFilter: 'blur(8px)',
          zIndex: 9999,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '20px'
        }}>
          <div style={{
            background: '#1e293b',
            border: '1px solid #334155',
            borderRadius: '16px',
            width: '100%',
            maxWidth: '480px',
            padding: '24px',
            boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)'
          }}>

            <h3 style={{ fontSize: '18px', fontWeight: 700, margin: '0 0 16px 0', color: '#f8fafc' }}>
              {createdSecret ? '🔑 API-Ключ успешно создан' : 'Сгенерировать новый API-ключ'}
            </h3>

            {!createdSecret ? (
              <form onSubmit={handleCreateKey} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <div>
                  <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, color: '#cbd5e1', marginBottom: '6px' }}>
                    Название ключа (например, Интеграция 1С:УНФ) *
                  </label>
                  <input
                    type="text"
                    value={newKeyName}
                    onChange={e => setNewKeyName(e.target.value)}
                    placeholder="1С:Управление Торговлей..."
                    required
                    style={{
                      width: '100%',
                      padding: '10px 14px',
                      borderRadius: '8px',
                      background: '#0f172a',
                      border: '1px solid #334155',
                      color: '#f8fafc',
                      fontSize: '13px',
                      boxSizing: 'border-box'
                    }}
                  />
                </div>

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px', marginTop: '12px' }}>
                  <button
                    type="button"
                    onClick={() => setIsKeyModalOpen(false)}
                    style={{ padding: '10px 16px', borderRadius: '8px', background: '#334155', border: 'none', color: '#cbd5e1', cursor: 'pointer' }}
                  >
                    Отмена
                  </button>
                  <button
                    type="submit"
                    style={{ padding: '10px 20px', borderRadius: '8px', background: '#6366f1', border: 'none', color: '#fff', fontWeight: 600, cursor: 'pointer' }}
                  >
                    Сгенерировать
                  </button>
                </div>
              </form>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <div style={{ padding: '12px', background: 'rgba(245, 158, 11, 0.15)', border: '1px solid rgba(245, 158, 11, 0.3)', borderRadius: '8px', color: '#fbbf24', fontSize: '12px' }}>
                  ⚠️ <strong>Внимание!</strong> Скопируйте этот секрет прямо сейчас. Он сохраняется в зашифрованном виде (SHA-256) и больше никогда не будет показан!
                </div>

                <div>
                  <label style={{ display: 'block', fontSize: '12px', color: '#94a3b8', marginBottom: '4px' }}>Секретный API Key:</label>
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <input
                      type="text"
                      readOnly
                      value={createdSecret}
                      style={{
                        flexGrow: 1,
                        padding: '10px',
                        borderRadius: '8px',
                        background: '#0f172a',
                        border: '1px solid #38bdf8',
                        color: '#38bdf8',
                        fontFamily: 'monospace',
                        fontSize: '13px'
                      }}
                    />
                    <button
                      onClick={() => copyToClipboard(createdSecret)}
                      style={{
                        padding: '10px 14px',
                        borderRadius: '8px',
                        background: copied ? '#10b981' : '#4f46e5',
                        border: 'none',
                        color: '#fff',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '6px'
                      }}
                    >
                      {copied ? <Check size={16} /> : <Copy size={16} />}
                      {copied ? 'Скопировано' : 'Копировать'}
                    </button>
                  </div>
                </div>

                <button
                  type="button"
                  onClick={() => {
                    setIsKeyModalOpen(false);
                    setCreatedSecret(null);
                  }}
                  style={{ width: '100%', padding: '10px', borderRadius: '8px', background: '#334155', border: 'none', color: '#fff', fontWeight: 600, cursor: 'pointer', marginTop: '12px' }}
                >
                  Понятно, я сохранил ключ
                </button>
              </div>
            )}

          </div>
        </div>
      )}

    </div>
  );
};
export default IntegrationsPublicApi;
