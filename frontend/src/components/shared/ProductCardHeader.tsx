interface ProductCardHeaderProps {
  name: string;
  sku: string;
  kaspiSku?: string;
  merchantSku?: string;
  image?: string;
}

export function ProductCardHeader({ name, sku, kaspiSku, merchantSku, image }: ProductCardHeaderProps) {
  const displaySku = sku || kaspiSku;
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '10px' }}>
      {image ? (
        <img
          src={image}
          alt=""
          loading="lazy"
          style={{ width: '60px', height: '60px', borderRadius: '10px', objectFit: 'cover', flexShrink: 0, background: 'rgba(255,255,255,0.04)' }}
          onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = 'hidden'; }}
        />
      ) : (
        <div style={{ width: '60px', height: '60px', borderRadius: '10px', background: 'rgba(255,255,255,0.04)', flexShrink: 0 }} />
      )}
      <div>
        <h3 style={{ fontSize: '14px', fontWeight: 600, color: '#fff' }}>{name}</h3>
        <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>
          Kaspi SKU: <strong>{displaySku}</strong>
          {merchantSku && merchantSku !== displaySku && (
            <span style={{ marginLeft: '10px' }}>
              • Merchant SKU: <strong style={{ color: '#818cf8' }}>{merchantSku}</strong>
            </span>
          )}
        </p>
      </div>
    </div>
  );
}
