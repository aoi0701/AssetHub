import React, { useCallback, useEffect, useRef, useState } from 'react';
import { MessageSquare, Plus, RefreshCw, Send } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import {
  askQuestion,
  createConversation,
  escalateTurn,
  getTurn,
  listConversations,
  listTurns,
} from '../api/assistantApi';
import Pagination from '../components/Pagination';
import AssistantTurn from '../components/assistant/AssistantTurn';
import { formatAssistantTime } from '../components/assistant/AssistantSources';

const MAX_QUESTION_CHARS = 1000;
const MAX_POLLS = 120;
const SCOPE_CHANGED = 'CONVERSATION_SCOPE_CHANGED';

const EXAMPLES = {
  USER: ['Tôi đang giữ tài sản nào?', 'Quy trình trả tài sản thế nào?', 'Các phiếu của tôi đang ở trạng thái gì?'],
  MANAGER: ['Phòng tôi còn bao nhiêu laptop sẵn sàng?', 'Có phiếu nào đang chờ duyệt không?', 'Quy trình duyệt báo hỏng thế nào?'],
  ADMIN: ['Toàn hệ thống có bao nhiêu tài sản đang bảo trì?', 'Thống kê tài sản theo phòng ban', 'Các vai trò được làm gì?'],
};

const REJECTED_MESSAGES = {
  400: 'Câu hỏi chưa hợp lệ. Vui lòng kiểm tra lại nội dung.',
  403: 'Bạn không có quyền thực hiện thao tác này.',
  404: 'Không tìm thấy hội thoại. Vui lòng tạo hội thoại mới.',
  429: 'Bạn đã gửi quá nhiều câu hỏi. Vui lòng thử lại sau ít phút.',
};

const isScopeChanged = (error) => error.response?.data?.code === SCOPE_CHANGED;

const byCreatedAt = (a, b) => Date.parse(a.created_at) - Date.parse(b.created_at) || 0;

// Turns are listed oldest first, so the newest turn (and any PROCESSING one) is on the last page.
const loadLatestTurns = async (conversationId) => {
  const first = await listTurns(conversationId, 1);
  const lastPage = first.total_pages || 1;
  if (lastPage === 1) return { turns: first.results, page: 1 };
  const last = await listTurns(conversationId, lastPage);
  return { turns: last.results, page: lastPage };
};

