import { useState } from 'react';
import { pricefeedPreview, pricefeedApply } from '../queries';
import type { PriceFeedMerchant, PriceFeedPreviewResult, PriceFeedApplyResult } from '../types';

export type PriceFeedImportStep =
  | 'idle'
  | 'credentials'
  | 'loading'
  | 'choose_merchant'
  | 'preview'
  | 'applying'
  | 'done';

// Пароль от PriceFeed живёт только в этом состоянии хука (не в react-query
// кэше — тот персистится в localStorage, см. queryClient.ts) и чистится
// сразу после того, как выгрузка завершилась (успешно или с ошибкой).
export function usePriceFeedImport(onImported: () => void) {
  const [step, setStep] = useState<PriceFeedImportStep>('idle');
  const [phone, setPhone] = useState('');
  const [password, setPassword] = useState('');
  const [merchants, setMerchants] = useState<PriceFeedMerchant[]>([]);
  const [preview, setPreview] = useState<PriceFeedPreviewResult | null>(null);
  const [applyResult, setApplyResult] = useState<PriceFeedApplyResult | null>(null);
  const [overwrite, setOverwrite] = useState(false);
  // По умолчанию импорт спящий — см. комментарий у activate в
  // backend/routers/pricefeed_import.py: включать сотни товаров разом нельзя,
  // квота Kaspi на запись офферов такого залпа не выдержит.
  const [activate, setActivate] = useState(false);
  const [error, setError] = useState('');

  const openModal = () => setStep('credentials');

  const reset = () => {
    setStep('idle');
    setPhone('');
    setPassword('');
    setMerchants([]);
    setPreview(null);
    setApplyResult(null);
    setOverwrite(false);
    setActivate(false);
    setError('');
  };

  const closeModal = () => reset();

  const runPreview = async (merchantId?: string) => {
    setError('');
    setStep('loading');
    try {
      const result = await pricefeedPreview(phone, password, merchantId);
      if (result.status === 'choose_merchant') {
        setMerchants(result.merchants || []);
        setStep('choose_merchant');
        return;
      }
      setPreview(result);
      setStep('preview');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка импорта из PriceFeed');
      setStep('credentials');
    } finally {
      // Пароль своё дело сделал — за пределами шага credentials он больше
      // не нужен ни для чего, кроме choose_merchant (перезапрос с тем же
      // паролем), поэтому не чистим его тут, только на закрытии/успехе.
    }
  };

  const submitCredentials = () => runPreview();
  const chooseMerchant = (merchantId: string) => runPreview(merchantId);

  const applyImport = async () => {
    if (!preview?.import_id) return;
    setError('');
    setStep('applying');
    try {
      const result = await pricefeedApply(preview.import_id, overwrite, activate);
      setApplyResult(result);
      setPassword('');
      setStep('done');
      onImported();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось применить импорт');
      setStep('preview');
    }
  };

  const finish = () => reset();

  return {
    step,
    phone,
    setPhone,
    password,
    setPassword,
    merchants,
    preview,
    applyResult,
    overwrite,
    setOverwrite,
    activate,
    setActivate,
    error,
    openModal,
    closeModal,
    submitCredentials,
    chooseMerchant,
    applyImport,
    finish,
  };
}
