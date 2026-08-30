// Единственный источник контактных данных на лендинге. Ни один компонент не
// хардкодит номер/юзернейм напрямую — только через CONTACTS / waLink() / tgLink().
// Ревью: grep -rn "wa.me\|t.me/" --include=*.tsx --include=*.ts src — совпадений
// вне этого файла быть не должно.

export const CONTACTS = {
  whatsappPhone: '77009990011',
  whatsappDisplay: '+7 700 999 00 11',
  telegram: 'buysharkkz',
  workHours: 'Пн–Пт, 10:00–23:00 (обычно отвечаем 24/7)',
} as const;

/**
 * source — метка места, откуда написали. Попадает в предзаполненный текст
 * сообщения, и это единственная доступная нам аналитика по каналам:
 * системы аналитики в проекте нет, а по тексту первого сообщения сразу
 * видно, с какой кнопки пришёл человек.
 */
export function waLink(source: string, text: string): string {
  const msg = `${text}\n\n[${source}]`;
  return `https://wa.me/${CONTACTS.whatsappPhone}?text=${encodeURIComponent(msg)}`;
}

export const tgLink = () => `https://t.me/${CONTACTS.telegram}`;
