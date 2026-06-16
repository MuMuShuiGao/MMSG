# 写入路径总览

8 条写入路径均从 `message` 表衍生，核心三层：

- **短期摘要**（`current_context.md`）：RecentRecapper 被动快照，给 agent 看「上次聊到哪」。
- **长期记忆**（`memory.md`）：curator worker 用户画像，只信 user 原话，软上限 4000 字。
- **向量事实**（`fact` 表）：consolidator worker 从长期记忆衍生 → embedding（`vec_fact` 虚表），供 Recaller 语义召回。
- **主动议程**（`curiosity_note` 表）：主动引擎 15min 周期生成，含 24h 冷却 + 反刍检测。

```
message（raw）
  ├─→ vec_message         用户原话 embedding（话题热度 + 反刍检测）
  ├─→ current_context.md  短期摘要
  ├─→ curiosity_note      主动议程 → push 状态
  └─→ memory.md           长期画像
       └─→ fact + embedding → merge 去重
```

注入：`MemoryRuntime.build_context_block()` 统一拼「长期记忆 + 短期摘要」，注入所有 LLM 调用。
