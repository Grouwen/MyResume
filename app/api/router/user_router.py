from fastapi import APIRouter, Depends
from fastapi import Request

from app.core.dependencies import get_user_service
from app.schema.result import Result
from app.schema.user_schema import LoginUserSchema
from app.service.user_service import UserService

user_router = APIRouter(prefix="/api/user", tags=["用户router"])

@user_router.get("/send_email/{user_email}", response_model=Result)
async def send_email(user_email: str,request: Request,
                    user_service: UserService = Depends(get_user_service)):
    return await user_service.send_email_service(user_email, request)

@user_router.post("/login", response_model=Result)
async def login(login_user_schema: LoginUserSchema,
                user_service: UserService = Depends(get_user_service)):
    return await user_service.login(login_user_schema)