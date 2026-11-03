import test from 'node:test';
import assert from 'node:assert/strict';
import nodeCrypto from 'node:crypto';

// Polyfill crypto.subtle.timingSafeEqual in test harness if not present
if (!globalThis.crypto.subtle.timingSafeEqual) {
  globalThis.crypto.subtle.timingSafeEqual = (a, b) => nodeCrypto.timingSafeEqual(a, b);
}

import worker from '../src/index.js';

const VALID_TOKEN = 'a'.repeat(32);

/**
 * Mock D1 Database at the isolate boundary.
 * Allows simulating:
 * 1. Crash immediately after env.DB.batch() commit, BEFORE any UPDATE response_body.
 * 2. Race condition: concurrent requests where both see key not found, one commits,
 *    and the second encounters UNIQUE constraint failure on _idempotency_log.
 */
function createMockDB(options = {}) {
  const idempotencyStore = new Map();
  let failOnUpdate = options.failOnUpdate ?? false;
  let crashAfterBatch = options.crashAfterBatch ?? false;

  return {
    idempotencyStore,
    prepare(sql) {
      return {
        _sql: sql,
        _params: [],
        bind(...params) {
          this._params = params;
          return this;
        },
        async first() {
          if (sql.includes('SELECT payload_hash, response_body FROM _idempotency_log')) {
            const key = this._params[0];
            const record = idempotencyStore.get(key);
            if (!record) return null;
            return {
              payload_hash: record.payload_hash,
              response_body: record.response_body,
            };
          }
          return null;
        },
        async run() {
          if (sql.includes('UPDATE _idempotency_log SET response_body')) {
            if (failOnUpdate) {
              throw new Error('Simulated D1 transport failure during UPDATE response_body');
            }
            const body = this._params[0];
            const key = this._params[1];
            const existing = idempotencyStore.get(key);
            if (existing) {
              existing.response_body = body;
            }
            return { meta: { changes: 1 } };
          }
          return { meta: { changes: 1 } };
        },
        async all() {
          return { results: [], meta: { changes: 0 } };
        },
        async raw(options) {
          if (options && options.columnNames) {
            if (sql.includes('SELECT a.id, a.name, c.id, c.name FROM assets a JOIN categories c') || (sql.includes('a.id') && sql.includes('c.id'))) {
              return [
                ['id', 'name', 'id', 'name'],
                [101, 'Asset Laptop', 201, 'Category Hardware'],
              ];
            }
            return [
              ['id', 'code'],
              [101, 'AST-001'],
            ];
          }
          return [
            [101, 'AST-001'],
          ];
        },
      };
    },
    async batch(statements) {
      // Execute statements atomically in mock
      let results = [];
      for (const stmt of statements) {
        const sql = stmt._sql;
        const params = stmt._params;

        if (sql.includes('INSERT INTO _idempotency_log')) {
          const key = params[0];
          const hash = params[1];
          let initialBody;

          if (sql.includes('COMMITTED_PENDING_RESOLUTION')) {
            initialBody = JSON.stringify({
              status: 'COMMITTED_PENDING_RESOLUTION',
              batch_size: statements.length - 1,
            });
          } else if (sql.includes('INCOMPLETE_RETURNING_STATE')) {
            initialBody = JSON.stringify({
              status: 'INCOMPLETE_RETURNING_STATE',
              committed: true,
            });
          } else if (params.length >= 3 && params[2]) {
            let parsedParam2;
            try {
              parsedParam2 = typeof params[2] === 'string' ? JSON.parse(params[2]) : params[2];
            } catch {
              parsedParam2 = params[2];
            }

            if (parsedParam2 && parsedParam2.status === 'COMMITTED_PENDING_RESOLUTION') {
              initialBody = typeof params[2] === 'string' ? params[2] : JSON.stringify(params[2]);
            } else if (parsedParam2 && parsedParam2.status === 'INCOMPLETE_RETURNING_STATE') {
              initialBody = typeof params[2] === 'string' ? params[2] : JSON.stringify(params[2]);
            } else if (Array.isArray(parsedParam2)) {
              initialBody = JSON.stringify({
                success: true,
                results: parsedParam2,
              });
            } else {
              initialBody = typeof params[2] === 'string' ? params[2] : JSON.stringify(params[2]);
            }
          } else {
            // Query mode: Check if last_insert_rowid() is injected into results array
            const hasReturningId = sql.includes("json_object('id', last_insert_rowid())") || sql.includes("'id', last_insert_rowid()");
            initialBody = JSON.stringify({
              success: true,
              results: hasReturningId ? [{ id: 101 }] : [],
              meta: { changes: 1, last_row_id: 101, duration: 0 },
            });
          }

          if (idempotencyStore.has(key)) {
            throw new Error('D1_ERROR: UNIQUE constraint failed: _idempotency_log.idempotency_key');
          }
          idempotencyStore.set(key, { payload_hash: hash, response_body: initialBody });
          results.push({ results: [], meta: { changes: 1 } });
        } else if (sql.includes('RETURNING id, code') || sql.includes('RETURNING "id", "code"') || sql.includes('RETURNING "id", code')) {
          results.push({
            results: [{ id: 101, code: params[0] || 'AST-001' }],
            meta: { changes: 1, last_row_id: 101 },
          });
        } else if (/RETURNING\s+(?:(?:"?[a-zA-Z0-9_]+"?)?\.)?"?id"?/i.test(sql)) {
          results.push({
            results: [{ id: 101 }],
            meta: { changes: 1, last_row_id: 101 },
          });
        } else {
          results.push({
            results: [],
            meta: { changes: 1, last_row_id: 101 },
          });
        }
      }

      if (crashAfterBatch) {
        throw new Error('Simulated crash immediately after commit, before UPDATE');
      }

      return results;
    },
  };
}

