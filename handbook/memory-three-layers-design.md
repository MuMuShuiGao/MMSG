# 三层记忆设计共识

> 通过「grill-me」逐分支拍板的设计稿。涉及短期摘要、curiosity_note、长期记忆三者的职责切分与长期记忆机制引入。

## 起点问题

1. 短期摘要 (`current_context.md`) 和 `curiosity_note` 看起来重复，是否真重复？
2. 长期记忆 (`memory.md`) 接口已存在但无写入路径，怎么引入？

## 结论：三者不重复，同源不同向

三条路径的上游都是「最近对话」，但**输入源、产物形态、用途**各异，没有重复。LLM 跑三遍是可接受成本。

| 层 | 触发时机 | 输入源 | 产物形态 | 用途 |
|---|---|---|---|---|
| **短期摘要** `current_context.md` | reasoner 内 N 轮一次（已存在） | reasoner 的对话上下文 | 5 字段状态快照（持续关注/明确偏好/待延续话题/避免事项/前置背景） | agent 重启或被召回时快速进入状态 |
| **curiosity_note** | `after_turn` 每轮（已存在） | 最近对话 | 自言自语口吻笔记 + quality 1-5 + category | 主动推送决策的弹药 |
| **长期记忆** `memory.md` | 独立 consolidator worker（新增） | 增量 `role=user` 原话 + 旧 `memory.md` | 分类小标题画像 | 注入所有 LLM 调用，让 agent「认识用户」 |

### 关键边界

- **短期摘要 = 被动状态快照**（read-only context，给 agent 看「上次聊到哪」）
- **curiosity_note = 主动议程项**（agent 闲着没事时找借口开口的弹药）
- **长期记忆 = 稳定画像档案**（让 agent 像「认识你的朋友」而非「翻日记的助手」）

「待延续话题」≈ `follow_up` note、「持续关注」≈ `concern` note 这种内容上的交叉是同源现象，不构成职责重复。

## 长期记忆机制设计

### 范围
- **全局单文件** `workspace/memory/memory.md`
- 假设：单用户私人 agent（跨 channel `qqbot` / `feishu` 都是同一人）
- 多用户场景以后再说，YAGNI

### 内容定位：用户画像为主
形如稳定事实 + 长期偏好 + 关系/项目/身份。**不是**事件流水，**不是**知识沉淀。

### 内部结构：分类小标题
```markdown
## 身份与项目
- 在做 MMSG-agent 项目（Python，私人朋友定位）

## 偏好
- 喜欢简洁回答，不要「您好」「请问」这类正式用语

## 生活与关系
- 养了一只猫叫毛毛

## 长期关注
- ...
```

LLM 整合时按节增删改。分类预设 4 节足够。

### 输入源：只信用户原话

**硬约束：不喂 AI 的回复或转述。** 排除短期摘要、note 内容（都是 AI 措辞），只用 `role=user` 原话。

整合 LLM 输入 = 增量 user 原话 + 旧 `memory.md`

### Watermark 增量机制

新增 SQLite 表 `memory_state(key TEXT PK, value TEXT)`，存：

| key | 含义 |
|---|---|
| `last_consolidated_id` | 已整合到的最大 `message.id` |
| `pending_batch_max_id` | 当前批次的最大 id（用于失败重试） |
| `retry_count` | 当前批次的失败次数 |
| `last_run_at` | 上次整合时间 |

每次整合：取 `message.id > last_consolidated_id` 且 `role=user` 的消息 → LLM 整合 → 写入 `memory.md` → 推进 `last_consolidated_id`。

### 触发节拍：数量或时间，首达即触发

- 累计新增 user 消息 ≥ K 条（如 5），**或**
- 距上次 ≥ T 小时（如 6 小时）且至少有新增

### 容量控制：软上限 + LLM 自压缩
prompt 里说明「memory.md 控制在 N 字以内（如 4000），超出就合并相近条目/删过期内容」。LLM 每次都看到完整旧版+新增，自然会裁。

### 失败处理：重试 ≤ 3 次后强行推进 watermark
- 长期记忆是锦上添花，不是关键路径
- 丢一批增量比卡死好
- `retry_count` 持久化在 `memory_state`

