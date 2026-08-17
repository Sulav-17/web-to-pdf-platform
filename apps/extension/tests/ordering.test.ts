import { describe, expect, it } from 'vitest';
import { moveItem, selectedInOrder } from '../src/lib/ordering';

describe('moveItem', () => {
  it('moves an item forward', () => {
    expect(moveItem(['a', 'b', 'c', 'd'], 0, 2)).toEqual(['b', 'c', 'a', 'd']);
  });

  it('moves an item backward', () => {
    expect(moveItem(['a', 'b', 'c', 'd'], 3, 1)).toEqual(['a', 'd', 'b', 'c']);
  });

  it('is a no-op for the same index', () => {
    expect(moveItem(['a', 'b'], 1, 1)).toEqual(['a', 'b']);
  });

  it('ignores out-of-range indices', () => {
    expect(moveItem(['a', 'b'], 5, 0)).toEqual(['a', 'b']);
    expect(moveItem(['a', 'b'], 0, 9)).toEqual(['a', 'b']);
  });

  it('never mutates the input', () => {
    const input = ['a', 'b', 'c'];
    moveItem(input, 0, 2);
    expect(input).toEqual(['a', 'b', 'c']);
  });

  it('preserves every element', () => {
    const input = ['a', 'b', 'c', 'd', 'e'];
    const moved = moveItem(input, 4, 0);
    expect([...moved].sort()).toEqual([...input].sort());
    expect(moved[0]).toBe('e');
  });
});

describe('selectedInOrder', () => {
  it('keeps row order and drops unselected rows', () => {
    const rows = [
      { id: 1, on: true },
      { id: 2, on: false },
      { id: 3, on: true },
    ];
    expect(selectedInOrder(rows, (row) => row.on).map((row) => row.id)).toEqual([1, 3]);
  });

  it('reflects a reorder before selection', () => {
    const rows = [
      { id: 'zebra', on: true },
      { id: 'apple', on: true },
    ];
    const reordered = moveItem(rows, 1, 0);
    expect(selectedInOrder(reordered, (row) => row.on).map((row) => row.id)).toEqual([
      'apple',
      'zebra',
    ]);
  });
});
