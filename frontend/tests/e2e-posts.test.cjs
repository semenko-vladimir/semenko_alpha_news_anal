const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { capturePosts } = require('./e2e-posts.cjs');
const base = 'http://127.0.0.1:1234';
const wanted = role => [
  { operation: 'run', path: '/api/v1/companies/A/analysis-runs', role },
  { operation: 'answer', path: `/api/v1/insights/${role}/questions`, questionId: `q-${role}` },
  { operation: 'message', path: `/api/v1/insights/${role}/messages` },
];
function emit(page, operation, overrides = {}) {
  const request = {
    method: () => 'POST', url: () => base + operation.path,
    redirectedFrom: () => null, resourceType: () => 'fetch',
    postDataJSON: () => ({ idempotency_key: 'private-key', role_id: operation.role,
      question_id: operation.questionId, text: 'PRIVATE USER TEXT', answer: 'PRIVATE ANSWER' }),
    ...overrides,
  };
  page.emit('request', request);
  page.emit('response', { request: () => request, status: () => operation.operation === 'run' ? 202 : 200 });
  return request;
}
test('six scoped operations pass, diagnostic output omits bodies/keys/query/fragment', () => {
  const page = new EventEmitter(), network = capturePosts(page, base);
  const expected = [...wanted('CFO'), ...wanted('COO')];
  for (const operation of expected) emit(page, operation, { url: () => base + operation.path + '?secret=hidden#fragment' });
  network.assertOperations(expected);
  assert.equal(network.posts.length, 6);
  const serialized = JSON.stringify(network.snapshot());
  for (const secret of ['PRIVATE', 'private-key', 'hidden', 'fragment', 'idempotency_key']) assert.ok(!serialized.includes(secret));
});
test('duplicate question/message and unknown API POSTs fail exact count', () => {
  for (const extra of [wanted('CFO')[1], wanted('CFO')[2], { path: '/api/v1/unexpected' }]) {
    const page = new EventEmitter(), network = capturePosts(page, base), expected = wanted('CFO');
    for (const operation of [...expected, extra]) emit(page, operation);
    assert.throws(() => network.assertOperations(expected), /Unexpected API POST count/);
  }
});
test('wrong company/role/insight/question and redirects fail despite matching totals', () => {
  for (const replacement of [
    { index: 0, change: { path: '/api/v1/companies/B/analysis-runs' } },
    { index: 0, change: { role: 'COO' } },
    { index: 1, change: { path: '/api/v1/insights/OTHER/questions' } },
    { index: 1, change: { questionId: 'other-question' } },
  ]) {
    const page = new EventEmitter(), network = capturePosts(page, base), expected = wanted('CFO');
    expected.forEach((operation, index) => emit(page, index === replacement.index ? { ...operation, ...replacement.change } : operation));
    assert.throws(() => network.assertOperations(expected));
  }
  const page = new EventEmitter(), network = capturePosts(page, base), expected = wanted('CFO');
  expected.forEach(operation => emit(page, operation, { redirectedFrom: () => ({ url: () => base + '/redirect?hidden=yes' }) }));
  assert.throws(() => network.assertOperations(expected), /must not be redirected/);
  assert.deepEqual(network.posts[0].redirected_from, { origin: base, path: '/redirect' });
});
test('runner counts six business POSTs while retaining external non-JSON diagnostics', () => {
  const page = new EventEmitter(), network = capturePosts(page, base), expected = [...wanted('CFO'), ...wanted('COO')];
  for (const operation of expected) emit(page, operation);
  for (const externalPath of ['/telemetry', '/api/v1/companies/A/analysis-runs']) {
    emit(page, {}, { url: () => `https://example.invalid${externalPath}?private=yes`,
      postDataJSON: () => { throw new Error('Non-JSON must not be parsed'); } });
  }
  network.assertOperations(expected);
  assert.equal(network.businessPosts.length, 6); // Same count used by the actual runner.
  assert.equal(network.posts.length, 8);
  assert.equal(network.snapshot().posts.filter(post => !post.same_origin).length, 2);
  assert.equal(network.posts.at(-1).same_origin, false);
});
test('failed business request is recorded and rejected', () => {
  const page = new EventEmitter(), network = capturePosts(page, base), expected = wanted('CFO');
  const requests = expected.map(operation => emit(page, operation));
  page.emit('requestfailed', requests[2]);
  assert.equal(network.posts[2].failed, true);
  assert.throws(() => network.assertOperations(expected), /Business POST failed/);
});
