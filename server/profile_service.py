import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from db import SessionLocal
from profile_generator import (
    generate_increase_profile_payload,
    generate_profile_payload,
)
from profile_repository import (
    get_profile,
    mark_profile_failed,
    mark_profile_refreshing,
    profile_to_response,
    save_increase_batch_ready,
    save_profile_source_snapshot,
    save_profile_ready,
)
from server_state import get_memory_instance

logger = logging.getLogger(__name__)

PROFILE_MEMORY_LIMIT = int(os.environ.get("MEM0_PROFILE_MEMORY_LIMIT", "200"))
PROFILE_MEMORY_SCAN_LIMIT = int(os.environ.get("MEM0_PROFILE_MEMORY_SCAN_LIMIT", "500"))
PROFILE_INCREASE_BATCH_SIZE = int(os.environ.get("MEM0_PROFILE_INCREASE_BATCH_SIZE", "200"))


@dataclass(frozen=True)
class SourceMemorySnapshot:
    memories: List[Dict[str, Any]]
    memory_count: int
    latest_updated_at: Optional[datetime]


def refresh_profile(user_id: str, mode: str = "increase") -> Dict[str, Any]:
    if mode not in {"increase", "full"}:
        raise ValueError("Profile refresh mode must be 'increase' or 'full'.")

    db = SessionLocal()
    try:
        source_snapshot = load_user_memory_snapshot(user_id)
        row = get_profile(db, user_id)

        if mode == "full":
            row = refresh_profile_full(db, row, user_id, source_snapshot)
        else:
            row, processed_memory_count = refresh_profile_increase(db, row, user_id, source_snapshot)

        logger.info(
            "Profile refresh completed",
            extra={
                "operation": "profile_refresh",
                "status": "ready",
                "user_id": user_id,
                "mode": mode,
                "source_memory_count": source_snapshot.memory_count,
            },
        )
        response = {"profile": profile_to_response(row), "status": "ready", "mode": mode}
        if mode == "increase":
            response["processed_memory_count"] = processed_memory_count
        return response
    except Exception as exc:
        db.rollback()
        try:
            mark_profile_failed(db, user_id, exc)
        except Exception:
            db.rollback()
            logger.exception("Failed to mark profile refresh as failed", extra={"user_id": user_id})
        raise
    finally:
        db.close()


def refresh_profile_full(db, row, user_id: str, source_snapshot: SourceMemorySnapshot):
    row = mark_profile_refreshing(db, row, user_id)
    memories_for_profile = select_profile_source_memories(source_snapshot.memories)
    if not memories_for_profile:
        generated = {"profile_text": "", "profile_json": {}}
    else:
        generated = generate_profile_payload(get_memory_instance().llm, memories_for_profile)
    return save_profile_ready(db, row, generated, source_snapshot)


def refresh_profile_increase(db, row, user_id: str, source_snapshot: SourceMemorySnapshot):
    row = mark_profile_refreshing(db, row, user_id)
    pending_memories = select_increase_memories(source_snapshot.memories, row.event_cursor_updated_at)
    processed_memory_count = 0

    if not pending_memories:
        row = save_profile_source_snapshot(db, row, source_snapshot)
        return row, processed_memory_count

    for batch in chunk_memories(pending_memories, PROFILE_INCREASE_BATCH_SIZE):
        current_profile = {"profile_text": row.profile_text or "", "profile_json": row.profile_json or {}}
        generated = generate_increase_profile_payload(get_memory_instance().llm, current_profile, batch)
        batch_cursor = memory_timestamp(batch[-1])
        row = save_increase_batch_ready(db, row, generated, batch_cursor)
        processed_memory_count += len(batch)

    row = save_profile_source_snapshot(db, row, source_snapshot)
    return row, processed_memory_count


def load_user_memory_snapshot(user_id: str) -> SourceMemorySnapshot:
    result = get_memory_instance().get_all(filters={"user_id": user_id}, top_k=PROFILE_MEMORY_SCAN_LIMIT)
    memories = result.get("results", []) if isinstance(result, dict) else result
    memories = [memory for memory in memories if isinstance(memory, dict)]
    return SourceMemorySnapshot(
        memories=memories,
        memory_count=len(memories),
        latest_updated_at=latest_memory_timestamp(memories),
    )


def select_profile_source_memories(memories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(memories, key=memory_sort_key, reverse=True)[:PROFILE_MEMORY_LIMIT]


def select_increase_memories(memories: List[Dict[str, Any]], cursor_updated_at: Optional[datetime]) -> List[Dict[str, Any]]:
    if cursor_updated_at is not None and cursor_updated_at.tzinfo is None:
        cursor_updated_at = cursor_updated_at.replace(tzinfo=timezone.utc)
    sortable_memories = []
    for memory in memories:
        timestamp = memory_timestamp(memory)
        if timestamp is None:
            continue
        if cursor_updated_at is None or timestamp > cursor_updated_at:
            sortable_memories.append((timestamp, memory))
    return [memory for _, memory in sorted(sortable_memories, key=lambda item: (item[0], str(item[1].get("id") or "")))]


def chunk_memories(memories: List[Dict[str, Any]], batch_size: int) -> List[List[Dict[str, Any]]]:
    return [memories[index : index + batch_size] for index in range(0, len(memories), batch_size)]


def memory_timestamp(memory: Dict[str, Any]) -> Optional[datetime]:
    return parse_datetime(memory.get("updated_at") or memory.get("created_at"))


def memory_sort_key(memory: Dict[str, Any]) -> tuple[str, str]:
    timestamp = memory_timestamp(memory)
    timestamp_key = timestamp.isoformat() if timestamp else ""
    return (timestamp_key, str(memory.get("id") or ""))


def parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def latest_memory_timestamp(memories: List[Dict[str, Any]]) -> Optional[datetime]:
    timestamps = []
    for memory in memories:
        parsed = parse_datetime(memory.get("updated_at") or memory.get("created_at"))
        if parsed is not None:
            timestamps.append(parsed)
    return max(timestamps) if timestamps else None
