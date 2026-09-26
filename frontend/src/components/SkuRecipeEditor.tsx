import React from 'react';
import { Layers, Plus, X } from 'lucide-react';
import { type SkuRecipe, type SkuRecipes, findRecipe, isCustomRecipe } from './skuRecipes';

/** Карточка товара в группе — то, что строит buildLinkedCards. */
export interface RecipeCard {
  key: string;
  label: string;
  name?: string;
  aliases: string[];
}

interface GroupOption {
  group_id: string;
  group_name: string;
  shared_stock: number;
  stop_qty: number;
  is_active: boolean;
}

interface Props {
  cards: RecipeCard[];
  recipes: SkuRecipes;
  onChange: (next: SkuRecipes) => void;
  /** Группа, которую редактируем (null — создаётся новая). */
  groupId: string | null;
  sharedStock: number;
  stopQty: number;
  groups: GroupOption[];
}

const PURPLE = '#c084fc';

const numInput: React.CSSProperties = {
  width: '56px',
  padding: '5px 6px',
  borderRadius: '8px',
  background: 'var(--card-bg)',
  border: '1px solid var(--card-border)',
  color: 'var(--text-color)',
  fontSize: '13px',
  textAlign: 'center',
  boxSizing: 'border-box',
};

const clampQty = (v: string) => Math.min(1000, Math.max(1, parseInt(v) || 1));

/**
 * «Списание за 1 продажу» в модалке группы: ×N у каждой карточки и наборы из
 * нескольких групп. Рядом — сколько такая карточка покажет на Kaspi, по тому
 * же правилу, что рассылка на бэкенде (sku_recipes.sellable).
 */
