import React from 'react';
import { act, render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

import { AuthProvider, useAuth } from '../context/AuthContext';
import AssetAssistantPage from '../pages/AssetAssistantPage';
import AssistantSupportPage from '../pages/AssistantSupportPage';
import * as assistantApi from '../api/assistantApi';
import { ticketDetail, ticketListItem, ticketNested } from './assistantDto';

const CONVERSATION_ID = '11111111-1111-4111-8111-111111111111';
const API = '/api/assistant';

const page = (results) => ({
  count: results.length, total_pages: 1, current_page: 1, page_size: 15, next: null, previous: null, results,
});

const paged = (all, request, size = 15) => {
  const current = Number(new URL(request.url).searchParams.get('page') || 1);
  const totalPages = Math.max(1, Math.ceil(all.length / size));
  return {
    count: all.length,
    total_pages: totalPages,
    current_page: current,
    page_size: size,
    next: current < totalPages ? `?page=${current + 1}` : null,
    previous: current > 1 ? `?page=${current - 1}` : null,
    results: all.slice((current - 1) * size, current * size),
  };
};

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

const aggregateSource = {
  id: 'aggregate:assets.summary:abc',
  kind: 'aggregate',
  label: 'Thống kê laptop phòng IT',
  href: '/assets',
  observed_at: '2026-11-05T03:00:00Z',
  scope_label: 'Phòng ban: IT',
  facts: { filters: { category: 'Laptop', statuses: ['AVAILABLE'] }, total: 23, secret_extra: 'KHONG-HIEN-THI' },
};

let turnCounter = 0;
const makeTurn = (overrides = {}) => ({
  id: `turn-${++turnCounter}`,
  conversation_id: CONVERSATION_ID,
  client_request_id: 'req',
  question: 'IT còn mấy laptop trống?',
  outcome: 'ANSWERED',
  answer: 'Phòng IT hiện còn 23 laptop sẵn sàng.',
  sources: [aggregateSource],
  created_at: '2026-11-05T03:00:00Z',
  updated_at: '2026-11-05T03:00:00Z',
  ticket: null,
  ...overrides,
});

const openDetail = ticketDetail({ turn_id: 'turn-rt', question_snapshot: 'Bao giờ có máy?' });
const openTicket = ticketNested(openDetail);

let conversations;
let storedTurns;
let posts;

const server = setupServer(
  http.get(`${API}/conversations/`, () => HttpResponse.json(page(conversations))),
  http.post(`${API}/conversations/`, () => {
    const created = { id: CONVERSATION_ID, created_at: '2026-11-05T02:59:00Z' };
    conversations = [created];
    return HttpResponse.json(created, { status: 201 });
  }),
  http.get(`${API}/conversations/:id/turns/`, () => HttpResponse.json(page(storedTurns))),
);

const answerPosts = (respond) => {
  server.use(
    http.post(`${API}/conversations/:id/turns/`, async ({ request, params }) => {
      const body = await request.json();
      posts.push({ ...body, conversationId: params.id });
      return respond(body, posts.length);
    }),
  );
};

const answerWithTurn = (overrides = {}) => answerPosts((body) => HttpResponse.json(
  makeTurn({ client_request_id: body.client_request_id, question: body.question, ...overrides }),
  { status: 201 },
));

const renderAssistant = (role = 'USER', props = {}) => {
  render(
    <MemoryRouter>
      <AuthProvider initialUser={{ id: 3, username: 'tester', role }}>
        <AssetAssistantPage pollIntervalMs={10} {...props} />
      </AuthProvider>
    </MemoryRouter>,
  );
  return { user: userEvent.setup() };
};

let auth;
const CaptureAuth = () => {
  auth = useAuth();
  return null;
};

const renderWithSession = (initialUser) => {
  render(
    <MemoryRouter>
      <AuthProvider initialUser={initialUser}>
        <CaptureAuth />
        <AssetAssistantPage pollIntervalMs={10} />
      </AuthProvider>
    </MemoryRouter>,
  );
  return { user: userEvent.setup() };
};

const manager = (overrides = {}) => ({ id: 3, username: 'tester', role: 'MANAGER', department_id: 1, ...overrides });
const conversation = (id, created_at = '2026-11-05T02:59:00Z') => ({ id, created_at });
const flush = () => act(() => new Promise((done) => setTimeout(done, 50)));

const ask = async (user, text) => {
  await user.type(await screen.findByLabelText('Câu hỏi của bạn'), text);
  await user.click(screen.getByRole('button', { name: 'Gửi' }));
};

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  conversations = [];
  storedTurns = [];
  posts = [];
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('AssetAssistantPage', () => {
  it('asks_in_vietnamese_and_shows_scoped_sources', async () => {
    answerWithTurn();
    const { user } = renderAssistant('MANAGER');

    expect(screen.getByRole('heading', { name: 'Hỏi đáp tài sản' })).toBeVisible();
    await ask(user, 'IT còn mấy laptop trống?');

    expect(await screen.findByText('Phòng IT hiện còn 23 laptop sẵn sàng.')).toBeVisible();
    const chip = screen.getByRole('button', { name: 'Thống kê laptop phòng IT' });
    await user.click(chip);

    const panel = screen.getByRole('region', { name: 'Căn cứ: Thống kê laptop phòng IT' });
    expect(within(panel).getByText('Phòng ban: IT')).toBeVisible();
    expect(within(panel).getByText('23')).toBeVisible();
    expect(within(panel).getByText(/Laptop/)).toBeVisible();
    // 03:00 UTC is 10:00 in Asia/Ho_Chi_Minh.
    expect(within(panel).getByText(/10:00/)).toBeVisible();
    expect(within(panel).getByRole('link', { name: 'Mở trang liên quan' })).toHaveAttribute('href', '/assets');
    expect(screen.queryByText('KHONG-HIEN-THI')).not.toBeInTheDocument();
    expect(posts[0].client_request_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(posts[0].question).toBe('IT còn mấy laptop trống?');
  });

  it('ignores_links_outside_the_internal_allowlist', async () => {
    answerWithTurn({
      sources: [
        { ...aggregateSource, id: 'a', label: 'Nguồn lạ', href: 'https://evil.example/x' },
        { ...aggregateSource, id: 'b', label: 'Nguồn lạ 2', href: '//evil.example' },
      ],
    });
    const { user } = renderAssistant('MANAGER');
    await ask(user, 'Câu hỏi');

    await user.click(await screen.findByRole('button', { name: 'Nguồn lạ' }));
    await user.click(screen.getByRole('button', { name: 'Nguồn lạ 2' }));
    expect(screen.queryByRole('link', { name: 'Mở trang liên quan' })).not.toBeInTheDocument();
  });

  it('opens_procedure_source_by_validated_topic', async () => {
    let fetched = 0;
    server.use(http.get(`${API}/procedures/:topic/`, ({ params }) => {
      fetched += 1;
      return HttpResponse.json({
        topic: params.topic, version: '2026-11-05', title: 'Quy trình trả', body: 'Bước một\nBước hai',
        actions: [{ label: 'Mở Phiếu của tôi', href: '/my-requests' }],
      });
    }));
    answerWithTurn({
      sources: [
        { ...aggregateSource, id: 'p', kind: 'procedure', label: 'Hướng dẫn trả', href: '/my-requests',
          facts: { topic: 'return', version: '2026-11-05', title: 'Quy trình trả', body: 'Bước một\nBước hai' } },
        { ...aggregateSource, id: 'q', kind: 'procedure', label: 'Chủ đề lạ', href: null,
          facts: { topic: '../../secret', version: '1', title: 'x', body: 'Nội dung đã lưu' } },
      ],
    });
    const { user } = renderAssistant();
    await ask(user, 'Trả tài sản thế nào?');

    await user.click(await screen.findByRole('button', { name: 'Hướng dẫn trả' }));
    expect(await screen.findByText(/Bước một/)).toBeVisible();
    expect(screen.getAllByText(/2026-11-05/).length).toBeGreaterThan(0);
    expect(screen.getByRole('link', { name: 'Mở Phiếu của tôi' })).toHaveAttribute('href', '/my-requests');

    await user.click(screen.getByRole('button', { name: 'Chủ đề lạ' }));
    expect(await screen.findByText('Nội dung đã lưu')).toBeVisible();
    expect(fetched).toBe(1);
  });

  it('clarifies_then_uses_same_conversation', async () => {
    answerPosts((body, count) => HttpResponse.json(makeTurn({
      client_request_id: body.client_request_id,
      question: body.question,
      outcome: count === 1 ? 'CLARIFY' : 'ANSWERED',
      answer: count === 1 ? 'Bạn muốn hỏi tài sản nào?' : 'Tài sản AST-1 đang có sẵn.',
      sources: [],
    }), { status: 201 }));
    const { user } = renderAssistant();

    await ask(user, 'Máy in');
    expect(await screen.findByText('Bạn muốn hỏi tài sản nào?')).toBeVisible();
    await ask(user, 'AST-1');
    expect(await screen.findByText('Tài sản AST-1 đang có sẵn.')).toBeVisible();

    expect(posts).toHaveLength(2);
    expect(posts[0].conversationId).toBe(posts[1].conversationId);
    expect(posts[0].client_request_id).not.toBe(posts[1].client_request_id);
  });

  it('network_retry_reuses_request_id', async () => {
    answerPosts((body, count) => (count === 1
      ? HttpResponse.error()
      : HttpResponse.json(makeTurn({ client_request_id: body.client_request_id, question: body.question }), { status: 201 })));
    const { user } = renderAssistant();

    await ask(user, 'Câu mất mạng');
    const retry = await screen.findByRole('button', { name: 'Gửi lại' });
    expect(screen.getByRole('button', { name: 'Gửi' })).toBeDisabled();
    await user.click(retry);

    expect(await screen.findByText('Phòng IT hiện còn 23 laptop sẵn sàng.')).toBeVisible();
    expect(posts).toHaveLength(2);
    expect(posts[1].client_request_id).toBe(posts[0].client_request_id);
    expect(posts[1].question).toBe('Câu mất mạng');
    expect(screen.queryByRole('button', { name: 'Gửi lại' })).not.toBeInTheDocument();
  });

  it('network_failure_after_commit_recovers_saved_turn_without_second_post', async () => {
    answerPosts((body) => {
      storedTurns = [makeTurn({ client_request_id: body.client_request_id, question: body.question })];
      return HttpResponse.error();
    });
    const { user } = renderAssistant();

    await ask(user, 'Câu đã lưu');

    expect(await screen.findByText('Phòng IT hiện còn 23 laptop sẵn sàng.')).toBeVisible();
    expect(posts).toHaveLength(1);
    expect(screen.queryByRole('button', { name: 'Gửi lại' })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText('Câu hỏi của bạn'), 'Câu tiếp theo');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Gửi' })).toBeEnabled());
  });

  it('processing_blocks_second_send_and_restores_from_get', async () => {
    let polls = 0;
    let finished = false;
    const turnId = 'turn-processing';
    server.use(http.get(`${API}/turns/:id/`, () => {
      polls += 1;
      return HttpResponse.json(makeTurn({
        id: turnId,
        outcome: finished ? 'ANSWERED' : 'PROCESSING',
        answer: finished ? 'Đã có kết quả sau khi tra cứu.' : '',
      }));
    }));
    answerPosts((body) => HttpResponse.json(
      makeTurn({ id: turnId, client_request_id: body.client_request_id, question: body.question, outcome: 'PROCESSING', answer: '', sources: [] }),
      { status: 202 },
    ));
    const { user } = renderAssistant();

    await ask(user, 'Câu dài');
    expect(await screen.findByText('Đang tra cứu…')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Gửi' })).toBeDisabled();
    await user.type(screen.getByLabelText('Câu hỏi của bạn'), 'Câu thứ hai{Enter}');
    expect(posts).toHaveLength(1);
    await waitFor(() => expect(polls).toBeGreaterThan(1));
    expect(screen.getByRole('button', { name: 'Gửi' })).toBeDisabled();

    finished = true;
    expect(await screen.findByText('Đã có kết quả sau khi tra cứu.')).toBeVisible();
    expect(screen.getByLabelText('Câu hỏi của bạn')).toHaveValue('Câu thứ hai');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Gửi' })).toBeEnabled());
    expect(posts).toHaveLength(1);
  });

  it('refresh_restores_processing_turn_from_backend_and_locks_send', async () => {
    conversations = [{ id: CONVERSATION_ID, created_at: '2026-11-05T02:59:00Z' }];
    storedTurns = [makeTurn({ id: 'turn-old', outcome: 'PROCESSING', answer: '', sources: [] })];
    server.use(http.get(`${API}/turns/:id/`, () => HttpResponse.json(makeTurn({ id: 'turn-old', answer: 'Kết quả mới.' }))));
    const { user } = renderAssistant();

    expect(await screen.findByText('Đang tra cứu…')).toBeVisible();
    expect(await screen.findByText('Kết quả mới.')).toBeVisible();
    await user.type(screen.getByLabelText('Câu hỏi của bạn'), 'Câu tiếp theo');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Gửi' })).toBeEnabled());
  });

  it('polling_failure_offers_manual_check_without_error_detail', async () => {
    let fail = true;
    server.use(http.get(`${API}/turns/:id/`, () => (fail
      ? HttpResponse.json({ detail: 'provider exploded: secret-host' }, { status: 500 })
      : HttpResponse.json(makeTurn({ id: 'turn-p', answer: 'Kết quả sau khi thử lại.' })))));
    answerPosts((body) => HttpResponse.json(
      makeTurn({ id: 'turn-p', client_request_id: body.client_request_id, question: body.question, outcome: 'PROCESSING', answer: '', sources: [] }),
      { status: 202 },
    ));
    const { user } = renderAssistant();

    await ask(user, 'Câu');
    const check = await screen.findByRole('button', { name: 'Kiểm tra lại' });
    expect(screen.queryByText(/secret-host/)).not.toBeInTheDocument();

    fail = false;
    await user.click(check);
    expect(await screen.findByText('Kết quả sau khi thử lại.')).toBeVisible();
  });

  it('scope_change_discards_old_thread', async () => {
    conversations = [{ id: CONVERSATION_ID, created_at: '2026-11-05T02:59:00Z' }];
    storedTurns = [makeTurn({ answer: 'Số liệu cũ của phòng IT.' })];
    answerPosts(() => HttpResponse.json({ code: 'CONVERSATION_SCOPE_CHANGED' }, { status: 409 }));
    const { user } = renderAssistant();

    expect(await screen.findByText('Số liệu cũ của phòng IT.')).toBeVisible();
    await ask(user, 'Câu tiếp theo');

    const create = await screen.findByRole('button', { name: 'Tạo hội thoại mới' });
    expect(screen.queryByText('Số liệu cũ của phòng IT.')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Gửi' })).toBeDisabled();

    storedTurns = [];
    await user.click(create);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Tạo hội thoại mới' })).not.toBeInTheDocument());
    expect(screen.getByLabelText('Câu hỏi của bạn')).toHaveValue('Câu tiếp theo');
  });

  it('scope_change_while_loading_never_renders_old_thread', async () => {
    conversations = [{ id: CONVERSATION_ID, created_at: '2026-11-05T02:59:00Z' }];
    server.use(http.get(`${API}/conversations/:id/turns/`, () => HttpResponse.json(
      { code: 'CONVERSATION_SCOPE_CHANGED' }, { status: 409 },
    )));
    const { user } = renderAssistant();

    const create = await screen.findByRole('button', { name: 'Tạo hội thoại mới' });
    await waitFor(() => expect(create).toBeEnabled());
    server.use(http.get(`${API}/conversations/:id/turns/`, () => HttpResponse.json(page([]))));
    await user.click(create);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Tạo hội thoại mới' })).not.toBeInTheDocument());
    await user.type(screen.getByLabelText('Câu hỏi của bạn'), 'Câu mới');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Gửi' })).toBeEnabled());
  });

  it('renders_text_and_respects_ime', async () => {
    const hostile = '<img src=x onerror="window.__pwned=1">';
    answerPosts((body) => HttpResponse.json(makeTurn({
      client_request_id: body.client_request_id, question: body.question, answer: hostile, sources: [],
    }), { status: 201 }));
    const { user } = renderAssistant();

    const input = await screen.findByLabelText('Câu hỏi của bạn');
    await user.type(input, 'viet');
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true });
    expect(posts).toHaveLength(0);
    fireEvent.compositionEnd(input);

    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
    expect(posts).toHaveLength(0);

    fireEvent.keyDown(input, { key: 'Enter' });
    expect(await screen.findByText(hostile)).toBeVisible();
    expect(document.querySelector('img[src="x"]')).toBeNull();
    expect(window.__pwned).toBeUndefined();
    expect(posts).toHaveLength(1);
  });

  it('shows_provider_failure_turn_without_raw_error_and_offers_support', async () => {
    answerPosts((body) => HttpResponse.json({
      code: 'ASSISTANT_UNAVAILABLE',
      detail: 'raw-provider-trace',
      turn: makeTurn({ client_request_id: body.client_request_id, question: body.question, outcome: 'ERROR', answer: '', sources: [] }),
    }, { status: 503 }));
    const { user } = renderAssistant();

    await ask(user, 'Câu');

    expect(await screen.findByRole('button', { name: 'Gửi yêu cầu hỗ trợ' })).toBeVisible();
    expect(screen.queryByText(/raw-provider-trace/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Đã chuyển/)).not.toBeInTheDocument();
    expect(posts).toHaveLength(1);
    expect(screen.queryByRole('button', { name: 'Gửi lại' })).not.toBeInTheDocument();
  });

  it('shows_support_only_after_persisted_ticket', async () => {
    let escalateStatus = 503;
    server.use(http.post(`${API}/turns/:id/escalate/`, () => (escalateStatus === 503
      ? HttpResponse.json({ code: 'ASSISTANT_UNAVAILABLE' }, { status: 503 })
      : HttpResponse.json(openDetail, { status: 201 }))));
    answerWithTurn({ outcome: 'ERROR', answer: '', sources: [] });
    const { user } = renderAssistant();

    await ask(user, 'Câu');
    await user.click(await screen.findByRole('button', { name: 'Gửi yêu cầu hỗ trợ' }));
    expect(await screen.findByText(/Chưa chuyển được yêu cầu hỗ trợ/)).toBeVisible();
    expect(screen.queryByText(/Đã chuyển đến/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Kiểm tra phản hồi' })).not.toBeInTheDocument();

    escalateStatus = 201;
    await user.click(screen.getByRole('button', { name: 'Gửi yêu cầu hỗ trợ' }));
    expect(await screen.findByText(/Đã chuyển đến Phòng IT/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Kiểm tra phản hồi' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Gửi yêu cầu hỗ trợ' })).not.toBeInTheDocument();
  });

  it('escalated_outcome_without_ticket_does_not_claim_transfer', async () => {
    answerWithTurn({ outcome: 'ESCALATED', answer: 'Câu này cần người phụ trách.', sources: [], ticket: null });
    const { user } = renderAssistant();

    await ask(user, 'Câu');

    expect(await screen.findByRole('button', { name: 'Gửi yêu cầu hỗ trợ' })).toBeVisible();
    expect(screen.queryByText(/Đã chuyển đến/)).not.toBeInTheDocument();
  });

  it('check_reply_shows_staff_reply_with_separate_label_and_time', async () => {
    server.use(http.get(`${API}/turns/:id/`, ({ params }) => HttpResponse.json(makeTurn({
      id: params.id,
      outcome: 'ESCALATED',
      answer: 'Đã chuyển câu hỏi.',
      sources: [],
      ticket: {
        ...openTicket, status: 'ANSWERED', version: 2, reply: 'Phòng IT sẽ cấp máy vào thứ Hai.',
        replied_by_name: 'Quản lý IT', replied_at: '2026-11-05T17:30:00Z',
      },
    }))));
    answerWithTurn({ outcome: 'ESCALATED', answer: 'Đã chuyển câu hỏi.', sources: [], ticket: openTicket });
    const { user } = renderAssistant();

    await ask(user, 'Câu');
    await user.click(await screen.findByRole('button', { name: 'Kiểm tra phản hồi' }));

    const reply = await screen.findByRole('region', { name: 'Phản hồi người phụ trách' });
    expect(within(reply).getByText('Phòng IT sẽ cấp máy vào thứ Hai.')).toBeVisible();
    expect(within(reply).getByText(/Quản lý IT/)).toBeVisible();
    // 17:30 UTC on Nov 5 is 00:30 on Nov 6 in Asia/Ho_Chi_Minh.
    expect(within(reply).getByText(/00:30/)).toBeVisible();
    expect(within(reply).getByText(/06\/11/)).toBeVisible();
  });

  it('provider_failure_saves_error_turn_and_next_question_gets_a_new_request_id', async () => {
    answerPosts((body, count) => (count === 1
      ? HttpResponse.json({
        code: 'ASSISTANT_UNAVAILABLE',
        turn: makeTurn({
          client_request_id: body.client_request_id, question: body.question, outcome: 'ERROR',
          answer: 'Dịch vụ trợ lý tạm thời không khả dụng. Vui lòng thử lại sau.', sources: [],
        }),
      }, { status: 503 })
      : HttpResponse.json(makeTurn({ client_request_id: body.client_request_id, question: body.question }), { status: 201 })));
    const { user } = renderAssistant();

    await ask(user, 'Câu lỗi');
    expect(await screen.findByText(/tạm thời không khả dụng/)).toBeVisible();
    expect(screen.queryByText(/Đã chuyển đến/)).not.toBeInTheDocument();

    await ask(user, 'Câu lỗi');
    expect(await screen.findByText('Phòng IT hiện còn 23 laptop sẵn sàng.')).toBeVisible();
    expect(posts[1].client_request_id).not.toBe(posts[0].client_request_id);
    expect(posts[1].conversationId).toBe(posts[0].conversationId);
  });

  it('replay_202_after_lost_response_keeps_polling_until_the_answer', async () => {
    conversations = [{ id: CONVERSATION_ID, created_at: '2026-11-05T02:59:00Z' }];
    let lookups = 0;
    server.use(
      http.get(`${API}/conversations/:id/turns/`, () => {
        lookups += 1;
        return lookups === 1 ? HttpResponse.json(page([])) : HttpResponse.error();
      }),
      http.get(`${API}/turns/:id/`, () => HttpResponse.json(makeTurn({ id: 'turn-replay', answer: 'Trả lời sau khi phát lại.', sources: [] }))),
    );
    answerPosts((body, count) => (count === 1
      ? HttpResponse.error()
      : HttpResponse.json(
        makeTurn({ id: 'turn-replay', client_request_id: body.client_request_id, question: body.question, outcome: 'PROCESSING', answer: '', sources: [] }),
        { status: 202 },
      )));
    const { user } = renderAssistant();
    await waitFor(() => expect(lookups).toBe(1));

    await ask(user, 'Câu đang xử lý');
    await user.click(await screen.findByRole('button', { name: 'Gửi lại' }));

    expect(await screen.findByText('Trả lời sau khi phát lại.')).toBeVisible();
    expect(posts[1].client_request_id).toBe(posts[0].client_request_id);
  });

  it('pending_expired_to_error_unlocks_send_and_offers_support', async () => {
    conversations = [{ id: CONVERSATION_ID, created_at: '2026-11-05T02:59:00Z' }];
    storedTurns = [makeTurn({ id: 'turn-old', outcome: 'PROCESSING', answer: '', sources: [] })];
    server.use(http.get(`${API}/turns/:id/`, () => HttpResponse.json(makeTurn({
      id: 'turn-old', outcome: 'ERROR', answer: 'Yêu cầu đã quá thời gian xử lý.', sources: [],
    }))));
    const { user } = renderAssistant();

    expect(await screen.findByText('Yêu cầu đã quá thời gian xử lý.')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Gửi yêu cầu hỗ trợ' })).toBeVisible();
    expect(screen.queryByText('Đang tra cứu…')).not.toBeInTheDocument();
    await user.type(screen.getByLabelText('Câu hỏi của bạn'), 'Hỏi lại');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Gửi' })).toBeEnabled());
  });

  it('disabled_assistant_keeps_question_and_request_id_without_a_lookup', async () => {
    conversations = [{ id: CONVERSATION_ID, created_at: '2026-11-05T02:59:00Z' }];
    let lookups = 0;
    server.use(http.get(`${API}/conversations/:id/turns/`, () => {
      lookups += 1;
      return HttpResponse.json(page([]));
    }));
    answerPosts((body, count) => (count === 1
      ? HttpResponse.json({ code: 'ASSISTANT_DISABLED', detail: 'raw-disabled' }, { status: 503 })
      : HttpResponse.json(makeTurn({ client_request_id: body.client_request_id, question: body.question }), { status: 201 })));
    const { user } = renderAssistant();
    await waitFor(() => expect(lookups).toBe(1));

    await ask(user, 'Câu khi đang tắt');

    expect(await screen.findByText(/Trợ lý đang tạm tắt/)).toBeVisible();
    expect(screen.queryByText(/raw-disabled/)).not.toBeInTheDocument();
    expect(lookups).toBe(1);

    await user.click(screen.getByRole('button', { name: 'Gửi lại' }));
    expect(await screen.findByText('Phòng IT hiện còn 23 laptop sẵn sàng.')).toBeVisible();
    expect(posts[1].client_request_id).toBe(posts[0].client_request_id);
  });

  it.each([
    ['polling', { outcome: 'PROCESSING', answer: '' }, null],
    ['check_reply', { outcome: 'ESCALATED', answer: 'Nội dung cũ.', ticket: openTicket }, 'Kiểm tra phản hồi'],
    ['escalate', { outcome: 'ERROR', answer: 'Nội dung cũ.' }, 'Gửi yêu cầu hỗ trợ'],
  ])('scope_change_during_%s_discards_old_thread', async (_flow, turnFields, button) => {
    conversations = [{ id: CONVERSATION_ID, created_at: '2026-11-05T02:59:00Z' }];
    storedTurns = [makeTurn({ id: 'turn-s', question: 'Câu hỏi cũ riêng tư', sources: [], ...turnFields })];
    const conflict = () => HttpResponse.json(
      { code: 'CONVERSATION_SCOPE_CHANGED', detail: 'raw-scope' }, { status: 409 },
    );
    server.use(http.get(`${API}/turns/:id/`, conflict), http.post(`${API}/turns/:id/escalate/`, conflict));
    const { user } = renderAssistant();

    if (button) await user.click(await screen.findByRole('button', { name: button }));

    expect(await screen.findByRole('button', { name: 'Tạo hội thoại mới' })).toBeEnabled();
    expect(screen.queryByText('Câu hỏi cũ riêng tư')).not.toBeInTheDocument();
    expect(screen.queryByText(/raw-scope/)).not.toBeInTheDocument();
  });

  it('ticket_routed_to_admin_has_no_department_name', async () => {
    answerWithTurn({
      outcome: 'ESCALATED', answer: 'Đã chuyển câu hỏi.', sources: [],
      ticket: { ...openTicket, target_kind: 'ADMIN', target_department_name: null },
    });
    const { user } = renderAssistant();

    await ask(user, 'Câu');

    expect(await screen.findByText(/Đã chuyển đến quản trị viên/)).toBeVisible();
  });

  it('role_examples_fill_the_composer_without_sending', async () => {
    const { user } = renderAssistant('USER');
    await user.click(await screen.findByRole('button', { name: 'Tôi đang giữ tài sản nào?' }));
    expect(screen.getByLabelText('Câu hỏi của bạn')).toHaveValue('Tôi đang giữ tài sản nào?');
    expect(posts).toHaveLength(0);
  });

  it('rejected_question_restores_draft_with_fixed_message', async () => {
    answerPosts(() => HttpResponse.json({ detail: 'raw-limit-info' }, { status: 429 }));
    const { user } = renderAssistant();

    await ask(user, 'Câu bị giới hạn');

    expect(await screen.findByText(/gửi quá nhiều câu hỏi/)).toBeVisible();
    expect(screen.queryByText(/raw-limit-info/)).not.toBeInTheDocument();
    expect(screen.getByLabelText('Câu hỏi của bạn')).toHaveValue('Câu bị giới hạn');
    expect(screen.queryByRole('button', { name: 'Gửi lại' })).not.toBeInTheDocument();
  });

  it('switching_conversation_loads_only_that_thread', async () => {
    const otherId = '22222222-2222-4222-8222-222222222222';
    conversations = [
      { id: CONVERSATION_ID, created_at: '2026-11-05T02:59:00Z' },
      { id: otherId, created_at: '2026-11-04T02:59:00Z' },
    ];
    server.use(http.get(`${API}/conversations/:id/turns/`, ({ params }) => HttpResponse.json(page([
      makeTurn({ answer: params.id === otherId ? 'Nội dung hội thoại cũ.' : 'Nội dung hội thoại mới.', sources: [] }),
    ]))));
    const { user } = renderAssistant();

    expect(await screen.findByText('Nội dung hội thoại mới.')).toBeVisible();
    const buttons = within(screen.getByRole('navigation', { name: 'Danh sách hội thoại' })).getAllByRole('button');
    await user.click(buttons[1]);

    expect(await screen.findByText('Nội dung hội thoại cũ.')).toBeVisible();
    expect(screen.queryByText('Nội dung hội thoại mới.')).not.toBeInTheDocument();
  });

  it('mobile_layout_and_accessibility', async () => {
    answerWithTurn({ answer: 'x'.repeat(500) });
    const { user } = renderAssistant();
    await ask(user, 'Câu');
    await screen.findByText('x'.repeat(500));

    expect(screen.getByRole('log')).toHaveAttribute('aria-live', 'polite');
    expect(screen.getByRole('log').className).toContain('min-w-0');
    expect(screen.getByText('x'.repeat(500)).className).toContain('break-words');
    expect(screen.getByLabelText('Câu hỏi của bạn').tagName).toBe('TEXTAREA');
  });
});

describe('session and scope changes', () => {
  it.each([
    ['department', manager({ department_id: 2 })],
    ['role', manager({ role: 'USER' })],
    ['identity', manager({ id: 4 })],
  ])('changing_%s_clears_thread_draft_and_reloads', async (_name, nextUser) => {
    conversations = [conversation(CONVERSATION_ID)];
    storedTurns = [makeTurn({ answer: 'Số liệu riêng của phòng IT.', sources: [] })];
    const { user } = renderWithSession(manager());
    expect(await screen.findByText('Số liệu riêng của phòng IT.')).toBeVisible();
    await user.type(screen.getByLabelText('Câu hỏi của bạn'), 'Bản nháp riêng');

    conversations = [];
    storedTurns = [];
    act(() => auth.setUser(nextUser));

    await waitFor(() => expect(screen.queryByText('Số liệu riêng của phòng IT.')).not.toBeInTheDocument());
    expect(screen.getByLabelText('Câu hỏi của bạn')).toHaveValue('');
  });

  it('logout_removes_thread_and_sends_nothing_more', async () => {
    conversations = [conversation(CONVERSATION_ID)];
    storedTurns = [makeTurn({ answer: 'Số liệu riêng.', outcome: 'PROCESSING', sources: [] })];
    let polls = 0;
    server.use(http.get(`${API}/turns/:id/`, () => {
      polls += 1;
      return HttpResponse.json(makeTurn({ outcome: 'PROCESSING', answer: '', sources: [] }));
    }));
    renderWithSession(manager());
    await waitFor(() => expect(polls).toBeGreaterThan(0));

    act(() => auth.setUser(null));
    expect(screen.queryByRole('heading', { name: 'Hỏi đáp tài sản' })).not.toBeInTheDocument();
    await flush();
    const afterLogout = polls;
    await flush();
    expect(polls).toBe(afterLogout);
  });

  it('stale_create_response_never_posts_old_question_into_new_session', async () => {
    const createGate = deferred();
    server.use(http.post(`${API}/conversations/`, async () => {
      await createGate.promise;
      return HttpResponse.json(conversation('stale-conversation'), { status: 201 });
    }));
    answerWithTurn();
    const { user } = renderWithSession(manager());
    await ask(user, 'Câu hỏi riêng của user 3');

    act(() => auth.setUser(manager({ id: 4 })));
    await flush();
    createGate.resolve();
    await flush();

    expect(posts).toHaveLength(0);
    expect(screen.queryByText('Câu hỏi riêng của user 3')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Câu hỏi của bạn')).toHaveValue('');
    expect(screen.queryByText('Đang gửi…')).not.toBeInTheDocument();

    // The new session is not left locked by the old request.
    server.use(http.post(`${API}/conversations/`, () => HttpResponse.json(conversation('new-conversation'), { status: 201 })));
    await ask(user, 'Câu của user 4');
    expect(await screen.findByText('Phòng IT hiện còn 23 laptop sẵn sàng.')).toBeVisible();
    expect(posts).toHaveLength(1);
    expect(posts[0]).toMatchObject({ question: 'Câu của user 4', conversationId: 'new-conversation' });
  });

  it('stale_turn_response_does_not_write_into_new_session', async () => {
    conversations = [conversation(CONVERSATION_ID)];
    const gate = deferred();
    answerPosts(async (body) => {
      await gate.promise;
      return HttpResponse.json(makeTurn({ client_request_id: body.client_request_id, question: body.question, answer: 'Trả lời cũ.' }), { status: 201 });
    });
    const { user } = renderWithSession(manager());
    await ask(user, 'Câu cũ');
    await waitFor(() => expect(posts).toHaveLength(1));

    conversations = [];
    act(() => auth.setUser(manager({ department_id: 2 })));
    gate.resolve();
    await flush();

    expect(screen.queryByText('Trả lời cũ.')).not.toBeInTheDocument();
    expect(screen.queryByText('Câu cũ')).not.toBeInTheDocument();
  });

  it('stale_recovery_lookup_does_not_write_into_new_session', async () => {
    conversations = [conversation(CONVERSATION_ID)];
    const gate = deferred();
    let lookups = 0;
    server.use(http.get(`${API}/conversations/:id/turns/`, async () => {
      lookups += 1;
      if (lookups === 1) return HttpResponse.json(page([]));
      await gate.promise;
      return HttpResponse.json(page([makeTurn({ answer: 'Trả lời đã lưu của phiên cũ.', sources: [] })]));
    }));
    answerPosts(() => HttpResponse.error());
    const { user } = renderWithSession(manager());
    await ask(user, 'Câu cũ');
    await waitFor(() => expect(lookups).toBe(2));

    conversations = [];
    act(() => auth.setUser(manager({ id: 4 })));
    gate.resolve();
    await flush();

    expect(screen.queryByText('Trả lời đã lưu của phiên cũ.')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Gửi lại' })).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('conflict_recovery_with_scope_change_discards_old_thread', async () => {
    conversations = [conversation(CONVERSATION_ID)];
    let lookups = 0;
    server.use(http.get(`${API}/conversations/:id/turns/`, () => {
      lookups += 1;
      return lookups === 1
        ? HttpResponse.json(page([makeTurn({ answer: 'Nội dung cũ.', sources: [] })]))
        : HttpResponse.json({ code: 'CONVERSATION_SCOPE_CHANGED' }, { status: 409 });
    }));
    answerPosts(() => HttpResponse.json({ code: 'CONVERSATION_BUSY' }, { status: 409 }));
    const { user } = renderAssistant();

    await screen.findByText('Nội dung cũ.');
    await ask(user, 'Câu tiếp');

    expect(await screen.findByRole('button', { name: 'Tạo hội thoại mới' })).toBeEnabled();
    expect(screen.queryByText('Nội dung cũ.')).not.toBeInTheDocument();
  });
});

describe('pagination', () => {
  const turnsOf = (count, extra = () => ({})) => Array.from({ length: count }, (_, index) => makeTurn({
    id: `t-${index + 1}`,
    client_request_id: `r-${index + 1}`,
    question: `Câu ${index + 1}.`,
    answer: `Đáp ${index + 1}.`,
    sources: [],
    created_at: new Date(Date.UTC(2026, 10, 1, 0, index)).toISOString(),
    ...extra(index),
  }));

  it('opens_conversations_beyond_the_first_page', async () => {
    const all = Array.from({ length: 17 }, (_, index) => conversation(`conv-${index + 1}`));
    const requested = [];
    server.use(
      http.get(`${API}/conversations/`, ({ request }) => HttpResponse.json(paged(all, request))),
      http.get(`${API}/conversations/:id/turns/`, ({ params }) => {
        requested.push(params.id);
        return HttpResponse.json(page([makeTurn({ answer: `Nội dung ${params.id}.`, sources: [] })]));
      }),
    );
    const { user } = renderAssistant();
    await screen.findByText('Nội dung conv-1.');
    const list = () => within(screen.getByRole('navigation', { name: 'Danh sách hội thoại' }));
    expect(list().getAllByRole('button', { name: /^Hội thoại \d/ })).toHaveLength(15);

    await user.click(list().getByRole('button', { name: 'Trang sau' }));

    await waitFor(() => expect(list().getAllByRole('button', { name: /^Hội thoại \d/ })).toHaveLength(2));
    await user.click(list().getAllByRole('button', { name: /^Hội thoại \d/ })[1]);
    expect(await screen.findByText('Nội dung conv-17.')).toBeVisible();
    expect(requested).toContain('conv-17');
  });

  it('opens_latest_page_then_loads_older_turns_in_order', async () => {
    conversations = [conversation(CONVERSATION_ID)];
    const all = turnsOf(35);
    const pagesAsked = [];
    server.use(http.get(`${API}/conversations/:id/turns/`, ({ request }) => {
      pagesAsked.push(new URL(request.url).searchParams.get('page'));
      return HttpResponse.json(paged(all, request));
    }));
    const { user } = renderAssistant();

    expect(await screen.findByText('Đáp 35.')).toBeVisible();
    expect(screen.queryByText('Đáp 30.')).not.toBeInTheDocument();
    expect(pagesAsked).toEqual(['1', '3']);

    await user.click(screen.getByRole('button', { name: 'Tải lượt cũ hơn' }));
    expect(await screen.findByText('Đáp 16.')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Tải lượt cũ hơn' }));
    expect(await screen.findByText('Đáp 1.')).toBeVisible();

    expect(screen.queryByRole('button', { name: 'Tải lượt cũ hơn' })).not.toBeInTheDocument();
    expect(pagesAsked).toEqual(['1', '3', '2', '1']);
    const text = screen.getByRole('log').textContent;
    expect(text.indexOf('Đáp 1.')).toBeLessThan(text.indexOf('Đáp 16.'));
    expect(text.indexOf('Đáp 16.')).toBeLessThan(text.indexOf('Đáp 35.'));
  });

  it('reaches_turns_beyond_twenty_pages', async () => {
    conversations = [conversation(CONVERSATION_ID)];
    const all = turnsOf(23);
    server.use(http.get(`${API}/conversations/:id/turns/`, ({ request }) => HttpResponse.json(paged(all, request, 1))));
    const { user } = renderAssistant();

    await screen.findByText('Đáp 23.');
    for (let remaining = 22; remaining > 0; remaining -= 1) {
      await user.click(screen.getByRole('button', { name: 'Tải lượt cũ hơn' }));
      await screen.findByText(`Đáp ${remaining}.`);
    }
    expect(screen.queryByRole('button', { name: 'Tải lượt cũ hơn' })).not.toBeInTheDocument();
  });

  it('older_page_failure_keeps_thread_and_can_retry', async () => {
    conversations = [conversation(CONVERSATION_ID)];
    const all = turnsOf(20);
    let failOlder = true;
    let seenFirstLoad = false;
    server.use(http.get(`${API}/conversations/:id/turns/`, ({ request }) => {
      const asked = paged(all, request);
      if (asked.current_page === 1 && failOlder && asked.results.length && seenFirstLoad) {
        return HttpResponse.json({ detail: 'raw-trace' }, { status: 500 });
      }
      seenFirstLoad = true;
      return HttpResponse.json(asked);
    }));
    const { user } = renderAssistant();

    await screen.findByText('Đáp 20.');
    await user.click(screen.getByRole('button', { name: 'Tải lượt cũ hơn' }));
    expect(await screen.findByText(/Không tải được các lượt cũ hơn/)).toBeVisible();
    expect(screen.queryByText(/raw-trace/)).not.toBeInTheDocument();
    expect(screen.getByText('Đáp 20.')).toBeVisible();

    failOlder = false;
    await user.click(screen.getByRole('button', { name: 'Tải lượt cũ hơn' }));
    expect(await screen.findByText('Đáp 1.')).toBeVisible();
  });

  it('processing_turn_on_last_page_locks_send_after_reload', async () => {
    conversations = [conversation(CONVERSATION_ID)];
    const all = turnsOf(16, (index) => (index === 15 ? { outcome: 'PROCESSING', answer: '' } : {}));
    let finished = false;
    server.use(
      http.get(`${API}/conversations/:id/turns/`, ({ request }) => HttpResponse.json(paged(all, request))),
      http.get(`${API}/turns/:id/`, ({ params }) => HttpResponse.json(all.find((t) => t.id === params.id) && {
        ...all[15], outcome: finished ? 'ANSWERED' : 'PROCESSING', answer: finished ? 'Đáp mới.' : '',
      })),
    );
    const { user } = renderAssistant();

    expect(await screen.findByText('Đang tra cứu…')).toBeVisible();
    await user.type(screen.getByLabelText('Câu hỏi của bạn'), 'Câu mới');
    expect(screen.getByRole('button', { name: 'Gửi' })).toBeDisabled();

    finished = true;
    expect(await screen.findByText('Đáp mới.')).toBeVisible();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Gửi' })).toBeEnabled());
  });

  it('recovers_saved_turn_on_last_page_without_second_post', async () => {
    conversations = [conversation(CONVERSATION_ID)];
    const all = turnsOf(15);
    server.use(http.get(`${API}/conversations/:id/turns/`, ({ request }) => HttpResponse.json(paged(all, request))));
    answerPosts((body) => {
      all.push(makeTurn({
        id: 't-saved', client_request_id: body.client_request_id, question: body.question, answer: 'Đáp đã lưu.',
        sources: [], created_at: '2026-11-05T00:00:00Z',
      }));
      return HttpResponse.error();
    });
    const { user } = renderAssistant();
    await screen.findByText('Đáp 15.');

    await ask(user, 'Câu lưu ở trang hai');

    expect(await screen.findByText('Đáp đã lưu.')).toBeVisible();
    expect(posts).toHaveLength(1);
    expect(screen.queryByRole('button', { name: 'Gửi lại' })).not.toBeInTheDocument();
    expect(screen.getByText('Đáp 15.')).toBeVisible();
  });

  it('saved_turn_not_on_loaded_page_is_replayed_with_same_request_id', async () => {
    conversations = [conversation(CONVERSATION_ID)];
    const visible = turnsOf(3);
    server.use(http.get(`${API}/conversations/:id/turns/`, () => HttpResponse.json(page(visible))));
    let saved = null;
    answerPosts((body, count) => {
      if (count === 1) {
        saved = makeTurn({ id: 't-saved', client_request_id: body.client_request_id, question: body.question, answer: 'Đáp lần đầu.', sources: [] });
        return HttpResponse.error();
      }
      // The server replays the stored turn for a known request ID instead of creating another.
      expect(body.client_request_id).toBe(saved.client_request_id);
      return HttpResponse.json(saved, { status: 200 });
    });
    const { user } = renderAssistant();
    await screen.findByText('Đáp 3.');

    await ask(user, 'Câu đã lưu nhưng chưa thấy');
    await user.click(await screen.findByRole('button', { name: 'Gửi lại' }));

    expect(await screen.findByText('Đáp lần đầu.')).toBeVisible();
    expect(screen.getAllByText('Đáp lần đầu.')).toHaveLength(1);
    expect(posts).toHaveLength(2);
    expect(posts[1].client_request_id).toBe(posts[0].client_request_id);
  });

  it('switching_thread_resets_stopped_polling', async () => {
    const other = 'conv-other';
    conversations = [conversation(CONVERSATION_ID), conversation(other, '2026-11-04T02:59:00Z')];
    server.use(
      http.get(`${API}/conversations/:id/turns/`, ({ params }) => HttpResponse.json(page([makeTurn({
        id: `turn-${params.id}`, outcome: 'PROCESSING', answer: '', sources: [],
      })]))),
      http.get(`${API}/turns/:id/`, ({ params }) => (params.id === `turn-${other}`
        ? HttpResponse.json(makeTurn({ id: params.id, answer: 'Đáp hội thoại khác.', sources: [] }))
        : HttpResponse.json({}, { status: 500 }))),
    );
    const { user } = renderAssistant();

    expect(await screen.findByRole('button', { name: 'Kiểm tra lại' })).toBeVisible();
    await user.click(within(screen.getByRole('navigation', { name: 'Danh sách hội thoại' })).getAllByRole('button')[1]);

    expect(await screen.findByText('Đáp hội thoại khác.')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Kiểm tra lại' })).not.toBeInTheDocument();
  });
});

describe('procedure evidence version', () => {
  const procedureSource = {
    ...aggregateSource,
    id: 'procedure:return:v1',
    kind: 'procedure',
    label: 'Hướng dẫn trả',
    href: '/my-requests',
    facts: { topic: 'return', version: 'v1', title: 'Quy trình cũ', body: 'Nội dung phiên bản một' },
  };

  it('keeps_stored_version_and_labels_newer_guidance_separately', async () => {
    server.use(http.get(`${API}/procedures/:topic/`, () => HttpResponse.json({
      topic: 'return', version: 'v2', title: 'Quy trình mới', body: 'Nội dung phiên bản hai',
      actions: [{ label: 'Mở Phiếu của tôi', href: '/my-requests' }, { label: 'Liên kết lạ', href: 'https://evil.example' }],
    })));
    answerWithTurn({ sources: [procedureSource] });
    const { user } = renderAssistant();
    await ask(user, 'Trả tài sản?');

    await user.click(await screen.findByRole('button', { name: 'Hướng dẫn trả' }));

    expect(await screen.findByText('Hướng dẫn hiện tại')).toBeVisible();
    const panel = within(screen.getByRole('region', { name: 'Căn cứ: Hướng dẫn trả' }));
    expect(panel.getByText('Căn cứ khi trả lời')).toBeVisible();
    expect(panel.getByText('Nội dung phiên bản một')).toBeVisible();
    expect(panel.getByText('Phiên bản: v1')).toBeVisible();
    expect(panel.getByText('Nội dung phiên bản hai')).toBeVisible();
    expect(panel.getByText('Phiên bản: v2')).toBeVisible();
    const text = screen.getByRole('region', { name: 'Căn cứ: Hướng dẫn trả' }).textContent;
    expect(text.indexOf('Căn cứ khi trả lời')).toBeLessThan(text.indexOf('Nội dung phiên bản một'));
    expect(text.indexOf('Nội dung phiên bản một')).toBeLessThan(text.indexOf('Hướng dẫn hiện tại'));
    expect(text.indexOf('Hướng dẫn hiện tại')).toBeLessThan(text.indexOf('Nội dung phiên bản hai'));
    expect(panel.queryByRole('link', { name: 'Liên kết lạ' })).not.toBeInTheDocument();
  });

  it('api_failure_still_shows_stored_evidence_without_raw_error', async () => {
    server.use(http.get(`${API}/procedures/:topic/`, () => HttpResponse.json({ detail: 'raw-trace' }, { status: 500 })));
    answerWithTurn({ sources: [procedureSource] });
    const { user } = renderAssistant();
    await ask(user, 'Trả tài sản?');

    await user.click(await screen.findByRole('button', { name: 'Hướng dẫn trả' }));

    const panel = within(screen.getByRole('region', { name: 'Căn cứ: Hướng dẫn trả' }));
    expect(panel.getByText('Nội dung phiên bản một')).toBeVisible();
    expect(panel.getByText('Phiên bản: v1')).toBeVisible();
    await flush();
    expect(screen.queryByText(/raw-trace/)).not.toBeInTheDocument();
    expect(screen.queryByText('Hướng dẫn hiện tại')).not.toBeInTheDocument();
    expect(panel.getByRole('link', { name: 'Mở trang liên quan' })).toHaveAttribute('href', '/my-requests');
  });
});

describe('support reply round trip', () => {
  it('requester_refreshes_and_reads_staff_reply', async () => {
    // One stateful ticket shared by the requester's turn and the staff inbox.
    let ticket = openDetail;
    const replies = [];
    const turnNow = () => makeTurn({
      id: 'turn-rt', question: 'Bao giờ có máy?', outcome: 'ESCALATED', answer: 'Đã chuyển câu hỏi.', sources: [],
      ticket: ticketNested(ticket),
    });
    conversations = [{ id: CONVERSATION_ID, created_at: '2026-11-05T02:59:00Z' }];
    server.use(
      http.get(`${API}/conversations/:id/turns/`, () => HttpResponse.json(page([turnNow()]))),
      http.get(`${API}/turns/:id/`, () => HttpResponse.json(turnNow())),
      http.get(`${API}/support-tickets/`, ({ request }) => {
        const status = new URL(request.url).searchParams.get('status');
        return HttpResponse.json(page(!status || ticket.status === status ? [ticketListItem(ticket)] : []));
      }),
      http.get(`${API}/support-tickets/:id/`, () => HttpResponse.json(ticket)),
      http.post(`${API}/support-tickets/:id/reply/`, async ({ request }) => {
        const body = await request.json();
        replies.push(body);
        ticket = {
          ...ticket, status: 'ANSWERED', version: body.expected_version + 1, reply: body.reply,
          replied_by_name: 'Quản lý IT', replied_at: '2026-11-05T17:30:00Z',
        };
        return HttpResponse.json(ticket);
      }),
    );
    const user = userEvent.setup();

    const requesterView = () => (
      <MemoryRouter>
        <AuthProvider initialUser={{ id: 3, username: 'requester', role: 'USER', department_id: 1 }}>
          <AssetAssistantPage pollIntervalMs={10} />
        </AuthProvider>
      </MemoryRouter>
    );
    const first = render(requesterView());
    expect(await screen.findByText(/Đã chuyển đến Phòng IT/)).toBeVisible();
    expect(screen.queryByRole('region', { name: 'Phản hồi người phụ trách' })).not.toBeInTheDocument();
    first.unmount();

    const staff = render(
      <MemoryRouter>
        <AuthProvider initialUser={{ id: 7, username: 'manager', role: 'MANAGER', department_id: 1 }}>
          <AssistantSupportPage />
        </AuthProvider>
      </MemoryRouter>,
    );
    await user.click(await screen.findByRole('button', { name: 'Xem câu hỏi' }));
    await user.type(await screen.findByLabelText('Phản hồi'), 'Thứ Hai có máy.');
    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));
    expect(await screen.findByText('Đã trả lời')).toBeVisible();
    expect(replies).toHaveLength(1);
    expect(replies[0].expected_version).toBe(1);
    staff.unmount();

    // A fresh load of the chat (browser refresh) shows the reply without the staff inbox.
    render(requesterView());
    const reply = await screen.findByRole('region', { name: 'Phản hồi người phụ trách' });
    expect(within(reply).getByText('Thứ Hai có máy.')).toBeVisible();
    expect(within(reply).getByText(/Quản lý IT/)).toBeVisible();
    expect(within(reply).getByText(/00:30/)).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Kiểm tra phản hồi' })).not.toBeInTheDocument();
  });

  it('check_reply_button_picks_up_a_reply_saved_after_the_chat_loaded', async () => {
    let ticket = openTicket;
    const turnNow = () => makeTurn({ id: 'turn-rt', outcome: 'ESCALATED', answer: 'Đã chuyển câu hỏi.', sources: [], ticket });
    conversations = [{ id: CONVERSATION_ID, created_at: '2026-11-05T02:59:00Z' }];
    server.use(
      http.get(`${API}/conversations/:id/turns/`, () => HttpResponse.json(page([turnNow()]))),
      http.get(`${API}/turns/:id/`, () => HttpResponse.json(turnNow())),
    );
    const { user } = renderAssistant();
    const check = await screen.findByRole('button', { name: 'Kiểm tra phản hồi' });

    await user.click(check);
    expect(await screen.findByText(/đang chờ phản hồi/)).toBeVisible();

    ticket = { ...openTicket, status: 'ANSWERED', version: 2, reply: 'Đã xử lý xong.', replied_by_name: 'Quản lý IT', replied_at: '2026-11-05T17:30:00Z' };
    await user.click(screen.getByRole('button', { name: 'Kiểm tra phản hồi' }));

    expect(await screen.findByRole('region', { name: 'Phản hồi người phụ trách' })).toBeVisible();
  });
});

describe('assistantApi', () => {
  it('returns_response_data_and_uses_frozen_paths', async () => {
    const seen = [];
    server.use(
      http.get(`${API}/conversations/:id/turns/`, ({ request, params }) => {
        seen.push(['turns', params.id, new URL(request.url).searchParams.get('page')]);
        return HttpResponse.json(page([]));
      }),
      http.get(`${API}/turns/:id/`, ({ params }) => {
        seen.push(['turn', params.id]);
        return HttpResponse.json({ id: params.id });
      }),
      http.post(`${API}/turns/:id/escalate/`, async ({ request, params }) => {
        seen.push(['escalate', params.id, await request.json()]);
        return HttpResponse.json(openDetail, { status: 201 });
      }),
      http.get(`${API}/procedures/:topic/`, ({ params }) => HttpResponse.json({ topic: params.topic })),
    );

    expect((await assistantApi.createConversation()).id).toBe(CONVERSATION_ID);
    expect((await assistantApi.listConversations()).results).toHaveLength(1);
    expect((await assistantApi.listTurns('c1', 2)).results).toEqual([]);
    expect((await assistantApi.getTurn('t1')).id).toBe('t1');
    expect((await assistantApi.escalateTurn('t1')).id).toBe('ticket-1');
    expect((await assistantApi.getProcedure('roles')).topic).toBe('roles');
    expect(seen).toEqual([['turns', 'c1', '2'], ['turn', 't1'], ['escalate', 't1', {}]]);
  });
});
