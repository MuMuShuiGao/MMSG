# 写入路径总览

8 条写入路径均从 `message` 表衍生，核心五类：

- **短期摘要**（`current_context.md`）：RecentRecapper 被动快照，给 agent 看「上次聊到哪」。
- **长期记忆**（`memory.md`）：MemoryCurator 增量追加 → PENDING.md → Evolver 定期合并，软上限 4000 字。
- **自我认知**（`self.md`）：Evolver 从 PENDING 选择性吸收更新，固定三节。
- **向量事实**（`fact` 表）：Consolidator worker 直接从 user 原话提取 → embedding（`vec_fact` 虚表），供 Recaller 语义召回。
- **主动议程**（`curiosity_note` 表）：主动引擎 15min 周期生成，含 24h 冷却 + 反刍检测。

```
message（raw，双方）
  ├─→ vec_message         user 原话 embedding（话题热度 + 反刍检测）
  ├─→ current_context.md  短期摘要
  ├─→ curiosity_note      主动议程 → push 状态
  ├─→ PENDING.md          增量事实（MemoryCurator 产出）
  │    ├─→ memory.md      Evolver 智能合并（去重/冲突解决/归类）
  │    └─→ self.md        Evolver 选择性吸收（三节固定）
  └─→ fact + vec_fact     直接从 user 原话提取，独立水位
```

## PENDING 管道

```
MemoryCurator（≥5 条新消息 或 ≥6h）
  → 对比 memory.md 产出增量 delta（前缀 新增/更正）
  → 追加写入 PENDING.md

Evolver（≥12h 或 PENDING ≥1000 字）
  → _merge_memory: 当前 memory.md + PENDING → LLM 智能合并
  → _update_self: PENDING → LLM 选择性吸收
  → 全量清空 PENDING.md
```

## memory.md 写入路径

```
输入
  [user]      所有对话原话（提取来源）
  [assistant] 所有对话原话（提供上下文，不直接写入）

Curator 阶段
              ↓  MemoryCurator（≥5 条新消息 或 距上次 ≥6h）
              ↓  MEMORY_CURATION_SYSTEM_PROMPT
              ↓  LLM → JSON { delta（增量，前缀 新增/更正）, note }
              ↓  追加写入 PENDING.md

Evolver 阶段
              ↓  Evolver（≥12h 或 PENDING ≥1000 字）
              ↓  LLM → JSON { memory（完整画像）, note }

输出
  memory.md   只写 [user] 明确表达的行动、经历、计划、状态
              [assistant] 的建议、推荐、解释一律不写入
              固定四节：身份与项目 / 偏好 / 生活与关系 / 长期关注
              软上限 4000 字，超出合并或删除过时条目
```

## self.md 写入路径

```
输入
  PENDING.md   MemoryCurator 产出的增量事实

Evolver 阶段
              ↓  Evolver（≥12h 或 PENDING ≥1000 字）
              ↓  从 PENDING 选择性吸收
              ↓  LLM → JSON { self, note }

输出
  self.md     固定三节（标题不可改）：
                ## 关于我自己
                ## 我眼中的用户
                ## 我们的相处关系
              只吸收影响自我认知/用户理解/相处模式的条目
```

## current_context.md 写入路径

```
输入
  [user]      对话原话（提取来源）
  [assistant] 对话原话（提供上下文，不直接写入）

Recapper 阶段
              ↓  RecentRecapper（滑动窗口每满 summarize_every 轮触发，fire-and-forget）
              ↓  内联 prompt（5 字段 JSON：持续关注 / 明确偏好 / 待延续话题 / 避免事项 / 前置背景）
              ↓  LLM → JSON { 持续关注, 明确偏好, 待延续话题, 避免事项, 前置背景 }

输出
  current_context.md  覆盖写入，只保留最新一条 [MM-DD HH:MM] 快照
                      内容：5 字段，每字段 ≤40 字
```

## fact 写入路径

```
输入
  message（role=user）  原话增量（Consolidator 独立水位跟踪）

Consolidator 阶段
              ↓  Consolidator（≥10 条新 user 消息 或 距上次 ≥2h，独立后台 worker）
              ↓  _CONSOLIDATION_SYSTEM_PROMPT
              ↓  LLM → JSON { facts: ["用户在...", ...] }

              ↓  embedding（text-embedding 模型，批量）
              ↓  insert_facts_batch → fact 表 + vec_fact 虚表
              （无去重，同批内容幂等由水位保证；跨批相似度去重由 Recaller 侧处理）

输出
  fact 表     每行一条原子事实，含 source_message_ids / mention_count
  vec_fact    虚表（sqlite-vec），供 Recaller 语义召回
              直接从 user 原话提取，不经 memory.md
```

## curiosity_note 写入路径

```
输入
  message（role=user/assistant）  近期对话（增量水位 last_note_generated_message_id）
  memory.md                       用户画像（CURIOSITY_PROMPT 注入参考）

生成阶段
              ↓  主动引擎（15min 周期轮询，_generate_notes_from_recent）
              ↓  CURIOSITY_PROMPT（+ memory context block）
              ↓  LLM → JSON [ { content, category, topic_key, quality } ]，每批最多 3 条

整理阶段（同周期，_review_curiosity_notes）
              ↓  CONSOLIDATE_PROMPT → LLM 重新打分 / 合并 / 标 dismissed
              ↓  mentions_recent：topic_key embedding 搜 vec_message（7d 窗口）

推送决策
              ↓  冷却检查：topic_key embedding 与 24h 内 pushed note 的 topic_key 相似度 > 0.85 → 跳过
              ↓  反刍检测：topic_key embedding 搜 vec_message（最近 50 条 user 原话），命中 → 跳过
              ↓  should_push(quality, hours_since_active, pushed_today)

输出
  curiosity_note 表   含 content / category / topic_key / quality / needs_research
                      状态流转：pending → pushed（→ dismissed 由整理阶段标记）
```

## 注入

`MemoryRuntime.build_context_block()` 统一拼「自我认知 → 长期记忆 → 短期摘要」，注入所有 LLM 调用。

**注意**：PENDING.md 不注入 system prompt。agent 即时信息由近期对话原文 + current_context 摘要覆盖，长期画像由 Evolver 定时合并保持稳定，以提升 KV cache 命中率。
