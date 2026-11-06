import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Inbox, RefreshCw } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { getSupportTicket, listSupportTickets } from '../api/assistantApi';
import Pagination from '../components/Pagination';
import SupportTicketPanel from '../components/assistant/SupportTicketPanel';
import { formatAssistantTime } from '../components/assistant/AssistantSources';

const STAFF_ROLES = ['ADMIN', 'MANAGER'];
const NOT_ALLOWED = 'Bạn không có quyền xem hộp thư hỗ trợ.';
const FILTERS = [
  { status: 'OPEN', label: 'Cần xử lý' },
  { status: 'ANSWERED', label: 'Đã phản hồi' },
  { status: undefined, label: 'Tất cả' },
];
const STATUS_LABELS = { OPEN: 'Đang chờ', ANSWERED: 'Đã trả lời' };

const Header = () => (
  <div className="pb-4 border-b border-gray-200">
    <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
      <Inbox className="h-7 w-7 text-blue-600" aria-hidden="true" />
      Hỗ trợ hỏi đáp
    </h1>
    <p className="text-sm text-gray-500 mt-1">
      Câu hỏi được chuyển đến bạn. Người hỏi đọc phản hồi ngay trong hội thoại của họ.
    </p>
  </div>
);

const SupportInbox = () => {
  const [tickets, setTickets] = useState([]);
  const [meta, setMeta] = useState({ page: 1, totalPages: 1, count: 0, pageSize: 15 });
  const [status, setStatus] = useState('OPEN');
  const [loading, setLoading] = useState(true);
  const [notAllowed, setNotAllowed] = useState(false);
  const [notice, setNotice] = useState('');
  const [selected, setSelected] = useState(null);

  // Only the newest request of each kind may write state; this instance dies with the session.
  const listRequestRef = useRef(0);
  const detailRequestRef = useRef(0);
  const pendingDetailIdRef = useRef(null);
  const aliveRef = useRef(true);
  // Read by reply callbacks, which may outlive the render that started them.
  const viewRef = useRef({});
  viewRef.current = { status, page: meta.page, tickets };

  const loadList = useCallback(async ({ page, nextStatus }) => {
    const request = ++listRequestRef.current;
    setLoading(true);
    setNotice('');
    try {
      const data = await listSupportTickets({ status: nextStatus, page });
      if (request !== listRequestRef.current) return;
      if (!data.results.length && page > 1) {
        loadList({ page: page - 1, nextStatus });
        return;
      }
      setTickets(data.results);
      setMeta({
        page,
        totalPages: data.total_pages || 1,
        count: data.count ?? data.results.length,
        pageSize: data.page_size || 15,
      });
    } catch (error) {
      if (request !== listRequestRef.current) return;
      const failure = error.response?.status;
      // The page can vanish once a reply shrinks the list; each step lowers the page, so this ends.
      if (failure === 404 && page > 1) loadList({ page: page - 1, nextStatus });
      else if ([403, 404].includes(failure)) setNotAllowed(true);
      else setNotice('Không tải được hộp thư hỗ trợ. Vui lòng thử lại.');
    } finally {
      if (request === listRequestRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    loadList({ page: 1, nextStatus: 'OPEN' });
    return () => {
      aliveRef.current = false;
      listRequestRef.current += 1;
      detailRequestRef.current += 1;
    };
  }, [loadList]);

  const openTicket = async (id) => {
    const request = ++detailRequestRef.current;
    pendingDetailIdRef.current = id;
    setNotice('');
    try {
      const ticket = await getSupportTicket(id);
      if (request === detailRequestRef.current) setSelected(ticket);
    } catch {
      if (request === detailRequestRef.current) {
        setSelected(null);
        setNotice('Không mở được phiếu này hoặc bạn không còn quyền xem.');
      }
    }
  };

  const changeFilter = (nextStatus) => {
    detailRequestRef.current += 1;
    setStatus(nextStatus);
    setSelected(null);
    loadList({ page: 1, nextStatus });
  };

  const refresh = () => {
    loadList({ page: meta.page, nextStatus: status });
    if (selected) openTicket(selected.id);
  };

  const handleReplied = useCallback((ticket) => {
    if (!aliveRef.current) return;
    // A read of this ticket started before the save must not bring back the older copy.
    if (pendingDetailIdRef.current === ticket.id) detailRequestRef.current += 1;
    setSelected((current) => (current?.id === ticket.id ? ticket : current));

    const { status: filter, page, tickets: shown } = viewRef.current;
    if (filter && ticket.status !== filter) {
      if (shown.some((item) => item.id === ticket.id)) {
        setMeta((current) => ({ ...current, count: Math.max(0, current.count - 1) }));
      }
      setTickets((items) => items.filter((item) => item.id !== ticket.id));
    } else {
      setTickets((items) => items.map((item) => (item.id === ticket.id ? { ...item, ...ticket } : item)));
    }
    // Replaces any list response still in flight: it was read before this save.
    loadList({ page, nextStatus: filter });
  }, [loadList]);

  if (notAllowed) return <p role="alert" className="mt-4 text-sm text-gray-700">{NOT_ALLOWED}</p>;

  return (
    <div className="mt-4 flex flex-col gap-4 min-w-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div role="group" aria-label="Lọc theo trạng thái" className="flex flex-wrap gap-2">
          {FILTERS.map((filter) => (
            <button
              key={filter.label}
              type="button"
              aria-pressed={status === filter.status}
              onClick={() => changeFilter(filter.status)}
              className={`rounded-full border px-3 py-1 text-sm ${
                status === filter.status
                  ? 'border-blue-300 bg-blue-50 text-blue-800'
                  : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50'
              }`}
            >
              {filter.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={refresh}
          className="inline-flex items-center gap-1 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
          Làm mới
        </button>
      </div>

      {notice && (
        <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800 break-words">
          {notice}
        </p>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-4 min-w-0">
        <div className="min-w-0">
          {!loading && tickets.length === 0 && !notice && (
            <p className="text-sm text-gray-500">Không có phiếu nào trong mục này.</p>
          )}
          <ul className="space-y-2 min-w-0">
            {tickets.map((ticket) => (
              <li
                key={ticket.id}
                className={`flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 rounded-lg border p-3 min-w-0 ${
                  selected?.id === ticket.id ? 'border-blue-300 bg-blue-50' : 'border-gray-200 bg-white'
                }`}
              >
                <div className="min-w-0 text-sm">
                  <p className="font-medium text-gray-900 break-words">
                    {ticket.target_kind === 'ADMIN' ? 'Quản trị viên' : ticket.target_department_name}
                  </p>
                  <p className="text-xs text-gray-500">
                    <span>{STATUS_LABELS[ticket.status] || 'Không rõ'}</span>
                    {ticket.created_at && <span> · {formatAssistantTime(ticket.created_at)}</span>}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => openTicket(ticket.id)}
                  className="self-start sm:self-auto shrink-0 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
                >
                  Xem câu hỏi
                </button>
              </li>
            ))}
          </ul>
          {meta.totalPages > 1 && (
            <Pagination
              currentPage={meta.page}
              totalPages={meta.totalPages}
              totalCount={meta.count}
              pageSize={meta.pageSize}
              onPageChange={(page) => loadList({ page, nextStatus: status })}
              loading={loading}
            />
          )}
        </div>

        <div className="min-w-0">
          {selected && <SupportTicketPanel key={selected.id} ticket={selected} onReplied={handleReplied} />}
        </div>
      </div>
    </div>
  );
};

const AssistantSupportPage = () => {
  const { user } = useAuth();
  if (!user) return null;
  // Remounting on identity or scope change drops tickets, the open ticket and any draft together.
  const scopeKey = `${user.id}:${user.role}:${user.department_id ?? 'none'}`;
  return (
    <div className="w-full py-6 px-4 sm:px-6 lg:px-8 min-w-0">
      <Header />
      {STAFF_ROLES.includes(user.role) ? (
        <SupportInbox key={scopeKey} />
      ) : (
        <p role="alert" className="mt-4 text-sm text-gray-700">{NOT_ALLOWED}</p>
      )}
    </div>
  );
};

export default AssistantSupportPage;
