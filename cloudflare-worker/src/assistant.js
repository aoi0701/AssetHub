import schema from '../assistant.schema.json' with { type: 'json' };

const MAX_REQUEST_BYTES = 32 * 1024; // 32768 bytes
const MAX_OUTPUT_BYTES = 64 * 1024; // 65536 bytes

const ALLOWED_ROOT_KEYS = ['kind', 'calls', 'clarification', 'reason'];
const ALLOWED_KINDS = ['tools', 'clarify', 'handoff', 'out_of_scope'];
const ALLOWED_TOOLS = [
  'assets.search',
  'assets.summary',
  'assets.history',
  'requests.search',
  'procedures.lookup',
];
const ALLOWED_REASONS = ['missing_knowledge', 'unsupported_asset_question'];

const ALLOWED_ASSET_STATUSES = [
  'AVAILABLE',
  'IN_USE',
  'DAMAGED_PENDING_REPAIR',
  'UNDER_MAINTENANCE',
  'PENDING_DISPOSAL',
  'DISPOSED',
];
const ALLOWED_ACTION_TYPES = [
  'CREATE',
  'ALLOCATE',
  'RETURN',
  'DAMAGE',
  'MAINTENANCE_START',
  'MAINTENANCE_END',
  'TRANSFER',
  'DISPOSAL_PROPOSE',
  'DISPOSAL_CONFIRM',
];
const ALLOWED_REQUEST_TYPES = ['ALLOCATE', 'RETURN', 'DAMAGE_REPORT'];
const ALLOWED_REQUEST_STATUSES = ['PENDING', 'APPROVED', 'REJECTED'];
const ALLOWED_PROCEDURE_TOPICS = ['allocate', 'return', 'damage_report', 'roles', 'asset_statuses'];
const ALLOWED_SUMMARY_GROUP_BYS = ['status', 'category', 'department', null];

const DATE_REGEX = /^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2}))?$/;

