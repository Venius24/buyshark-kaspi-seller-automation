interface ParcelLoaderProps {
  size?: number;
  color?: string;
}

/** Индикатор загрузки для состояний фетча (списки товаров, заказы и т.п.) — иконка посылки, переворачивающаяся по оси Y. */
export function ParcelLoader({ size = 32, color = 'var(--primary)' }: ParcelLoaderProps) {
  return (
    <div style={{ width: size, height: size, color, margin: '0 auto', perspective: 180 }}>
      <svg
        className="parcel-loader-svg"
        viewBox="0 0 48 48"
        fill="none"
        style={{ width: '100%', height: '100%', animation: 'parcel-flip 1.6s ease-in-out infinite', transformStyle: 'preserve-3d' }}
      >
        <path d="M24 6L42 15V33L24 42L6 33V15L24 6Z" stroke="currentColor" strokeWidth="2.5" strokeLinejoin="round" />
        <path d="M6 15L24 24L42 15" stroke="currentColor" strokeWidth="2.5" strokeLinejoin="round" />
        <path d="M24 24V42" stroke="currentColor" strokeWidth="2.5" />
      </svg>
    </div>
  );
}
