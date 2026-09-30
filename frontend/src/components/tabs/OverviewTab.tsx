import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { DndContext, closestCenter, PointerSensor, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core';
import { SortableContext, verticalListSortingStrategy, arrayMove } from '@dnd-kit/sortable';
import { LayoutGrid, Users, X } from 'lucide-react';
import { WIDGET_REGISTRY, type OverviewRenderCtx } from '../../dashboard/widgetRegistry';
import { useDashboardLayout } from '../../hooks/useDashboardLayout';
import { DashboardWidgetShell } from '../dashboard/DashboardWidgetShell';
import { RoleDefaultsModal } from '../dashboard/RoleDefaultsModal';
import { useIsStoreOwner } from '../../storeContext';
import { useScoreStats } from '../../hooks/useScoreStats';
import { useQualityMetrics } from '../../hooks/useQualityMetrics';
import { useInsightsStats } from '../../hooks/useInsightsStats';

interface OverviewTabProps {
  analytics: {
    best_price_share: number;
    winning_count: number;
    total_scanned: number;
    total_saved_margin: number;
  };
  widgetData: any;
  addLog: (message: string) => void;
  mcHasSession: boolean;
  onOpenMcAuth: () => void;
  onOpenProducts: () => void;
  onOpenDumping: () => void;
  onOpenPackaging: () => void;
  onOpenLogs: () => void;
}

export function OverviewTab({
  analytics,
  widgetData,
  addLog,
  mcHasSession,
  onOpenMcAuth,
  onOpenProducts,
  onOpenDumping,
  onOpenPackaging,
  onOpenLogs,
}: OverviewTabProps) {
  const { t } = useTranslation('tab_overview');
  const scoreStats = useScoreStats();
  const qualityMetrics = useQualityMetrics();
  const insightsStats = useInsightsStats();

  const { widgets, setWidgetVisible, reorderWidgets, setWidgetConfig } = useDashboardLayout();
  const [editMode, setEditMode] = useState(false);
  const [roleModalOpen, setRoleModalOpen] = useState(false);
  const isStoreOwner = useIsStoreOwner();
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));

  const ctx: OverviewRenderCtx = {
    t,
    analytics,
    widgetData,
    scoreStats,
    qualityMetrics,
    insightsStats,
    addLog,
    mcHasSession,
    onOpenMcAuth,
    onOpenProducts,
    onOpenDumping,
    onOpenPackaging,
    onOpenLogs,
  };

  const visibleWidgets = widgets.filter((w) => w.visible);
  const hiddenWidgets = widgets.filter((w) => !w.visible);

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const ids = visibleWidgets.map((w) => w.id);
    const oldIndex = ids.indexOf(String(active.id));
    const newIndex = ids.indexOf(String(over.id));
    if (oldIndex === -1 || newIndex === -1) return;
    reorderWidgets(arrayMove(ids, oldIndex, newIndex));
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', flexWrap: 'wrap' }}>
        {editMode && hiddenWidgets.length > 0 && (
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Скрытые:</span>
            {hiddenWidgets.map((w) => (
              <button
                key={w.id}
                className="btn btn-secondary"
                style={{ padding: '4px 10px', fontSize: '11px' }}
                onClick={() => setWidgetVisible(w.id, true)}
              >
                + {w.title}
              </button>
            ))}
          </div>
        )}
        {editMode && isStoreOwner && (
          <button className="btn btn-secondary" style={{ padding: '8px 14px', fontSize: '13px' }} onClick={() => setRoleModalOpen(true)}>
            <Users size={14} /> Управление ролями
          </button>
        )}
        <button className="btn btn-secondary" style={{ padding: '8px 14px', fontSize: '13px' }} onClick={() => setEditMode((v) => !v)}>
          {editMode ? <X size={14} /> : <LayoutGrid size={14} />}
          {editMode ? ' Готово' : ' Настроить дашборд'}
        </button>
      </div>

      {roleModalOpen && <RoleDefaultsModal onClose={() => setRoleModalOpen(false)} />}

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={visibleWidgets.map((w) => w.id)} strategy={verticalListSortingStrategy}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
            {visibleWidgets.map((widget) => {
              const def = WIDGET_REGISTRY.find((d) => d.id === widget.id);
              if (!def) return null;
              return (
                <DashboardWidgetShell
                  key={widget.id}
                  widget={widget}
                  editMode={editMode}
                  onHide={(id) => setWidgetVisible(id, false)}
                  onConfigChange={(id, config) => setWidgetConfig(id, config)}
                >
                  {def.render(ctx, widget)}
                </DashboardWidgetShell>
              );
            })}
          </div>
        </SortableContext>
      </DndContext>
    </div>
  );
}
