from datetime import datetime, timezone
from typing import Any, Dict, Optional

from models import MemoryProfile
from sqlalchemy.orm import Session


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_profile(db: Session, user_id: str) -> Optional[MemoryProfile]:
    return db.query(MemoryProfile).filter(MemoryProfile.user_id == user_id).one_or_none()


def profile_to_response(row: MemoryProfile) -> Dict[str, Any]:
    stale = row.status in {"refreshing", "failed"}
    return {
        "user_id": row.user_id,
        "profile_text": row.profile_text,
        "profile_json": row.profile_json,
        "status": row.status,
        "stale": stale,
        "source_memory_count": row.source_memory_count,
        "source_memory_updated_at": row.source_memory_updated_at.isoformat()
        if row.source_memory_updated_at
        else None,
        "event_cursor_updated_at": row.event_cursor_updated_at.isoformat() if row.event_cursor_updated_at else None,
        "last_refreshed_at": row.last_refreshed_at.isoformat() if row.last_refreshed_at else None,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_profile_response(db: Session, user_id: str) -> Dict[str, Any]:
    row = get_profile(db, user_id)
    if row is None:
        return {"profile": None, "status": "not_found"}
    return {"profile": profile_to_response(row)}


def mark_profile_refreshing(db: Session, row: Optional[MemoryProfile], user_id: str) -> MemoryProfile:
    now = utcnow()
    if row is None:
        row = MemoryProfile(user_id=user_id, status="refreshing", created_at=now, updated_at=now)
        db.add(row)
    else:
        row.status = "refreshing"
        row.updated_at = now
        row.error_message = None
    db.commit()
    db.refresh(row)
    return row


def save_profile_ready(db: Session, row: MemoryProfile, generated: Dict[str, Any], source_snapshot) -> MemoryProfile:
    now = utcnow()
    row.profile_text = generated["profile_text"]
    row.profile_json = generated["profile_json"]
    row.source_memory_count = source_snapshot.memory_count
    row.source_memory_updated_at = source_snapshot.latest_updated_at
    row.event_cursor_updated_at = source_snapshot.latest_updated_at
    row.status = "ready"
    row.last_refreshed_at = now
    row.updated_at = now
    row.error_message = None
    db.commit()
    db.refresh(row)
    return row


def save_increase_batch_ready(
    db: Session,
    row: MemoryProfile,
    generated: Dict[str, Any],
    event_cursor_updated_at: datetime,
) -> MemoryProfile:
    now = utcnow()
    row.profile_text = generated["profile_text"]
    row.profile_json = generated["profile_json"]
    row.event_cursor_updated_at = event_cursor_updated_at
    row.status = "ready"
    row.last_refreshed_at = now
    row.updated_at = now
    row.error_message = None
    db.commit()
    db.refresh(row)
    return row


def save_profile_source_snapshot(db: Session, row: MemoryProfile, source_snapshot) -> MemoryProfile:
    now = utcnow()
    row.source_memory_count = source_snapshot.memory_count
    row.source_memory_updated_at = source_snapshot.latest_updated_at
    row.status = "ready"
    row.last_refreshed_at = now
    row.updated_at = now
    row.error_message = None
    db.commit()
    db.refresh(row)
    return row


def mark_profile_failed(db: Session, user_id: str, exc: Exception) -> None:
    row = get_profile(db, user_id)
    now = utcnow()
    if row is None:
        row = MemoryProfile(user_id=user_id, status="failed", created_at=now)
        db.add(row)
    row.status = "failed"
    row.updated_at = now
    row.error_message = str(exc)[:2000]
    db.commit()
