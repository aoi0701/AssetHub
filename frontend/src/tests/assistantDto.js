// Ticket shapes from spec section 10: mocks share them so a screen cannot rely on a field its endpoint lacks.

const NESTED_FIELDS = [
  'id', 'target_kind', 'target_department_name', 'status', 'version', 'reply', 'replied_by_name', 'replied_at',
];
const LIST_FIELDS = ['id', 'target_kind', 'target_department_name', 'status', 'version', 'created_at', 'updated_at'];

const pick = (source, fields) => Object.fromEntries(fields.map((field) => [field, source[field]]));

export const ticketDetail = (overrides = {}) => ({
  id: 'ticket-1',
  turn_id: 'turn-1',
  target_kind: 'DEPARTMENT',
  target_department_name: 'Phòng IT',
  question_snapshot: 'Khi nào tôi được cấp màn hình mới?',
  status: 'OPEN',
  version: 1,
  reply: '',
  replied_by_name: null,
  replied_at: null,
  created_at: '2026-11-05T03:00:00Z',
  updated_at: '2026-11-05T03:00:00Z',
  ...overrides,
});

export const ticketNested = (detail) => pick(detail, NESTED_FIELDS);
export const ticketListItem = (detail) => pick(detail, LIST_FIELDS);
