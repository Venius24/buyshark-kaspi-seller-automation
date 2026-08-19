import { useTranslation } from 'react-i18next';
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
  const { t } = useTranslation('tab_settings');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px', maxWidth: '800px' }}>
      <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        <h2>{t('title')}</h2>

        <div className="status-widget">
          <div>
            <h3 style={{ fontSize: '15px', fontWeight: 600 }}>{t('rest_api_title')}</h3>
            <p style={{ marginTop: '4px', fontSize: '12px' }}>{t('rest_api_description')}</p>
            <div style={{ display: 'flex', alignItems: 'center', marginTop: '8px' }}>
              <span className="glow-dot glow-dot-success"></span>
              <span className="badge badge-success">{t('badge_active')}</span>
            </div>
          </div>
          <button className="btn btn-secondary" onClick={() => {
            addLog(t('log_checking'));
            apiFetch(`${API_URL}/api/verify-session`, { method: 'POST' })
              .then(r => r.json())
              .then(d => addLog(d.valid ? t('log_active') : t('log_invalid')))
              .catch(() => addLog(t('log_error')));
          }}>
            {t('btn_check')}
          </button>
        </div>

        <div className="status-widget">
          <div>
            <h3 style={{ fontSize: '15px', fontWeight: 600 }}>{t('mc_title')}</h3>
            <p style={{ marginTop: '4px', fontSize: '12px' }}>{t('mc_description')}</p>
            <div style={{ display: 'flex', alignItems: 'center', marginTop: '8px' }}>
              <span className={`glow-dot ${mcHasSession ? 'glow-dot-success' : 'glow-dot-warning'}`}></span>
              {mcHasSession ? (
                <span className="badge badge-success">{t('badge_mc_active')}</span>
              ) : (
                <span className="badge badge-warning">{t('badge_mc_required')}</span>
              )}
            </div>
          </div>
          <div style={{ display: 'flex', gap: '10px' }}>
            <button className="btn btn-primary" onClick={onOpenMcAuth}>
              {mcHasSession ? t('btn_mc_login_new') : t('btn_mc_login')}
            </button>
            <button className="btn btn-secondary" onClick={() => {
              addLog(t('log_checking_mc'));
              onCheckMcStatus().then(() => addLog(t('log_check_complete')));
            }}>
              {t('btn_check')}
            </button>
          </div>
        </div>
      </div>

      <TelegramLinkPanel />
      <NotificationSettingsPanel />
    </div>
  );
}
