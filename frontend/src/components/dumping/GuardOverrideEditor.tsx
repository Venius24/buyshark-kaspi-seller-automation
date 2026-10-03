import { useTranslation } from 'react-i18next';
import type { GuardOverride, GuardSettings } from '../../types';
import { GuardSettingsForm } from './GuardSettingsForm';
import { HelpHint } from '../shared/HelpHint';

/** Защита от оттеснения на одном товаре / Мастер-Группе: наследовать общие
 *  настройки, задать свои поверх них или выключить. */
interface Props {
  value: GuardOverride;
  onChange: (next: GuardOverride) => void;
  /** Что действует уровнем выше (магазин, для товара в группе — магазин+группа). */
  inherited?: GuardSettings;
  disabled?: boolean;
}

export function GuardOverrideEditor({ value, onChange, inherited, disabled }: Props) {
  const { t } = useTranslation('dumping_guard');
  const mode = value?.mode ?? 'INHERIT';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, color: '#fff', fontWeight: 600 }}>🛡 {t('override_title')}</span>
        <HelpHint text={t('override_hint')} />
        <select
          className="form-input"
          disabled={disabled}
          value={mode}
          onChange={(e) => {
            const m = e.target.value;
            if (m === 'INHERIT') onChange(null);
            else if (m === 'OFF') onChange({ mode: 'OFF' });
            else onChange({ mode: 'CUSTOM' });
          }}
          style={{ background: 'var(--input-bg)', color: '#fff', border: '1px solid var(--input-border)', height: 34, borderRadius: 8, fontSize: 12, width: 'auto' }}
        >
          <option value="INHERIT">
            {t('override_inherit')}{inherited ? ` (${inherited.enabled ? t('state_on') : t('state_off')})` : ''}
          </option>
          <option value="CUSTOM">{t('override_custom')}</option>
          <option value="OFF">{t('override_off')}</option>
        </select>
      </div>

      {mode === 'CUSTOM' && (
        <GuardSettingsForm
          mode="override"
          disabled={disabled}
          inherited={inherited}
          value={value && value.mode === 'CUSTOM' ? value : {}}
          onChange={(next) => onChange({ mode: 'CUSTOM', ...next } as GuardOverride)}
        />
      )}
    </div>
  );
}
