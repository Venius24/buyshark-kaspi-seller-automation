import type { PriceFeedImportStep } from '../hooks/usePriceFeedImport';
import type { PriceFeedMerchant, PriceFeedPreviewResult, PriceFeedApplyResult } from '../types';

interface PriceFeedImportModalProps {
  step: PriceFeedImportStep;
  phone: string;
  onPhoneChange: (value: string) => void;
  password: string;
  onPasswordChange: (value: string) => void;
  merchants: PriceFeedMerchant[];
  preview: PriceFeedPreviewResult | null;
  applyResult: PriceFeedApplyResult | null;
  overwrite: boolean;
  onOverwriteChange: (value: boolean) => void;
  error: string;
  onClose: () => void;
  onSubmitCredentials: () => void;
  onChooseMerchant: (merchantId: string) => void;
  onApply: () => void;
  onFinish: () => void;
}

const SKIP_REASON_LABELS: Record<string, string> = {
  algorithm_off_no_limits: 'демпинг выключен в PriceFeed',
  no_min_or_max: 'не задана мин. или макс. цена',
  min_gte_max: 'мин. цена ≥ макс.',
  not_in_catalog: 'нет в нашем каталоге',
  duplicate_sku: 'дубликат SKU',
  unknown_algorithm: 'незнакомый алгоритм',
};

