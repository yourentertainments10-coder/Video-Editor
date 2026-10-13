export type Command =
  | 'playPause' | 'prevFrame' | 'nextFrame' | 'back1s' | 'forward1s'
  | 'prevEdit' | 'nextEdit' | 'home' | 'end' | 'split' | 'addText' | 'delete'
  | 'undo' | 'redo' | 'zoomIn' | 'zoomOut' | 'zoomFit' | 'saveNow';

export type KeyInput = {
  key: string; ctrlKey: boolean; shiftKey: boolean; altKey: boolean; metaKey: boolean;
};

export function commandForKey(input: KeyInput): Command | null {
  const { key, ctrlKey, shiftKey, altKey, metaKey } = input;
  const commandModifier = ctrlKey || metaKey;
  if (altKey) return null;
  if (commandModifier) {
    if ((key === 's' || key === 'S') && !shiftKey) return 'saveNow';
    if ((key === 'z' || key === 'Z') && shiftKey) return 'redo';
    if ((key === 'z' || key === 'Z') && !shiftKey) return 'undo';
    if ((key === 'y' || key === 'Y') && !shiftKey) return 'redo';
    return null;
  }
  if (key === ' ' || key === 'Spacebar') return 'playPause';
  if (key === 'ArrowLeft') return shiftKey ? 'back1s' : 'prevFrame';
  if (key === 'ArrowRight') return shiftKey ? 'forward1s' : 'nextFrame';
  if (key === '+' || key === '=') return 'zoomIn';
  if (shiftKey) return null;
  if (key === 'ArrowUp') return 'prevEdit';
  if (key === 'ArrowDown') return 'nextEdit';
  if (key === 'Home') return 'home';
  if (key === 'End') return 'end';
  if (key.toLowerCase() === 's') return 'split';
  if (key.toLowerCase() === 't') return 'addText';
  if (key === 'Delete' || key === 'Backspace') return 'delete';
  if (key === '-') return 'zoomOut';
  if (key === '\\') return 'zoomFit';
  return null;
}
