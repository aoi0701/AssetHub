"""Task 1 schema: PostgreSQL constraints, then D1 DDL parity on a temporary SQLite file."""
import json
import re
import sqlite3
import uuid
from pathlib import Path

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.db.utils import ConnectionHandler
from django.utils import timezone

from apps.assets.models import Asset, AssetHistoryLog
from apps.assistant.models import ChatTurn, Conversation, SupportTicket

D1_SQL_DIR = Path(__file__).resolve().parents[2] / "assethub_api" / "backends" / "d1" / "sql"
ASSISTANT_TABLES = ("assistant_conversation", "assistant_chatturn", "assistant_supportticket")


def _turn(conversation, outcome="ESCALATED", **fields):
    fields.setdefault("client_request_id", uuid.uuid4())
    fields.setdefault("question", "Tôi đang giữ tài sản nào?")
    return ChatTurn.objects.create(conversation=conversation, outcome=outcome, **fields)


def _ticket(turn, **fields):
    fields.setdefault("target_kind", "ADMIN")
    fields.setdefault("question_snapshot", turn.question)
    return SupportTicket.objects.create(turn=turn, **fields)


def _assert_rejected(create, *args, **kwargs):
    with pytest.raises(IntegrityError), transaction.atomic():
        create(*args, **kwargs)


def test_turn_request_id_is_unique_per_conversation(conversation, request_id, regular_user):
    ChatTurn.objects.create(conversation=conversation, client_request_id=request_id,
                            question="Tôi đang giữ tài sản nào?", outcome="ANSWERED")
    with pytest.raises(IntegrityError), transaction.atomic():
        ChatTurn.objects.create(conversation=conversation, client_request_id=request_id,
                                question="Câu khác", outcome="ANSWERED")

    other = Conversation.objects.create(owner=regular_user, role_snapshot=regular_user.role)
    assert _turn(other, "ANSWERED", client_request_id=request_id).pk


def test_only_one_processing_turn_per_conversation(conversation, regular_user):
    first = _turn(conversation, "PROCESSING")
    _assert_rejected(_turn, conversation, "PROCESSING")

    other = Conversation.objects.create(owner=regular_user, role_snapshot=regular_user.role)
    assert _turn(other, "PROCESSING").pk

    first.outcome = "ANSWERED"
    first.save(update_fields=["outcome"])
    assert _turn(conversation, "PROCESSING").pk


def test_ticket_is_unique_per_turn_and_reply_state_is_consistent(conversation, manager_user, dept_it):
    turn = _turn(conversation)
    ticket = _ticket(turn, target_kind="DEPARTMENT", target_department=dept_it)
    assert (ticket.status, ticket.version, ticket.reply) == ("OPEN", 1, "")
    assert turn.ticket == ticket
    _assert_rejected(_ticket, turn)

    now = timezone.now()
    answered = dict(status="ANSWERED", reply="Đã xử lý", replied_by=manager_user, replied_at=now)
    for missing in ({"reply": ""}, {"replied_by": None}, {"replied_at": None}):
        _assert_rejected(_ticket, _turn(conversation), **{**answered, **missing})
    assert _ticket(_turn(conversation), **answered).pk

    for premature in (
        {"reply": "Sớm"},
        {"replied_by": manager_user},
        {"replied_at": now},
        {"reply_request_id": uuid.uuid4()},
    ):
        _assert_rejected(_ticket, _turn(conversation), **premature)


def test_ticket_target_matches_kind(conversation, dept_it):
    _assert_rejected(_ticket, _turn(conversation), target_kind="DEPARTMENT")
    _assert_rejected(_ticket, _turn(conversation), target_kind="ADMIN", target_department=dept_it)
    _assert_rejected(_ticket, _turn(conversation), target_kind="OTHER")
    assert _ticket(_turn(conversation), target_kind="ADMIN").target_department is None


def test_lengths_and_choices_are_enforced_by_database(conversation, regular_user):
    _assert_rejected(Conversation.objects.create, owner=regular_user, role_snapshot="ROOT")
    _assert_rejected(_turn, conversation, "UNKNOWN")
    _assert_rejected(_turn, conversation, question="")
    _assert_rejected(_turn, conversation, question="ơ" * 1001)
    _assert_rejected(_turn, conversation, answer="ơ" * 6001)
    _assert_rejected(_ticket, _turn(conversation), question_snapshot="")
    _assert_rejected(_ticket, _turn(conversation), question_snapshot="ơ" * 3001)
    _assert_rejected(_ticket, _turn(conversation), version=0)
    _assert_rejected(_ticket, _turn(conversation), status="CLOSED")
    # Limits count characters, not UTF-8 bytes.
    assert _turn(conversation, question="ơ" * 1000, answer="ơ" * 6000).pk
    assert _ticket(_turn(conversation), question_snapshot="ơ" * 3000).pk


