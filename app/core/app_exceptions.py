from enum import Enum


class AppExceptionCode(Enum):
    UNAUTHORIZED = 401

class AppException(Exception):
    """
    所有自定义异常的基类
    """
    def __init__(self,code:AppExceptionCode,message: str, cause: Exception | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.cause = cause

class AuthException(AppException):
    """
    认证异常
    """
    def __init__(self, message: str = "认证失败", cause: Exception | None = None):
        super().__init__(AppExceptionCode.UNAUTHORIZED,message=message, cause=cause)