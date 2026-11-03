/**
 * Secure D1 Bridge Worker for AssetHub Backend
 * Production-hardened with:
 * - Constant-time cryptographic token authentication (No fallback default)
 * - Atomic Idempotency Key validation against double-mutation on retry
 * - Semantic HTTP status mapping (409 Conflict for SQLite constraints)
 * - Payload size and statement count boundaries
 */

async function timingSafeEqual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') return false;
  const encoder = new TextEncoder();
  const aBuf = encoder.encode(a);
  const bBuf = encoder.encode(b);
  if (aBuf.byteLength !== bBuf.byteLength) return false;

  if (typeof crypto !== 'undefined') {
    if (crypto.subtle && typeof crypto.subtle.timingSafeEqual === 'function') {
      return crypto.subtle.timingSafeEqual(aBuf, bBuf);
    }
    if (typeof crypto.timingSafeEqual === 'function') {
      return crypto.timingSafeEqual(aBuf, bBuf);
    }
  }

  let diff = 0;
  for (let i = 0; i < aBuf.byteLength; i++) {
    diff |= aBuf[i] ^ bBuf[i];
  }
  return diff === 0;
}

async function sha256(text) {
  const encoder = new TextEncoder();
  const data = encoder.encode(text);
  const hashBuf = await crypto.subtle.digest('SHA-256', data);
  const hashArr = Array.from(new Uint8Array(hashBuf));
  return hashArr.map(b => b.toString(16).padStart(2, '0')).join('');
}

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
 * Analyzes whether an SQL statement has a single-column ID RETURNING clause on INSERT.
 * Supported single ID forms:
 * - RETURNING "id"
 * - RETURNING id
 * - RETURNING "table"."id"
 * - RETURNING table.id
 * - RETURNING "table".id
 * - RETURNING table."id"
 *
 * For unhandled / multi-column / non-ID RETURNING (or UPDATE/DELETE RETURNING),
 * do NOT fake recovery (canRecover = false).
 */