function makeRequest(path, body, headers = {}) {
  return new Request(`https://test-worker.local${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Bridge-Token': VALID_TOKEN,
      ...headers,
    },
    body: JSON.stringify(body),
  });
}

test('Regression Defect 2: Race condition winnerObj must not throw ReferenceError', async () => {
  const db = createMockDB();
  const env = { BRIDGE_SECRET_TOKEN: VALID_TOKEN, DB: db };
  const idemKey = 'test-race-winner-obj';
  const body = { sql: 'INSERT INTO departments_department (code) VALUES (?)', params: ['D1'] };

  // Simulate exact concurrent race condition:
  // Both requests start at the same time: both execute first() and see NO existing key!
  // Request 1 enters batch() and commits.
  // Request 2 enters batch() and hits UNIQUE constraint collision!
  let r2FirstCallCount = 0;
  const racingDB = {
    prepare(sql) {
      const baseStmt = db.prepare(sql);
      return {
        _sql: sql,
        _params: [],
        bind(...params) {
          this._params = params;
          baseStmt.bind(...params);
          return this;
        },
        async first() {
          if (sql.includes('SELECT payload_hash, response_body FROM _idempotency_log')) {
            r2FirstCallCount++;
            if (r2FirstCallCount === 1) {
              return null; // Both read key as not yet existing!
            }
          }
          return baseStmt.first();
        },
        async raw(options) {
          return baseStmt.raw ? baseStmt.raw(options) : [];
        },
      };
    },
    async batch(statements) {
      // Winner commits to db
      await db.batch(statements);
      // Loser hits UNIQUE constraint
      throw new Error('D1_ERROR: UNIQUE constraint failed: _idempotency_log.idempotency_key');
    },
  };

  const racingEnv = { BRIDGE_SECRET_TOKEN: VALID_TOKEN, DB: racingDB };
  const req = makeRequest('/api/v1/query', body, { 'Idempotency-Key': idemKey });

  const res = await worker.fetch(req, racingEnv);
  assert.equal(res.status, 200, 'Loser must recover committed result and return 200');
  const json = await res.json();
  assert.equal(json.success, true);
  assert.equal(res.headers.get('X-Idempotent-Replay'), 'true');
});

test('Test 1a: INSERT with RETURNING "id" (quoted) - crash after commit, replay must return correct extracted ID from last_insert_rowid()', async () => {
  const db = createMockDB({ failOnUpdate: true }); // Fails at UPDATE response_body
  const env = { BRIDGE_SECRET_TOKEN: VALID_TOKEN, DB: db };
  const idemKey = 'test-query-returning-quoted-id';
  const body = {
    sql: 'INSERT INTO "departments_department" ("code", "name") VALUES (?, ?) RETURNING "id"',
    params: ['D101A', 'Test Dept 1a'],
  };

  // Step 1: Request 1 commits batch, but fails during UPDATE response_body
  const req1 = makeRequest('/api/v1/query', body, { 'Idempotency-Key': idemKey });
  const res1 = await worker.fetch(req1, env);
  assert.equal(res1.status, 500, 'Initial call must fail on simulated UPDATE failure');

  // Step 2: Client retries with same key
  db.failOnUpdate = false;
  const req2 = makeRequest('/api/v1/query', body, { 'Idempotency-Key': idemKey });
  const res2 = await worker.fetch(req2, env);

  assert.equal(res2.status, 200, 'Replay must return 200');
  const json2 = await res2.json();
  assert.equal(json2.success, true);
  assert.ok(Array.isArray(json2.results), 'results must be an array');
  assert.equal(json2.results.length, 1, 'results must have exactly 1 row');
  assert.equal(json2.results[0].id, 101, 'Quoted RETURNING "id" must be correctly extracted on replay');
});

test('Test 1b: INSERT with RETURNING "departments_department"."id" (quoted & table-prefixed) - crash after commit, replay must return correct extracted ID', async () => {
  const db = createMockDB({ failOnUpdate: true });
  const env = { BRIDGE_SECRET_TOKEN: VALID_TOKEN, DB: db };
  const idemKey = 'test-query-returning-table-prefixed-quoted-id';
  const body = {
    sql: 'INSERT INTO "departments_department" ("code", "name") VALUES (?, ?) RETURNING "departments_department"."id"',
    params: ['D101B', 'Test Dept 1b'],
  };

  // Step 1: Request 1 commits batch, but fails during UPDATE response_body
  const req1 = makeRequest('/api/v1/query', body, { 'Idempotency-Key': idemKey });
  const res1 = await worker.fetch(req1, env);
  assert.equal(res1.status, 500, 'Initial call must fail on simulated UPDATE failure');

  // Step 2: Client retries with same key
  db.failOnUpdate = false;
  const req2 = makeRequest('/api/v1/query', body, { 'Idempotency-Key': idemKey });
  const res2 = await worker.fetch(req2, env);

  assert.equal(res2.status, 200, 'Replay must return 200');
  const json2 = await res2.json();
  assert.equal(json2.success, true);
  assert.ok(Array.isArray(json2.results), 'results must be an array');
  assert.equal(json2.results.length, 1, 'results must have exactly 1 row');
  assert.equal(json2.results[0].id, 101, 'Table-prefixed quoted RETURNING "table"."id" must be correctly extracted on replay');
});

test('Test 1c: INSERT with RETURNING id (unquoted) - standard behavior preserved', async () => {
  const db = createMockDB({ failOnUpdate: true });
  const env = { BRIDGE_SECRET_TOKEN: VALID_TOKEN, DB: db };
  const idemKey = 'test-query-returning-unquoted-id';
  const body = {
    sql: 'INSERT INTO departments_department (code, name) VALUES (?, ?) RETURNING id',
    params: ['D101C', 'Test Dept 1c'],
  };

  // Step 1: Request 1 commits batch, but fails during UPDATE response_body
  const req1 = makeRequest('/api/v1/query', body, { 'Idempotency-Key': idemKey });
  const res1 = await worker.fetch(req1, env);
  assert.equal(res1.status, 500, 'Initial call must fail on simulated UPDATE failure');

  // Step 2: Client retries with same key
  db.failOnUpdate = false;
  const req2 = makeRequest('/api/v1/query', body, { 'Idempotency-Key': idemKey });
  const res2 = await worker.fetch(req2, env);

  assert.equal(res2.status, 200, 'Replay must return 200');
  const json2 = await res2.json();
  assert.equal(json2.success, true);
  assert.ok(Array.isArray(json2.results), 'results must be an array');
  assert.equal(json2.results.length, 1, 'results must have exactly 1 row');
  assert.equal(json2.results[0].id, 101, 'Unquoted RETURNING id standard behavior must be preserved');
});

test('Test 2: Batch crash before update - replay must receive HTTP 500 with code: "INCOMPLETE_BATCH_STATE" and committed: true, absolutely ZERO fake changes: 1 or mock results shells!', async () => {
  const db = createMockDB({ failOnUpdate: true });
  const env = { BRIDGE_SECRET_TOKEN: VALID_TOKEN, DB: db };
  const idemKey = 'test-batch-crash-incomplete';
  const body = {
    statements: [
      { sql: 'INSERT INTO departments_department (code) VALUES (?)', params: ['D102'] },
      { sql: 'SELECT count(*) FROM departments_department' },
    ],
  };

  // Step 1: Request 1 commits batch, but fails during UPDATE response_body
  const req1 = makeRequest('/api/v1/batch', body, { 'Idempotency-Key': idemKey });
  const res1 = await worker.fetch(req1, env);
  assert.equal(res1.status, 500, 'Initial request must fail with 500');

  // Step 2: Client retries with same key
  db.failOnUpdate = false;
  const req2 = makeRequest('/api/v1/batch', body, { 'Idempotency-Key': idemKey });
  const res2 = await worker.fetch(req2, env);

  // Replay must receive HTTP 500 with INCOMPLETE_BATCH_STATE and committed: true
  assert.equal(res2.status, 500, 'Replay must return HTTP 500');
  const json2 = await res2.json();
  assert.equal(json2.code, 'INCOMPLETE_BATCH_STATE', 'Error code must be INCOMPLETE_BATCH_STATE');
  assert.equal(json2.committed, true, 'committed flag must be true');
  // ZERO fake changes: 1 or mock results shells!
  assert.equal(json2.results, undefined, 'Must not return fake results array shell');
});

test('Test 3: UPDATE / DELETE with RETURNING or multi-column returning outside scope: crash before update must NOT fake results', async () => {
  const env = { BRIDGE_SECRET_TOKEN: VALID_TOKEN };

  // Case 3A: UPDATE with RETURNING id
  {
    const dbUpdate = createMockDB({ failOnUpdate: true });
    env.DB = dbUpdate;
    const idemKey = 'test-update-returning-scope';
    const body = {
      sql: 'UPDATE departments_department SET name = ? WHERE id = ? RETURNING id',
      params: ['Updated Name', 101],
    };

    const req1 = makeRequest('/api/v1/query', body, { 'Idempotency-Key': idemKey });
    const res1 = await worker.fetch(req1, env);
    assert.equal(res1.status, 500);

    dbUpdate.failOnUpdate = false;
    const req2 = makeRequest('/api/v1/query', body, { 'Idempotency-Key': idemKey });
    const res2 = await worker.fetch(req2, env);

    if (res2.status === 200) {
      const json2 = await res2.json();
      assert.equal(json2.success, true);
      assert.deepEqual(json2.results, [], 'UPDATE with RETURNING id must NOT fabricate results from last_insert_rowid()');
    } else {
      assert.equal(res2.status, 500);
      const json2 = await res2.json();
      assert.equal(json2.code, 'INCOMPLETE_RETURNING_STATE');
      assert.equal(json2.committed, true);
      assert.equal(json2.results, undefined, 'Must not fake results on error response');
    }
  }

  // Case 3B: Multi-column RETURNING id, code
  {
    const dbMulti = createMockDB({ failOnUpdate: true });
    env.DB = dbMulti;
    const idemKey = 'test-insert-multicol-returning';
    const body = {
      sql: 'INSERT INTO departments_department (code, name) VALUES (?, ?) RETURNING id, code',
      params: ['D103', 'Multi Col Dept'],
    };

    const req1 = makeRequest('/api/v1/query', body, { 'Idempotency-Key': idemKey });
    const res1 = await worker.fetch(req1, env);
    assert.equal(res1.status, 500);

    dbMulti.failOnUpdate = false;
    const req2 = makeRequest('/api/v1/query', body, { 'Idempotency-Key': idemKey });
    const res2 = await worker.fetch(req2, env);

    if (res2.status === 200) {
      const json2 = await res2.json();
      assert.equal(json2.success, true);
      assert.deepEqual(json2.results, [], 'Multi-column RETURNING must NOT fabricate partial single-column results');
    } else {
      assert.equal(res2.status, 500);
      const json2 = await res2.json();
      assert.equal(json2.code, 'INCOMPLETE_RETURNING_STATE');
      assert.equal(json2.committed, true);
      assert.equal(json2.results, undefined, 'Must not fake results on error response');
    }
  }

  // Case 3C: DELETE with RETURNING id
  {
    const dbDelete = createMockDB({ failOnUpdate: true });
    env.DB = dbDelete;
    const idemKey = 'test-delete-returning-scope';
    const body = {
      sql: 'DELETE FROM departments_department WHERE id = ? RETURNING id',
      params: [101],
    };

    const req1 = makeRequest('/api/v1/query', body, { 'Idempotency-Key': idemKey });
    const res1 = await worker.fetch(req1, env);
    assert.equal(res1.status, 500);

    dbDelete.failOnUpdate = false;
    const req2 = makeRequest('/api/v1/query', body, { 'Idempotency-Key': idemKey });
    const res2 = await worker.fetch(req2, env);

    if (res2.status === 200) {
      const json2 = await res2.json();
      assert.equal(json2.success, true);
      assert.deepEqual(json2.results, [], 'DELETE with RETURNING id must NOT fabricate results from last_insert_rowid()');
    } else {
      assert.equal(res2.status, 500);
      const json2 = await res2.json();
      assert.equal(json2.code, 'INCOMPLETE_RETURNING_STATE');
      assert.equal(json2.committed, true);
      assert.equal(json2.results, undefined, 'Must not fake results on error response');
    }
  }
});

test('Test 4: SELECT with duplicate column names via raw({ columnNames: true }) - preserves column order and duplicate names without key overwriting', async () => {
  const db = createMockDB();
  const env = { BRIDGE_SECRET_TOKEN: VALID_TOKEN, DB: db };
  const body = {
    sql: 'SELECT a.id, a.name, c.id, c.name FROM assets a JOIN categories c',
    params: [],
  };

  const req = makeRequest('/api/v1/query', body);
  const res = await worker.fetch(req, env);

  assert.equal(res.status, 200, 'Query endpoint must return 200');
  const json = await res.json();
  assert.equal(json.success, true);

  // Columns array must preserve duplicate names in exact order
  assert.deepEqual(
    json.columns,
    ['id', 'name', 'id', 'name'],
    'Column names array must preserve duplicates in exact order'
  );

  // Rows array must preserve values at exact column indices
  assert.equal(json.rows.length, 1);
  assert.deepEqual(
    json.rows[0],
    [101, 'Asset Laptop', 201, 'Category Hardware'],
    'Row values must match exact column positions'
  );

  // Legacy results array still present for backward compatibility
  assert.ok(Array.isArray(json.results));
  assert.equal(json.results.length, 1);

  // Meta must be present
  assert.ok(json.meta);
  assert.equal(json.meta.changes, 0);
});

test('Test 5: SELECT with raw({ columnNames: true }) returning 0 rows - returns columns header and empty rows array', async () => {
  const db = createMockDB();
  const originalPrepare = db.prepare.bind(db);
  db.prepare = (sql) => {
    const stmt = originalPrepare(sql);
    stmt.raw = async (options) => {
      if (options && options.columnNames) {
        return [['id', 'name']]; // only column header, 0 data rows
      }
      return [];
    };
    return stmt;
  };
  const env = { BRIDGE_SECRET_TOKEN: VALID_TOKEN, DB: db };
  const body = {
    sql: 'SELECT id, name FROM departments_department WHERE 1 = 0',
  };

  const req = makeRequest('/api/v1/query', body);
  const res = await worker.fetch(req, env);

  assert.equal(res.status, 200);
  const json = await res.json();
  assert.equal(json.success, true);
  assert.deepEqual(json.columns, ['id', 'name']);
  assert.deepEqual(json.rows, []);
  assert.deepEqual(json.results, []);
});
