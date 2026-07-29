import { useState } from 'react';

const INITIAL_LOGS = [
  'Система автоматизации инициализирована.',
  'Ожидание подключения Kaspi API...',
];

export function useLogs() {
  const [logs, setLogs] = useState<string[]>(INITIAL_LOGS);

  const addLog = (message: string) => {
    const timestamp = new Date().toLocaleTimeString();
    setLogs((prev) => [`[${timestamp}] ${message}`, ...prev.slice(0, 49)]);
  };

  const clearLogs = () => {
    setLogs(['Журнал событий очищен.']);
  };

  return { logs, addLog, clearLogs };
}
