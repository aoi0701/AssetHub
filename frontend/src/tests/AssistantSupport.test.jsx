import React from 'react';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

import { AuthProvider, useAuth } from '../context/AuthContext';
import AssistantSupportPage from '../pages/AssistantSupportPage';
import * as assistantApi from '../api/assistantApi';
import { ticketDetail, ticketListItem } from './assistantDto';

const API = '/api/assistant';

const makeTicket = ticketDetail;

const answered = (overrides = {}) => makeTicket({
  status: 'ANSWERED',
  version: 2,
  reply: 'Đã tiếp nhận câu hỏi.',
  replied_by_name: 'Quản lý IT',
  replied_at: '2026-11-05T17:30:00Z',
  ...overrides,
});

const listing = (all, request, size = 15) => {
  const params = new URL(request.url).searchParams;
  const current = Number(params.get('page') || 1);
  const totalPages = Math.max(1, Math.ceil(all.length / size));
  return {
    count: all.length,
    total_pages: totalPages,
    current_page: current,
    page_size: size,
    next: current < totalPages ? `?page=${current + 1}` : null,
    previous: current > 1 ? `?page=${current - 1}` : null,
    results: all.slice((current - 1) * size, current * size).map(ticketListItem),
  };
};

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

let tickets;
let listRequests;
let replyPosts;

const server = setupServer(
  http.get(`${API}/support-tickets/`, ({ request }) => {
    const params = Object.fromEntries(new URL(request.url).searchParams);
    listRequests.push(params);
    const matching = tickets.filter((ticket) => !params.status || ticket.status === params.status);
    const result = listing(matching, request);
    // Like DRF, a page past the end is a 404 rather than an empty page.
    if (result.current_page > result.total_pages) return HttpResponse.json({ detail: 'Invalid page.' }, { status: 404 });
    return HttpResponse.json(result);
  }),
  http.get(`${API}/support-tickets/:id/`, ({ params }) => {
    const found = tickets.find((ticket) => ticket.id === params.id);
    return found ? HttpResponse.json(found) : HttpResponse.json({ detail: 'raw-not-found' }, { status: 404 });
  }),
);

const answerReplies = (respond) => {
  server.use(http.post(`${API}/support-tickets/:id/reply/`, async ({ request, params }) => {
    const body = await request.json();
    replyPosts.push({ ...body, ticketId: params.id });
    return respond(body, replyPosts.length, params.id);
  }));
};

const answerRepliesWithSuccess = () => answerReplies((body, _count, id) => {
  const stored = tickets.find((ticket) => ticket.id === id);
  const updated = answered({ ...stored, status: 'ANSWERED', reply: body.reply, version: body.expected_version + 1 });
  tickets = tickets.map((ticket) => (ticket.id === id ? updated : ticket));
  return HttpResponse.json(updated);
});

let auth;
const CaptureAuth = () => {
  auth = useAuth();
  return null;
};

const staff = (role = 'MANAGER', overrides = {}) => ({ id: 7, username: 'staff', role, department_id: 1, ...overrides });

const renderSupportInbox = (role = 'MANAGER', overrides = {}) => {
  render(
    <MemoryRouter>
      <AuthProvider initialUser={staff(role, overrides)}>
        <CaptureAuth />
        <AssistantSupportPage />
      </AuthProvider>
    </MemoryRouter>,
  );
  return { user: userEvent.setup(), replyPosts };
};

const flush = () => act(() => new Promise((done) => setTimeout(done, 50)));

