# MMSG-agent: 我做了什么

## 概述

一个个人 Agent 编排系统，具备事件总线、可观测性、分层记忆和插件架构。

## 架构

```
mmsg/
├── agent/         # Agent 循环 — 核心推理周期
├── bus/           # 事件总线 + 事件常量（agent 内部 + message 外部）
├── channel/       # 外部 IM 通道适配器（QQBot）
├── core/          # 插件注册表、日志
├── llm/           # LLM 抽象层（ChatMessage 协议 + OpenAI provider）
├── memory/        # 分层记忆（工作记忆 + 后端工厂）
├── observability/ # 可观测性（控制台 sink）
├── tools/         # 工具基类 + echo / now 工具
├── transport/     # TCP 客户端/服务端通信
└── ui/            # CLI + Textual TUI（聊天组件）
```

## 核心模块

| 模块 | 作用 |
|------|------|
| `bus/eventbus.py` | EventBus 类 — intercept() 顺序改写管道 + observe() 并行旁路通知，通配符匹配 |
| `bus/agent.py` | Agent 内部事件常量（AgentEvent）— Interceptor(BeforeStep/AfterReasoning) + Observer(BeforeTurn/BeforeToolCall/AfterToolCall/AfterStep/AfterTurn) |
| `bus/messagebus.py` | 外部消息总线 + 事件常量（message.*, session.*, transport.* 等） |
| `core/plugin.py` | Registry — LLM/Tool 插件注册与按名创建 |
| `agent/loop.py` | AgentLoop — serve() 消费消息队列 + 感知→思考→行动→观察 主循环，流式 LLM + 工具调用 |
| `llm/base.py` | LLM 协议 — ChatMessage, LLMProvider, StreamChunk |
| `llm/openai_provider.py` | OpenAI API 接入（chat + chat_stream） |
| `memory/base.py` | Memory 协议 + LayeredMemory 组合层 |
| `memory/working.py` | WorkingMemory — 定长环形缓冲区短期记忆 |
| `memory/factory.py` | create_memory() — 根据 MEMORY_BACKEND 环境变量选择后端 |
| `memory/backends/builtin.py` | 内置后端：WorkingMemory + 未来 episodic/semantic 层 |
| `channel/qqbot.py` | QQBot 私聊通道 — WS 收消息 → message_bus → REST 发消息 |
| `tools/base.py` | Tool 抽象基类 — JSON Schema 参数 + async run |
| `tools/echo.py` | EchoTool + NowTool 演示工具 |
| `transport/server.py` | TCP JSON-lines 服务端 |
| `transport/client.py` | TCP JSON-lines 客户端 |
| `observability/console_sink.py` | 彩色控制台事件输出，按 Interceptor/Observer 事件类型着色 |
| `ui/textual/` | 富终端 TUI：聊天记录、输入栏、状态栏、工具块 |
| `app.py` | 启动逻辑 — _serve / _batch，注册插件、启动 channel |

## 技术栈

- Python >=3.10
- httpx（异步 HTTP）
- websockets（QQBot WebSocket 连接）
- pydantic（数据校验 + Event 模型）
- python-dotenv（环境配置）
- textual（终端 TUI）
- setuptools（打包）

## 入口

```bash
mmsg serve              # 启动服务端
mmsg cli                # 启动 TUI 客户端
mmsg --print "问题"     # 单次批处理模式
```
入口统一在 `mmsg/__main__.py`，分别调度 `app.py` 服务端/批处理逻辑和 `ui/cli.py` TUI 客户端。
