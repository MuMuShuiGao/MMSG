# OpenClaw Memory 机制深度解析

> 基于 `extensions/memory-core/` + `src/memory/` + `src/plugin-sdk/memory-*` 源码

---

## 一、总体架构

Memory 机制由 **`memory-core` plugin** 实现（`extensions/memory-core/index.ts`），以 `kind: "memory"` 注册进 OpenClaw 插件系统。核心组成：

```
memory-core plugin
  ├── MemoryIndexManager     ← 索引/检索引擎（SQLite + 向量扩展）
  ├── memory_search 工具      ← Agent 可调用的语义搜索
  ├── memory_get 工具         ← 精确文件行读取
  ├── Dreaming（梦境）机制    ← 定时 LLM 记忆整合
  ├── Short-Term Promotion   ← 短期召回 → 长期记忆晋升
  └── Flush Plan             ← 会话压缩前记忆落盘
```

---

## 二、存储结构

### 2.1 内存文件布局（工作区）

| 路径 | 用途 |
|------|------|
| `MEMORY.md` | 规范根文件（常青知识，不衰减） |
| `memory.md` | 遗留根文件（`src/memory/root-memory-files.ts:8`） |
| `memory/YYYY-MM-DD.md` | 日期记忆文件（参与时间衰减） |
| `memory/*.md` | 主题记忆文件（常青） |
| `memory/.dreams/short-term-recall.json` | 短期召回状态（`short-term-promotion.ts:56`） |
| `memory/.dreams/daily-ingestion.json` | 日常梦境摄取状态 |
| `memory/.dreams/session-corpus/` | 会话文本语料 |
| `memory/dreaming/` | 梦境 narrative 输出目录 |

### 2.2 SQLite 索引数据库

数据库用 Node.js 内置 `node:sqlite`（`DatabaseSync`）打开，WAL 模式，`busy_timeout=5000ms`（`manager-db.ts:18-20`）。

三张核心表：

| 表名 | 用途 |
|------|------|
| `chunks_vec` | 向量块（`sqlite-vec` 扩展，Float32 blob） |
| `chunks_fts` | FTS5 全文检索表 |
| `embedding_cache` | 按 `(provider, model, provider_key, hash)` 缓存 embedding 向量 |

数据库路径：per-agent workspace dir，由 `resolveAgentWorkspaceDir` 解析。

---

## 三、索引管理器（MemoryIndexManager）

### 3.1 单例缓存

```ts
// extensions/memory-core/src/memory/manager.ts:72-83
const MEMORY_INDEX_MANAGER_CACHE_KEY = Symbol.for("openclaw.memoryIndexManagerCache");
const { cache: INDEX_CACHE, pending: INDEX_CACHE_PENDING } =
  resolveSingletonManagedCache<MemoryIndexManager>(MEMORY_INDEX_MANAGER_CACHE_KEY);
```

通过 `MemoryIndexManager.get()` 获取实例，缓存键 = `agentId + workspaceDir + settings + providerRequirement + purpose`（`manager.ts:168-182`）。

**Purpose 三种模式**：

| 模式 | 行为 |
|------|------|
| `"default"` | 完整功能：启用 watcher + session 监听 + 定时同步 |
| `"status"` | transient，不启动 watcher，不缓存 |
| `"cli"` | transient，不启动 watcher，不缓存 |

### 3.2 Embedding Provider 选择逻辑

```ts
// extensions/memory-core/src/memory/manager.ts:145-166
// provider=none        → fts-only 模式（纯关键词）
// provider=auto/local  → optional（本地失败可降级）
// 显式配置非 none      → required（失败报错）
```

默认 provider 为 `"openai"`（`embeddings.ts:37`），支持以下适配器：
- openai、google、voyage、mistral、deepinfra、amazon-bedrock、lmstudio
- `local`（llama.cpp，需安装 `@openclaw/llama-cpp-provider` 插件）

---

## 四、检索机制

### 4.1 混合检索（Hybrid Search）

