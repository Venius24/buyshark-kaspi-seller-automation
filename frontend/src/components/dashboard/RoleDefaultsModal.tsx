import React, { useState } from 'react';
import { X } from 'lucide-react';
import { WIDGET_REGISTRY } from '../../dashboard/widgetRegistry';
import { useDashboardRoleDefaultsAdmin } from '../../hooks/useDashboardLayout';

interface RoleDefaultsModalProps {
  onClose: () => void;
}

// OWNER-only: что по умолчанию видит MANAGER на своём дашборде — сам
// MANAGER может это переопределить у себя (см. mergeWithDefaults в
// useDashboardLayout.ts), это только стартовая раскладка для новых/ещё
// не настроивших свой вид сотрудников.
export const RoleDefaultsModal: React.FC<RoleDefaultsModalProps> = ({ onClose }) => {
  const [role, setRole] = useState<'MANAGER' | 'VIEWER'>('MANAGER');
  const { widgets, setVisible } = useDashboardRoleDefaultsAdmin(role);
  const visibleMap = new Map(widgets.map((w) => [w.id, w.visible]));

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 100, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
      onClick={onClose}
    >
      <div className="glass-card" style={{ width: '420px', maxHeight: '80vh', overflowY: 'auto', padding: '20px' }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
          <h2 style={{ fontSize: '16px', fontWeight: 700 }}>Управление ролями</h2>
          <button className="btn btn-secondary" style={{ padding: '4px 8px' }} onClick={onClose}>
            <X size={14} />
          </button>
        </div>
        <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '14px' }}>
          Что по умолчанию видит выбранная роль на дашборде. Сотрудник может вручную снова показать скрытый виджет у себя.
        </p>

        <div style={{ display: 'flex', gap: '6px', marginBottom: '14px' }}>
          {(['MANAGER', 'VIEWER'] as const).map((r) => (
            <button
              key={r}
              className="btn btn-secondary"
              style={{ padding: '6px 12px', fontSize: '12px', ...(role === r ? { background: 'var(--secondary)', color: '#000', fontWeight: 700 } : {}) }}
              onClick={() => setRole(r)}
            >
              {r === 'MANAGER' ? 'Менеджер' : 'Наблюдатель'}
            </button>
          ))}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
          {WIDGET_REGISTRY.map((def) => {
            const isVisible = visibleMap.has(def.id) ? visibleMap.get(def.id) : def.defaultVisible;
            return (
              <label key={def.id} style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '8px 4px', fontSize: '13px', cursor: 'pointer' }}>
                <input type="checkbox" checked={!!isVisible} onChange={(e) => setVisible(def.id, e.target.checked)} />
                {def.title}
              </label>
            );
          })}
        </div>
      </div>
    </div>
  );
};