export const SkuRecipeEditor: React.FC<Props> = ({
  cards, recipes, onChange, groupId, sharedStock, stopQty, groups,
}) => {
  const byId = new Map(groups.map(g => [g.group_id, g]));
  const others = groups
    .filter(g => g.group_id !== groupId)
    .sort((a, b) => a.group_name.localeCompare(b.group_name, 'ru'));

  const recipeOf = (card: RecipeCard): SkuRecipe =>
    findRecipe(recipes, [card.label, ...card.aliases])?.[1] ?? { qty: 1 };

  const setRecipe = (card: RecipeCard, r: SkuRecipe) => {
    const next: SkuRecipes = { ...recipes };
    // Старый ключ мог быть записан другим идентификатором карточки.
    const found = findRecipe(next, [card.label, ...card.aliases]);
    if (found) delete next[found[0]];
    // Незаполненная строка набора («— выберите группу —») живёт в форме,
    // пока её не заполнят; при сохранении её отбрасывает cleanRecipesForSubmit.
    const extra = r.extra || [];
    if (r.qty > 1 || extra.length > 0) {
      next[card.label] = { qty: r.qty, ...(extra.length ? { extra } : {}) };
    }
    onChange(next);
  };

  /** [сколько наборов собирается, сколько уйдёт на Kaspi] — как sellable(). */
  const sellable = (r: SkuRecipe): [number, number] => {
    const parts: { stock: number; stop: number; qty: number }[] = [
      { stock: sharedStock, stop: stopQty, qty: r.qty || 1 },
    ];
    for (const x of r.extra || []) {
      const g = byId.get(x.group_id);
      if (g && g.is_active) parts.push({ stock: g.shared_stock, stop: g.stop_qty ?? 5, qty: x.qty || 1 });
    }
    const avail = parts.map(p => Math.floor(Math.max(0, p.stock) / p.qty));
    const push = parts.map((p, i) => (p.stock > p.stop ? avail[i] : 0));
    return [Math.min(...avail), Math.min(...push)];
  };

  if (cards.length === 0) return null;

  return (
    <div>
      <label style={{ display: 'block', fontSize: '13px', fontWeight: 600, color: '#cbd5e1', marginBottom: '4px' }}>
        Списание за 1 продажу
      </label>
      <p style={{ margin: '0 0 10px', fontSize: '12px', color: 'var(--text-muted)', lineHeight: 1.45 }}>
        ×N — сколько штук остатка этой группы уходит за одну проданную карточку (двойная упаковка — ×2).
        «Набор» — продажа списывает ещё и из других групп; на Kaspi набор показывает остаток
        самой бедной составляющей и снимается, как только любая из них дошла до стоп-количества.
      </p>
      <div style={{
        display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '300px', overflowY: 'auto',
        background: 'var(--card-bg)', border: '1px solid var(--card-border)', borderRadius: '10px', padding: '8px',
      }}>
        {cards.map(card => {
          const r = recipeOf(card);
          const custom = isCustomRecipe(r);
          const [, onKaspi] = sellable(r);
          const extras = r.extra || [];
          return (
            <div key={card.key} style={{
              padding: '8px 10px', borderRadius: '8px',
              background: custom ? 'rgba(168, 85, 247, 0.08)' : 'transparent',
              border: custom ? '1px solid rgba(168, 85, 247, 0.3)' : '1px solid transparent',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                <span style={{ flex: '1 1 220px', minWidth: 0, fontSize: '12px', color: '#cbd5e1', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                      title={card.name || card.label}>
                  <span style={{ fontFamily: 'monospace', color: custom ? PURPLE : 'var(--primary-hover)' }}>{card.label}</span>
                  {card.name ? <span style={{ color: 'var(--text-muted)' }}> · {card.name}</span> : null}
                </span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', fontSize: '12px', color: 'var(--text-muted)' }}
                      title="Сколько штук остатка ЭТОЙ группы уходит за одну продажу карточки">
                  ×
                  <input
                    type="number" min={1} max={1000} value={r.qty}
                    onChange={e => setRecipe(card, { ...r, qty: clampQty(e.target.value) })}
                    style={{ ...numInput, ...(r.qty > 1 ? { border: `1px solid ${PURPLE}`, color: PURPLE, fontWeight: 700 } : {}) }}
                  />
                </span>
                <button
                  type="button"
                  onClick={() => setRecipe(card, { ...r, extra: [...extras, { group_id: '', qty: 1 }] })}
                  title="Продажа этой карточки будет списывать ещё и из другой группы"
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: '4px', padding: '5px 10px', borderRadius: '8px',
                    background: 'transparent', border: '1px dashed var(--card-border)', color: 'var(--text-muted)',
                    fontSize: '12px', cursor: 'pointer',
                  }}
                >
                  <Plus size={12} /> {extras.length ? 'группа' : 'набор'}
                </button>
                <span style={{ fontSize: '11px', color: onKaspi > 0 ? 'var(--text-muted)' : 'var(--error)', whiteSpace: 'nowrap', minWidth: '92px', textAlign: 'right' }}
                      title="Сколько эта карточка покажет на Kaspi при текущих остатках">
                  на Kaspi: {onKaspi} шт.
                </span>
              </div>

              {extras.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '8px', paddingLeft: '14px', borderLeft: `2px solid rgba(168, 85, 247, 0.35)` }}>
                  {extras.map((x, i) => {
                    const g = byId.get(x.group_id);
                    const update = (patch: Partial<typeof x>) =>
                      setRecipe(card, { ...r, extra: extras.map((y, j) => (j === i ? { ...y, ...patch } : y)) });
                    return (
                      <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                        <Layers size={13} color={PURPLE} />
                        <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>+ из группы</span>
                        <select
                          value={x.group_id}
                          onChange={e => update({ group_id: e.target.value })}
                          style={{
                            flex: '1 1 200px', minWidth: 0, padding: '5px 8px', borderRadius: '8px', background: 'var(--card-bg)',
                            border: x.group_id ? '1px solid var(--card-border)' : '1px solid var(--warning)',
                            color: 'var(--text-color)', fontSize: '12px',
                          }}
                        >
                          <option value="">— выберите группу —</option>
                          {others
                            .filter(o => o.group_id === x.group_id || !extras.some(y => y.group_id === o.group_id))
                            .map(o => (
                              <option key={o.group_id} value={o.group_id}>
                                {o.group_name} · {o.shared_stock} шт.{o.is_active ? '' : ' (выключена)'}
                              </option>
                            ))}
                        </select>
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', fontSize: '12px', color: 'var(--text-muted)' }}>
                          ×
                          <input type="number" min={1} max={1000} value={x.qty}
                                 onChange={e => update({ qty: clampQty(e.target.value) })} style={numInput} />
                        </span>
                        <button
                          type="button"
                          onClick={() => setRecipe(card, { ...r, extra: extras.filter((_, j) => j !== i) })}
                          title="Убрать группу из набора"
                          style={{ display: 'inline-flex', background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: 2 }}
                        >
                          <X size={14} />
                        </button>
                        {g && !g.is_active && (
                          <span style={{ fontSize: '11px', color: 'var(--warning)' }}>выключена — списываться не будет</span>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