`mergeHybridResults`（`hybrid.ts:52`）将向量检索和关键词检索结果合并：

```ts
score = vectorWeight * vectorScore + textWeight * textScore
```

合并后依次经过：
1. **时间衰减**（可选）→ 调整 score
2. **按 score 降序排列**
3. **MMR 重排**（可选）→ 去除冗余

### 4.2 向量检索

- 使用 `sqlite-vec` 扩展做 KNN 查询
- 过采样因子 `VECTOR_KNN_OVERSAMPLE_FACTOR = 8`（`manager-search.ts:19`）
- 无可用 vec0 索引时退化为批量扫描（`FALLBACK_VECTOR_BATCH_SIZE = 256`，`manager-search.ts:24`），每批后 `setImmediate` 让出事件循环，防止阻塞 I/O

### 4.3 关键词检索（BM25 FTS5）

```ts
// extensions/memory-core/src/memory/hybrid.ts:41-50
export function bm25RankToScore(rank: number): number {
  if (rank < 0) {
    const relevance = -rank;
    return relevance / (1 + relevance);
  }
  return 1 / (1 + rank);
}
```

FTS 查询通过 `buildFtsQuery` 构造，提取 unicode 词素（含 CJK 三元组支持）加引号后用 `AND` 连接。

### 4.4 时间衰减（Temporal Decay）

> `temporal-decay.ts` — 默认关闭（`enabled: false, halfLifeDays: 30`）

衰减公式：`score × e^(−λ × age_days)`，其中 `λ = ln(2) / halfLifeDays`。

| 文件类型 | 时间处理方式 |
|---------|------------|
| `MEMORY.md` 及 `memory/` 下非日期命名文件 | 常青，不衰减 |
| `memory/YYYY-MM-DD.md` | 从文件名解析日期（`DATED_MEMORY_PATH_RE`） |
| 其他文件 | 用 `fs.stat().mtimeMs` 作为时间戳 |

### 4.5 MMR 多样性重排

> `mmr.ts` — 默认关闭（`enabled: false, lambda: 0.7`）

实现 Carbonell & Goldstein (1998) 的 Maximal Marginal Relevance：

```ts
MMR_score = λ × relevance − (1−λ) × max_similarity_to_selected
```

相似度用 Jaccard（token 集合交并比），支持 CJK 感知 tokenizer（`tokenize.ts`）。迭代选取：先取最高分，再每轮选令 MMR score 最大的候选。

---

## 五、同步与文件监听

### 5.1 文件系统监听

非 transient 实例在构造时启动（`manager.ts:416-418`）：

```ts
this.ensureWatcher();         // chokidar 监听 memory 目录
this.ensureSessionListener(); // 会话 transcript 更新订阅
this.ensureIntervalSync();    // 定时兜底同步
```

忽略目录（`manager-sync-ops.ts:131-139`）：`.git`、`node_modules`、`.pnpm-store`、`.venv`、`__pycache__` 等。

Session dirty debounce = **5000ms**（`SESSION_DIRTY_DEBOUNCE_MS`），防止高频写入触发重复同步。

### 5.2 增量同步

`MemorySessionStartupFileState` 记录会话文件的 `lastSize` / `pendingBytes` / `pendingMessages`，增量读取 `SESSION_DELTA_READ_CHUNK_BYTES = 64KB`。每处理 `SESSION_SYNC_YIELD_EVERY = 10` 个文件后让出事件循环（`manager-sync-ops.ts:127-128`）。

### 5.3 Embedding 缓存

按 `(provider, model, provider_key, content_hash)` 唯一存储，批量加载上限 400 条/批，结果写回 SQLite `embedding_cache` 表（`manager-embedding-cache.ts`）。

---

## 六、Dreaming（记忆整合）机制

### 6.1 三个阶段

