import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseRecoveryLink, passwordValidation, recoveryRedirect, recoverySessionExpired } from '../web/lib/password-recovery.ts';
import { isLearnerAuthCallback } from '../web/lib/auth.ts';

const token = exp => `header.${Buffer.from(JSON.stringify({ exp })).toString('base64url')}.signature`;
const callback = (access = token(2000)) => `https://app.audli.ai/auth/recovery#type=recovery&access_token=${access}&refresh_token=refresh-token`;

test('learner SDK callback detection preserves normal restoration and signup while excluding recovery', () => {
  assert.equal(isLearnerAuthCallback({}), false);
  assert.equal(isLearnerAuthCallback({ signin: '1' }), false);
  assert.equal(isLearnerAuthCallback({ type: 'signup', access_token: 'token' }), true);
  assert.equal(isLearnerAuthCallback({ error_code: 'confirmation_error' }), true);
  assert.equal(isLearnerAuthCallback({ type: 'recovery', access_token: 'token' }), false);
  assert.equal(isLearnerAuthCallback({ type: 'recovery', error: 'expired' }), false);
});

test('production redirect is fixed to approved HTTPS domain, including previews and hostile input', () => {
  for (const origin of ['https://app.audli.ai', 'https://preview.vercel.app', 'http://localhost:3100', 'https://app.audli.ai.evil.example', 'https://evil.example/?next=https://evil.example']) {
    assert.equal(recoveryRedirect(origin, false), 'https://app.audli.ai/auth/recovery');
  }
  assert.equal(recoveryRedirect('http://127.0.0.1:3100', true), 'http://127.0.0.1:3100/auth/recovery');
  assert.equal(recoveryRedirect('http://evil.example', true), 'https://app.audli.ai/auth/recovery');
});
test('only explicit recovery links are accepted; arbitrary navigation is ignored', () => {
  assert.equal(parseRecoveryLink(callback() + '&next=https://evil.example', 1000).kind, 'session');
  assert.deepEqual(parseRecoveryLink('https://app.audli.ai/auth/recovery?type=recovery&token_hash=0123456789abcdef&next=//evil.example'), { kind: 'hash', tokenHash: '0123456789abcdef' });
  for (const url of ['bad-url', callback().replace('type=recovery', 'type=signup'), callback() + '&type=recovery',
    callback().replace('#', '?type=recovery#'), 'https://app.audli.ai/auth/recovery?type=recovery&code=pkce',
    'https://app.audli.ai/auth/recovery?type=recovery&token_hash=short',
    'https://app.audli.ai/auth/recovery?type=recovery&token_hash=0123456789abcdef&token_hash=0123456789abcdef',
    callback() + '&error_code=otp_expired', callback().replace('refresh_token=refresh-token', ''),
    callback(token(999)), callback(token('invalid')), callback('malformed'), callback('x'.repeat(25000))]) {
    assert.deepEqual(parseRecoveryLink(url, 1000), { kind: 'invalid' });
  }
});
test('password validation preserves characters, requires confirmation and bounded length', () => {
  assert.match(passwordValidation('short', 'short'), /at least 8/);
  assert.match(passwordValidation('a'.repeat(129), 'a'.repeat(129)), /128/);
  assert.match(passwordValidation('        ', '        '), /only spaces/);
  assert.match(passwordValidation('new-password', 'different'), /do not match/);
  for (const password of ['12345678', 'a'.repeat(128), ' with spaces ', 'pässword☀']) assert.equal(passwordValidation(password, password), null);
});
test('expired/revoked recovery authorization fails closed while policy errors remain recoverable', () => {
  assert.equal(recoverySessionExpired('otp_expired', 403), true);
  assert.equal(recoverySessionExpired(undefined, 401), true);
  assert.equal(recoverySessionExpired('session_not_found', 400), true);
  assert.equal(recoverySessionExpired('weak_password', 422), false);
  assert.equal(recoverySessionExpired(undefined, 503), false);
});
