import React, { useState, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';
import { useActiveStoreId } from '../storeContext';
import { HelpHint } from './shared/HelpHint';
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

const DEFAULT_WEBHOOK_CONFIG: WebhookConfig = { url: '', secret: '', enabled: true };

const fetchKeys = async (): Promise<ApiKeyItem[]> => {
  const res = await apiFetch(`${API_URL}/api/integrations/keys`);
  const data = await res.json();
  return data.success ? (data.keys || []) : [];
};

const fetchWebhookConfig = async (): Promise<WebhookConfig> => {
  const res = await apiFetch(`${API_URL}/api/integrations/webhook`);
  const data = await res.json();
  if (data.success && data.config) {
    return {
      url: data.config.url || '',
      secret: data.config.secret || '',
      secret_masked: data.config.secret_masked || '',
      enabled: data.config.enabled ?? true
    };
  }
  return DEFAULT_WEBHOOK_CONFIG;
};

export const IntegrationsPublicApi: React.FC = () => {
  const queryClient = useQueryClient();
  const activeStoreId = useActiveStoreId();
  const keysQueryKey = ['integration-keys', activeStoreId];
  const webhookQueryKey = ['integration-webhook', activeStoreId];

  // react-query вместо ручного fetch-on-mount — переживает переключение
  // вкладок и перезагрузку страницы через персистентный кэш (queryClient.ts).
  const { data: keys = [] } = useQuery({ queryKey: keysQueryKey, queryFn: fetchKeys });
  const { data: fetchedWebhookConfig } = useQuery({
    queryKey: webhookQueryKey,
    queryFn: fetchWebhookConfig,
  });

  // Локальный editable-драфт, отдельно от query — иначе бы затирал то, что
  // пользователь печатает в форме, при любом фоновом рефетче.
  const [webhookConfig, setWebhookConfig] = useState<WebhookConfig>(DEFAULT_WEBHOOK_CONFIG);
  useEffect(() => {
    if (fetchedWebhookConfig) setWebhookConfig(fetchedWebhookConfig);
  }, [fetchedWebhookConfig]);

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

  const handleCreateKey = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newKeyName.trim()) {
      showToast('error', 'Укажите название API-ключа.');
      return;
    }

    try {
      const res = await apiFetch(`${API_URL}/api/integrations/keys`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newKeyName.trim() })
      });
      const data = await res.json();
      if (data.success) {
        setCreatedSecret(data.api_secret);
        setNewKeyName('');
        queryClient.invalidateQueries({ queryKey: keysQueryKey });
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
      const res = await apiFetch(`${API_URL}/api/integrations/keys/${keyId}`, { method: 'DELETE' });
      const data = await res.json();
      if (data.success) {
        showToast('success', 'API-ключ отозван.');
        queryClient.invalidateQueries({ queryKey: keysQueryKey });
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
      const res = await apiFetch(`${API_URL}/api/integrations/webhook`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(webhookConfig)
      });
      const data = await res.json();
      if (data.success) {
        showToast('success', 'Настройки Webhook сохранены.');
        queryClient.invalidateQueries({ queryKey: webhookQueryKey });
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
      const res = await apiFetch(`${API_URL}/api/integrations/webhook/test`, { method: 'POST' });
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
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px', color: 'var(--text-color)' }}>

      {/* Toast Alert */}
      {toast && (
        <div style={{
          position: 'fixed',
          bottom: '24px',
          right: '24px',
          zIndex: 9999,
          padding: '12px 20px',
          borderRadius: '10px',
          background: toast.type === 'success' ? 'var(--success)' : toast.type === 'error' ? 'var(--error)' : 'var(--primary)',
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
            <Code size={26} color="var(--primary)" />
            Раздел для разработчиков (API & Webhooks)
            <HelpHint text="X-API-Key даёт вашим внешним системам (1С, МойСклад, ERP) доступ к данным кабинета через REST API. Webhooks — наоборот, сервис сам присылает уведомления на ваш URL при событиях (новый заказ, изменение цены и т.д.)." />
          </h2>
          <p style={{ margin: '4px 0 0 0', fontSize: '13px', color: 'var(--text-muted)' }}>
            Управление ключами доступа (X-API-Key), входящими запросами и исходящими Webhooks для интеграции 1С, МойСклад и внешней ERP
          </p>
        </div>


        <a
          href={`${API_URL}/docs`}
          target="_blank"
          rel="noopener noreferrer"
          className="bs-tag"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '8px',
            padding: '10px 18px',
            background: 'var(--primary-glow)',
            border: '1px solid rgba(44, 78, 245, 0.4)',
            color: '#8fa2ff',
            fontSize: '13px',
            textDecoration: 'none',
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
        <div className="glass-card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
            <h3 style={{ fontSize: '16px', fontWeight: 600, margin: 0, color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Key size={20} color="var(--primary-hover)" />
              Публичные API-Ключи (X-API-Key)
            </h3>
            <button
              type="button"
              onClick={() => setIsKeyModalOpen(true)}
              className="btn btn-primary"
              style={{ padding: '8px 14px', fontSize: '13px', display: 'flex', alignItems: 'center', gap: '6px' }}
            >
              <Plus size={16} />
              Сгенерировать ключ
            </button>
          </div>

          <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '20px' }}>
            Используйте API-ключ в заголовке <code style={{ color: 'var(--primary-hover)', background: 'var(--input-bg)', padding: '2px 6px', borderRadius: '4px' }}>X-API-Key</code> при вызове <code style={{ color: 'var(--secondary)' }}>POST /api/v1/public/stocks/update</code> из 1С.
          </p>

          {/* Key List */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {keys.length === 0 ? (
              <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-muted)', background: 'rgba(255,255,255,0.03)', borderRadius: '10px' }}>
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
                    background: 'var(--input-bg)',
                    border: '1px solid var(--input-border)'
                  }}
                >
                  <div>
                    <div style={{ fontSize: '13px', fontWeight: 600, color: '#f8fafc' }}>{k.name}</div>
                    <div style={{ fontSize: '11px', color: 'var(--primary-hover)', fontFamily: 'monospace', marginTop: '2px' }}>
                      {k.prefix}
                    </div>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px', display: 'flex', alignItems: 'center', gap: '6px' }}>
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
                      background: 'rgba(226, 84, 95, 0.12)',
                      border: '1px solid rgba(226, 84, 95, 0.3)',
                      color: '#f3a2a8',
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
        <div className="glass-card">
          <h3 style={{ fontSize: '16px', fontWeight: 600, margin: '0 0 16px 0', color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Webhook size={20} color="var(--secondary)" />
            Исходящие Webhooks (SaaS → 1С/ERP)
          </h3>

          <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '20px' }}>
            SaaS отправляет HTTP POST запросы на ваш сервер при заказах на Kaspi (<code style={{ color: 'var(--secondary)' }}>order.created</code>) или списании остатков.
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
                className="form-input"
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
                className="form-input"
              />
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '8px' }}>
              <button
                type="button"
                onClick={handleTestWebhook}
                disabled={testingWebhook || !webhookConfig.url}
                className="btn btn-secondary"
                style={{ padding: '10px 16px', fontSize: '13px', display: 'flex', alignItems: 'center', gap: '8px' }}
              >
                <Send size={15} />
                {testingWebhook ? 'Отправка...' : 'Отправить тест 1С'}
              </button>

              <button
                type="submit"
                disabled={savingWebhook}
                className="btn btn-primary"
                style={{ padding: '10px 20px', fontSize: '13px' }}
              >
                {savingWebhook ? 'Сохранение...' : 'Сохранить настройки'}
              </button>
            </div>
          </form>
        </div>

      </div>

      {/* Generate API Key Modal */}
      {isKeyModalOpen && (
        <div className="modal-overlay">
          <div className="glass-card" style={{ width: '100%', maxWidth: '480px' }}>

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
                    className="form-input"
                  />
                </div>

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px', marginTop: '12px' }}>
                  <button
                    type="button"
                    onClick={() => setIsKeyModalOpen(false)}
                    className="btn btn-secondary"
                    style={{ padding: '10px 16px' }}
                  >
                    Отмена
                  </button>
                  <button
                    type="submit"
                    className="btn btn-primary"
                    style={{ padding: '10px 20px' }}
                  >
                    Сгенерировать
                  </button>
                </div>
              </form>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <div style={{ padding: '12px', background: 'rgba(224, 163, 62, 0.15)', border: '1px solid rgba(224, 163, 62, 0.3)', borderRadius: '8px', color: 'var(--warning)', fontSize: '12px' }}>
                  ⚠️ <strong>Внимание!</strong> Скопируйте этот секрет прямо сейчас. Он сохраняется в зашифрованном виде (SHA-256) и больше никогда не будет показан!
                </div>

                <div>
                  <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '4px' }}>Секретный API Key:</label>
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <input
                      type="text"
                      readOnly
                      value={createdSecret}
                      className="form-input"
                      style={{
                        flexGrow: 1,
                        border: '1px solid var(--secondary)',
                        color: 'var(--secondary)',
                        fontFamily: 'monospace',
                      }}
                    />
                    <button
                      onClick={() => copyToClipboard(createdSecret)}
                      className="btn"
                      style={{
                        padding: '10px 14px',
                        background: copied ? 'var(--success)' : 'var(--primary)',
                        color: '#fff',
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
                  className="btn btn-secondary"
                  style={{ width: '100%', padding: '10px', marginTop: '12px' }}
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
