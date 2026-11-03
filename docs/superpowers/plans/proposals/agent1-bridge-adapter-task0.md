# Task 0 & Task 1 Implementation Proposal: D1 Bridge Protocol & Backend Adapter

> **Author:** Agent 1 — Bridge, Adapter & Task 0 Planning Specialist  
> **Target Branch:** `feat/cloudflare-d1`  
> **Dependencies:** Pre-requisite gate for Task 2 (Production Bridge) and Task 4 (Business Services Batch)  
> **Status:** Proposal Draft for Codex & Orchestrator Review  

---

## 1. Executive Summary & Gating Architecture

The Cloudflare D1 integration operates across a critical boundary: an asynchronous, containerized Django 5.2/DRF application communicating over HTTPS with a Cloudflare Worker Bridge running on V8 isolates, backed by Cloudflare D1 (SQLite at the Edge).

```
┌─────────────────────────────────┐
│     Django 5.2 Origin Server    │
│  (Docker Linux Container: DRF)  │
└────────────────┬────────────────┘
                 │
                 │ 1. SQL Query / Atomic Batch (X-Bridge-Token + Idempotency-Key)
                 │ 2. DB-API 2.0 Cursor & D1Client with URL Guard
                 ▼
┌─────────────────────────────────┐
│   Cloudflare D1 Worker Bridge   │
│  (Edge V8 Gateway: index.js)    │
│  - Constant-time Token Auth     │
│  - Commit-Time Idempotency Log  │
│  - No Assumed/Fake Batch Fallback
└────────────────┬────────────────┘
                 │
                 │ Native SQLite Bindings (env.DB.batch / prepare)
                 ▼
┌─────────────────────────────────┐
│      Cloudflare D1 Database     │
│      (Primary SQLite Storage)   │
└─────────────────────────────────┘
```

### The Task 0 Gating Rule
**Task 0 is a hard blocking gate.** No work on Task 1 (Database Adapter Productionization), Task 2 (Production Worker Bridge deployment), or Task 4 (Business Services Batch Adaptation) may be merged or declared complete until all Task 0 blocker resolutions pass 100% of their regression tests.

### Critical Defect Analysis from Spike Implementation
1. **Blocker 1 (Regex Identifier Blindness):** In `spikes/cf-d1-spike/bridge-worker/src/index.js`, the regex `/returning\s+(?:(?:\w+\.)?id)\b/i` strictly expects unquoted word characters. When Django ORM generates `RETURNING "id"` or `RETURNING "departments_department"."id"`, the check returns `false`, causing the commit-time idempotency statement to write `json_array()`. If the worker terminates before secondary update, the client replay receives an empty array instead of the inserted record ID.
2. **Blocker 2 (Assumed/Fake Batch Results):** In `spikes/cf-d1-spike/bridge-worker/src/index.js`, batch initialization hardcodes `{ results: [], meta: { changes: 1, last_row_id: null } }` for every statement in `initialBatchResults`. This produces fake execution counts (`changes: 1`) on replay after an interrupted execution. If a transaction is committed but statement-level results are unrecoverable, the protocol must return an explicit `HTTP 500 Incomplete State` (`INCOMPLETE_BATCH_STATE`), never fabricated data.

---

## 2. Task 0: Implementation Blockers Resolution Gate

### 2.1 Blocker 1: Quoted Identifiers in `RETURNING` Clauses

#### Problem Statement
Django's SQL compiler generates quoted table and column identifiers for SQLite when constructing insert statements:
- Standard single-column insert:
  ```sql
  INSERT INTO "departments_department" ("code", "name", "description") VALUES (?, ?, ?) RETURNING "departments_department"."id"
  ```
- Unqualified quoted insert:
  ```sql
  INSERT INTO "departments_department" ("code", "name") VALUES (?, ?) RETURNING "id"
  ```
- Standard unquoted or legacy variations:
  ```sql
  INSERT INTO departments_department (code, name) VALUES (?, ?) RETURNING departments_department.id
  INSERT INTO departments_department (code, name) VALUES (?, ?) RETURNING id
  ```
- Whitespace and trailing characters:
  ```sql
  INSERT INTO "departments_department" ("code") VALUES (?) RETURNING   "id"  ;
  ```

The existing regex `const hasReturningId = /returning\s+(?:(?:\w+\.)?id)\b/i.test(sql);` fails on `"` because `\w` only matches `[a-zA-Z0-9_]`.

#### Solution Specification
We implement a two-stage SQL analysis pipeline in `bridge-worker/src/index.js`:
1. **Comment & Whitespace Normalization:** Strip block comments (`/* ... */`) and line comments (`-- ...`), then trim trailing semicolons and whitespace.
2. **Deterministic Returning Analysis (`analyzeReturningClause`):** Extract the returning clause expression and classify whether it is an atomic single primary key `id` versus multi-column or non-id projection.

