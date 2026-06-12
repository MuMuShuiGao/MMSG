# MMSG-agent 架构总览

> 本文档基于源码事实绘制，截至 `mmsg/` 当前实现。所有模块路径、类名、函数名都可在仓库中检索到。

## 一、入口与启动模式

入口在 `mmsg/__main__.py`，通过 `pyproject.toml` 暴露的 `mmsg` 命令调用：

| 命令 | 行为 | 调用的函数 |
|------|------|-----------|
| `mmsg serve [--host --port]` | 启动服务端（agent + transport + channel） | `mmsg.app._serve()` |
| `mmsg cli` | 启动 TUI 客户端（连接服务端） | `mmsg.ui.cli.main()` |
| `mmsg --print "问题"` | 单次批处理，跑完就退出 | `mmsg.app._batch()` |

无 `setup` / `init` / `gateway` / `dashboard` 子命令。配置依赖根目录的 `config.toml`（`mmsg/config.py:7` 直接 `open` 读取）。

---

## 二、整体架构图

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          mmsg serve （服务端进程）                        │
│                                                                          │
│   ┌─────────────────┐                                                    │
│   │   QQBotChannel  │  ws/rest                                           │
│   │ channel/qqbot.py│ ─────────► publish_inbound(source="qqbot:openid")  │
│   └─────────────────┘ ◄───────── subscribe_outbound("qqbot:*")           │
│                                                                          │
│   ┌─────────────────┐  TCP 9090                                          │
│   │  TCP server     │ ─────────► publish_inbound(source="ui")            │
│   │ transport/      │ ◄───────── events.subscribe("*")                   │
│   │   server.py     │            (把 AgentBus 事件中继给 TUI)            │
│   └─────────────────┘                                                    │
│                                                                          │
│           │ 入站                                       出站 ▲             │
│           ▼                                                │             │
│   ┌────────────────────────────────────────────────────────┴───────┐    │
│   │                         MessageBus                              │    │
│   │  bus/messagebus.py                                              │    │
│   │   ├─ inbound:  asyncio.Queue[InboundItem]                       │    │
│   │   ├─ outbound: asyncio.Queue[OutboundItem] + dispatch loop      │    │
│   │   └─ events: EventBus  (跨进程可观测事件)                       │    │
│   └─────────────────────────────────────────────────────────────────┘    │
│           │ consume_inbound()                            ▲                │
│           ▼                                              │ publish_outbound│
│   ┌─────────────────────────────────────────────────────┴────────────┐  │
│   │                          AgentLoop                                │  │
│   │  agent/loop.py                                                    │  │
│   │   serve() 长驻消费 → run() 单轮 → reasoner.think()                │  │
│   │                                                                   │  │
│   │   依赖：Reasoner / Memory / Tools / SqliteStore / AgentBus        │  │
│   └───┬─────────────────┬──────────────┬──────────────────────────────┘  │
│       │                 │              │                                 │
│       ▼                 ▼              ▼                                 │
│  ┌─────────┐     ┌────────────┐   ┌────────────┐    ┌──────────────┐    │
│  │Reasoner │     │ Memory     │   │ tools dict │    │ SqliteStore  │    │
│  │reason/  │     │ memory/    │   │  echo /    │    │ storage/     │    │
│  │engine.py│     │  default   │   │  now       │    │ history.db   │    │
│  └────┬────┘     │  engine    │   └────────────┘    └──────────────┘    │
│       │          └────────────┘                                          │
│       │                                                                  │
│       │ chat_stream / intercept / observe                                │
│       ▼                                                                  │
│  ┌──────────────┐    ┌──────────────────────────────┐                    │
│  │OpenAIProvider│    │      AgentBus (EventBus)      │                   │
│  │llm/          │    │  bus/agent.py                 │                   │
│  │  openai_     │    │  Interceptor: BeforeStep,     │                   │
│  │  provider.py │    │               AfterReasoning  │                   │
│  └──────────────┘    │  Observer: BeforeTurn /       │                   │
│                      │            BeforeToolCall /   │                   │
│                      │            AfterToolCall /    │                   │
│                      │            AfterStep /        │                   │
│                      │            AfterTurn          │                   │
│                      └────────────────┬──────────────┘                   │
│                                       │ subscribe("*")                   │
│                                       ▼                                  │
│                      ┌──────────────────────────────┐                    │
│                      │ console_sink （彩色打印）     │                   │
│                      │ + bridge → MessageBus.events  │                   │
│                      │   （转发给 TUI 显示）         │                   │
│                      └──────────────────────────────┘                    │
└──────────────────────────────────────────────────────────────────────────┘
                                       ▲ TCP JSON-lines
                                       │
