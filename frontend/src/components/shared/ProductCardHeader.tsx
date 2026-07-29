interface ProductCardHeaderProps {
  name: string;
  sku: string;
  kaspiSku?: string;
  merchantSku?: string;
}

export function ProductCardHeader({ name, sku, kaspiSku, merchantSku }: ProductCardHeaderProps) {
  const displaySku = sku || kaspiSku;
  return (
    <>
      <h3 style={{ fontSize: '14px', fontWeight: 600, color: '#fff' }}>{name}</h3>
      <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>
        Kaspi SKU: <strong>{displaySku}</strong>
        {merchantSku && merchantSku !== displaySku && (
          <span style={{ marginLeft: '10px' }}>
            • Merchant SKU: <strong style={{ color: '#818cf8' }}>{merchantSku}</strong>
          </span>
        )}
      </p>
    </>
  );
}
