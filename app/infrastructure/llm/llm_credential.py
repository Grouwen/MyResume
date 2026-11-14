from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import SecretStr

__all__ = ["LLMCredential"]


@dataclass(frozen=True, slots=True)
class LLMCredential:
    """一次 run 使用的 LLM 凭证。

    生命周期 = 一个请求 / 一次 run，只存在于内存。
    不落库、不进 LangGraph state（否则会被 checkpointer 序列化进 PG）。

    约定：
    - base_url 末尾斜杠会被去掉，方便后续 f"{base_url}/chat/completions" 拼接
    - 禁止出现在日志 / traceback / trace 的 metadata 中，见 __repr__
    """

    base_url: str
    api_key: SecretStr
    model: str

    def __post_init__(self) -> None:
        base_url = (self.base_url or "").strip().rstrip("/")
        model = (self.model or "").strip()

        parts = urlsplit(base_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError(f"base_url 必须是 http(s) 地址，收到: {base_url!r}")
        if not model:
            raise ValueError("model 不能为空")

        raw_key = (
            self.api_key.get_secret_value()
            if isinstance(self.api_key, SecretStr)
            else str(self.api_key)
        )
        if not raw_key.strip():
            raise ValueError("api_key 不能为空")

        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "model", model)
        if not isinstance(self.api_key, SecretStr):
            object.__setattr__(self, "api_key", SecretStr(raw_key))

    @property
    def fingerprint(self) -> str:
        """不可逆短指纹，用于日志 / 用量表 / 限流排查，不泄露 key。"""
        raw = f"{self.base_url}|{self.model}|{self.api_key.get_secret_value()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @property
    def masked_key(self) -> str:
        """仅供前端设置页回显，例如 sk-a***z9。不要写进日志。"""
        key = self.api_key.get_secret_value()
        return "***" if len(key) <= 8 else f"{key[:4]}***{key[-4:]}"

    def __repr__(self) -> str:
        return f"LLMCredential(base_url={self.base_url!r}, model={self.model!r}, key=***)"

    __str__ = __repr__