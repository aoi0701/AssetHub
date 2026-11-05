import uuid

import pytest

from .factories import build_assistant_graph


@pytest.fixture
def assistant_graph(db):
    return build_assistant_graph()


@pytest.fixture
def conversation(regular_user):
    from apps.assistant.models import Conversation

    return Conversation.objects.create(
        owner=regular_user,
        role_snapshot=regular_user.role,
        department_id_snapshot=regular_user.department_id,
    )


@pytest.fixture
def request_id():
    return uuid.uuid4()
