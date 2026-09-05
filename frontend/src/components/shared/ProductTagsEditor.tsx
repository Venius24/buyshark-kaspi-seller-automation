import { useState } from 'react';
import { Tag, X } from 'lucide-react';
import { useSaveProductTags, useStoreTags } from '../../hooks/useProductTags';

interface ProductTagsEditorProps {
  /** Артикул продавца — тот же ключ, по которому метки ищет печать. */
  sku: string;
  tags: string[];
}

export function ProductTagsEditor({ sku, tags }: ProductTagsEditorProps) {
  const [draft, setDraft] = useState<string[]>(tags);
  const [input, setInput] = useState('');
  const storeTags = useStoreTags();
  const saveTags = useSaveProductTags();

  const add = (raw: string) => {
    const tag = raw.trim();
    if (!tag || draft.some((t) => t.toLowerCase() === tag.toLowerCase())) {
      setInput('');
      return;
    }
    setDraft([...draft, tag]);
    setInput('');
  };

  const suggestions = storeTags
    .filter((s) => !draft.some((t) => t.toLowerCase() === s.tag.toLowerCase()))
    .slice(0, 8);

  const dirty =
    draft.length !== tags.length || draft.some((t, i) => t !== tags[i]);

  return (
    <div>
      <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>
        Метки для этикетки
      </label>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginBottom: '8px' }}>
        {draft.map((tag) => (
          <span
            key={tag}
            className="badge"
            style={{
              background: 'rgba(255,255,255,0.05)',
              color: '#eee',
              border: '1px solid rgba(255,255,255,0.08)',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '4px',
            }}
          >
            <Tag size={11} />
            {tag}
            <button
              type="button"
              aria-label={`Убрать метку ${tag}`}
              onClick={() => setDraft(draft.filter((t) => t !== tag))}
              style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', padding: 0, display: 'flex' }}
            >
              <X size={11} />
            </button>
          </span>
        ))}
        {draft.length === 0 && (
          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Меток нет</span>
        )}
      </div>

      <div style={{ display: 'flex', gap: '8px' }}>
        <input
          type="text"
          className="form-input"
          value={input}
          maxLength={60}
          placeholder="Например: Хрупкое"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              add(input);
            }
          }}
        />
        <button
          type="button"
          className="btn btn-secondary"
          style={{ padding: '8px 12px', fontSize: '12px', whiteSpace: 'nowrap' }}
          onClick={() => add(input)}
          disabled={!input.trim()}
        >
          Добавить
        </button>
        <button
          type="button"
          className="btn btn-primary"
          style={{ padding: '8px 12px', fontSize: '12px', whiteSpace: 'nowrap' }}
          onClick={() => saveTags.mutate({ sku, tags: draft })}
          disabled={!dirty || saveTags.isPending}
        >
          {saveTags.isPending ? 'Сохраняю…' : 'Сохранить метки'}
        </button>
      </div>

      {suggestions.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '8px', alignItems: 'center' }}>
          <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Уже используются:</span>
          {suggestions.map((s) => (
            <button
              key={s.tag}
              type="button"
              onClick={() => add(s.tag)}
              style={{
                fontSize: '11px',
                padding: '2px 8px',
                borderRadius: '10px',
                background: 'transparent',
                border: '1px dashed rgba(255,255,255,0.18)',
                color: 'var(--text-muted)',
                cursor: 'pointer',
              }}
            >
              {s.tag} · {s.products}
            </button>
          ))}
        </div>
      )}

      {saveTags.isError && (
        <div style={{ fontSize: '11px', color: 'var(--danger, #ef4444)', marginTop: '6px' }}>
          {(saveTags.error as Error).message}
        </div>
      )}
    </div>
  );
}
