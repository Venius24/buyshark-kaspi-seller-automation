import React, { useState } from 'react';
import { ChevronDown } from 'lucide-react';

interface ExpandableCardProps {
  icon?: React.ReactNode;
  title: string;
  subtitle?: string;
  headerRight?: React.ReactNode;
  children: React.ReactNode;
  defaultExpanded?: boolean;
}

export const ExpandableCard: React.FC<ExpandableCardProps> = ({
  icon,
  title,
  subtitle,
  headerRight,
  children,
  defaultExpanded = false,
}) => {
  const [expanded, setExpanded] = useState(defaultExpanded);

  return (
    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: expanded ? '18px' : '0px' }}>
      <button
        onClick={() => setExpanded((v) => !v)}
        style={{
          all: 'unset',
          cursor: 'pointer',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: '12px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0 }}>
          {icon && <span style={{ fontSize: '22px', flexShrink: 0 }}>{icon}</span>}
          <div style={{ minWidth: 0 }}>
            <h2 style={{ fontSize: '16px', fontWeight: 700, letterSpacing: '-0.3px' }}>{title}</h2>
            {subtitle && (
              <p style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>{subtitle}</p>
            )}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0 }}>
          {headerRight}
          <ChevronDown
            size={18}
            color="var(--text-muted)"
            style={{
              transition: 'transform 0.3s cubic-bezier(0.16, 1, 0.3, 1)',
              transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)',
            }}
          />
        </div>
      </button>

      <div
        style={{
          display: 'grid',
          gridTemplateRows: expanded ? '1fr' : '0fr',
          transition: 'grid-template-rows 0.35s cubic-bezier(0.16, 1, 0.3, 1)',
        }}
      >
        <div style={{ overflow: 'hidden' }}>
          <div style={{ paddingTop: expanded ? '4px' : '0px' }}>{children}</div>
        </div>
      </div>
    </div>
  );
};
