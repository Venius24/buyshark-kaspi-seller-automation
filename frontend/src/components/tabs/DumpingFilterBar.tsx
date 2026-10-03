import { useTranslation } from 'react-i18next';
import type { DumpingFacets, DumpingFilters } from '../../types';
import { EMPTY_DUMPING_FILTERS } from '../../types';
import { HelpHint } from '../shared/HelpHint';

interface Option {
  value: string;
  label: string;
  /** Ключ счётчика в facets. Отсутствует — счётчик не показываем. */
  facet?: string;
}

interface DumpingFilterBarProps {
  filters: DumpingFilters;
  onChange: (next: DumpingFilters) => void;
  facets: DumpingFacets | null;
  /** Границы известного числа конкурентов — подсказка в placeholder'ах. */
  competitorsRange: { min: number | null; max: number | null } | null;
}

const selectStyle: React.CSSProperties = {
  background: 'var(--input-bg)',
  color: '#fff',
  border: '1px solid var(--input-border)',
  height: '34px',
  borderRadius: '8px',
  cursor: 'pointer',
  fontSize: '12px',
  padding: '0 8px',
};

const numberStyle: React.CSSProperties = {
  ...selectStyle,
  cursor: 'text',
  width: '72px',
};

const labelStyle: React.CSSProperties = {
  display: 'block',
  fontSize: '10px',
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
  color: 'var(--text-muted)',
  marginBottom: '4px',
};

/** "Название (12)" — счётчик подставляем только когда бэкенд его прислал. */
function withCount(label: string, facets: DumpingFacets | null, key?: string): string {
  if (!key || !facets || facets[key] === undefined) return label;
  return `${label} (${facets[key]})`;
}

