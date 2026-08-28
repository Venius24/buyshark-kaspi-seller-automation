import React, { useState } from 'react';
import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { GripVertical, EyeOff, Settings } from 'lucide-react';
import type { MergedWidget } from '../../hooks/useDashboardLayout';
import { PERIOD_OPTIONS, type ChartType, type Period } from '../../dashboard/widgetRegistry';

interface DashboardWidgetShellProps {
  widget: MergedWidget;
  editMode: boolean;
  onHide: (id: string) => void;
  onConfigChange: (id: string, config: { chartType?: ChartType; metrics?: string[]; period?: Period }) => void;
  children: React.ReactNode;
}

const CHART_TYPE_LABELS: Record<ChartType, string> = {
  bar: 'Столбцы',
  line: 'Линия',
  area: 'Область',
  pie: 'Круговая',
  donut: 'Кольцевая',
};

export const DashboardWidgetShell: React.FC<DashboardWidgetShellProps> = ({ widget, editMode, onHide, onConfigChange, children }) => {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: widget.id, disabled: !editMode });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    position: 'relative',
    opacity: isDragging ? 0.5 : 1,
    outline: editMode ? '1px dashed rgba(255,255,255,0.15)' : 'none',
    borderRadius: '16px',
  };

  const hasSettings = (widget.supportsChartType?.length || widget.supportsMetrics?.length || widget.supportsPeriod) ?? false;

  return (
    <div ref={setNodeRef} style={style}>
      {editMode && (
        <div
          style={{
            position: 'absolute',
            top: '8px',
            right: '8px',
            zIndex: 5,
            display: 'flex',
            gap: '6px',
            background: 'rgba(10,10,16,0.85)',
            borderRadius: '10px',
            padding: '4px',
          }}
        >
          <button
            {...attributes}
            {...listeners}
            className="btn btn-secondary"
            style={{ padding: '4px 8px', cursor: 'grab' }}
            title="Перетащить"
          >
            <GripVertical size={14} />
          </button>
          {hasSettings ? (
            <button
              className="btn btn-secondary"
              style={{ padding: '4px 8px' }}
              title="Настроить виджет"
              onClick={() => setSettingsOpen((v) => !v)}
            >
              <Settings size={14} />
            </button>
          ) : null}
          <button
            className="btn btn-secondary"
            style={{ padding: '4px 8px' }}
            title="Скрыть виджет"
            onClick={() => onHide(widget.id)}
          >
            <EyeOff size={14} />
          </button>
        </div>
      )}

      {editMode && settingsOpen && (
        <div
          className="glass-card"
          style={{
            position: 'absolute',
            top: '44px',
            right: '8px',
            zIndex: 6,
            padding: '12px',
            minWidth: '200px',
            display: 'flex',
            flexDirection: 'column',
            gap: '10px',
          }}
        >
          {widget.supportsChartType && (
            <div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '6px' }}>Тип диаграммы</div>
              <div style={{ display: 'flex', gap: '6px' }}>
                {widget.supportsChartType.map((ct) => (
                  <button
                    key={ct}
                    className="btn btn-secondary"
                    style={{
                      padding: '4px 10px',
                      fontSize: '11px',
                      ...((widget.chartType || widget.supportsChartType![0]) === ct
                        ? { background: 'var(--secondary)', color: '#000', fontWeight: 700 }
                        : {}),
                    }}
                    onClick={() => onConfigChange(widget.id, { chartType: ct })}
                  >
                    {CHART_TYPE_LABELS[ct]}
                  </button>
                ))}
              </div>
            </div>
          )}
          {widget.supportsMetrics && (
            <div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '6px' }}>Метрика</div>
              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                {widget.supportsMetrics.map((m) => (
                  <button
                    key={m.key}
                    className="btn btn-secondary"
                    style={{
                      padding: '4px 10px',
                      fontSize: '11px',
                      ...((widget.metrics?.[0] || widget.supportsMetrics![0].key) === m.key
                        ? { background: 'var(--secondary)', color: '#000', fontWeight: 700 }
                        : {}),
                    }}
                    onClick={() => onConfigChange(widget.id, { metrics: [m.key] })}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
            </div>
          )}
          {widget.supportsPeriod && (
            <div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '6px' }}>Период</div>
              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                {PERIOD_OPTIONS.map((p) => (
                  <button
                    key={p}
                    className="btn btn-secondary"
                    style={{
                      padding: '4px 10px',
                      fontSize: '11px',
                      ...((widget.period || 30) === p ? { background: 'var(--secondary)', color: '#000', fontWeight: 700 } : {}),
                    }}
                    onClick={() => onConfigChange(widget.id, { period: p })}
                  >
                    {p} дн.
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {children}
    </div>
  );
};
