import test from 'node:test';
import assert from 'node:assert/strict';
import { createCelebrationGate, celebrationKey, startMascot } from '../web/lib/mascot-celebration.ts';

function fixture() {
  const calls = [];
  const instance = { trigger(name) { assert.equal(name, 'celebrate'); return { trigger() { calls.push('trigger'); } }; } };
  const rive = {
    viewModelByName(name) { assert.equal(name, 'AudliMascotControls'); return { defaultInstance: () => instance }; },
    setViewModelInstance(value) { assert.equal(value, instance); calls.push('instance'); },
    bind() { calls.push('bind'); },
    play(name) { assert.equal(name, 'AudliMascotMachine'); calls.push('play'); },
  };
  return { rive, calls };
}

test('binds the ViewModel before playback; fresh completion fires once across repeated setup/remounts', () => {
  const claim = createCelebrationGate(), key = celebrationKey('account', 'lesson');
  const { rive, calls } = fixture();
  startMascot(rive, key, true, claim, null);
  assert.deepEqual(calls, ['instance', 'bind', 'play', 'trigger']);
  startMascot(rive, key, true, claim, null);
  assert.equal(calls.filter(value => value === 'trigger').length, 1);
});

test('restoration is idle, while distinct lessons and accounts can each celebrate', () => {
  const claim = createCelebrationGate(), { rive, calls } = fixture();
  startMascot(rive, celebrationKey('a', 'restored'), false, claim, null);
  assert.equal(calls.includes('trigger'), false);
  for (const [account, lesson] of [['a', 'one'], ['a', 'two'], ['b', 'one']]) {
    startMascot(rive, celebrationKey(account, lesson), true, claim, null);
  }
  assert.equal(calls.filter(value => value === 'trigger').length, 3);
});

test('presentation storage prevents replay after a new gate is created', () => {
  const saved = new Map();
  const storage = { getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value) };
  assert.equal(createCelebrationGate()('lesson', storage), true);
  assert.equal(createCelebrationGate()('lesson', storage), false);
});

test('reduced motion consumes a celebration so re-enabling it cannot cause late playback', () => {
  const claim = createCelebrationGate(), { rive, calls } = fixture();
  claim('suppressed', null);
  startMascot(rive, 'suppressed', true, claim, null);
  assert.equal(calls.includes('trigger'), false);
});

test('unavailable storage still keeps one-shot protection in memory', () => {
  const broken = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
  const claim = createCelebrationGate();
  assert.equal(claim('fresh', broken), true);
  assert.equal(claim('fresh', broken), false);
});

test('invalid binding fails before consuming the completion', () => {
  let claimed = false;
  assert.throws(() => startMascot({ viewModelByName: () => null }, 'fresh', true, () => { claimed = true; return true; }, null));
  assert.equal(claimed, false);
});


test('initialization finishes before the one-shot trigger is consumed', () => {
  const claim = createCelebrationGate(), { rive, calls } = fixture();
  let initialized;
  startMascot(rive, 'initialized', true, claim, null, fire => { initialized = fire; });
  assert.deepEqual(calls, ['instance', 'bind', 'play']);
  initialized(); initialized();
  assert.equal(calls.filter(value => value === 'trigger').length, 1);
});
