import { describe, it, expect, vi, beforeEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import worker, { handleAssistantPlan, validatePlannerDecision } from '../src/assistant.js';

describe('Cloudflare Assistant Planner Worker', () => {
  const env = {
    AI_SERVICE_TOKEN: 'test-assistant-token',
    AI_MODEL: '@cf/meta/llama-3.1-8b-instruct-fp8',
    AI: {
      run: vi.fn(),
    },
    DB: {
      prepare: vi.fn(),
    },
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  const validPayload = {
    question: 'Phòng tôi còn bao nhiêu laptop chưa cấp?',
    context: [],
    scope: {
      role: 'MANAGER',
      department_name: 'IT',
      display_timezone: 'Asia/Ho_Chi_Minh',
    },
  };

  const validDecision = {
    kind: 'tools',
    calls: [
      {
        name: 'assets.summary',
        arguments: {
          category: 'Laptop',
          statuses: ['AVAILABLE'],
        },
      },
    ],
    clarification: null,
    reason: null,
  };

  function assistantRequest(body, options = {}) {
    const {
      method = 'POST',
      token = 'test-assistant-token',
      headers = {},
    } = options;

    const reqHeaders = {
      'Content-Type': 'application/json',
      ...headers,
    };
    if (token !== null) {
      reqHeaders['Authorization'] = `Bearer ${token}`;
    }

    return new Request('http://localhost/api/assistant/plan', {
      method,
      headers: reqHeaders,
      body: body !== undefined ? (typeof body === 'string' ? body : JSON.stringify(body)) : undefined,
    });
  }

  describe('Schema Parity', () => {
    it('matches backend plan.schema.json exactly', () => {
      const backendSchemaPath = path.resolve(__dirname, '../../backend/apps/assistant/plan.schema.json');
      const workerSchemaPath = path.resolve(__dirname, '../assistant.schema.json');
      expect(fs.existsSync(workerSchemaPath)).toBe(true);
      if (fs.existsSync(backendSchemaPath)) {
        const backendContent = fs.readFileSync(backendSchemaPath, 'utf8');
        const workerContent = fs.readFileSync(workerSchemaPath, 'utf8');
        expect(workerContent).toBe(backendContent);
        expect(JSON.parse(workerContent)).toEqual(JSON.parse(backendContent));
      }
    });
  });

  describe('HTTP Method & Auth', () => {
    it('returns 405 on GET', async () => {
      const req = assistantRequest(undefined, { method: 'GET' });
      const res = await worker.fetch(req, env);
      expect(res.status).toBe(405);
      const data = await res.json();
      expect(data.error).toBe('Method Not Allowed');
      expect(env.AI.run).not.toHaveBeenCalled();
    });

    it('returns 401 when Authorization header is missing', async () => {
      const req = assistantRequest(validPayload, { token: null });
      const res = await worker.fetch(req, env);
      expect(res.status).toBe(401);
      const data = await res.json();
      expect(data.error).toBe('Unauthorized');
      expect(env.AI.run).not.toHaveBeenCalled();
    });

    it('returns 401 when Bearer token is invalid', async () => {
      const req = assistantRequest(validPayload, { token: 'wrong-token' });
      const res = await worker.fetch(req, env);
      expect(res.status).toBe(401);
      const data = await res.json();
      expect(data.error).toBe('Unauthorized');
      expect(env.AI.run).not.toHaveBeenCalled();
    });

    it('returns 401 when Bearer token is empty', async () => {
      const req = new Request('http://localhost/api/assistant/plan', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer ',
        },
        body: JSON.stringify(validPayload),
      });
      const res = await worker.fetch(req, env);
      expect(res.status).toBe(401);
      const data = await res.json();
      expect(data.error).toBe('Unauthorized');
      expect(env.AI.run).not.toHaveBeenCalled();
    });

    it('returns 401 when env.AI_SERVICE_TOKEN is missing or blank', async () => {
      const req = assistantRequest(validPayload);
      const res = await worker.fetch(req, { ...env, AI_SERVICE_TOKEN: '' });
      expect(res.status).toBe(401);
      expect(env.AI.run).not.toHaveBeenCalled();
    });
  });

  describe('Payload Size Constraints (413)', () => {
    it('returns 413 when Content-Length exceeds 32KiB', async () => {
      const req = assistantRequest(validPayload, {
        headers: { 'content-length': '32769' },
      });
      const res = await worker.fetch(req, env);
      expect(res.status).toBe(413);
      const data = await res.json();
      expect(data.error).toBe('Payload too large');
      expect(env.AI.run).not.toHaveBeenCalled();
    });

    it('returns 413 when raw UTF-8 body exceeds 32768 bytes even with fewer characters', async () => {
      // 17,000 Vietnamese 2-byte characters 'ế' = 34,000 bytes > 32768 bytes, but character count is only 17,000
      const largeQuestion = 'ế'.repeat(17000);
      const body = JSON.stringify({ question: largeQuestion });
      expect(largeQuestion.length).toBeLessThan(32768);
      expect(new TextEncoder().encode(body).byteLength).toBeGreaterThan(32768);

      const req = assistantRequest(body);
      const res = await worker.fetch(req, env);
      expect(res.status).toBe(413);
      const data = await res.json();
      expect(data.error).toBe('Payload too large');
      expect(env.AI.run).not.toHaveBeenCalled();
    });
  });

  describe('Body Validation (400)', () => {
    it('returns 400 on invalid UTF-8 bytes in request body (e.g. byte 0xFF)', async () => {
      // Construct raw Request body with invalid UTF-8 byte 0xFF
      const invalidUtf8Bytes = new Uint8Array([0x7b, 0x22, 0x71, 0x75, 0x65, 0x73, 0x74, 0x69, 0x6f, 0x6e, 0x22, 0x3a, 0x20, 0x22, 0xff, 0x22, 0x7d]);
      const req = new Request('http://localhost/api/assistant/plan', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer test-assistant-token',
        },
        body: invalidUtf8Bytes,
      });
      const res = await worker.fetch(req, env);
      expect(res.status).toBe(400);
      const data = await res.json();
      expect(data.error).toContain('Invalid UTF-8');
      expect(env.AI.run).not.toHaveBeenCalled();
    });

    it('returns 400 on malformed JSON', async () => {
      const req = assistantRequest('{ invalid json');
      const res = await worker.fetch(req, env);
      expect(res.status).toBe(400);
      const data = await res.json();
      expect(data.error).toBe('Invalid request body');
      expect(env.AI.run).not.toHaveBeenCalled();
    });

    it('returns 400 when body is an array or non-object', async () => {
      const req = assistantRequest([validPayload]);
      const res = await worker.fetch(req, env);
      expect(res.status).toBe(400);
      const data = await res.json();
      expect(data.error).toBe('Invalid request body');
      expect(env.AI.run).not.toHaveBeenCalled();
    });

    it('returns 400 when question is missing or blank', async () => {
      const req1 = assistantRequest({ context: [], scope: {} });
      const res1 = await worker.fetch(req1, env);
      expect(res1.status).toBe(400);

      const req2 = assistantRequest({ question: '   ', context: [], scope: {} });
      const res2 = await worker.fetch(req2, env);
      expect(res2.status).toBe(400);

      const req3 = assistantRequest({ question: 12345 });
      const res3 = await worker.fetch(req3, env);
      expect(res3.status).toBe(400);
      expect(env.AI.run).not.toHaveBeenCalled();
    });

    it('returns 400 when question exceeds 1000 characters', async () => {
      const longQuestion = 'x'.repeat(1001);
      const req = assistantRequest({ question: longQuestion });
      const res = await worker.fetch(req, env);
      expect(res.status).toBe(400);
      expect(env.AI.run).not.toHaveBeenCalled();
    });

    it('returns 400 when context exceeds 6 turns', async () => {
      const turns = Array(7).fill({ question: 'test?' });
      const req = assistantRequest({ question: 'câu hỏi?', context: turns });
      const res = await worker.fetch(req, env);
      expect(res.status).toBe(400);
      expect(env.AI.run).not.toHaveBeenCalled();
    });

    it('returns 400 when context total characters exceeds 6000', async () => {
      const bigTurn = { question: 'x'.repeat(6001) };
      const req = assistantRequest({ question: 'câu hỏi?', context: [bigTurn] });
      const res = await worker.fetch(req, env);
      expect(res.status).toBe(400);
      expect(env.AI.run).not.toHaveBeenCalled();
    });

    it('returns 400 when context turn is malformed', async () => {
      const badTurn = { refs: [{ asset_code: 'AST-1' }] }; // missing question
      const req = assistantRequest({ question: 'câu hỏi?', context: [badTurn] });
      const res = await worker.fetch(req, env);
      expect(res.status).toBe(400);
      expect(env.AI.run).not.toHaveBeenCalled();
    });

    it('returns 400 when context turn contains unexpected fields (e.g. role/content)', async () => {
      const badTurn = { question: 'câu hỏi?', role: 'user', content: 'test' };
      const req = assistantRequest({ question: 'câu hỏi?', context: [badTurn] });
      const res = await worker.fetch(req, env);
      expect(res.status).toBe(400);
      const data = await res.json();
      expect(data.error).toContain('unexpected field');
      expect(env.AI.run).not.toHaveBeenCalled();
    });

    it('returns 400 when context turn refs is not array or has non-string asset_code or extra fields', async () => {
      const badRefs1 = { question: 'câu hỏi?', refs: 'not-array' };
      const res1 = await worker.fetch(assistantRequest({ question: 'câu hỏi?', context: [badRefs1] }), env);
      expect(res1.status).toBe(400);

      const badRefs2 = { question: 'câu hỏi?', refs: [{ asset_code: 123 }] };
      const res2 = await worker.fetch(assistantRequest({ question: 'câu hỏi?', context: [badRefs2] }), env);
      expect(res2.status).toBe(400);

      const badRefs3 = { question: 'câu hỏi?', refs: [{ asset_code: 'AST-1', extra: 'bad' }] };
      const res3 = await worker.fetch(assistantRequest({ question: 'câu hỏi?', context: [badRefs3] }), env);
      expect(res3.status).toBe(400);

      const validRefs = { question: 'câu hỏi?', refs: [{ asset_code: 'AST-1' }] };
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(validDecision) });
      const res4 = await worker.fetch(assistantRequest({ question: 'câu hỏi?', context: [validRefs] }), env);
      expect(res4.status).toBe(200);
    });

    it('returns 400 when context is not an array', async () => {
      const req = assistantRequest({ question: 'test', context: 'not-an-array' });
      const res = await worker.fetch(req, env);
      expect(res.status).toBe(400);
      expect(env.AI.run).not.toHaveBeenCalled();
    });

    it('returns 400 when scope is not an object', async () => {
      const req = assistantRequest({ question: 'test', scope: 'invalid' });
      const res = await worker.fetch(req, env);
      expect(res.status).toBe(400);
      expect(env.AI.run).not.toHaveBeenCalled();
    });
  });

  describe('Prompt Construction & Security', () => {
    it('constructs prompt without elevating user/context to system role and does not touch D1', async () => {
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(validDecision) });

      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(200);

      // Verify D1 was never touched
      expect(env.DB.prepare).not.toHaveBeenCalled();

      // Verify AI call params
      expect(env.AI.run).toHaveBeenCalledTimes(1);
      const [model, options] = env.AI.run.mock.calls[0];
      expect(model).toBe('@cf/meta/llama-3.1-8b-instruct-fp8');
      expect(options.temperature).toBe(0.1);
      expect(options.max_tokens).toBe(768);

      // Verify messages array
      expect(options.messages).toHaveLength(2);
      expect(options.messages[0].role).toBe('system');
      expect(options.messages[0].content).toContain('Bạn là AI Planner');
      expect(options.messages[0].content).not.toContain(validPayload.question);

      expect(options.messages[1].role).toBe('user');
      expect(options.messages[1].content).toContain(validPayload.question);
      expect(options.messages[1].content).toContain('MANAGER');
    });

    it('embeds the full canonical schema compactly in the system prompt sent to the model', async () => {
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(validDecision) });

      const res = await handleAssistantPlan(assistantRequest(validPayload), env);
      expect(res.status).toBe(200);

      const [, options] = env.AI.run.mock.calls[0];
      const system = options.messages[0].content;
      const head = 'theo đúng JSON Schema sau:\n\n';
      const tail = '\n\nYêu cầu bắt buộc:';
      const start = system.indexOf(head) + head.length;
      const end = system.indexOf(tail);
      expect(start).toBeGreaterThan(head.length - 1);
      expect(end).toBeGreaterThan(start);
      const embedded = system.slice(start, end);

      const canonical = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../assistant.schema.json'), 'utf8'));
      expect(JSON.parse(embedded)).toEqual(canonical);
      expect(embedded).not.toMatch(/\n/);
      const pretty = JSON.stringify(canonical, null, 2);
      expect(Buffer.byteLength(embedded)).toBeLessThan(Buffer.byteLength(pretty));

      // Instructions around the schema stay intact.
      expect(system.startsWith('Bạn là AI Planner cho hệ thống quản lý tài sản AssetHub.')).toBe(true);
      for (let rule = 1; rule <= 7; rule += 1) {
        expect(system).toContain(`\n${rule}. `);
      }
      expect(system).toContain('"kind", "calls", "clarification", "reason"');
      expect(system).toContain('KHÔNG bọc trong markdown code fence');
      expect(options.messages[1].content).toContain(validPayload.question);
    });

    it('passes response_format json_object, messages and temperature to env.AI.run', async () => {
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(validDecision) });

      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(200);

      expect(env.AI.run).toHaveBeenCalledTimes(1);
      const [model, options] = env.AI.run.mock.calls[0];
      expect(model).toBe('@cf/meta/llama-3.1-8b-instruct-fp8');
      expect(options.temperature).toBe(0.1);
      expect(options.max_tokens).toBe(768);
      expect(options.response_format).toEqual({ type: 'json_object' });
      expect(options.messages).toHaveLength(2);
      expect(options.messages[0].role).toBe('system');
      expect(options.messages[1].role).toBe('user');
    });

    it('includes general Vietnamese semantic tool-routing guidance without hardcoded evaluation codes', async () => {
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(validDecision) });

      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(200);

      const [, options] = env.AI.run.mock.calls[0];
      const system = options.messages[0].content;

      // Verify general tool-routing rules & supported tool names
      expect(system).toContain('Quy tắc định tuyến công cụ (Tool Routing Guidance):');
      expect(system).toContain('assets.summary');
      expect(system).toContain('category');
      expect(system).toContain('statuses: ["AVAILABLE"]');
      expect(system).toContain('requests.search');
      expect(system).toContain('request_type');
      expect(system).toContain('assets.search');
      expect(system).toContain('asset_code');
      expect(system).toContain('assets.history');
      expect(system).toContain('"mine": true');
      expect(system).toContain('procedures.lookup');
      for (const topic of ['allocate', 'return', 'damage_report', 'roles', 'asset_statuses']) {
        expect(system).toContain(topic);
      }
      expect(system).toContain('refs');
      expect(system).toContain('clarify');
      expect(system).toContain('out_of_scope');
      expect(system).toContain('missing_knowledge');

      // Specific selection rule nuances & anti-echo & latest-turn distinct refs
      expect(system).toContain('CHỈ dùng "assets.summary" khi đối tượng là TÀI SẢN');
      expect(system).toContain('tuyệt đối KHÔNG dùng "assets.summary" cho phiếu yêu cầu');
      expect(system).toContain('KHÔNG tự thêm tham số ngoài schema');
      expect(system).toContain('chưa cấp');
      expect(system).toContain('chưa được cấp');
      expect(system).toContain('TUYỆT ĐỐI KHÔNG lặp lại nguyên văn câu hỏi của người dùng');
      expect(system).toContain('lượt gần nhất liên quan');
      expect(system).toContain('đếm các mã phân biệt trong lượt đó');
      expect(system).toContain('lặp lại cùng 1 mã qua nhiều lượt không tính thành nhiều thiết bị');
      expect(system).toContain('không làm hỏng câu hỏi nêu rõ mã cụ thể hoặc câu hỏi về cả hai/tất cả thiết bị');
      expect(system).toContain('TUYỆT ĐỐI KHÔNG tự tiện chọn 1 mã và KHÔNG tự bịa thêm bộ lọc');

      // Scope identity & status omission rules
      expect(system).toContain('Scope là danh tính người hỏi, KHÔNG phải bộ lọc');
      expect(system).toContain('phòng/người giữ nêu trong câu hỏi phải giữ nguyên vào department/holder kể cả khác phòng');
      expect(system).toContain('quyền do backend kiểm tra');
      expect(system).toContain('Chỉ đặt statuses khi câu hỏi có chỉ trạng thái; không nêu thì bỏ statuses');
      expect(system).toContain('Minh họa cấu trúc: {"kind":"clarify","calls":[],"clarification":"Bạn muốn hỏi về tài sản nào?","reason":null}');

      // Ensure no hardcoded evaluation specifics
      expect(system).not.toContain('W5-IT');
      expect(system).not.toContain('23');
      expect(system).not.toContain('Nhân viên IT');
      expect(system).not.toContain('HR');
      expect(system).not.toContain('W5');
    });

    it('instructs planner that scope is caller identity not filter and preserves user filters', async () => {
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(validDecision) });

      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(200);

      const [, options] = env.AI.run.mock.calls[0];
      const system = options.messages[0].content;

      expect(system).toContain('Scope là danh tính người hỏi, KHÔNG phải bộ lọc');
      expect(system).toContain('quyền do backend kiểm tra');
      expect(system).toContain('không nêu thì bỏ statuses');
      expect(system).not.toContain('HR');
      expect(system).not.toContain('W5');
    });

    it('prefers env.ASSISTANT_MODEL over env.AI_MODEL', async () => {
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(validDecision) });
      const customEnv = {
        ...env,
        ASSISTANT_MODEL: '@cf/meta/llama-3.3-70b-instruct-fp8-fast',
        AI_MODEL: '@cf/meta/llama-3.1-8b-instruct-fp8',
      };

      const res = await worker.fetch(assistantRequest(validPayload), customEnv);
      expect(res.status).toBe(200);
      expect(env.AI.run.mock.calls[0][0]).toBe('@cf/meta/llama-3.3-70b-instruct-fp8-fast');
    });
  });

  describe('Successful Decision Parsing (200)', () => {
    it('handles direct string response', async () => {
      env.AI.run.mockResolvedValueOnce(JSON.stringify(validDecision));
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(200);
      const data = await res.json();
      expect(data.decision).toEqual(validDecision);
    });

    it('handles response object with string response', async () => {
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(validDecision) });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(200);
      const data = await res.json();
      expect(data.decision).toEqual(validDecision);
    });

    it('handles response object with already-parsed object response', async () => {
      env.AI.run.mockResolvedValueOnce({ response: validDecision });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(200);
      const data = await res.json();
      expect(data.decision).toEqual(validDecision);
    });

    it('rejects markdown code fence with 502 according to strict JSON plan', async () => {
      const fenced = '```json\n' + JSON.stringify(validDecision, null, 2) + '\n```';
      env.AI.run.mockResolvedValueOnce({ response: fenced });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(502);
      const data = await res.json();
      expect(data.code).toBe('INVALID_PLAN');
    });

    it('rejects JSON surrounded by prose with 502', async () => {
      const prose = 'Here is your plan: ' + JSON.stringify(validDecision);
      env.AI.run.mockResolvedValueOnce({ response: prose });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(502);
      const data = await res.json();
      expect(data.code).toBe('INVALID_PLAN');
    });

    it('accepts valid clarify decision', async () => {
      const clarifyDecision = {
        kind: 'clarify',
        calls: [],
        clarification: 'Bạn muốn kiểm tra máy tính xách tay hay màn hình máy tính?',
        reason: null,
      };
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(clarifyDecision) });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(200);
      const data = await res.json();
      expect(data.decision).toEqual(clarifyDecision);
    });

    it('accepts valid clarify decision for multi-ref follow-up and verifies prompt contains clarify shape illustration', async () => {
      // Synthetic payload with 2 non-W5 refs in context turn
      const syntheticPayload = {
        question: 'Thiết bị đó ai mượn?',
        context: [
          {
            question: 'Tìm màn hình Dell và máy in HP',
            refs: [
              { asset_code: 'EQ-DISP-01' },
              { asset_code: 'EQ-PRN-02' },
            ],
          },
        ],
        scope: { role: 'USER', department_id: 2, department_name: 'OPS' },
      };

      const syntheticClarifyDecision = {
        kind: 'clarify',
        calls: [],
        clarification: 'Bạn muốn hỏi về thiết bị EQ-DISP-01 hay EQ-PRN-02?',
        reason: null,
      };

      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(syntheticClarifyDecision) });

      const res = await worker.fetch(assistantRequest(syntheticPayload), env);
      expect(res.status).toBe(200);

      const data = await res.json();
      expect(data.decision).toEqual(syntheticClarifyDecision);

      // Verify the system prompt captured by fake AI contains the general clarify shape illustration
      const [, options] = env.AI.run.mock.calls[0];
      const system = options.messages[0].content;

      expect(system).toContain('{"kind":"clarify","calls":[],"clarification":"Bạn muốn hỏi về tài sản nào?","reason":null}');
      expect(system).toContain('mẫu chỉ minh họa cấu trúc');
      expect(system).toContain('nêu lựa chọn refs nếu cần');

      // Negative assertions: ensure no W5, HR or hardcoded case tokens in prompt
      expect(system).not.toContain('W5');
      expect(system).not.toContain('HR');
      expect(system).not.toContain('EQ-DISP');
    });

    it('accepts valid handoff decision with missing_knowledge', async () => {
      const handoffDecision = {
        kind: 'handoff',
        calls: [],
        clarification: null,
        reason: 'missing_knowledge',
      };
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(handoffDecision) });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(200);
      const data = await res.json();
      expect(data.decision).toEqual(handoffDecision);
    });

    it('accepts valid handoff decision with unsupported_asset_question', async () => {
      const handoffDecision = {
        kind: 'handoff',
        calls: [],
        clarification: null,
        reason: 'unsupported_asset_question',
      };
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(handoffDecision) });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(200);
      const data = await res.json();
      expect(data.decision).toEqual(handoffDecision);
    });

    it('accepts valid out_of_scope decision', async () => {
      const outOfScopeDecision = {
        kind: 'out_of_scope',
        calls: [],
        clarification: null,
        reason: null,
      };
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(outOfScopeDecision) });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(200);
      const data = await res.json();
      expect(data.decision).toEqual(outOfScopeDecision);
    });
  });

  describe('Model Output Validation & Tool Rejection (502)', () => {
    it('returns 502 when assets.search has wrong argument types (null, number, boolean, object, array)', async () => {
      const invalidArgs = [
        { query: null },
        { query: 123 },
        { query: true },
        { category: {} },
        { holder: [] },
        { department: null },
        { statuses: null },
        { statuses: [] },
        { statuses: ['NOT_A_STATUS'] },
        { limit: '5' },
        { limit: true },
        { limit: 0 },
        { limit: 25 },
      ];

      for (const args of invalidArgs) {
        const payload = {
          kind: 'tools',
          calls: [{ name: 'assets.search', arguments: args }],
          clarification: null,
          reason: null,
        };
        env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(payload) });
        const res = await worker.fetch(assistantRequest(validPayload), env);
        expect(res.status).toBe(502);
        const data = await res.json();
        expect(data.code).toBe('INVALID_PLAN');
      }
    });

    it('returns 502 when assets.history has wrong argument types or invalid selectors', async () => {
      const invalidHistoryArgs = [
        { mine: true, asset_code: null },
        { mine: true, asset_code: 123 },
        { mine: true, asset_code: 'W5-IT-L24' },
        { mine: false },
        { mine: 'true' },
        { asset_code: null },
        { asset_code: 123 },
        { asset_code: {} },
        { actions: [] },
        { actions: ['BAD_ACTION'] },
        { since: 'invalid-date' },
        { since: null },
        { until: 'invalid-date' },
        { since: '2026-11-10', until: '2026-11-01' }, // since > until
      ];

      for (const args of invalidHistoryArgs) {
        const payload = {
          kind: 'tools',
          calls: [{ name: 'assets.history', arguments: args }],
          clarification: null,
          reason: null,
        };
        env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(payload) });
        const res = await worker.fetch(assistantRequest(validPayload), env);
        expect(res.status).toBe(502);
        const data = await res.json();
        expect(data.code).toBe('INVALID_PLAN');
      }
    });

    it('accepts assets.summary with group_by: null and rejects invalid group_by types', async () => {
      // Valid group_by: null
      const validSummaryNull = {
        kind: 'tools',
        calls: [{ name: 'assets.summary', arguments: { group_by: null } }],
        clarification: null,
        reason: null,
      };
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(validSummaryNull) });
      const res1 = await worker.fetch(assistantRequest(validPayload), env);
      expect(res1.status).toBe(200);

      // Invalid group_by types: number, bool, object, invalid string
      const invalidGroupBys = [123, true, {}, 'invalid_group'];
      for (const gb of invalidGroupBys) {
        const badPayload = {
          kind: 'tools',
          calls: [{ name: 'assets.summary', arguments: { group_by: gb } }],
          clarification: null,
          reason: null,
        };
        env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(badPayload) });
        const res = await worker.fetch(assistantRequest(validPayload), env);
        expect(res.status).toBe(502);
      }
    });

    it('returns 502 INVALID_PLAN on completely invalid JSON from AI', async () => {
      env.AI.run.mockResolvedValueOnce({ response: 'I am not sure how to answer this.' });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(502);
      const data = await res.json();
      expect(data.error).toBe('Invalid AI plan');
      expect(data.code).toBe('INVALID_PLAN');
    });

    it('returns 502 INVALID_PLAN when AI invents unknown tool (tool lạ)', async () => {
      const unknownToolDecision = {
        kind: 'tools',
        calls: [
          {
            name: 'execute_sql',
            arguments: { sql: 'SELECT * FROM assets' },
          },
        ],
        clarification: null,
        reason: null,
      };
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(unknownToolDecision) });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(502);
      const data = await res.json();
      expect(data.error).toBe('Invalid AI plan');
      expect(data.code).toBe('INVALID_PLAN');
    });

    it('returns 502 INVALID_PLAN when decision has extra root keys (additionalProperties violation)', async () => {
      const extraKeyDecision = {
        ...validDecision,
        sql: 'SELECT 1',
      };
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(extraKeyDecision) });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(502);
      const data = await res.json();
      expect(data.error).toBe('Invalid AI plan');
      expect(data.code).toBe('INVALID_PLAN');
    });

    it('returns 502 INVALID_PLAN when kind is tools but calls is empty', async () => {
      const emptyToolsDecision = {
        kind: 'tools',
        calls: [],
        clarification: null,
        reason: null,
      };
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(emptyToolsDecision) });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(502);
      const data = await res.json();
      expect(data.code).toBe('INVALID_PLAN');
    });

    it('returns 502 INVALID_PLAN when kind is tools with >3 calls', async () => {
      const tooManyCalls = {
        kind: 'tools',
        calls: [
          { name: 'assets.search', arguments: {} },
          { name: 'assets.summary', arguments: {} },
          { name: 'assets.history', arguments: { mine: true } },
          { name: 'procedures.lookup', arguments: { topic: 'allocate' } },
        ],
        clarification: null,
        reason: null,
      };
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(tooManyCalls) });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(502);
      const data = await res.json();
      expect(data.code).toBe('INVALID_PLAN');
    });

    it('returns 502 INVALID_PLAN when tool arguments have unknown keys or raw SQL injection', async () => {
      const sqlInjectionCall = {
        kind: 'tools',
        calls: [
          {
            name: 'assets.search',
            arguments: { limit: 100, raw_sql: 'SELECT secret' },
          },
        ],
        clarification: null,
        reason: null,
      };
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(sqlInjectionCall) });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(502);
      const data = await res.json();
      expect(data.code).toBe('INVALID_PLAN');
    });

    it('returns 502 INVALID_PLAN when assets.summary contains limit argument', async () => {
      const summaryWithLimit = {
        kind: 'tools',
        calls: [
          {
            name: 'assets.summary',
            arguments: { category: 'Laptop', limit: 10 },
          },
        ],
        clarification: null,
        reason: null,
      };
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(summaryWithLimit) });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(502);
      const data = await res.json();
      expect(data.code).toBe('INVALID_PLAN');
    });

    it('returns 502 INVALID_PLAN when assets.history contains both asset_code and mine', async () => {
      const historyBothSelectors = {
        kind: 'tools',
        calls: [
          {
            name: 'assets.history',
            arguments: { asset_code: 'W5-IT-L24', mine: true },
          },
        ],
        clarification: null,
        reason: null,
      };
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(historyBothSelectors) });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(502);
      const data = await res.json();
      expect(data.code).toBe('INVALID_PLAN');
    });

    it('returns 502 INVALID_PLAN when assets.history contains mine: false', async () => {
      const historyMineFalse = {
        kind: 'tools',
        calls: [
          {
            name: 'assets.history',
            arguments: { mine: false },
          },
        ],
        clarification: null,
        reason: null,
      };
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(historyMineFalse) });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(502);
      const data = await res.json();
      expect(data.code).toBe('INVALID_PLAN');
    });

    it('returns 502 INVALID_PLAN when kind is clarify but calls has items', async () => {
      const badClarify = {
        kind: 'clarify',
        calls: [{ name: 'assets.search', arguments: {} }],
        clarification: 'Clarify please',
        reason: null,
      };
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(badClarify) });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(502);
      const data = await res.json();
      expect(data.code).toBe('INVALID_PLAN');
    });

    it('returns 502 INVALID_PLAN when output exceeds 64KiB cap', async () => {
      const bigString = 'x'.repeat(66000);
      env.AI.run.mockResolvedValueOnce({ response: bigString });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(502);
      const data = await res.json();
      expect(data.error).toBe('Invalid AI plan');
      expect(data.code).toBe('INVALID_PLAN');
      expect(data.reason_code).toBe('PAYLOAD_TOO_LARGE');
    });

    it('returns static allowlisted reason_code on 502 and never reflects dynamic user or model text', async () => {
      const ALLOWED_REASON_CODES = [
        'CODE_FENCE',
        'JSON_PARSE_ERROR',
        'SCHEMA_VALIDATION_FAILED',
        'PAYLOAD_TOO_LARGE',
      ];

      const testCases = [
        {
          response: '```json\n{"kind":"tools"}\n```',
          expectedReason: 'CODE_FENCE',
        },
        {
          response: 'Malformed { not json <script>alert(1)</script>',
          expectedReason: 'JSON_PARSE_ERROR',
        },
        {
          response: JSON.stringify({ kind: 'tools', calls: [{ name: 'unknown_tool' }], clarification: null, reason: null }),
          expectedReason: 'SCHEMA_VALIDATION_FAILED',
        },
        {
          response: 'y'.repeat(66000),
          expectedReason: 'PAYLOAD_TOO_LARGE',
        },
      ];

      const sensitivePayload = {
        question: 'SENSITIVE_LEAK_PROMPT_KEYWORD_XYZ',
        context: [],
        scope: {},
      };

      for (const tc of testCases) {
        env.AI.run.mockResolvedValueOnce({ response: tc.response });
        const res = await worker.fetch(assistantRequest(sensitivePayload), env);
        expect(res.status).toBe(502);
        const data = await res.json();
        expect(data.code).toBe('INVALID_PLAN');
        expect(ALLOWED_REASON_CODES).toContain(data.reason_code);
        expect(data.reason_code).toBe(tc.expectedReason);

        // Security assertion: verify dynamic user text or raw model error strings are NOT in response
        const rawJson = JSON.stringify(data);
        expect(rawJson).not.toContain('SENSITIVE_LEAK_PROMPT_KEYWORD_XYZ');
        expect(rawJson).not.toContain('alert(1)');
        expect(rawJson).not.toContain('unknown_tool');
      }
    });
  });

  describe('Upstream AI Errors (429 & 503)', () => {
    it('returns 429 on quota exceeded error code 3036', async () => {
      const quotaErr = new Error('Workers AI Quota Exceeded');
      quotaErr.code = 3036;
      env.AI.run.mockRejectedValueOnce(quotaErr);

      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(429);
      const data = await res.json();
      expect(data.error).toBe('Quota exceeded');
      expect(data.code).toBe(3036);
    });

    it('returns 429 on capacity constrained error code 3040', async () => {
      const capErr = new Error('Capacity constrained');
      capErr.code = 3040;
      env.AI.run.mockRejectedValueOnce(capErr);

      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(429);
      const data = await res.json();
      expect(data.error).toBe('Quota exceeded');
      expect(data.code).toBe(3036);
    });

    it('returns 503 UNAVAILABLE on error 10016 (does not confuse with quota)', async () => {
      const modelErr = new Error('Model error');
      modelErr.code = 10016;
      env.AI.run.mockRejectedValueOnce(modelErr);

      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(503);
      const data = await res.json();
      expect(data.error).toBe('AI service unavailable');
      expect(data.code).toBe('UNAVAILABLE');
    });

    it('returns 503 UNAVAILABLE on generic upstream error', async () => {
      env.AI.run.mockRejectedValueOnce(new Error('Gateway timeout'));

      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(503);
      const data = await res.json();
      expect(data.error).toBe('AI service unavailable');
      expect(data.code).toBe('UNAVAILABLE');
    });
  });

  describe('Isolation & D1 Protection', () => {
    it('does not query D1 when planning an asset question (spec requirement)', async () => {
      env.AI.run.mockResolvedValueOnce({ response: JSON.stringify(validDecision) });
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(200);
      expect(env.DB.prepare).not.toHaveBeenCalled();
    });

    it('does not query D1 even on error paths', async () => {
      env.AI.run.mockRejectedValueOnce(new Error('failure'));
      const res = await worker.fetch(assistantRequest(validPayload), env);
      expect(res.status).toBe(503);
      expect(env.DB.prepare).not.toHaveBeenCalled();
    });
  });
});