```javascript
/**
 * Strips comments and trailing semicolon from SQL.
 */
function cleanSql(sql) {
  if (!sql || typeof sql !== 'string') return '';
  return sql
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/--.*$/gm, ' ')
    .trim()
    .replace(/;+\s*$/, '');
}

/**
 * Analyzes whether an SQL statement has a RETURNING clause and classifies it.
 *
 * Supported single ID forms:
 * - RETURNING "id"
 * - RETURNING id
 * - RETURNING "table"."id"
 * - RETURNING table.id
 * - RETURNING "table".id
 * - RETURNING table."id"
 */
function analyzeReturningClause(sql) {
  const cleaned = cleanSql(sql);
  const match = cleaned.match(/\breturning\s+([^,]+?)$/i);
  if (!match) {
    // Check if there is a multi-column returning clause (contains comma)
    const multiMatch = cleaned.match(/\breturning\s+(.+)$/i);
    if (multiMatch) {
      return { hasReturning: true, isSingleId: false, rawClause: multiMatch[1].trim() };
    }
    return { hasReturning: false, isSingleId: false, rawClause: null };
  }

  const rawClause = match[1].trim();

  // Pattern strictly matches: optional ([quoted_or_unquoted_table] .) followed by [quoted_or_unquoted_id]
  const singleIdPattern = /^(?:(?:"?[a-zA-Z0-9_]+"?)?\.)?"?id"?$/i;
  const isSingleId = singleIdPattern.test(rawClause);

  return {
    hasReturning: true,
    isSingleId: isSingleId,
    rawClause: rawClause,
  };
}
```

#### Commit-Time Idempotency Bundling
In `/api/v1/query`:
- When `isSingleId === true`: The atomic idempotency statement inserted alongside the query captures `last_insert_rowid()` directly inside SQLite:
  ```sql
  INSERT INTO _idempotency_log (idempotency_key, payload_hash, response_body)
  VALUES (?, ?, json_object(
    'success', json('true'),
    'results', json_array(json_object('id', last_insert_rowid())),
    'meta', json_object('changes', changes(), 'last_row_id', last_insert_rowid(), 'duration', 0)
  ))
  ```
- When `hasReturning === true && isSingleId === false`: SQLite cannot dynamically bundle arbitrary multi-column projections across statements in a batch call. The initial log entry must store `status: 'INCOMPLETE_RETURNING_STATE'`. If execution crashes before the secondary `UPDATE`, replay returns `HTTP 500` with `code: "INCOMPLETE_RETURNING_STATE"`, preventing data omission.
- When `hasReturning === false`: The initial log entry stores:
  ```sql
  'results', json_array(),
  'meta', json_object('changes', changes(), 'last_row_id', last_insert_rowid(), 'duration', 0)
  ```

---

### 2.2 Blocker 2: Elimination of Assumed/Fake Batch Fallback Results

#### Problem Statement
In `spikes/cf-d1-spike/bridge-worker/src/index.js`, the `/api/v1/batch` endpoint initialized `_idempotency_log` with:
```javascript
const initialBatchResults = statements.map(st => ({
  results: [],
  meta: { changes: 1, last_row_id: null },
}));
```
If the Worker died immediately after `env.DB.batch(prepared)` and before `UPDATE _idempotency_log`, subsequent replay requests received an array claiming every statement modified exactly 1 row (`changes: 1`), even if statements modified 0 rows, 100 rows, or performed `SELECT` queries.

#### Solution Specification
1. **Initial Log Entry Schema:** In the atomic `env.DB.batch()` call, the `_idempotency_log` statement writes an uncompleted transaction envelope:
   ```javascript
   const initialPayload = JSON.stringify({
     status: 'COMMITTED_PENDING_RESOLUTION',
     batch_size: statements.length,
     committed_at: new Date().toISOString(),
   });

   const logStmt = env.DB.prepare(
     `INSERT INTO _idempotency_log (idempotency_key, payload_hash, response_body)
      VALUES (?, ?, ?)`
   ).bind(idempotencyKey, currentPayloadHash, initialPayload);
   prepared.push(logStmt);
   ```

2. **Secondary Update with Real Data:** After `const rawBatchResults = await env.DB.batch(prepared);` succeeds, the Worker strips the log statement result (`userBatchResults = rawBatchResults.slice(0, -1)`) and constructs the true response:
   ```javascript
   responsePayload = JSON.stringify({
     success: true,
     results: userBatchResults.map(r => ({
       results: r.results || [],
       meta: {
         changes: r.meta?.changes ?? r.meta?.rows_written ?? 0,
         last_row_id: r.meta?.last_row_id ?? null,
       },
     })),
   });

   await env.DB.prepare(
     'UPDATE _idempotency_log SET response_body = ? WHERE idempotency_key = ?'
   ).bind(responsePayload, idempotencyKey).run();
   ```

