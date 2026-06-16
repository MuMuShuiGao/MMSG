# curiosity_note 全链路

## 数据模型

### curiosity_note 表

```sql
CREATE TABLE curiosity_note (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT,
    content        TEXT NOT NULL DEFAULT '',    -- 自言自语口吻
    category       TEXT NOT NULL DEFAULT 'curiosity', -- follow_up | concern | curiosity
    topic_key      TEXT NOT NULL DEFAULT '',    -- 3~8字短词，embedding 匹配用
    quality        INTEGER NOT NULL DEFAULT 3,  -- 1~5
    needs_research INTEGER NOT NULL DEFAULT 0,  -- 是否需要查资料
    status         TEXT NOT NULL DEFAULT 'pending', -- pending | dismissed | pushed | answered
    triggered_at   TEXT,                        -- 推送时间
    merged_from    TEXT,                        -- 合并来源 note id JSON 数组
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
```

### vec_message 虚表（"被惦记"信号源）

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS vec_message USING vec0(
    message_id INTEGER PRIMARY KEY,
    embedding FLOAT[1024]
);
```

## 写入链路

```
message 表
  → MessageEmbedder worker（60s 轮询，增量扫 user message）
    → embed(text) → INSERT vec_message
  → ProactiveEngine 主循环（15min 一次）
    → _generate_notes_from_recent() 扫新对话 → LLM(CURIOSITY_PROMPT) → save_notes()
```

## 读取 & 整理链路

```
ProactiveEngine 主循环（15min）
  _review_curiosity_notes():
    1. 取 pending notes（按 quality DESC 取 top 30）
    2. 对每条 note.topic_key:
         embed → 查 vec_message (cos > 0.85, 最近7天)
         → mentions_recent = 命中条数
    3. 喂 LLM(CONSOLIDATE_PROMPT) → {id, quality, topic_key, mentions_recent, ...}
    4. LLM 输出 → update_note(quality, status, content, ...)
    5. 返回 status=pending 的候选列表
```

## 推送决策链路

```
主循环取 best 候选：
  should_push(quality, hours_since_active, pushed_today)?
    ├─ _is_topic_cooldown()   → 查 24h 内 pushed notes，topic_key cos 匹配
    └─ _is_rumination()       → 扫最近 50 条 user message embedding，topic_key cos 匹配
  → _do_push(best) → mark_pushed(note_id)
```

## 链条总览

```
对话结束
  ↓
message 表落库
  ↓ (并行两条)
  ├─ MessageEmbedder: message → embedding → vec_message
  └─ 15min 后 ProactiveEngine 主循环:
       ├─ 扫新对话 → LLM 生成 note → save_notes
       ├─ _review_curiosity_notes:
       │    翻 pending notes → emb(topic_key) → vec_message → mentions_recent → LLM 重打分
       └─ 决策推送:
            should_push ✓ → 冷却 ✓ → 反刍 ✓ → do_push → mark_pushed
```
