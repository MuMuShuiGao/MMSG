# 召回 Metric 评测设计 v1

## 目标

为 `mmsg.memory.recall.Recaller` 建立独立于端到端 MCQ 的召回质量指标，能定位流水线各 stage 的瓶颈。

## 评测集

- **数据集**：LongMemEval（NeurIPS'24），purpose-built for personal AI assistant 长期记忆 QA
- **变体**：Oracle（仅 evidence sessions，~5MB）— 跑通阶段先用，后续切 S
- **来源**：HuggingFace `xiaowu0162/LongMemEval`，`longmemeval_oracle.json`
- **数据位置**：`eval/longmemeval/data/`（gitignore），CLI 接 `--data-path` 可配
- **抽样规模**：v1 跑 stratified 10 题，按 `question_type` 分层（跳过 `temporal-reasoning`，4 类 × ~2-3 题）
- **种子**：固定 seed=42

### 为什么选 LongMemEval

- 场景与 MMSG 1:1：单用户跨多 session 与 assistant 对话
- 题目第一人称，覆盖个人偏好/事实/状态/abstention
- 每题标注了 `answer_session_ids`（含证据的 session），评分对齐论文

## Hit 判定（Session 级）

```
evidence_message_ids = ⋃ messages(session) for session in answer_session_ids
fact F is hit ⇔ F.source_message_ids ∩ evidence_message_ids ≠ ∅
```

为何不用 turn 级：consolidator 会合并多 turn → 单条 fact 跨多个 message，turn 级粒度产生大量假阴。
为何不用 LLM-judge：成本高、噪声大；可作为二级信号留 TODO。

## 流水线改造（产线代码）

`mmsg/memory/recall.py` 加：

```python
@dataclass
class RecallTrace:
    discriminator: dict          # {need_recall: bool, query: str | None, raw: str}
    retrieval_candidates: list[Fact]   # hybrid_search 返回的全部候选（~60）
    rrf_top20: list[Fact]              # RRF 融合后截 top 20
    mmr_top5: list[Fact]               # MMR 后 top 5

class Recaller:
    async def recall_with_trace(self, user_msg: str) -> RecallTrace:
        """始终跑完所有 stage，无视判别器决策。供评测使用。"""
        ...

    async def recall_for_turn(self, user_msg: str) -> list[Fact]:
        trace = await self.recall_with_trace(user_msg)
        if not trace.discriminator["need_recall"]:
            return []
        return trace.mmr_top5
```

要点：
- `recall_with_trace` **始终跑完所有 stage**，判别器决策只记录不阻断
- 原 `recall_for_turn` 薄封装，运行时行为不变
- trace 内 fact 保留 `rrf_score / distance / bm25_rank` 字段供 metric 算 MRR/nDCG

## Metrics

每条记录 trace 后离线计算下表全部指标，全部按 `question_type` 分组报。

| Stage | k | 指标 |
|---|---|---|
| 判别器 | — | Precision / Recall / F1（gold = 非 abstention 题需召回，abstention 题不需召回） |
| Retrieval（候选池） | 10 / 20 / 30 / 60 | Recall@k |
| RRF | 5 / 10 / 20 | Recall@k + MRR + nDCG@k |
| MMR | 1 / 3 / 5 | Recall@k + MRR + nDCG@k + Precision@k |
| 端到端 | 5 | **realistic** Recall@5（尊重判别器，say no 视为返空）+ **oracle-gated** Recall@5（绕过判别器） |
| Abstention | — | FP rate = abstention 题 top-5 平均误召回的 fact 数 |

### Abstention 处理

- gold set = ∅，不参与 Recall@k 平均（分母 0 跳过）
- 单独报 FP rate
- 判别器 F1 计算把 abstention 题视为"不需召回"的正样本

## 代码结构

仿 `eval/personamem/`：

```
eval/longmemeval/
  __init__.py
  config.py     # variant, subset_size, k_values, seed
  dataset.py    # 加载 LongMemEval JSON，stratified 抽样
  ingest.py     # 复用 personamem 灌 haystack 模式
  runner.py     # 单题：ingest → recall_with_trace → 存原始 trace
  metrics.py    # 上述全部指标计算
  report.py     # Markdown + JSON → eval/results/<ts>_longmemeval/
  run.py        # CLI 入口
```

### 灌 haystack

LongMemEval 的 haystack_sessions 是多 session 对话，每 session 内 user/assistant 交替。flatten 成 `[{role, content}, ...]` 喂给现有 ingest 逻辑：
- `Curator` / `Recapper` / `Evolver` / `Consolidator` 全跑（与生产一致）
- Oracle 变体 haystack 极小，单题一次性灌完，consolidator 收尾跑一次

### CLI 用法

```bash
python -m eval.longmemeval.run \
  --data-path eval/longmemeval/data/longmemeval_oracle.json \
  --n 10 \
  --seed 42
```

输出 `eval/results/<timestamp>_longmemeval/`：
- `report.json`：每题完整 trace + 评分
- `report.md`：分组指标汇总表

## TODO（留档）

1. **LongMemEval S/M 变体**：成本数量级提升，需评估 embedding/LLM 预算
2. **temporal-reasoning 题支持**：要给 `SqliteStore.save_message` 和 `Consolidator` 写 fact 加 `created_at` 可选参数；需要回归测试 `Fact.created_at` 在 `agent/context.py:146` 等下游的展示行为
3. **LLM-judge 语义级 hit**：作为 session-level 的二级信号，验证 session 级是否漏召高语义相关 fact
4. **延迟 / 成本指标**：每 stage 耗时分布、embedding 调用数、LLM token 数
5. **failure case dump**：失败题目的 trace 全量打印，便于人工诊断

## 决策日志

| 决策 | 选择 | 备选 | 理由 |
|---|---|---|---|
| 评测集 | LongMemEval Oracle | LoCoMo / PersonaMem 反标 | 论文级标注，场景对齐，evidence session 直接可用 |
| Hit 粒度 | Session 级 | Turn 级 / LLM-judge | consolidator 合并 turn 不破坏；零额外标注成本 |
| Stage 拆分 | 4 stage 独立测 | 仅端到端 | metric 核心价值是定位瓶颈 |
| 指标集合 | Recall@k + MRR + nDCG + 判别器 F1 + abstention FP | 单 Recall@k / 全套 | 诊断能力 vs 实现成本平衡 |
| K 值 | 阶段扫 k | 单点 / 全曲线 | 直接看每段截断的损耗 |
| Trace 行为 | 始终跑完所有 stage | 尊重判别器 | 解耦判别器锅 vs 检索锅 |
| 规模 | 10 题 Oracle | 25 题 / 全量 500 | 先跑通 metric pipeline，5 分钟级反馈 |
| Timestamp 注入 | v1 跳过，跳过 temporal | 立即改产线 | 保持 v1 零产线侵入；temporal 改动留 TODO 一次性做 |