function analyzeReturningClause(sql) {
  const cleaned = cleanSql(sql);
  const isInsert = /^\s*insert\b/i.test(cleaned);

  const match = cleaned.match(/\breturning\s+([^,]+?)$/i);
  if (!match) {
    const multiMatch = cleaned.match(/\breturning\s+(.+)$/i);
    if (multiMatch) {
      return {
        hasReturning: true,
        isSingleId: false,
        canRecover: false,
        rawClause: multiMatch[1].trim(),
      };
    }
    return {
      hasReturning: false,
      isSingleId: false,
      canRecover: false,
      rawClause: null,
    };
  }

  const rawClause = match[1].trim();
  const singleIdPattern = /^(?:(?:"?[a-zA-Z0-9_]+"?)?\s*\.\s*)?"?id"?$/i;
  const isSingleId = singleIdPattern.test(rawClause);
  const canRecover = isInsert && isSingleId;

  return {
    hasReturning: true,
    isSingleId,
    canRecover,
    rawClause,
  };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    // Public health check (does not access DB or reveal internal state)
    if (path === '/health') {
      return new Response(JSON.stringify({ status: 'ok', service: 'd1-bridge' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    // Enforce environment configuration: Reject if secret is missing or too short
    const expectedToken = env.BRIDGE_SECRET_TOKEN;
    if (!expectedToken || typeof expectedToken !== 'string' || expectedToken.trim().length < 32) {
      return new Response(JSON.stringify({ error: 'Server Configuration Error: Bridge secret token unconfigured' }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    // Authenticate inter-service request with constant-time equality
    const bridgeToken = request.headers.get('X-Bridge-Token') || '';
    const isValid = await timingSafeEqual(bridgeToken.trim(), expectedToken.trim());
    if (!isValid) {
      return new Response(JSON.stringify({ error: 'Unauthorized: Invalid bridge token' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    if (request.method !== 'POST') {
      return new Response(JSON.stringify({ error: 'Method Not Allowed' }), {
        status: 405,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    // Payload size boundaries
    const contentLength = request.headers.get('content-length');
    if (contentLength && parseInt(contentLength, 10) > 1024 * 1024) {
      return new Response(JSON.stringify({ error: 'Payload Too Large (> 1 MB)' }), {
        status: 413,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    let rawBody;
    try {
      rawBody = await request.text();
    } catch {
      return new Response(JSON.stringify({ error: 'Failed to read request body' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    let body;
    try {
      body = JSON.parse(rawBody);
    } catch {
      return new Response(JSON.stringify({ error: 'Invalid JSON body' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    // Idempotency check for mutations
    const idempotencyKey = request.headers.get('Idempotency-Key');
    let currentPayloadHash = null;
    if (idempotencyKey) {
      currentPayloadHash = await sha256(rawBody);
      // Strictly query existing idempotency record; do NOT catch or swallow DB errors
      const existing = await env.DB.prepare(
        'SELECT payload_hash, response_body FROM _idempotency_log WHERE idempotency_key = ?'
      ).bind(idempotencyKey).first();

      if (existing) {
        if (existing.payload_hash !== currentPayloadHash) {
          return new Response(JSON.stringify({
            error: 'Conflict: Idempotency-Key already used with different payload',
          }), {
            status: 422,
            headers: { 'Content-Type': 'application/json' },
          });
        }

        // Validate cached response body: separate validation for query vs batch!
        let cachedObj = null;
        try {
          cachedObj = JSON.parse(existing.response_body);
        } catch {
          // If response_body was invalid JSON, reject with 500
        }

        if (cachedObj && cachedObj.status === 'COMMITTED_PENDING_RESOLUTION') {
          return new Response(JSON.stringify({
            error: "Incomplete batch state after commit",
            code: "INCOMPLETE_BATCH_STATE",
            committed: true,
          }), {
            status: 500,
            headers: { "Content-Type": "application/json" },
          });
        }

        if (cachedObj && cachedObj.status === 'INCOMPLETE_RETURNING_STATE') {
          return new Response(JSON.stringify({
            error: "Incomplete returning state after commit",
            code: "INCOMPLETE_RETURNING_STATE",
            committed: true,
          }), {
            status: 500,
            headers: { "Content-Type": "application/json" },
          });
        }

        let isValidReplay = false;
        if (cachedObj && typeof cachedObj === 'object' && cachedObj.success === true && Array.isArray(cachedObj.results) && cachedObj.status !== 'PENDING') {
          if (path === '/api/v1/query') {
            // Query protocol: meta object with changes count
            isValidReplay = Boolean(cachedObj.meta && typeof cachedObj.meta === 'object');
          } else if (path === '/api/v1/batch') {
            // Batch protocol: each item in results must have results array and meta object
            isValidReplay = cachedObj.results.length > 0 && cachedObj.results.every(
              r => r && typeof r === 'object' && Array.isArray(r.results) && r.meta && typeof r.meta === 'object'
            );
          }
        }

        if (!isValidReplay) {
          return new Response(JSON.stringify({
            error: 'Server Error: Idempotent transaction in incomplete state (pending resolution)',
          }), {
            status: 500,
            headers: { 'Content-Type': 'application/json' },
          });
        }

        return new Response(JSON.stringify(cachedObj), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'X-Idempotent-Replay': 'true',
          },
        });
      }
    }

    try {
      if (path === '/api/v1/query') {
        const { sql, params = [] } = body;

        if (!sql || typeof sql !== 'string' || sql.length > 100000) {
          return new Response(JSON.stringify({ error: 'Missing, invalid or oversized SQL query' }), {
            status: 400,
            headers: { 'Content-Type': 'application/json' },
          });
        }

        let responsePayload;

        if (idempotencyKey && currentPayloadHash) {
          // Atomic execution: query + idempotency record bundled in a single db.batch()
          // Statement 2 builds complete response JSON atomically using changes() and last_insert_rowid()!
          // If query uses RETURNING id on INSERT, last_insert_rowid() captures the returning ID at commit time!
          const userStmt = params.length > 0 ? env.DB.prepare(sql).bind(...params) : env.DB.prepare(sql);
          const returningAnalysis = analyzeReturningClause(sql);

          let logStmt;
          if (returningAnalysis.hasReturning && !returningAnalysis.canRecover) {
            // Unhandled returning clause (multi-column, non-ID, or UPDATE/DELETE RETURNING):
            // Do NOT fake recovery
            logStmt = env.DB.prepare(
              `INSERT INTO _idempotency_log (idempotency_key, payload_hash, response_body)
               VALUES (?, ?, json_object(
                 'status', 'INCOMPLETE_RETURNING_STATE',
                 'committed', json('true')
               ))`
            ).bind(idempotencyKey, currentPayloadHash);
          } else {
            const initialResultsSql = returningAnalysis.canRecover
              ? "json_array(json_object('id', last_insert_rowid()))"
              : "json_array()";

            logStmt = env.DB.prepare(
              `INSERT INTO _idempotency_log (idempotency_key, payload_hash, response_body)
               VALUES (?, ?, json_object(
                 'success', json('true'),
                 'results', ${initialResultsSql},
                 'meta', json_object('changes', changes(), 'last_row_id', last_insert_rowid(), 'duration', 0)
               ))`
            ).bind(idempotencyKey, currentPayloadHash);
          }

          const [queryRes] = await env.DB.batch([userStmt, logStmt]);

          // Fault Injection point: Simulate failure/crash right after commit, BEFORE UPDATE response_body
          const faultInjection = request.headers.get('X-Test-Fault-Injection');
          if (faultInjection === 'crash-after-commit' || faultInjection === 'crash-before-update') {
            return new Response(JSON.stringify({
              error: 'Fault Injected: Simulated crash after commit',
            }), {
              status: 500,
              headers: { 'Content-Type': 'application/json' },
            });
          }

          responsePayload = JSON.stringify({
            success: true,
            results: queryRes.results || [],
            meta: {
              changes: queryRes.meta?.changes ?? queryRes.meta?.rows_written ?? 0,
              last_row_id: queryRes.meta?.last_row_id ?? null,
              duration: queryRes.meta?.duration ?? 0,
            },
          });

          // Always update idempotency log with full actual results (including RETURNING data) before returning
          await env.DB.prepare(
            'UPDATE _idempotency_log SET response_body = ? WHERE idempotency_key = ?'
          ).bind(responsePayload, idempotencyKey).run();

        } else {
          // Standard read/unkeyed execution
          const stmt = params.length > 0 ? env.DB.prepare(sql).bind(...params) : env.DB.prepare(sql);
          const result = await stmt.all();

          responsePayload = JSON.stringify({
            success: true,
            results: result.results || [],
            meta: {
              changes: result.meta?.changes ?? result.meta?.rows_written ?? 0,
              last_row_id: result.meta?.last_row_id ?? null,
              duration: result.meta?.duration ?? 0,
            },
          });
        }

        return new Response(responsePayload, {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });

      } else if (path === '/api/v1/batch') {
        const { statements } = body;

        if (!Array.isArray(statements) || statements.length === 0 || statements.length > 100) {
          return new Response(JSON.stringify({ error: 'Statements must be an array of 1 to 100 queries' }), {
            status: 400,
            headers: { 'Content-Type': 'application/json' },
          });
        }

        const prepared = [];
        for (const st of statements) {
          const sql = typeof st === 'string' ? st : st.sql;
          if (!sql || typeof sql !== 'string' || sql.length > 100000) {
            return new Response(JSON.stringify({ error: 'Statement exceeds maximum SQL length (100 KB)' }), {
              status: 400,
              headers: { 'Content-Type': 'application/json' },
            });
          }
          const params = (typeof st === 'object' && Array.isArray(st.params)) ? st.params : [];
          prepared.push(params.length > 0 ? env.DB.prepare(sql).bind(...params) : env.DB.prepare(sql));
        }

        let responsePayload;

        if (idempotencyKey && currentPayloadHash) {
          const initialPayload = JSON.stringify({
            status: 'COMMITTED_PENDING_RESOLUTION',
            batch_size: statements.length,
          });

          // Bundle idempotency insertion into the atomic batch with pending resolution status!
          const logStmt = env.DB.prepare(
            `INSERT INTO _idempotency_log (idempotency_key, payload_hash, response_body)
             VALUES (?, ?, ?)`
          ).bind(idempotencyKey, currentPayloadHash, initialPayload);
          prepared.push(logStmt);

          const rawBatchResults = await env.DB.batch(prepared);
          const userBatchResults = rawBatchResults.slice(0, -1);

          // Fault Injection point: Simulate failure/crash right after commit, BEFORE UPDATE response_body
          const faultInjection = request.headers.get('X-Test-Fault-Injection');
          if (faultInjection === 'crash-after-commit' || faultInjection === 'crash-before-update') {
            return new Response(JSON.stringify({
              error: 'Fault Injected: Simulated crash after commit',
            }), {
              status: 500,
              headers: { 'Content-Type': 'application/json' },
            });
          }

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

          // Always update detailed per-statement results in log before returning or fault injection
          await env.DB.prepare(
            'UPDATE _idempotency_log SET response_body = ? WHERE idempotency_key = ?'
          ).bind(responsePayload, idempotencyKey).run();

        } else {
          const batchResults = await env.DB.batch(prepared);
          responsePayload = JSON.stringify({
            success: true,
            results: batchResults.map(r => ({
              results: r.results || [],
              meta: {
                changes: r.meta?.changes ?? r.meta?.rows_written ?? 0,
                last_row_id: r.meta?.last_row_id ?? null,
              },
            })),
          });
        }

        return new Response(responsePayload, {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });

      } else {
        return new Response(JSON.stringify({ error: 'Endpoint Not Found' }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' },
        });
      }
    } catch (err) {
      const errMsg = String(err?.message || err);

      // Handle race condition: If concurrent request failed on UNIQUE constraint of _idempotency_log,
      // it means another concurrent worker committed the exact same idempotency_key!
      if (idempotencyKey && currentPayloadHash && (errMsg.includes('_idempotency_log') || (errMsg.includes('UNIQUE constraint failed') && errMsg.includes('idempotency_key')))) {
        await new Promise(r => setTimeout(r, 60));
        const winnerLog = await env.DB.prepare(
          'SELECT payload_hash, response_body FROM _idempotency_log WHERE idempotency_key = ?'
        ).bind(idempotencyKey).first();

        if (winnerLog) {
          if (winnerLog.payload_hash !== currentPayloadHash) {
            return new Response(JSON.stringify({
              error: 'Conflict: Idempotency-Key already used with different payload',
            }), {
              status: 422,
              headers: { 'Content-Type': 'application/json' },
            });
          }

          let winnerObj = null;
          try {
            winnerObj = JSON.parse(winnerLog.response_body);
          } catch {}

          if (winnerObj && winnerObj.status === 'COMMITTED_PENDING_RESOLUTION') {
            return new Response(JSON.stringify({
              error: "Incomplete batch state after commit",
              code: "INCOMPLETE_BATCH_STATE",
              committed: true,
            }), {
              status: 500,
              headers: { "Content-Type": "application/json" },
            });
          }

          if (winnerObj && winnerObj.status === 'INCOMPLETE_RETURNING_STATE') {
            return new Response(JSON.stringify({
              error: "Incomplete returning state after commit",
              code: "INCOMPLETE_RETURNING_STATE",
              committed: true,
            }), {
              status: 500,
              headers: { "Content-Type": "application/json" },
            });
          }

          let isValidWinner = false;
          if (winnerObj && typeof winnerObj === 'object' && winnerObj.success === true && Array.isArray(winnerObj.results) && winnerObj.status !== 'PENDING') {
            if (path === '/api/v1/query') {
              isValidWinner = Boolean(winnerObj.meta && typeof winnerObj.meta === 'object');
            } else if (path === '/api/v1/batch') {
              isValidWinner = winnerObj.results.length > 0 && winnerObj.results.every(
                r => r && typeof r === 'object' && Array.isArray(r.results) && r.meta && typeof r.meta === 'object'
              );
            }
          }

          if (isValidWinner) {
            return new Response(JSON.stringify(winnerObj), {
              status: 200,
              headers: {
                'Content-Type': 'application/json',
                'X-Idempotent-Replay': 'true',
              },
            });
          }
        }
      }

      // Map SQLite constraint failures to 409 Conflict (business conflict, not internal server error)
      const isConstraintConflict = 
        errMsg.includes('SQLITE_CONSTRAINT') || 
        errMsg.includes('CHECK constraint failed') || 
        errMsg.includes('UNIQUE constraint failed') || 
        errMsg.includes('Cannot demote or deactivate the last active Admin');

      const statusCode = isConstraintConflict ? 409 : 500;

      return new Response(JSON.stringify({
        success: false,
        error: errMsg,
        conflict: isConstraintConflict,
      }), {
        status: statusCode,
        headers: { 'Content-Type': 'application/json' },
      });
    }
  },
};

export { cleanSql, analyzeReturningClause };
