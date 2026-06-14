# 主动聊天（Proactive Chat）设计决策表

> 生成日期: 2026-06-14  
> grill 决策：48 个问题逐一敲定。实现前回看本文档。

---

## 一、产品定位

| # | 问题 | 决策 | 备注 |
|----|------|------|------|
| 1 | 主动聊天的气质是什么 | 人格化、有好奇心、体贴，像朋友 | 不是定时播报，不是事件告警 |
| 2 | 触发条件以什么为主 | C 为主（agent 自己产生好奇），A（memory 变化）/B（外部信息接入）为辅 | |
| 3 | 刚聊完 vs 安静 vs 当日主动次数怎么调节 | 动态自适应：聊越多越沉默，越安静越活跃，今日主动次数越多越克制 | 反向调节 |
| 4 | agent 不开口时在干什么 | 后台活跃——翻 notes、整理、筛选、默默消化 | 即使不说话也在"心里活动" |

---

## 二、Curiosity Note 生命周期

| # | 问题 | 决策 |
|----|------|------|
| 5 | note 怎么产生 | 两层：第一层 AfterTurn 生成原料（新鲜），第二层固定间隔整理筛选（跨会话发现模式） |
| 6 | 存储方式 | SQLite 新表 `curiosity_note` |
| 7 | 表结构 | id, session_id, content, category, quality, needs_research, status, triggered_at, merged_from, created_at, updated_at |
| 8 | note 与推送的关联 | C 方案——推送时记录 triggered_at，整理时 LLM 结合关联信息判断是否闭环，不自动关闭 |
| 9 | 存储位置 | 模型放 `storage/models.py`，CRUD 放 `proactive/notes.py` |
| 10 | 建表职责 | `SqliteStore._init_tables()` 统一管理 |

---

## 三、推送时机与决策

| # | 问题 | 决策 |
|----|------|------|
| 11 | 静默阈值 | 三档：强 2h / 中 4h（默认）/ 弱 24h |
| 12 | 深夜处理 | 0:00-7:00 闭嘴，整理也停，直接睡到 quiet_end |
| 13 | 推送决策公式 | `score = 0.5×内容质量(1-5) + 0.3×静默收益(1-5) + 0.2×克制惩罚(1-5) - 3.0`，>0 即推 |
| 14 | 内容质量打分 | LLM 在整理阶段直接给 1-5 分 |
| 15 | 静默收益映射 | <1h=1, 1-2h=2, 2-4h=3, 4-8h=4, >8h=5 |
| 16 | 克制惩罚映射 | 今日0次=5, 1次=3, 2次=2, 3次以上=1 |
| 17 | 多条候选命中 | 只推得分最高的那一条 |
| 18 | 推送前确认 | 不需要 |
| 19 | 冷启动 | 沉默等待，不主动说话 |

---

## 四、推送渠道与分发

| # | 问题 | 决策 |
|----|------|------|
| 20 | 推送渠道 | config.toml `[proactive].channel` 指定，如 `"qqbot"` |
| 21 | 静默感知范围 | 所有渠道取最近活跃时间（max），不是只看推送渠道 |
| 22 | 消息分发 | 走 MessageBus `publish_outbound`，自然排队，不打断被动回复 |
| 23 | 每次推送条数 | 一条 |

---

## 五、配置

| # | 问题 | 决策 |
|----|------|------|
| 24 | 配置格式 | 独立的 `[proactive]` 段，类比 `[qqbot]` / `[feishu]` |
| 25 | 配置字段 | channel, intensity (strong/medium/weak), quiet_start, quiet_end |

```toml
[proactive]
channel = "qqbot"
intensity = "medium"
quiet_start = "00:00"
quiet_end = "07:00"
```

---

## 六、引擎架构

