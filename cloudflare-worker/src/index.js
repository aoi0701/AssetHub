function parsePositiveInteger(val) {
  if (typeof val === 'number') {
    return (Number.isInteger(val) && Number.isFinite(val) && val > 0 && Number.isSafeInteger(val)) ? val : null;
  }
  if (typeof val === 'string') {
    const trimmed = val.trim();
    if (/^\d+$/.test(trimmed)) {
      const num = Number(trimmed);
      return (Number.isSafeInteger(num) && num > 0) ? num : null;
    }
    const match = trimmed.match(/^ID\s*[:#-]?\s*(\d+)$/i);
    if (match) {
      const num = Number(match[1]);
      return (Number.isSafeInteger(num) && num > 0) ? num : null;
    }
  }
  return null;
}

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
      .map((c) => `- ID ${c.id}: "${c.name}"`)
      .join('\n');

    const sampleCat = categories[0] || { id: 1, name: 'Default' };
    const systemPrompt = `Bạn là trợ lý AI chuyên phân loại tài sản thiết bị.
Nhiệm vụ: Chọn đúng 1 danh mục phù hợp nhất từ danh sách sau:
${categoryListStr}

Yêu cầu bắt buộc:
1. "suggested_category_id": Số nguyên ID danh mục phù hợp nhất (ví dụ: ${sampleCat.id}).
2. "suggested_category_name": Tên danh mục tương ứng (ví dụ: "${sampleCat.name}").
3. "explanation": Giải thích ngắn gọn (tối đa 50 từ).
4. Chỉ trả về một JSON object hợp lệ duy nhất, không kèm markdown code block, không thêm lời mở đầu hay kết luận.

Ví dụ định dạng:
{"suggested_category_id": ${sampleCat.id}, "suggested_category_name": "${sampleCat.name}", "explanation": "Giải thích phù hợp"}`;
    const userPrompt = `Tên tài sản: ${name}\nMô tả: ${description || 'Không có mô tả'}`;

    const model = env.AI_MODEL || '@cf/meta/llama-3.1-8b-instruct-fp8';
    let aiOutput;
    try {
      aiOutput = await env.AI.run(model, {
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userPrompt },
        ],
        temperature: 0.1,
        max_tokens: 256,
      });
    } catch (err) {
      const errStr = `${String(err)} ${err?.message || ''}`;
      const errCode = err && typeof err === 'object' ? err.code : undefined;
      const is10016 = errCode === 10016 || String(errCode) === '10016' || errStr.includes('10016');

      if (
        !is10016 &&
        (errCode === 3036 ||
          String(errCode).includes('3036') ||
          errStr.includes('3036') ||
          /quota/i.test(errStr))
      ) {
        return new Response(JSON.stringify({ error: 'Quota exceeded', code: 3036 }), {
          status: 429,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      if (
        errCode === 3040 ||
        String(errCode).includes('3040') ||
        errStr.includes('3040') ||
        /capacity/i.test(errStr)
      ) {
        return new Response(JSON.stringify({ error: 'Capacity constrained', code: 3040 }), {
          status: 429,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      return new Response(
        JSON.stringify({ error: 'AI service unavailable', code: 'UNAVAILABLE' }),
        {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        }
      );
    }

    let suggestedId = null;
    let explanation = '';
    let parsedObj = null;

    if (
      typeof aiOutput === 'object' &&
      aiOutput !== null &&
      typeof aiOutput.response === 'object' &&
      aiOutput.response !== null
    ) {
      parsedObj = aiOutput.response;
    } else {
      let rawResponse =
        typeof aiOutput === 'string'
          ? aiOutput
          : typeof aiOutput?.response === 'string'
            ? aiOutput.response
            : typeof aiOutput === 'object'
              ? JSON.stringify(aiOutput)
              : '';

      let text = rawResponse.trim();
      // 1. Strip markdown code fence ```json ... ``` or ``` ... ```
      text = text.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/i, '').trim();

      // 2. Direct JSON parse
      try {
        parsedObj = JSON.parse(text);
      } catch {
        // 3. Extract outermost JSON object
        const firstBrace = text.indexOf('{');
        const lastBrace = text.lastIndexOf('}');
        if (firstBrace !== -1 && lastBrace !== -1 && lastBrace > firstBrace) {
          const candidate = text.slice(firstBrace, lastBrace + 1);
          try {
            parsedObj = JSON.parse(candidate);
          } catch {
            // 4. Strip trailing commas
            try {
              const sanitized = candidate.replace(/,\s*([\}\]])/g, '$1');
              parsedObj = JSON.parse(sanitized);
            } catch {
              // Extraction failed
            }
          }
        }
      }
    }

    if (parsedObj && typeof parsedObj === 'object') {
      const rawId =
        parsedObj?.suggested_category_id ??
        parsedObj?.category_id ??
        parsedObj?.id ??
        parsedObj?.category?.id ??
        parsedObj?.suggested_category?.id;
      suggestedId = parsePositiveInteger(rawId);
      if (parsedObj?.explanation) {
        explanation = String(parsedObj.explanation);
      }
    }

    let matchedCategory = null;
    if (suggestedId !== null) {
      matchedCategory = categories.find((c) => Number(c.id) === suggestedId);
    }
    if (!matchedCategory && parsedObj && typeof parsedObj === 'object') {
      const catNameProp =
        parsedObj.suggested_category_name ??
        parsedObj.category_name ??
        parsedObj.suggested_category ??
        parsedObj.category ??
        parsedObj.name ??
        parsedObj.category?.name ??
        (typeof parsedObj.suggested_category_id === 'string' &&
        !/^\d+$/.test(parsedObj.suggested_category_id.trim()) &&
        !/^ID\s*[:#-]?\s*\d+$/i.test(parsedObj.suggested_category_id.trim())
          ? parsedObj.suggested_category_id
          : null);
      if (
        catNameProp !== undefined &&
        catNameProp !== null &&
        typeof catNameProp === 'string' &&
        catNameProp.trim() !== ''
      ) {
        const cleanProp = catNameProp.trim().toLowerCase();
        matchedCategory = categories.find(
          (c) =>
            typeof c.name === 'string' &&
            c.name.trim().toLowerCase() === cleanProp
        );
        if (!matchedCategory) {
          matchedCategory = categories.find(
            (c) =>
              typeof c.name === 'string' &&
              c.name.trim().toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/đ/g, 'd').replace(/Đ/g, 'D') ===
              cleanProp.normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/đ/g, 'd').replace(/Đ/g, 'D')
          );
        }
      }
    }

    if (!matchedCategory) {
      return new Response(
        JSON.stringify({
          error: 'Invalid AI suggestion: category not in list',
          code: 'INVALID_SUGGESTION',
        }),
        {
          status: 502,
          headers: { 'Content-Type': 'application/json' },
        }
      );
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
