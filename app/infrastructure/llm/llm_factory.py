from httpx import AsyncClient
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.infrastructure.httpx_regis import HttpxRegistry
from app.infrastructure.llm.llm_credential import LLMCredential


class LLMFactory:
    def __init__(self, httpx_client: AsyncClient) -> None:
        self._httpx_client = httpx_client

    def build(self, cred: LLMCredential, *, streaming: bool = True) -> BaseChatModel:
        chat_model = init_chat_model(
            model=cred.model,
            model_provider="openai",
            api_key=cred.api_key,
            base_url=cred.base_url,
            temperature=0,
            streaming=streaming,
            http_async_client=self._httpx_client
        )

        return chat_model