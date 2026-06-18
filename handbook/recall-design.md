# 召回系统设计共识

> 通过「grill-me」逐分支拍板的设计稿。在现有 `current_context.md`（compression）+ `memory.md`（curator 画像）之上，引入第三条路径：基于向量库的语义召回。

## 起点问题

现状：上下文喂 LLM 只靠两个 markdown 文件全量注入：
- `current_context.md` — 滑窗压缩出的 5 字段近期摘要（compression）
- `memory.md` — curator 提炼的用户画像（长期）

痛点：
1. 跨会话事实级检索缺位 — "我之前说过用什么数据库？" 画像合并后细节丢失
2. 同会话超窗回溯丢真相 — 滑窗砍掉的早期消息被压成摘要，原话不可追
3. 项目名 / 版本号 / 人名等专有信息 — 摘要会抹平，画像会归类

→ 引入第四条路径：**consolidator + 向量库 + Recaller**，按 query 召回 facts，注入 prompt。

## 产品定位（前置约束）

目标用户：**愿意把 AI 当长期协作对象的个人用户**。

明确不做：
- ❌ 多租户 SaaS
- ❌ Web 聊天界面
- ❌ RAG 文档库（记忆面向个人画像，不是文档检索）
- ❌ 移动 App

→ 召回是面向**个人画像**的 fact 检索，不是文档 RAG。

## 总览：四路记忆并存，职责正交

| 层 | 触发 | 输入 | 产物 | 用途 |
|---|---|---|---|---|
| **compression** `current_context.md`（已有 recapper） | 滑窗 N 轮 | 一段对话 | 5 字段状态快照 | 喂 prompt（短期被动） |
| **curator** `memory.md`（已有） | 水位+阈值 | user 原话累积 | 分类画像 | 喂 prompt（长期画像） |
| **召回** `fact` 表 + 向量库（新增） | consolidator 水位 → embed；Recaller 按 turn 触发 | user 原话 | facts 数组 + 向量索引 | 按 query 召回，临时注入 prompt |

四条路径同源（最近对话 / user 原话），但**产物形态和消费时机不同**，不重复。

## 数据流总览

```
用户消息 → message 表（reasoner / agent_loop 写）
                ↓
        consolidator worker（独立后台，水位扫 user 原话）
                ↓
        LLM 提取 facts 数组
        （每条保留专有名词原话片段）
                ↓
        三处同步写：fact / vec_fact / fts_fact
                ↓ ──────────────────────────────────┐
                                                    │
turn 来了 → Recaller.recall_for_turn()              │
   ├─ 判别器 LLM → {need_recall, query}             │
   ├─ if need_recall: hybrid 检索 ←─────────────────┘
   │     ├─ dense lane: vec_fact top-30 (cos)
   │     ├─ sparse lane: fts_fact top-30 (BM25, jieba 切词)
   │     ├─ RRF(k=60) → top-20
   │     └─ MMR(λ=0.7) → top-5
   ├─ 拼 recall_block (system message)
   └─ 进入 ReAct，多 step 共享同一份 facts
                ↓
        合并 worker（每 3 天）→ cos > 0.97 合并
```

## 决策清单（17 项）

### 召回场景与策略

| # | 决策点 | 结果 | 备注 |
|---|---|---|---|
| 1 | 召回场景 | **C**：跨会话长期检索 + 同会话超窗回溯 | 全部历史一锅 embed |
| 2 | dense ingest 内容 | **summary 派**：LLM 提取的 facts | 不直接 embed 原文 |
| 3 | BM25 tokenizer | **C** jieba（Python 层切词 + FTS5 unicode61） | 牺牲未登录词召回换专有名词精度 |

### Embedding 与向量库

| # | 决策点 | 结果 | 备注 |
|---|---|---|---|
| 4 | embedding model | dashscope `text-embedding-v3` | 1024 维 |
| 5 | provider 形态 | **B** 独立 `EmbeddingProvider` 协议 | 不污染 `LLMProvider` |
| 6 | 向量库 | **A** `sqlite-vec` | 跟 FTS5 / message 同库，hybrid 一条 SQL 搞定 |

