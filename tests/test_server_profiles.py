import importlib
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient


@pytest.fixture
def _mock_memory():
    mock_instance = MagicMock()
    with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-key", "ADMIN_API_KEY": "", "AUTH_DISABLED": "true"}):
        with patch("mem0.Memory.from_config", return_value=mock_instance):
            yield mock_instance


@pytest.fixture
def server_main(_mock_memory):
    import server.main as loaded_server_main

    with patch.dict(os.environ, {"ADMIN_API_KEY": "", "AUTH_DISABLED": "true"}):
        importlib.reload(loaded_server_main)
    return loaded_server_main


@pytest.fixture
def client(server_main):
    return TestClient(server_main.app)


def test_get_profile_returns_cached_profile(client, server_main):
    import routers.profiles as profiles_router

    expected = {
        "profile": {
            "user_id": "u1",
            "profile_text": "User likes concise technical answers.",
            "profile_json": {"preferences": ["concise technical answers"]},
            "status": "ready",
            "stale": False,
        }
    }
    with patch.object(profiles_router, "get_profile_response", return_value=expected) as get_profile_response:
        resp = client.get("/profiles/u1")

    assert resp.status_code == 200
    assert resp.json() == expected
    get_profile_response.assert_called_once()


def test_refresh_profile_returns_profile(client, server_main):
    import routers.profiles as profiles_router

    expected = {
        "profile": {
            "user_id": "u1",
            "profile_text": "Fresh profile",
            "profile_json": {},
            "status": "ready",
        },
        "status": "ready",
        "mode": "full",
    }
    with patch.object(profiles_router, "refresh_profile", return_value=expected) as refresh_profile:
        resp = client.post("/profiles/u1/refresh", json={"mode": "full"})

    assert resp.status_code == 200
    assert resp.json() == expected
    refresh_profile.assert_called_once_with(user_id="u1", mode="full")


def test_parse_profile_response_invalid_json_fails(caplog):
    import profile_generator

    with pytest.raises(profile_generator.ProfileGenerationError, match="not valid JSON"):
        profile_generator.parse_profile_response("not a json response")

    assert "profile refresh will fail" in caplog.text


def test_generate_profile_payload_uses_json_response_format():
    import profile_generator

    llm = MagicMock()
    llm.generate_response.return_value = """
    {
      "profile_text": "User likes concise answers.",
      "profile_json": {
        "basic_info": {},
        "preferences": ["concise answers"],
        "work_context": [],
        "stable_facts": [],
        "goals": [],
        "communication_style": []
      }
    }
    """

    result = profile_generator.generate_profile_payload(llm, [{"memory": "User likes concise answers."}])

    assert result["profile_text"] == "User likes concise answers."
    llm.generate_response.assert_called_once()
    assert llm.generate_response.call_args.kwargs["response_format"] == {"type": "json_object"}


def test_generate_profile_payload_retries_invalid_json_once():
    import profile_generator

    llm = MagicMock()
    llm.generate_response.side_effect = [
        "not a json response",
        """
        {
          "profile_text": "User likes concise answers.",
          "profile_json": {
            "basic_info": {},
            "preferences": ["concise answers"],
            "work_context": [],
            "stable_facts": [],
            "goals": [],
            "communication_style": []
          }
        }
        """,
    ]

    result = profile_generator.generate_profile_payload(llm, [{"memory": "User likes concise answers."}])

    assert result["profile_text"] == "User likes concise answers."
    assert llm.generate_response.call_count == 2
    for call in llm.generate_response.call_args_list:
        assert call.kwargs["response_format"] == {"type": "json_object"}


