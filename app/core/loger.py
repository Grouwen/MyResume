import copy
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 统一日志格式
LOG_FORMAT = (
    "%(asctime)s | "
    "%(levelname)s | "
    "%(name)s | "
    "%(message)s"
)

# 终端颜色配置（仅开发环境使用，让日志更易读）
COLORS = {
    "DEBUG": "\033[36m",     # 青色
    "INFO": "\033[32m",      # 绿色
    "WARNING": "\033[33m",   # 黄色
    "ERROR": "\033[31m",     # 红色
    "CRITICAL": "\033[35m",  # 品红
}
RESET = "\033[0m"

def want_color() -> bool:
    """终端是否上色：默认开，需要关时用环境变量显式关掉。

    不再用 isatty() 判断——IDE 运行窗口、tee、CI 都能渲染 ANSI 却不是 TTY，
    判反了就是一整屏没颜色。文件 handler 走纯 Formatter，不受这里影响。
    """
    flag = os.environ.get("LOG_COLOR", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    if flag in {"0", "false", "no", "off"}:
        return False
    return not os.environ.get("NO_COLOR")   # 遵循 NO_COLOR 约定

class ColorFormatter(logging.Formatter):
    """为终端输出添加颜色；只改 LogRecord 的副本，不影响文件 handler。"""

    def __init__(self, fmt=None, datefmt=None, style="%", use_color: bool = True):
        super().__init__(fmt, datefmt, style)
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        color = COLORS.get(record.levelname, "") if self.use_color else ""
        if not color:
            return super().format(record)

        # LogRecord 是共享的：直接改 record.levelname，
        # 会让随后执行的文件 handler 把 ANSI 码一起写进 .log
        colored = copy.copy(record)
        colored.levelname = f"{color}{record.levelname}{RESET}"
        return super().format(colored)


def setup_logging(log_file: Path,
                  level: int = logging.INFO,
                  max_bytes: int = 10 * 1024 * 1024,
                  backup_count: int = 5):
    """
    初始化全局日志配置。
    :param log_file: 日志文件路径
    :param level: 日志等级 DEBUG = 10, INFO = 20, WARNING = 30
    :param max_bytes: 单个日志文件最大字节数（默认 10MB）
    :param backup_count: 保留的历史日志文件数量
    """
    # 准备 handlers
    handlers = []

    # 1. 终端输出（带颜色）
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        ColorFormatter(LOG_FORMAT, use_color=want_color())
    )
    handlers.append(console_handler)

    # 2. 文件输出（自动轮转，防止日志撑爆磁盘）
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handlers.append(file_handler)

    # 强制重新配置（解决 basicConfig 只能生效一次的问题）
    logging.basicConfig(
        level=level,
        handlers=handlers,
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    """获取指定名称的 Logger 实例"""
    return logging.getLogger(name)