import React, { useEffect, useRef, useState } from 'react';
import { getSupportTicket, replySupportTicket } from '../../api/assistantApi';
import { formatAssistantTime } from './AssistantSources';

const MAX_REPLY_CHARS = 4000;

const REJECTED_MESSAGES = {
  400: 'Phản hồi chưa hợp lệ. Vui lòng kiểm tra lại nội dung.',
  403: 'Bạn không còn quyền xử lý phiếu này.',
  404: 'Bạn không còn quyền xử lý phiếu này.',
};

const FIELD_ID = 'support-ticket-reply';

const SupportTicketPanel = ({ ticket, onReplied }) => {
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState('');

  // One attempt keeps one request ID, so a retry after a lost response cannot reply twice.
  const attemptRef = useRef(null);
  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const handleConflict = async () => {
    attemptRef.current = null;
    try {
      const fresh = await getSupportTicket(ticket.id);
      if (!aliveRef.current) return;
      onReplied(fresh);
      setNotice(fresh.status === 'ANSWERED'
        ? 'Phiếu này đã được xử lý. Nội dung bên dưới là phản hồi đã lưu.'
        : 'Phiếu này đã thay đổi. Vui lòng kiểm tra rồi gửi lại.');
    } catch {
      if (aliveRef.current) setNotice('Phiếu này đã thay đổi nhưng chưa tải được bản mới. Vui lòng bấm Làm mới.');
    }
  };

  const send = async (event) => {
    event.preventDefault();
    const reply = draft.trim();
    if (sending || !reply) return;

    const previous = attemptRef.current;
    const attempt = previous && previous.reply === reply && previous.expected_version === ticket.version
      ? previous
      : { client_request_id: crypto.randomUUID(), expected_version: ticket.version, reply };
    attemptRef.current = attempt;
    setSending(true);
    setNotice('');
    try {
      const saved = await replySupportTicket(ticket.id, attempt);
      attemptRef.current = null;
      // The inbox owner matches by ticket ID, and is itself gone if the session changed.
      onReplied({ ...ticket, ...saved });
      if (aliveRef.current) setDraft('');
    } catch (error) {
      if (!aliveRef.current) return;
      const status = error.response?.status;
      if (status === 409) {
        await handleConflict();
      } else if (REJECTED_MESSAGES[status]) {
        attemptRef.current = null;
        setNotice(REJECTED_MESSAGES[status]);
      } else {
        setNotice('Chưa xác nhận được phản hồi đã gửi. Bấm “Gửi phản hồi” để thử lại, phản hồi sẽ không bị gửi hai lần.');
      }
    } finally {
      if (aliveRef.current) setSending(false);
    }
  };

  const isOpen = ticket.status === 'OPEN';

  return (
    <section aria-label="Chi tiết phiếu hỗ trợ" className="rounded-lg border border-gray-200 bg-white p-4 space-y-3 min-w-0">
      {ticket.target_kind === 'ADMIN' && (
        <p className="text-sm text-gray-600">
          Không có quản lý phòng ban khác tiếp nhận nên phiếu được chuyển đến quản trị viên.
        </p>
      )}

      <div className="min-w-0">
        <p className="text-xs font-semibold text-gray-500">Câu hỏi cần xử lý</p>
        <p className="mt-1 text-sm text-gray-900 whitespace-pre-wrap break-words">{ticket.question_snapshot}</p>
        {ticket.created_at && <p className="mt-1 text-xs text-gray-500">{formatAssistantTime(ticket.created_at)}</p>}
      </div>

      {notice && (
        <p role="alert" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 break-words">
          {notice}
        </p>
      )}

      {isOpen ? (
        <form onSubmit={send} className="flex flex-col gap-2 min-w-0">
          <label htmlFor={FIELD_ID} className="text-sm font-medium text-gray-700">Phản hồi</label>
          <textarea
            id={FIELD_ID}
            rows={4}
            maxLength={MAX_REPLY_CHARS}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            className="w-full min-w-0 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <button
            type="submit"
            disabled={sending || !draft.trim()}
            className="self-start rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            Gửi phản hồi
          </button>
        </form>
      ) : (
        <div className="rounded-lg border border-green-200 bg-green-50 p-3 min-w-0">
          <p className="text-sm font-medium text-green-800">Đã trả lời</p>
          <p className="mt-1 text-xs font-semibold text-green-800">Phản hồi người phụ trách</p>
          <p className="mt-1 text-sm text-gray-900 whitespace-pre-wrap break-words">{ticket.reply}</p>
          <p className="mt-1 text-xs text-gray-600 break-words">
            {ticket.replied_by_name} · {formatAssistantTime(ticket.replied_at)}
          </p>
        </div>
      )}
    </section>
  );
};

export default SupportTicketPanel;
