/** Pure list-ordering helpers backing the popup's drag-to-order behaviour. */

/**
 * Move the item at `from` so it sits at index `to`, shifting the rest.
 * Returns a new array; out-of-range indices return the input unchanged.
 */
export function moveItem<T>(items: readonly T[], from: number, to: number): T[] {
  if (from === to) return [...items];
  if (from < 0 || from >= items.length) return [...items];
  if (to < 0 || to >= items.length) return [...items];
  const next = [...items];
  const [moved] = next.splice(from, 1);
  if (moved === undefined) return [...items];
  next.splice(to, 0, moved);
  return next;
}

/**
 * Build the final pack payload order: the user's row order, filtered to the
 * selected rows. This is the exact order sent to POST /v1/packs.
 */
export function selectedInOrder<T>(rows: readonly T[], isSelected: (row: T) => boolean): T[] {
  return rows.filter(isSelected);
}
