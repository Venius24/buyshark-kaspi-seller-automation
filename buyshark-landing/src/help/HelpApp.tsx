import { Nav } from '../components/Nav';
import { Footer } from '../components/Footer';
import { waLink } from '../data/contacts';
import { HELP_ARTICLES, HELP_GROUPS, getArticlesByGroup, type HelpBlock } from '../../../shared/help-content';
import styles from './HelpApp.module.css';

function HelpBlockView({ block }: { block: HelpBlock }) {
  switch (block.type) {
    case 'p':
      return <p>{block.text}</p>;
    case 'h3':
      return <h3>{block.text}</h3>;
    case 'link':
      return (
        <a href={block.href} target="_blank" rel="noopener noreferrer" className={styles.linkButton}>
          {block.text}
        </a>
      );
    case 'list':
      return (
        <ul>
          {block.items.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      );
    case 'note':
      return (
        <div className={`${styles.note} ${block.tone === 'warning' ? styles.noteWarning : styles.noteInfo}`}>
          {block.tone === 'warning' ? '⚠️ ' : 'ℹ️ '}{block.text}
        </div>
      );
    case 'table':
      return (
        <table className={styles.table}>
          <thead>
            <tr>
              {block.headers.map((h, i) => (
                <th key={i}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) => (
                  <td key={j}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      );
  }
}

export function HelpApp() {
  return (
    <div className={styles.page}>
      <Nav />

      <div className={styles.hero}>
        <h1>Помощь и энциклопедия</h1>
        <p>Как устроены все механики BuyShark: демпинг, связка остатков, автосборка заказов, тарифы и остальное — простым языком, без инженерного жаргона.</p>
      </div>

      <div className={styles.layout}>
        <nav className={styles.toc}>
          {HELP_GROUPS.map((group) => (
            <div key={group} className={styles.tocGroup}>
              <div className={styles.tocGroupTitle}>{group}</div>
              {getArticlesByGroup(group).map((article) => (
                <a key={article.slug} className={styles.tocLink} href={`#${article.slug}`}>
                  <span>{article.icon}</span>
                  <span>{article.title}</span>
                  {article.badge === 'beta' && <span className={styles.badge}>Beta</span>}
                </a>
              ))}
            </div>
          ))}
        </nav>

        <div className={styles.articles}>
          {HELP_ARTICLES.map((article) => (
            <article key={article.slug} id={article.slug} className={styles.article}>
              <h2 className={styles.articleTitle}>
                <span>{article.icon}</span>
                <span>{article.title}</span>
                {article.badge === 'beta' && <span className={styles.badge}>Beta</span>}
              </h2>
              <p className={styles.summary}>{article.summary}</p>
              <div className={styles.blocks}>
                {article.blocks.map((block, i) => (
                  <HelpBlockView key={i} block={block} />
                ))}
              </div>
            </article>
          ))}
        </div>
      </div>

      <div className={styles.contactCta}>
        <p>Не нашли ответ? Напишите — ответим живым человеком</p>
        <a
          className={styles.linkButton}
          href={waLink('help', 'Здравствуйте! Не нашёл ответ в справке BuyShark, есть вопрос.')}
          target="_blank"
          rel="noopener noreferrer"
        >
          Написать в WhatsApp
        </a>
      </div>

      <Footer />
    </div>
  );
}
