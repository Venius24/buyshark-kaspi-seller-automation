import type { ChatbotConfig, ChatbotStatus } from '../../types';

interface ChatbotTabProps {
  chatbotConfig: ChatbotConfig;
  chatbotStatus: ChatbotStatus;
  onSaveChatbotConfig: (cfg: Partial<ChatbotConfig>) => void;
  onUnblockChatbotGroup: (groupId: string) => void;
}

export function ChatbotTab({ chatbotConfig, chatbotStatus, onSaveChatbotConfig, onUnblockChatbotGroup }: ChatbotTabProps) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
      {/* Top Header Card */}
      <div className="glass-card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h2 style={{ fontSize: '18px', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '8px', color: '#fff' }}>
            🤖 Kaspi ИИ-Автоответчик (DeepSeek + Telegram)
          </h2>
          <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>
            Автоматические умные ответы клиентам Kaspi, курьерские шаблоны логистики и эскалация менеджеру через Telegram.
          </p>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Статус автоответчика:</span>
            <span className={`badge ${chatbotStatus.enabled ? 'badge-success' : 'badge-warning'}`}>
              {chatbotStatus.enabled ? '🟢 Автоответчик Активен' : '⏸️ Приостановлен'}
            </span>
          </div>

          <button
            className="btn"
            style={{
              background: chatbotStatus.enabled ? 'linear-gradient(135deg, #ef4444, #dc2626)' : 'linear-gradient(135deg, #10b981, #059669)',
              color: '#fff',
              padding: '10px 20px',
              fontSize: '13px',
              fontWeight: 600,
              borderRadius: '8px'
            }}
            onClick={() => onSaveChatbotConfig({ enabled: !chatbotStatus.enabled })}
          >
            {chatbotStatus.enabled ? '⏸️ Приостановить бота' : '▶️ Включить автоответчик'}
          </button>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '20px' }}>
        {/* 📢 Настройки рассылки Telegram Admin IDs */}
        <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h3 style={{ fontSize: '14px', color: '#fff', fontWeight: 600 }}>📢 Telegram Админы для рассылки</h3>
            <span className="badge badge-success">Telegram CRM</span>
          </div>

          <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            Укажите Telegram ID администраторов, которые будут получать алерты при вызове менеджера и сбоях сессии.
          </p>

          <form onSubmit={(e) => {
            e.preventDefault();
            const adminIdsRaw = (document.getElementById('admin-ids-input') as HTMLInputElement).value;
            const adminIds = adminIdsRaw.split(',').map(s => s.trim()).filter(Boolean);

            onSaveChatbotConfig({
              admin_ids: adminIds
            });
          }} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <div>
              <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>
                Telegram Admin IDs (через запятую)
              </label>
              <input
                type="text"
                id="admin-ids-input"
                className="form-input"
                defaultValue={(chatbotConfig.admin_ids || []).join(', ')}
                placeholder="7119137844, 8661441552"
              />
            </div>

            <button type="submit" className="btn btn-primary" style={{ padding: '10px', marginTop: '8px' }}>
              💾 Сохранить параметры рассылки
            </button>
          </form>
        </div>

        {/* 🔒 Передача менеджеру (Human Override) */}
        <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h3 style={{ fontSize: '14px', color: '#fff', fontWeight: 600 }}>🔒 Заблокированные чаты (Вызов менеджера)</h3>
            <span className="badge badge-warning">{chatbotStatus.blocked_chats_count} чатов</span>
          </div>

          <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            Диалоги, в которых покупатель попросил менеджера/возврат/брак. ИИ приостановлен по этим чатам до разблокировки.
          </p>

          {chatbotStatus.blocked_chats.length === 0 ? (
            <div style={{ padding: '24px 0', textAlign: 'center', color: '#888', fontSize: '13px' }}>
              🎉 Нет заблокированных диалогов. ИИ отвечает на все входящие!
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', maxHeight: '320px', overflowY: 'auto' }}>
              {chatbotStatus.blocked_chats.map((item) => (
                <div key={item.group_id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'rgba(255,255,255,0.03)', padding: '10px 14px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.06)' }}>
                  <div>
                    <div style={{ fontSize: '13px', fontWeight: 600, color: '#fff' }}>Чат #{item.group_id}</div>
                    <div style={{ fontSize: '11px', color: 'var(--warning)', marginTop: '2px' }}>Причина: {item.reason}</div>
                  </div>

                  <button
                    className="btn btn-secondary"
                    style={{ padding: '6px 12px', fontSize: '11px' }}
                    onClick={() => onUnblockChatbotGroup(item.group_id)}
                  >
                    ✅ Разблокировать для ИИ
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
