export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname !== '/api/suggest-category') {
      return new Response(JSON.stringify({ error: 'Not Found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    if (request.method !== 'POST') {
      return new Response(JSON.stringify({ error: 'Method Not Allowed' }), {
        status: 405,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const authHeader = request.headers.get('Authorization') || '';
    const expectedToken = env.AI_SERVICE_TOKEN;
    if (
      !expectedToken ||
      !authHeader.startsWith('Bearer ') ||
      authHeader.slice(7).trim() !== expectedToken.trim()
    ) {
      return new Response(JSON.stringify({ error: 'Unauthorized' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const contentLength = request.headers.get('content-length');
    if (contentLength && parseInt(contentLength, 10) > 16 * 1024) {
      return new Response(JSON.stringify({ error: 'Payload too large' }), {
        status: 413,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    let body;
    try {
      const rawText = await request.text();
      if (rawText.length > 16 * 1024) {
        return new Response(JSON.stringify({ error: 'Payload too large' }), {
          status: 413,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      body = JSON.parse(rawText);
    } catch {
      return new Response(JSON.stringify({ error: 'Invalid JSON' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const { request_id, name, description = '', categories } = body || {};
    if (
      !request_id ||
      !name ||
      typeof name !== 'string' ||
      !Array.isArray(categories) ||
      categories.length === 0
    ) {
      return new Response(JSON.stringify({ error: 'Invalid request body' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const categoryListStr = categories
      .map((c) => `- ID ${c.id}: ${c.name}`)
      .join('\n');

    const systemPrompt = `Bạn là trợ lý phân loại tài sản thiết bị. Hãy chọn danh mục phù hợp nhất từ danh sách sau:\n${categoryListStr}\nChỉ trả về JSON hợp lệ theo đúng cấu trúc: {"suggested_category_id": <id>, "explanation": "<giải thích ngắn gọn>"}`;
    const userPrompt = `Tên tài sản: ${name}\nMô tả: ${description || 'Không có mô tả'}`;

    const model = env.AI_MODEL || '@cf/meta/llama-3-8b-instruct';
    let aiOutput;
    try {
      aiOutput = await env.AI.run(model, {
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userPrompt },
        ],
      });
    } catch (err) {
      return new Response(JSON.stringify({ error: 'AI service unavailable' }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    let suggestedId = null;
    let explanation = '';

    const rawResponse =
      typeof aiOutput === 'string'
        ? aiOutput
        : aiOutput?.response || (typeof aiOutput === 'object' ? JSON.stringify(aiOutput) : '');

    try {
      const jsonMatch = typeof rawResponse === 'string' ? rawResponse.match(/\{[\s\S]*\}/) : null;
      const parsedObj = jsonMatch ? JSON.parse(jsonMatch[0]) : JSON.parse(rawResponse);
      const rawId = parsedObj.suggested_category_id ?? parsedObj.category_id;
      if (rawId !== undefined && rawId !== null) {
        suggestedId = Number(rawId);
      }
      if (parsedObj.explanation) {
        explanation = String(parsedObj.explanation);
      }
    } catch {
      // JSON parsing fallback
    }

    let matchedCategory = categories.find((c) => Number(c.id) === Number(suggestedId));
    if (!matchedCategory) {
      // Fallback matching by category name
      matchedCategory = categories.find(
        (c) =>
          typeof rawResponse === 'string' &&
          rawResponse.toLowerCase().includes(c.name.toLowerCase())
      );
      if (!matchedCategory) {
        matchedCategory = categories[0];
      }
    }

    const finalCategoryId = matchedCategory.id;
    const finalCategoryName = matchedCategory.name;
    const finalExplanation = explanation.slice(0, 500);

    let logSaved = false;
    if (env.DB && typeof env.DB.prepare === 'function') {
      try {
        await env.DB.prepare(
          'INSERT INTO ai_suggestion_logs (id, asset_name, description, suggested_category_id, suggested_category_name, model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)'
        )
          .bind(
            request_id,
            name,
            description || '',
            finalCategoryId,
            finalCategoryName,
            model,
            new Date().toISOString()
          )
          .run();
        logSaved = true;
      } catch (err) {
        console.warn('D1 log error:', err);
        logSaved = false;
      }
    }

    return new Response(
      JSON.stringify({
        request_id,
        suggested_category_id: finalCategoryId,
        suggested_category_name: finalCategoryName,
        explanation: finalExplanation,
        log_saved: logSaved,
      }),
      {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }
    );
  },
};
