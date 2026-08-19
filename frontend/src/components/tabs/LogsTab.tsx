import { useTranslation } from 'react-i18next';
import { LiveTerminalStream } from '../LiveTerminalStream';

interface LogsTabProps {
  logs: string[];
  isConnected: boolean;
  onClear: () => void;
}

export function LogsTab({ logs, isConnected, onClear }: LogsTabProps) {
  const { t } = useTranslation('tab_logs');
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <LiveTerminalStream
        logs={logs}
        isConnected={isConnected}
      />
      <div className="glass-card" style={{ display: 'flex', flexDirection: 'column' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
          <h2>{t('detailed_log')}</h2>
          <button
            className="btn btn-secondary"
            style={{ padding: '4px 8px', fontSize: '11px', borderRadius: '6px' }}
            onClick={onClear}
          >
            {t('btn_clear')}
          </button>
        </div>
        <div className="console-box" style={{ height: '420px' }}>
          {logs.map((log, index) => {
            const timeMatch = log.match(/^\[([0-9:]+)\]\s*(.*)$/);
            const isError = log.includes('Ошибка') || log.includes('✗');
            const isWarning = log.includes('Предупреждение') || log.includes('WARN');
            const isSuccess = log.includes('успешно') || log.includes('✓') || log.includes('активна') || log.includes('обработан');

            let textColor = '#e5e7eb';
            if (isError) textColor = 'var(--error)';
            else if (isWarning) textColor = 'var(--warning)';
            else if (isSuccess) textColor = '#34d399';

            if (timeMatch) {
              return (
                <div key={index} className="console-line" style={{ color: textColor }}>
                  <span className="console-timestamp">[{timeMatch[1]}]</span>
                  <span>{timeMatch[2]}</span>
                </div>
              );
            }
            return (
              <div key={index} className="console-line" style={{ color: textColor }}>
                {log}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