| # | 问题 | 决策 |
|----|------|------|
| 26 | 模块位置 | `mmsg/proactive/` 独立模块 |
| 27 | 文件拆分 | engine.py / notes.py / prompts.py / decision.py |
| 28 | 引擎复用程度 | B 方案——共用 Reasoner 类，不同实例，各自独立的提示词和循环 |
| 29 | 是否共用 AgentLoop 的队列 | 不共用——两个独立协程，各自自己的循环 |
| 30 | LLM 调用方式 | 轻量推送直接调 LLM；needs_research=true 时跑完整 Reasoner（max_steps=8） |
| 31 | 主动提示词 | 不用 SystemPromptBuilder，直接用字符串，LLMContext 传 `system_builder=None` |
| 32 | LLMContext | 共用类，不同实例，`summarize_every=999` 禁用后台摘要压缩 |

---

## 七、基础设施共享

| # | 问题 | 决策 |
|----|------|------|
| 33 | LLM provider | 共享同一实例，无状态的 HTTP 调用，无冲突 |
| 34 | SqliteStore | 共享同一实例，WAL 模式 + asyncio 单线程自然串行化 |
| 35 | Memory | 共享同一实例，主动引擎只读不写 |
| 36 | Tools | 共享 tool_registry，各引擎实例自行声明启用哪些 |

---

## 八、运行生命周期

| # | 问题 | 决策 |
|----|------|------|
| 37 | serve() 方式 | 单 `serve()` 方法，跟 AgentLoop 风格一致，`asyncio.create_task(proactive.serve())` 一行启动 |
| 38 | AfterTurn 订阅 | 主动引擎 `serve()` 内部通过 `agent_bus.subscribe("after_turn", ...)` 注册 |
| 39 | 启动恢复 | `SELECT MAX(created_at) FROM message` 恢复 last_active_at；当日次数归 0；正常等下一轮整理 |
| 40 | 循环节奏 | 整理层固定间隔（15-30 分钟），推送层自适应决策 |
| 41 | 失败处理 | AAA——curiosity 生成失败、整理 LLM 失败、推送生成失败，全部跳过不重试 |
| 42 | 并发处理 | 两个引擎并行无锁，不排队 |

---

## 九、提示词

| # | 问题 | 决策 |
|----|------|------|
| 43 | 好奇心生成提示词 | 独立起草，AfterTurn 时主动引擎用自己的 LLM 调用生成 notes |
| 44 | 整理筛选提示词 | 独立起草，合并 + 打分 + 标记过时 + 决定是否值得说 |
| 45 | 提示词后续 | 实现时再迭代优化 |

---

## 十、仪表盘

| # | 问题 | 决策 |
|----|------|------|
| 46 | 是否展示 curiosity notes | C——可交互：查看 pending notes、手动 dismiss、提升优先级 |

---

## 十一、架构依赖图

```
mmsg/proactive/
├── engine.py       # 主循环: 醒来→整理→决策→推送
│   ├── → from .notes import NoteStore
│   ├── → from .prompts import CURIOSITY_PROMPT, CONSOLIDATE_PROMPT, RESEARCH_SYSTEM_PROMPT
│   └── → from .decision import should_push
├── notes.py        # SQLite CRUD，from mmsg.storage.models import CuriosityNote
├── prompts.py      # 纯字符串常量
└── decision.py     # 纯函数，无依赖

mmsg/storage/
├── models.py       # + CuriosityNote 数据类
└── sqlite.py       # + CREATE TABLE curiosity_note
```

---

## 十二、实现顺序

| 阶段 | 内容 |
|------|------|
| 1. 数据层 | `CuriosityNote` 模型 + SQLite 建表 |
| 2. 配置层 | `config.toml` `[proactive]` + config.py 读取 |
| 3. 存储层 | `proactive/notes.py` CRUD |
| 4. 纯逻辑 | `proactive/decision.py` + `proactive/prompts.py` |
| 5. 引擎 | `proactive/engine.py` 主循环 + Reasoner 集成 |
| 6. 集成 | `app.py` 接线 |
| 7. 仪表盘 | curiosity notes 可视化 + 操作 |
