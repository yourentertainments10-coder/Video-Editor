import { describe, expect, it } from 'vitest';
import { commandForKey } from '../src/timeline/keys';

const k = (key: string, mods: Partial<{ ctrlKey: boolean; shiftKey: boolean; altKey: boolean; metaKey: boolean }> = {}) =>
  commandForKey({ key, ctrlKey: false, shiftKey: false, altKey: false, metaKey: false, ...mods });

describe('keyboard map', () => {
  it('maps transport and navigation keys', () => {
    expect(k(' ')).toBe('playPause');
    expect(k('ArrowLeft')).toBe('prevFrame');
    expect(k('ArrowRight')).toBe('nextFrame');
    expect(k('ArrowLeft', { shiftKey: true })).toBe('back1s');
    expect(k('ArrowRight', { shiftKey: true })).toBe('forward1s');
    expect(k('ArrowUp')).toBe('prevEdit');
    expect(k('ArrowDown')).toBe('nextEdit');
    expect(k('Home')).toBe('home');
    expect(k('End')).toBe('end');
  });

  it('maps editing keys only without modifiers (caps lock still works)', () => {
    expect(k('s')).toBe('split');
    expect(k('S')).toBe('split');
    expect(k('t')).toBe('addText');
    expect(k('Delete')).toBe('delete');
    expect(k('Backspace')).toBe('delete');
    expect(k('s', { ctrlKey: true })).toBe('saveNow');
    expect(k('s', { metaKey: true })).toBe('saveNow');
    expect(k('s', { altKey: true })).toBeNull();
    expect(k('t', { ctrlKey: true })).toBeNull();
    expect(k('Delete', { ctrlKey: true })).toBeNull();
    expect(k('s', { shiftKey: true })).toBeNull();
  });

  it('maps undo, redo and zoom', () => {
    expect(k('z', { ctrlKey: true })).toBe('undo');
    expect(k('z', { metaKey: true })).toBe('undo');
    expect(k('Z', { ctrlKey: true, shiftKey: true })).toBe('redo');
    expect(k('y', { ctrlKey: true })).toBe('redo');
    expect(k('z')).toBeNull();
    expect(k('=')).toBe('zoomIn');
    expect(k('+', { shiftKey: true })).toBe('zoomIn');
    expect(k('-')).toBe('zoomOut');
    expect(k('\\')).toBe('zoomFit');
  });

  it('ignores everything else', () => {
    expect(k('q')).toBeNull();
    expect(k('F5')).toBeNull();
    expect(k('ArrowLeft', { ctrlKey: true })).toBeNull();
  });
});