| 阶段 | HTML 标记 | 功能 |
|------|----------|------|
| **Light Sleep** | `<!-- openclaw:dreaming:light:start/end -->` | 日期记忆文件摘要，lookback N 天，去重相似度阈值 `dedupeSimilarity` |
| **REM Sleep** | `<!-- openclaw:dreaming:rem:start/end -->` | 跨会话模式识别，`minPatternStrength` 过滤 |
| **Short-Term Promotion** | `memory/.dreams/short-term-recall.json` | 高频召回片段晋升为长期记忆 |

### 6.2 Cron 触发

通过 `api.registerCron` 注册受管 cron 任务（`dreaming.ts`）：

- cron 表达式调度（`DEFAULT_MEMORY_DREAMING_FREQUENCY`）
- `sessionTarget: "main" | "isolated"` 控制执行上下文
- `wakeMode: "now"` 立即唤醒 agent

遗留命名兼容（`dreaming.ts:11-18`）：`LEGACY_MEMORY_LIGHT_DREAMING_CRON_NAME`、`LEGACY_MEMORY_REM_DREAMING_CRON_NAME`。

### 6.3 Short-Term Promotion 晋升条件

```ts
// extensions/memory-core/src/short-term-promotion.ts:47-49
DEFAULT_PROMOTION_MIN_SCORE = 0.75
DEFAULT_PROMOTION_MIN_RECALL_COUNT = 3       // 至少被召回 3 次
DEFAULT_PROMOTION_MIN_UNIQUE_QUERIES = 2     // 至少来自 2 个不同查询
```

短期召回条目上限 512 条，单条 snippet 上限 800 字符（`SHORT_TERM_RECALL_MAX_SNIPPET_CHARS`）。

---

## 七、Agent 工具接口

两个工具注册于 `extensions/memory-core/index.ts:200-206`：

### `memory_search`

```json
{
  "query": "string (required)",
  "maxResults": "integer (optional)",
  "minScore": "number (optional)",
  "corpus": "memory | wiki | all | sessions (optional)"
}
```

工具描述中注明**"Mandatory recall step"**：回答关于历史工作、决策、日期、人员、偏好、待办前必须先调用。`corpus=sessions` 仅检索会话 transcript 块，`corpus=memory` 仅检索记忆文件。

### `memory_get`

```json
{
  "path": "string (required)",
  "from": "integer (optional)",
  "lines": "integer (optional)",
  "corpus": "memory | wiki | all (optional)"
}
```

精确读取文件指定行范围，超出时返回截断/续读信息。`corpus=wiki` 读取已注册的编译 wiki 补充材料。

---

## 八、Flush Plan（记忆落盘）

会话接近压缩阈值时触发（`flush-plan.ts`）：

| 参数 | 值 |
|------|-----|
| Soft token 上限 | `DEFAULT_MEMORY_FLUSH_SOFT_TOKENS = 4000` |
| Force flush 字节 | `DEFAULT_MEMORY_FLUSH_FORCE_TRANSCRIPT_BYTES = 2MB` |
| 落盘目标 | `memory/YYYY-MM-DD.md`（**追加**，不覆盖） |

只读保护文件（flush 期间禁止写入）：`MEMORY.md`、`DREAMS.md`、`SOUL.md`、`TOOLS.md`、`AGENTS.md`。

---

## 九、系统提示注入

`buildPromptSection`（`prompt-section.ts`）在 agent 启动时向系统提示注入 `## Memory Recall` 节：

| 可用工具 | 注入内容 |
|---------|---------|
| `memory_search` + `memory_get` | 完整召回指导（先搜索，再精确读取） |
| 仅 `memory_search` | 语义搜索指导 |
| 仅 `memory_get` | 精确行读取指导 |

`citationsMode !== "off"` 时追加 `Source: <path#line>` 引用格式说明。

---

## 十、完整数据流

