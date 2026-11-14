from enum import Enum


class AgentException(Exception):
    """
    所有agent异常的基类
    """
class ToolExceptionType(Enum):
    INVALID_ARGUMENTS  = "参数错误"
    TOOL_RUNNING_ERROR = "工具处理时出错"
    UNKOWN = "未知类型"

class ToolException(AgentException):

    def __init__(self,
                 type:ToolExceptionType,
                 error_message: str,
                 is_retryable: bool,
                 suggestion: str):
        super().__init__(error_message)
        self.type = type
        self.error_message = error_message
        self.is_retryable = is_retryable
        self.suggestion = suggestion