import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { GuardSettings } from '../../types';
import { HelpHint } from '../shared/HelpHint';

/**
 * Поля защиты от оттеснения (backend/dumper/guard.py::GuardConfig).
 *
 * Один компонент на все три уровня:
 * - mode="full" — общие настройки магазина, каждое поле имеет значение;
 * - mode="override" — переопределение на Мастер-Группе / товаре: пустое
 *   поле (ключа нет в value) значит «как у родителя», а `inherited` даёт
 *   родительские значения в подсказках.
 */
type Value = Partial<GuardSettings>;

interface Props {
  value: Value;
  onChange: (next: Value) => void;
  mode: 'full' | 'override';
  inherited?: GuardSettings;
  disabled?: boolean;
}

const label: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 };
const section: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 12 };
const sectionTitle: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: 'var(--secondary)', margin: '4px 0 0' };
const selectStyle: React.CSSProperties = { background: 'var(--input-bg)', color: '#fff', border: '1px solid var(--input-border)', height: 38, borderRadius: 8 };

type NumKey = 'min_reviews' | 'min_rating' | 'min_merchant_age_days' | 'max_extra_delivery_days'
  | 'floor_below_next_pct' | 'max_daily_drop_pct' | 'drop_pause_hours'
  | 'ladder_min_moves' | 'ladder_window_hours' | 'ladder_cooldown_hours';
type BoolKey = 'ignore_intercity' | 'ignore_preorder' | 'drop_alert';
type SelectKey = 'all_ignored_action' | 'ladder_response' | 'no_rating_action';
const SELECT_KEYS: ReadonlyArray<keyof GuardSettings> = ['all_ignored_action', 'ladder_response', 'no_rating_action'];

