import React, { useState } from 'react';
import { Loader2 } from 'lucide-react';
import AssistantSources, { formatAssistantTime } from './AssistantSources';

const OUTCOME_NOTES = {
  CLARIFY: 'Cần làm rõ',
  RESTRICTED: 'Ngoài quyền xem',
  OUT_OF_SCOPE: 'Ngoài phạm vi tài sản',
  ERROR: 'Chưa xử lý được câu hỏi này. Bạn có thể hỏi lại hoặc gửi yêu cầu hỗ trợ.',
};

const ActionButton = ({ onAction, children }) => {
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      await onAction();
    } finally {
      setBusy(false);
    }
  };
  return (
    <button
      type="button"
      onClick={run}
      disabled={busy}
      className="inline-flex items-center rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
    >
      {children}
    </button>
  );
};

const TicketStatus = ({ turn, onCheckReply }) => {
  const { ticket } = turn;
  const target = ticket.target_kind === 'DEPARTMENT' ? ticket.target_department_name : 'quản trị viên';
  if (ticket.status === 'ANSWERED') {
    return (
      <section aria-label="Phản hồi người phụ trách" className="mt-3 rounded-lg border border-green-200 bg-green-50 p-3 min-w-0">
        <p className="text-xs font-semibold text-green-800">Phản hồi người phụ trách</p>
        <p className="mt-1 text-sm text-gray-900 whitespace-pre-wrap break-words">{ticket.reply}</p>
        <p className="mt-1 text-xs text-gray-600 break-words">
          {ticket.replied_by_name} · {formatAssistantTime(ticket.replied_at)}
        </p>
      </section>
    );
  }
  return (
    <div className="mt-3 flex flex-col sm:flex-row sm:items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 min-w-0">
      <p className="text-sm text-amber-900 break-words min-w-0">Đã chuyển đến {target}, đang chờ phản hồi.</p>
      <ActionButton onAction={() => onCheckReply(turn)}>Kiểm tra phản hồi</ActionButton>
    </div>
  );
};

const AssistantTurn = ({ turn, onCheckReply, onEscalate }) => {
  const canEscalate = !turn.ticket && (turn.outcome === 'ERROR' || turn.outcome === 'ESCALATED');
  const note = OUTCOME_NOTES[turn.outcome];

  return (
    <article className="space-y-2 min-w-0">
      <div className="flex justify-end min-w-0">
        <p className="max-w-full sm:max-w-[80%] rounded-lg bg-blue-600 px-3 py-2 text-sm text-white whitespace-pre-wrap break-words">
          {turn.question}
        </p>
      </div>
      <div className="max-w-full rounded-lg border border-gray-200 bg-white p-3 min-w-0">
        {turn.outcome === 'PROCESSING' ? (
          <p className="flex items-center gap-2 text-sm text-gray-500">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            <span>Đang tra cứu…</span>
          </p>
        ) : (
          <>
            {note && <p className="mb-1 text-xs font-medium text-gray-500">{note}</p>}
            {turn.answer && <p className="text-sm text-gray-900 whitespace-pre-wrap break-words">{turn.answer}</p>}
            <AssistantSources sources={turn.sources} />
          </>
        )}
        {turn.ticket && <TicketStatus turn={turn} onCheckReply={onCheckReply} />}
        {canEscalate && (
          <div className="mt-3">
            <ActionButton onAction={() => onEscalate(turn)}>Gửi yêu cầu hỗ trợ</ActionButton>
          </div>
        )}
      </div>
    </article>
  );
};

export default AssistantTurn;