### 写入路径

| # | 决策点 | 结果 | 备注 |
|---|---|---|---|
| 7 | 写入时机 | **A** consolidator 独立 worker | 跟 curator 平行，各自水位 |
| 7-a | hybrid lane 索引对齐 | **C** 都索引 summary，prompt 强约束保留专有名词 | RRF 要求 lane 同语料 |
| 8 | summary content 形态 | **B** 原子 facts 数组 | 单点检索粒度 |
| 9 | 去重策略 | **A** 全存 + MMR + 3 天合并 worker | 不可逆决策延后 |

### 召回触发与注入

| # | 决策点 | 结果 | 备注 |
|---|---|---|---|
| 10 | 召回触发 | **C** LLM 判别器 | 接受额外延迟 + 成本换语义清洁 |
| 11 | 判别器输出 | **B** `{need_recall, query}` | 一次调用同时出决策 + 改写 query |
| 12 | 注入位置 | **B** 单独 system message，紧跟 memory_ctx | 跟「持久 memory」分隔清楚 |
| 12-b | 调用链架构 | **C** 新增 `Recaller` 类 → `mmsg/memory/recall.py` | 独立可测 |

### 检索数值

| # | 决策点 | 结果 |
|---|---|---|
| 13 | 检索数值 | dense 30 + sparse 30 → RRF(k=60) top-20 → MMR(λ=0.7) top-5 |

### 数据模型与存储

| # | 决策点 | 结果 | 备注 |
|---|---|---|---|
| 14-a | embedding 维度 | 1024 | vec0 建表时定死 |
| 14-b | fts_fact schema | external content + jieba 应用层切词 | 不用 `content='fact'` |
| 14-c | source_message_ids | 存（JSON 数组） | 溯源 + 合并证据链 |
| 15 | 数据模型 | **B** 新建 `Fact`，`MemoryEngine` 接口改名 | 删 `write(MemoryRecord)` |

### 配置与运维

| # | 决策点 | 结果 | 备注 |
|---|---|---|---|
| 16 | config | 新增 `[embedding]` + `[consolidator]` 段 | |
| 17 | 合并 worker 周期 | 3 天 | 距上次合并 ≥3 天才跑 |

## 关键设计要点深入

### 为什么 hybrid 而不是单 dense

两种失效模式互补：

- **dense 翻车场景**：版本号、人名、专有名词（"pg 14.2" "李四" "MMSG-agent"）。embedding 把它们抹平成"软件 / 人 / 项目"。BM25 字面命中即满分。
- **BM25 翻车场景**：同义改写。用户之前说"我用 postgres"，现在问"我数据库是啥"。BM25 零命中，dense 救场。

个人长期记忆里这两类 query 都常见。

### 为什么走 summary 而不是原文

原文派：零 LLM 写入成本、无信息损失、可追溯、query 跟原话同分布。
Summary 派：信噪比高、可结构化拆原子事实、能加 prompt 约束。

最终选 summary 派 + facts 数组，理由：
- 个人画像本质是事实集合，原子化贴合
- 闲聊噪声（"嗯""好"）天然被过滤
- 召回 token 成本低（5 条 fact ≈ 150 字注入）

但 prompt 强约束「保留专有名词原话片段」补回 BM25 lane 的字面匹配能力。

### 为什么 lane 对齐

RRF 融合要求两个 lane 索引**同一份语料**，不然分数没法融。

- 都走 summary（选定）：lane 对齐干净，但要靠 prompt 约束保留专有名词
- dense 走 summary、BM25 走原文：ID 空间不一样，融合崩
- → 选都走 summary，prompt 里加约束「必须保留专有名词、版本号、人名、项目名原文」

### 为什么 LLM 判别召回（C），明知贵和慢