┌──────────────────────────────────────┴───────────────────────────────────┐
│                          mmsg cli （客户端进程）                          │
│                                                                          │
│   ┌──────────────────┐    ┌──────────────────┐                          │
│   │ Textual ChatApp  │ ◄─►│ EventBus (本地)  │ ◄─► transport/client.py │
│   │ ui/textual/      │    │                  │     双向 relay           │
│   └──────────────────┘    └──────────────────┘                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 三、模块职责（按目录）

### 3.1 `mmsg/__main__.py` — CLI 入口
`argparse` 分发到 `_serve` / `_batch` / `cli_main`。无任何业务逻辑。

### 3.2 `mmsg/app.py` — 启动装配
**这是装配链的核心，没有独立的 bootstrap/ 目录。** 关键函数：

- `_register_plugins()` (`app.py:29`)：硬编码注册 `EchoTool`、`NowTool`、`OpenAIProvider` 到全局 registry。
- `_build_agent()` (`app.py:36`)：创建 `SqliteStore` → 从 registry 实例化 LLM/tools → `create_memory()` → 组装 `AgentLoop`。
- `_start_channels()` (`app.py:51`)：检查 `config.toml` 里有无 qqbot 配置，有则启动 `QQBotChannel`。
- `_serve()` (`app.py:64`)：完整启动流程。
- `_batch()` (`app.py:86`)：跑一次 `agent.run()` 后退出，无 transport / channel。

### 3.3 `mmsg/core/` — 全局基础设施
- `plugin.py`: `Registry` 类 + 两个全局实例 `llm_registry`、`tool_registry`。装饰器风格 `@registry.register("name")`。
- `__init__.py`: `setup_logging()`，同时输出 stdout 和 `mmsg.log` 文件。

### 3.4 `mmsg/config.py` — 配置读取
极简：模块加载时 `open("config.toml")`，提供 `workspace_path()`、`llm()`、`memory_backend()`、`qqbot()` 四个 getter。**无配置文件时直接抛错**，无 fallback。

### 3.5 `mmsg/bus/` — 双总线
- `eventbus.py`: 通用 `EventBus`，支持 `observe`（并行通知）和 `intercept`（顺序改写管道），`Event` 是 Pydantic 模型，可 JSON 序列化。
- `agent.py`: `AgentBus` 继承 `EventBus`，定义 `AgentEvent` 枚举（7 个事件类型，注释里画了 Turn 生命周期）。
- `messagebus.py`: `MessageBus` = inbound/outbound 两条 `asyncio.Queue` + 一个内嵌 `EventBus`。`subscribe_outbound` 用 fnmatch 匹配 source。

**关键设计**：AgentBus 用于推理过程内部事件；MessageBus 用于 channel 与 agent 之间的消息流。两者通过 `app.py:75 _bridge_observable` 单向桥接（agent 事件 → message_bus.events，供 transport 中继给 TUI）。

### 3.6 `mmsg/agent/` — Agent 主体
- `loop.py` (`AgentLoop`): 消息消费循环 + 会话管理 + 持久化。**不懂多轮工具调用**，全权委托给 `Reasoner`。
- `reason/engine.py` (`Reasoner`): 完整 ReAct 循环。从 memory 召回上下文 → 多步 LLM 调用 + 工具执行 → 滑动窗口 + 周期性摘要压缩。`max_window=40` / `llm_input_turns=10` / `summarize_every=5` 写死在 `__init__`。