### 工具暴露：不暴露 `remember()` 给 agent
- 严格符合「只用用户原话」约束
- 架构最简，单一信息源
- 跑一段有需求再加

### Worker 归属
新建 `mmsg/memory/engines/default/consolidator.py`：
- 独立 worker，自己的循环 + 安静时段保护
- 不复用 `proactive/engine.py`（职责不同：proactive=推送决策，memory=画像沉淀，SRP）
- app 启动时和 proactive 平行起 task

### 注入策略：所有 LLM 调用都注入

原则：**任何 LLM 调用都要带完整记忆上下文。**

抽统一函数 `MemoryRuntime.build_context_block() -> str`：
- 返回拼好的「长期记忆 + 近期摘要」字符串块
- 各调用点自己决定塞 prompt 的哪个位置（灵活）
- 单点维护，避免散写失控

落地点：
- ✓ agent reasoner system prompt（已有，改为统一函数）
- ✓ proactive consolidate prompt（已有，改为统一函数）
- ✓ proactive 推送消息生成 (`PUSH_GENERATION_PROMPT` / `RESEARCH_SYSTEM_PROMPT`，**新增**)
- ✓ curiosity note 生成 (`CURIOSITY_PROMPT`，**新增**)
- ✓ memory consolidator 自己（读旧 memory 做合并）

### 可观测：dashboard 入口 + 日志

新增 dashboard 端点：
- `POST /api/memory/consolidate` 手动触发一次整合
- `GET /api/memory/state` 查 watermark / retry_count / last_run_at

日志：每次整合的输入条数、产出字数、watermark 推进、失败原因。

## 落地结构

```
mmsg/
├── memory/
│   ├── protocol.py              # MemoryRuntime 加 build_context_block()
│   └── engines/default/
│       ├── engine.py            # 现有：短期摘要 consolidate（不动）
│       ├── memory.py            # 现有：KnowledgeBase 文件 I/O（不动）
│       ├── current_context.py   # 现有（不动）
│       └── consolidator.py      # 新增：长期记忆 worker
├── proactive/engine.py          # 不动（仅在 prompt 拼接处改用 build_context_block）
├── storage/
│   ├── models.py                # 加 MemoryState dataclass
│   └── sqlite.py                # 加 memory_state 表 schema
└── dashboard/api.py             # 加 memory consolidator 端点
```

## 职责对照速查

```
原始对话 (message 表)
   │
   ├── reasoner 上下文 ──→ summarize_every N 轮 ──→ current_context.md (短期摘要)
   │                                                       │
   ├── after_turn 事件 ──→ _generate_notes ──→ curiosity_note 表
   │                                                       │
   └── role=user 原话增量 ──→ memory consolidator ──→ memory.md (长期画像)
                                                          │
   build_context_block() = "长期记忆 + 短期摘要" ←─────────┘
        │
        └─→ 注入所有 LLM 调用（reasoner / consolidate / push / curiosity / memory worker）
```

## 决策记录（grill 出来的分支）

1. **职责定位** → 状态快照 vs 行为议程
2. **是否合并 LLM 调用** → 不合并，两次 LLM 保留
3. **长期记忆装什么** → 用户画像为主
4. **写入路径** → 不阻塞，异步
5. **note 是否作为 memory 输入** → 不直接喂内容（AI 措辞），只信用户原话
6. **输入策略** → 增量扫 user 原话 + 旧 memory 合并
7. **触发节拍** → 数量或时间首达
8. **worker 归属** → memory 模块下独立 consolidator
9. **状态持久化** → SQLite `memory_state` 表
10. **memory.md 结构** → 分类小标题
11. **容量控制** → 软上限 + LLM 自压缩
12. **范围** → 全局单文件（单用户）
13. **注入范围** → 所有 LLM 调用都注入
14. **注入实现** → 抽 `build_context_block()`
15. **失败处理** → 重试 ≤ 3 次后跳过推进
16. **重试上限存哪** → 持久化在 memory_state，上限 3
17. **是否给 agent 暴露 remember 工具** → 不暴露
18. **可观测** → dashboard 入口 + 日志
