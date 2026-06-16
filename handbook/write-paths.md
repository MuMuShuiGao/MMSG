# 写入路径总览

项目共有 8 条数据写入路径，均从原始聊天记录（`message` 表）衍生。

## 八条写入路径

| # | 路径 | 落库位置 | 触发时机 | 同步/异步 | 作用 |
|---|---|---|---|---|---|
| 1 | **聊天记录** | `message` 表 | 每轮 turn 结束 | 同步 | raw 对话落库，完整追溯 |
| 2 | **短期摘要** | `current_context.md` | 每 N 轮（reasoner 内） | 异步 | 会话状态快照，重启快速进入上下文 |
| 3 | **curiosity_note** | `curiosity_note` 表 | 主动引擎 15min 周期 | 异步 | 主动推送弹药（follow_up / concern / curiosity） |
| 4 | **长期记忆（策展）** | `memory.md` | curator worker（≥5 新消息 or ≥6h） | 异步 | 用户画像档案，注入所有 LLM 调用 |
| 5 | **向量事实** | `fact` 表 | consolidator worker 衍生 | 异步 | 语义检索库 |
| 6 | **事实合并** | fact 表去重 | merger worker（每 3 天） | 异步 | 合并 cos > 0.97 的近重复 fact |
| 7 | **推送记录** | `curiosity_note` 状态更新 | execute_push() | 同步 | 标记 status=pushed + triggered_at |
| 8 | **user message embedding** | `vec_message` 虚表 | message_embedder worker（每 60s） | 异步 | "被惦记"信号源：topic_key 近邻匹配 |

## 金字塔关系

```
message（raw）
  ├─→ vec_message         用户原话 embedding（供话题热度 + 反刍检测）
  ├─→ current_context.md  短期摘要
  ├─→ curiosity_note      主动议程
  │    └─→ push 状态      推送记录
  └─→ memory.md           长期画像
       └─→ fact + embedding  向量库
            └─→ merge     去重合并
```

## 三层核心职责切分

| 层 | 文件/表 | 职责 |
|---|---|---|
| **短期摘要** | `current_context.md` | 被动状态快照，给 agent 看「上次聊到哪」 |
| **curiosity_note** | `curiosity_note` 表 | 主动议程项，agent 找借口开口的弹药 |
| **长期记忆** | `memory.md` | 稳定用户画像，让 agent 像「认识你的朋友」 |

三者上游都是最近对话，但**输入源、产物形态、用途**各异，不重复。LLM 多跑几遍是可接受成本。

## 长期记忆关键约束

- **只信用户原话**（`role=user`），不喂 AI 的回复或转述
- 输入 = 增量 user 原话 + 旧 `memory.md`
- 软上限 4000 字，超出由 LLM 自压缩
- 重试 ≤ 3 次后强行推进 watermark（丢一批增量比卡死好）
- 不暴露 `remember()` 工具给 agent（严格单一信息源）

## curiosity_note 重构（v2）

curiosity_note 生成从 AfterTurn 阻塞路径搬至主动引擎 15min 主循环，新增：

- `topic_key` 自由短词 → embedding（路 3：自由词 + embedding 兜底）
- `mentions_recent` 信号：note 的话题在最近 7 天 user message 中被提到的次数
- 同话题 24h 冷却 + 反刍检测（推送前扫最近 50 条消息防重复）

详见 `curiosity-note-redesign.md`。

## 注入策略

`MemoryRuntime.build_context_block()` 统一拼「长期记忆 + 短期摘要」，注入所有 LLM 调用：

- agent reasoner system prompt
- proactive consolidate prompt
- push 消息生成
- curiosity note 生成
- memory curator 自身