### 3.7 `mmsg/llm/` — LLM 抽象
- `base.py`: `LLMProvider` 抽象类，OpenAI 风格的 `ChatMessage` / `ToolCall` / `StreamChunk`。
- `openai_provider.py`: 唯一实现。

### 3.8 `mmsg/tools/` — 工具
- `base.py`: `Tool` 抽象类，`schema()` 返回 OpenAI function calling 格式。
- `echo.py` / `now.py`: 两个示例工具。

### 3.9 `mmsg/memory/` — 记忆
- `protocol.py`: `Memory` 抽象（`write` / `recall` / `summarize` / `start_turn` / `end_turn`）。
- `factory.py`: `create_memory()` 根据 `config.toml` 的 `memory.backend` 选择引擎。
- `engines/default/`: 当前唯一实现，双文件持久化（`current_context.md` 近期摘要 + `memory.md` 长期知识）。摘要由 `Reasoner` 触发，LLM 生成 5 字段 JSON。

### 3.10 `mmsg/storage/` — 持久化
`SqliteStore`：`session` + `message` 两张表，`history.db` 在 `workspace_path()` 下。

### 3.11 `mmsg/transport/` — 跨进程传输
- `server.py` (`run_tcp_server`): 监听 TCP，每个客户端订阅 `message_bus.events` 全部事件做中继；客户端发来的 `MESSAGE_INBOUND` 类型注入 inbound 队列，其他事件 echo 回 events bus。
- `client.py` (`connect_to_server`): TUI 端用，双向 relay JSON-lines。

### 3.12 `mmsg/channel/` — 外部消息通道
当前唯一实现 `QQBotChannel`：WebSocket 收 C2C 私聊 → `publish_inbound("qqbot:openid", ...)`；订阅 `subscribe_outbound("qqbot:*")` → REST API 发回。

### 3.13 `mmsg/observability/` — 可观测性
`console_sink.py`: 订阅 `AgentBus` 全部事件，按事件类型彩色打印。

### 3.14 `mmsg/ui/` — TUI 客户端
- `cli.py`: 进程入口，建本地 EventBus + 启动 ChatApp + 连接 transport server。
- `textual/`: Textual 应用，`app.py` / `bridge.py` / `commands.py` / 多个 widgets。

---

## 四、关键设计决策

### 4.1 双总线分离
- **AgentBus**：进程内、推理生命周期事件、纯异步 fanout。
- **MessageBus**：跨 channel 的消息队列 + 可观测事件 relay 通道。

两者职责正交：AgentBus 是"内部脏活"，MessageBus 是"外部接口"。`app.py` 只在两者之间做单向桥接。

### 4.2 Registry 模式 vs Wiring 模式
当前用全局 `Registry`（`core/plugin.py`）做"名字 → 类"的映射，**装饰器自注册**。`_register_plugins()` 是硬编码注册。没有独立的 `wiring.py` 配置层。

### 4.3 进程拓扑
- **服务端**：agent + transport server + channel，单进程。
- **TUI 客户端**：独立进程，通过 TCP 连服务端。

这样 TUI 崩溃不影响 agent；channel（如 QQBot）和 TUI 共用同一个 agent。

### 4.4 Reasoner 与 AgentLoop 分离
`AgentLoop` 只管"消息进出 + 落库"，`Reasoner` 管"多轮推理 + 工具调用 + 上下文窗口"。这是合理的关注点分离 —— 未来想换 ReAct 之外的推理范式（Plan-and-Execute / Reflexion），只需替换 `Reasoner` 实现。

### 4.5 拦截器 vs 观察者
`AgentBus` 区分两类订阅：
- `subscribe_intercept`：顺序执行，可改写 payload，影响推理管道。用于 `BeforeStep` / `AfterReasoning`。
- `subscribe`：并行通知，不改管道。用于 metrics / 日志 / TUI 推送。

这是非常实用的设计，避免了"所有事件都能改管道"导致的不可控副作用。

---

## 五、当前局限

