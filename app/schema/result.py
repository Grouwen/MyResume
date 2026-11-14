from enum import Enum
from typing import Dict, Any

from pydantic import BaseModel, Field


class Result(BaseModel):
    code: int
    message: str
    data: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def build(cls,code: int,
              message: str,
              data: Dict[str, Any] | None = None):
        return cls(code=code,message=message,data=data or {})

    @classmethod
    def ok(cls,data: Dict[str, Any]):
        return cls(code=200,message="ok",data=data)

    @classmethod
    def error(cls,message:str):
        return cls(code=2001,message=message,data={})