const openFirst = async (user) => {
  const buttons = await screen.findAllByRole('button', { name: 'Xem câu hỏi' });
  await user.click(buttons[0]);
  return screen.findByLabelText('Phản hồi');
};

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  tickets = [makeTicket()];
  listRequests = [];
  replyPosts = [];
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('AssistantSupportPage', () => {
  it('sends_reply_with_the_loaded_ticket_version', async () => {
    answerRepliesWithSuccess();
    const { user, replyPosts: posts } = renderSupportInbox('MANAGER');
    await user.click(await screen.findByRole('button', { name: 'Xem câu hỏi' }));
    await user.type(await screen.findByLabelText('Phản hồi'), 'Đã tiếp nhận câu hỏi.');
    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));

    expect(await screen.findByText('Đã trả lời')).toBeVisible();
    expect(posts[0]).toMatchObject({ expected_version: 1, reply: 'Đã tiếp nhận câu hỏi.' });
    expect(posts[0].client_request_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(screen.queryByLabelText('Phản hồi')).not.toBeInTheDocument();
    expect(screen.getByText('Đã tiếp nhận câu hỏi.')).toBeVisible();
  });

  it('manager_reads_department_queue_only', async () => {
    const { user } = renderSupportInbox('MANAGER');

    expect(await screen.findByRole('heading', { name: 'Hỗ trợ hỏi đáp' })).toBeVisible();
    await waitFor(() => expect(listRequests).toHaveLength(1));
    expect(listRequests[0]).toMatchObject({ view: 'inbox', status: 'OPEN', page: '1' });
    expect(await screen.findByText(/Phòng IT/)).toBeVisible();
    // Routing is decided by the backend; the inbox offers no way to pick a queue or recipient.
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    await openFirst(user);
    expect(screen.queryByLabelText(/phòng ban/i)).not.toBeInTheDocument();
  });

  it('admin_sees_fallback_queue', async () => {
    tickets = [makeTicket({ target_kind: 'ADMIN', target_department_name: null })];
    const { user } = renderSupportInbox('ADMIN');

    expect(await screen.findByText('Quản trị viên')).toBeVisible();
    await openFirst(user);
    expect(screen.getByText(/Không có quản lý phòng ban khác tiếp nhận/)).toBeVisible();
  });

  it('panel_loads_only_the_question_snapshot', async () => {
    // Any request to conversation or turn endpoints fails the test: onUnhandledRequest is "error".
    const { user } = renderSupportInbox();
    await openFirst(user);

    expect(screen.getByText('Khi nào tôi được cấp màn hình mới?')).toBeVisible();
    expect(screen.queryByText(/turn-1/)).not.toBeInTheDocument();
  });

  it('filters_by_status_and_paginates_using_metadata', async () => {
    tickets = Array.from({ length: 17 }, (_, index) => makeTicket({
      id: `t-${index}`, question_snapshot: `Câu ${index}`,
    }));
    const { user } = renderSupportInbox();
    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Xem câu hỏi' })).toHaveLength(15));

    await user.click(screen.getByRole('button', { name: 'Trang sau' }));
    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Xem câu hỏi' })).toHaveLength(2));
    expect(listRequests.at(-1)).toMatchObject({ page: '2', status: 'OPEN' });

    await user.click(screen.getByRole('button', { name: 'Đã phản hồi' }));
    await waitFor(() => expect(listRequests.at(-1)).toMatchObject({ page: '1', status: 'ANSWERED' }));

    await user.click(screen.getByRole('button', { name: 'Tất cả' }));
    await waitFor(() => expect(listRequests.at(-1).status).toBeUndefined());
    expect(listRequests.at(-1).page).toBe('1');
  });

  it('reply_conflict_preserves_server_reply', async () => {
    answerReplies(() => {
      tickets = [answered({ reply: 'Câu trả lời của người khác.', replied_by_name: 'Quản lý khác', version: 2 })];
      return HttpResponse.json({ code: 'TICKET_ALREADY_ANSWERED', detail: 'raw-conflict' }, { status: 409 });
    });
    const { user } = renderSupportInbox();
    await user.type(await openFirst(user), 'Câu trả lời của tôi.');
    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));

    expect(await screen.findByText('Câu trả lời của người khác.')).toBeVisible();
    expect(screen.getByText(/Quản lý khác/)).toBeVisible();
    expect(screen.getByText(/Phiếu này đã được xử lý/)).toBeVisible();
    expect(screen.queryByText('Câu trả lời của tôi.')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Gửi phản hồi' })).not.toBeInTheDocument();
    expect(screen.queryByText(/raw-conflict/)).not.toBeInTheDocument();
    expect(screen.getByText('Đã trả lời')).toBeVisible();
    expect(replyPosts).toHaveLength(1);
  });

  it('conflict_with_still_open_ticket_uses_new_version_and_new_request_id', async () => {
    answerReplies((body, count) => {
      if (count === 1) {
        tickets = [makeTicket({ version: 5 })];
        return HttpResponse.json({ code: 'STALE_VERSION_CONFLICT' }, { status: 409 });
      }
      return HttpResponse.json(answered({ reply: body.reply, version: 6 }));
    });
    const { user } = renderSupportInbox();
    await user.type(await openFirst(user), 'Nội dung.');
    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));

    await screen.findByText(/Phiếu này đã thay đổi/);
    expect(screen.getByLabelText('Phản hồi')).toHaveValue('Nội dung.');
    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));

    expect(await screen.findByText('Đã trả lời')).toBeVisible();
    expect(replyPosts[1].expected_version).toBe(5);
    expect(replyPosts[1].client_request_id).not.toBe(replyPosts[0].client_request_id);
  });

  it('conflict_with_failed_refresh_never_claims_success', async () => {
    answerReplies(() => HttpResponse.json({ code: 'STALE_VERSION_CONFLICT' }, { status: 409 }));
    const { user } = renderSupportInbox();
    await user.type(await openFirst(user), 'Nội dung.');
    server.use(http.get(`${API}/support-tickets/:id/`, () => HttpResponse.json({}, { status: 500 })));
    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));

    expect(await screen.findByText(/Phiếu này đã thay đổi nhưng chưa tải được/)).toBeVisible();
    expect(screen.queryByText('Đã trả lời')).not.toBeInTheDocument();
  });

  it('reply_network_retry_reuses_request_id', async () => {
    answerReplies((body, count) => (count === 1
      ? HttpResponse.error()
      : HttpResponse.json(answered({ reply: body.reply }))));
    const { user } = renderSupportInbox();
    await user.type(await openFirst(user), 'Nội dung gửi lại.');
    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));

    expect(await screen.findByText(/Chưa xác nhận được phản hồi đã gửi/)).toBeVisible();
    expect(screen.getByLabelText('Phản hồi')).toHaveValue('Nội dung gửi lại.');
    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));

    expect(await screen.findByText('Đã trả lời')).toBeVisible();
    expect(replyPosts).toHaveLength(2);
    expect(replyPosts[1]).toMatchObject({
      client_request_id: replyPosts[0].client_request_id,
      expected_version: replyPosts[0].expected_version,
      reply: replyPosts[0].reply,
    });
  });

  it('edited_text_after_network_failure_is_a_new_attempt_guarded_by_version', async () => {
    answerReplies((body, count) => (count === 1
      ? HttpResponse.error()
      : HttpResponse.json(answered({ reply: body.reply }))));
    const { user } = renderSupportInbox();
    const input = await openFirst(user);
    await user.type(input, 'Bản một');
    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));
    await screen.findByText(/Chưa xác nhận được phản hồi đã gửi/);

    await user.type(input, ' sửa');
    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));

    await screen.findByText('Đã trả lời');
    expect(replyPosts[1].client_request_id).not.toBe(replyPosts[0].client_request_id);
    expect(replyPosts[1].expected_version).toBe(1);
  });

  it('double_click_sends_once', async () => {
    const gate = deferred();
    answerReplies(async (body) => {
      await gate.promise;
      return HttpResponse.json(answered({ reply: body.reply }));
    });
    const { user } = renderSupportInbox();
    await user.type(await openFirst(user), 'Một lần.');
    const send = screen.getByRole('button', { name: 'Gửi phản hồi' });
    await user.click(send);
    await waitFor(() => expect(send).toBeDisabled());
    await user.click(send);
    gate.resolve();

    await screen.findByText('Đã trả lời');
    expect(replyPosts).toHaveLength(1);
  });

  it('reply_text_is_required_and_rendered_as_text', async () => {
    const hostile = '<img src=x onerror="window.__pwnedSupport=1">';
    tickets = [makeTicket({ question_snapshot: hostile })];
    answerRepliesWithSuccess();
    const { user } = renderSupportInbox();
    const input = await openFirst(user);

    expect(screen.getByText(hostile)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Gửi phản hồi' })).toBeDisabled();
    await user.type(input, '   ');
    expect(screen.getByRole('button', { name: 'Gửi phản hồi' })).toBeDisabled();
    expect(input).toHaveAttribute('maxlength', '4000');
    expect(document.querySelector('img[src="x"]')).toBeNull();
  });

  it.each([[400, /chưa hợp lệ/], [403, /không còn quyền/], [404, /không còn quyền/]])(
    'rejected_reply_%i_shows_fixed_message',
    async (status, message) => {
      answerReplies(() => HttpResponse.json({ detail: 'raw-reject' }, { status }));
      const { user } = renderSupportInbox();
      await user.type(await openFirst(user), 'Nội dung.');
      await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));

      expect(await screen.findByText(message)).toBeVisible();
      expect(screen.queryByText(/raw-reject/)).not.toBeInTheDocument();
      expect(screen.queryByText('Đã trả lời')).not.toBeInTheDocument();
    },
  );

  it('disabled_assistant_keeps_reply_and_request_id_for_retry', async () => {
    answerReplies((body, count) => (count === 1
      ? HttpResponse.json({ code: 'ASSISTANT_DISABLED', detail: 'raw-disabled' }, { status: 503 })
      : HttpResponse.json(answered({ reply: body.reply }))));
    const { user } = renderSupportInbox();
    await user.type(await openFirst(user), 'Nội dung.');
    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));

    expect(await screen.findByText(/Trợ lý đang tạm tắt/)).toBeVisible();
    expect(screen.queryByText(/raw-disabled/)).not.toBeInTheDocument();
    expect(screen.queryByText('Đã trả lời')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Phản hồi')).toHaveValue('Nội dung.');

    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));
    await screen.findByText('Đã trả lời');
    expect(replyPosts[1].client_request_id).toBe(replyPosts[0].client_request_id);
  });

  it('user_cannot_open_support_inbox', async () => {
    renderSupportInbox('USER');

    expect(await screen.findByText(/không có quyền xem hộp thư hỗ trợ/)).toBeVisible();
    await flush();
    expect(listRequests).toHaveLength(0);
    expect(screen.queryByRole('button', { name: 'Xem câu hỏi' })).not.toBeInTheDocument();
  });

  it.each([403, 404])('backend_%i_on_inbox_is_shown_compactly', async (status) => {
    server.use(http.get(`${API}/support-tickets/`, () => HttpResponse.json({ detail: 'raw-policy' }, { status })));
    renderSupportInbox();

    expect(await screen.findByText(/không có quyền xem hộp thư hỗ trợ/)).toBeVisible();
    expect(screen.queryByText(/raw-policy/)).not.toBeInTheDocument();
  });

  it('ticket_detail_failure_is_compact_and_keeps_the_list', async () => {
    server.use(http.get(`${API}/support-tickets/:id/`, () => HttpResponse.json({ detail: 'raw-detail' }, { status: 404 })));
    const { user } = renderSupportInbox();
    await user.click(await screen.findByRole('button', { name: 'Xem câu hỏi' }));

    expect(await screen.findByText(/Không mở được phiếu này/)).toBeVisible();
    expect(screen.queryByText(/raw-detail/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Xem câu hỏi' })).toBeVisible();
  });

  it('empty_queue_says_so', async () => {
    tickets = [];
    renderSupportInbox();
    expect(await screen.findByText('Không có phiếu nào trong mục này.')).toBeVisible();
  });

  it('refresh_loads_new_version_so_reply_is_not_sent_with_old_one', async () => {
    answerRepliesWithSuccess();
    const { user } = renderSupportInbox();
    await user.type(await openFirst(user), 'Nội dung.');

    tickets = [makeTicket({ version: 3 })];
    await user.click(screen.getByRole('button', { name: 'Làm mới' }));
    await waitFor(() => expect(listRequests.length).toBeGreaterThan(1));
    await flush();
    expect(screen.getByLabelText('Phản hồi')).toHaveValue('Nội dung.');
    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));

    await screen.findByText('Đã trả lời');
    expect(replyPosts[0].expected_version).toBe(3);
  });
});

