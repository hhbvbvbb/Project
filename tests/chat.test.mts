import test from 'node:test';
import assert from 'node:assert/strict';
import handler, { caption, validUser, ApiError } from '../netlify/functions/chat.mjs';

for (const path of ['/api/me', '/api/conversations', '/api/messages?conversation=private', '/api/files/00000000-0000-0000-0000-000000000000']) {
  test(`anonymous ${path} returns JSON 401 rather than a hosting 404`, async () => {
    const response = await handler(new Request(`https://chat.example${path}`));
    assert.equal(response.status, 401);
    assert.match(response.headers.get('content-type')!, /application\/json/);
    assert.deepEqual(await response.json(), { error: 'Sign in required' });
  });
}
test('cross-origin login is rejected before authentication', async () => {
  const response = await handler(new Request('https://chat.example/api/login', {
    method: 'POST', headers: { Origin: 'https://attacker.example' },
  }));
  assert.equal(response.status, 403);
});
test('malformed login JSON returns a useful client error', async () => {
  const response = await handler(new Request('https://chat.example/api/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{',
  }));
  assert.equal(response.status, 400);
});
test('unsupported methods return 405', async () => {
  assert.equal((await handler(new Request('https://chat.example/api/messages', { method: 'DELETE' }))).status, 405);
});
test('login rejects accounts outside the fixed roster', async () => {
  const response = await handler(new Request('https://chat.example/api/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'outsider', password: 'unused' }),
  }));
  assert.equal(response.status, 401);
});
test('inherited object properties cannot become chat accounts', () => {
  assert.equal(validUser('constructor'), false);
  assert.equal(validUser('__proto__'), false);
  assert.equal(validUser('kyle'), true);
});
test('messages reject empty or oversized text and trim valid text', () => {
  assert.throws(() => caption('  '), ApiError);
  assert.throws(() => caption('a'.repeat(2001)), ApiError);
  assert.equal(caption(' hi '), 'hi');
  assert.equal(caption('', false), '');
});
