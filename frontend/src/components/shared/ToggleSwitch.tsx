interface ToggleSwitchProps {
  checked: boolean;
  /** Средний штрих вместо кружка — используется у общего рубильника, когда часть фич включена, а часть выключена. */
  indeterminate?: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  size?: 'md' | 'lg';
}

export function ToggleSwitch({ checked, indeterminate, disabled, onChange, label, size = 'md' }: ToggleSwitchProps) {
  const isLarge = size === 'lg';
  const width = isLarge ? 52 : 40;
  const height = isLarge ? 30 : 22;
  const knob = height - 6;

  let trackColor = 'rgba(255, 255, 255, 0.14)';
  if (checked) trackColor = 'var(--secondary)';
  if (indeterminate) trackColor = 'var(--warning)';

  return (
    <button
      type="button"
      role="switch"
      aria-checked={indeterminate ? 'mixed' : checked}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      style={{
        width, height,
        borderRadius: height,
        border: 'none',
        background: trackColor,
        position: 'relative',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        transition: 'background 0.18s ease',
        flexShrink: 0,
        padding: 0,
      }}
    >
      <span
        style={{
          position: 'absolute',
          top: 3,
          left: indeterminate ? (width - knob) / 2 : checked ? width - knob - 3 : 3,
          width: knob,
          height: knob,
          borderRadius: '50%',
          background: '#fff',
          boxShadow: '0 1px 3px rgba(0,0,0,0.4)',
          transition: 'left 0.18s ease',
        }}
      />
    </button>
  );
}