```
文件系统/Session 变更
       ↓  (chokidar / session listener)
  Dirty 标记 + Debounce (5s)
       ↓
  增量读取 + Hash 对比
       ↓
  Embedding 缓存查询 (SQLite embedding_cache)
       ↓  cache miss
  Provider.embedBatch()  →  写回缓存
       ↓
  写入 chunks_vec (sqlite-vec) + chunks_fts (FTS5)
       ↓
  ─────────────── 检索时 ───────────────
  memory_search 调用
       ↓
  向量 KNN + BM25 关键词并行查询
       ↓
  mergeHybridResults
    → 时间衰减 (可选, halfLife=30d)
    → MMR 重排 (可选, λ=0.7)
       ↓
  返回 MemorySearchResult[]  →  Agent 上下文
       ↓
  ─────────────── Dreaming cron ───────────────
  Short-Term Promotion / Light Sleep / REM Sleep
       ↓
  持久化写入 memory/YYYY-MM-DD.md
```

---

## 十一、关键源码索引

| 功能 | 文件 |
|------|------|
| Plugin 注册入口 | `extensions/memory-core/index.ts` |
| 索引管理器 | `extensions/memory-core/src/memory/manager.ts` |
| 混合检索合并 | `extensions/memory-core/src/memory/hybrid.ts` |
| 时间衰减 | `extensions/memory-core/src/memory/temporal-decay.ts` |
| MMR 重排 | `extensions/memory-core/src/memory/mmr.ts` |
| 向量/关键词搜索 | `extensions/memory-core/src/memory/manager-search.ts` |
| Embedding 缓存 | `extensions/memory-core/src/memory/manager-embedding-cache.ts` |
| 文件同步/监听 | `extensions/memory-core/src/memory/manager-sync-ops.ts` |
| SQLite 数据库 | `extensions/memory-core/src/memory/manager-db.ts` |
| Dreaming 调度 | `extensions/memory-core/src/dreaming.ts` |
| Dreaming 阶段 | `extensions/memory-core/src/dreaming-phases.ts` |
| 短期晋升 | `extensions/memory-core/src/short-term-promotion.ts` |
| Flush Plan | `extensions/memory-core/src/flush-plan.ts` |
| 系统提示注入 | `extensions/memory-core/src/prompt-section.ts` |
| 根记忆文件定位 | `src/memory/root-memory-files.ts` |

---

## 十二、设计决策与方案对比

以下所有结论均直接源自代码注释、常量命名、降级路径、兼容性分支和测试描述，不做推测。

---

### 12.1 为什么用 SQLite 而不是独立向量数据库

**选择**：`node:sqlite`（Node.js 内置）+ `sqlite-vec` 扩展，所有索引数据写入 SQLite 文件。

**代码证据**：
- `manager-db.ts` 直接 `new DatabaseSync(dbPath)`，无外部进程依赖。
- 当 `sqlite-vec` 加载失败时系统不崩溃，而是进入 **向量降级模式**（`manager-vector-warning.ts:8`）：仅 FTS5 关键词检索继续工作，日志输出 `"chunks_vec not updated — sqlite-vec unavailable"`。

**设计意图**：零额外进程，用户无需安装 PostgreSQL/Weaviate/Qdrant 等服务。SQLite WAL 模式保证多进程并发（`busy_timeout=5000ms`）；降级路径保证无 GPU / 无网络时系统仍可用。

**未选方案的痕迹**：`MemoryProviderLifecycleState`（`manager-provider-state.ts:22-47`）枚举了五个状态：`pending / active / degraded / fallback-active / fts-only`。`fts-only` 即"放弃向量，纯关键词"降级态，说明设计者明确预见了向量不可用的场景并为此保留了完整运行路径。

---

### 12.2 为什么混合检索而不是纯向量或纯关键词

**选择**：向量（cosine similarity）+ BM25 FTS5 加权合并，默认均开启。

**代码证据**（`hybrid.ts:52-136`）：
```ts
score = vectorWeight * vectorScore + textWeight * textScore
```

- `buildFtsQuery` 将查询拆成 unicode token 加引号 AND 连接——这是标准精确关键词匹配，不依赖 embedding model。
- `scoreFallbackKeywordResult`（`manager-search.ts:48-73`）在 FTS 之上还额外加了路径名 boost（+0.18/token）和文本密度 boost，说明单纯 BM25 分数被认为不足够。
- 向量检索有 `VECTOR_KNN_OVERSAMPLE_FACTOR = 8` 过采样，用于补偿 HNSW 近似召回的漏召情况。

