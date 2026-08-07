import { apiFetch, API_URL } from '../../api';
import { TelegramLinkPanel } from '../TelegramLinkPanel';
import { NotificationSettingsPanel } from '../NotificationSettingsPanel';

interface SettingsTabProps {
  mcHasSession: boolean;
  addLog: (message: string) => void;
  onOpenMcAuth: () => void;
  onCheckMcStatus: () => Promise<boolean>;
}

export function SettingsTab({ mcHasSession, addLog, onOpenMcAuth, onCheckMcStatus }: SettingsTabProps) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px', maxWidth: '800px' }}>
      <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        <h2>Статус авторизации и сессии</h2>

        <div className="status-widget">
          <div>
            <h3 style={{ fontSize: '15px', fontWeight: 600 }}>Kaspi Merchant API (REST)</h3>
            <p style={{ marginTop: '4px', fontSize: '12px' }}>Токен X-Auth-Token проверен и сохранен.</p>
            <div style={{ display: 'flex', alignItems: 'center', marginTop: '8px' }}>
              <span className="glow-dot glow-dot-success"></span>
              <span className="badge badge-success">Сессия активна</span>
            </div>
          </div>
          <button className="btn btn-secondary" onClick={() => {
            addLog('Проверка состояния сессии REST API...');
            apiFetch(`${API_URL}/api/verify-session`, { method: 'POST' })
              .then(r => r.json())
              .then(d => addLog(d.valid ? 'Сессия REST API активна ✓' : 'Сессия REST API невалидна ✗'))
              .catch(() => addLog('Ошибка проверки сессии REST API'));
          }}>
            🔄 Проверить
          </button>
        </div>

        <div className="status-widget">
          <div>
            <h3 style={{ fontSize: '15px', fontWeight: 600 }}>Kaspi MC (сессионные куки браузера)</h3>
            <p style={{ marginTop: '4px', fontSize: '12px' }}>Используется для получения цен конкурентов и управления демпингом.</p>
            <div style={{ display: 'flex', alignItems: 'center', marginTop: '8px' }}>
              <span className={`glow-dot ${mcHasSession ? 'glow-dot-success' : 'glow-dot-warning'}`}></span>
              {mcHasSession ? (
                <span className="badge badge-success">Сессионные куки активны</span>
              ) : (
                <span className="badge badge-warning">Требуется повторный вход</span>
              )}
            </div>
          </div>
          <div style={{ display: 'flex', gap: '10px' }}>
            <button className="btn btn-primary" onClick={onOpenMcAuth}>
              🔑 {mcHasSession ? 'Авторизоваться заново' : 'Войти в Kaspi MC'}
            </button>
            <button className="btn btn-secondary" onClick={() => {
              addLog('Проверка кук Kaspi MC...');
              onCheckMcStatus().then(() => addLog('Проверка завершена.'));
            }}>
              🔄 Проверить
            </button>
          </div>
        </div>
      </div>

      <TelegramLinkPanel />
      <NotificationSettingsPanel />
    </div>
  );
}