def test_turn_defaults_keep_empty_answer_and_source_list(conversation):
    turn = _turn(conversation)
    turn.refresh_from_db()
    assert (turn.answer, turn.sources) == ("", [])
    assert isinstance(turn.pk, uuid.UUID)


def test_referenced_rows_are_protected(conversation, regular_user, manager_user, dept_it):
    turn = _turn(conversation)
    ticket = _ticket(turn, target_kind="DEPARTMENT", target_department=dept_it,
                     status="ANSWERED", reply="Xong", replied_by=manager_user, replied_at=timezone.now())
    for referenced in (regular_user, conversation, turn, manager_user, dept_it):
        with pytest.raises(ProtectedError), transaction.atomic():
            referenced.delete()
    assert SupportTicket.objects.filter(pk=ticket.pk).exists()


def test_assistant_graph_matches_plan_totals(assistant_graph):
    graph = assistant_graph
    assert set(graph) == {"admin", "manager_it", "manager_hr", "user_it", "user_hr", "other_it_user",
                          "it", "hr", "laptop", "held_asset"}

    def laptops(department, status):
        return Asset.objects.filter(category=graph["laptop"], department=department, status=status)

    assert laptops(graph["it"], "AVAILABLE").count() == 23
    assert laptops(graph["it"], "IN_USE").count() == 2
    assert laptops(graph["hr"], "AVAILABLE").count() == 2
    assert laptops(graph["hr"], "IN_USE").count() == 1
    assert Asset.objects.filter(department=graph["it"], status="DAMAGED_PENDING_REPAIR").count() == 1
    assert graph["held_asset"].assigned_to_user == graph["user_it"]
    assert Asset.objects.filter(assigned_to_user=graph["other_it_user"]).count() == 1
    assert graph["other_it_user"].full_name == "Nhân viên IT B"

    user_it_actions = set(AssetHistoryLog.objects.filter(to_user=graph["user_it"]).values_list("action_type", flat=True))
    user_it_actions |= set(AssetHistoryLog.objects.filter(from_user=graph["user_it"]).values_list("action_type", flat=True))
    assert user_it_actions == {"ALLOCATE", "RETURN"}
    assert AssetHistoryLog.objects.filter(to_user=graph["other_it_user"]).exists()


# --- D1 DDL -----------------------------------------------------------------------------