export function GuardSettingsForm({ value, onChange, mode, inherited, disabled }: Props) {
  const { t } = useTranslation('dumping_guard');
  const isOverride = mode === 'override';

  // Списки правим как текст: иначе запятую на середине ввода съедал бы разбор.
  const [listText, setListText] = useState({
    blacklist: (value.blacklist ?? []).join(', '),
    whitelist: (value.whitelist ?? []).join(', '),
  });
  useEffect(() => {
    setListText({ blacklist: (value.blacklist ?? []).join(', '), whitelist: (value.whitelist ?? []).join(', ') });
    // value.* меняется извне только при загрузке/сбросе формы
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(value.blacklist), JSON.stringify(value.whitelist)]);

  const set = (key: keyof GuardSettings, v: unknown) => {
    const next: Value = { ...value };
    if (isOverride && (v === undefined || v === '' || v === null)) {
      delete next[key];
    } else {
      (next as Record<string, unknown>)[key] = v;
    }
    onChange(next);
  };

  const inheritedHint = (key: keyof GuardSettings): string => {
    if (!isOverride || !inherited) return '';
    const v = inherited[key];
    if (v === null || v === undefined || (Array.isArray(v) && v.length === 0)) return t('inherit_empty');
    if (typeof v === 'boolean') return t('inherit_value', { v: v ? t('yes') : t('no') });
    if (SELECT_KEYS.includes(key)) return t('inherit_value', { v: t(`${key}_${v}`) });
    return t('inherit_value', { v: Array.isArray(v) ? v.join(', ') : String(v) });
  };

  const num = (key: NumKey, opts: { step?: string; suffix?: string } = {}) => (
    <div>
      <label style={label}>
        {t(`f_${key}`)}
        <HelpHint text={t(`h_${key}`)} />
      </label>
      <input
        type="number"
        min={0}
        step={opts.step ?? '1'}
        className="form-input"
        disabled={disabled}
        value={value[key] ?? ''}
        placeholder={isOverride ? inheritedHint(key) : t('not_set')}
        onChange={(e) => {
          const raw = e.target.value;
          set(key, raw === '' ? (isOverride ? undefined : null) : Number(raw));
        }}
      />
    </div>
  );

  const bool = (key: BoolKey) => {
    if (!isOverride) {
      return (
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: '#fff', cursor: 'pointer' }}>
          <input
            type="checkbox"
            disabled={disabled}
            checked={!!value[key]}
            onChange={(e) => set(key, e.target.checked)}
            style={{ width: 16, height: 16 }}
          />
          {t(`f_${key}`)}
          <HelpHint text={t(`h_${key}`)} />
        </label>
      );
    }
    const current = value[key] === undefined ? '' : value[key] ? 'yes' : 'no';
    return (
      <div>
        <label style={label}>{t(`f_${key}`)}<HelpHint text={t(`h_${key}`)} /></label>
        <select
          className="form-input"
          style={selectStyle}
          disabled={disabled}
          value={current}
          onChange={(e) => set(key, e.target.value === '' ? undefined : e.target.value === 'yes')}
        >
          <option value="">{inheritedHint(key) || t('inherit')}</option>
          <option value="yes">{t('yes')}</option>
          <option value="no">{t('no')}</option>
        </select>
      </div>
    );
  };

  const list = (key: 'blacklist' | 'whitelist') => (
    <div style={{ gridColumn: '1 / -1' }}>
      <label style={label}>{t(`f_${key}`)}<HelpHint text={t(`h_${key}`)} /></label>
      <input
        type="text"
        className="form-input"
        disabled={disabled}
        value={listText[key]}
        placeholder={isOverride ? inheritedHint(key) : t('list_placeholder')}
        onChange={(e) => setListText((prev) => ({ ...prev, [key]: e.target.value }))}
        onBlur={() => {
          const arr = listText[key].split(',').map((s) => s.trim()).filter(Boolean);
          set(key, isOverride && arr.length === 0 ? undefined : arr);
        }}
      />
    </div>
  );

  const select = (key: SelectKey, options: string[]) => (
    <div>
      <label style={label}>{t(`f_${key}`)}<HelpHint text={t(`h_${key}`)} /></label>
      <select
        className="form-input"
        style={selectStyle}
        disabled={disabled}
        value={(value[key] as string | undefined) ?? ''}
        onChange={(e) => set(key, e.target.value || undefined)}
      >
        {isOverride && <option value="">{inheritedHint(key) || t('inherit')}</option>}
        {options.map((o) => <option key={o} value={o}>{t(`${key}_${o}`)}</option>)}
      </select>
    </div>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <h5 style={sectionTitle}>{t('section_ignore')}</h5>
      <div style={section}>
        {num('min_reviews')}
        {select('no_rating_action', ['ASK', 'IGNORE', 'CONSIDER'])}
        {num('min_rating', { step: '0.1' })}
        {num('min_merchant_age_days')}
        {num('max_extra_delivery_days')}
        {select('all_ignored_action', ['HOLD', 'MAX'])}
      </div>
      <div style={{ display: isOverride ? 'grid' : 'flex', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', flexWrap: 'wrap', gap: isOverride ? 12 : 20 }}>
        {bool('ignore_intercity')}
        {bool('ignore_preorder')}
      </div>
      <div style={section}>
        {list('blacklist')}
        {list('whitelist')}
      </div>

      <h5 style={sectionTitle}>{t('section_drop')}</h5>
      <div style={section}>
        {num('max_daily_drop_pct', { step: '0.5' })}
        {num('drop_pause_hours')}
        <div style={{ display: 'flex', alignItems: 'flex-end', paddingBottom: isOverride ? 0 : 10 }}>{bool('drop_alert')}</div>
        {num('floor_below_next_pct', { step: '0.5' })}
      </div>

      <h5 style={sectionTitle}>{t('section_ladder')}</h5>
      <div style={section}>
        {select('ladder_response', ['RESET_MAX', 'FLAG', 'FREEZE', 'RETREAT'])}
        {num('ladder_min_moves')}
        {num('ladder_window_hours')}
        {num('ladder_cooldown_hours')}
      </div>
    </div>
  );
}
