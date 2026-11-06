import assert from 'node:assert/strict';
import { DEFAULT_SETTINGS, SETTINGS_SPEEDS, loadSettings, parseSettings, saveSettings, serializeSettings, settingsKey } from '../web/lib/settings-core.ts';

const memory = () => { const m = new Map(); return { getItem: k => m.get(k) ?? null, setItem: (k, v) => void m.set(k, v), m }; };

assert.deepEqual(parseSettings(undefined), DEFAULT_SETTINGS);
assert.deepEqual(parseSettings('not json {'), DEFAULT_SETTINGS);                                      // corrupt storage
assert.deepEqual(parseSettings({ speed: 3, transcriptMode: 'review', textSize: 'huge', reduceMotion: 'yes' }), DEFAULT_SETTINGS);   // invalid values, incl. the unsupported Review mode
assert.deepEqual(parseSettings({ speed: 0.75, transcriptMode: 'guided', textSize: 'large', reduceMotion: true, highContrast: true }),
  { speed: 0.75, transcriptMode: 'guided', textSize: 'large', reduceMotion: true, highContrast: true });
assert.deepEqual([...SETTINGS_SPEEDS], [0.75, 0.9, 1, 1.25]);                                         // the four chips in Figma

// each account has its own preferences, even on one browser
const store = memory();
assert.ok(saveSettings(store, 'user-a', { ...DEFAULT_SETTINGS, speed: 1.25 }));
assert.equal(loadSettings(store, 'user-a').speed, 1.25);
assert.equal(loadSettings(store, 'user-b').speed, 1);
assert.notEqual(settingsKey('user-a'), settingsKey('user-b'));
assert.equal(serializeSettings({ ...DEFAULT_SETTINGS, speed: 9 }), serializeSettings(DEFAULT_SETTINGS));   // never persists an invalid value

// storage that throws or is absent never breaks the page
const blocked = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('quota'); } };
assert.deepEqual(loadSettings(blocked, 'x'), DEFAULT_SETTINGS);
assert.equal(saveSettings(blocked, 'x', DEFAULT_SETTINGS), false);
assert.equal(saveSettings(null, 'x', DEFAULT_SETTINGS), false);
assert.deepEqual(loadSettings(null, 'x'), DEFAULT_SETTINGS);
console.log('settings tests passed');
