import { useEffect, useState } from 'react';

let listeners: Array<(url: string | null) => void> = [];

/** Открыть картинку на весь экран по клику — вызывается из любого места без контекста/пропсов. */
export function openImageLightbox(url: string) {
  listeners.forEach((l) => l(url));
}

/** Монтируется один раз в корне приложения. */
export function ImageLightbox() {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    const listener = (u: string | null) => setUrl(u);
    listeners.push(listener);
    return () => {
      listeners = listeners.filter((l) => l !== listener);
    };
  }, []);

  useEffect(() => {
    if (!url) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setUrl(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [url]);

  if (!url) return null;

  return (
    <div
      onClick={() => setUrl(null)}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.85)',
        zIndex: 10000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        cursor: 'zoom-out',
        padding: '32px',
      }}
    >
      <img
        src={url}
        alt=""
        style={{
          maxWidth: '90vw',
          maxHeight: '90vh',
          borderRadius: '12px',
          boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
          objectFit: 'contain',
        }}
      />
    </div>
  );
}