候选：
- A 每 turn 必召 — 闲聊 turn 浪费
- B 每 turn + cos 门槛 — 成本最低
- **C LLM 判别（选定）** — 严格不召无关
- D agentic tool — Reasoner step +1
- E 启发式规则 — 漏召太多

代价已知：
- 延迟 +200-500ms（首字节时间被拉长）
- 每 turn 多一次 LLM 调用，成本比 B 高 5-10×
- 多一个 prompt 要维护，多一个失败分支

但接受这个代价换语义清洁。判别器同时改写 query（B 选项），让一次调用拿全决策 + 优化 query。

### 为什么独立 Recaller 类

放 `LLMContext.build()` 内 — build 变成有 LLM 副作用，单测难。
放 `Reasoner.think()` 入口 — 可行但 Reasoner 职责膨胀。
**独立 `Recaller` 类（选定）** — 召回是独立能力，跟推理循环和上下文拼装都正交。

```python
class Recaller:
    async def recall_for_turn(self, user_msg: str) -> list[Fact]:
        decision = await self._classify(user_msg)
        if not decision.need_recall:
            return []
        return await self._hybrid_recall(decision.query)
```

Reasoner 在 `think()` 入口调一次，结果传给所有 step 的 `LLMContext.build(history, facts=...)`。多 step 共享同一份 facts。

### 为什么 sqlite-vec 而不是 chromadb / lancedb

hybrid 需要 BM25（FTS5），FTS5 必然在 sqlite。如果向量也在同一 sqlite：

```sql
WITH dense AS (
  SELECT id, distance FROM vec_fact 
  WHERE embedding MATCH ? ORDER BY distance LIMIT 30
),
sparse AS (
  SELECT rowid AS id, rank FROM fts_fact 
  WHERE fts_fact MATCH ? ORDER BY rank LIMIT 30
)
SELECT f.id, f.content, d.distance, s.rank
FROM fact f
LEFT JOIN dense d ON d.id = f.id
LEFT JOIN sparse s ON s.id = f.id
WHERE d.id IS NOT NULL OR s.id IS NOT NULL
```

一条 SQL 拿全部数据，应用层做 RRF + MMR。chromadb / lancedb 跟 sqlite 分家，hybrid 查询要应用层 JOIN，代码丑。

### 为什么 facts 数组而不是单 content 字符串

候选：
- A 单条字符串：实现简单，召回粒度粗
- **B facts 数组（选定）**：精度高，库膨胀 5-10×（个人量级无压力）
- D 带 type 的原子记录：过度

个人画像 query 多是单点事实（"我用什么数据库""我之前提过的 xxx"）→ 原子事实命中率高。

### 为什么不写入时去重

候选：
- 写入时阈值（cos > 0.95 跳过）— 不可逆，丢的事实救不回
- 写入时 LLM 判重 — 每次写入多一次 LLM 调用，成本爆炸
- **A 全存 + MMR（选定）** — 可逆，重复 facts 还携带「提及频率」信号
- 月度合并 worker — 兜底

MMR 解决 top-k 多样性（一次性，可调）；合并 worker 解决库膨胀（3 天一次）。两者职责正交。

## 数据模型

### `Fact` 模型

```python
class Fact(BaseModel):
    id: int | None = None
    content: str                          # LLM 提取的事实陈述（含专有名词原文）
    source_message_ids: list[int] = []    # 哪些 message 提取出的
    created_at: datetime
    mention_count: int = 1                # 合并 worker 累计
    last_mentioned_at: datetime           # 合并 worker 更新
    # 召回时填充（不入库）
    rrf_score: float | None = None
    distance: float | None = None
```

### sqlite schema