3. **Replay Validation & Protocol Error (No Fake Fallback):**
   When an idempotent replay arrives (`SELECT payload_hash, response_body FROM _idempotency_log`):
   - If `cachedObj.status === 'COMMITTED_PENDING_RESOLUTION'`:
     The Worker detects that the transaction was committed in SQLite, but the process terminated before individual statement outcomes could be serialized.
     **Action:** Return `HTTP 500 Internal Server Error` with protocol payload:
     ```json
     {
       "error": "Protocol Error: Idempotent batch transaction committed in database but in incomplete state (per-statement execution results unavailable after crash)",
       "code": "INCOMPLETE_BATCH_STATE",
       "committed": true,
       "batch_size": 2
     }
     ```
   - The Worker **never** returns `HTTP 200` with simulated `meta.changes: 1`.
   - The client **never** retries the batch against D1, because `committed: true` proves the statements already executed.

4. **Concurrent Race Condition Resolution:**
   If a concurrent worker hits `UNIQUE constraint failed: _idempotency_log.idempotency_key`, it means another worker thread is actively executing or updating this key.
   The losing worker executes a bounded poll (up to 5 attempts with 50ms exponential backoff):
   ```javascript
   let winnerObj = null;
   for (let attempt = 0; attempt < 5; attempt++) {
     await new Promise(r => setTimeout(r, 40 * (attempt + 1)));
     const winnerLog = await env.DB.prepare(
       'SELECT payload_hash, response_body FROM _idempotency_log WHERE idempotency_key = ?'
     ).bind(idempotencyKey).first();

     if (!winnerLog) continue;
     if (winnerLog.payload_hash !== currentPayloadHash) {
       return new Response(JSON.stringify({
         error: 'Conflict: Idempotency-Key already used with different payload',
         code: 'PAYLOAD_MISMATCH'
       }), { status: 422, headers: { 'Content-Type': 'application/json' } });
     }

     try {
       winnerObj = JSON.parse(winnerLog.response_body);
       if (winnerObj && winnerObj.success === true && Array.isArray(winnerObj.results)) {
         return new Response(JSON.stringify(winnerObj), {
           status: 200,
           headers: { 'Content-Type': 'application/json', 'X-Idempotent-Replay': 'true' }
         });
       }
     } catch {}
   }
   ```
   If after 5 attempts `winnerObj` is still in `COMMITTED_PENDING_RESOLUTION` (indicating the winner isolate crashed), return `HTTP 500` with `code: "INCOMPLETE_BATCH_STATE"` and `committed: true`.

---

### 2.3 Exact SQL & Replay Scope Matrix

| SQL Pattern | Commit-Time Idempotency Action | Secondary Update Required | Replay Guarantee After Crash Before Update |
| :--- | :--- | :--- | :--- |
| `INSERT ... RETURNING "id"` | Bundled `last_insert_rowid()` into JSON | Yes (for duration/extra meta) | **Guaranteed:** Replay returns `HTTP 200`, `results: [{"id": <pk>}]`, `changes: 1`. |
| `INSERT ... RETURNING "table"."id"` | Bundled `last_insert_rowid()` into JSON | Yes | **Guaranteed:** Replay returns `HTTP 200`, `results: [{"id": <pk>}]`, `changes: 1`. |
| `INSERT ... VALUES ...` (no RETURNING) | Bundled `changes()` & `last_insert_rowid()` | Yes | **Guaranteed:** Replay returns `HTTP 200`, `results: []`, `changes: 1`. |
| `UPDATE ...` / `DELETE ...` | Bundled `changes()` into JSON | Yes | **Guaranteed:** Replay returns `HTTP 200`, `results: []`, `changes: N`. |
| `INSERT ... RETURNING "id", "created_at"` (multi-col) | Writes `INCOMPLETE_RETURNING_STATE` | Yes (stores full row dict) | **Explicit Protocol Error:** Replay returns `HTTP 500`, `code: "INCOMPLETE_RETURNING_STATE"`, `committed: true`. |
| `BATCH [stmt1, stmt2, ...]` | Writes `COMMITTED_PENDING_RESOLUTION` | Yes (stores all per-statement results) | **Explicit Protocol Error:** Replay returns `HTTP 500`, `code: "INCOMPLETE_BATCH_STATE"`, `committed: true`. No fake data. |
| Payload mismatch on existing key | None (pre-execution check) | No | **Rejected:** `HTTP 422 Unprocessable Entity`, `code: "PAYLOAD_MISMATCH"`. |
| SQL length > 100 KB or Batch > 100 items | None (pre-execution check) | No | **Rejected:** `HTTP 400 Bad Request`. |
| Missing/Invalid `X-Bridge-Token` | None (pre-execution check) | No | **Rejected:** `HTTP 401 Unauthorized`. |

