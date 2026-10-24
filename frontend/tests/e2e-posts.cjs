const assert = require('node:assert/strict');

// Retain only network metadata. Bodies stay on Playwright Request objects and
// are read transiently for assertions; never persist text or idempotency keys.
function capturePosts(page, base) {
  const origin = new URL(base).origin;
  const posts = [];
  const records = new Map();
  const isBusinessPost = post => post.same_origin && post.path.startsWith('/api/');
  page.on('request', request => {
    if (request.method() !== 'POST') return;
    const url = new URL(request.url());
    const redirectedFrom = request.redirectedFrom();
    const previous = redirectedFrom && new URL(redirectedFrom.url());
    const operation = /^\/api\/v1\/companies\/[^/]+\/analysis-runs$/.test(url.pathname) ? 'run'
      : /^\/api\/v1\/insights\/[^/]+\/questions$/.test(url.pathname) ? 'answer'
      : /^\/api\/v1\/insights\/[^/]+\/messages$/.test(url.pathname) ? 'message' : null;
    const record = { origin: url.origin, path: url.pathname, operation,
      same_origin: url.origin === origin, resource_type: request.resourceType(),
      redirected_from: previous ? { origin: previous.origin, path: previous.pathname } : null,
      status: null, failed: false };
    records.set(request, record);
    posts.push(record);
  });
  page.on('response', response => {
    const record = records.get(response.request());
    if (record) record.status = response.status();
  });
  page.on('requestfailed', request => {
    const record = records.get(request);
    if (record) record.failed = true;
  });
  return {
    posts,
    get businessPosts() { return posts.filter(isBusinessPost); },
    snapshot: () => ({ posts, counts: posts.reduce((counts, post) => {
      const key = `${post.origin}${post.path}`;
      counts[key] = (counts[key] || 0) + 1;
      return counts;
    }, {}) }),
    assertOperations(expected) {
      // Includes ALL same-origin API POSTs, even unrecognised operations.
      const actual = [...records].filter(([, post]) => isBusinessPost(post));
      assert.equal(actual.length, expected.length, 'Unexpected API POST count; inspect network diagnostics');
      actual.forEach(([request, post], index) => {
        const wanted = expected[index];
        assert.equal(post.path, wanted.path, 'POST scope/order');
        assert.equal(post.operation, wanted.operation);
        assert.equal(post.redirected_from, null, 'Business POST must not be redirected');
        assert.equal(post.failed, false, 'Business POST failed');
        assert.equal(post.status, wanted.operation === 'run' ? 202 : 200);
        const body = request.postDataJSON();
        assert.ok(body && typeof body.idempotency_key === 'string' && body.idempotency_key.length > 0);
        if (wanted.role) assert.equal(body.role_id, wanted.role);
        if (wanted.questionId) assert.equal(body.question_id, wanted.questionId);
      });
    },
  };
}

module.exports = { capturePosts };
