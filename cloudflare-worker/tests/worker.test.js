import { describe, it, expect, vi } from 'vitest';
import worker from '../src/index.js';

describe('Cloudflare AI Worker', () => {
  const env = {
    AI_SERVICE_TOKEN: 'test-secret-token',
    AI_MODEL: '@cf/meta/llama-3-8b-instruct',
    AI: {
      run: vi.fn(),
    },
    DB: {
      prepare: vi.fn(),
    },
  };

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

    expect(env.DB.prepare).toHaveBeenCalledWith(
      expect.stringContaining('INSERT INTO ai_suggestion_logs')
    );
    expect(bindMock).toHaveBeenCalledWith(
      'req-12345',
      samplePayload.name,
      samplePayload.description,
      1,
      'Laptop',
      '@cf/meta/llama-3-8b-instruct',
      expect.any(String)
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

  it('returns 503 when AI service fails', async () => {
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
    expect(data.error).toBe('AI service unavailable');
  });
});
