import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { FileText } from 'lucide-react';
import { getProcedure } from '../../api/assistantApi';

const INTERNAL_HREF = /^\/(my-assets|my-history|my-requests|requests|assets(\/[1-9][0-9]*)?)$/;
const PROCEDURE_TOPICS = ['allocate', 'return', 'damage_report', 'roles', 'asset_statuses'];

const KIND_LABELS = {
  asset: 'Tài sản',
  aggregate: 'Thống kê',
  history: 'Lịch sử',
  request: 'Phiếu yêu cầu',
  procedure: 'Hướng dẫn',
};

const FACT_LABELS = {
  total: 'Tổng số',
  group_by: 'Nhóm theo',
  asset_code: 'Mã tài sản',
  request_code: 'Mã phiếu',
  request_type: 'Loại phiếu',
  status: 'Trạng thái',
  action_type: 'Hành động',
  from_status: 'Trạng thái trước',
  to_status: 'Trạng thái sau',
  created_at: 'Thời điểm',
};

const FILTER_LABELS = {
  query: 'Từ khóa',
  asset_code: 'Mã tài sản',
  category: 'Danh mục',
  statuses: 'Trạng thái',
  department: 'Phòng ban',
  holder: 'Người giữ',
  mine: 'Của chính bạn',
  actions: 'Hành động',
  since: 'Từ',
  until: 'Đến',
  request_type: 'Loại phiếu',
  status: 'Trạng thái',
};

const timeFormatter = new Intl.DateTimeFormat('vi-VN', {
  timeZone: 'Asia/Ho_Chi_Minh',
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

export const formatAssistantTime = (value) => {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? '' : timeFormatter.format(parsed);
};

const displayValue = (value) => {
  if (Array.isArray(value)) return value.join(', ');
  if (value === true) return 'Có';
  return String(value);
};

const Row = ({ label, children }) => (
  <div className="flex flex-col sm:flex-row sm:gap-2 min-w-0">
    <dt className="text-gray-500 shrink-0">{label}:</dt>
    <dd className="text-gray-900 break-words min-w-0">{children}</dd>
  </div>
);

const InternalLink = ({ href, children }) =>
  INTERNAL_HREF.test(href || '') ? (
    <Link to={href} className="text-sm font-medium text-blue-600 hover:text-blue-800 underline">
      {children}
    </Link>
  ) : null;

const ProcedureContent = ({ heading, procedure }) => (
  <div className="space-y-2 min-w-0">
    {heading && <p className="text-xs font-semibold text-gray-600">{heading}</p>}
    <p className="text-sm font-medium text-gray-900 break-words">{procedure.title}</p>
    <p className="text-xs text-gray-500">Phiên bản: {procedure.version}</p>
    <p className="text-sm text-gray-800 whitespace-pre-wrap break-words">{procedure.body}</p>
  </div>
);

const ProcedureDetail = ({ source }) => {
  const stored = source.facts || {};
  const [current, setCurrent] = useState(null);

  useEffect(() => {
    if (!PROCEDURE_TOPICS.includes(stored.topic)) return undefined;
    let active = true;
    getProcedure(stored.topic)
      .then((procedure) => {
        if (active) setCurrent(procedure);
      })
      .catch(() => {
        // The stored snapshot below is still the evidence for this answer.
      });
    return () => {
      active = false;
    };
  }, [stored.topic]);

  const sameVersion = current && current.version === stored.version;
  return (
    <div className="space-y-3 min-w-0">
      <ProcedureContent heading={current && !sameVersion ? 'Căn cứ khi trả lời' : null} procedure={stored} />
      {current && !sameVersion && <ProcedureContent heading="Hướng dẫn hiện tại" procedure={current} />}
      {current ? (
        (current.actions || []).map((action) => (
          <div key={`${action.href}-${action.label}`}>
            <InternalLink href={action.href}>{action.label}</InternalLink>
          </div>
        ))
      ) : (
        <InternalLink href={source.href}>Mở trang liên quan</InternalLink>
      )}
    </div>
  );
};

const FactsDetail = ({ facts = {} }) => (
  <>
    {Object.entries(FACT_LABELS).map(([key, label]) =>
      facts[key] === undefined || facts[key] === null ? null : (
        <Row key={key} label={label}>{key === 'created_at' ? formatAssistantTime(facts[key]) : displayValue(facts[key])}</Row>
      ))}
    {Object.entries(FILTER_LABELS).map(([key, label]) =>
      facts.filters?.[key] === undefined ? null : (
        <Row key={`filter-${key}`} label={`Lọc theo ${label.toLowerCase()}`}>{displayValue(facts.filters[key])}</Row>
      ))}
    {(facts.groups || []).map((group) => (
      <Row key={`group-${group.key}`} label={String(group.key)}>{displayValue(group.count)}</Row>
    ))}
  </>
);

const SourcePanel = ({ source }) => (
  <section
    aria-label={`Căn cứ: ${source.label}`}
    className="mt-2 rounded-lg border border-gray-200 bg-gray-50 p-3 space-y-2 min-w-0"
  >
    <dl className="text-sm space-y-1 min-w-0">
      <Row label="Loại">{KIND_LABELS[source.kind] || 'Khác'}</Row>
      <Row label="Phạm vi">{source.scope_label}</Row>
      <Row label="Thời điểm tra cứu">{formatAssistantTime(source.observed_at)}</Row>
      {source.kind !== 'procedure' && <FactsDetail facts={source.facts} />}
    </dl>
    {source.kind === 'procedure' ? (
      <ProcedureDetail source={source} />
    ) : (
      <InternalLink href={source.href}>Mở trang liên quan</InternalLink>
    )}
  </section>
);

const AssistantSources = ({ sources }) => {
  const [openId, setOpenId] = useState(null);
  if (!sources?.length) return null;
  const opened = sources.find((source) => source.id === openId);

  return (
    <div className="mt-3 min-w-0">
      <div className="flex flex-wrap gap-2">
        {sources.map((source) => (
          <button
            key={source.id}
            type="button"
            aria-expanded={source.id === openId}
            onClick={() => setOpenId(source.id === openId ? null : source.id)}
            className="inline-flex items-center gap-1 max-w-full rounded-full border border-gray-300 bg-white px-2.5 py-1 text-xs text-gray-700 hover:bg-gray-50"
          >
            <FileText className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span className="truncate">{source.label}</span>
          </button>
        ))}
      </div>
      {opened && <SourcePanel key={opened.id} source={opened} />}
    </div>
  );
};

export default AssistantSources;
