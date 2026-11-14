import asyncio
import uuid
from asyncio import Task, Queue, Event
from typing import AsyncIterator

import aiofiles
from fastapi.sse import ServerSentEvent
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.agent.core.plug_regis import PlugRegis
from app.agent.core.skill_regis import SkillRegis
from app.agent.core.tool_regis import ToolRegis
from app.agent.main_agent import  main_agent_no_checkpointer
from langgraph.graph.state import CompiledStateGraph
from app.core.constants import AGENT_WORK_SPACE_PATH, PROMPTS_PATH, AGENT_ROOT, PER_USER_MAX_CHAT
from app.core.enum.running_chat_type_enum import RunningChatTypeEnum
from app.core.enum.sse_enum import AgentEnum
from app.core.loger import get_logger
from app.graph.context.main_agent_context import MainAgentContext
from app.graph.state.main_agent.input_state import InputMainAgentState
from app.infrastructure.llm.llm_credential import LLMCredential
from app.infrastructure.llm.llm_factory import LLMFactory
from app.models.running_chat import RunningChat
from app.oper.httpx_oper import HttpxOper
from app.repository.running_chat_pg_repo import RunningChatPgRepo
from app.repository.session_pg_repo import SessionPgRepo
from app.schema.chat_schema import ChatSchema
from app.schema.result import Result
from app.schema.user_schema import UserSchema

logger = get_logger(__name__)

_bg_tasks: set[Task] = set()