describe('reconciling the list after a reply', () => {
  const manyOpen = (count) => Array.from({ length: count }, (_, index) => makeTicket({
    id: `t-${index}`, question_snapshot: `Câu ${index}`, target_department_name: `Hàng ${index}`,
  }));

  it('list_response_started_before_reply_cannot_restore_open_row', async () => {
    const gate = deferred();
    const oldSnapshot = [makeTicket()];
    let holdNext = false;
    server.use(http.get(`${API}/support-tickets/`, async ({ request }) => {
      const params = Object.fromEntries(new URL(request.url).searchParams);
      listRequests.push(params);
      if (holdNext) {
        holdNext = false;
        await gate.promise;
        return HttpResponse.json(listing(oldSnapshot, request));
      }
      return HttpResponse.json(listing(tickets.filter((ticket) => ticket.status === params.status), request));
    }));
    answerRepliesWithSuccess();
    const { user } = renderSupportInbox();
    await user.type(await openFirst(user), 'Đã xử lý.');

    holdNext = true;
    await user.click(screen.getByRole('button', { name: 'Làm mới' }));
    await waitFor(() => expect(listRequests).toHaveLength(2));
    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));
    await screen.findByText('Đã trả lời');
    await screen.findByText('Không có phiếu nào trong mục này.');

    gate.resolve();
    await flush();

    expect(screen.queryByText('Đang chờ')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Xem câu hỏi' })).not.toBeInTheDocument();
    expect(screen.getByText('Đã xử lý.')).toBeVisible();
    expect(listRequests).toHaveLength(3);
  });

  it('open_filter_drops_answered_row_and_updates_count_keeping_detail', async () => {
    tickets = manyOpen(17);
    answerRepliesWithSuccess();
    const { user } = renderSupportInbox();
    await user.type(await openFirst(user), 'Đã xử lý.');
    const pager = () => screen.getByRole('navigation', { name: 'Phân trang' });
    expect(pager().textContent).toContain('17');

    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));

    await screen.findByText('Đã trả lời');
    await waitFor(() => expect(pager().textContent).toContain('16'));
    expect(screen.queryByText('Hàng 0')).not.toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Xem câu hỏi' })).toHaveLength(15);
    expect(screen.getByText('Câu 0')).toBeVisible();
    expect(listRequests.at(-1)).toMatchObject({ status: 'OPEN', page: '1' });
    expect(replyPosts).toHaveLength(1);
  });

  it('replying_to_only_row_of_last_page_falls_back_without_a_loop', async () => {
    tickets = manyOpen(16);
    answerRepliesWithSuccess();
    const { user } = renderSupportInbox();
    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Xem câu hỏi' })).toHaveLength(15));
    await user.click(screen.getByRole('button', { name: 'Trang sau' }));
    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Xem câu hỏi' })).toHaveLength(1));
    await user.type(await openFirst(user), 'Đã xử lý.');
    const before = listRequests.length;

    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));

    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Xem câu hỏi' })).toHaveLength(15));
    await flush();
    // One reload of the vanished page (404), then one of the previous page.
    expect(listRequests.slice(before).map((params) => params.page)).toEqual(['2', '1']);
    expect(screen.queryByText(/không có quyền/)).not.toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'Phân trang' })).not.toBeInTheDocument();
    expect(screen.getByText('Đã trả lời')).toBeVisible();
    expect(screen.getByText('Câu 15')).toBeVisible();
  });

  it('empty_page_response_also_falls_back_once', async () => {
    tickets = manyOpen(16);
    server.use(http.get(`${API}/support-tickets/`, ({ request }) => {
      const params = Object.fromEntries(new URL(request.url).searchParams);
      listRequests.push(params);
      const matching = tickets.filter((ticket) => ticket.status === params.status);
      const result = listing(matching, request);
      return HttpResponse.json(result.current_page > result.total_pages ? { ...result, results: [] } : result);
    }));
    answerRepliesWithSuccess();
    const { user } = renderSupportInbox();
    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Xem câu hỏi' })).toHaveLength(15));
    await user.click(screen.getByRole('button', { name: 'Trang sau' }));
    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Xem câu hỏi' })).toHaveLength(1));
    await user.type(await openFirst(user), 'Đã xử lý.');

    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));

    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Xem câu hỏi' })).toHaveLength(15));
  });

  it('all_filter_keeps_the_row_and_shows_it_answered', async () => {
    answerRepliesWithSuccess();
    const { user } = renderSupportInbox();
    await screen.findByRole('button', { name: 'Xem câu hỏi' });
    await user.click(screen.getByRole('button', { name: 'Tất cả' }));
    await waitFor(() => expect(listRequests.at(-1).status).toBeUndefined());
    await user.type(await openFirst(user), 'Đã xử lý.');

    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));

    await waitFor(() => expect(screen.getAllByText('Đã trả lời')).toHaveLength(2));
    expect(screen.queryByText('Đang chờ')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Xem câu hỏi' })).toBeVisible();
    await flush();
    expect(screen.getAllByText('Đã trả lời')).toHaveLength(2);
    expect(listRequests.at(-1).status).toBeUndefined();
  });

  it('reply_finishing_after_filter_change_shows_row_in_answered_view', async () => {
    const gate = deferred();
    answerReplies(async (body, _count, id) => {
      await gate.promise;
      const updated = answered({ id, reply: body.reply });
      tickets = tickets.map((ticket) => (ticket.id === id ? updated : ticket));
      return HttpResponse.json(updated);
    });
    const { user } = renderSupportInbox();
    await user.type(await openFirst(user), 'Đã xử lý.');
    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));
    await waitFor(() => expect(replyPosts).toHaveLength(1));

    await user.click(screen.getByRole('button', { name: 'Đã phản hồi' }));
    await screen.findByText('Không có phiếu nào trong mục này.');
    gate.resolve();

    expect(await screen.findByRole('button', { name: 'Xem câu hỏi' })).toBeVisible();
    expect(screen.getByText('Đã trả lời')).toBeVisible();
    expect(listRequests.at(-1)).toMatchObject({ status: 'ANSWERED', page: '1' });
  });

  it('reply_from_previous_panel_updates_list_without_disturbing_the_open_ticket', async () => {
    tickets = manyOpen(2);
    const gate = deferred();
    answerReplies(async (body, _count, id) => {
      await gate.promise;
      const updated = answered({ id, reply: body.reply, question_snapshot: 'Câu 0', target_department_name: 'Hàng 0' });
      tickets = tickets.map((ticket) => (ticket.id === id ? updated : ticket));
      return HttpResponse.json(updated);
    });
    const { user } = renderSupportInbox();
    const buttons = await screen.findAllByRole('button', { name: 'Xem câu hỏi' });
    await user.click(buttons[0]);
    await user.type(await screen.findByLabelText('Phản hồi'), 'Trả lời phiếu đầu.');
    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));
    await waitFor(() => expect(replyPosts).toHaveLength(1));

    await user.click(buttons[1]);
    await screen.findByText('Câu 1');
    await user.type(screen.getByLabelText('Phản hồi'), 'Bản nháp phiếu sau');
    gate.resolve();

    await waitFor(() => expect(screen.queryByText('Hàng 0')).not.toBeInTheDocument());
    expect(screen.getByText('Câu 1')).toBeVisible();
    expect(screen.getByLabelText('Phản hồi')).toHaveValue('Bản nháp phiếu sau');
    expect(screen.getByText('Hàng 1')).toBeVisible();
  });

  it('reply_finishing_while_another_ticket_is_loading_does_not_cancel_that_open', async () => {
    tickets = manyOpen(2);
    const replyGate = deferred();
    const detailGate = deferred();
    server.use(http.get(`${API}/support-tickets/:id/`, async ({ params }) => {
      if (params.id === 't-1') await detailGate.promise;
      return HttpResponse.json(tickets.find((ticket) => ticket.id === params.id));
    }));
    answerReplies(async (body, _count, id) => {
      await replyGate.promise;
      const updated = answered({ id, reply: body.reply });
      tickets = tickets.map((ticket) => (ticket.id === id ? updated : ticket));
      return HttpResponse.json(updated);
    });
    const { user } = renderSupportInbox();
    const buttons = await screen.findAllByRole('button', { name: 'Xem câu hỏi' });
    await user.click(buttons[0]);
    await user.type(await screen.findByLabelText('Phản hồi'), 'Trả lời.');
    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));
    await waitFor(() => expect(replyPosts).toHaveLength(1));
    await user.click(buttons[1]);

    replyGate.resolve();
    await waitFor(() => expect(screen.getByText('Đã trả lời')).toBeVisible());
    detailGate.resolve();

    expect(await screen.findByText('Câu 1')).toBeVisible();
  });
});