**未选纯向量的证据**：`provider=none` 配置直接跳过所有 embedding 相关操作，仅运行 FTS（`manager.ts:145-166`），说明关键词路径是设计上的一等公民，而非向量的降级 fallback。

---

### 12.3 为什么 FTS 删除时不按 model 过滤

**代码证据**（`manager-fts-state.ts:13-16`）：

```ts
// Lexical search is model-agnostic, so refreshed/deleted files must not
// leave old-model FTS rows behind for the same path/source.
params.db.prepare(`DELETE FROM ${tableName} WHERE path = ? AND source = ?`)
  .run(params.path, params.source);
```

注释直接说明：词法搜索与 embedding model 无关，若按 model 分区删除会残留旧 model 的 FTS 行。向量表 `chunks_vec` 的删除则需携带 model 信息（不同 model 维度不同，索引不兼容）。

---

### 12.4 为什么 Embedding 有批量失败熔断

**选择**：连续失败 `MEMORY_BATCH_FAILURE_LIMIT = 2` 次后禁用 embedding（`manager-batch-state.ts:2`）。

**设计逻辑**：embedding API 失败（网络、鉴权、quota）不应反复重试阻塞索引流程。两次失败后自动切到 FTS-only 模式，保证索引同步流水线不卡死。`forceDisable` 参数允许单次不可恢复错误（如 model 不存在）直接触发熔断，不需要攒够两次。

---

### 12.5 为什么 Embedding 超时对 local provider 更长

**代码证据**（`manager-embedding-timeout.test.ts:15-23`）：

| Provider | query 超时 | batch 超时 |
|---------|-----------|-----------|
| `openai`（hosted） | 60s | 120s |
| `local`（llama.cpp） | 300s | 600s |

本地 GGUF 模型在 CPU 上首次推理远慢于云 API，超时必须单独放宽。测试用例直接固化这些数值作为契约，防止被意外收紧。

---

### 12.6 为什么向量扫描要分批 yield 事件循环

**代码证据**（`manager-search.ts:20-28`）：

```ts
// Scan fallback vector rows in bounded batches so large chunk tables (no usable
// vec0 index) cannot pin the main thread for multi-second windows and starve
// channel I/O / liveness signals. Matches the session-indexing yield pattern
// introduced in #76978 for the same class of bug. Issue #81172.
const FALLBACK_VECTOR_BATCH_SIZE = 256;

function yieldToEventLoop(): Promise<void> {
  return new Promise<void>((resolve) => { setImmediate(resolve); });
}
```

注释明确引用了两个 issue：`#76978`（session 索引卡死先例）和 `#81172`（向量扫描重现）。根因是 Node.js 单线程 + `DatabaseSync` 同步 API，大表全扫会锁住事件循环数秒，导致 channel I/O 掉包。解决方案：每扫 256 行插入一次 `setImmediate`，与 session 同步路径的 `SESSION_SYNC_YIELD_EVERY = 10` 机制完全对称。

---

### 12.7 为什么 MMR 和时间衰减默认关闭

**代码证据**：
- `DEFAULT_MMR_CONFIG = { enabled: false, lambda: 0.7 }`（`mmr.ts:26`）
- `DEFAULT_TEMPORAL_DECAY_CONFIG = { enabled: false, halfLifeDays: 30 }`（`temporal-decay.ts:10`）

两者都是可选增强，非零成本：MMR 需要对所有候选对做 Jaccard 相似度计算（O(n²) token 比较），时间衰减需要为每条结果调用 `fs.stat`（异步 I/O）。默认关闭让基础检索路径最快，用户按需开启。

---

### 12.8 为什么 CJK 用 unigram + bigram 而不是分词库

**代码证据**（`tokenize.ts:3-9`，注释）：

