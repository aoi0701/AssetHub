-- Week 5 assistant tables (matches apps.assistant.models and migration 0001_initial).
-- Additive and re-entrant: applied on top of schema.sql, touches no existing table.
-- Column types mirror Django's SQLite editor; UUIDs are stored as 32 hex characters.

CREATE TABLE IF NOT EXISTS assistant_conversation (
    id char(32) NOT NULL PRIMARY KEY,
    owner_id bigint NOT NULL REFERENCES authentication_user(id),
    role_snapshot varchar(20) NOT NULL,
    department_id_snapshot integer NULL,
    created_at datetime NOT NULL,
    updated_at datetime NOT NULL,
    CONSTRAINT chk_assistant_conv_role CHECK (role_snapshot IN ('ADMIN', 'MANAGER', 'USER'))
);
CREATE INDEX IF NOT EXISTS assistant_conv_owner_date
ON assistant_conversation(owner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS assistant_chatturn (
    id char(32) NOT NULL PRIMARY KEY,
    conversation_id char(32) NOT NULL REFERENCES assistant_conversation(id),
    client_request_id char(32) NOT NULL,
    question text NOT NULL,
    outcome varchar(20) NOT NULL,
    answer text NOT NULL,
    sources text NOT NULL CHECK (JSON_VALID(sources) OR sources IS NULL),
    created_at datetime NOT NULL,
    updated_at datetime NOT NULL,
    CONSTRAINT uniq_assistant_turn_request UNIQUE (conversation_id, client_request_id),
    CONSTRAINT chk_assistant_turn_outcome CHECK (
        outcome IN ('PROCESSING', 'ANSWERED', 'CLARIFY', 'ESCALATED', 'RESTRICTED', 'OUT_OF_SCOPE', 'ERROR')
    ),
    CONSTRAINT chk_assistant_turn_question_len CHECK (LENGTH(question) >= 1 AND LENGTH(question) <= 1000),
    CONSTRAINT chk_assistant_turn_answer_len CHECK (LENGTH(answer) <= 6000)
);
CREATE INDEX IF NOT EXISTS assistant_turn_conv_date
ON assistant_chatturn(conversation_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_assistant_turn_processing
ON assistant_chatturn(conversation_id) WHERE outcome = 'PROCESSING';

CREATE TABLE IF NOT EXISTS assistant_supportticket (
    id char(32) NOT NULL PRIMARY KEY,
    turn_id char(32) NOT NULL UNIQUE REFERENCES assistant_chatturn(id),
    target_kind varchar(20) NOT NULL,
    target_department_id bigint NULL REFERENCES departments_department(id),
    question_snapshot text NOT NULL,
    status varchar(20) NOT NULL,
    version integer NOT NULL,
    reply text NOT NULL,
    replied_by_id bigint NULL REFERENCES authentication_user(id),
    replied_at datetime NULL,
    reply_request_id char(32) NULL,
    created_at datetime NOT NULL,
    updated_at datetime NOT NULL,
    CONSTRAINT chk_assistant_ticket_target CHECK (
        (target_kind = 'DEPARTMENT' AND target_department_id IS NOT NULL)
        OR (target_kind = 'ADMIN' AND target_department_id IS NULL)
    ),
    CONSTRAINT chk_assistant_ticket_reply_state CHECK (
        (status = 'OPEN' AND reply = '' AND replied_by_id IS NULL
            AND replied_at IS NULL AND reply_request_id IS NULL)
        OR (status = 'ANSWERED' AND LENGTH(reply) >= 1
            AND replied_by_id IS NOT NULL AND replied_at IS NOT NULL)
    ),
    CONSTRAINT chk_assistant_ticket_snapshot CHECK (
        LENGTH(question_snapshot) >= 1 AND LENGTH(question_snapshot) <= 3000
    ),
    CONSTRAINT chk_assistant_ticket_reply_len CHECK (LENGTH(reply) <= 4000),
    CONSTRAINT chk_assistant_ticket_version CHECK (version >= 1)
);
CREATE INDEX IF NOT EXISTS assistant_ticket_target_date
ON assistant_supportticket(target_kind, target_department_id, status, created_at DESC);