---

### 2.4 Regression Test Specifications

#### 1. Worker Test Suite (`node:test` in `bridge-worker/tests/test_worker_regression.mjs`)
The test suite runs against the Worker isolate boundary with a mock D1 engine capable of injecting crash-after-commit conditions:

```javascript
// Test 1: Quoted identifier RETURNING "id" preserves ID across crash
test('Task 0 Blocker 1a: RETURNING "id" (quoted) preserves ID on replay after crash', async () => {
  const db = createMockDB({ failOnUpdate: true });
  const env = { BRIDGE_SECRET_TOKEN: VALID_TOKEN, DB: db };
  const idemKey = 'test-returning-quoted-id';
  const body = {
    sql: 'INSERT INTO "departments_department" ("code", "name") VALUES (?, ?) RETURNING "id"',
    params: ['D-Q1', 'Dept Quoted 1'],
  };

  const req1 = makeRequest('/api/v1/query', body, { 'Idempotency-Key': idemKey });
  const res1 = await worker.fetch(req1, env);
  assert.equal(res1.status, 500, 'Must fail on secondary update failure');

  db.failOnUpdate = false;
  const req2 = makeRequest('/api/v1/query', body, { 'Idempotency-Key': idemKey });
  const res2 = await worker.fetch(req2, env);

  assert.equal(res2.status, 200, 'Replay must succeed with commit-time data');
  const json2 = await res2.json();
  assert.equal(json2.success, true);
  assert.equal(json2.results.length, 1);
  assert.equal(json2.results[0].id, 101, 'Quoted "id" must be correctly captured at commit-time');
});

// Test 2: Quoted table-prefixed identifier RETURNING "departments_department"."id"
test('Task 0 Blocker 1b: RETURNING "table"."id" preserves ID on replay after crash', async () => {
  const db = createMockDB({ failOnUpdate: true });
  const env = { BRIDGE_SECRET_TOKEN: VALID_TOKEN, DB: db };
  const idemKey = 'test-returning-table-quoted-id';
  const body = {
    sql: 'INSERT INTO "departments_department" ("code", "name") VALUES (?, ?) RETURNING "departments_department"."id"',
    params: ['D-Q2', 'Dept Quoted 2'],
  };

  const req1 = makeRequest('/api/v1/query', body, { 'Idempotency-Key': idemKey });
  const res1 = await worker.fetch(req1, env);
  assert.equal(res1.status, 500);

  db.failOnUpdate = false;
  const req2 = makeRequest('/api/v1/query', body, { 'Idempotency-Key': idemKey });
  const res2 = await worker.fetch(req2, env);

  assert.equal(res2.status, 200);
  const json2 = await res2.json();
  assert.equal(json2.results[0].id, 101, 'Table-qualified quoted identifier must resolve to ID');
});

// Test 3: Batch crash after commit returns HTTP 500 Incomplete State (No Fake Data)
test('Task 0 Blocker 2: Batch crash before update returns HTTP 500 INCOMPLETE_BATCH_STATE (No fake changes: 1)', async () => {
  const db = createMockDB({ failOnUpdate: true });
  const env = { BRIDGE_SECRET_TOKEN: VALID_TOKEN, DB: db };
  const idemKey = 'test-batch-crash-no-fake';
  const body = {
    statements: [
      { sql: 'UPDATE test_assets SET status = ? WHERE id = ?', params: ['ALLOCATED', 1] },
      { sql: 'INSERT INTO test_audit_logs (asset_id, action) VALUES (?, ?)', params: [1, 'ALLOCATE'] },
    ],
  };

  const req1 = makeRequest('/api/v1/batch', body, { 'Idempotency-Key': idemKey });
  const res1 = await worker.fetch(req1, env);
  assert.equal(res1.status, 500);

  db.failOnUpdate = false;
  const req2 = makeRequest('/api/v1/batch', body, { 'Idempotency-Key': idemKey });
  const res2 = await worker.fetch(req2, env);

  // MUST return 500 with explicit protocol error, NEVER 200 with fake results!
  assert.equal(res2.status, 500);
  const errJson = await res2.json();
  assert.equal(errJson.code, 'INCOMPLETE_BATCH_STATE');
  assert.equal(errJson.committed, true);
  assert.ok(errJson.error.includes('incomplete state'));
});
```