class ChatService:
    def __init__(self, llm_factory: LLMFactory,
                 plug_regis:PlugRegis,
                 skill_regis:SkillRegis,
                 tool_regis:ToolRegis,
                 httpx_oper: HttpxOper,
                 session_pg_repo: SessionPgRepo,
                 running_chat_pg_repo: RunningChatPgRepo,
                 pg_session_factory: async_sessionmaker[AsyncSession],
                 main_agent: CompiledStateGraph):
        self._llm_factory = llm_factory
        self._plug_regis = plug_regis
        self._skill_regis = skill_regis
        self._tool_regis = tool_regis
        self._httpx_oper = httpx_oper
        self._session_pg_repo = session_pg_repo
        self._running_chat_pg_repo = running_chat_pg_repo
        self._pg_session_factory = pg_session_factory
        self._main_agent = main_agent

    # todo: 每个用户单独workspace
    async def chat(self,chat_schema:ChatSchema,user_schema:UserSchema)->AsyncIterator[ServerSentEvent]:
        logger.info(f"user:{user_schema.user_id}->session:{chat_schema.session_pk}: {chat_schema.user_input}")
        # thread_id标识：user_id:session_pk
        queue: Queue[ServerSentEvent | None] = Queue()
        isgone = Event()
        user_id = user_schema.user_id
        # 未登录用户标识为-1
        is_login = user_id != -1
        # 有就用，没有就新建
        chat_schema.session_pk = chat_schema.session_pk or uuid.uuid4()
        task = None

        try:
            if is_login:
                # 单用户限制最大对话数
                running_chats = await self._running_chat_pg_repo.list_running_chats(user_id)
                running_session_pks = [chat.session_pk for chat in running_chats]
                if len(running_chats) >= PER_USER_MAX_CHAT:
                    yield ServerSentEvent(
                        event=AgentEnum.ERROR.value,
                        data=Result.error(f"每个用户最多同时对话次数为 {PER_USER_MAX_CHAT}次，请等待对话完毕")
                    )
                    return
                elif chat_schema.session_pk in running_session_pks:
                    yield ServerSentEvent(
                        event=AgentEnum.ERROR.value,
                        data=Result.error(f"当前对话正在处理中，请稍后")
                    )
                    return

            if is_login:
                # 有session_pk就更新没有就新建
                thread_id = f"{user_id}:{chat_schema.session_pk}"
                await self._session_pg_repo.upsert_session(chat_schema.session_pk, user_id, thread_id)
                await self._running_chat_pg_repo.add_running_chat(
                    RunningChat(user_id=user_id, session_pk=chat_schema.session_pk, user_input=chat_schema.user_input)
                )
                await self._running_chat_pg_repo.commit()
                await self._session_pg_repo.close()
            else:
                thread_id = f"{user_id}:{uuid.uuid4().hex}"


            task = asyncio.create_task(
                self._run_agent(thread_id,chat_schema,queue,isgone,
                                self._pg_session_factory,not is_login),
                name=f"thread_id-{thread_id}",
            )

            # 登录用户添加后台任务，未登录则在前台
            if is_login:
                _bg_tasks.add(task)
                task.add_done_callback(_bg_tasks.discard)

            # 返回
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            isgone.set()
            if not is_login and task and not task.done():
                task.cancel()


    async def _run_agent(self,thread_id:str,chat_schema:ChatSchema,queue:Queue,isgone:Event,
                         pg_session_factory:async_sessionmaker[AsyncSession],no_save:bool=False):
        try:
            cred = LLMCredential(
                base_url=chat_schema.base_url,
                api_key=SecretStr(chat_schema.api_key),
                model=chat_schema.model_name,
            )
            llm_model = self._llm_factory.build(cred)

            # 读取内容
            async with aiofiles.open(PROMPTS_PATH/"main_agent_system_prompt.jinja2","r",encoding="utf-8") as f:
                system_prompt_template = await f.read()
            async with aiofiles.open(AGENT_ROOT/"Agent.md","r",encoding="utf-8") as f:
                agent_md_content = await f.read()

            # agent
            input_state = InputMainAgentState(
                user_input=chat_schema.user_input
            )

            context = MainAgentContext(
                agent_work_space=AGENT_WORK_SPACE_PATH,
                agent_md_content=agent_md_content,
                system_prompt_template=system_prompt_template,
                llm_model = llm_model,
                plug_regis=self._plug_regis,
                skill_regis=self._skill_regis,
                tool_regis=self._tool_regis,
                httpx_oper=self._httpx_oper
            )

            config = {"configurable": {"thread_id": thread_id}}

            main_agent = main_agent_no_checkpointer if no_save else self._main_agent

            # yield
            async for event in main_agent.astream_events(input_state, config=config,
                                                         context=context,version="v2"):
                if isgone.is_set():
                    continue
                kind = event["event"]

                if kind == "on_chat_model_stream":
                    token = event["data"]["chunk"].content
                    if token:
                        queue.put_nowait(ServerSentEvent(event=AgentEnum.TOKEN.value,
                                              data=Result.ok({"token": token})))

                elif kind == "on_tool_start":
                    queue.put_nowait(ServerSentEvent(event=AgentEnum.TOOL_START.value,
                                          data=Result.ok({
                                              "tool_name": event['name'],
                                              "run_id": event['run_id'],
                                          })))

                elif kind == "on_tool_error":
                    exc = event["data"]["error"]
                    msg = getattr(exc, "error_message", None) or str(exc)
                    queue.put_nowait(ServerSentEvent(event=AgentEnum.TOOL_ERROR.value,
                                          data=Result.ok({
                                              "tool_name": event['name'],
                                              "msg": msg,
                                              "run_id": event['run_id']
                                          })))

                elif kind == "on_tool_end":
                    tool_result = event['data']["output"]
                    msg = tool_result.content if hasattr(tool_result, "content") else str(tool_result.update.get("messages", ""))
                    queue.put_nowait(ServerSentEvent(event=AgentEnum.TOOL_END.value,
                                          data=Result.ok({
                                              "tool_name": event['name'],
                                              "msg": msg,
                                              "run_id": event['run_id']
                                          })))

                elif kind == "on_chain_end" and event.get("name") == "LangGraph":
                    queue.put_nowait(ServerSentEvent(event=AgentEnum.DONE.value,
                                          data=Result.ok({
                                              "session_pk":chat_schema.session_pk
                                          })))
                    break

            if not no_save:
                async with pg_session_factory.begin() as session:
                    running_chat_pg_repo = RunningChatPgRepo(session)
                    await running_chat_pg_repo.update_running_chat_status(chat_schema.session_pk, RunningChatTypeEnum.DONE)

        except Exception as e:
            logger.error(f"chat 流式处理失败：{e}")
            if not isgone.is_set():
                queue.put_nowait(
                    ServerSentEvent(
                        event=AgentEnum.ERROR.value,
                        data=Result.error(f"错误：{type(e).__name__}: {e}"),
                    )
                )
            if not no_save:
                async with pg_session_factory.begin() as session:
                    running_chat_pg_repo = RunningChatPgRepo(session)
                    await running_chat_pg_repo.update_running_chat_status(chat_schema.session_pk,RunningChatTypeEnum.ERROR)
        finally:
            queue.put_nowait(None)