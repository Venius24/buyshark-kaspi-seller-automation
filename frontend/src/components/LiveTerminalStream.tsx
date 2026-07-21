import React, { useEffect, useRef } from 'react';
import { Terminal, Wifi, WifiOff } from 'lucide-react';

interface LiveTerminalStreamProps {
  logs: string[];
  isConnected: boolean;
}

export const LiveTerminalStream: React.FC<LiveTerminalStreamProps> = ({ logs, isConnected }) => {
  const terminalEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    terminalEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  return (
    <div className="glass-panel" style={{ padding: '16px', borderRadius: '14px', border: '1px solid rgba(255, 255, 255, 0.08)', background: '#0a0d14' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px', paddingBottom: '8px', borderBottom: '1px solid rgba(255, 255, 255, 0.06)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#a7f3d0', fontSize: '13px', fontFamily: 'monospace', fontWeight: 600 }}>
          <Terminal size={16} color="#10b981" />
          <span>LIVE TELEMETRY STREAM</span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: isConnected ? '#10b981' : '#ef4444' }}>
          {isConnected ? <Wifi size={12} /> : <WifiOff size={12} />}
          <span>{isConnected ? 'WebSocket Active' : 'Disconnected'}</span>
        </div>
      </div>

      <div style={{
        height: '180px',
        overflowY: 'auto',
        fontFamily: 'Consolas, Monaco, "Andale Mono", "Ubuntu Mono", monospace',
        fontSize: '12px',
        color: '#e2e8f0',
        lineHeight: 1.6,
        paddingRight: '6px'
      }}>
        {logs.length === 0 ? (
          <div style={{ color: 'var(--text-muted)', fontStyle: 'italic', padding: '12px 0' }}>
            ⏳ Ожидание событий демпинга... Телеметрия подключена.
          </div>
        ) : (
          logs.map((log, idx) => (
            <div key={idx} style={{ marginBottom: '4px', wordBreak: 'break-word' }}>
              <span style={{ color: '#64748b', marginRight: '6px' }}>&gt;</span>
              {log}
            </div>
          ))
        )}
        <div ref={terminalEndRef} />
      </div>
    </div>
  );
};