const SYSTEM_PROMPT = `Bạn là AI Planner cho hệ thống quản lý tài sản AssetHub.
Nhiệm vụ: Phân tích câu hỏi của người dùng và quyết định hành động tra cứu/phản hồi theo đúng JSON Schema sau:

${JSON.stringify(schema)}

Yêu cầu bắt buộc:
1. Bắt buộc trả về đúng 4 khóa root: "kind", "calls", "clarification", "reason". Không thêm bất kỳ khóa root nào khác.
2. "kind" chỉ được là một trong: "tools", "clarify", "handoff", "out_of_scope".
3. Khi "kind" là "tools", "calls" chứa từ 1 đến 3 công cụ hợp lệ trong danh sách cho phép, "clarification": null, "reason": null.
4. Khi "kind" là "clarify", "calls": [], "clarification": chuỗi hỏi lại thông tin hoặc lựa chọn còn thiếu (1-300 ký tự), TUYỆT ĐỐI KHÔNG lặp lại nguyên văn câu hỏi của người dùng, "reason": null.
5. Khi "kind" là "handoff", "calls": [], "clarification": null, "reason": "missing_knowledge" hoặc "unsupported_asset_question".
6. Khi "kind" là "out_of_scope", "calls": [], "clarification": null, "reason": null.
7. Chỉ trả về DUY NHẤT một JSON object hợp lệ, KHÔNG bọc trong markdown code fence, KHÔNG kèm bất kỳ lời giải thích hay văn bản nào ngoài JSON.

Quy tắc định tuyến công cụ (Tool Routing Guidance):
- Thống kê, đếm số lượng tài sản: CHỈ dùng "assets.summary" khi đối tượng là TÀI SẢN. Đặt loại tài sản vào "category", KHÔNG đặt vào "query".
- Phân biệt Scope và Bộ lọc câu hỏi: Scope là danh tính người hỏi, KHÔNG phải bộ lọc; phòng/người giữ nêu trong câu hỏi phải giữ nguyên vào department/holder kể cả khác phòng, quyền do backend kiểm tra.
- Bộ lọc trạng thái ("statuses"): Chỉ đặt statuses khi câu hỏi có chỉ trạng thái; không nêu thì bỏ statuses. Các từ ngữ "chưa cấp", "chưa được cấp", "chưa cấp phát", "trống", "sẵn sàng" ĐỀU biểu thị khả dụng, BẮT BUỘC đặt statuses: ["AVAILABLE"].
- Tra cứu hoặc đếm phiếu yêu cầu (requests): dùng "requests.search" với "request_type" (ALLOCATE, RETURN, DAMAGE_REPORT), "status" (PENDING, APPROVED, REJECTED) hoặc "query"; tuyệt đối KHÔNG dùng "assets.summary" cho phiếu yêu cầu (ví dụ hỏi "bao nhiêu phiếu").
- Tra cứu danh sách/chi tiết tài sản: dùng "assets.search". Khi có mã tài sản cụ thể, bắt buộc đặt vào "asset_code" và giữ nguyên mã gốc; trường "query" chỉ dùng cho từ khóa tìm kiếm tự do khi không có mã. Khi hỏi theo người giữ, đặt tên vào "holder", không đặt vào "query".
- Lịch sử tài sản: dùng "assets.history" với "asset_code" cho tài sản cụ thể, HOẶC "mine": true khi hỏi lịch sử tài sản của chính mình; KHÔNG kết hợp cả hai selector cùng lúc, KHÔNG tự thêm tham số ngoài schema.
- Quy trình/quy định chính thức: dùng "procedures.lookup" với đúng 1 trong 5 chủ đề hỗ trợ: "allocate", "return", "damage_report", "roles", "asset_statuses". Mọi câu hỏi chính sách/quy định khác nằm ngoài 5 chủ đề này: chọn "kind": "handoff" với "reason": "missing_knowledge".
- Câu hỏi tiếp nối (Follow-up) có đại từ thay thế (ví dụ: "máy đó", "thiết bị này", "nó"):
  * Ưu tiên mã tài sản nếu người dùng nêu rõ trong câu hỏi; không làm hỏng câu hỏi nêu rõ mã cụ thể hoặc câu hỏi về cả hai/tất cả thiết bị.
  * Nếu câu hỏi không nêu mã cụ thể và dùng đại từ số ít mơ hồ: BẮT BUỘC xét refs ở lượt gần nhất liên quan và đếm các mã phân biệt trong lượt đó (lặp lại cùng 1 mã qua nhiều lượt không tính thành nhiều thiết bị).
  * Nếu lượt gần nhất liên quan có từ 2 mã phân biệt trở lên gây mơ hồ: BẮT BUỘC chọn "kind": "clarify" liệt kê chính xác các mã ứng viên từ refs của lượt đó để người dùng chọn; TUYỆT ĐỐI KHÔNG tự tiện chọn 1 mã và KHÔNG tự bịa thêm bộ lọc (như category, query, holder).
  * Nếu lượt gần nhất liên quan có đúng 1 mã phân biệt: dùng mã đó cho "asset_code".
  * Nếu Context không có ref nào: chọn "kind": "clarify" yêu cầu cung cấp mã tài sản.
- Khi làm rõ ("clarify"): trường "clarification" phải là câu hỏi người dùng cần cung cấp mã tài sản hoặc lựa chọn cụ thể nào, TUYỆT ĐỐI KHÔNG lặp lại nguyên văn câu hỏi của người dùng. Minh họa cấu trúc: {"kind":"clarify","calls":[],"clarification":"Bạn muốn hỏi về tài sản nào?","reason":null} (mẫu chỉ minh họa cấu trúc; nội dung hỏi lại phải phù hợp ngữ cảnh và nêu lựa chọn refs nếu cần, không copy cố định).
- Câu hỏi hoàn toàn ngoài phạm vi quản lý tài sản (thời tiết, chào hỏi chung, du lịch, thể thao, xã hội...): chọn "kind": "out_of_scope".`;

function buildUserPrompt(question, context, scope) {
  return `Scope: ${JSON.stringify(scope)}
Context: ${JSON.stringify(context)}
Câu hỏi người dùng: ${question}`;
}

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function isCleanString(val, maxLength) {
  return typeof val === 'string' && val.trim().length > 0 && !val.includes('__') && val.length <= maxLength;
}

function isValidLimit(val) {
  return typeof val === 'number' && Number.isInteger(val) && val >= 1 && val <= 20;
}

