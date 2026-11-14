from enum import Enum


class AgentEnum(Enum):
    TOKEN = "token"
    TOOL_START = "tool_start"
    TOOL_ERROR = "tool_error"
    TOOL_END = "tool_end"
    DONE = "done"
    ERROR = "error"
