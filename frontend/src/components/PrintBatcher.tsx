import React, { useState, useEffect, useRef } from 'react';
import { apiFetch, API_URL } from '../api';

interface GeometryAudit {
  is_valid: boolean;
  actual_dimensions: string;
  mediabox_pt: string;
  audit_message: string;
}

interface BatchInfo {
  category: string;
  title: string;
  icon: string;
  printer_type: string;
  file_name: string;
  total_pages: number;
  download_url: string;
  preview_url: string;
  sample_dimensions: string;
  is_express?: boolean;
  geometry_audit?: GeometryAudit;
}

interface ManifestResponse {
  batch_id: string;
  original_filename: string;
  total_files: number;
  total_pages: number;
  deduplicated_pages: number;
  new_pages: number;
  all_duplicates: boolean;
  sort_preset: string;
  auto_rotate: boolean;
  express_mode: string;
  output_layout: string;
  deduplicate_history: boolean;
  batches: Record<string, BatchInfo>;
}

export const PrintBatcher: React.FC = () => {
  const [file, setFile] = useState<File | null>(null);
  const [isDragOver, setIsDragOver] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(false);
  const [sortPreset, setSortPreset] = useState<string>('filename');
  const [autoRotate, setAutoRotate] = useState<boolean>(true);
  const [expressMode, setExpressMode] = useState<string>('separate');
  const [outputLayout, setOutputLayout] = useState<string>('auto'); // 'auto' | 'a4_4' | 'a4_8' | 'a4_9' | 'a4_1' | 'thermal_75_120' | 'thermal_100_150' | 'just_merge'
  const [deduplicateHistory, setDeduplicateHistory] = useState<boolean>(true);
  const [resultManifest, setResultManifest] = useState<ManifestResponse | null>(null);
  const [printedCount, setPrintedCount] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchHistoryStats = async () => {
    try {
      const res = await apiFetch(`${API_URL}/api/print-batcher/history-stats`);
      if (res.ok) {
        const data = await res.json();
        setPrintedCount(data.printed_count || 0);
      }
    } catch {
      // Ignore if server restarting
    }
  };

  useEffect(() => {
    fetchHistoryStats();
  }, []);

  const handleResetHistory = async () => {
    if (!window.confirm('Вы уверены, что хотите сбросить историю напечатанных заказов за сегодня?')) {
      return;
    }
    try {
      const res = await apiFetch(`${API_URL}/api/print-batcher/reset-history`, { method: 'POST' });
      if (res.ok) {
        setPrintedCount(0);
        alert('История печати за сегодня сброшена');
      }
    } catch {
      alert('Ошибка при сбросе истории');
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
      setError(null);
    }
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragOver(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      const dropped = e.dataTransfer.files[0];
      if (dropped.name.endsWith('.zip') || dropped.name.endsWith('.pdf')) {
        setFile(dropped);
        setError(null);
      } else {
        setError('Поддерживаются только ZIP-архивы и PDF-файлы');
      }
    }
  };

  const handleProcess = async (forceReprint: boolean = false) => {
    if (!file) {
      setError('Пожалуйста, выберите ZIP-архив или PDF-файл');
      return;
    }

    setLoading(true);
    setError(null);
    setResultManifest(null);

    const formData = new FormData();
    formData.append('file', file);
    formData.append('sort_preset', sortPreset);
    formData.append('auto_rotate', String(autoRotate));
    formData.append('express_mode', expressMode);
    formData.append('output_layout', outputLayout);
    formData.append('deduplicate_history', String(deduplicateHistory));
    formData.append('force_reprint', String(forceReprint));

    try {
      const response = await apiFetch(`${API_URL}/api/print-batcher/process`, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const errData = await response.json();
        throw new Error(errData.detail || 'Ошибка при обработке печатного пакета');
      }

      const data: ManifestResponse = await response.json();
      setResultManifest(data);
      fetchHistoryStats();
    } catch (err: any) {
      setError(err.message || 'Произошла непредвиденная ошибка');
    } finally {
      setLoading(false);
    }
  };

  const handlePrint = (previewEndpoint: string) => {
    const fullUrl = `${API_URL}${previewEndpoint}`;
    const win = window.open(fullUrl, '_blank');
    if (win) {
      win.focus();
    }
  };

  return (
    <div style={styles.container}>
      {/* Header Banner */}
      <div style={styles.header}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
          <div style={styles.headerTitleGroup}>
            <div style={styles.headerIcon}>🖨️</div>
            <div>
              <h2 style={styles.headerTitle}>Print-Batcher Engine (Автосборщик печати)</h2>
              <p style={styles.headerSubtitle}>
                Подгонка накладных 100% без полей, мульти-раскладка (4/8/9 на A4), суточная дедупликация и аудит MediaBox.
              </p>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div style={styles.historyStatBadge}>
              🏷️ Напечатано за сегодня: <strong>{printedCount} заказов</strong>
            </div>
            <button onClick={handleResetHistory} style={styles.resetHistoryBtn} title="Сбросить историю печати за сегодня">
              ♻️ Сбросить историю
            </button>
          </div>
        </div>
      </div>

      {/* Control & Upload Panel */}
      <div style={styles.card}>
        {/* N-up Layout Selection Buttons Grid */}
        <div style={styles.layoutSelectorCard}>
          <div style={styles.layoutSelectorHeader}>
            <span style={{ fontSize: '18px' }}>⚡</span>
            <span style={{ fontWeight: 700, fontSize: '15px', color: '#ffffff' }}>
              Формат обработки по умолчанию (Выберите режим раскладки):
            </span>
          </div>

          <div style={styles.layoutButtonsGrid}>
            <button
              type="button"
              style={{
                ...styles.layoutBtn,
                ...(outputLayout === 'a4_4' ? styles.layoutBtnActive : {}),
              }}
              onClick={() => setOutputLayout('a4_4')}
            >
              4 на A4
            </button>

            <button
              type="button"
              style={{
                ...styles.layoutBtn,
                ...(outputLayout === 'a4_8' ? styles.layoutBtnActive : {}),
              }}
              onClick={() => setOutputLayout('a4_8')}
            >
              8 на A4
            </button>

            <button
              type="button"
              style={{
                ...styles.layoutBtn,
                ...(outputLayout === 'a4_9' ? styles.layoutBtnActive : {}),
              }}
              onClick={() => setOutputLayout('a4_9')}
            >
              9 на A4
            </button>

            <button
              type="button"
              style={{
                ...styles.layoutBtn,
                ...(outputLayout === 'a4_1' ? styles.layoutBtnActive : {}),
              }}
              onClick={() => setOutputLayout('a4_1')}
            >
              1 на A4
            </button>

            <button
              type="button"
              style={{
                ...styles.layoutBtn,
                ...(outputLayout === 'thermal_75_120' ? styles.layoutBtnActive : {}),
              }}
              onClick={() => setOutputLayout('thermal_75_120')}
            >
              Термопринтер 75/120
            </button>

            <button
              type="button"
              style={{
                ...styles.layoutBtn,
                ...(outputLayout === 'thermal_100_150' ? styles.layoutBtnActive : {}),
              }}
              onClick={() => setOutputLayout('thermal_100_150')}
            >
              Термопринтер 100/150
            </button>

            <button
              type="button"
              style={{
                ...styles.layoutBtnFull,
                ...(outputLayout === 'just_merge' ? styles.layoutBtnActive : {}),
              }}
              onClick={() => setOutputLayout('just_merge')}
            >
              Просто объединить
            </button>

            <button
              type="button"
              style={{
                ...styles.layoutBtnFull,
                ...(outputLayout === 'auto' ? styles.layoutBtnActive : {}),
              }}
              onClick={() => setOutputLayout('auto')}
            >
              ⚡ Авто-категории оборудования (Раздельный пакет)
            </button>
          </div>
        </div>

        {/* Secondary Settings bar */}
        <div style={styles.settingsGrid}>
          <div style={styles.settingItem}>
            <label style={styles.settingLabel}>⚡ Обработка Экспресс-заказов (Kaspi Express):</label>
            <select
              value={expressMode}
              onChange={(e) => setExpressMode(e.target.value)}
              style={{ ...styles.selectInput, borderColor: '#f59e0b', color: '#fef08a' }}
            >
              <option value="separate">⚡ Выделять Экспресс в отдельные VIP-батчи (Рекомендуется)</option>
              <option value="exclude">🚫 Исключать Экспресс-заказы из печати</option>
              <option value="ignore">📦 Печатать все заказы вместе (Без разделения)</option>
            </select>
          </div>

          <div style={styles.settingItem}>
            <label style={styles.settingLabel}>📋 Порядок сортировки внутри батчей:</label>
            <select
              value={sortPreset}
              onChange={(e) => setSortPreset(e.target.value)}
              style={styles.selectInput}
            >
              <option value="filename">По имени исходного файла (алфавитная/числовая)</option>
              <option value="order_id">По номеру заказа (Kaspi Order ID)</option>
              <option value="original">Оригинальный порядок из архива</option>
            </select>
          </div>

          <div style={styles.settingItem}>
            <label style={styles.settingLabel}>🛡️ Дедупликация & Ориентация:</label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              <div style={styles.toggleRow}>
                <input
                  type="checkbox"
                  id="deduplicateHistory"
                  checked={deduplicateHistory}
                  onChange={(e) => setDeduplicateHistory(e.target.checked)}
                  style={styles.checkbox}
                />
                <label htmlFor="deduplicateHistory" style={{ cursor: 'pointer', fontSize: '13px', color: '#34d399', fontWeight: 600 }}>
                  Исключать повторные заказы из прошлых печатей
                </label>
              </div>

              <div style={styles.toggleRow}>
                <input
                  type="checkbox"
                  id="autoRotate"
                  checked={autoRotate}
                  onChange={(e) => setAutoRotate(e.target.checked)}
                  style={styles.checkbox}
                />
                <label htmlFor="autoRotate" style={{ cursor: 'pointer', fontSize: '13px', color: '#e2e8f0' }}>
                  Поворачивать альбомные страницы под ленту
                </label>
              </div>
            </div>
          </div>
        </div>

        {/* Dropzone */}
        <div
          style={{
            ...styles.dropzone,
            ...(isDragOver ? styles.dropzoneActive : {}),
            ...(file ? styles.dropzoneFilled : {}),
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setIsDragOver(true);
          }}
          onDragLeave={() => setIsDragOver(false)}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <input
            type="file"
            ref={fileInputRef}
            onChange={handleFileChange}
            accept=".zip,.pdf"
            style={{ display: 'none' }}
          />

          <div style={styles.dropzoneContent}>
            <div style={styles.dropzoneIcon}>{file ? '📦' : '📁'}</div>
            {file ? (
              <div>
                <div style={styles.fileName}>{file.name}</div>
                <div style={styles.fileSize}>{(file.size / (1024 * 1024)).toFixed(2)} МБ</div>
              </div>
            ) : (
              <div>
                <div style={styles.dropzoneTitle}>Перетащите сюда ZIP-архив с накладными из Kaspi / 1С</div>
                <div style={styles.dropzoneSub}>или кликните для выбора файла (.zip, .pdf)</div>
              </div>
            )}
          </div>
        </div>

        {/* Error message */}
        {error && <div style={styles.errorBox}>⚠️ {error}</div>}

        {/* Action Button */}
        <div style={{ marginTop: '20px', textAlign: 'right' }}>
          <button
            onClick={() => handleProcess(false)}
            disabled={!file || loading}
            style={{
              ...styles.primaryBtn,
              ...(!file || loading ? styles.btnDisabled : {}),
            }}
          >
            {loading ? '⏳ Обрезка полей и подгонка...' : '⚡ Сформировать пакеты для печати'}
          </button>
        </div>
      </div>

      {/* Results View */}
      {resultManifest && (
        <div style={{ marginTop: '30px' }}>
          <div style={styles.summaryBar}>
            <div>
              <div style={styles.summaryBadge}>
                ✅ Обработано архивов: <strong>{resultManifest.total_files} файлов</strong> (Всего в архиве: {resultManifest.total_pages} листов)
              </div>
              <div style={{ fontSize: '13px', color: '#cbd5e1', marginTop: '4px' }}>
                🛑 Исключено ранее напечатанных дубликатов: <strong style={{ color: '#f87171' }}>{resultManifest.deduplicated_pages} страниц</strong> | 
                ✨ Новых листов в PDF: <strong style={{ color: '#34d399' }}>{resultManifest.new_pages} страниц</strong> | 
                Раскладка: <code>{resultManifest.output_layout}</code>
              </div>
            </div>
            <div style={{ fontSize: '12px', color: '#94a3b8', textAlign: 'right' }}>
              Батч-ID: <code>{resultManifest.batch_id.substring(0, 8)}</code>
            </div>
          </div>

          {/* All duplicates warning banner */}
          {resultManifest.all_duplicates && (
            <div style={styles.allDuplicatesBanner}>
              <div style={{ fontSize: '16px', fontWeight: 700, color: '#fef08a' }}>
                ⚠️ Все {resultManifest.total_pages} заказов из этого архива уже были напечатаны ранее!
              </div>
              <p style={{ fontSize: '13px', margin: '6px 0 14px 0', color: '#fef3c7' }}>
                Система отфильтровала все старые накладные. Если вы хотите принудительно перепечатать весь этот архив заново — нажмите кнопку ниже:
              </p>
              <button
                onClick={() => handleProcess(true)}
                style={styles.forceReprintBtn}
              >
                🖨️ Напечатать весь архив заново принудительно
              </button>
            </div>
          )}

          {/* Cards list */}
          {Object.keys(resultManifest.batches).length > 0 && (
            <>
              <h3 style={styles.sectionTitle}>🖨️ Скомпонованные файлы к печати по категориям оборудования:</h3>

              <div style={styles.cardsGrid}>
                {Object.values(resultManifest.batches).map((batch) => {
                  const isExpress = batch.is_express || batch.category.startsWith('EXPRESS_');
                  return (
                    <div
                      key={batch.category}
                      style={{
                        ...styles.batchCard,
                        ...(isExpress ? styles.expressBatchCard : {}),
                      }}
                    >
                      {isExpress && (
                        <div style={styles.expressBadge}>⚡ СРОЧНЫЙ БАТЧ (KASPI EXPRESS)</div>
                      )}

                      <div style={styles.batchHeader}>
                        <span style={styles.batchIcon}>{batch.icon}</span>
                        <div>
                          <h4 style={styles.batchTitle}>{batch.title}</h4>
                          <div style={styles.batchSubtitle}>{batch.printer_type}</div>
                        </div>
                      </div>

                      {/* Audit badge */}
                      {batch.geometry_audit && (
                        <div
                          style={{
                            ...styles.auditBadge,
                            ...(batch.geometry_audit.is_valid ? {} : styles.auditBadgeError),
                          }}
                        >
                          {batch.geometry_audit.audit_message}
                        </div>
                      )}

                      <div style={styles.batchMetrics}>
                        <div style={styles.metricBadge}>
                          <span>Страниц в PDF:</span> <strong>{batch.total_pages} шт</strong>
                        </div>
                        <div style={styles.metricBadge}>
                          <span>Размер MediaBox:</span> <strong>{batch.sample_dimensions}</strong>
                        </div>
                      </div>

                      <div style={styles.batchActions}>
                        <button
                          onClick={() => handlePrint(batch.preview_url)}
                          style={{
                            ...styles.printBtn,
                            ...(isExpress ? { background: '#f59e0b', color: '#0f172a' } : {}),
                          }}
                        >
                          🖨️ Предпросмотр / Печать
                        </button>

                        <a
                          href={`${API_URL}${batch.download_url}`}
                          target="_blank"
                          rel="noreferrer"
                          style={styles.downloadBtn}
                        >
                          ⬇️ Скачать PDF
                        </a>
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      )}

      {/* PDF Modal Preview */}
      {previewUrl && (
        <div style={styles.modalOverlay} onClick={() => setPreviewUrl(null)}>
          <div style={styles.modalContent} onClick={(e) => e.stopPropagation()}>
            <div style={styles.modalHeader}>
              <span>Предпросмотр скомпонованного печатного файла</span>
              <button style={styles.modalCloseBtn} onClick={() => setPreviewUrl(null)}>
                ✖
              </button>
            </div>
            <iframe src={previewUrl} style={styles.iframePreview} title="Print Batch Preview" />
          </div>
        </div>
      )}
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  container: {
    padding: '24px',
    maxWidth: '1200px',
    margin: '0 auto',
    color: '#f8fafc',
    fontFamily: 'Inter, system-ui, -apple-system, sans-serif',
  },
  header: {
    background: 'linear-gradient(135deg, #1e1b4b 0%, #312e81 100%)',
    borderRadius: '16px',
    padding: '24px 32px',
    marginBottom: '24px',
    border: '1px solid #4338ca33',
    boxShadow: '0 10px 30px -10px rgba(49, 46, 129, 0.4)',
  },
  headerTitleGroup: {
    display: 'flex',
    alignItems: 'center',
    gap: '20px',
  },
  headerIcon: {
    fontSize: '42px',
    background: '#3730a3',
    padding: '12px 16px',
    borderRadius: '14px',
  },
  headerTitle: {
    margin: 0,
    fontSize: '24px',
    fontWeight: 700,
    color: '#ffffff',
  },
  headerSubtitle: {
    margin: '6px 0 0 0',
    color: '#c7d2fe',
    fontSize: '14px',
    lineHeight: 1.5,
  },
  historyStatBadge: {
    background: '#312e81',
    border: '1px solid #6366f166',
    padding: '8px 14px',
    borderRadius: '8px',
    fontSize: '13px',
    color: '#e0e7ff',
  },
  resetHistoryBtn: {
    background: 'rgba(239, 68, 68, 0.15)',
    border: '1px solid rgba(239, 68, 68, 0.3)',
    color: '#fca5a5',
    padding: '8px 14px',
    borderRadius: '8px',
    fontSize: '12px',
    fontWeight: 600,
    cursor: 'pointer',
  },
  card: {
    background: '#0f172a',
    borderRadius: '16px',
    padding: '24px',
    border: '1px solid #1e293b',
    boxShadow: '0 4px 20px rgba(0, 0, 0, 0.2)',
  },
  layoutSelectorCard: {
    background: '#1e293b80',
    border: '1px solid #334155',
    borderRadius: '12px',
    padding: '20px',
    marginBottom: '20px',
  },
  layoutSelectorHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    marginBottom: '14px',
  },
  layoutButtonsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
    gap: '12px',
  },
  layoutBtn: {
    background: '#475569',
    color: '#ffffff',
    border: 'none',
    borderRadius: '10px',
    padding: '14px 12px',
    fontSize: '14px',
    fontWeight: 700,
    cursor: 'pointer',
    textAlign: 'center',
    transition: 'all 0.2s ease',
  },
  layoutBtnFull: {
    gridColumn: 'span 2',
    background: '#334155',
    color: '#ffffff',
    border: 'none',
    borderRadius: '10px',
    padding: '14px 12px',
    fontSize: '14px',
    fontWeight: 700,
    cursor: 'pointer',
    textAlign: 'center',
    transition: 'all 0.2s ease',
  },
  layoutBtnActive: {
    background: 'linear-gradient(135deg, #10b981 0%, #059669 100%)',
    boxShadow: '0 4px 14px rgba(16, 185, 129, 0.4)',
    transform: 'scale(1.02)',
  },
  settingsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))',
    gap: '20px',
    marginBottom: '20px',
  },
  settingItem: {
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
  },
  settingLabel: {
    fontSize: '13px',
    fontWeight: 600,
    color: '#94a3b8',
  },
  selectInput: {
    background: '#1e293b',
    color: '#f8fafc',
    border: '1px solid #334155',
    borderRadius: '8px',
    padding: '10px 14px',
    fontSize: '13px',
    outline: 'none',
    cursor: 'pointer',
  },
  toggleRow: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    background: '#1e293b',
    padding: '10px 14px',
    borderRadius: '8px',
    border: '1px solid #334155',
  },
  checkbox: {
    width: '18px',
    height: '18px',
    accentColor: '#10b981',
    cursor: 'pointer',
  },
  dropzone: {
    border: '2px dashed #334155',
    borderRadius: '12px',
    padding: '40px 20px',
    textAlign: 'center',
    cursor: 'pointer',
    background: '#1e293b40',
    transition: 'all 0.2s ease',
  },
  dropzoneActive: {
    borderColor: '#6366f1',
    background: '#6366f115',
  },
  dropzoneFilled: {
    borderColor: '#10b981',
    background: '#10b98110',
  },
  dropzoneContent: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: '12px',
  },
  dropzoneIcon: {
    fontSize: '48px',
  },
  dropzoneTitle: {
    fontSize: '16px',
    fontWeight: 600,
    color: '#e2e8f0',
  },
  dropzoneSub: {
    fontSize: '13px',
    color: '#64748b',
    marginTop: '4px',
  },
  fileName: {
    fontSize: '16px',
    fontWeight: 600,
    color: '#10b981',
  },
  fileSize: {
    fontSize: '13px',
    color: '#94a3b8',
    marginTop: '2px',
  },
  errorBox: {
    marginTop: '16px',
    background: '#451a1a',
    border: '1px solid #991b1b',
    color: '#fca5a5',
    padding: '12px 16px',
    borderRadius: '8px',
    fontSize: '14px',
  },
  primaryBtn: {
    background: 'linear-gradient(135deg, #6366f1 0%, #4f46e5 100%)',
    color: '#ffffff',
    border: 'none',
    borderRadius: '10px',
    padding: '14px 28px',
    fontSize: '15px',
    fontWeight: 600,
    cursor: 'pointer',
    boxShadow: '0 4px 14px rgba(99, 102, 241, 0.4)',
    transition: 'transform 0.1s ease',
  },
  btnDisabled: {
    opacity: 0.5,
    cursor: 'not-allowed',
    boxShadow: 'none',
  },
  summaryBar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    background: '#1e293b',
    padding: '16px 20px',
    borderRadius: '12px',
    marginBottom: '20px',
    border: '1px solid #334155',
  },
  summaryBadge: {
    fontSize: '15px',
    color: '#10b981',
  },
  allDuplicatesBanner: {
    background: 'linear-gradient(135deg, #451a03 0%, #78350f 100%)',
    border: '2px solid #f59e0b',
    borderRadius: '12px',
    padding: '20px',
    marginBottom: '20px',
  },
  forceReprintBtn: {
    background: '#f59e0b',
    color: '#0f172a',
    border: 'none',
    borderRadius: '8px',
    padding: '10px 18px',
    fontSize: '13px',
    fontWeight: 700,
    cursor: 'pointer',
  },
  sectionTitle: {
    margin: '0 0 16px 0',
    fontSize: '18px',
    fontWeight: 600,
    color: '#f8fafc',
  },
  cardsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(350px, 1fr))',
    gap: '20px',
  },
  batchCard: {
    background: '#0f172a',
    border: '1px solid #1e293b',
    borderRadius: '14px',
    padding: '20px',
    display: 'flex',
    flexDirection: 'column',
    gap: '16px',
    boxShadow: '0 4px 12px rgba(0, 0, 0, 0.15)',
    position: 'relative',
  },
  expressBatchCard: {
    background: 'linear-gradient(135deg, #2a1600 0%, #1e1b4b 100%)',
    border: '2px solid #f59e0b',
    boxShadow: '0 4px 20px rgba(245, 158, 11, 0.3)',
  },
  expressBadge: {
    background: '#f59e0b',
    color: '#0f172a',
    fontWeight: 800,
    fontSize: '11px',
    padding: '4px 10px',
    borderRadius: '6px',
    letterSpacing: '0.5px',
    alignSelf: 'flex-start',
  },
  auditBadge: {
    background: '#064e3b',
    border: '1px solid #059669',
    color: '#6ee7b7',
    padding: '8px 12px',
    borderRadius: '8px',
    fontSize: '12px',
    fontWeight: 600,
  },
  auditBadgeError: {
    background: '#451a1a',
    border: '1px solid #991b1b',
    color: '#fca5a5',
  },
  batchHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: '14px',
  },
  batchIcon: {
    fontSize: '32px',
    background: '#1e293b',
    padding: '8px 12px',
    borderRadius: '10px',
  },
  batchTitle: {
    margin: 0,
    fontSize: '16px',
    fontWeight: 600,
    color: '#ffffff',
  },
  batchSubtitle: {
    margin: '2px 0 0 0',
    fontSize: '12px',
    color: '#94a3b8',
  },
  batchMetrics: {
    display: 'flex',
    gap: '10px',
  },
  metricBadge: {
    background: '#1e293b',
    padding: '6px 12px',
    borderRadius: '6px',
    fontSize: '12px',
    color: '#cbd5e1',
  },
  batchActions: {
    display: 'flex',
    gap: '10px',
    marginTop: 'auto',
  },
  printBtn: {
    flex: 1,
    background: '#3b82f6',
    color: '#ffffff',
    border: 'none',
    borderRadius: '8px',
    padding: '10px',
    fontSize: '13px',
    fontWeight: 600,
    cursor: 'pointer',
    textAlign: 'center',
  },
  downloadBtn: {
    flex: 1,
    background: '#10b981',
    color: '#ffffff',
    borderRadius: '8px',
    padding: '10px',
    fontSize: '13px',
    fontWeight: 600,
    textDecoration: 'none',
    textAlign: 'center',
    display: 'inline-block',
  },
  modalOverlay: {
    position: 'fixed',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    background: 'rgba(0, 0, 0, 0.8)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 9999,
  },
  modalContent: {
    width: '90%',
    height: '90%',
    background: '#0f172a',
    borderRadius: '12px',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },
  modalHeader: {
    padding: '16px 20px',
    background: '#1e293b',
    color: '#ffffff',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    fontSize: '16px',
    fontWeight: 600,
  },
  modalCloseBtn: {
    background: 'transparent',
    border: 'none',
    color: '#94a3b8',
    fontSize: '20px',
    cursor: 'pointer',
  },
  iframePreview: {
    width: '100%',
    height: '100%',
    border: 'none',
  },
};
