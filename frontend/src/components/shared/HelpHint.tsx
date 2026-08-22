import { useState, useRef, useLayoutEffect, type CSSProperties } from 'react';
import { HelpCircle } from 'lucide-react';

const POPOVER_WIDTH = 280;

interface HelpHintProps {
  text: string;
  /** Только для сложных фич (печать, чаты и т.п.) — скриншот/схема с пояснением. */
  imageSrc?: string;
}

export function HelpHint({ text, imageSrc }: HelpHintProps) {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);
  const [style, setStyle] = useState<CSSProperties>({});

  useLayoutEffect(() => {
    if (!open || !anchorRef.current) return;
    const rect = anchorRef.current.getBoundingClientRect();
    let left = rect.left - POPOVER_WIDTH / 2 + rect.width / 2;
    left = Math.max(12, Math.min(left, window.innerWidth - POPOVER_WIDTH - 12));
    setStyle({ position: 'fixed', top: rect.bottom + 8, left, width: POPOVER_WIDTH });
  }, [open]);

  return (
    <span className="help-hint">
      <button
        ref={anchorRef}
        type="button"
        className="help-hint-trigger"
        onClick={() => setOpen((o) => !o)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        aria-label="Помощь"
      >
        <HelpCircle size={14} />
      </button>
      {open && (
        <div className="help-hint-popover" style={style}>
          <div className="help-hint-text">{text}</div>
          {imageSrc && <img src={imageSrc} alt="" className="help-hint-image" />}
        </div>
      )}
    </span>
  );
}
