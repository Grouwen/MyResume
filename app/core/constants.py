from pathlib import Path
from typing import Final

# httpx的默认ua
HTTPX_UA:Final[str] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"

# 单个用户最大同时会话数量
PER_USER_MAX_CHAT: Final[int] = 3

# -------------- Path --------------
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

LOG_FILE_PATH: Final[Path] = PROJECT_ROOT / "logs" / "log.log"

PROMPTS_PATH: Final[Path] = PROJECT_ROOT / "prompts"

# -------------- AGENT --------------
AGENT_ROOT: Final[Path] = PROJECT_ROOT / "app" / "agent"

AGENT_WORK_SPACE_PATH: Final[Path] = PROJECT_ROOT / "agent_work_space"

AGENT_MD_FILE_PATH: Final[Path] = AGENT_ROOT / "Agent.md"