#### 2. Pytest Integration Suite (`backend/assethub_api/backends/d1/tests/test_bridge_atomicity.py`)
```python
def test_django_orm_generated_sql_returning_id_replay(bridge_credentials):
    """Verifies real Django ORM model creation using quoted identifiers preserves PK on replay."""
    url, token = bridge_credentials
    client = D1Client(base_url=url, token=token)
    
    code = f"T-QPK-{uuid.uuid4().hex[:6].upper()}"
    idem_key = f"orm-test-pk-{uuid.uuid4()}"
    
    # Real SQL syntax compiled by Django for SQLite INSERT
    sql = 'INSERT INTO "departments_department" ("code", "name", "description") VALUES (?, ?, ?) RETURNING "departments_department"."id"'
    params = [code, "Quoted PK Test", "Testing Django quoted identifier compilation"]
    
    # 1. Execute initial mutation
    res1 = client.query(sql, params, idempotency_key=idem_key)
    assert res1.get("success") is True
    assert len(res1.get("results", [])) == 1
    created_id = res1["results"][0]["id"]
    assert isinstance(created_id, int)
    
    # 2. Replay execution with same key -> Must match created_id exactly
    res2 = client.query(sql, params, idempotency_key=idem_key)
    assert res2.get("success") is True
    assert res2["results"][0]["id"] == created_id
    
    # Cleanup
    client.query('DELETE FROM "departments_department" WHERE "id" = ?', [created_id])


def test_batch_fault_injection_reports_incomplete_state_without_retry(bridge_credentials):
    """Verifies that batch crash before UPDATE returns INCOMPLETE_BATCH_STATE and client raises OperationalError."""
    url, token = bridge_credentials
    client = D1Client(base_url=url, token=token)
    
    code = f"T-BF-{uuid.uuid4().hex[:6].upper()}"
    idem_key = f"batch-fault-{uuid.uuid4()}"
    
    statements = [
        {"sql": 'INSERT INTO "departments_department" ("code", "name") VALUES (?, ?)', "params": [code, "Batch Fault"]},
        {"sql": 'SELECT COUNT(*) as cnt FROM "departments_department" WHERE "code" = ?', "params": [code]},
    ]
    
    # Step 1: Inject crash after commit
    with pytest.raises(OperationalError) as exc_info:
        client.batch(
            statements,
            idempotency_key=idem_key,
            custom_headers={"X-Test-Fault-Injection": "crash-after-commit"},
            retries=1,
        )
    assert "500" in str(exc_info.value)
    
    # Step 2: Retry with same key -> Must raise OperationalError indicating committed incomplete state, NOT fake HTTP 200
    with pytest.raises(OperationalError) as exc_info2:
        client.batch(statements, idempotency_key=idem_key, retries=1)
    
    assert "INCOMPLETE_BATCH_STATE" in str(exc_info2.value) or "incomplete state" in str(exc_info2.value)
    
    # Step 3: Verify row was committed once (no duplicate, no rollback)
    check = client.query('SELECT COUNT(*) as cnt FROM "departments_department" WHERE "code" = ?', [code])
    assert check["results"][0]["cnt"] == 1
    
    # Cleanup
    client.query('DELETE FROM "departments_department" WHERE "code" = ?', [code])
```

### 2.5 Task 0 Gating Acceptance Criteria
To unblock Task 1 and Task 4, the following criteria must pass 100%:
- [ ] `node --test spikes/cf-d1-spike/bridge-worker/tests/test_worker_regression.mjs` exits with code 0 (zero failures).
- [ ] Pytest suite `pytest backend/assethub_api/backends/d1/tests/test_bridge_atomicity.py -v` passes 100%.
- [ ] Zero fake data: No response generated by the bridge or adapter ever contains assumed `changes: 1` or mock results shells.
- [ ] Zero double-writes: Replay under crash conditions never runs the mutation query a second time.

---

## 3. Task 1: D1 Database Backend Adapter & Client

### 3.1 URL Guard Normalization & Pre-Transport Blocking

#### Interface & Signature (`backend/assethub_api/backends/d1/client.py`)
```python
ALLOWED_TEST_BRIDGE_URLS: tuple[str, ...] = (
    "https://assethub-test-worker.hnhoa494-assethub.workers.dev",
    "http://127.0.0.1:8787",
    "http://localhost:8787",
)

def parse_and_normalize_endpoint(url_str: str) -> tuple[str, str, int, str]:
    """
    Parses and canonicalizes bridge URL into (scheme, hostname, port, path).
    Enforces RFC 3986 compliance, forbids userinfo credentials, and canonicalizes default ports.
    
    Raises:
        django.db.OperationalError: If URL is invalid, contains credentials, or uses an unsupported scheme.
    """
```

