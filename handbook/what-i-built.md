# MMSG-agent: 我做了什么

## 概述

一个个人 Agent 编排系统，具备事件总线、可观测性、分层记忆和插件架构。

## 架构

```
mmsg/
├── agent/         # Agent 循环 — 核心推理周期
├── bus/           # 事件常量（agent 内部 + message 外部）
├── core/          # 插件系统、日志
├── llm/           # LLM 抽象层（OpenAI provider）
├── memory/        # 分层记忆（工作记忆）
├── router/        # SessionRouter — 桥接外部消息 ↔ agent 内部
├── observability/ # 可观测性（控制台输出）
├── tools/         # 工具基类 + echo 工具
├── transport/     # 客户端/服务端通信
└── ui/            # CLI + Textual TUI（聊天组件）
```

## 核心模块

| 模块 | 作用 |
|------|------|
| `core/bus.py` | EventBus 类 — 发布/订阅基础设施 |
| `bus/agent.py` | Agent 内部事件常量（llm.*, tool.*, loop.* 等） |
| `bus/message.py` | 外部消息事件常量（message.inbound/outbound, session.* 等） |
| `core/plugin.py` | 插件注册与生命周期管理 |
| `router/router.py` | SessionRouter — 桥接 message_bus ↔ agent_bus |
| `agent/loop.py` | Agent 主推理循环 |
| `llm/openai_provider.py` | OpenAI API 接入 |
| `memory/working.py` | 短期工作记忆 |
| `ui/textual/` | 富终端 UI：聊天记录、输入栏、状态栏、工具块 |

## 技术栈

- Python >=3.10
- httpx（异步 HTTP）
- pydantic（数据校验）
- python-dotenv（环境配置）
- textual（终端 UI）
- setuptools（打包）

## 入口

```bash
mmsg serve              # 启动服务端
mmsg cli                # 启动 TUI 客户端
mmsg --print "问题"     # 单次批处理模式
```
入口统一在 `mmsg/__main__.py`，分别调度 `app.py` 服务端/批处理逻辑和 `ui/cli.py` TUI 客户端。
