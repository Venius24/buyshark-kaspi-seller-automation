import React, { useState, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch, API_URL } from '../api';
import { useActiveStoreId } from '../storeContext';

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

const fetchHistoryStats = async (): Promise<number> => {
  try {
    const res = await apiFetch(`${API_URL}/api/print-batcher/history-stats`);
    if (res.ok) {
      const data = await res.json();
      return data.printed_count || 0;
    }
  } catch {
    // Ignore if server restarting
  }
  return 0;
};

export const PrintBatcher: React.FC = () => {
  const queryClient = useQueryClient();
  const activeStoreId = useActiveStoreId();
  const historyStatsKey = ['print-batcher-history-stats', activeStoreId];
  const [file, setFile] = useState<File | null>(null);
  const [isDragOver, setIsDragOver] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(false);
  const [sortPreset, setSortPreset] = useState<string>('filename');
  const [autoRotate, setAutoRotate] = useState<boolean>(true);
  const [expressMode, setExpressMode] = useState<string>('separate');
  const [outputLayout, setOutputLayout] = useState<string>('auto'); // 'auto' | 'a4_4' | 'a4_8' | 'a4_9' | 'a4_1' | 'thermal_75_120' | 'thermal_100_150' | 'just_merge'
  const [deduplicateHistory, setDeduplicateHistory] = useState<boolean>(true);
  const [resultManifest, setResultManifest] = useState<ManifestResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  // react-query, а не ручной fetch-on-mount — переживает переключение вкладок
  // и перезагрузку страницы через персистентный кэш (queryClient.ts).
  const { data: printedCount = 0 } = useQuery({
    queryKey: historyStatsKey,
    queryFn: fetchHistoryStats,
  });

  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleResetHistory = async () => {
    if (!window.confirm('Вы уверены, что хотите сбросить историю напечатанных заказов за сегодня?')) {
      return;
    }
    try {
      const res = await apiFetch(`${API_URL}/api/print-batcher/reset-history`, { method: 'POST' });
      if (res.ok) {
        queryClient.setQueryData(historyStatsKey, 0);
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
      queryClient.invalidateQueries({ queryKey: historyStatsKey });
    } catch (err: any) {
      setError(err.message || 'Произошла непредвиденная ошибка');
    } finally {
      setLoading(false);
    }
  };

  const handlePrint = async (previewEndpoint: string) => {
    // Прямая навигация (window.open на URL API) не несёт Authorization —
    // токен живёт только в localStorage и цепляется apiFetch'ем, поэтому
    // голый window.open бил 401 "Требуется вход..." даже у залогиненных.
    const fullUrl = `${API_URL}${previewEndpoint}`;
    const win = window.open('', '_blank');
    try {
      const res = await apiFetch(fullUrl);
      if (!res.ok) throw new Error('Не удалось открыть предпросмотр');
      const blob = await res.blob();
      const blobUrl = URL.createObjectURL(blob);
      if (win) {
        win.location.href = blobUrl;
      }
    } catch (err) {
      if (win) win.close();
      setError('Не удалось открыть предпросмотр PDF');
    }
  };

  const handleDownload = async (downloadEndpoint: string, fileName: string) => {
    try {
      const res = await apiFetch(`${API_URL}${downloadEndpoint}`);
      if (!res.ok) throw new Error('Не удалось скачать файл');
      const blob = await res.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = fileName;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(blobUrl);
    } catch (err) {
      setError('Не удалось скачать файл');
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
              <h2 style={styles.headerTitle}>Склейка накладных</h2>
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
              <option value="product_name">По названию товара</option>
              <option value="master_group">По Мастер-Группе остатка (с штампом на этикетке)</option>
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
                🛑 Исключено ранее напечатанных дубликатов: <strong style={{ color: 'var(--error)' }}>{resultManifest.deduplicated_pages} страниц</strong> |
                ✨ Новых листов в PDF: <strong style={{ color: 'var(--secondary)' }}>{resultManifest.new_pages} страниц</strong> |
                Раскладка: <code>{resultManifest.output_layout}</code>
              </div>
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', textAlign: 'right' }}>
              Батч-ID: <code>{resultManifest.batch_id.substring(0, 8)}</code>
            </div>
          </div>

          {/* All duplicates warning banner */}
          {resultManifest.all_duplicates && (
            <div style={styles.allDuplicatesBanner}>
              <div style={{ fontSize: '16px', fontWeight: 700, color: 'var(--warning)' }}>
                ⚠️ Все {resultManifest.total_pages} заказов из этого архива уже были напечатаны ранее!
              </div>
              <p style={{ fontSize: '13px', margin: '6px 0 14px 0', color: 'rgba(255,255,255,.75)' }}>
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
                            ...(isExpress ? { background: 'var(--warning)', color: '#1a1305' } : {}),
                          }}
                        >
                          🖨️ Предпросмотр / Печать
                        </button>

                        <button
                          onClick={() => handleDownload(batch.download_url, batch.file_name)}
                          style={{ ...styles.downloadBtn, cursor: 'pointer' }}
                        >
                          ⬇️ Скачать PDF
                        </button>
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
    color: 'var(--text-color)',
    fontFamily: 'var(--font-sans)',
  },
  header: {
    backgroundImage: 'radial-gradient(420px 260px at 85% 0%, rgba(255,255,255,.12), transparent 70%), linear-gradient(150deg, #2c4ef5, #16246b)',
    borderRadius: '18px',
    padding: '24px 32px',
    marginBottom: '24px',
  },
  headerTitleGroup: {
    display: 'flex',
    alignItems: 'center',
    gap: '20px',
  },
  headerIcon: {
    fontSize: '42px',
    background: 'rgba(255,255,255,0.15)',
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
    color: 'rgba(255,255,255,.85)',
    fontSize: '14px',
    lineHeight: 1.5,
  },
  historyStatBadge: {
    background: 'rgba(255,255,255,0.12)',
    border: '1px solid rgba(255,255,255,0.2)',
    padding: '8px 14px',
    borderRadius: '8px',
    fontSize: '13px',
    color: '#fff',
  },
  resetHistoryBtn: {
    background: 'rgba(226, 84, 95, 0.15)',
    border: '1px solid rgba(226, 84, 95, 0.3)',
    color: '#f3a2a8',
    padding: '8px 14px',
    borderRadius: '8px',
    fontSize: '12px',
    fontWeight: 600,
    cursor: 'pointer',
  },
  card: {
    background: 'var(--card-bg)',
    borderRadius: '18px',
    padding: '24px',
    border: '1px solid var(--card-border)',
    boxShadow: '0 4px 20px rgba(0, 0, 0, 0.2)',
  },
  layoutSelectorCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid var(--card-border)',
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
    background: 'rgba(255,255,255,0.06)',
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
    background: 'rgba(255,255,255,0.04)',
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
    background: 'linear-gradient(100deg, var(--secondary), var(--secondary-hover))',
    boxShadow: '0 4px 14px var(--secondary-glow)',
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
    color: 'var(--text-muted)',
  },
  selectInput: {
    background: 'var(--input-bg)',
    color: 'var(--text-color)',
    border: '1px solid var(--input-border)',
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
    background: 'var(--input-bg)',
    padding: '10px 14px',
    borderRadius: '8px',
    border: '1px solid var(--input-border)',
  },
  checkbox: {
    width: '18px',
    height: '18px',
    accentColor: 'var(--secondary)',
    cursor: 'pointer',
  },
  dropzone: {
    border: '2px dashed var(--input-border)',
    borderRadius: '12px',
    padding: '40px 20px',
    textAlign: 'center',
    cursor: 'pointer',
    background: 'rgba(255,255,255,0.02)',
    transition: 'all 0.2s ease',
  },
  dropzoneActive: {
    borderColor: 'var(--primary)',
    background: 'var(--primary-glow)',
  },
  dropzoneFilled: {
    borderColor: 'var(--secondary)',
    background: 'var(--secondary-glow)',
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
    color: 'var(--text-muted)',
    marginTop: '4px',
  },
  fileName: {
    fontSize: '16px',
    fontWeight: 600,
    color: 'var(--secondary)',
  },
  fileSize: {
    fontSize: '13px',
    color: 'var(--text-muted)',
    marginTop: '2px',
  },
  errorBox: {
    marginTop: '16px',
    background: 'rgba(226, 84, 95, 0.12)',
    border: '1px solid rgba(226, 84, 95, 0.35)',
    color: '#f3a2a8',
    padding: '12px 16px',
    borderRadius: '8px',
    fontSize: '14px',
  },
  primaryBtn: {
    background: 'linear-gradient(100deg, var(--primary), var(--primary-hover))',
    color: '#ffffff',
    border: 'none',
    borderRadius: '10px',
    padding: '14px 28px',
    fontSize: '15px',
    fontWeight: 600,
    cursor: 'pointer',
    boxShadow: '0 6px 16px var(--primary-glow)',
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
    background: 'var(--card-bg)',
    padding: '16px 20px',
    borderRadius: '12px',
    marginBottom: '20px',
    border: '1px solid var(--card-border)',
  },
  summaryBadge: {
    fontSize: '15px',
    color: 'var(--secondary)',
  },
  allDuplicatesBanner: {
    background: 'rgba(224, 163, 62, 0.12)',
    border: '2px solid var(--warning)',
    borderRadius: '12px',
    padding: '20px',
    marginBottom: '20px',
  },
  forceReprintBtn: {
    background: 'var(--warning)',
    color: '#1a1305',
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
    color: 'var(--text-color)',
  },
  cardsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(350px, 1fr))',
    gap: '20px',
  },
  batchCard: {
    background: 'var(--card-bg)',
    border: '1px solid var(--card-border)',
    borderRadius: '14px',
    padding: '20px',
    display: 'flex',
    flexDirection: 'column',
    gap: '16px',
    boxShadow: '0 4px 12px rgba(0, 0, 0, 0.15)',
    position: 'relative',
  },
  expressBatchCard: {
    backgroundImage: 'radial-gradient(300px 160px at 90% 0%, rgba(255,255,255,.14), transparent 70%), linear-gradient(150deg, rgba(224,163,62,0.35), #16246b)',
    border: '2px solid var(--warning)',
    boxShadow: '0 4px 20px rgba(224, 163, 62, 0.3)',
  },
  expressBadge: {
    background: 'var(--warning)',
    color: '#1a1305',
    fontWeight: 800,
    fontSize: '11px',
    padding: '4px 10px',
    borderRadius: '6px',
    letterSpacing: '0.5px',
    alignSelf: 'flex-start',
  },
  auditBadge: {
    background: 'var(--success-glow)',
    border: '1px solid var(--success)',
    color: '#8ee6bd',
    padding: '8px 12px',
    borderRadius: '8px',
    fontSize: '12px',
    fontWeight: 600,
  },
  auditBadgeError: {
    background: 'rgba(226, 84, 95, 0.12)',
    border: '1px solid rgba(226, 84, 95, 0.35)',
    color: '#f3a2a8',
  },
  batchHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: '14px',
  },
  batchIcon: {
    fontSize: '32px',
    background: 'rgba(255,255,255,0.05)',
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
    color: 'var(--text-muted)',
  },
  batchMetrics: {
    display: 'flex',
    gap: '10px',
  },
  metricBadge: {
    background: 'rgba(255,255,255,0.05)',
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
    background: 'var(--primary)',
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
    background: 'var(--secondary)',
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
    background: 'var(--card-bg)',
    borderRadius: '12px',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },
  modalHeader: {
    padding: '16px 20px',
    background: 'rgba(255,255,255,0.03)',
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
    color: 'var(--text-muted)',
    fontSize: '20px',
    cursor: 'pointer',
  },
  iframePreview: {
    width: '100%',
    height: '100%',
    border: 'none',
  },
};
