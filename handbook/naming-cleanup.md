# 命名清理方案

> 当前 consolidate/curate/merge 三个词在记忆层、主动引擎、推理层各自使用，职责重叠、含义混乱。本文列出所有需要改名的地方。

---

## 一、记忆层三兄弟（各自独立 worker）

### 1. Consolidator → FactExtractor

| 改前 | 改后 |
|------|------|
| `memory/consolidator.py` | `memory/fact_extractor.py` |
| `class Consolidator` | `class FactExtractor` |
| `Consolidator._consolidate()` | `FactExtractor._extract()` |
| 配置段 `[consolidator]` | `[fact_extractor]` |
| `config.py: consolidator()` | `config.py: fact_extractor()` |
| log: `mmsg.memory.consolidator` | `mmsg.memory.fact_extractor` |

**职责：** 扫 user 原话 → LLM 提取原子 fact → embed → 入向量库

### 2. Merger → 保持不变（命名准确）

| 改前 | 改后 |
|------|------|
| `memory/merger.py` | 不动 |
| `class Merger` | 不动 |

**职责：** 向量去重——cos > 0.97 的近重复 fact 合并

### 3. MemoryCurator → ProfileCurator

| 改前 | 改后 |
|------|------|
| `memory/curator.py` | 不动，类名改 |
| `class MemoryCurator` | `class ProfileCurator` |
| 变量名 `memory_curator` | `profile_curator` |
| log: `mmsg.memory.curator` | `mmsg.memory.profile_curator` |

**职责：** 扫 user 原话 → LLM 更新 memory.md 用户画像

---

## 二、主动引擎层

### ProactiveEngine._consolidate → _review_notes

| 改前 | 改后 |
|------|------|
| `ProactiveEngine._consolidate()` | `ProactiveEngine._review_notes()` |
| `ProactiveEngine.trigger_consolidate()` | `ProactiveEngine.trigger_review_notes()` |
| 配置 `consolidate_interval` | `review_interval` |
| 配置提示注释 | "轮询间隔（秒）" |
| Dashboard API: `/api/curiosity/trigger-consolidate` | `/api/curiosity/trigger-review` |

**职责：** 翻 pending notes → LLM 合并/筛选/打分

---

## 三、推理层（LLMContext 的滑动窗口摘要）

### LLMContext._schedule_consolidate → _schedule_compress

| 改前 | 改后 |
|------|------|
| `LLMContext._schedule_consolidate()` | `LLMContext._schedule_compress()` |
| `LLMContext._do_consolidate()` | `LLMContext._do_compress()` |

**职责：** 对话段过期 → 触发 RecentRecapper 压缩为 current_context.md 摘要

---

## 四、涉及的其他文件（连锁改名）

### `app.py`
- `memory_curator` → `profile_curator`
- `consolidator` → `fact_extractor`
- import 路径改

### `dashboard/api.py`
- 参数名 `memory_curator` → `profile_curator`
- 参数名 `consolidator` → `fact_extractor`
- API 路由 `/api/memory/curate` → 暂不动（改不改路由看前端用没用）
- 变量名同步

### `eval/personamem/ingest.py`
- `consolidate_every` 参数 → `compress_every`
- 注释 "short-term consolidate" → "short-term compression"
- import `MemoryCurator` → import `ProfileCurator`

### `config.py`
- 配置段 `[consolidator]` → `[fact_extractor]`
- 函数 `consolidator()` → `fact_extractor()`

### `storage/sqlite.py`
- 注释 "consolidator 用" → "fact_extractor 用"
- `get_user_messages_since` 注释

### `memory/protocol.py`
- `consolidate()` 方法 → 保持不动（这是接口方法，只改内部委托的注释）

### `memory/engines/default/engine.py`
- `DefaultMarkdownLayer.consolidate()` → 保持不动（接口方法）

### `memory/engines/default/recapper.py`
- 注释 "consolidate()" → 不强制改（内部文档，低优先级）

---

## 五、最终语义

| 新名称 | 干什么 | 产出 |
|--------|--------|------|
| `FactExtractor` | 从对话提取原子事实 | SQLite 向量 fact |
| `Merger` | 向量相似度去重合并 | 合并后的 fact |
| `ProfileCurator` | 提炼用户画像 | `memory.md` |
| `ProactiveEngine._review_notes` | 审阅 pending notes | 合并/打分/筛选后的候选 |
| `LLMContext._schedule_compress` | 对话摘要压缩 | `current_context.md` |

**关键词不再重叠：** extract / merge / curate / review / compress —— 五个词五个活。
