from fastapi import FastAPI,Request
from starlette.responses import JSONResponse

from app.core.app_exceptions import AppException, AuthException
from app.core.loger import get_logger

logger = get_logger(__name__)

def register_exception_handlers(app: FastAPI):
    """
    注册全局异常处理器
    """
    @app.exception_handler(AppException)
    async def app_error_handler(request: Request, exc: AppException):
        logger.error("AppError: %s", exc.message, exc_info=True)

        return JSONResponse(status_code=exc.code.value,
                            content={"code": exc.code.value, "msg": exc.message, "data": None})

    @app.exception_handler(AuthException)
    async def app_error_handler(request: Request, exc: AppException):
        logger.info(f"Auth失败，{exc.message}-> {request.headers.get('Authorization')}")

        return JSONResponse(status_code=exc.code.value,
                            content={"code": exc.code.value, "msg": exc.message, "data": None})