export function PriceFeedImportModal({
  step,
  phone,
  onPhoneChange,
  password,
  onPasswordChange,
  merchants,
  preview,
  applyResult,
  overwrite,
  onOverwriteChange,
  error,
  onClose,
  onSubmitCredentials,
  onChooseMerchant,
  onApply,
  onFinish,
}: PriceFeedImportModalProps) {
  if (step === 'idle') return null;

  return (
    <div className="modal-overlay">
      <div className="glass-card modal-content" style={{ maxWidth: '520px', width: '100%', padding: '30px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
          <h3 style={{ fontSize: '18px', fontWeight: 700 }}>📥 Импорт настроек демпинга из PriceFeed</h3>
          <button
            onClick={step === 'done' ? onFinish : onClose}
            style={{ background: 'transparent', border: 'none', color: '#fff', fontSize: '20px', cursor: 'pointer' }}
          >
            ✕
          </button>
        </div>

        {error && (
          <div style={{ padding: '10px 12px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239, 68, 68, 0.3)', color: '#fca5a5', borderRadius: '8px', marginBottom: '16px', fontSize: '13px' }}>
            {error}
          </div>
        )}

        {step === 'credentials' && (
          <div>
            <p style={{ fontSize: '13px', marginBottom: '16px', color: 'var(--text-muted)' }}>
              Введите телефон и пароль от вашего аккаунта PriceFeed.kz. Мы разово залогинимся под вашим аккаунтом,
              заберём min/max цены и алгоритмы демпинга — <strong>пароль нигде не сохраняется</strong>.
            </p>
            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>Телефон</label>
              <input
                type="tel"
                className="form-input"
                placeholder="+7 707 000 12 34"
                value={phone}
                autoComplete="off"
                onChange={(e) => onPhoneChange(e.target.value)}
              />
            </div>
            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>Пароль</label>
              <input
                type="password"
                className="form-input"
                placeholder="••••••••"
                value={password}
                autoComplete="current-password"
                onChange={(e) => onPasswordChange(e.target.value)}
              />
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '16px' }}>
              <input type="checkbox" required /> Я подтверждаю, что это мой аккаунт PriceFeed
            </label>
            <button className="btn btn-primary" style={{ width: '100%' }} onClick={onSubmitCredentials} disabled={!phone || !password}>
              Начать импорт
            </button>
          </div>
        )}

        {step === 'loading' && (
          <div style={{ textAlign: 'center', padding: '20px 0' }}>
            <div style={{ width: '32px', height: '32px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 16px auto' }} />
            <p style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
              Импорт может занять 1–2 минуты, не закрывайте окно...
            </p>
          </div>
        )}

        {step === 'choose_merchant' && (
          <div>
            <p style={{ fontSize: '13px', marginBottom: '16px', color: 'var(--text-muted)' }}>
              В этом аккаунте PriceFeed несколько магазинов — выберите нужный:
            </p>
            {merchants.map((m) => (
              <button
                key={m.id}
                className="btn btn-secondary"
                style={{ width: '100%', marginBottom: '8px', textAlign: 'left' }}
                onClick={() => onChooseMerchant(m.id)}
              >
                {m.name}
              </button>
            ))}
          </div>
        )}

        {step === 'preview' && preview && (
          <div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', marginBottom: '16px' }}>
              <div style={{ background: 'rgba(255,255,255,0.04)', borderRadius: '8px', padding: '10px 12px' }}>
                <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Найдено в PriceFeed</div>
                <div style={{ fontSize: '20px', fontWeight: 700 }}>{preview.total_fetched}</div>
              </div>
              <div style={{ background: 'rgba(16, 185, 129, 0.08)', borderRadius: '8px', padding: '10px 12px' }}>
                <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Можно импортировать</div>
                <div style={{ fontSize: '20px', fontWeight: 700, color: 'var(--success)' }}>{preview.importable}</div>
              </div>
            </div>

            {!!preview.already_configured && (
              <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '10px' }}>
                Из них у {preview.already_configured} уже есть настройки демпинга в нашей системе.
              </div>
            )}

            {!!preview.unmatched && (
              <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '10px' }}>
                {preview.unmatched} товар(ов) из PriceFeed не найдены в нашем каталоге — возможно, каталог не
                синхронизирован. Обновите товары и повторите импорт, если ожидали больше совпадений.
              </div>
            )}

            {preview.skipped && Object.keys(preview.skipped).length > 0 && (
              <details style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '12px' }}>
                <summary style={{ cursor: 'pointer' }}>Что пропущено и почему</summary>
                <ul style={{ marginTop: '6px', paddingLeft: '18px' }}>
                  {Object.entries(preview.skipped).map(([reason, count]) => (
                    <li key={reason}>{count} — {SKIP_REASON_LABELS[reason] || reason}</li>
                  ))}
                </ul>
              </details>
            )}

            {!!preview.warnings?.length && (
              <div style={{ fontSize: '12px', color: '#fbbf24', marginBottom: '12px' }}>
                {preview.warnings.map((w, i) => <div key={i}>⚠️ {w}</div>)}
              </div>
            )}

            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '12px' }}>
              Не переносится: интервал автообновления, фильтры конкурентов, город — эти настройки остаются как
              настроены у нас (либо по умолчанию).
            </div>

            {!!preview.sample?.length && (
              <div style={{ maxHeight: '180px', overflowY: 'auto', border: '1px solid rgba(255,255,255,0.06)', borderRadius: '8px', marginBottom: '16px' }}>
                <table style={{ width: '100%', fontSize: '12px', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ textAlign: 'left', color: 'var(--text-muted)' }}>
                      <th style={{ padding: '6px 8px' }}>SKU</th>
                      <th style={{ padding: '6px 8px' }}>Min</th>
                      <th style={{ padding: '6px 8px' }}>Max</th>
                      <th style={{ padding: '6px 8px' }}>Режим</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.sample.map((row) => (
                      <tr key={row.sku} style={{ borderTop: '1px solid rgba(255,255,255,0.04)' }}>
                        <td style={{ padding: '6px 8px' }}>{row.sku}{row.already_configured ? ' 🔁' : ''}</td>
                        <td style={{ padding: '6px 8px' }}>{row.min_price}</td>
                        <td style={{ padding: '6px 8px' }}>{row.max_price}</td>
                        <td style={{ padding: '6px 8px' }}>{row.mode}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <label style={{
              display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', marginBottom: '16px',
              opacity: preview.already_configured ? 1 : 0.5,
            }}>
              <input
                type="checkbox"
                checked={overwrite}
                disabled={!preview.already_configured}
                onChange={(e) => onOverwriteChange(e.target.checked)}
              />
              Перезаписать уже настроенные у нас товары ({preview.already_configured || 0})
            </label>

            <button className="btn btn-primary" style={{ width: '100%' }} onClick={onApply} disabled={!preview.importable}>
              Импортировать {preview.importable} товар(ов)
            </button>
          </div>
        )}

        {step === 'applying' && (
          <div style={{ textAlign: 'center', padding: '20px 0' }}>
            <div style={{ width: '32px', height: '32px', border: '3px solid rgba(255,255,255,0.1)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 16px auto' }} />
            <p style={{ fontSize: '13px', color: 'var(--text-muted)' }}>Записываем настройки...</p>
          </div>
        )}

        {step === 'done' && applyResult && (
          <div style={{ textAlign: 'center', padding: '10px 0' }}>
            <div style={{
              width: '48px', height: '48px',
              background: 'rgba(16, 185, 129, 0.1)',
              border: '2px solid var(--success)',
              borderRadius: '50%', display: 'inline-flex',
              justifyContent: 'center', alignItems: 'center',
              color: 'var(--success)', fontSize: '24px',
              margin: '0 auto 16px auto'
            }}>✓</div>
            <h4 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '8px' }}>Импорт завершён</h4>
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '20px' }}>
              Добавлено: {applyResult.created}, обновлено: {applyResult.updated}
              {applyResult.skipped_existing ? `, пропущено (уже настроено): ${applyResult.skipped_existing}` : ''}.
            </p>
            <button className="btn btn-primary" style={{ width: '100%' }} onClick={onFinish}>
              Закрыть
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
