# Memory 注入与调用链

## 当前记忆系统

采用 **双文件持久化** 引擎 (`default` 引擎)：

```
workspace/
└── memory/
    ├── memory.md            ← 长期知识（常青事实，手动策展）
    └── current_context.md   ← 短期上下文（近期摘要 + 最近 N 轮对话）
```

- **memory.md**: 通过 `KnowledgeBase` 管理，支持 `append(fact)` 追加事实，`read()` 全量返回。
- **current_context.md**: 通过 `ContextWindow` 管理，每轮对话追加 `**role**: content`，回合结束时裁剪到最近 `max_turns` 轮（默认 5）。每 10 轮触发 `_summarize()` 用 LLM 压缩成 5 字段摘要写入文件头部。

### recall 返回值

`engine.recall()` 返回两条 `MemoryRecord`（均 role=system）：
1. `"# 长期记忆\n\n{memory.md 内容}"`
2. `{current_context.md 全部内容}`

这两条在 `AgentLoop._assemble_messages()` 中插入到 LLM 消息列表的最前面（紧随 system_prompt）。

## 调用链

```
AgentLoop.serve() 或 app._batch → agent.run(text):
  memory.start_turn()
  memory.write(user)
  【思考循环：memory.write(assistant/tool) ... memory.recall()】
  memory.end_turn(user_input, result)
  return result
```

## 引擎选择

通过 `config.toml` 的 `[memory] backend = "default"` 指定。工厂 `create_memory()` → `engines/__init__.py` 注册表 → 加载对应引擎的 `create()` 函数。

## 替换引擎

在 `engines/` 下新建子包（如 `graph/`），实现 `Memory` 协议 + 暴露 `create(config)`，然后在 `engines/__init__.py` 的 `ENGINE_REGISTRY` 加一行，改 `config.toml` 的 `backend` 即可。上游（app/agent）零改动。
