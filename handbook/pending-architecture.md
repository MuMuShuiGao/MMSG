# PENDING 延迟归档架构

> 目标：通过引入 PENDING.md 暂存区，减少 memory.md / self.md 的写入频率，提升 system prompt 的 KV cache 命中率。

---

## 整体管道

```
对话流 ──→ MemoryCurator（增量产出，对比 memory.md）
               │
               ▼
          PENDING.md（自然语言 + 前缀）
               │
       ───────┼────────（12h 或 ≥1000 字）
       ▼                ▼
   Evolver          Evolver
  _merge_memory    _update_self
       │                │
       ▼                ▼
   memory.md         self.md
```

---

## 组件职责

| 组件 | 改动 | 说明 |
|------|------|------|
| **MemoryCurator** | 修改 | 对比 memory.md 产出增量（前缀 `新增`/`更正`）→ 追加 PENDING.md。**不写主文件** |
| **SelfCurator** | **停掉** | self.md 只在 Evolver 阶段从 PENDING 选择性吸收更新 |
| **Evolver** | **新增** | `mmsg/memory/engines/default/evolver.py`。独立 `serve()` 循环，双 LLM 调用合并 memory + 更新 self，完成后**全量清空** PENDING.md |
| **Consolidator** | 不动 | — |
| **Merger** | 不动 | — |
| **Recapper** | 不动 | — |
| **current_context.md** | 不动 | — |
| **self.md** | 不动 | 由 Evolver 更新 |
| **memory.md** | 不动 | 由 Evolver 更新 |

---

## PENDING.md

- **位置**：`memory/PENDING.md`
- **格式**：自然语言增量，带轻量前缀
- **无结构性 tag**：不加 `[identity]` / `[preference]` 等结构化标记
- **无消息区间标记**：不加 `[msg:142~156]`
- **无幂等标记**：curator 水位机制已防重复
- **Evolver 跑完后全量清空**

```
- 新增：用户偏好终端操作，对 IDE GUI 无感
- 更正：用户已从 AWS 迁至 Azure
- 新增：用户关注 WASM 浏览器端性能
```

---

## Evolver 配置

| 参数 | 值 | 说明 |
|------|-----|------|
| `min_hours` | 12 | 距上次 Evolver ≥12h 触发 |
| `min_chars` | 1000 | PENDING ≥1000 字触发 |
| `poll_interval` | 3600 | 每小时检查一次 |
| 触发关系 | OR | 满足任一即触发 |

---

## Evolver 双 LLM 调用

### Step 1：`_merge_memory(current_memory, pending)`

- 读取 memory.md 当前全文 + PENDING.md 全文
- LLM 做去重、冲突解决（`更正` 覆盖旧值）、分类、去噪音
- 输出全新的 memory.md

### Step 2：`_update_self(pending)`

- 独立 LLM 调用，不同 prompt
- 从同一份 PENDING 选择性吸收
- 只更新 self.md 的固定 section，无关项直接忽略

---

## MemoryCurator 增量逻辑

- curator 保留读取 memory.md，让 LLM 对比已有画像
- LLM 只输出**真正新增/更正**的部分
- 前缀约定：`新增`（新事实）、`更正`（覆盖旧值）
- 追加写入 PENDING.md，不做覆盖

---

## KV cache 收益

- **memory.md**：12h 才变一次，每天 ≤2 次变更
- **self.md**：随 Evolver 一起更新，频率同上
- **PENDING.md**：不注入 system prompt，高频变化不影响 cache
- agent 即时信息由**近期对话原文 + current_context 摘要**覆盖，不依赖 pending

---

## 决策记录

| # | 决策 | 理由 |
|---|------|------|
| 1 | 单文件 PENDING.md | 简单，解耦 |
| 2 | 自然语言增量 + 前缀 `新增`/`更正` | curator 改动小，LLM merge 有线索 |
| 3 | 只存 memory 事实，SELF 不经过 pending | self 更新频率低，由 Evolver 处理即可 |
| 4 | Rewrite 走 LLM 智能合并，不拼接 | 冲突解决和去重能力 |
| 5 | Curator 增量产出 | pending 要增量化 |
| 6 | 不带消息区间标记 | curator 水位已防重复 |
| 7 | Evolver 两路独立 LLM 调用 | prompt 各自专注 |
| 8 | Evolver 独立 `serve()` 循环 | 解耦，架构一致性 |
| 9 | PENDING 全量清空 | Evolver 已全部吸收 |
| 10 | 触发阈值 12h / 1000字，OR | 保底不溢出 |
| 11 | SelfCurator 停掉 | 功能并入 Evolver |
| 12 | Curator 保留读 memory.md | 增量更精准 |
