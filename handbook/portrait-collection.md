# 画像收集链路（Portrait Collection）

## 定位

只有一条主动链路。agent 定期扫描用户画像中的空白维度，以朋友式问句主动补全。

- **取材**：长期 memory（`memory.md` + `PENDING.md`），不看近期聊天记录
- **节奏**：评分触发——K 轮计数器 + 双维度评分，一天最多 1 条
- **形态**：单步——起意 → 一次 LLM 出问句 → 推送
- **持久化**：无 pending 池，仅推送后写事件 log（`asked_question`）

## 触发

主循环每跑一次 `consolidate_interval`（默认 15min），画像计数器 +1。

```
if portrait_tick % portrait_every_n_ticks == 0     # 默认 8 轮 ≈ 2h
   and score(silence, warmth) >= portrait_score_threshold  # 默认 0.5
   and not in_quiet_hours
   and asked_today < daily_cap:
       run_portrait_tick()
```

### 评分模型

两个信号，加权求和：

```
silence = min(1, hours_since_active / portrait_energy_full_hours)
          # 距上次互动越久 → silence 越高，24h 饱和到 1

warmth  = min(1, user_msgs_last_7d / portrait_recent_full_count)
          # 近 7 天用户消息越多 → warmth 越高，70 条（≈ 一天 10 条）饱和到 1

score = portrait_weight_energy * silence + (1 - portrait_weight_energy) * warmth
        # 默认 0.6 * silence + 0.4 * warmth
```

三个典型场景：

| 场景 | hours | msgs_7d | silence | warmth | score | 结果 |
|---|---|---|---|---|---|---|
| 刚聊完很多 | 2 | 40 | 0.08 | 0.57 | **0.28** | 不发 |
| 昨天热聊今天没音信 | 20 | 35 | 0.83 | 0.50 | **0.70** | 发（最佳）|
| 久没聊平时也少 | 48 | 5 | 1.00 | 0.07 | **0.63** | 发（次优）|

### 当日上限

```sql
SELECT COUNT(*) FROM asked_question WHERE asked_at >= today
```

≤ `daily_cap`（默认 1）。

## 现场生成

触发后单次 LLM 调用：

**输入**：
- `memory.md` 全文（4 节：身份与项目 / 偏好 / 生活与关系 / 长期关注）
- `PENDING.md` 全文（curator 已抽取但未合并的最新事实，补 Evolver 滞后窗口）
- 近 2 天 `asked_question` 列表（去重参考）

**任务**：
- 审视四节内细维度的空白（音乐/食物/作息/家庭/工作偏好/价值观/健康/兴趣脉络……），不强制范围
- 挑覆盖度最低且近期未问过的方向，生成自然朋友问句

**输出**：
```json
{ "topic_key": "工作环境", "message": "对了，最近换工作的事还在跟吗？没听你提了" }
```

## 去重

```sql
CREATE TABLE asked_question (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    content   TEXT NOT NULL,
    topic_key TEXT NOT NULL DEFAULT '',
    asked_at  TEXT NOT NULL
);

CREATE VIRTUAL TABLE vec_asked_question USING vec0(
    asked_question_id INTEGER PRIMARY KEY,
    embedding         FLOAT[1024]
);
```

生成新问句后：
1. 拉 `asked_at >= now - 2d` 的 `asked_question` 行
2. 用 `Memory.embed_provider` embed 新问句
3. 跟历史 embedding 比 cosine，超阈值（默认 0.85）丢弃，重生成最多 N 次（默认 2）
4. 通过 → publish → 写 `asked_question` + `vec_asked_question`

超过 2 天自动"过期"，类似问题可再问。

## 链路

```
主循环 tick
   ↓
portrait_tick % K == 0
   ↓
计算 E（沉默时长）+ R（近 7 天活跃度）→ score
score >= θ  +  quiet gate  +  当日上限 gate
   ↓
读 memory.md + PENDING.md + 近 2d asked_question
   ↓
LLM 单次出 {topic_key, message}
   ↓
embed + 2d 向量去重校验
   ↓ 通过
publish → 写 asked_question + vec_asked_question
   ↓
（用户回答 → Curator 自动吸收进 memory.md，画像自闭环）
```

## 代码组织

`mmsg/proactive/portrait.py` — `PortraitCollector` 类：

```python
class PortraitCollector:
    async def maybe_tick(self) -> str | None:
        """主循环每轮调一次。内部判 K 轮 + 评分 + 当日上限，过门槛则生成 + 去重 + 推送。"""

    async def simulate(self) -> dict:
        """Dashboard 演练：跑完整链路但不真发，返回诊断（silence/warmth/score）+ 消息预览。"""

    async def execute(self) -> dict:
        """Dashboard 强制触发：跳过 K 轮和评分门槛，真发。"""
```

`ProactiveEngine.serve()` 主循环：

```python
while True:
    await asyncio.sleep(consolidate_interval)
    await self._portrait.maybe_tick()
```

## Dashboard

`POST /api/portrait/simulate` → 演练，不真发（返回 silence/warmth/score 便于观测）  
`POST /api/portrait/execute` → 强制触发，真发

## 配置

画像评分参数硬编码在 `PortraitCollector` 类常量中，不对外暴露：

```python
_EVERY_N_TICKS    = 8     # 每 8 轮主循环（≈ 2h）考虑一次
_ENERGY_FULL_HOURS = 24   # silence 饱和点（小时）
_RECENT_WINDOW_DAYS = 7   # warmth 统计窗口（天）
_RECENT_FULL_COUNT  = 70  # warmth 饱和点（条）
_SCORE_THRESHOLD    = 0.5
_WEIGHT_SILENCE     = 0.6  # warmth 权重 = 0.4
_DEDUP_DAYS         = 2
_DEDUP_THRESHOLD    = 0.85
_DEDUP_RETRY        = 2
_DAILY_CAP          = 1
```

仍可通过 toml 配置的只有基础运行参数：

```toml
[proactive]
channel = ""
quiet_start = "00:00"
quiet_end = "07:00"
consolidate_interval = 900   # 主循环心跳（秒）
```

## 写回

用户回答 → Curator 从对话抽取 → PENDING.md → Evolver 合并 → memory.md。  
画像链路本身不写 memory.md，沉淀完全交给现有 memory 写入路径。

## 设计要点

- **双维度评分替代硬门槛**：`silence` 衡量沉默时长（渴望互动），`warmth` 衡量近期活跃度（有话可聊）。乘积不够区分"昨天热聊今天无音信（最佳）"与"久没聊平时也少（次优）"，加权和 + silence 偏重能精确排出优先级。
- **silence 在 24h 饱和而非 48h**：让"昨天热聊、今天沉默 20h"的 silence 接近峰值，使场景 2 得分高于场景 3。
- **零留痕 = 无 pending 池**：不预先酝酿候选。`asked_question` 是事件 log，是"已问过什么"的事实之源。
- **schema 比对 ≠ 硬约束**：四节是宏观结构，细维度是启发示例，LLM 自由选最该补的点。
- **PENDING.md 兜底 curator 滞后**：用户刚说过的内容还未合并进 memory.md，PENDING 已有记录，LLM 不会重复问。
- **不开反刍检测**：抽象 topic_key 与具体 user message 的 embedding 相似度阈值难调；PENDING.md 已覆盖滞后窗口，不需要二次拦截。