export function DumpingFilterBar({ filters, onChange, facets, competitorsRange }: DumpingFilterBarProps) {
  const { t } = useTranslation('tab_dumping');

  const set = <K extends keyof DumpingFilters>(key: K, value: DumpingFilters[K]) =>
    onChange({ ...filters, [key]: value });

  const parseCount = (raw: string): number | null => {
    if (raw.trim() === '') return null;
    const n = parseInt(raw, 10);
    return Number.isFinite(n) && n >= 0 ? n : null;
  };

  const dumpingStateOptions: Option[] = [
    { value: '', label: t('f_dumping_state_any') },
    { value: 'ON', label: t('f_dumping_state_on'), facet: 'ON' },
    { value: 'OFF_WITH_RIVALS', label: t('f_dumping_state_off_with_rivals'), facet: 'OFF_WITH_RIVALS' },
    { value: 'OFF', label: t('f_dumping_state_off'), facet: 'OFF' },
    { value: 'NO_CONFIG', label: t('f_dumping_state_no_config'), facet: 'NO_CONFIG' },
  ];

  const priceStateOptions: Option[] = [
    { value: '', label: t('f_price_state_any') },
    { value: 'AT_MIN', label: t('f_price_state_at_min'), facet: 'AT_MIN' },
    { value: 'AT_MAX', label: t('f_price_state_at_max'), facet: 'AT_MAX' },
    { value: 'IN_RANGE', label: t('f_price_state_in_range'), facet: 'IN_RANGE' },
    { value: 'NO_LIMITS', label: t('f_price_state_no_limits'), facet: 'NO_LIMITS' },
  ];

  const rankStateOptions: Option[] = [
    { value: '', label: t('f_rank_state_any') },
    { value: 'LEADER', label: t('f_rank_state_leader'), facet: 'LEADER' },
    { value: 'NOT_LEADER', label: t('f_rank_state_not_leader'), facet: 'NOT_LEADER' },
    { value: 'NO_DATA', label: t('f_rank_state_no_data'), facet: 'NO_DATA' },
  ];

  const attackStateOptions: Option[] = [
    { value: '', label: t('f_attack_state_any') },
    { value: 'UNDER_ATTACK', label: t('f_attack_state_under_attack'), facet: 'UNDER_ATTACK' },
    { value: 'LADDER', label: t('f_attack_state_ladder'), facet: 'LADDER' },
    { value: 'GUARDED', label: t('f_attack_state_guarded'), facet: 'GUARDED' },
  ];

  const statusOptions: Option[] = [
    { value: '', label: t('all_statuses') },
    { value: 'WINNING_1ST', label: t('option_1st_place') },
    { value: 'HOLDING_2ND', label: t('option_2nd_place') },
    { value: 'TARGET_POS_REACHED', label: t('option_target_position') },
    { value: 'AT_MIN_PRICE', label: t('option_min_price') },
    { value: 'MAX_PRICE_SINGLE', label: t('option_max_price') },
    { value: 'GUARD_HOLD', label: t('option_guard_hold') },
    { value: 'OFF', label: t('option_disabled') },
  ];

  const activeCount =
    (filters.status ? 1 : 0) +
    (filters.dumpingState ? 1 : 0) +
    (filters.priceState ? 1 : 0) +
    (filters.rankState ? 1 : 0) +
    (filters.attackState ? 1 : 0) +
    (filters.minCompetitors !== null ? 1 : 0) +
    (filters.maxCompetitors !== null ? 1 : 0);

  const renderSelect = (
    label: string,
    hint: string,
    value: string,
    options: Option[],
    onSelect: (v: string) => void
  ) => (
    <div>
      <label style={labelStyle}>
        {label}
        <HelpHint text={hint} />
      </label>
      <select
        value={value}
        onChange={(e) => onSelect(e.target.value)}
        className="form-input"
        style={{ ...selectStyle, borderColor: value ? 'var(--primary)' : 'var(--input-border)' }}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {withCount(o.label, facets, o.facet)}
          </option>
        ))}
      </select>
    </div>
  );

  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'flex-end',
        gap: '12px',
        marginTop: '12px',
        padding: '12px',
        background: 'rgba(255,255,255,0.02)',
        border: '1px solid rgba(255,255,255,0.06)',
        borderRadius: '10px',
      }}
    >
      {renderSelect(
        t('f_dumping_state_label'),
        t('f_dumping_state_hint'),
        filters.dumpingState,
        dumpingStateOptions,
        (v) => set('dumpingState', v)
      )}

      {renderSelect(
        t('f_price_state_label'),
        t('f_price_state_hint'),
        filters.priceState,
        priceStateOptions,
        (v) => set('priceState', v)
      )}

      {renderSelect(
        t('f_rank_state_label'),
        t('f_rank_state_hint'),
        filters.rankState,
        rankStateOptions,
        (v) => set('rankState', v)
      )}

      {renderSelect(
        t('f_attack_state_label'),
        t('f_attack_state_hint'),
        filters.attackState,
        attackStateOptions,
        (v) => set('attackState', v)
      )}

      {renderSelect(
        t('f_status_label'),
        t('f_status_hint'),
        filters.status,
        statusOptions,
        (v) => set('status', v)
      )}

      <div>
        <label style={labelStyle}>
          {t('f_competitors_label')}
          <HelpHint text={t('f_competitors_hint')} />
        </label>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <input
            type="number"
            min={0}
            className="form-input"
            style={{
              ...numberStyle,
              borderColor: filters.minCompetitors !== null ? 'var(--primary)' : 'var(--input-border)',
            }}
            placeholder={competitorsRange?.min !== null && competitorsRange?.min !== undefined
              ? String(competitorsRange.min)
              : t('f_competitors_from')}
            value={filters.minCompetitors ?? ''}
            onChange={(e) => set('minCompetitors', parseCount(e.target.value))}
          />
          <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>—</span>
          <input
            type="number"
            min={0}
            className="form-input"
            style={{
              ...numberStyle,
              borderColor: filters.maxCompetitors !== null ? 'var(--primary)' : 'var(--input-border)',
            }}
            placeholder={competitorsRange?.max !== null && competitorsRange?.max !== undefined
              ? String(competitorsRange.max)
              : t('f_competitors_to')}
            value={filters.maxCompetitors ?? ''}
            onChange={(e) => set('maxCompetitors', parseCount(e.target.value))}
          />
        </div>
      </div>

      <button
        type="button"
        className="btn btn-secondary"
        style={{ height: '34px', padding: '0 14px', fontSize: '12px', opacity: activeCount ? 1 : 0.4 }}
        disabled={activeCount === 0}
        onClick={() => onChange(EMPTY_DUMPING_FILTERS)}
      >
        {activeCount ? t('f_reset_n', { n: activeCount }) : t('f_reset')}
      </button>
    </div>
  );
}
