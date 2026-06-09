# MMSG-agent: 我做了什么

## 概述

一个个人 Agent 编排系统，具备事件总线、可观测性、分层记忆和插件架构。

## 架构

```
mmsg/
├── agent/         # Agent 循环 — 核心推理周期
├── core/          # 事件总线、事件定义、插件系统
├── llm/           # LLM 抽象层（OpenAI provider）
├── memory/        # 分层记忆（工作记忆）
├── observability/ # 可观测性（控制台输出）
├── tools/         # 工具基类 + echo 工具
├── transport/     # 客户端/服务端通信
└── ui/            # CLI + Textual TUI（聊天组件）
```

## 核心模块

| 模块 | 作用 |
|------|------|
| `core/bus.py` | 中央事件总线，模块间通信 |
| `core/plugin.py` | 插件注册与生命周期管理 |
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
mmsg  # 映射到 mmsg.ui.cli:main
```
