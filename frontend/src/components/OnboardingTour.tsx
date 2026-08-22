import { useState, useLayoutEffect, useCallback, type CSSProperties } from 'react';
import { useTranslation } from 'react-i18next';

// Онбординг-тур (2026-08-22): пошагово подсвечивает пункты сайдбара через
// data-tour="<id>" атрибуты (см. Sidebar.tsx), не переключает вкладки — чтобы
// не сбивать текущий экран пользователя, если тур открыт не с самого начала.
const STEPS = [
  { target: 'stores', titleKey: 'step_stores_title', textKey: 'step_stores_text' },
  { target: 'products', titleKey: 'step_products_title', textKey: 'step_products_text' },
  { target: 'print-batcher', titleKey: 'step_print_title', textKey: 'step_print_text' },
  { target: 'packaging', titleKey: 'step_packaging_title', textKey: 'step_packaging_text' },
  { target: 'chats', titleKey: 'step_chats_title', textKey: 'step_chats_text' },
  { target: 'help', titleKey: 'step_help_title', textKey: 'step_help_text' },
] as const;

const TOOLTIP_WIDTH = 320;

interface OnboardingTourProps {
  onFinish: () => void;
}

export function OnboardingTour({ onFinish }: OnboardingTourProps) {
  const { t } = useTranslation('onboarding_tour');
  const [stepIndex, setStepIndex] = useState(0);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const step = STEPS[stepIndex];
  const isLast = stepIndex === STEPS.length - 1;

  const measure = useCallback(() => {
    const el = document.querySelector(`[data-tour="${step.target}"]`);
    setRect(el ? el.getBoundingClientRect() : null);
  }, [step.target]);

  useLayoutEffect(() => {
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [measure]);

  const handleNext = () => {
    if (isLast) { onFinish(); return; }
    setStepIndex((i) => i + 1);
  };

  let tooltipStyle: CSSProperties;
  if (rect) {
    const top = Math.min(Math.max(rect.top, 16), window.innerHeight - 240);
    const left = Math.min(rect.right + 16, window.innerWidth - TOOLTIP_WIDTH - 16);
    tooltipStyle = { position: 'fixed', top, left, width: TOOLTIP_WIDTH };
  } else {
    tooltipStyle = {
      position: 'fixed', top: '50%', left: '50%',
      transform: 'translate(-50%, -50%)', width: TOOLTIP_WIDTH,
    };
  }

  return (
    <div className="onboarding-overlay">
      {rect && (
        <div
          className="onboarding-spotlight"
          style={{
            top: rect.top - 6,
            left: rect.left - 6,
            width: rect.width + 12,
            height: rect.height + 12,
          }}
        />
      )}
      <div className="onboarding-tooltip" style={tooltipStyle}>
        <div className="onboarding-tooltip-step">{stepIndex + 1} / {STEPS.length}</div>
        <div className="onboarding-tooltip-title">{t(step.titleKey)}</div>
        <div className="onboarding-tooltip-text">{t(step.textKey)}</div>
        <div className="onboarding-tooltip-actions">
          <button className="btn btn-secondary" onClick={onFinish}>{t('skip')}</button>
          <button className="btn btn-primary" onClick={handleNext}>{isLast ? t('finish') : t('next')}</button>
        </div>
      </div>
    </div>
  );
}
