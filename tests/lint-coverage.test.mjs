import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';

// Run in each project: exercise the installed tools and the actual configs.
const cwd = process.cwd();
const require = createRequire(join(cwd, 'package.json'));
const { ESLint } = require('eslint');
const eslint = new ESLint({ cwd });
const codes = async (source, file = 'lint-fixture.tsx') =>
  (await eslint.lintText(source, { filePath: join(cwd, file) }))[0].messages.map(m => m.ruleId);

test('React, hooks, accessibility and import rules remain effective', async () => {
  assert.ok((await codes('export default function View(){ return <img />; }')).includes('jsx-a11y/alt-text'));
  assert.ok((await codes("import {useState} from 'react'; export function View({ok}:{ok:boolean}){ if(ok) useState(0); return null; }")).includes('react-hooks/rules-of-hooks'));
  assert.ok((await codes('export function View(){return <div>{[1,2].map(x=><span>{x}</span>)}</div>;}')).includes('react/jsx-key'));
  assert.ok((await codes('export default {};', 'lint-fixture.mjs')).includes('import/no-anonymous-default-export'));
});

test('navigation protection covers direct, computed and dynamic destinations', async () => {
  for (const expression of [
    "location.assign('/plan')", "window.location['assign']('/plan')",
    "globalThis['location'].assign('/plan')", "document.location.href = '/plan'",
    "self['location']['href'] = '/plan'", "location.assign(destination)",
    "window.location.href = 'https://example.com'",
  ]) assert.ok((await codes(expression + ';')).includes('no-restricted-syntax'), expression);
  assert.ok(!(await codes('const url = window.location.href;')).includes('no-restricted-syntax'));
});

test('web retains recommended TypeScript coverage; marketing retains its parser', async () => {
  const config = await eslint.calculateConfigForFile(join(cwd, 'lint-fixture.tsx'));
  assert.ok(config.languageOptions.parser);
  if (JSON.parse(readFileSync(join(cwd, 'package.json'))).name === 'audli-web') {
    assert.ok((await codes('export const value: any = 1;')).includes('@typescript-eslint/no-explicit-any'));
    assert.equal(config.rules['@typescript-eslint/no-unused-vars'][0], 1);
    assert.equal(config.rules['@typescript-eslint/no-unused-expressions'][0], 1);
  }
});

test('Next rule set is available and dangerous scripts still fail lint', () => {
  const executable = join(cwd, 'node_modules', 'oxlint', 'bin', 'oxlint');
  const listed = spawnSync(process.execPath, [executable, '--rules', '--format', 'json'], { cwd, encoding: 'utf8' });
  assert.equal(listed.status, 0, listed.stderr);
  const supported = JSON.parse(listed.stdout).filter(r => r.scope === 'nextjs').map(r => r.value);
  const config = JSON.parse(readFileSync(join(cwd, '.oxlintrc.json')));
  assert.equal(Object.keys(config.rules).length, 21);
  for (const rule of Object.keys(config.rules)) assert.ok(supported.includes(rule.replace('nextjs/', '')), rule);
  assert.equal(config.rules['nextjs/no-html-link-for-pages'], 'error');
  assert.equal(config.rules['nextjs/no-sync-scripts'], 'error');
  const folder = mkdtempSync(join(cwd, '.aud28-lint-'));
  try {
    const file = join(folder, 'unsafe.tsx');
    writeFileSync(file, '"use client"; export default async function View(){return <><a href="/">Home</a><head><title>Title</title></head><script src="/local.js"/></>;}');
    const result = spawnSync(process.execPath, [executable, '--config', join(cwd, '.oxlintrc.json'), '--format', 'json', file], { cwd, encoding: 'utf8' });
    assert.equal(result.status, 1, result.stderr);
    const diagnostics = JSON.parse(result.stdout).diagnostics;
    for (const [rule, severity] of [
      ['no-sync-scripts', 'error'], ['no-html-link-for-pages', 'error'],
      ['no-async-client-component', 'warning'], ['no-head-element', 'warning'],
    ]) assert.ok(diagnostics.some(d => d.code === `next(${rule})` && d.severity === severity), rule);
  } finally { rmSync(folder, { recursive: true, force: true }); }
});

test('neither installed graph retains the vulnerable glob dependency chain', () => {
  const lock = JSON.parse(readFileSync(join(cwd, 'package-lock.json')));
  assert.deepEqual(Object.keys(lock.packages).filter(path => /\/(braces|micromatch|fast-glob|eslint-config-next|@next\/eslint-plugin-next)$/.test(path)), []);
});
