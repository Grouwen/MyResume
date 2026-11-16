# MyResume

一个找工作顾问 agent 的后端。FastAPI 管 HTTP 接口和 SSE 流式输出，LangGraph 编排 agent 循环，Postgres 存会话和 checkpoint。

模型不写在配置文件里。前端每次请求带上 `base_url`、`api_key`、`model_name`，服务端按这几个值建客户端，所以换模型不用重启。

## 现在能做什么

- 流式对话：`POST /api/chat` 返回 SSE，分成 token、工具开始、工具结束、工具报错、完成、错误六种事件
- 多轮记忆：登录用户的图状态落在 Postgres checkpoint 里，thread_id 是 `user_id:session_pk`；同一会话断线重连还能接着聊
- 会话管理：列表、改名、删除、拉历史消息。删会话时会把对应的 checkpoint 一起清掉
- 用户系统：邮箱验证码登录，JWT 七天有效
- 工具、插件、技能三层：基础工具常驻，插件按需加载，技能是 md 文档，由模型自己调 `operate_plug` / `operate_skill` 挂载
- 预算控制：一轮最多 20 次 LLM 决策，到点就不绑工具，让模型汇报进度并问用户要不要继续

## 技术选型

| 层 | 用了什么 |
| --- | --- |
| Web | FastAPI + Uvicorn |
| Agent 编排 | LangGraph（StateGraph + ToolNode） |
| 模型接入 | `init_chat_model`，走 OpenAI 兼容协议 |
| 存储 | PostgreSQL 17，业务表用 SQLAlchemy 2 + psycopg3，checkpoint 用 langgraph-checkpoint-postgres |
| 搜索与抓取 | Tavily |
| 邮件 | aiosmtplib，默认 QQ 邮箱 SMTP |

## 目录

```
app/
├── api/            FastAPI 实例、路由、全局异常处理
├── agent/          工具 / 插件 / 技能三个注册中心，Agent.md 人设
│   ├── core/       ToolRegis、PlugRegis、SkillRegis
│   ├── tool/       常驻工具
│   ├── plug/       插件
│   └── skill/      技能
├── graph/          LangGraph 图：节点、状态、运行时上下文
├── service/        对话、会话、用户
├── repository/     仓储，包一层 SQLAlchemy
├── models/         ORM 模型
├── schema/         请求与响应的结构
├── infrastructure/ 连接池、LLM 工厂、checkpoint
├── config/         各模块配置
├── core/           常量、异常、鉴权、日志
├── main.py         启动入口
prompts/            系统提示词模板（jinja2）
tests/
init_db.py          建表脚本
docker-compose.yml  只起一个 Postgres
```

## 跑起来

需要 Python 3.12 和 Docker。

```bash
uv sync
docker compose up -d postgres
cp .env.example .env
python init_db.py
python -m app.main
```

服务默认监听 `127.0.0.1:20056`。

`.env` 至少要改这几个：