function parseIsoDate(val, isUntil = false) {
  if (typeof val !== 'string' || !DATE_REGEX.test(val)) {
    return null;
  }
  if (val.length === 10) {
    const base = new Date(`${val}T00:00:00.000Z`);
    if (Number.isNaN(base.getTime())) return null;
    if (isUntil) {
      return new Date(`${val}T23:59:59.999Z`);
    }
    return base;
  }
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(val)) {
    return null; // Naive datetime not allowed
  }
  const dt = new Date(val);
  if (Number.isNaN(dt.getTime())) return null;
  return dt;
}

function validateToolArgs(toolName, args) {
  if (!args || typeof args !== 'object' || Array.isArray(args)) {
    return false;
  }

  const keys = Object.keys(args);

  if (toolName === 'assets.search') {
    const allowed = ['query', 'asset_code', 'category', 'statuses', 'department', 'holder', 'limit'];
    for (const k of keys) {
      if (!allowed.includes(k)) return false;
    }
    if (Object.prototype.hasOwnProperty.call(args, 'query') && !isCleanString(args.query, 100)) return false;
    if (Object.prototype.hasOwnProperty.call(args, 'asset_code') && !isCleanString(args.asset_code, 50)) return false;
    if (Object.prototype.hasOwnProperty.call(args, 'category') && !isCleanString(args.category, 100)) return false;
    if (Object.prototype.hasOwnProperty.call(args, 'department') && !isCleanString(args.department, 100)) return false;
    if (Object.prototype.hasOwnProperty.call(args, 'holder') && !isCleanString(args.holder, 100)) return false;
    if (Object.prototype.hasOwnProperty.call(args, 'limit') && !isValidLimit(args.limit)) return false;
    if (Object.prototype.hasOwnProperty.call(args, 'statuses')) {
      if (!Array.isArray(args.statuses) || args.statuses.length === 0) return false;
      const seen = new Set();
      for (const st of args.statuses) {
        if (typeof st !== 'string' || !ALLOWED_ASSET_STATUSES.includes(st) || seen.has(st)) return false;
        seen.add(st);
      }
    }
    return true;
  }

  if (toolName === 'assets.summary') {
    const allowed = ['query', 'asset_code', 'category', 'statuses', 'department', 'holder', 'group_by'];
    for (const k of keys) {
      if (!allowed.includes(k)) return false;
    }
    // Summary forbids limit strictly!
    if (Object.prototype.hasOwnProperty.call(args, 'limit')) return false;

    if (Object.prototype.hasOwnProperty.call(args, 'query') && !isCleanString(args.query, 100)) return false;
    if (Object.prototype.hasOwnProperty.call(args, 'asset_code') && !isCleanString(args.asset_code, 50)) return false;
    if (Object.prototype.hasOwnProperty.call(args, 'category') && !isCleanString(args.category, 100)) return false;
    if (Object.prototype.hasOwnProperty.call(args, 'department') && !isCleanString(args.department, 100)) return false;
    if (Object.prototype.hasOwnProperty.call(args, 'holder') && !isCleanString(args.holder, 100)) return false;
    if (Object.prototype.hasOwnProperty.call(args, 'statuses')) {
      if (!Array.isArray(args.statuses) || args.statuses.length === 0) return false;
      const seen = new Set();
      for (const st of args.statuses) {
        if (typeof st !== 'string' || !ALLOWED_ASSET_STATUSES.includes(st) || seen.has(st)) return false;
        seen.add(st);
      }
    }
    if (Object.prototype.hasOwnProperty.call(args, 'group_by')) {
      if (args.group_by !== null && !ALLOWED_SUMMARY_GROUP_BYS.includes(args.group_by)) {
        return false;
      }
    }
    return true;
  }

  if (toolName === 'assets.history') {
    const allowed = ['asset_code', 'mine', 'actions', 'since', 'until', 'limit'];
    for (const k of keys) {
      if (!allowed.includes(k)) return false;
    }

    const hasCodeProp = Object.prototype.hasOwnProperty.call(args, 'asset_code');
    const hasMineProp = Object.prototype.hasOwnProperty.call(args, 'mine');

    // Selector: exactly one selector must be present
    if (hasCodeProp && hasMineProp) {
      return false; // cannot specify both
    }
    if (!hasCodeProp && !hasMineProp) {
      return false; // must specify at least one
    }

    if (hasCodeProp) {
      if (!isCleanString(args.asset_code, 50)) return false;
    }

    if (hasMineProp) {
      if (args.mine !== true) return false; // mine: false, null, or wrong type forbidden
    }

    if (Object.prototype.hasOwnProperty.call(args, 'actions')) {
      if (!Array.isArray(args.actions) || args.actions.length === 0) return false;
      const seen = new Set();
      for (const act of args.actions) {
        if (typeof act !== 'string' || !ALLOWED_ACTION_TYPES.includes(act) || seen.has(act)) return false;
        seen.add(act);
      }
    }

    let sinceDt = null;
    let untilDt = null;

    if (Object.prototype.hasOwnProperty.call(args, 'since')) {
      sinceDt = parseIsoDate(args.since, false);
      if (!sinceDt) return false;
    }

    if (Object.prototype.hasOwnProperty.call(args, 'until')) {
      untilDt = parseIsoDate(args.until, true);
      if (!untilDt) return false;
    }

    if (sinceDt && untilDt && sinceDt > untilDt) {
      return false;
    }

    if (Object.prototype.hasOwnProperty.call(args, 'limit') && !isValidLimit(args.limit)) {
      return false;
    }

    return true;
  }

  if (toolName === 'requests.search') {
    const allowed = ['query', 'request_type', 'status', 'limit'];
    for (const k of keys) {
      if (!allowed.includes(k)) return false;
    }
    if (Object.prototype.hasOwnProperty.call(args, 'query') && !isCleanString(args.query, 100)) return false;
    if (Object.prototype.hasOwnProperty.call(args, 'request_type')) {
      if (typeof args.request_type !== 'string' || !ALLOWED_REQUEST_TYPES.includes(args.request_type)) {
        return false;
      }
    }
    if (Object.prototype.hasOwnProperty.call(args, 'status')) {
      if (typeof args.status !== 'string' || !ALLOWED_REQUEST_STATUSES.includes(args.status)) {
        return false;
      }
    }
    if (Object.prototype.hasOwnProperty.call(args, 'limit') && !isValidLimit(args.limit)) return false;
    return true;
  }

  if (toolName === 'procedures.lookup') {
    const allowed = ['topic'];
    for (const k of keys) {
      if (!allowed.includes(k)) return false;
    }
    if (
      !Object.prototype.hasOwnProperty.call(args, 'topic') ||
      typeof args.topic !== 'string' ||
      !ALLOWED_PROCEDURE_TOPICS.includes(args.topic)
    ) {
      return false;
    }
    return true;
  }

  return false;
}