```
// Originally introduced for memory MMR re-ranking; now also used by the dreaming
// dedupe path so similar-but-not-identical CJK candidates do not slip past the
// Jaccard threshold (issue #80613).
```

选用 unigram（单字）+ 仅相邻字符的 bigram，原因：
1. 无需引入 `jieba`/`kuromoji` 等分词外部依赖，零运行时体积。
2. Bigram 只在原文相邻字符间生成（`cjkData[i+1].index === cjkData[i].index + 1`），防止跨词产生虚假 bigram（注释举例：`"欢你"` 不会在 `"我喜欢hello你好"` 中出现）。
3. issue #80613 是 CJK 候选在 dreaming dedup 中未被正确过滤的具体 bug，这个 tokenizer 同时服务于 MMR 和 dreaming 两个路径。

---

### 12.9 为什么 Dreaming 要独立于主会话用 subagent 执行

**代码证据**（`dreaming-narrative.ts:93-101`，注释）：

```ts
// Narrative generation is best-effort. Keep the timeout bounded so a stalled
// diary subagent does not leave the parent dreaming cron job "running" for
// many minutes after the reports have already been written. The previous 15 s
// limit was empirically too tight for warm-gateway runs across light, REM, and
// deep phases — even unblocked LLM calls hit it on the first sweep after a
// restart. 60 s gives realistic latency headroom while still capping the
// worst case at one minute, well below the multi-minute stall the original
// comment warned against.
const NARRATIVE_TIMEOUT_MS = 60_000;
```

Dreaming 的 Narrative（日记体文本）通过 `subagent.run` 在独立 session 中执行（`sessionTarget: "isolated"`），原因：
- Narrative 是 **best-effort**（"尽力而为"），失败不应影响主会话。
- 15s 超时被实验证明过紧（注释明确写"empirically too tight"），调整为 60s。
- cron job 本身与主 agent session 解耦，防止梦境处理阻塞用户交互。

---

### 12.10 为什么原子重建索引需要 rename + retry

**代码证据**（`manager-atomic-reindex.ts:28-30`）：

```ts
const transientFileErrorCodes = new Set(["EBUSY", "EPERM", "EACCES"]);
const defaultMaxRenameAttempts = 6;
const defaultRenameRetryDelayMs = 25;
```

完整重建索引时先写临时文件，再 `rename` 替换旧文件（atomic swap）。Windows 上 SQLite WAL 文件可能被其他进程短暂持有（`EBUSY`/`EPERM`/`EACCES`），所以 rename 最多重试 6 次，每次间隔 25ms；rm（清理临时文件）最多重试 10 次，每次间隔 50ms。这是为 Windows 文件系统行为专门加固的容错路径。

---

### 12.11 为什么 MemoryIndexManager 实例用 Symbol 挂 globalThis

**代码证据**（`manager-cache.ts:13-16`）：

```ts
export function resolveSingletonManagedCache<T>(cacheKey: symbol): ManagedCache<T> {
  const resolved = resolveGlobalSingleton<unknown>(cacheKey, () => ({
    cache: new Map<string, T>(),
    pending: new Map<string, Promise<T>>(),
  }));
```

OpenClaw 的 plugin 加载器可能多次 `import` 同一模块（bundle vs 外部 plugin 的模块边界），导致模块级 `Map` 变量重复初始化。用 `Symbol.for("openclaw.memoryIndexManagerCache")` 绑到 `globalThis` 确保进程内唯一缓存，即使模块被加载两次也不会产生两个独立的 manager 池。

---

### 12.12 文件监听的 watch pressure 阈值

**代码证据**（`watch-pressure.ts:4`）：

```ts
export const MEMORY_WATCH_PRESSURE_WARNING_THRESHOLD = 2_000;
```

chokidar 监听超过 2000 个路径/目录时发出警告（仅一次，`state.shown` 防重复）。这个阈值是经验值，反映了 chokidar 在 macOS（FSEvents）和 Linux（inotify）上的系统资源压力拐点，超过后 watch 事件延迟会明显上升。
