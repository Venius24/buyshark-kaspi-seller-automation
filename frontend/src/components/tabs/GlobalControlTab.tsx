import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { SlidersHorizontal, Zap, Link2, Package, Bot, type LucideIcon } from 'lucide-react';
import { fetchFeatureFlagsWithDefault, saveFeatureFlags, DEFAULT_FEATURE_FLAGS } from '../../queries';
import { ToggleSwitch } from '../shared/ToggleSwitch';
import { HelpHint } from '../shared/HelpHint';
import { useActiveStoreId } from '../../storeContext';
import type { ActiveTab, FeatureFlags } from '../../types';

interface FeatureRow {
  key: keyof FeatureFlags;
  icon: LucideIcon;
  titleKey: string;
  descKey: string;
  targetTab: ActiveTab;
  /** Выключение снимает уже настроенные точечные параметры (per-SKU/per-group)? У нас — никогда, но показываем предупреждение только там, где точечные настройки реально есть. */
  showPreservedNote: boolean;
}

const ROWS: FeatureRow[] = [
  { key: 'dumping_enabled', icon: Zap, titleKey: 'dumping_title', descKey: 'dumping_desc', targetTab: 'dumping', showPreservedNote: true },
  { key: 'stock_groups_enabled', icon: Link2, titleKey: 'stock_groups_title', descKey: 'stock_groups_desc', targetTab: 'stock-groups', showPreservedNote: true },
  { key: 'autoassembly_enabled', icon: Package, titleKey: 'autoassembly_title', descKey: 'autoassembly_desc', targetTab: 'auto-assembly', showPreservedNote: true },
  { key: 'chatbot_enabled', icon: Bot, titleKey: 'chatbot_title', descKey: 'chatbot_desc', targetTab: 'chatbot', showPreservedNote: false },
];

export function GlobalControlTab({ onSelectTab }: { onSelectTab: (tab: ActiveTab) => void }) {
  const { t } = useTranslation('global_control');
  const queryClient = useQueryClient();
  const activeStoreId = useActiveStoreId();
  const queryKey = ['feature-flags', activeStoreId];
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [error, setError] = useState(false);

  const { data: flags = DEFAULT_FEATURE_FLAGS } = useQuery({
    queryKey,
    queryFn: fetchFeatureFlagsWithDefault,
    enabled: !!activeStoreId,
  });

  const applyUpdate = async (updates: Partial<FeatureFlags>, savingId: string) => {
    setError(false);
    setSavingKey(savingId);
    const previous = flags;
    queryClient.setQueryData(queryKey, { ...flags, ...updates });
    try {
      const result = await saveFeatureFlags(updates);
      if (!result) throw new Error('save failed');
      queryClient.setQueryData(queryKey, result);
    } catch {
      queryClient.setQueryData(queryKey, previous);
      setError(true);
    } finally {
      setSavingKey(null);
    }
  };

  const enabledCount = ROWS.filter((row) => flags[row.key]).length;
  const masterState: 'on' | 'off' | 'partial' =
    enabledCount === ROWS.length ? 'on' : enabledCount === 0 ? 'off' : 'partial';

  // checked=true для 'on' и 'partial' (см. пропы ниже), поэтому клик в обоих случаях даёт next=false — "выключить всё", как и задумано.
  const handleMasterToggle = (next: boolean) => {
    const updates: Partial<FeatureFlags> = {};
    for (const row of ROWS) updates[row.key] = next;
    applyUpdate(updates, 'master');
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px', maxWidth: '820px' }}>
      <div>
        <h1 style={{ marginBottom: '6px', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <SlidersHorizontal size={22} /> {t('title')}
          <HelpHint text="Один переключатель на весь магазин: выключает демпинг, связку остатков, автосборку и ИИ-автоответчика разом, без похода по отдельным разделам. Точечные настройки (per-SKU, per-группа) при этом не сбрасываются — просто перестают срабатывать, пока автоматика выключена." />
        </h1>
        <p style={{ color: 'var(--text-muted)', fontSize: '13.5px', lineHeight: 1.5 }}>{t('subtitle')}</p>
      </div>

      <div className="glass-card" style={{ display: 'flex', alignItems: 'center', gap: '18px' }}>
        <ToggleSwitch
          size="lg"
          checked={masterState !== 'off'}
          indeterminate={masterState === 'partial'}
          disabled={savingKey !== null}
          onChange={handleMasterToggle}
          label={t('master_title')}
        />
        <div style={{ flexGrow: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '4px' }}>
            <h2 style={{ margin: 0 }}>{t('master_title')}</h2>
            <span
              className={`badge ${masterState === 'on' ? 'badge-success' : masterState === 'off' ? 'badge-warning' : ''}`}
              style={masterState === 'partial' ? { background: 'rgba(224, 163, 62, 0.14)', color: 'var(--warning)' } : undefined}
            >
              {masterState === 'on' ? t('master_state_on') : masterState === 'off' ? t('master_state_off') : t('master_state_partial')}
            </span>
          </div>
          <p style={{ color: 'var(--text-muted)', fontSize: '13px', lineHeight: 1.5, margin: 0 }}>{t('master_desc')}</p>
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {ROWS.map((row) => {
          const checked = flags[row.key];
          const isSaving = savingKey === row.key;
          const RowIcon = row.icon;
          return (
            <div key={row.key} className="glass-card" style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
              <span style={{ display: 'flex' }}><RowIcon size={22} /></span>
              <div style={{ flexGrow: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '2px', flexWrap: 'wrap' }}>
                  <h3 style={{ margin: 0 }}>{t(row.titleKey)}</h3>
                  <button
                    type="button"
                    onClick={() => onSelectTab(row.targetTab)}
                    style={{ background: 'none', border: 'none', color: 'var(--secondary)', fontSize: '12.5px', cursor: 'pointer', padding: 0 }}
                  >
                    {t('configure_link')}
                  </button>
                </div>
                <p style={{ color: 'var(--text-muted)', fontSize: '13px', lineHeight: 1.5, margin: 0 }}>{t(row.descKey)}</p>
                {!checked && row.showPreservedNote && (
                  <p style={{ color: 'var(--text-muted)', fontSize: '12px', marginTop: '4px', fontStyle: 'italic' }}>
                    {t('preserved_note')}
                  </p>
                )}
              </div>
              {isSaving && <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{t('saving')}</span>}
              <ToggleSwitch
                checked={checked}
                disabled={savingKey !== null}
                onChange={(next) => applyUpdate({ [row.key]: next } as Partial<FeatureFlags>, row.key)}
                label={t(row.titleKey)}
              />
            </div>
          );
        })}
      </div>

      {error && (
        <div style={{ color: 'var(--error)', fontSize: '13px' }}>{t('save_error')}</div>
      )}
    </div>
  );
}
