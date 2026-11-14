import uuid

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import get_session_service
from app.core.token_auth import require_login
from app.schema.result import Result
from app.schema.session_schema import RenameSessionInput, SessionItem
from app.schema.user_schema import UserSchema
from app.service.session_service import SessionService

session_router = APIRouter(prefix="/api/sessions", tags=["session管理"])


@session_router.get("", response_model=Result)
async def list_sessions(
        page: int = Query(1, ge=1),
        size: int = Query(20, ge=1, le=100),
        session_service: SessionService = Depends(get_session_service),
        user_schema: UserSchema = Depends(require_login),
) -> Result:
    sessions, total = await session_service.list_sessions(user_schema, page, size)
    return Result.ok({
        "items": [
            SessionItem.model_validate(session).model_dump(mode="json")
            for session in sessions
        ],
        "total": total,
        "page": page,
        "size": size,
    })


@session_router.patch("/{session_pk}", response_model=Result)
async def rename_session(
        session_pk: uuid.UUID,
        body: RenameSessionInput,
        session_service: SessionService = Depends(get_session_service),
        user_schema: UserSchema = Depends(require_login),
) -> Result:
    title = await session_service.rename_session(user_schema, session_pk, body.title)
    return Result.ok({"session_pk": str(session_pk), "title": title})


@session_router.delete("/{session_pk}", response_model=Result)
async def delete_session(
        session_pk: uuid.UUID,
        session_service: SessionService = Depends(get_session_service),
        user_schema: UserSchema = Depends(require_login),
) -> Result:
    await session_service.delete_session(user_schema, session_pk)
    return Result.ok({"session_pk": str(session_pk)})


@session_router.get("/{session_pk}/messages", response_model=Result)
async def get_messages(
        session_pk: uuid.UUID,
        limit: int = Query(100, ge=1, le=500),
        include_tools: bool = Query(False),
        session_service: SessionService = Depends(get_session_service),
        user_schema: UserSchema = Depends(require_login),
) -> Result:
    items, total, has_more, updated_at = await session_service.get_messages(
        user_schema, session_pk, limit, include_tools
    )
    return Result.ok({
        "session_pk": str(session_pk),
        "items": [item.model_dump(mode="json") for item in items],
        "total": total,
        "has_more": has_more,
        "updated_at": updated_at,
    })