| 变量 | 说明 |
| --- | --- |
| `POSTGRESQL_URL` | 业务库连接串，格式 `postgresql+psycopg://用户:密码@主机:5432/库名` |
| `JWT_SECRET_KEY` | 签发 token 的密钥 |
| `TAVILY_API_KEY` | 搜索和网页抓取用，去 [tavily.com](https://www.tavily.com/) 拿 |
| `EMAIL_SENDER_EMAIL` / `EMAIL_HOST` / `EMAIL_AUTH_CODE` | 发验证码的邮箱和 SMTP 授权码 |

`.env` 已经在 `.gitignore` 里，别把密钥提交上去。

`CHECKPOINT_BACKEND` 可以填 `memory`，本地调试不想连库时用。代价是进程一重启对话历史全没。

Windows 上 `main.py` 会替换成 SelectorEventLoop，因为 psycopg 的异步连接跑在默认的 ProactorEventLoop 上会出问题。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/chat` | SSE 流式对话 |
| GET | `/api/sessions` | 会话列表，`page` / `size` 分页 |
| PATCH | `/api/sessions/{session_pk}` | 改会话名 |
| DELETE | `/api/sessions/{session_pk}` | 删会话，对话进行中会返回 409 |
| GET | `/api/sessions/{session_pk}/messages` | 历史消息，`include_tools` 控制要不要带工具调用 |

对话请求体：

```json
{
  "session_pk": null,
  "user_input": "帮我看看这个仓库",
  "api_key": "sk-...",
  "model_name": "deepseek-chat",
  "base_url": "https://api.deepseek.com/v1"
}
```

`session_pk` 留空就新建一个，返回值里会带上。

SSE 每条事件的 `data` 都是 `{"code": 200, "message": "ok", "data": {...}}` 这个外壳：

| 事件 | data 里有什么 |
| --- | --- |
| `token` | 一个增量文本片段 |
| `tool_start` | 工具名、run_id |
| `tool_end` | 工具名、结果文本、run_id |
| `tool_error` | 工具名、错误信息、run_id |
| `done` | session_pk、need_confirm（是否因为预算用完在等用户确认） |
| `error` | code 2001 加错误信息 |

鉴权走 `Authorization: Bearer <token>`。不带头就当匿名用户，能对话但什么都不落库；会话那四个接口必须登录。

## Agent 是怎么跑的

图里有四个节点：

```
entrance → call_llm → call_tool → finalize ─┐
              ↑                             │
              └─────────────────────────────┘
```

`entrance` 每轮把 `finalize`、`budget_stop`、`turn_llm_steps` 清零。这一步不能省：state 是跨轮持久化的，上一轮置成 True 的标志不复位，下一轮会一直停在「询问」状态。

`call_llm` 拼 system prompt，来源是 `app/agent/Agent.md` 加上 `prompts/main_agent_system_prompt.jinja2` 渲染后的内容，模板里填技能列表、插件列表、已加载的插件。拼完按轮裁历史，默认留最近 8 轮；超过 1000 字符的旧工具结果换成一行占位符，最近 3 条保留原文，模型需要时再调一次。

`call_tool` 用 ToolNode 执行，工具集要等运行时才知道，因为插件可以中途挂上和卸下。

`finalize` 看模型这轮有没有调工具，没有就结束。

两道闸门：`MAX_LLM_STEPS_PER_TURN = 20` 是软上限，到点不再绑工具，改成让模型收尾并问用户；`RECURSION_LIMIT = 80` 是图步数硬上限，兜住异常情况。用户回一句「继续」，下一轮重新拿到额度。

并发上，单个用户最多同时跑 3 个会话，`running_chat` 表记状态，同会话重复发请求会被挡住。

## 加东西

### 加一个基础工具

`app/agent/tool/` 下新建 `.py`，用 `@tool` 装饰，启动时自动扫到。工具名重复会直接报错退出，不会静默覆盖。

工具里拿工作目录走 `runtime.context.agent_work_space`，路径校验用 `app/util/agent_util.py` 里的 `verify_file_path_async`，它会把 `..`、绝对路径这类挡掉。

### 加一个插件

```
app/agent/plug/<插件包名>/__init__.py    # 写 PLUG_META
app/agent/plug/<插件包名>/<工具目录>/     # 放 @tool
```

`PLUG_META` 认五个字段：`name`、`description`、`version`，可选的 `enabled` 和 `tools_path`。`tools_path` 只是换工具代码的位置，插件根目录永远是 `__init__.py` 那一层。

现在只有一个 `github_research` 插件，带 `get_repo_info`、`clone_repo`、`grep_code`、`glob_files` 四个工具。

### 加一个技能

```
app/agent/skill/<技能名>/SKILL.md
```

frontmatter 必须有 `name` 和 `description`，`name` 得和目录名一致，不写或者对不上，服务启动就报错。附属文件放在同目录下，模型用 `operate_skill(name, resource_path="references/xxx.md")` 读，`resource_path` 不能是绝对路径，也不能含 `..`。

现在有一个 `shuorenhua` 技能，做审稿和去 AI 味。

## 还没做完的

- workspace 已经按 `user_id` 分目录，还没做体积限制和定时清理
- 只支持 OpenAI 兼容协议，别的厂商要改 `LLMFactory`
- 工具里的路径校验只拦越界访问，没有进程级沙箱；`clone_repo` 会真的在本地跑 `git`

## 测试

```
python tests/test_turn_budget.py
```

用假 LLM 跑真实图，只验证跨轮预算：每轮额度独立、超限那轮注入收尾指令、用户回「继续」后能重新用工具。不联网，也不需要数据库。