describe('races', () => {
  it('detail_request_started_before_reply_cannot_restore_open_ticket', async () => {
    const gate = deferred();
    let holdDetail = false;
    server.use(http.get(`${API}/support-tickets/:id/`, async () => {
      if (holdDetail) {
        await gate.promise;
        return HttpResponse.json(makeTicket());
      }
      return HttpResponse.json(makeTicket());
    }));
    answerRepliesWithSuccess();
    const { user } = renderSupportInbox();
    await user.type(await openFirst(user), 'Đã xử lý.');

    holdDetail = true;
    await user.click(screen.getByRole('button', { name: 'Làm mới' }));
    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));
    await screen.findByText('Đã trả lời');
    gate.resolve();
    await flush();

    expect(screen.queryByLabelText('Phản hồi')).not.toBeInTheDocument();
    expect(screen.getByText('Đã xử lý.')).toBeVisible();
  });
});

describe('session and scope changes', () => {
  it.each([
    ['department', staff('MANAGER', { department_id: 2 })],
    ['role', staff('ADMIN')],
    ['identity', staff('MANAGER', { id: 8 })],
  ])('changing_%s_clears_tickets_detail_and_draft', async (_name, nextUser) => {
    const { user } = renderSupportInbox();
    await user.type(await openFirst(user), 'Bản nháp riêng');
    expect(screen.getByText('Khi nào tôi được cấp màn hình mới?')).toBeVisible();

    tickets = [];
    act(() => auth.setUser(nextUser));

    await waitFor(() => expect(screen.queryByText('Khi nào tôi được cấp màn hình mới?')).not.toBeInTheDocument());
    expect(screen.queryByLabelText('Phản hồi')).not.toBeInTheDocument();
    expect(await screen.findByText('Không có phiếu nào trong mục này.')).toBeVisible();
  });

  it('role_change_to_user_stops_inbox_and_logout_renders_nothing', async () => {
    renderSupportInbox();
    await screen.findByRole('button', { name: 'Xem câu hỏi' });

    act(() => auth.setUser(staff('USER')));
    expect(screen.queryByRole('button', { name: 'Xem câu hỏi' })).not.toBeInTheDocument();
    expect(await screen.findByText(/không có quyền xem hộp thư hỗ trợ/)).toBeVisible();

    act(() => auth.setUser(null));
    expect(screen.queryByRole('heading', { name: 'Hỗ trợ hỏi đáp' })).not.toBeInTheDocument();
  });

  it('stale_list_response_does_not_fill_new_session', async () => {
    const gate = deferred();
    let first = true;
    server.use(http.get(`${API}/support-tickets/`, async ({ request }) => {
      if (first) {
        first = false;
        await gate.promise;
        return HttpResponse.json(listing([makeTicket({ target_department_name: 'Phòng cũ' })], request));
      }
      return HttpResponse.json(listing([], request));
    }));
    renderSupportInbox();
    await flush();

    act(() => auth.setUser(staff('MANAGER', { department_id: 2 })));
    await screen.findByText('Không có phiếu nào trong mục này.');
    gate.resolve();
    await flush();

    expect(screen.queryByText(/Phòng cũ/)).not.toBeInTheDocument();
  });

  it('stale_filter_response_does_not_replace_newer_list', async () => {
    const gate = deferred();
    server.use(http.get(`${API}/support-tickets/`, async ({ request }) => {
      const status = new URL(request.url).searchParams.get('status');
      if (status === 'OPEN') {
        return HttpResponse.json(listing([makeTicket({ id: 'open', target_department_name: 'Hàng OPEN' })], request));
      }
      if (status === 'ANSWERED') {
        await gate.promise;
        return HttpResponse.json(listing([answered({ id: 'old', target_department_name: 'Hàng cũ' })], request));
      }
      return HttpResponse.json(listing([makeTicket({ id: 'all', target_department_name: 'Hàng tất cả' })], request));
    }));
    const { user } = renderSupportInbox();
    await screen.findByText(/Hàng OPEN/);

    await user.click(screen.getByRole('button', { name: 'Đã phản hồi' }));
    await user.click(screen.getByRole('button', { name: 'Tất cả' }));
    await screen.findByText(/Hàng tất cả/);
    gate.resolve();
    await flush();

    expect(screen.queryByText(/Hàng cũ/)).not.toBeInTheDocument();
    expect(screen.getByText(/Hàng tất cả/)).toBeVisible();
  });

  it('stale_detail_response_does_not_open_ticket_in_new_session_or_over_another', async () => {
    tickets = [
      makeTicket({ id: 'slow', question_snapshot: 'Câu chậm' }),
      makeTicket({ id: 'fast', question_snapshot: 'Câu nhanh' }),
    ];
    const gate = deferred();
    server.use(http.get(`${API}/support-tickets/:id/`, async ({ params }) => {
      if (params.id === 'slow') await gate.promise;
      return HttpResponse.json(tickets.find((ticket) => ticket.id === params.id));
    }));
    const { user } = renderSupportInbox();
    const buttons = await screen.findAllByRole('button', { name: 'Xem câu hỏi' });
    await user.click(buttons[0]);
    await user.click(buttons[1]);
    await screen.findByText('Câu nhanh');

    gate.resolve();
    await flush();

    expect(screen.queryByText('Câu chậm')).not.toBeInTheDocument();
    expect(screen.getByText('Câu nhanh')).toBeVisible();
  });

  it('reply_in_flight_during_session_change_does_not_touch_new_session', async () => {
    const gate = deferred();
    answerReplies(async (body) => {
      await gate.promise;
      return HttpResponse.json(answered({ reply: body.reply }));
    });
    const { user } = renderSupportInbox();
    await user.type(await openFirst(user), 'Phản hồi cũ.');
    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));
    await waitFor(() => expect(replyPosts).toHaveLength(1));

    tickets = [];
    act(() => auth.setUser(staff('MANAGER', { id: 8 })));
    await screen.findByText('Không có phiếu nào trong mục này.');
    gate.resolve();
    await flush();

    const listCalls = listRequests.length;
    await flush();
    expect(screen.queryByText('Phản hồi cũ.')).not.toBeInTheDocument();
    expect(screen.queryByText('Đã trả lời')).not.toBeInTheDocument();
    // The old panel's callback must not reload the list under the new session.
    expect(listRequests).toHaveLength(listCalls);
    expect(listCalls).toBe(2);
  });

  it('conflict_in_old_session_does_not_refetch_under_new_session', async () => {
    const gate = deferred();
    let details = 0;
    server.use(http.get(`${API}/support-tickets/:id/`, () => {
      details += 1;
      return HttpResponse.json(makeTicket());
    }));
    answerReplies(async () => {
      await gate.promise;
      return HttpResponse.json({ code: 'STALE_VERSION_CONFLICT' }, { status: 409 });
    });
    const { user } = renderSupportInbox();
    await user.type(await openFirst(user), 'Nội dung.');
    await user.click(screen.getByRole('button', { name: 'Gửi phản hồi' }));
    await waitFor(() => expect(replyPosts).toHaveLength(1));
    const before = details;

    tickets = [];
    act(() => auth.setUser(staff('MANAGER', { id: 8 })));
    gate.resolve();
    await flush();

    expect(details).toBe(before);
  });
});

describe('assistantApi support functions', () => {
  it('uses_frozen_paths_and_returns_response_data', async () => {
    const seen = [];
    server.use(http.post(`${API}/support-tickets/:id/reply/`, async ({ request, params }) => {
      seen.push([params.id, await request.json()]);
      return HttpResponse.json(answered());
    }));
    const body = { client_request_id: 'u', expected_version: 1, reply: 'r' };
    tickets = Array.from({ length: 16 }, (_, index) => makeTicket({ id: index ? `t-${index}` : 'ticket-1' }));

    expect((await assistantApi.listSupportTickets()).results).toHaveLength(15);
    expect(listRequests[0]).toEqual({ view: 'inbox', page: '1' });
    await assistantApi.listSupportTickets({ view: 'mine', status: 'OPEN', page: 2 });
    expect(listRequests[1]).toEqual({ view: 'mine', status: 'OPEN', page: '2' });
    expect((await assistantApi.getSupportTicket('ticket-1')).id).toBe('ticket-1');
    expect((await assistantApi.replySupportTicket('ticket-1', body)).status).toBe('ANSWERED');
    expect(seen).toEqual([['ticket-1', body]]);
  });
});
