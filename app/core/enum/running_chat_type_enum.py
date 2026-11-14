from enum import Enum


class RunningChatTypeEnum(Enum):
    RUNNING = "running"
    DONE = "done"
    INTERRUPTED = "interrupted"
    ERROR = "error"