```sql
-- 主表
CREATE TABLE fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content TEXT NOT NULL,
  source_message_ids TEXT NOT NULL DEFAULT '[]',  -- JSON 数组
  created_at TEXT NOT NULL,
  mention_count INTEGER NOT NULL DEFAULT 1,
  last_mentioned_at TEXT NOT NULL
);

-- 向量
CREATE VIRTUAL TABLE vec_fact USING vec0(
  fact_id INTEGER PRIMARY KEY,
  embedding FLOAT[1024]
);

-- BM25（external content，应用层 jieba 切词）
CREATE VIRTUAL TABLE fts_fact USING fts5(
  content,
  tokenize='unicode61'
);
```

`memory_state` 新增水位键：
- `consolidator_last_id`
- `consolidator_pending_batch_max_id`
- `consolidator_retry_count`
- `consolidator_last_run_at`
- `merger_last_run_at`

## 接口设计

### 协议改动

`MemoryEngine` 接口：
```python
class MemoryEngine(ABC):
    async def ingest_fact(self, fact: Fact) -> int        # 返回 fact_id
    async def query(self, query: str, k: int = 5) -> list[Fact]
    # 删除 ingest(MemoryRecord)
```

`MemoryRuntime`：
```python
async def recall(self, query: str, k: int = 5) -> list[Fact]
async def ingest_fact(self, fact: Fact) -> int
# 删除 write(MemoryRecord) — reasoner 不再调
```

### `EmbeddingProvider` 协议

```python
class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
```

实现 `OpenAIEmbeddingProvider`（兼容 dashscope `/v1/embeddings`）。

### `LLMContext.build()` 签名

```python
async def build(
    self,
    history: list[ChatMessage],
    facts: list[Fact] | None = None,   # 新增
) -> list[ChatMessage]:
```

facts 非空时拼成 system message：
```
# 与本次问题相关的历史记忆

- 用户在 MMSG-agent 项目用 PostgreSQL 14.2 [2024-09-12]
- 用户偏好 rust 而非 go [2024-08-30]
...
```

### `Recaller` 类

```python
class Recaller:
    def __init__(
        self,
        memory: MemoryRuntime,
        llm: LLMProvider,           # 判别器 LLM
    ) -> None: ...

    async def recall_for_turn(self, user_msg: str) -> list[Fact]:
        decision = await self._classify(user_msg)
        if not decision["need_recall"]:
            return []
        return await self.memory.recall(decision["query"], k=5)
```

判别器 prompt：
```
判断用户最新消息是否需要从历史记忆中检索内容。
- 闲聊、确认、简短指令 → 不需要
- 提到"之前""上次""我说过"等回顾性表达 → 需要
- 涉及具体事实、偏好、历史状态的提问 → 需要

如果需要，将用户消息改写为最适合检索的 query（保留专有名词原文）。

输出 JSON：
{ "need_recall": false }
或
{ "need_recall": true, "query": "改写后的检索 query" }
```

## 算法细节

### Hybrid 检索

```python
async def hybrid_search(query: str) -> list[tuple[Fact, float]]:
    q_vec = await embedding.embed([query])
    q_tokens = " ".join(jieba.cut(query))

    # 一条 SQL 拿全部
    rows = sql.execute("""
        WITH dense AS (
          SELECT fact_id AS id, distance FROM vec_fact 
          WHERE embedding MATCH ? ORDER BY distance LIMIT 30
        ),
        sparse AS (
          SELECT rowid AS id, bm25(fts_fact) AS rank FROM fts_fact 
          WHERE fts_fact MATCH ? ORDER BY rank LIMIT 30
        )
        SELECT f.*, d.distance, s.rank
        FROM fact f
        LEFT JOIN dense d ON d.id = f.id
        LEFT JOIN sparse s ON s.id = f.id
        WHERE d.id IS NOT NULL OR s.id IS NOT NULL
    """, [q_vec, q_tokens])

    # RRF 融合
    return rrf_fusion(rows, k=60)[:20]
```

### RRF 融合

