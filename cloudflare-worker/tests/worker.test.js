import { describe, it, expect, vi, beforeEach } from 'vitest';
import worker from '../src/index.js';

describe('Cloudflare AI Worker', () => {
  const env = {
    AI_SERVICE_TOKEN: 'test-secret-token',
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

  const samplePayload = {
    request_id: 'req-12345',
    name: 'MacBook Pro M3 Max 16 inch',
    description: 'Máy tính xách tay cấu hình cao cho kỹ sư',
    categories: [
      { id: 1, name: 'Laptop' },
      { id: 2, name: 'Màn hình' },
      { id: 3, name: 'Bàn phím' },
    ],
  };

  it('returns 404 on invalid path', async () => {
    const req = new Request('http://localhost/api/other', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(404);
    const data = await res.json();
    expect(data.error).toBe('Not Found');
  });

  it('returns 405 on invalid method', async () => {
    const req = new Request('http://localhost/api/suggest-category', {
      method: 'GET',
      headers: {
        Authorization: 'Bearer test-secret-token',
      },
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(405);
    const data = await res.json();
    expect(data.error).toBe('Method Not Allowed');
  });

  it('returns 401 when Authorization header is missing or invalid', async () => {
    const noAuthReq = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(samplePayload),
    });
    const res1 = await worker.fetch(noAuthReq, env);
    expect(res1.status).toBe(401);
    const data1 = await res1.json();
    expect(data1.error).toBe('Unauthorized');

    const badAuthReq = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer wrong-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });
    const res2 = await worker.fetch(badAuthReq, env);
    expect(res2.status).toBe(401);
  });

  it('returns 200 with suggestion and saves log to D1', async () => {
    env.AI.run = vi.fn().mockResolvedValue({
      response: JSON.stringify({
        suggested_category_id: 1,
        explanation: 'MacBook Pro là dòng máy tính xách tay.',
      }),
    });

    const runMock = vi.fn().mockResolvedValue({ success: true });
    const bindMock = vi.fn().mockReturnValue({ run: runMock });
    env.DB.prepare = vi.fn().mockReturnValue({ bind: bindMock });

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(200);

    const data = await res.json();
    expect(data.request_id).toBe('req-12345');
    expect(data.suggested_category_id).toBe(1);
    expect(data.suggested_category_name).toBe('Laptop');
    expect(data.explanation).toContain('MacBook Pro');
    expect(data.log_saved).toBe(true);

    expect(env.AI.run).toHaveBeenCalledWith(
      '@cf/meta/llama-3.1-8b-instruct-fp8',
      expect.any(Object)
    );
    expect(env.DB.prepare).toHaveBeenCalledWith(
      expect.stringContaining('INSERT INTO ai_suggestion_logs')
    );
    expect(bindMock).toHaveBeenCalledWith(
      'req-12345',
      samplePayload.name,
      samplePayload.description,
      1,
      'Laptop',
      '@cf/meta/llama-3.1-8b-instruct-fp8',
      expect.any(String)
    );
  });

  it('uses default model @cf/meta/llama-3.1-8b-instruct-fp8 when env.AI_MODEL is not set', async () => {
    const envWithoutModel = {
      ...env,
      AI_MODEL: undefined,
    };
    envWithoutModel.AI.run = vi.fn().mockResolvedValue({
      response: JSON.stringify({
        suggested_category_id: 1,
        explanation: 'MacBook Pro',
      }),
    });
    const runMock = vi.fn().mockResolvedValue({ success: true });
    envWithoutModel.DB.prepare = vi.fn().mockReturnValue({
      bind: vi.fn().mockReturnValue({ run: runMock }),
    });

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, envWithoutModel);
    expect(res.status).toBe(200);
    expect(envWithoutModel.AI.run).toHaveBeenCalledWith(
      '@cf/meta/llama-3.1-8b-instruct-fp8',
      expect.any(Object)
    );
  });

  it('returns suggestion with log_saved: false when D1 fails', async () => {
    env.AI.run = vi.fn().mockResolvedValue({
      response: JSON.stringify({
        suggested_category_id: 1,
        explanation: 'Máy tính xách tay.',
      }),
    });

    env.DB.prepare = vi.fn().mockImplementation(() => {
      throw new Error('D1 database connection lost');
    });

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(200);

    const data = await res.json();
    expect(data.suggested_category_id).toBe(1);
    expect(data.suggested_category_name).toBe('Laptop');
    expect(data.log_saved).toBe(false);
  });

  it('returns 429 when AI returns quota exceeded error (code 3036)', async () => {
    const err = new Error('3036: Quota exceeded for model');
    err.code = 3036;
    env.AI.run = vi.fn().mockRejectedValue(err);

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(429);
    const data = await res.json();
    expect(data).toEqual({ error: 'Quota exceeded', code: 3036 });
  });

  it('returns 429 when AI returns capacity error (code 3040)', async () => {
    const err = new Error('Capacity constrained at this time');
    err.code = 3040;
    env.AI.run = vi.fn().mockRejectedValue(err);

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(429);
    const data = await res.json();
    expect(data).toEqual({ error: 'Capacity constrained', code: 3040 });
  });

  it('does NOT treat error 10016 as quota, returns 503 UNAVAILABLE', async () => {
    const err = new Error('Error 10016: rate limit or other code');
    err.code = 10016;
    env.AI.run = vi.fn().mockRejectedValue(err);

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(503);
    const data = await res.json();
    expect(data).toEqual({ error: 'AI service unavailable', code: 'UNAVAILABLE' });
  });

  it('returns 503 UNAVAILABLE when AI service fails with other error', async () => {
    env.AI.run = vi.fn().mockRejectedValue(new Error('Cloudflare AI internal error'));

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(503);
    const data = await res.json();
    expect(data).toEqual({ error: 'AI service unavailable', code: 'UNAVAILABLE' });
  });

  it('returns 502 INVALID_SUGGESTION when category is not in list (id unknown)', async () => {
    env.AI.run = vi.fn().mockResolvedValue({
      response: JSON.stringify({
        suggested_category_id: 999,
        explanation: 'Danh mục linh tinh không tồn tại.',
      }),
    });

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(502);
    const data = await res.json();
    expect(data).toEqual({
      error: 'Invalid AI suggestion: category not in list',
      code: 'INVALID_SUGGESTION',
    });
  });

  it('returns 502 INVALID_SUGGESTION on completely malformed or unmatchable AI output', async () => {
    env.AI.run = vi.fn().mockResolvedValue({
      response: 'Tôi không thể trả lời câu hỏi này.',
    });

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(502);
    const data = await res.json();
    expect(data).toEqual({
      error: 'Invalid AI suggestion: category not in list',
      code: 'INVALID_SUGGESTION',
    });
  });

  it('returns 502 and does NOT insert log when model returns invalid ID and explanation mentions a category name in negative context (Bug 5)', async () => {
    env.AI.run = vi.fn().mockResolvedValue({
      response: JSON.stringify({
        suggested_category_id: 999,
        explanation: 'Laptop không phù hợp với yêu cầu này',
      }),
    });

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(502);

    const data = await res.json();
    expect(data.code).toBe('INVALID_SUGGESTION');
    expect(data.error).toBe('Invalid AI suggestion: category not in list');
    expect(env.DB.prepare).not.toHaveBeenCalled();
  });

  it('returns 502 with code INVALID_SUGGESTION on non-JSON output containing category name', async () => {
    env.AI.run = vi.fn().mockResolvedValue({
      response: 'Theo đánh giá thì thiết bị này thuộc loại Bàn phím cơ cao cấp.',
    });

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(502);

    const data = await res.json();
    expect(data.code).toBe('INVALID_SUGGESTION');
    expect(data.error).toBe('Invalid AI suggestion: category not in list');
    expect(env.DB.prepare).not.toHaveBeenCalled();
  });

  it('returns 200 and inserts log in D1 when valid structured category name is provided', async () => {
    env.AI.run = vi.fn().mockResolvedValue({
      response: JSON.stringify({
        suggested_category_name: 'Bàn phím',
        explanation: 'Thiết bị ngoại vi dùng để gõ phím.',
      }),
    });

    const runMock = vi.fn().mockResolvedValue({ success: true });
    const bindMock = vi.fn().mockReturnValue({ run: runMock });
    env.DB.prepare = vi.fn().mockReturnValue({ bind: bindMock });

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(200);

    const data = await res.json();
    expect(data.request_id).toBe('req-12345');
    expect(data.suggested_category_id).toBe(3);
    expect(data.suggested_category_name).toBe('Bàn phím');
    expect(data.explanation).toContain('gõ phím');
    expect(data.log_saved).toBe(true);

    expect(env.DB.prepare).toHaveBeenCalledWith(
      expect.stringContaining('INSERT INTO ai_suggestion_logs')
    );
    expect(bindMock).toHaveBeenCalledWith(
      'req-12345',
      samplePayload.name,
      samplePayload.description,
      3,
      'Bàn phím',
      '@cf/meta/llama-3.1-8b-instruct-fp8',
      expect.any(String)
    );
  });

  it('rejects AI output with suggested_category_id: 1.9 with 502 INVALID_SUGGESTION and no DB log', async () => {
    env.AI.run = vi.fn().mockResolvedValue({
      response: JSON.stringify({
        suggested_category_id: 1.9,
        explanation: 'Float ID test',
      }),
    });

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(502);
    const data = await res.json();
    expect(data).toEqual({
      error: 'Invalid AI suggestion: category not in list',
      code: 'INVALID_SUGGESTION',
    });
    expect(env.DB.prepare).not.toHaveBeenCalled();
  });

  it('rejects AI output with suggested_category_id: Infinity with 502 INVALID_SUGGESTION and no DB log', async () => {
    env.AI.run = vi.fn().mockResolvedValue({
      response: {
        suggested_category_id: Infinity,
        explanation: 'Infinity test',
      },
    });

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(502);
    const data = await res.json();
    expect(data).toEqual({
      error: 'Invalid AI suggestion: category not in list',
      code: 'INVALID_SUGGESTION',
    });
    expect(env.DB.prepare).not.toHaveBeenCalled();
  });

  it('rejects AI output with suggested_category_id: true with 502 INVALID_SUGGESTION and no DB log', async () => {
    env.AI.run = vi.fn().mockResolvedValue({
      response: JSON.stringify({
        suggested_category_id: true,
        explanation: 'Boolean test',
      }),
    });

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(502);
    const data = await res.json();
    expect(data).toEqual({
      error: 'Invalid AI suggestion: category not in list',
      code: 'INVALID_SUGGESTION',
    });
    expect(env.DB.prepare).not.toHaveBeenCalled();
  });

  it('rejects AI output with suggested_category_id: [1] with 502 INVALID_SUGGESTION and no DB log', async () => {
    env.AI.run = vi.fn().mockResolvedValue({
      response: JSON.stringify({
        suggested_category_id: [1],
        explanation: 'Array test',
      }),
    });

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(502);
    const data = await res.json();
    expect(data).toEqual({
      error: 'Invalid AI suggestion: category not in list',
      code: 'INVALID_SUGGESTION',
    });
    expect(env.DB.prepare).not.toHaveBeenCalled();
  });

  it('accepts AI output with suggested_category_id: "1" (valid string integer) with 200, category 1', async () => {
    env.AI.run = vi.fn().mockResolvedValue({
      response: JSON.stringify({
        suggested_category_id: '1',
        explanation: 'String integer test',
      }),
    });

    const runMock = vi.fn().mockResolvedValue({ success: true });
    const bindMock = vi.fn().mockReturnValue({ run: runMock });
    env.DB.prepare = vi.fn().mockReturnValue({ bind: bindMock });

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.suggested_category_id).toBe(1);
    expect(data.suggested_category_name).toBe('Laptop');
    expect(env.DB.prepare).toHaveBeenCalled();
  });

  it('accepts AI output with suggested_category_id: 1 (valid integer) with 200, category 1', async () => {
    env.AI.run = vi.fn().mockResolvedValue({
      response: JSON.stringify({
        suggested_category_id: 1,
        explanation: 'Valid integer test',
      }),
    });

    const runMock = vi.fn().mockResolvedValue({ success: true });
    const bindMock = vi.fn().mockReturnValue({ run: runMock });
    env.DB.prepare = vi.fn().mockReturnValue({ bind: bindMock });

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.suggested_category_id).toBe(1);
    expect(data.suggested_category_name).toBe('Laptop');
    expect(env.DB.prepare).toHaveBeenCalled();
  });

  it('handles AI output wrapped in markdown code fence with 200', async () => {
    env.AI.run = vi.fn().mockResolvedValue({
      response: '```json\n{\n  "suggested_category_id": 1,\n  "suggested_category_name": "Laptop",\n  "explanation": "Dell Latitude là máy tính xách tay."\n}\n```',
    });

    const runMock = vi.fn().mockResolvedValue({ success: true });
    env.DB.prepare = vi.fn().mockReturnValue({
      bind: vi.fn().mockReturnValue({ run: runMock }),
    });

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.suggested_category_id).toBe(1);
    expect(data.suggested_category_name).toBe('Laptop');
  });

  it('handles AI output with conversational preamble and postscript with 200', async () => {
    env.AI.run = vi.fn().mockResolvedValue({
      response: 'Dưới đây là kết quả phân loại cho bạn:\n{"suggested_category_id": 1, "explanation": "Phù hợp"}\nHy vọng câu trả lời này giúp ích cho bạn!',
    });

    const runMock = vi.fn().mockResolvedValue({ success: true });
    env.DB.prepare = vi.fn().mockReturnValue({
      bind: vi.fn().mockReturnValue({ run: runMock }),
    });

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.suggested_category_id).toBe(1);
  });

  it('handles AI output with ID prefix format like "ID 1" with 200', async () => {
    env.AI.run = vi.fn().mockResolvedValue({
      response: JSON.stringify({
        suggested_category_id: 'ID 1',
        explanation: 'ID prefix format test',
      }),
    });

    const runMock = vi.fn().mockResolvedValue({ success: true });
    env.DB.prepare = vi.fn().mockReturnValue({
      bind: vi.fn().mockReturnValue({ run: runMock }),
    });

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.suggested_category_id).toBe(1);
    expect(data.suggested_category_name).toBe('Laptop');
  });

  it('handles AI output with trailing comma with 200', async () => {
    env.AI.run = vi.fn().mockResolvedValue({
      response: '{\n  "suggested_category_id": 1,\n  "explanation": "Trailing comma test",\n}',
    });

    const runMock = vi.fn().mockResolvedValue({ success: true });
    env.DB.prepare = vi.fn().mockReturnValue({
      bind: vi.fn().mockReturnValue({ run: runMock }),
    });

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.suggested_category_id).toBe(1);
  });

  it('matches category by accent-insensitive name fallback with 200', async () => {
    env.AI.run = vi.fn().mockResolvedValue({
      response: JSON.stringify({
        suggested_category_name: 'Man hinh',
        explanation: 'Màn hình hiển thị không dấu',
      }),
    });

    const runMock = vi.fn().mockResolvedValue({ success: true });
    env.DB.prepare = vi.fn().mockReturnValue({
      bind: vi.fn().mockReturnValue({ run: runMock }),
    });

    const req = new Request('http://localhost/api/suggest-category', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer test-secret-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(samplePayload),
    });

    const res = await worker.fetch(req, env);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.suggested_category_id).toBe(2);
    expect(data.suggested_category_name).toBe('Màn hình');
  });
});
