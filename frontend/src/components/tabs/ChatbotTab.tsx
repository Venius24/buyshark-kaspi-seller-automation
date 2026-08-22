import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useChatAuth } from '../../hooks/useChatAuth';
import { ChatSmsAuthModal } from '../ChatSmsAuthModal';
import { BotProfileCard } from './BotProfileCard';
import { OutboundTestCard } from './OutboundTestCard';
import { OutboundStatsCard } from './OutboundStatsCard';
import { fetchChatbotHasSession } from '../../queries';
import { useActiveStoreId } from '../../storeContext';
import { HelpHint } from '../shared/HelpHint';
import type { ChatbotConfig, ChatbotStatus } from '../../types';

interface ChatbotTabProps {
  chatbotStatus: ChatbotStatus;
  onSaveChatbotConfig: (cfg: Partial<ChatbotConfig>) => void;
  onUnblockChatbotGroup: (groupId: string) => void;
}

export function ChatbotTab({ chatbotStatus, onSaveChatbotConfig, onUnblockChatbotGroup }: ChatbotTabProps) {
  const { t } = useTranslation('tab_chatbot');
  const {
    phoneInput, setPhoneInput,
    codeInput, setCodeInput,
    chatAuthStep, chatAuthError,
    openModal: openChatAuthModal,
    closeModal: closeChatAuthModal,
    submitPhone: submitChatAuthPhone,
    submitCode: submitChatAuthCode,
  } = useChatAuth();

  // Отдельный статус SMS-сессии чата (не путать с chatbotStatus.enabled/
  // blocked_chats выше — та часть по-прежнему приходит из App.tsx). Живой
  // поллинг, чтобы бейдж не зависал на устаревшем значении.
  const activeStoreId = useActiveStoreId();
  const { data: hasChatSession = false } = useQuery({
    queryKey: ['chatbot-status', activeStoreId],
    queryFn: fetchChatbotHasSession,
    refetchInterval: 30_000,
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
      <ChatSmsAuthModal
        chatAuthStep={chatAuthStep}
        chatAuthError={chatAuthError}
        phoneInput={phoneInput}
        onPhoneChange={setPhoneInput}
        codeInput={codeInput}
        onCodeChange={setCodeInput}
        onClose={closeChatAuthModal}
        onSubmitPhone={submitChatAuthPhone}
        onSubmitCode={submitChatAuthCode}
      />

      {/* Top Header Card */}
      <div className="glass-card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
        <div>
          <h2 style={{ fontSize: '18px', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '8px', color: '#fff' }}>
            {t('title')}
            <HelpHint text="ИИ-бот отвечает покупателям в чатах Kaspi вместо вас — по своей базе знаний и настройкам ниже. Нужна отдельная SMS-сессия чата Kaspi (не путать с обычным входом в кабинет продавца) — подключается кнопкой рядом." />
          </h2>
          <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>
            {t('description')}
          </p>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '16px', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>{t('chat_session_label')}</span>
            <span className={`badge ${hasChatSession ? 'badge-success' : 'badge-warning'}`}>
              {hasChatSession ? t('chat_session_connected') : t('chat_session_required')}
            </span>
          </div>

          <button
            className="btn btn-secondary"
            style={{ padding: '10px 20px', fontSize: '13px', fontWeight: 600, borderRadius: '8px' }}
            onClick={openChatAuthModal}
          >
            {hasChatSession ? t('btn_chat_login_new') : t('btn_chat_login')}
          </button>

          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>{t('chatbot_status_label')}</span>
            <span className={`badge ${chatbotStatus.enabled ? 'badge-success' : 'badge-warning'}`}>
              {chatbotStatus.enabled ? t('chatbot_enabled') : t('chatbot_paused')}
            </span>
          </div>

          <button
            className="btn"
            style={{
              background: chatbotStatus.enabled ? 'linear-gradient(100deg, var(--error), #c23d47)' : 'linear-gradient(100deg, var(--secondary), var(--secondary-hover))',
              color: '#fff',
              padding: '10px 20px',
              fontSize: '13px',
              fontWeight: 600,
              borderRadius: '8px'
            }}
            onClick={() => onSaveChatbotConfig({ enabled: !chatbotStatus.enabled })}
          >
            {chatbotStatus.enabled ? t('btn_pause') : t('btn_enable')}
          </button>
        </div>
      </div>

      <BotProfileCard />

      <OutboundStatsCard />

      <OutboundTestCard />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '20px' }}>
        {/* 🔒 Передача менеджеру (Human Override) */}
        <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h3 style={{ fontSize: '14px', color: '#fff', fontWeight: 600 }}>{t('blocked_title')}</h3>
            <span className="badge badge-warning">{t('blocked_count_badge').replace('%count%', String(chatbotStatus.blocked_chats_count))}</span>
          </div>

          <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            {t('blocked_description')}
          </p>

          {chatbotStatus.blocked_chats.length === 0 ? (
            <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
              {t('no_blocked_chats')}
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', maxHeight: '320px', overflowY: 'auto' }}>
              {chatbotStatus.blocked_chats.map((item) => (
                <div key={item.group_id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'rgba(255,255,255,0.03)', padding: '10px 14px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.06)' }}>
                  <div>
                    <div style={{ fontSize: '13px', fontWeight: 600, color: '#fff' }}>{t('blocked_chat_title').replace('%id%', item.group_id)}</div>
                    <div style={{ fontSize: '11px', color: 'var(--warning)', marginTop: '2px' }}>{t('blocked_chat_reason')} {item.reason}</div>
                  </div>

                  <button
                    className="btn btn-secondary"
                    style={{ padding: '6px 12px', fontSize: '11px' }}
                    onClick={() => onUnblockChatbotGroup(item.group_id)}
                  >
                    {t('btn_unblock')}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
