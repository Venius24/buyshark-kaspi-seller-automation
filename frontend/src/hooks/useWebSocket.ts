import { useEffect, useState } from 'react';

export interface TelemetryEvent {
  type: string;
  timestamp: number;
  data: any;
}

export function useWebSocket(url: string) {
  const [isConnected, setIsConnected] = useState(false);
  const [latest, setLatest] = useState<{ url: string; event: TelemetryEvent } | null>(null);

  useEffect(() => {
    let disposed = false;
    let ws: WebSocket | null = null;
    let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (disposed) return;
      try {
        const socket = new WebSocket(url);
        ws = socket;

        socket.onopen = () => {
          if (!disposed) setIsConnected(true);
        };

        socket.onmessage = (message) => {
          if (disposed) return;
          try {
            setLatest({ url, event: JSON.parse(message.data) });
          } catch {
            // Ignore text messages
          }
        };

        socket.onclose = () => {
          if (disposed) return;
          setIsConnected(false);
          reconnectTimeout = setTimeout(connect, 3000);
        };

        socket.onerror = () => socket.close();
      } catch {
        setIsConnected(false);
        reconnectTimeout = setTimeout(connect, 3000);
      }
    };

    setIsConnected(false);
    connect();
    return () => {
      disposed = true;
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
      ws?.close();
    };
  }, [url]);

  return { isConnected, lastEvent: latest?.url === url ? latest.event : null };
}