def test_refresh_profile_invalid_llm_response_marks_failed_without_overwriting(monkeypatch):
    import profile_service
    from db import Base
    from models import MemoryProfile

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with testing_session() as db:
        db.add(
            MemoryProfile(
                user_id="u1",
                profile_text="Old profile",
                profile_json={"stable_facts": ["old"]},
                source_memory_count=1,
                source_memory_updated_at=now,
                status="ready",
                last_refreshed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()

    mock_memory = MagicMock()
    mock_memory.get_all.return_value = {
        "results": [
            {
                "id": "mem-2",
                "memory": "New memory",
                "created_at": "2026-06-04T00:00:00+00:00",
                "updated_at": "2026-06-04T00:00:00+00:00",
            }
        ]
    }
    mock_memory.llm.generate_response.return_value = "not a json response"
    monkeypatch.setattr(profile_service, "SessionLocal", testing_session)
    monkeypatch.setattr(profile_service, "get_memory_instance", lambda: mock_memory)

    result = profile_service.refresh_profile("u1", mode="full")

    assert result["status"] == "failed"
    assert result["profile"]["profile_text"] == "Old profile"
    assert result["profile"]["profile_json"] == {"stable_facts": ["old"]}
    assert result["profile"]["stale"] is True
    assert "not valid JSON" in result["error_message"]
    assert mock_memory.llm.generate_response.call_count == 2

    with testing_session() as db:
        row = db.query(MemoryProfile).filter(MemoryProfile.user_id == "u1").one()
        assert row.status == "failed"
        assert row.profile_text == "Old profile"
        assert row.profile_json == {"stable_facts": ["old"]}
        assert "not valid JSON" in row.error_message


def test_refresh_profile_skips_when_no_memories_after_cursor(monkeypatch):
    import profile_service
    from db import Base
    from models import MemoryProfile

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    memory_updated_at = datetime(2026, 6, 4, tzinfo=timezone.utc)
    memories = [
        {
            "id": "mem-1",
            "memory": "User likes concise technical answers.",
            "created_at": memory_updated_at.isoformat(),
            "updated_at": memory_updated_at.isoformat(),
        }
    ]
    with testing_session() as db:
        db.add(
            MemoryProfile(
                user_id="u1",
                profile_text="Cached profile",
                profile_json={"preferences": ["concise technical answers"]},
                source_memory_count=1,
                source_memory_updated_at=memory_updated_at,
                event_cursor_updated_at=memory_updated_at,
                status="ready",
                last_refreshed_at=memory_updated_at,
                created_at=memory_updated_at,
                updated_at=memory_updated_at,
            )
        )
        db.commit()

    mock_memory = MagicMock()
    mock_memory.get_all.return_value = {"results": memories}
    monkeypatch.setattr(profile_service, "SessionLocal", testing_session)
    monkeypatch.setattr(profile_service, "get_memory_instance", lambda: mock_memory)

    result = profile_service.refresh_profile("u1")

    assert result["profile"]["profile_text"] == "Cached profile"
    mock_memory.llm.generate_response.assert_not_called()


def test_refresh_profile_processes_memories_after_cursor(monkeypatch):
    import profile_service
    from db import Base
    from models import MemoryProfile

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    memory_updated_at = datetime(2026, 6, 4, tzinfo=timezone.utc)
    memories = [
        {
            "id": "mem-1",
            "memory": "User likes detailed implementation notes.",
            "created_at": memory_updated_at.isoformat(),
            "updated_at": memory_updated_at.isoformat(),
        }
    ]
    with testing_session() as db:
        db.add(
            MemoryProfile(
                user_id="u1",
                profile_text="Old profile",
                profile_json={"preferences": ["old"]},
                source_memory_count=1,
                source_memory_updated_at=memory_updated_at,
                event_cursor_updated_at=datetime(2026, 6, 3, tzinfo=timezone.utc),
                status="ready",
                last_refreshed_at=memory_updated_at,
                created_at=memory_updated_at,
                updated_at=memory_updated_at,
            )
        )
        db.commit()

    mock_memory = MagicMock()
    mock_memory.get_all.return_value = {"results": memories}
    mock_memory.llm.generate_response.return_value = """
    {
      "profile_text": "User likes detailed implementation notes.",
      "profile_json": {
        "basic_info": {},
        "preferences": ["detailed implementation notes"],
        "work_context": [],
        "stable_facts": [],
        "goals": [],
        "communication_style": []
      }
    }
    """
    monkeypatch.setattr(profile_service, "SessionLocal", testing_session)
    monkeypatch.setattr(profile_service, "get_memory_instance", lambda: mock_memory)

    result = profile_service.refresh_profile("u1")

    assert result["profile"]["profile_text"] == "User likes detailed implementation notes."
    mock_memory.llm.generate_response.assert_called_once()
    with testing_session() as db:
        row = db.query(MemoryProfile).filter(MemoryProfile.user_id == "u1").one()
        saved_cursor = row.event_cursor_updated_at
        if saved_cursor.tzinfo is None:
            saved_cursor = saved_cursor.replace(tzinfo=timezone.utc)
        assert saved_cursor == memory_updated_at


def test_increase_profile_does_not_advance_cursor_when_llm_fails(monkeypatch):
    import profile_service
    from db import Base
    from models import MemoryProfile

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    cursor = datetime(2026, 6, 4, 0, 0, tzinfo=timezone.utc)
    next_updated_at = datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc)
    with testing_session() as db:
        db.add(
            MemoryProfile(
                user_id="u1",
                profile_text="Old profile",
                profile_json={"stable_facts": ["old"]},
                source_memory_count=1,
                source_memory_updated_at=cursor,
                event_cursor_updated_at=cursor,
                status="ready",
                last_refreshed_at=cursor,
                created_at=cursor,
                updated_at=cursor,
            )
        )
        db.commit()

    mock_memory = MagicMock()
    mock_memory.get_all.return_value = {
        "results": [
            {
                "id": "mem-2",
                "memory": "New memory",
                "created_at": next_updated_at.isoformat(),
                "updated_at": next_updated_at.isoformat(),
            }
        ]
    }
    mock_memory.llm.generate_response.return_value = "not a json response"
    monkeypatch.setattr(profile_service, "SessionLocal", testing_session)
    monkeypatch.setattr(profile_service, "get_memory_instance", lambda: mock_memory)

    result = profile_service.refresh_profile("u1", mode="increase")

    assert result["status"] == "failed"
    assert result["profile"]["profile_text"] == "Old profile"
    assert result["profile"]["profile_json"] == {"stable_facts": ["old"]}
    assert result["profile"]["stale"] is True
    assert "not valid JSON" in result["error_message"]
    assert mock_memory.llm.generate_response.call_count == 2

    with testing_session() as db:
        row = db.query(MemoryProfile).filter(MemoryProfile.user_id == "u1").one()
        assert row.status == "failed"
        assert row.profile_text == "Old profile"
        assert row.profile_json == {"stable_facts": ["old"]}
        saved_cursor = row.event_cursor_updated_at
        if saved_cursor.tzinfo is None:
            saved_cursor = saved_cursor.replace(tzinfo=timezone.utc)
        assert saved_cursor == cursor
