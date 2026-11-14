from fastapi import APIRouter, Depends
from fastapi import Request

from app.core.dependencies import get_user_service
from app.schema.user_schema import LoginUserSchema
from app.service.user_service import UserService

user_router = APIRouter(prefix="/api/user", tags=["用户"])

async def get_email(email: str,request: Request,
                    user_service: UserService = Depends(get_user_service)):
    await user_service.send_email_service(email, request)

async def login(login_user_schema: LoginUserSchema,
                user_service: UserService = Depends(get_user_service)):
    await user_service.login(login_user_schema)