#### Pre-Transport Blocking Rule
Before opening any network socket or calling `urllib.request.urlopen`:
1. Check `is_mutation` and `is_test_environment()`.
2. Parse `self.base_url` using `parse_and_normalize_endpoint()`.
3. Check `parsed_endpoint in ALLOWED_TEST_BRIDGE_ENDPOINTS`.
4. If unauthorized, immediately raise `django.db.OperationalError` with the offending URL in the message. The HTTP request object is never instantiated and no network packet is dispatched.

---

### 3.2 DB-API 2.0 Cursor & Connection Implementation

#### File: `backend/assethub_api/backends/d1/base.py`

#### Interface Specification (`D1Cursor`)
```python
class D1Cursor:
    """
    DB-API 2.0 compliant cursor dispatching queries to Cloudflare D1 via D1Client.
    Maintains row navigation pointer and standard metadata tuples.
    """
    def __init__(self, connection: "D1Connection"):
        self.connection = connection
        self._rows: list[tuple[Any, ...]] = []
        self._columns: list[str] = []
        self._pos: int = 0
        self.rowcount: int = -1
        self.lastrowid: Optional[int] = None
        self.description: Optional[tuple[tuple[str, None, None, None, None, None, None], ...]] = None

    def execute(self, query: str, params: Optional[Sequence[Any]] = None) -> "D1Cursor":
        ...

    def executemany(self, query: str, seq_of_params: Sequence[Sequence[Any]]) -> "D1Cursor":
        ...

    def fetchone(self) -> Optional[tuple[Any, ...]]:
        ...

    def fetchall(self) -> list[tuple[Any, ...]]:
        ...

    def fetchmany(self, size: Optional[int] = None) -> list[tuple[Any, ...]]:
        ...

    def close(self) -> None:
        ...

    def __enter__(self) -> "D1Cursor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
```

#### Execution Logic & Mapping
1. **Placeholder Translation:** Converts Django standard `%s` placeholders to SQLite `?` positional parameters.
2. **Boolean & Type Normalization:**
   - Python `True` -> SQLite integer `1`.
   - Python `False` -> SQLite integer `0`.
   - Python `None` -> SQLite `NULL`.
   - Python `datetime` / `date` -> ISO-8601 UTC string format.
3. **`django_date_trunc` Rewrite:** Intercepts and rewrites Django UDF calls to standard SQLite date functions before sending to bridge.
4. **Metadata Extraction:**
   - If query produces rows: `self._columns = list(results[0].keys())`, `self._rows = [tuple(r[c] for c in self._columns) for r in results]`, `self.description = tuple((col, None, None, None, None, None, None) for col in self._columns)`.
   - If query modifies rows (`INSERT`/`UPDATE`/`DELETE`): `self.rowcount = meta.get("changes", 0)`, `self.lastrowid = meta.get("last_row_id", None)`.
   - If query is `SELECT`: `self.rowcount = len(self._rows)`.

---

### 3.3 `django_date_trunc` Replacement Engine

In standard SQLite on local disk, Django registers Python C-extension UDFs (`connection.create_function('django_date_trunc', 4, ...)`). Since Cloudflare D1 runs SQLite on V8 isolates without Python UDF support, queries with date truncations must be rewritten in the adapter before transmission:

```python
def replace_date_trunc_in_sql(sql: str) -> str:
    """
    Translates django_date_trunc and django_datetime_trunc UDFs into standard SQLite STRFTIME/DATE expressions.
    """
    if "django_date_trunc" not in sql and "django_datetime_trunc" not in sql:
        return sql

    pattern = r"django_(?:date|datetime)_trunc\(%s,\s*([^,]+),\s*%s,\s*%s\)"

    def replace_func(match):
        field = match.group(1).strip()
        return (
            f"CASE %s "
            f"WHEN 'year' THEN STRFTIME('%Y-01-01', {field}) "
            f"WHEN 'quarter' THEN CASE ((CAST(STRFTIME('%m', {field}) AS INTEGER) - 1) / 3 + 1) "
            f"  WHEN 1 THEN STRFTIME('%Y-01-01', {field}) "
            f"  WHEN 2 THEN STRFTIME('%Y-04-01', {field}) "
            f"  WHEN 3 THEN STRFTIME('%Y-07-01', {field}) "
            f"  WHEN 4 THEN STRFTIME('%Y-10-01', {field}) END "
            f"WHEN 'month' THEN STRFTIME('%Y-%m-01', {field}) "
            f"WHEN 'week' THEN DATE({field}, '-' || CAST((CAST(STRFTIME('%w', {field}) AS INTEGER) + 6) % 7 AS TEXT) || ' days') "
            f"WHEN 'day' THEN DATE({field}) "
            f"WHEN 'hour' THEN STRFTIME('%Y-%m-%d %H:00:00', {field}) "
            f"WHEN 'minute' THEN STRFTIME('%Y-%m-%d %H:%M:00', {field}) "
            f"WHEN 'second' THEN STRFTIME('%Y-%m-%d %H:%M:%S', {field}) "
            f"WHEN 'date' THEN DATE({field}) "
            f"WHEN 'time' THEN TIME({field}) "
            f"END"
        )

    return re.sub(pattern, replace_func, sql)
```