const AssistantThread = ({ user, pollIntervalMs }) => {
  const [conversations, setConversations] = useState([]);
  const [conversationList, setConversationList] = useState({ page: 1, totalPages: 1, count: 0, pageSize: 15 });
  const [listLoading, setListLoading] = useState(false);
  const [conversationId, setConversationId] = useState(null);
  const [turns, setTurns] = useState([]);
  const [oldestPage, setOldestPage] = useState(1);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [scopeChanged, setScopeChanged] = useState(false);
  const [notice, setNotice] = useState('');
  const [pollStopped, setPollStopped] = useState(false);
  const [pollTick, setPollTick] = useState(0);

  // Bumped when the visible thread is replaced or this session ends: late responses must not act.
  const generationRef = useRef(0);
  const listRequestRef = useRef(0);
  const composingRef = useRef(false);
  const conversationIdRef = useRef(null);
  conversationIdRef.current = conversationId;

  const isCurrent = (generation) => generation === generationRef.current;

  const mergeTurns = useCallback((incoming) => {
    setTurns((current) => {
      const merged = [...current];
      incoming.forEach((turn) => {
        const index = merged.findIndex((item) => item.id === turn.id);
        if (index === -1) merged.push(turn);
        else merged[index] = turn;
      });
      return merged.sort(byCreatedAt);
    });
  }, []);

  const resetThread = useCallback(() => {
    setTurns([]);
    setOldestPage(1);
    setLoadingOlder(false);
    setPending(null);
    setPollStopped(false);
    setPollTick(0);
  }, []);

  const discardThread = useCallback(() => {
    generationRef.current += 1;
    resetThread();
    setConversations((items) => items.filter((item) => item.id !== conversationIdRef.current));
    setConversationId(null);
    setLoading(false);
    setSending(false);
    setScopeChanged(true);
  }, [resetThread]);

  const loadConversations = useCallback(async (page) => {
    const request = ++listRequestRef.current;
    setListLoading(true);
    try {
      const data = await listConversations(page);
      if (request !== listRequestRef.current) return null;
      setConversations(data.results);
      setConversationList({
        page,
        totalPages: data.total_pages || 1,
        count: data.count ?? data.results.length,
        pageSize: data.page_size || 15,
      });
      setListLoading(false);
      return data;
    } catch {
      if (request !== listRequestRef.current) return null;
      setNotice('Không tải được danh sách hội thoại. Vui lòng thử lại.');
      setListLoading(false);
      return null;
    }
  }, []);

  const openConversation = useCallback(async (id) => {
    const generation = ++generationRef.current;
    setConversationId(id);
    resetThread();
    setNotice('');
    setLoading(true);
    try {
      const loaded = await loadLatestTurns(id);
      if (generation !== generationRef.current) return;
      setTurns(loaded.turns);
      setOldestPage(loaded.page);
      setLoading(false);
    } catch (error) {
      if (generation !== generationRef.current) return;
      if (isScopeChanged(error)) {
        discardThread();
      } else {
        setNotice('Không tải được hội thoại. Vui lòng thử lại.');
        setLoading(false);
      }
    }
  }, [discardThread, resetThread]);

  useEffect(() => {
    const generation = ++generationRef.current;
    loadConversations(1).then((data) => {
      if (generation !== generationRef.current) return;
      if (data?.results.length) openConversation(data.results[0].id);
      else setLoading(false);
    });
    return () => {
      generationRef.current += 1;
      listRequestRef.current += 1;
    };
  }, [loadConversations, openConversation]);

  const processingId = turns.find((turn) => turn.outcome === 'PROCESSING')?.id ?? null;

  const checkProcessing = useCallback(async (id, generation) => {
    try {
      const fresh = await getTurn(id);
      if (generation !== generationRef.current) return;
      mergeTurns([fresh]);
      setPollTick((tick) => tick + 1);
    } catch (error) {
      if (generation !== generationRef.current) return;
      if (isScopeChanged(error)) discardThread();
      else setPollStopped(true);
    }
  }, [discardThread, mergeTurns]);

  useEffect(() => {
    if (!processingId || pollStopped) return undefined;
    if (pollTick >= MAX_POLLS) {
      setPollStopped(true);
      return undefined;
    }
    const generation = generationRef.current;
    const timer = setTimeout(() => checkProcessing(processingId, generation), pollIntervalMs);
    return () => clearTimeout(timer);
  }, [processingId, pollStopped, pollTick, pollIntervalMs, checkProcessing]);

  const applyLatest = (loaded) => {
    mergeTurns(loaded.turns);
    setOldestPage((page) => Math.min(page, loaded.page));
    setPollTick(0);
    setPollStopped(false);
  };

  const handleSendFailure = async (error, request, generation) => {
    const status = error.response?.status;
    const data = error.response?.data;
    if (status === 503 && data?.turn) {
      mergeTurns([data.turn]);
      setPending(null);
      return;
    }
    if (isScopeChanged(error)) {
      setDraft(request.question);
      discardThread();
      return;
    }
    if (status === 409) {
      setPending(null);
      setDraft(request.question);
      setNotice('Hội thoại đang bận hoặc câu hỏi đã được gửi. Đang tải lại nội dung mới nhất.');
      try {
        const loaded = await loadLatestTurns(request.conversationId);
        if (isCurrent(generation)) applyLatest(loaded);
      } catch (lookupError) {
        if (isCurrent(generation) && isScopeChanged(lookupError)) discardThread();
      }
      return;
    }
    if (REJECTED_MESSAGES[status]) {
      setPending(null);
      setDraft(request.question);
      setNotice(REJECTED_MESSAGES[status]);
      return;
    }
    // No clear answer (network or 5xx): the turn may be saved, so look before offering a resend.
    // Not finding it proves nothing, which is why a resend always reuses the same request ID.
    try {
      const loaded = await loadLatestTurns(request.conversationId);
      if (!isCurrent(generation)) return;
      if (loaded.turns.some((turn) => turn.client_request_id === request.id)) {
        applyLatest(loaded);
        setPending(null);
        return;
      }
    } catch (lookupError) {
      if (!isCurrent(generation)) return;
      if (isScopeChanged(lookupError)) {
        setDraft(request.question);
        discardThread();
        return;
      }
    }
    setPending({ ...request, failed: true });
    setNotice('Chưa xác nhận được câu hỏi đã gửi. Bấm “Gửi lại” để thử lại, câu hỏi sẽ không bị tính hai lần.');
  };

  const submit = async (request) => {
    const generation = generationRef.current;
    setSending(true);
    setNotice('');
    setPending({ ...request, failed: false });
    try {
      const turn = await askQuestion(request.conversationId, {
        client_request_id: request.id,
        question: request.question,
      });
      if (!isCurrent(generation)) return;
      mergeTurns([turn]);
      setPending(null);
      setPollTick(0);
      setPollStopped(false);
    } catch (error) {
      if (isCurrent(generation)) await handleSendFailure(error, request, generation);
    } finally {
      if (isCurrent(generation)) setSending(false);
    }
  };

  const threadBusy = loading || sending || scopeChanged || Boolean(pending);
  const locked = threadBusy || Boolean(processingId);

  const send = async () => {
    const question = draft.trim();
    if (locked || !question || question.length > MAX_QUESTION_CHARS) return;

    const generation = generationRef.current;
    let targetId = conversationId;
    setSending(true);
    if (!targetId) {
      try {
        const created = await createConversation();
        if (!isCurrent(generation)) return;
        targetId = created.id;
        setConversationId(created.id);
        loadConversations(1);
      } catch {
        if (isCurrent(generation)) {
          setSending(false);
          setNotice('Không tạo được hội thoại. Vui lòng thử lại.');
        }
        return;
      }
    }
    setDraft('');
    // shortcut: randomUUID needs a secure context (HTTPS or localhost), which every deployment here uses.
    await submit({ id: crypto.randomUUID(), conversationId: targetId, question });
  };

  const handleKeyDown = (event) => {
    if (event.key !== 'Enter' || event.shiftKey) return;
    // Enter confirms the IME candidate while composing Vietnamese; it must not send.
    if (composingRef.current || event.nativeEvent.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    send();
  };

  const startNewConversation = async () => {
    const generation = ++generationRef.current;
    setNotice('');
    setLoading(true);
    try {
      const created = await createConversation();
      if (!isCurrent(generation)) return;
      setConversationId(created.id);
      resetThread();
      setScopeChanged(false);
      setLoading(false);
      loadConversations(1);
    } catch {
      if (isCurrent(generation)) {
        setNotice('Không tạo được hội thoại. Vui lòng thử lại.');
        setLoading(false);
      }
    }
  };

  const loadOlderTurns = async () => {
    const generation = generationRef.current;
    const target = oldestPage - 1;
    setLoadingOlder(true);
    try {
      const data = await listTurns(conversationId, target);
      if (!isCurrent(generation)) return;
      mergeTurns(data.results);
      setOldestPage(target);
      setLoadingOlder(false);
    } catch (error) {
      if (!isCurrent(generation)) return;
      setLoadingOlder(false);
      if (isScopeChanged(error)) discardThread();
      else setNotice('Không tải được các lượt cũ hơn. Vui lòng thử lại.');
    }
  };

  const handleCheckReply = async (turn) => {
    const generation = generationRef.current;
    try {
      const fresh = await getTurn(turn.id);
      if (isCurrent(generation)) {
        mergeTurns([fresh]);
        setNotice('');
      }
    } catch (error) {
      if (!isCurrent(generation)) return;
      if (isScopeChanged(error)) discardThread();
      else setNotice('Chưa kiểm tra được phản hồi. Vui lòng thử lại.');
    }
  };

  const handleEscalate = async (turn) => {
    const generation = generationRef.current;
    try {
      const data = await escalateTurn(turn.id);
      if (isCurrent(generation)) {
        mergeTurns([{ ...turn, ticket: data.ticket ?? data }]);
        setNotice('');
      }
    } catch (error) {
      if (!isCurrent(generation)) return;
      if (isScopeChanged(error)) discardThread();
      else setNotice('Chưa chuyển được yêu cầu hỗ trợ. Vui lòng thử lại.');
    }
  };

  const resumePolling = () => {
    setNotice('');
    setPollTick(0);
    setPollStopped(false);
  };

  const examples = EXAMPLES[user?.role] || [];
  const canSend = !locked && draft.trim().length > 0 && draft.trim().length <= MAX_QUESTION_CHARS;

  return (
    <div className="w-full py-6 px-4 sm:px-6 lg:px-8 min-w-0">
      <div className="pb-4 border-b border-gray-200">
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <MessageSquare className="h-7 w-7 text-blue-600" aria-hidden="true" />
          Hỏi đáp tài sản
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Tra cứu theo đúng phạm vi bạn được xem. Mỗi câu trả lời có căn cứ và thời điểm tra cứu.
        </p>
      </div>

      <div className="mt-4 grid grid-cols-1 lg:grid-cols-[16rem_minmax(0,1fr)] gap-4 min-w-0">
        <nav aria-label="Danh sách hội thoại" className="min-w-0">
          <ul className="flex lg:flex-col gap-2 overflow-x-auto">
            {conversations.map((conversation) => (
              <li key={conversation.id} className="shrink-0">
                <button
                  type="button"
                  disabled={threadBusy}
                  aria-current={conversation.id === conversationId ? 'true' : undefined}
                  onClick={() => openConversation(conversation.id)}
                  className={`w-full rounded-lg border px-3 py-2 text-left text-sm disabled:opacity-60 ${
                    conversation.id === conversationId
                      ? 'border-blue-300 bg-blue-50 text-blue-800'
                      : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50'
                  }`}
                >
                  Hội thoại {formatAssistantTime(conversation.created_at)}
                </button>
              </li>
            ))}
          </ul>
          {conversationList.totalPages > 1 && (
            <Pagination
              currentPage={conversationList.page}
              totalPages={conversationList.totalPages}
              totalCount={conversationList.count}
              pageSize={conversationList.pageSize}
              onPageChange={loadConversations}
              loading={listLoading}
            />
          )}
          {!scopeChanged && (
            <button
              type="button"
              disabled={threadBusy}
              onClick={startNewConversation}
              className="mt-2 inline-flex items-center gap-1 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-60"
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              Hội thoại mới
            </button>
          )}
        </nav>

        <div className="min-w-0 flex flex-col gap-3">
          {scopeChanged && (
            <div role="alert" className="flex flex-col sm:flex-row sm:items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3">
              <p className="text-sm text-amber-900 min-w-0">
                Vai trò hoặc phòng ban của bạn đã thay đổi nên hội thoại cũ không còn dùng được.
              </p>
              <button
                type="button"
                disabled={loading}
                onClick={startNewConversation}
                className="self-start sm:self-auto shrink-0 rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                Tạo hội thoại mới
              </button>
            </div>
          )}

          {notice && (
            <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800 break-words">
              {notice}
            </p>
          )}

          <div role="log" aria-live="polite" aria-label="Nội dung hội thoại" className="min-w-0 space-y-4">
            {oldestPage > 1 && (
              <button
                type="button"
                disabled={loadingOlder}
                onClick={loadOlderTurns}
                className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                Tải lượt cũ hơn
              </button>
            )}
            {turns.map((turn) => (
              <AssistantTurn key={turn.id} turn={turn} onCheckReply={handleCheckReply} onEscalate={handleEscalate} />
            ))}
            {pending && (
              <article className="space-y-2 min-w-0">
                <div className="flex justify-end min-w-0">
                  <p className="max-w-full sm:max-w-[80%] rounded-lg bg-blue-600 px-3 py-2 text-sm text-white whitespace-pre-wrap break-words">
                    {pending.question}
                  </p>
                </div>
                {pending.failed ? (
                  <button
                    type="button"
                    disabled={sending}
                    onClick={() => submit(pending)}
                    className="inline-flex items-center gap-1 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                  >
                    <RefreshCw className="h-4 w-4" aria-hidden="true" />
                    Gửi lại
                  </button>
                ) : (
                  <p className="text-sm text-gray-500">Đang gửi…</p>
                )}
              </article>
            )}
            {processingId && pollStopped && (
              <div className="flex flex-col sm:flex-row sm:items-center gap-2">
                <p className="text-sm text-gray-600">Chưa cập nhật được trạng thái câu hỏi.</p>
                <button
                  type="button"
                  onClick={resumePolling}
                  className="self-start sm:self-auto rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
                >
                  Kiểm tra lại
                </button>
              </div>
            )}
          </div>

          {!turns.length && !pending && !loading && !scopeChanged && examples.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {examples.map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => setDraft(example)}
                  className="max-w-full rounded-full border border-gray-300 bg-white px-3 py-1 text-left text-sm text-gray-700 hover:bg-gray-50 break-words"
                >
                  {example}
                </button>
              ))}
            </div>
          )}

          <form
            className="flex flex-col gap-2 min-w-0"
            onSubmit={(event) => {
              event.preventDefault();
              send();
            }}
          >
            <label htmlFor="assistant-question" className="text-sm font-medium text-gray-700">
              Câu hỏi của bạn
            </label>
            <textarea
              id="assistant-question"
              rows={3}
              maxLength={MAX_QUESTION_CHARS}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={handleKeyDown}
              onCompositionStart={() => {
                composingRef.current = true;
              }}
              onCompositionEnd={() => {
                composingRef.current = false;
              }}
              className="w-full min-w-0 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-gray-500">Enter để gửi, Shift+Enter để xuống dòng.</span>
              <button
                type="submit"
                disabled={!canSend}
                className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                <Send className="h-4 w-4" aria-hidden="true" />
                Gửi
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
};

// Remounting on identity or scope change drops the old thread, draft and pending state together.
const AssetAssistantPage = ({ pollIntervalMs = 2000 }) => {
  const { user } = useAuth();
  if (!user) return null;
  const scopeKey = `${user.id}:${user.role}:${user.department_id ?? 'none'}`;
  return <AssistantThread key={scopeKey} user={user} pollIntervalMs={pollIntervalMs} />;
};

export default AssetAssistantPage;