def _d1_connection(path):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")  # D1 always enforces foreign keys.
    connection.executescript((D1_SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
    return connection


def _apply_week5(connection):
    connection.executescript((D1_SQL_DIR / "0002_week5_assistant.sql").read_text(encoding="utf-8"))


@pytest.fixture
def django_sqlite(tmp_path, django_db_blocker):
    """The same models created by Django's SQLite editor on a throwaway file, never the default alias."""
    # A private handler: its "default" is this file, unrelated to django.db.connections.
    handler = ConnectionHandler({"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(tmp_path / "dj.sqlite3")}})
    wrapper = handler["default"]
    with django_db_blocker.unblock():
        # atomic=False: an atomic editor would open its transaction on the global "default" alias.
        with wrapper.schema_editor(atomic=False) as editor:
            for model in (Conversation, ChatTurn, SupportTicket):
                editor.create_model(model)
        yield wrapper, tmp_path / "dj.sqlite3"
        wrapper.close()


def _normalize_sql(text):
    return re.sub(r"\s+", " ", text.replace('"', "")).strip().lower()


def _shape(connection, table):
    columns = {row[1]: (row[2].lower(), row[3], row[4], row[5])
               for row in connection.execute(f"PRAGMA table_info({table})")}
    foreign_keys = {(row[3], row[2], row[4], row[6]) for row in connection.execute(f"PRAGMA foreign_key_list({table})")}
    indexes = set()
    for _, name, unique, origin, partial in connection.execute(f"PRAGMA index_list({table})").fetchall():
        keys = tuple((row[2], row[3]) for row in connection.execute(f"PRAGMA index_xinfo({name})") if row[5])
        where = ""
        if partial:
            sql = connection.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()[0]
            where = _normalize_sql(sql.split(" WHERE ", 1)[1])
        indexes.add((name if origin == "c" else None, unique, origin, keys, where))
    return {"columns": columns, "foreign_keys": foreign_keys, "indexes": indexes}


def _check_names(connection, table):
    sql = connection.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()[0]
    return set(re.findall(r'CONSTRAINT "?(\w+)"? CHECK', sql))


def test_ddl_is_additive_and_reentrant(tmp_path):
    connection = _d1_connection(tmp_path / "d1.sqlite3")
    connection.execute("INSERT INTO departments_department (name, code) VALUES ('Sentinel', 'SENT')")
    connection.commit()
    base_objects = connection.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()

    _apply_week5(connection)
    now = "2026-11-05 00:00:00"
    connection.execute(
        "INSERT INTO authentication_user (id, username, email, password, role, full_name, phone, is_superuser,"
        " first_name, last_name, is_staff, is_active, date_joined, auth_version, created_at)"
        " VALUES (1, 'u', 'u@x', 'p', 'USER', '', '', 0, '', '', 0, 1, ?, 1, ?)", (now, now))
    conversation_id = uuid.uuid4().hex
    connection.execute(
        "INSERT INTO assistant_conversation (id, owner_id, role_snapshot, department_id_snapshot, created_at, updated_at)"
        " VALUES (?, 1, 'USER', NULL, ?, ?)", (conversation_id, now, now))
    connection.commit()
    _apply_week5(connection)

    after = dict((row[1], row) for row in connection.execute("SELECT type, name, sql FROM sqlite_master"))
    assert all(after[row[1]] == row for row in base_objects)
    new_objects = set(after) - {row[1] for row in base_objects}
    assert set(ASSISTANT_TABLES) <= new_objects
    assert connection.execute("SELECT name FROM departments_department WHERE code = 'SENT'").fetchone() == ("Sentinel",)
    assert connection.execute("SELECT id FROM assistant_conversation").fetchall() == [(conversation_id,)]
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    statements = [s.strip() for s in re.sub(r"--.*", "", (D1_SQL_DIR / "0002_week5_assistant.sql").read_text()).split(";")]
    for statement in filter(None, statements):
        assert re.match(r"CREATE (UNIQUE )?(TABLE|INDEX) IF NOT EXISTS ", statement), statement


def test_ddl_structure_matches_django_models(tmp_path, django_sqlite):
    wrapper, django_path = django_sqlite
    d1 = _d1_connection(tmp_path / "d1.sqlite3")
    _apply_week5(d1)
    django_db = sqlite3.connect(django_path)

    for table in ASSISTANT_TABLES:
        assert _shape(d1, table) == _shape(django_db, table)
        assert _check_names(d1, table) == _check_names(django_db, table)

    value = uuid.uuid4()
    assert Conversation._meta.pk.get_db_prep_value(value, wrapper) == value.hex
    assert _shape(d1, "assistant_conversation")["columns"]["id"][0] == "char(32)"


def _rows():
    conversation = {"id": "c" * 32, "owner_id": 1, "role_snapshot": "USER", "department_id_snapshot": None,
                    "created_at": "t", "updated_at": "t"}
    turn = {"id": "d" * 32, "conversation_id": "c" * 32, "client_request_id": "e" * 32, "question": "q",
            "outcome": "ANSWERED", "answer": "", "sources": "[]", "created_at": "t", "updated_at": "t"}
    ticket = {"id": "f" * 32, "turn_id": "d" * 32, "target_kind": "ADMIN", "target_department_id": None,
              "question_snapshot": "q", "status": "OPEN", "version": 1, "reply": "", "replied_by_id": None,
              "replied_at": None, "reply_request_id": None, "created_at": "t", "updated_at": "t"}
    return conversation, turn, ticket


ANSWERED = {"status": "ANSWERED", "reply": "r", "replied_by_id": 1, "replied_at": "t"}
INVALID_ROWS = [
    ("assistant_conversation", {"role_snapshot": "ROOT"}),
    ("assistant_chatturn", {"outcome": "UNKNOWN"}),
    ("assistant_chatturn", {"question": ""}),
    ("assistant_chatturn", {"question": "ơ" * 1001}),
    ("assistant_chatturn", {"answer": "ơ" * 6001}),
    ("assistant_chatturn", {"sources": "not-json"}),
    ("assistant_supportticket", {"target_kind": "OTHER"}),
    ("assistant_supportticket", {"target_kind": "DEPARTMENT"}),
    ("assistant_supportticket", {"target_department_id": 1}),
    ("assistant_supportticket", {"status": "CLOSED"}),
    ("assistant_supportticket", {"version": 0}),
    ("assistant_supportticket", {"question_snapshot": ""}),
    ("assistant_supportticket", {"question_snapshot": "ơ" * 3001}),
    ("assistant_supportticket", {**ANSWERED, "reply": "ơ" * 4001}),
    ("assistant_supportticket", {"reply": "r"}),
    ("assistant_supportticket", {"replied_by_id": 1}),
    ("assistant_supportticket", {"replied_at": "t"}),
    ("assistant_supportticket", {"reply_request_id": "a" * 32}),
    ("assistant_supportticket", {**ANSWERED, "reply": ""}),
    ("assistant_supportticket", {**ANSWERED, "replied_by_id": None}),
    ("assistant_supportticket", {**ANSWERED, "replied_at": None}),
]


def _insert(connection, table, row):
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(row.values()))


def _seed_parents(connection):
    now = "2026-11-05 00:00:00"
    connection.execute("INSERT INTO departments_department (id, name, code) VALUES (1, 'IT', 'IT')")
    connection.execute(
        "INSERT INTO authentication_user (id, username, email, password, role, full_name, phone, is_superuser,"
        " first_name, last_name, is_staff, is_active, date_joined, auth_version, created_at)"
        " VALUES (1, 'u', 'u@x', 'p', 'USER', '', '', 0, '', '', 0, 1, ?, 1, ?)", (now, now))


@pytest.mark.parametrize("engine", ["d1", "django"])
def test_ddl_checks_accept_and_reject_same_rows(tmp_path, django_sqlite, engine):
    """The same corpus runs against both schemas, proving CHECK clauses behave equivalently."""
    if engine == "d1":
        connection = _d1_connection(tmp_path / "d1.sqlite3")
        _apply_week5(connection)
        _seed_parents(connection)
    else:
        connection = sqlite3.connect(django_sqlite[1])
    conversation, turn, ticket = _rows()
    _insert(connection, "assistant_conversation", conversation)
    _insert(connection, "assistant_chatturn", turn)
    base = {"assistant_conversation": conversation, "assistant_chatturn": turn, "assistant_supportticket": ticket}

    for table, change in INVALID_ROWS:
        row = {**base[table], "id": uuid.uuid4().hex, **change}
        if table == "assistant_chatturn":
            row["client_request_id"] = uuid.uuid4().hex
        with pytest.raises(sqlite3.IntegrityError):
            _insert(connection, table, row)

    _insert(connection, "assistant_supportticket", {**ticket, **ANSWERED})
    with pytest.raises(sqlite3.IntegrityError):  # OneToOne turn
        _insert(connection, "assistant_supportticket", {**ticket, "id": uuid.uuid4().hex})
    with pytest.raises(sqlite3.IntegrityError):  # unique request ID per conversation
        _insert(connection, "assistant_chatturn", {**turn, "id": uuid.uuid4().hex})
    _insert(connection, "assistant_chatturn",
            {**turn, "id": uuid.uuid4().hex, "client_request_id": uuid.uuid4().hex, "outcome": "PROCESSING"})
    with pytest.raises(sqlite3.IntegrityError):  # one PROCESSING per conversation
        _insert(connection, "assistant_chatturn",
                {**turn, "id": uuid.uuid4().hex, "client_request_id": uuid.uuid4().hex, "outcome": "PROCESSING"})
    _insert(connection, "assistant_chatturn",
            {**turn, "id": uuid.uuid4().hex, "client_request_id": uuid.uuid4().hex,
             "question": "ơ" * 1000, "answer": "ơ" * 6000, "sources": json.dumps([{"type": "asset"}])})


def test_ddl_foreign_keys_protect_references(tmp_path):
    connection = _d1_connection(tmp_path / "d1.sqlite3")
    _apply_week5(connection)
    _seed_parents(connection)
    conversation, turn, ticket = _rows()

    with pytest.raises(sqlite3.IntegrityError):
        _insert(connection, "assistant_conversation", {**conversation, "owner_id": 999})
    _insert(connection, "assistant_conversation", conversation)
    _insert(connection, "assistant_chatturn", turn)
    _insert(connection, "assistant_supportticket",
            {**ticket, "target_kind": "DEPARTMENT", "target_department_id": 1, **ANSWERED})

    for statement in (
        "DELETE FROM authentication_user WHERE id = 1",
        "DELETE FROM departments_department WHERE id = 1",
        "DELETE FROM assistant_conversation",
        "DELETE FROM assistant_chatturn",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(statement)
