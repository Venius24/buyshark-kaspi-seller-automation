// Тарификация: все суммы с бэкенда приходят в тиын (1/100 ₸), BigInteger —
// единственное место форматирования, чтобы не плодить /100 по компонентам.
export function formatTenge(minor: number): string {
  const value = minor / 100;
  return `${value.toLocaleString('ru-RU', { maximumFractionDigits: 0 })} ₸`;
}
