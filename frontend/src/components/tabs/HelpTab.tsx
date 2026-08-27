import { useState } from 'react';
import { AlertTriangle, Info } from 'lucide-react';
import { HELP_ARTICLES, HELP_GROUPS, getArticlesByGroup, type HelpBlock } from '../../../../shared/help-content';

function HelpBlockView({ block }: { block: HelpBlock }) {
  switch (block.type) {
    case 'p':
      return <p style={{ fontSize: '13px', lineHeight: 1.6, color: 'var(--text-secondary, #cbd5e1)' }}>{block.text}</p>;
    case 'h3':
      return <h3 style={{ fontSize: '14px', fontWeight: 600, marginTop: '8px' }}>{block.text}</h3>;
    case 'link':
      return (
        <a
          href={block.href}
          target="_blank"
          rel="noopener noreferrer"
          className="btn btn-secondary"
          style={{ alignSelf: 'flex-start', padding: '8px 14px', fontSize: '13px', textDecoration: 'none' }}
        >
          {block.text}
        </a>
      );
    case 'list':
      return (
        <ul style={{ paddingLeft: '18px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
          {block.items.map((item, i) => (
            <li key={i} style={{ fontSize: '13px', lineHeight: 1.5, color: 'var(--text-secondary, #cbd5e1)' }}>{item}</li>
          ))}
        </ul>
      );
    case 'note': {
      const isWarning = block.tone === 'warning';
      return (
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: '8px',
            padding: '10px 14px',
            fontSize: '12.5px',
            lineHeight: 1.5,
            borderRadius: '8px',
            background: isWarning ? 'rgba(245, 158, 11, 0.1)' : 'rgba(59, 130, 246, 0.1)',
            border: `1px solid ${isWarning ? 'rgba(245, 158, 11, 0.15)' : 'rgba(59, 130, 246, 0.15)'}`,
            color: isWarning ? 'var(--warning)' : '#93c5fd',
          }}
        >
          {isWarning ? <AlertTriangle size={15} style={{ flexShrink: 0, marginTop: '1px' }} /> : <Info size={15} style={{ flexShrink: 0, marginTop: '1px' }} />}
          <span>{block.text}</span>
        </div>
      );
    }
    case 'table':
      return (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12.5px' }}>
            <thead>
              <tr>
                {block.headers.map((h, i) => (
                  <th key={i} style={{ textAlign: 'left', padding: '8px 10px', borderBottom: '1px solid rgba(255,255,255,0.1)', color: 'var(--text-muted)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => (
                    <td key={j} style={{ padding: '8px 10px', borderBottom: '1px solid rgba(255,255,255,0.06)', verticalAlign: 'top' }}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
  }
}

export function HelpTab() {
  const [activeSlug, setActiveSlug] = useState(HELP_ARTICLES[0].slug);
  const activeArticle = HELP_ARTICLES.find((a) => a.slug === activeSlug) ?? HELP_ARTICLES[0];

  return (
    <div style={{ display: 'flex', gap: '24px', alignItems: 'flex-start' }}>
      <nav style={{ width: '260px', flexShrink: 0, display: 'flex', flexDirection: 'column', gap: '18px', position: 'sticky', top: '0' }}>
        {HELP_GROUPS.map((group) => (
          <div key={group}>
            <div className="sidebar-group-title" style={{ marginBottom: '6px' }}>{group}</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
              {getArticlesByGroup(group).map((article) => (
                <button
                  key={article.slug}
                  type="button"
                  onClick={() => setActiveSlug(article.slug)}
                  className={`sidebar-item ${activeSlug === article.slug ? 'active' : ''}`}
                  style={{ width: '100%', textAlign: 'left' }}
                >
                  <div className="sidebar-item-left">
                    <span className="sidebar-icon">{article.icon}</span>
                    <span>{article.title}</span>
                  </div>
                  {article.badge === 'beta' && <span className="sidebar-pill">Beta</span>}
                </button>
              ))}
            </div>
          </div>
        ))}
      </nav>

      <div className="glass-card" style={{ flexGrow: 1, display: 'flex', flexDirection: 'column', gap: '14px', maxWidth: '760px' }}>
        <div>
          <h2 style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span>{activeArticle.icon}</span>
            <span>{activeArticle.title}</span>
            {activeArticle.badge === 'beta' && <span className="sidebar-pill">Beta</span>}
          </h2>
          <p style={{ marginTop: '6px', fontSize: '13px', color: 'var(--text-muted)' }}>{activeArticle.summary}</p>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {activeArticle.blocks.map((block, i) => (
            <HelpBlockView key={i} block={block} />
          ))}
        </div>
      </div>
    </div>
  );
}
