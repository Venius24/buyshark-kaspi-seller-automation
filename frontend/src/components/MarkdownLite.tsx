import React from 'react';

/**
 * Минимальный рендерер Markdown для ответов ИИ-помощника.
 *
 * Почему свой, а не react-markdown: в проекте нет ни одной markdown-зависимости,
 * а `npm install` здесь упирается в пред­существующий конфликт пиров
 * (i18next/typescript) — тянуть два пакета и чинить лок-файл ради жирного
 * шрифта в чате несоразмерно. Модель выдаёт узкое подмножество разметки, оно
 * целиком покрыто ниже.
 *
 * Важно: рендерим в React-элементы, а НЕ через dangerouslySetInnerHTML. Текст
 * приходит от языковой модели, то есть в него может попасть что угодно из
 * данных магазина (названия товаров, имена конкурентов) — вставлять это в DOM
 * как HTML нельзя. React экранирует всё сам.
 */

type Block =
  | { kind: 'p'; lines: string[] }
  | { kind: 'h'; level: number; text: string }
  | { kind: 'ul'; items: string[] }
  | { kind: 'ol'; items: string[] }
  | { kind: 'table'; head: string[]; rows: string[][] };

const H_RE = /^(#{1,4})\s+(.*)$/;
const UL_RE = /^\s*[-*•]\s+(.*)$/;
const OL_RE = /^\s*\d+[.)]\s+(.*)$/;
const TABLE_SEP_RE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function splitRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((c) => c.trim());
}

function parseBlocks(text: string): Block[] {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) {
      i += 1;
      continue;
    }

    const h = H_RE.exec(line);
    if (h) {
      blocks.push({ kind: 'h', level: h[1].length, text: h[2] });
      i += 1;
      continue;
    }

    // Таблица: строка с | и следующая — разделитель из дефисов.
    if (line.includes('|') && i + 1 < lines.length && TABLE_SEP_RE.test(lines[i + 1])) {
      const head = splitRow(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        rows.push(splitRow(lines[i]));
        i += 1;
      }
      blocks.push({ kind: 'table', head, rows });
      continue;
    }

    if (UL_RE.test(line)) {
      const items: string[] = [];
      while (i < lines.length && UL_RE.test(lines[i])) {
        items.push(UL_RE.exec(lines[i])![1]);
        i += 1;
      }
      blocks.push({ kind: 'ul', items });
      continue;
    }

    if (OL_RE.test(line)) {
      const items: string[] = [];
      while (i < lines.length && OL_RE.test(lines[i])) {
        items.push(OL_RE.exec(lines[i])![1]);
        i += 1;
      }
      blocks.push({ kind: 'ol', items });
      continue;
    }

    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !H_RE.test(lines[i]) && !UL_RE.test(lines[i]) && !OL_RE.test(lines[i])) {
      para.push(lines[i]);
      i += 1;
    }
    blocks.push({ kind: 'p', lines: para });
  }

  return blocks;
}

// **жирный**, *курсив*, `код`. Порядок в чередовании важен: двойная звёздочка
// должна разбираться раньше одинарной, иначе `**x**` съест только края.
const INLINE_RE = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\n]+\*)/g;

function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  const parts = text.split(INLINE_RE).filter((p) => p !== undefined && p !== '');
  return parts.map((part, idx) => {
    const key = `${keyPrefix}-${idx}`;
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return <strong key={key}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return <code key={key} style={codeStyle}>{part.slice(1, -1)}</code>;
    }
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
      return <em key={key}>{part.slice(1, -1)}</em>;
    }
    return <React.Fragment key={key}>{part}</React.Fragment>;
  });
}

export function MarkdownLite({ text }: { text: string }) {
  if (!text) return null;
  const blocks = parseBlocks(text);

  return (
    <div className="md-lite">
      {blocks.map((block, bi) => {
        switch (block.kind) {
          case 'h': {
            const Tag = (`h${Math.min(block.level + 2, 6)}`) as 'h3' | 'h4' | 'h5' | 'h6';
            return <Tag key={bi} style={headingStyle}>{renderInline(block.text, `h${bi}`)}</Tag>;
          }
          case 'ul':
            return (
              <ul key={bi} style={listStyle}>
                {block.items.map((it, ii) => <li key={ii}>{renderInline(it, `${bi}-${ii}`)}</li>)}
              </ul>
            );
          case 'ol':
            return (
              <ol key={bi} style={listStyle}>
                {block.items.map((it, ii) => <li key={ii}>{renderInline(it, `${bi}-${ii}`)}</li>)}
              </ol>
            );
          case 'table':
            return (
              // Таблицы модели бывают широкими — скроллим их отдельно, иначе
              // они распирают пузырь сообщения и ломают колонку диалога.
              <div key={bi} style={tableWrapStyle}>
                <table style={tableStyle}>
                  <thead>
                    <tr>{block.head.map((c, ci) => <th key={ci} style={thStyle}>{renderInline(c, `th${bi}-${ci}`)}</th>)}</tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, ri) => (
                      <tr key={ri}>
                        {row.map((c, ci) => <td key={ci} style={tdStyle}>{renderInline(c, `td${bi}-${ri}-${ci}`)}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          default:
            return (
              <p key={bi} style={paraStyle}>
                {block.lines.map((ln, li) => (
                  <React.Fragment key={li}>
                    {li > 0 && <br />}
                    {renderInline(ln, `${bi}-${li}`)}
                  </React.Fragment>
                ))}
              </p>
            );
        }
      })}
    </div>
  );
}

const paraStyle: React.CSSProperties = { margin: '0 0 8px' };
const headingStyle: React.CSSProperties = { margin: '12px 0 6px', fontSize: 14, fontWeight: 600 };
const listStyle: React.CSSProperties = { margin: '0 0 8px', paddingLeft: 20 };
const codeStyle: React.CSSProperties = {
  background: 'rgba(255,255,255,0.09)',
  padding: '1px 4px',
  borderRadius: 4,
  fontSize: 12,
};
const tableWrapStyle: React.CSSProperties = { overflowX: 'auto', margin: '0 0 8px' };
const tableStyle: React.CSSProperties = { borderCollapse: 'collapse', fontSize: 13, width: '100%' };
const thStyle: React.CSSProperties = {
  textAlign: 'left',
  padding: '4px 8px',
  borderBottom: '1px solid rgba(255,255,255,0.18)',
  whiteSpace: 'nowrap',
  opacity: 0.8,
};
const tdStyle: React.CSSProperties = {
  padding: '4px 8px',
  borderBottom: '1px solid rgba(255,255,255,0.07)',
  verticalAlign: 'top',
};