1. **Provider/Tool 注册靠硬编码** —— `_register_plugins()` 写死了三行 `register()`，新增需改 `app.py`。
2. **配置层薄** —— `config.py` 模块加载时直接读文件，无校验、无默认值、无环境变量覆盖。
3. **生命周期管理简陋** —— `_serve()` 是一条直线启动，没有 shutdown 钩子，channel 失败不影响主流程但也无法优雅停机。
4. **Memory 引擎单一** —— 虽有 `factory.py` 和 `engines/` 目录暗示多引擎，但只有 `default` 一种实现。
5. **Reasoner 参数硬编码** —— `max_window=40` / `summarize_every=5` 等写在构造函数里，不接受外部配置。
6. **无主动消息能力** —— 无 proactive loop / 定时任务，agent 只能被动响应入站消息。
7. **无 dashboard / 无 HTTP API** —— 只有 TCP 协议给 TUI 用，无法 curl 调用或集成 Web 前端。

---

## 六、未来演进方向（按优先级）

### 阶段 1：把硬编码挪到配置（小成本高回报）
- **插件自动发现**：扫描 `mmsg.tools` / `mmsg.llm` 子模块，靠装饰器自注册替代 `_register_plugins()` 硬编码。
- **Reasoner 参数下放到 `config.toml`**：`max_steps` / `max_window` / `summarize_every` 通过配置注入。
- **配置层加固**：`config.py` 改 Pydantic Settings，支持环境变量覆盖、默认值、启动时校验。

### 阶段 2：第二个实现倒逼抽象（什么时候做：第二个 channel/provider 出现时）
- **Channel registry**：`_start_channels()` 改成扫描所有已注册 channel 配置，每个 channel 自决定是否启动。这时才有必要单独抽出 `bootstrap/channels.py`。
- **多 LLM provider**：加 Claude / 本地模型时，复用现有 `llm_registry`，不需要新增结构。
- **多 Memory 引擎**：当前 `engines/` 目录已经留好位置，加 `engines/vector/` 即可。

### 阶段 3：生命周期容器（什么时候做：组件 ≥ 8 个、有启停依赖时）
- 引入 `AppRuntime` 类统一管理 `start()` / `shutdown()`，每个组件实现 `Lifecycle` 协议。
- 这时拆 `bootstrap/app.py` / `bootstrap/tools.py` / `bootstrap/channels.py` 才有意义。
- 在此之前，`app.py` 一个文件足够清晰。

### 阶段 4：扩展能力（按需）
- **Proactive loop**：增加定时任务、主动推送，独立 task 跑在 `_serve()` 里，订阅 MessageBus 发出站消息。
- **HTTP API / Dashboard**：FastAPI 暴露会话查询、配置热更、metrics。这时才有必要引入 `mmsg gateway` / `mmsg dashboard` 子命令做单进程拆分。
- **Setup 向导**：交互式生成 `config.toml`，对应 `mmsg setup` 子命令。

### 阶段 5（可能永远不做）：DI 框架 / Wiring 层
当组件数量 ≥ 20 且存在多种部署组合（gateway-only / agent-only / all-in-one）时，才考虑独立的 `wiring.py` 做依赖注入映射。在此之前是过度设计。

---

## 七、读懂代码的推荐顺序

1. `mmsg/__main__.py` —— 1 分钟，理解入口分发。
2. `mmsg/app.py` —— 5 分钟，理解整个启动装配。
3. `mmsg/bus/messagebus.py` + `mmsg/bus/agent.py` —— 10 分钟，理解双总线。
4. `mmsg/agent/loop.py` —— 5 分钟，理解消息消费 + 落库。
5. `mmsg/agent/reason/engine.py` —— 15 分钟，理解 ReAct 主循环 + 滑动窗口。
6. `mmsg/transport/server.py` + `mmsg/channel/qqbot.py` —— 10 分钟，理解一个 channel 怎么接入。

之后的 memory / storage / ui 可以按需深入。

---

*文档生成时间：基于当前仓库快照，源码事实优先。如有差异以源码为准。*
