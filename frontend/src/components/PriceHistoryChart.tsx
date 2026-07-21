import React from 'react';
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts';
import { LineChart as LineChartIcon } from 'lucide-react';

interface HistoryPoint {
  time: string;
  myPrice: number;
  bestCompetitor: number;
}

interface PriceHistoryChartProps {
  data: HistoryPoint[];
  title?: string;
}

export const PriceHistoryChart: React.FC<PriceHistoryChartProps> = ({ data, title = "Динамика цен и конкурентной позиции" }) => {
  return (
    <div className="glass-panel" style={{ padding: '20px', borderRadius: '14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '16px', color: 'var(--text-muted)', fontSize: '13px', fontWeight: 600 }}>
        <LineChartIcon size={16} color="#3b82f6" />
        <span>{title}</span>
      </div>

      <div style={{ width: '100%', height: '220px' }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
            <defs>
              <linearGradient id="colorMyPrice" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.4} />
                <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0} />
              </linearGradient>
              <linearGradient id="colorCompPrice" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#10b981" stopOpacity={0.4} />
                <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
            <XAxis dataKey="time" stroke="#64748b" fontSize={11} tickLine={false} />
            <YAxis stroke="#64748b" fontSize={11} tickLine={false} />
            <Tooltip
              contentStyle={{ background: '#0f172a', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', color: '#fff', fontSize: '12px' }}
            />
            <Area type="monotone" dataKey="myPrice" name="Моя цена (₸)" stroke="#8b5cf6" strokeWidth={2} fillOpacity={1} fill="url(#colorMyPrice)" />
            <Area type="monotone" dataKey="bestCompetitor" name="Цена конкурента (₸)" stroke="#10b981" strokeWidth={2} fillOpacity={1} fill="url(#colorCompPrice)" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};
