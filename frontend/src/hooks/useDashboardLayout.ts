import { useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';
import { WIDGET_REGISTRY, type WidgetConfig, type ChartType, type Period } from '../dashboard/widgetRegistry';
import { useActiveStoreId, useActiveStoreRole } from '../storeContext';

export interface MergedWidget extends WidgetConfig {
  id: string;
  title: string;
  visible: boolean;
  order: number;
  supportsChartType?: ChartType[];
  supportsMetrics?: { key: string; label: string }[];
  supportsPeriod?: boolean;
}

interface SavedWidgetLayout {
  id: string;
  visible?: boolean;
  order?: number;
  chartType?: ChartType;
  metrics?: string[];
  period?: Period;
}

interface RoleDefaultWidget {
  id: string;
  visible: boolean;
}

const QUERY_KEY = ['dashboard-layout'];

const fetchLayout = async (): Promise<SavedWidgetLayout[]> => {
  try {
    const res = await apiFetch(`${API_URL}/api/dashboard/layout`);
    const data = await res.json();
    return res.ok && data.success ? data.widgets : [];
  } catch {
    return [];
  }
};

const putLayout = async (widgets: SavedWidgetLayout[]) => {
  const res = await apiFetch(`${API_URL}/api/dashboard/layout`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ widgets }),
  });
  if (!res.ok) throw new Error('Не удалось сохранить раскладку дашборда');
  return res.json();
};

const fetchRoleDefaults = async (role: string): Promise<RoleDefaultWidget[]> => {
  try {
    const res = await apiFetch(`${API_URL}/api/dashboard/role-defaults?role=${encodeURIComponent(role)}`);
    const data = await res.json();
    return res.ok && data.success ? data.widgets : [];
  } catch {
    return [];
  }
};

const putRoleDefaults = async ({ role, widgets }: { role: string; widgets: RoleDefaultWidget[] }) => {
  const res = await apiFetch(`${API_URL}/api/dashboard/role-defaults`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role, widgets }),
  });
  if (!res.ok) throw new Error('Не удалось сохранить дефолты дашборда для роли');
  return res.json();
};

function mergeWithDefaults(saved: SavedWidgetLayout[], roleDefaults: RoleDefaultWidget[]): MergedWidget[] {
  const roleOverrides = new Map(roleDefaults.map((w) => [w.id, w.visible]));
  const overrides = new Map(saved.map((w) => [w.id, w]));
  const merged = WIDGET_REGISTRY.map((def) => {
    const o = overrides.get(def.id);
    // Видимость: реестр -> дефолт роли (если OWNER его настроил) -> личный
    // оверрайд пользователя (сам явно скрыл/показал — побеждает всегда).
    const roleVisible = roleOverrides.has(def.id) ? roleOverrides.get(def.id) : def.defaultVisible;
    return {
      id: def.id,
      title: def.title,
      visible: o?.visible ?? roleVisible ?? def.defaultVisible,
      order: o?.order ?? def.defaultOrder,
      chartType: o?.chartType,
      metrics: o?.metrics,
      period: o?.period,
      supportsChartType: def.supportsChartType,
      supportsMetrics: def.supportsMetrics,
      supportsPeriod: def.supportsPeriod,
    };
  });
  return merged.sort((a, b) => a.order - b.order);
}

export function useDashboardLayout() {
  const queryClient = useQueryClient();
  const activeStoreId = useActiveStoreId();
  const myRole = useActiveStoreRole();
  const needsRoleDefaults = myRole === 'MANAGER' || myRole === 'VIEWER';

  const { data: saved = [], isLoading } = useQuery({
    queryKey: QUERY_KEY,
    queryFn: fetchLayout,
    staleTime: 60_000,
  });

  const { data: roleDefaults = [] } = useQuery({
    queryKey: ['dashboard-role-defaults', activeStoreId, myRole],
    queryFn: () => fetchRoleDefaults(myRole as string),
    enabled: needsRoleDefaults && !!activeStoreId,
    staleTime: 60_000,
  });

  const mergedWidgets = useMemo(() => mergeWithDefaults(saved, needsRoleDefaults ? roleDefaults : []), [saved, roleDefaults, needsRoleDefaults]);

  const mutation = useMutation({
    mutationFn: putLayout,
    onMutate: async (widgets: SavedWidgetLayout[]) => {
      await queryClient.cancelQueries({ queryKey: QUERY_KEY });
      const previous = queryClient.getQueryData<SavedWidgetLayout[]>(QUERY_KEY);
      queryClient.setQueryData(QUERY_KEY, widgets);
      return { previous };
    },
    onError: (_err, _widgets, context) => {
      if (context?.previous) queryClient.setQueryData(QUERY_KEY, context.previous);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    },
  });

  const saveLayout = (widgets: MergedWidget[]) => {
    const payload: SavedWidgetLayout[] = widgets.map((w) => ({
      id: w.id,
      visible: w.visible,
      order: w.order,
      ...(w.chartType ? { chartType: w.chartType } : {}),
      ...(w.metrics ? { metrics: w.metrics } : {}),
      ...(w.period ? { period: w.period } : {}),
    }));
    mutation.mutate(payload);
  };

  const setWidgetVisible = (id: string, visible: boolean) => {
    saveLayout(mergedWidgets.map((w) => (w.id === id ? { ...w, visible } : w)));
  };

  const reorderWidgets = (orderedIds: string[]) => {
    const orderIndex = new Map(orderedIds.map((id, i) => [id, i]));
    saveLayout(mergedWidgets.map((w) => ({ ...w, order: orderIndex.get(w.id) ?? w.order })));
  };

  const setWidgetConfig = (id: string, config: Partial<Pick<MergedWidget, 'chartType' | 'metrics' | 'period'>>) => {
    saveLayout(mergedWidgets.map((w) => (w.id === id ? { ...w, ...config } : w)));
  };

  return { widgets: mergedWidgets, isLoading, setWidgetVisible, reorderWidgets, setWidgetConfig };
}

// Только для OWNER — управление дефолтной видимостью виджетов для роли
// (MANAGER/VIEWER) в активном магазине. Отдельно от useDashboardLayout,
// т.к. OWNER настраивает чужой дефолт, а не свою личную раскладку.
export function useDashboardRoleDefaultsAdmin(role: 'MANAGER' | 'VIEWER') {
  const queryClient = useQueryClient();
  const activeStoreId = useActiveStoreId();
  const queryKey = ['dashboard-role-defaults', activeStoreId, role];

  const { data: widgets = [] } = useQuery({
    queryKey,
    queryFn: () => fetchRoleDefaults(role),
    enabled: !!activeStoreId,
  });

  const mutation = useMutation({
    mutationFn: (next: RoleDefaultWidget[]) => putRoleDefaults({ role, widgets: next }),
    onSuccess: (_data, next) => {
      queryClient.setQueryData(queryKey, next);
    },
  });

  const setVisible = (id: string, visible: boolean) => {
    const known = new Map(WIDGET_REGISTRY.map((w) => [w.id, w.defaultVisible]));
    const current = new Map(widgets.map((w) => [w.id, w.visible]));
    WIDGET_REGISTRY.forEach((w) => {
      if (!current.has(w.id)) current.set(w.id, known.get(w.id) ?? true);
    });
    current.set(id, visible);
    mutation.mutate(Array.from(current.entries()).map(([wid, vis]) => ({ id: wid, visible: vis })));
  };

  return { widgets, setVisible };
}