---

### 3.4 Foreign Key Pragma & Connection Lifecycle

1. **Foreign Keys Enabled by Default:** Cloudflare D1 has `PRAGMA foreign_keys = ON;` permanently enabled at the database engine level.
2. **Feature Flags (`D1DatabaseFeatures`):**
   ```python
   class D1DatabaseFeatures(SQLiteDatabaseFeatures):
       atomic_transactions = False
       supports_transactions = False
       uses_savepoints = False
       can_release_savepoints = False
       has_select_for_update = False
       supports_atomic_references_rename = False
       can_clone_databases = False
       can_rollback_ddl = False
       supports_pragma_foreign_key_check = False
       can_defer_constraint_checks = False
   ```
3. **Stateless Lifecycle (`D1Connection`):**
   `commit()` and `rollback()` are implemented as safe no-ops because HTTP communications are autocommitted on D1. Transactions requiring cross-statement atomicity use `d1_batch()`.

---

### 3.5 Conservative Read/Write Classification & Safe Retry Policy

#### Classification Logic (`is_read_only_query`)
```python
def is_read_only_query(sql: str) -> bool:
    """
    Conservative classification: strictly whitelists verified read-only statements.
    Comments are stripped prior to tokenization. Any unrecognized query or CTE is treated as a mutation.
    """
    if not sql or not isinstance(sql, str):
        return False

    cleaned = sql.strip()
    while cleaned.startswith("/*"):
        end_idx = cleaned.find("*/")
        if end_idx == -1: break
        cleaned = cleaned[end_idx + 2:].strip()

    while cleaned.startswith("--"):
        end_idx = cleaned.find("\n")
        if end_idx == -1: return False
        cleaned = cleaned[end_idx + 1:].strip()

    tokens = cleaned.split()
    if not tokens:
        return False

    first_word = tokens[0].upper()
    if first_word in ("SELECT", "EXPLAIN"):
        return True

    if first_word == "PRAGMA":
        upper_sql = cleaned.upper()
        read_only_pragmas = (
            "PRAGMA TABLE_INFO",
            "PRAGMA TABLE_XINFO",
            "PRAGMA INDEX_LIST",
            "PRAGMA INDEX_INFO",
            "PRAGMA FOREIGN_KEY_LIST",
            "PRAGMA DATABASE_LIST",
        )
        return any(upper_sql.startswith(p) for p in read_only_pragmas)

    return False
```

#### Safe Retry Matrix
- **Read-Only Queries (`is_mutation = False`):** Safe to retry up to 3 times on network failure (`urllib.error.URLError`, HTTP 502/503/504).
- **Mutations With `Idempotency-Key`:** Safe to retry up to 3 times on network transport failure; D1 Bridge guarantees idempotency via `_idempotency_log`.
- **Mutations Without `Idempotency-Key`:** Max attempts = 1 (`can_retry = False`). If a network timeout occurs, immediately raise `django.db.OperationalError("D1 Network Timeout during un-idempotent mutation! State is UNKNOWN")`. **Zero automatic retry.**
- **Non-Retryable Errors:** HTTP 400 (Bad Request), HTTP 401 (Unauthorized), HTTP 409 (Constraint Conflict), HTTP 422 (Payload Mismatch) are raised immediately without retry.

---

### 3.6 Public Helpers & Cross-Service Interface

In `backend/assethub_api/backends/d1/__init__.py`:
```python
from typing import Sequence, Any, Union
from django.db import connections

def d1_batch(statements: Sequence[Union[str, tuple[str, Sequence[Any]], dict]], using: str = 'default') -> list[dict]:
    """
    Executes an atomic batch of statements on Cloudflare D1.
    
    Args:
        statements: List of SQL strings, (sql, params) tuples, or {'sql': ..., 'params': ...} dicts.
        using: Django database alias (defaults to 'default').
        
    Returns:
        List of statement results: [{'results': [...], 'meta': {'changes': int, 'last_row_id': int}}]
        
    Raises:
        django.db.IntegrityError: If any statement triggers a foreign key, check, or unique constraint violation.
        django.db.OperationalError: On network, bridge, or isolation errors.
    """
    connection = connections[using]
    if hasattr(connection, 'client'):
        return connection.client.batch(statements)
    elif hasattr(connection, 'connection') and hasattr(connection.connection, 'client'):
        return connection.connection.client.batch(statements)
    else:
        raise django.db.OperationalError(f"Database backend '{using}' does not support d1_batch.")
```

