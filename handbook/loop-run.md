# AgentLoop.run() 详细执行流程

```
AgentLoop.serve()                                         ← 长驻消费循环
│  item = await message_bus.consume_inbound()             ← 从队列拉取 (asyncio.Queue)
│  result = await self.run(text)
│  await message_bus.publish_outbound(item.source, ...)   ← 结果推入出站队列
│
└─ self.run(text)
   │
   ├─ 1. _ensure_session()                               ← 首次自动创建 session_id
   ├─ 2. memory.start_turn()                             ← 开启新一轮上下文写入
   │
   ├─ 3. memory.write(user_record)                       ← 用户消息持久化到 current_context.md
   │
   └─ 4. 思考循环 (max_steps=8)
      │
      ├─ _assemble_messages()
      │   ├─ memory.recall("", k=64)                     ← 从磁盘取回：
      │   │   ├─ memory.md       (长期记忆/常青知识)
      │   │   └─ current_context.md (近期摘要 + 最近 N 轮对话)
      │   └─ 转成 list[ChatMessage]: system_prompt + recalled records
      │
      ├─ bus.intercept(BeforeStep)                       ← 拦截器可改写请求
      │
      ├─ llm.chat_stream(messages, tools)                 ← 流式 LLM 调用（带工具 schema）
      │   └─ 逐 chunk 收集 text + tool_calls + finish_reason + usage
      │
      ├─ bus.intercept(AfterReasoning)                    ← 拦截器可改写 LLM 输出
      │
      ├─ memory.write(assistant_record)                   ← LLM 回复持久化（含 tool_calls meta）
      │
      ├─ 若无 tool_calls → break 返回 final_text
      │
      └─ 若有 tool_calls → 逐个执行:
          ├─ bus.observe(BeforeToolCall)
          ├─ tool.run(**arguments)
          ├─ memory.write(tool_record)                    ← 工具结果持久化
          └─ bus.observe(AfterToolCall)
          → 回到循环顶部，下一轮 _assemble_messages() 携带工具结果
   │
   ├─ 5. memory.end_turn(user_input, final_text)          ← 关闭本轮：
   │   │   引擎内部累加轮次，每 10 轮触发 LLM 摘要压缩
   │   │   current_context.md 裁剪到最近 max_turns 轮
   │   └─ _rebuild()                                     ← ContextWindow._rebuild()
   │
   ├─ 6. bus.observe(AfterTurn, {final})                  ← 通知外部本轮结束
   │
   └─ 7. return final_text
```

## 数据流

```
channel.publish_inbound()  →  [_inbound queue]  →  AgentLoop.serve()  →  AgentLoop.run()
                                                                               │
channel.callback(item)    ←  [_outbound queue]  ←  AgentLoop.serve()  ←  return
```

## 关键点

- **serve()**: `asyncio.Queue.get()` 阻塞等待，串行处理每条消息，天然排队。AgentLoop 自身掌管消息消费，不再需要 SessionRouter 中间层。
- **recall 时机**: 每步循环开头，LLM 调用前。确保 LLM 看到从 turn 开始到上一步为止的全部消息。
- **write 时机**: 每一步产出（assistant文本、工具结果）立刻写入，保证在下一步 recall 中可见。
- **end_turn 时机**: 所有步骤结束后，由引擎决定是否摘要压缩、裁剪轮次。
- **memory 双文件**: recall 同时返回 memory.md（长期知识）+ current_context.md（短期上下文），合并成 system 角色插入 LLM 消息列表。
