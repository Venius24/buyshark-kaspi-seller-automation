/**
 * Рецепт списания артикула — фронтовая половина backend/services/sku_recipes.py.
 *
 * Рецепт лежит на «родной» группе артикула: `qty` — сколько штук этой группы
 * уходит за одну продажу (×N), `extra` — другие группы набора. Артикула нет в
 * словаре — продажа списывает 1 шт. своей группы.
 */

export interface SkuRecipeComponent {
  group_id: string;
  qty: number;
}

export interface SkuRecipe {
  qty: number;
  extra?: SkuRecipeComponent[];
}

export type SkuRecipes = Record<string, SkuRecipe>;

/** То же правило, что _canon на бэкенде: без регистра и лишних пробелов. */
export const canonSku = (s: string | null | undefined): string =>
  String(s ?? '').trim().split(/\s+/).join(' ').toLowerCase();

/** Рецепт карточки товара по любому из её идентификаторов: [ключ, рецепт]. */
export function findRecipe(recipes: SkuRecipes | undefined, ids: string[]): [string, SkuRecipe] | null {
  if (!recipes) return null;
  const want = new Set(ids.map(canonSku));
  for (const [k, v] of Object.entries(recipes)) {
    if (want.has(canonSku(k))) return [k, v];
  }
  return null;
}

/** Рецепт отличается от «своя группа × 1». */
export const isCustomRecipe = (r?: SkuRecipe | null): boolean =>
  !!r && ((r.qty ?? 1) !== 1 || (r.extra?.length ?? 0) > 0);

interface GroupLike {
  group_id: string;
  group_name: string;
  is_active: boolean;
  sku_recipes?: SkuRecipes;
}

export interface ForeignBundle {
  homeGroupId: string;
  homeGroupName: string;
  sku: string;
  /** Сколько штук ЭТОЙ группы уходит за одну продажу набора. */
  qtyHere: number;
}

/** Наборы из других групп, куда группа groupId входит составляющей. */
export function foreignBundles(groupId: string, groups: GroupLike[]): ForeignBundle[] {
  const out: ForeignBundle[] = [];
  for (const g of groups) {
    if (g.group_id === groupId || !g.is_active) continue;
    for (const [sku, r] of Object.entries(g.sku_recipes || {})) {
      const part = (r.extra || []).find(x => x.group_id === groupId);
      if (part) {
        out.push({ homeGroupId: g.group_id, homeGroupName: g.group_name, sku, qtyHere: part.qty || 1 });
      }
    }
  }
  return out;
}

/** Группа с комплектами ×N или наборами — для быстрого фильтра. */
export const hasPacksOrBundles = (g: GroupLike, groups: GroupLike[]): boolean =>
  Object.values(g.sku_recipes || {}).some(isCustomRecipe) || foreignBundles(g.group_id, groups).length > 0;

/**
 * Рецепты к отправке: только для карточек, которые остались в группе, без
 * незаполненных строк набора и без записей «×1 без набора».
 */
export function cleanRecipesForSubmit(recipes: SkuRecipes, cards: { label: string; aliases: string[] }[]): SkuRecipes {
  const out: SkuRecipes = {};
  for (const card of cards) {
    const found = findRecipe(recipes, [card.label, ...card.aliases]);
    if (!found) continue;
    const [, r] = found;
    const extra = (r.extra || []).filter(x => x.group_id);
    if ((r.qty ?? 1) !== 1 || extra.length > 0) {
      out[card.label] = { qty: r.qty ?? 1, ...(extra.length ? { extra } : {}) };
    }
  }
  return out;
}