---

## 4. File Boundaries, Interfaces & Verification Workflows

### 4.1 Consumes & Produces Architecture

| File Path | Status | Consumes (Inputs) | Produces (Outputs / Exports) |
| :--- | :--- | :--- | :--- |
| `spikes/cf-d1-spike/bridge-worker/src/index.js` | Modify (Task 0) | `env.DB`, `env.BRIDGE_SECRET_TOKEN`, HTTP Request | Secure HTTP Bridge with quoted `RETURNING` support & zero-fake batch handling |
| `spikes/cf-d1-spike/bridge-worker/tests/test_worker_regression.mjs` | Modify (Task 0) | Node test runner, Worker module | Regression test coverage for quoted RETURNING and batch crash errors |
| `backend/assethub_api/backends/d1/__init__.py` | Create (Task 1) | Django connections | Module export `d1_batch` |
| `backend/assethub_api/backends/d1/client.py` | Create/Refine (Task 1) | `D1_BRIDGE_URL`, `D1_BRIDGE_TOKEN` | `D1Client`, URL isolation guard, query classification |
| `backend/assethub_api/backends/d1/base.py` | Create/Refine (Task 1) | `D1Client`, Django SQLite base | `DatabaseWrapper`, `D1Cursor`, `D1Connection`, `d1_batch` |
| `backend/assethub_api/backends/d1/tests/test_isolation_guard.py` | Existing (Verified) | `D1Client`, Django connection | Pre-transport test URL guard verification |
| `backend/assethub_api/backends/d1/tests/test_bridge_atomicity.py` | Modify (Task 0 & 1) | D1 test database | Pytest regression suite covering atomicity, quoted RETURNING, no-fake batch |
| `backend/assethub_api/backends/d1/tests/test_d1_orm_integration.py` | Existing (Verified) | Real Django ORM models, SimpleJWT | Minimal integration gate verification on D1 |

---

### 4.2 Step-by-Step TDD Verification Commands

#### Phase 1: Task 0 Bridge Blocker Verification (Worker Level)
```bash
# 1. Run Node.js worker regression test suite
node --test spikes/cf-d1-spike/bridge-worker/tests/test_worker_regression.mjs

# Expected result:
# ✔ Task 0 Blocker 1a: RETURNING "id" (quoted) preserves ID on replay after crash
# ✔ Task 0 Blocker 1b: RETURNING "table"."id" preserves ID on replay after crash
# ✔ Task 0 Blocker 2: Batch crash before update returns HTTP 500 INCOMPLETE_BATCH_STATE (No fake changes: 1)
# ✔ Regression Defect 2: Race condition winnerObj must not throw ReferenceError
# PASS: 4 tests, 0 failures
```

#### Phase 2: Task 0 Integration Gate Verification (Backend Level)
```bash
# 2. Run backend bridge atomicity and idempotency tests in Docker container
docker compose exec backend pytest assethub_api/backends/d1/tests/test_bridge_atomicity.py -v

# Expected result:
# All tests PASSED including quoted RETURNING replay and no-fake batch fault injection
```

#### Phase 3: Task 1 Database Adapter Full Verification
```bash
# 3. Run all D1 backend tests (Isolation Guard + Atomicity + Full ORM Integration)
docker compose exec backend pytest assethub_api/backends/d1/tests/ -v

# Expected result:
# 22+ tests passed, 0 failures, 100% pass rate
```

---

## 5. Review & Acceptance Sign-off Matrix

| Review Criterion | Responsible Task | Verification Command | Sign-Off Condition |
| :--- | :--- | :--- | :--- |
| **Quoted Identifiers Support** | Task 0 | `node --test spikes/.../test_worker_regression.mjs` | Matches `RETURNING "id"`, `RETURNING "table"."id"` without losing PK |
| **Zero Assumed/Fake Batch Data** | Task 0 | `pytest .../test_bridge_atomicity.py` | Crash before UPDATE returns `HTTP 500 INCOMPLETE_BATCH_STATE`, never `changes: 1` |
| **URL Guard Pre-Transport Blocking** | Task 1 | `pytest .../test_isolation_guard.py` | Blocks unauthorized mutation URLs before `urllib.request.urlopen` |
| **DB-API 2.0 Compliance** | Task 1 | `pytest .../test_d1_orm_integration.py` | `description`, `rowcount`, `lastrowid`, `fetchall` work with Django ORM |
| **Conservative Classification** | Task 1 | `pytest .../test_bridge_atomicity.py` | Unrecognized queries, CTEs, mutations never auto-retried |
| **100% Gating Pass** | Gate | Docker pytest + Node test | Must pass 100% before starting Task 2 or Task 4 |

---
*End of Implementation Proposal for Task 0 & Task 1.*