export function validatePlannerDecision(d) {
  if (!d || typeof d !== 'object' || Array.isArray(d)) {
    return false;
  }

  const keys = Object.keys(d);
  if (keys.length !== 4) {
    return false;
  }
  for (const k of ALLOWED_ROOT_KEYS) {
    if (!Object.prototype.hasOwnProperty.call(d, k)) {
      return false;
    }
  }

  const { kind, calls, clarification, reason } = d;

  if (typeof kind !== 'string' || !ALLOWED_KINDS.includes(kind)) {
    return false;
  }

  if (!Array.isArray(calls)) {
    return false;
  }

  if (kind === 'tools') {
    if (calls.length < 1 || calls.length > 3) {
      return false;
    }
    if (clarification !== null || reason !== null) {
      return false;
    }
    for (const call of calls) {
      if (!call || typeof call !== 'object' || Array.isArray(call)) {
        return false;
      }
      const callKeys = Object.keys(call);
      if (
        callKeys.length !== 2 ||
        !Object.prototype.hasOwnProperty.call(call, 'name') ||
        !Object.prototype.hasOwnProperty.call(call, 'arguments')
      ) {
        return false;
      }
      if (typeof call.name !== 'string' || !ALLOWED_TOOLS.includes(call.name)) {
        return false;
      }
      if (!validateToolArgs(call.name, call.arguments)) {
        return false;
      }
    }
    return true;
  }

  if (kind === 'clarify') {
    if (calls.length !== 0) {
      return false;
    }
    if (typeof clarification !== 'string' || clarification.length < 1 || clarification.length > 300) {
      return false;
    }
    if (reason !== null) {
      return false;
    }
    return true;
  }

  if (kind === 'handoff') {
    if (calls.length !== 0) {
      return false;
    }
    if (clarification !== null) {
      return false;
    }
    if (typeof reason !== 'string' || !ALLOWED_REASONS.includes(reason)) {
      return false;
    }
    return true;
  }

  if (kind === 'out_of_scope') {
    if (calls.length !== 0) {
      return false;
    }
    if (clarification !== null || reason !== null) {
      return false;
    }
    return true;
  }

  return false;
}