```python
def rrf_fusion(rows, k=60):
    # 各 lane 独立 rank
    dense_rank = {r.id: i for i, r in enumerate(sorted(d_rows, key=lambda x: x.distance))}
    sparse_rank = {r.id: i for i, r in enumerate(sorted(s_rows, key=lambda x: x.rank))}
    
    scores = {}
    for fid in dense_rank.keys() | sparse_rank.keys():
        s = 0
        if fid in dense_rank:
            s += 1 / (k + dense_rank[fid] + 1)
        if fid in sparse_rank:
            s += 1 / (k + sparse_rank[fid] + 1)
        scores[fid] = s
    
    return sorted(scores.items(), key=lambda x: -x[1])
```

### MMR

```python
def mmr(candidates: list[Fact], k=5, lambda_=0.7):
    selected = []
    remaining = list(candidates)
    while len(selected) < k and remaining:
        if not selected:
            selected.append(remaining.pop(0))
            continue
        best, best_score = None, -inf
        for f in remaining:
            rel = f.rrf_score
            sim = max(cos(f.embedding, s.embedding) for s in selected)
            score = lambda_ * rel - (1 - lambda_) * sim
            if score > best_score:
                best, best_score = f, score
        selected.append(best)
        remaining.remove(best)
    return selected
```

## 模块改动清单

### 新增文件

- `mmsg/llm/embedding.py` — `EmbeddingProvider` 协议 + `OpenAIEmbeddingProvider`
- `mmsg/memory/fact.py` — `Fact` Pydantic 模型
- `mmsg/memory/recall.py` — `Recaller` 类（判别器 + hybrid + MMR）
- `mmsg/memory/engines/default/consolidator.py` — 独立 worker
- `mmsg/memory/engines/default/merger.py` — 3 天合并 worker
- `mmsg/memory/engines/default/vector_store.py` — sqlite-vec + FTS5 + jieba 封装

### 修改文件

- `mmsg/memory/protocol.py` — `MemoryEngine` 接口改名 `ingest_fact` / `query`；`MemoryRuntime` 加 `recall(query, k)`，删 `write(MemoryRecord)`
- `mmsg/memory/engines/default/engine.py` — `create()` 工厂注入 `EmbeddingProvider` + `VectorStore`
- `mmsg/storage/sqlite.py` — migration 加三表 + memory_state 新水位键
- `mmsg/agent/reason/engine.py` — 删 `await self.memory.write(record)`；持有 `Recaller`，`think()` 入口调一次，传 facts 给 `LLMContext.build()`
- `mmsg/agent/context.py` — `build(history, facts=None)` 加参数
- `mmsg/config.py` — 加 `embedding()` / `consolidator()` accessor，模板补两段
- `mmsg/app.py`（worker 启动处）— 启动 consolidator + merger 两个后台 task
- `pyproject.toml` — 加 `sqlite-vec`、`jieba` 依赖

### config 新增段

```toml
[embedding]
api_key = "sk-your-dashscope-key"
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
model = "text-embedding-v3"
dimensions = 1024

[consolidator]
min_new_msg = 10
min_hours = 2
poll_interval = 120

[merger]
min_days = 3
poll_interval = 3600
similarity_threshold = 0.97
```

## 待落地后再决定

- 判别器 prompt 用 `mmsg/llm` 默认 LLM 还是单独 cheap LLM 实例
- consolidator 失败重试上限（参考 curator 的 `MAX_RETRY=3`）
- merger 合并策略细节：合并时 content 取最新原文还是 LLM 综合改写
- dashboard 的 fact 浏览/手动删除接口

## 不做的（划清边界）

- ❌ 写入时去重（不可逆，靠 MMR + merger 兜底）
- ❌ Reranker 模型（hybrid + MMR 已够，不加二阶段重排）
- ❌ 多 collection / 跨 user 隔离（单用户私人 agent）
- ❌ 把召回挂成 ReAct tool（已选 Recaller 路线，避免 step +1）
- ❌ embedding 缓存（每条 fact 只 embed 一次，无缓存场景）
