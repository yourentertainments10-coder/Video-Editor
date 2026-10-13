import { describe, expect, it } from 'vitest';
import { canRedo, canUndo, createHistory, push, redo, undo } from '../src/timeline/history';

const s = (n: number): any => ({ duration_ticks: n });

describe('timeline history', () => {
  it('undo and redo walk back and forth', () => {
    let h = createHistory(s(1));
    h = push(h, s(2));
    h = push(h, s(3));
    h = undo(h);
    expect(h.present).toEqual(s(2));
    h = undo(h);
    expect(h.present).toEqual(s(1));
    expect(canUndo(h)).toBe(false);
    h = redo(h);
    expect(h.present).toEqual(s(2));
    expect(canRedo(h)).toBe(true);
  });

  it('a new edit after undo clears the redo stack', () => {
    let h = push(push(createHistory(s(1)), s(2)), s(3));
    h = push(undo(h), s(9));
    expect(canRedo(h)).toBe(false);
    expect(h.present).toEqual(s(9));
    expect(undo(h).present).toEqual(s(2));
  });

  it('undo and redo at the ends return the same history object', () => {
    const h = createHistory(s(1));
    expect(undo(h)).toBe(h);
    expect(redo(h)).toBe(h);
  });

  it('keeps at most 100 past states', () => {
    let h = createHistory(s(0));
    for (let i = 1; i <= 150; i++) h = push(h, s(i));
    let steps = 0;
    while (canUndo(h)) { h = undo(h); steps++; }
    expect(steps).toBe(100);
    expect(h.present).toEqual(s(50));
  });

  it('never copies or mutates stored states', () => {
    const first = s(1);
    const h = push(createHistory(first), s(2));
    expect(undo(h).present).toBe(first);
    expect(first).toEqual(s(1));
  });
});
