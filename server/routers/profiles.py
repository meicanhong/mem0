from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth import verify_auth
from db import get_db
from errors import upstream_error
from profile_repository import get_profile_response
from profile_service import refresh_profile

router = APIRouter(prefix="/profiles", tags=["profiles"])


class ProfileRefreshRequest(BaseModel):
    mode: Literal["increase", "full"] = Field("increase", description="Profile refresh mode.")


@router.get("/{user_id}", summary="Get cached user profile")
def get_profile(user_id: str, db: Session = Depends(get_db), _auth=Depends(verify_auth)):
    """Retrieve the cached profile for a user."""
    return get_profile_response(db, user_id)


@router.post("/{user_id}/refresh", summary="Refresh cached user profile")
def refresh_user_profile(
    user_id: str,
    req: ProfileRefreshRequest | None = None,
    _auth=Depends(verify_auth),
):
    """Refresh the cached profile for a user."""
    refresh_req = req or ProfileRefreshRequest()
    try:
        return refresh_profile(user_id=user_id, mode=refresh_req.mode)
    except Exception:
        raise upstream_error()