async function readCappedBody(request, maxBytes) {
  const contentLength = request.headers.get('content-length');
  if (contentLength && parseInt(contentLength, 10) > maxBytes) {
    return { error: 'Payload too large', status: 413 };
  }
  if (!request.body) {
    return { text: '' };
  }
  const reader = request.body.getReader();
  const chunks = [];
  let totalBytes = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    totalBytes += value.byteLength;
    if (totalBytes > maxBytes) {
      await reader.cancel();
      return { error: 'Payload too large', status: 413 };
    }
    chunks.push(value);
  }
  const allBytes = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    allBytes.set(chunk, offset);
    offset += chunk.byteLength;
  }

  try {
    const text = new TextDecoder('utf-8', { fatal: true }).decode(allBytes);
    return { text };
  } catch {
    return { error: 'Invalid UTF-8 encoding in request body', status: 400 };
  }
}

export async function handleAssistantPlan(request, env) {
  if (request.method !== 'POST') {
    return jsonResponse({ error: 'Method Not Allowed' }, 405);
  }

  const authHeader = request.headers.get('Authorization') || '';
  const expectedToken = env.AI_SERVICE_TOKEN;
  if (
    !expectedToken ||
    typeof expectedToken !== 'string' ||
    !expectedToken.trim() ||
    !authHeader.startsWith('Bearer ') ||
    authHeader.slice(7).trim() !== expectedToken.trim()
  ) {
    return jsonResponse({ error: 'Unauthorized' }, 401);
  }

  const bodyResult = await readCappedBody(request, MAX_REQUEST_BYTES);
  if (bodyResult.error) {
    return jsonResponse({ error: bodyResult.error }, bodyResult.status);
  }

  let body;
  try {
    body = JSON.parse(bodyResult.text);
  } catch {
    return jsonResponse({ error: 'Invalid request body' }, 400);
  }

  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return jsonResponse({ error: 'Invalid request body' }, 400);
  }

  const { question, context = [], scope = {} } = body;
  if (typeof question !== 'string' || !question.trim()) {
    return jsonResponse({ error: 'Invalid request body' }, 400);
  }

  // Spec check: question length <= 1000
  if (question.length > 1000) {
    return jsonResponse({ error: 'Question too long (max 1000 characters)' }, 400);
  }

  if (!Array.isArray(context)) {
    return jsonResponse({ error: 'Invalid request body' }, 400);
  }

  // Spec check: context length <= 6 turns, total characters <= 6000
  if (context.length > 6) {
    return jsonResponse({ error: 'Context too long (max 6 turns)' }, 400);
  }

  let totalContextChars = 0;
  for (const turn of context) {
    if (!turn || typeof turn !== 'object' || Array.isArray(turn)) {
      return jsonResponse({ error: 'Invalid context turn' }, 400);
    }
    const turnKeys = Object.keys(turn);
    for (const k of turnKeys) {
      if (k !== 'question' && k !== 'refs') {
        return jsonResponse({ error: 'Invalid context turn: unexpected field' }, 400);
      }
    }
    if (typeof turn.question !== 'string' || !turn.question.trim()) {
      return jsonResponse({ error: 'Invalid context turn: missing question' }, 400);
    }
    totalContextChars += turn.question.length;
    if (turn.refs !== undefined) {
      if (!Array.isArray(turn.refs)) {
        return jsonResponse({ error: 'Invalid context turn: refs must be array' }, 400);
      }
      for (const ref of turn.refs) {
        if (!ref || typeof ref !== 'object' || Array.isArray(ref)) {
          return jsonResponse({ error: 'Invalid context turn: malformed ref' }, 400);
        }
        const refKeys = Object.keys(ref);
        if (refKeys.length !== 1 || !Object.prototype.hasOwnProperty.call(ref, 'asset_code')) {
          return jsonResponse({ error: 'Invalid context turn: malformed ref' }, 400);
        }
        if (typeof ref.asset_code !== 'string' || !ref.asset_code.trim()) {
          return jsonResponse({ error: 'Invalid context turn: malformed ref' }, 400);
        }
        totalContextChars += ref.asset_code.length;
      }
    }
  }

  if (totalContextChars > 6000) {
    return jsonResponse({ error: 'Context too large (max 6000 characters)' }, 400);
  }

  if (typeof scope !== 'object' || scope === null || Array.isArray(scope)) {
    return jsonResponse({ error: 'Invalid request body' }, 400);
  }

  const model = env.ASSISTANT_MODEL || env.AI_MODEL || '@cf/meta/llama-3.1-8b-instruct-fp8';

  let aiOutput;
  try {
    aiOutput = await env.AI.run(model, {
      messages: [
        { role: 'system', content: SYSTEM_PROMPT },
        { role: 'user', content: buildUserPrompt(question, context, scope) },
      ],
      temperature: 0.1,
      max_tokens: 768,
      response_format: { type: 'json_object' },
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
        /quota/i.test(errStr) ||
        errCode === 3040 ||
        String(errCode).includes('3040') ||
        errStr.includes('3040') ||
        /capacity/i.test(errStr))
    ) {
      return jsonResponse({ error: 'Quota exceeded', code: 3036 }, 429);
    }

    return jsonResponse({ error: 'AI service unavailable', code: 'UNAVAILABLE' }, 503);
  }

  let parsedDecision = null;

  if (
    typeof aiOutput === 'object' &&
    aiOutput !== null &&
    typeof aiOutput.response === 'object' &&
    aiOutput.response !== null
  ) {
    parsedDecision = aiOutput.response;
    try {
      const serialized = JSON.stringify(parsedDecision);
      if (new TextEncoder().encode(serialized).byteLength > MAX_OUTPUT_BYTES) {
        return jsonResponse(
          { error: 'Invalid AI plan', code: 'INVALID_PLAN', reason_code: 'PAYLOAD_TOO_LARGE' },
          502
        );
      }
    } catch {
      return jsonResponse(
        { error: 'Invalid AI plan', code: 'INVALID_PLAN', reason_code: 'SCHEMA_VALIDATION_FAILED' },
        502
      );
    }
  } else {
    let rawResponse = '';
    if (typeof aiOutput === 'string') {
      rawResponse = aiOutput;
    } else if (typeof aiOutput?.response === 'string') {
      rawResponse = aiOutput.response;
    } else if (typeof aiOutput === 'object' && aiOutput !== null) {
      rawResponse = JSON.stringify(aiOutput);
    }

    if (!rawResponse || typeof rawResponse !== 'string') {
      return jsonResponse(
        { error: 'Invalid AI plan', code: 'INVALID_PLAN', reason_code: 'SCHEMA_VALIDATION_FAILED' },
        502
      );
    }

    if (new TextEncoder().encode(rawResponse).byteLength > MAX_OUTPUT_BYTES) {
      return jsonResponse(
        { error: 'Invalid AI plan', code: 'INVALID_PLAN', reason_code: 'PAYLOAD_TOO_LARGE' },
        502
      );
    }

    const text = rawResponse.trim();
    // Task 3: No salvage parsing. Code fence, prose or non-JSON MUST result in 502!
    if (text.startsWith('```') || text.endsWith('```')) {
      return jsonResponse(
        { error: 'Invalid AI plan', code: 'INVALID_PLAN', reason_code: 'CODE_FENCE' },
        502
      );
    }

    try {
      parsedDecision = JSON.parse(text);
    } catch {
      return jsonResponse(
        { error: 'Invalid AI plan', code: 'INVALID_PLAN', reason_code: 'JSON_PARSE_ERROR' },
        502
      );
    }
  }

  if (!validatePlannerDecision(parsedDecision)) {
    return jsonResponse(
      { error: 'Invalid AI plan', code: 'INVALID_PLAN', reason_code: 'SCHEMA_VALIDATION_FAILED' },
      502
    );
  }

  return jsonResponse({ decision: parsedDecision }, 200);
}

export default {
  fetch: handleAssistantPlan,
};
