# 多 Agent 触发场景报告

> 全部引用基于本仓库源码，未编造。引用格式 `file_path:line`。

---

## 一、触发入口总览

OpenClaw 的多 Agent（subagent）能力由内置工具 `sessions_spawn` 提供。模型在对话回合中调用此工具即触发子 Agent。所有子 Agent 流量最终汇聚到 `spawnSubagentDirect()` (`src/agents/subagent-spawn.ts:1075`)。

工具注册位置：
- `createSessionsSpawnTool()` 定义于 `src/agents/tools/sessions-spawn-tool.ts:253`
- 在工具集合中的注入点 `src/agents/openclaw-tools.ts:507`

---

## 二、触发场景

### 场景 1：Agent 主回合中调用 `sessions_spawn`（最主要路径）

模型决定将一个子任务委托给子 Agent。工具执行流程：

```
sessions_spawn 调用 (src/agents/tools/sessions-spawn-tool.ts:282)
    └── runtime="subagent" → spawnSubagentDirect()  (sessions-spawn-tool.ts:472)
    └── runtime="acp"     → AcpSpawnModule (acp-spawn.ts)
```

参数 schema 中支持的字段（`src/agents/tools/sessions-spawn-tool.ts:162-241`）：
- `task` / `taskName` / `label`
- `runtime`：`"subagent" | "acp"`
- `agentId`：选用哪个已配置 agent
- `model` / `thinking`：覆盖默认模型与思考预算
- `cwd`：工作目录
- `mode`：`"run"`（一次性）或 `"session"`（长期）
- `sandbox`：`"inherit" | "require"`
- `context`：`"isolated" | "fork"`（子是否 fork 父对话历史）
- `lightContext`：仅 subagent runtime，使用轻量 bootstrap
- `attachments`：附件数组（最多 50 个，`maxItems: 50`）

---

### 场景 2：Thread-bound 子 Agent（`mode="session"` + `thread=true`）

仅当通道支持 thread-binding 且配置启用时可用。

判定逻辑 `src/agents/tools/sessions-spawn-tool.ts:128-160` 的 `resolveSessionsSpawnThreadAvailability`：

```ts
const channel = opts?.agentChannel;
if (!channel || !cfg || !supportsAutomaticThreadBindingSpawn(channel)) {
  return { subagent: false, acp: false };
}
const policy = resolveThreadBindingSpawnPolicy({ cfg, channel, accountId, kind });
return policy.enabled && policy.spawnEnabled;
```

强制约束 `src/agents/subagent-spawn.ts:1109-1116`：
```ts
if (spawnMode === "session" && !requestThreadBinding) {
  return {
    status: "error",
    error: 'sessions_spawn(mode="session") requires thread=true ...'
  };
}
```

session 模式自动设 `cleanup="keep"`（`subagent-spawn.ts:1117-1122`），子会话不删除，可继续接收消息。

---

### 场景 3：ACP runtime 子 Agent

需 `acpx` 插件加载。可用性判断 `src/agents/tools/sessions-spawn-tool.ts:268-271`：

```ts
const acpAvailable = isAcpRuntimeSpawnAvailable({
  config: opts?.config,
  sandboxed: opts?.sandboxed,
});
```

不可用时的明确错误（`sessions-spawn-tool.ts:243-251`）：
- 沙箱内 → "ACP sessions run on the host. Use runtime=\"subagent\""
- `acp.enabled=false` → "ACP is disabled by policy"
- 无 ACP 后端 → "Enable the acpx plugin or use runtime=\"subagent\""

ACP 实际 spawn 走 `src/agents/acp-spawn.ts`。

---

### 场景 4：Cron 定时任务触发

Cron session 是另一类发起者。识别由 `isCronSessionKey()` (`src/routing/session-key.ts:14`) 完成。

特殊处理 `src/agents/subagent-spawn-accepted-note.ts:13-22`：

```ts
export function resolveSubagentSpawnAcceptedNote(params: {
  spawnMode: "run" | "session";
  agentSessionKey?: string;
}): string | undefined {
  if (params.spawnMode === "session") {
    return SUBAGENT_SPAWN_SESSION_ACCEPTED_NOTE;
  }
  return isCronSessionKey(params.agentSessionKey) ? undefined : SUBAGENT_SPAWN_ACCEPTED_NOTE;
}
```

— Cron session spawn 时**抑制 polling 提示**，因 Cron 周期触发不应轮询。

---

### 场景 5：Embedded（无 Gateway）模式下的 spawn

工具集合编排时检查 `src/agents/openclaw-tools.ts:394`：

```ts
const includeSubagentSpawnTool = !embedded || options?.allowGatewaySubagentBinding === true;
```

— 嵌入模式下**默认不暴露 `sessions_spawn`**，除非显式 `allowGatewaySubagentBinding=true`。

`src/agents/embedded-agent-runner/run/attempt.ts:1437` 同时给模型注入提示："Gateway-dependent tools (... sessions_spawn ...) are unavailable"。

---

### 场景 6：插件 Hook 监听 spawn 事件（被动触发链）

子 Agent 注册成功后 `src/agents/subagent-spawn.ts:1707-1734`：

```ts
if (hookRunner?.hasHooks("subagent_spawned")) {
  await hookRunner.runSubagentSpawned({
    runId: childRunId, childSessionKey, agentId: targetAgentId,
    label, requester: { channel, accountId, to, threadId },
    threadRequested: requestThreadBinding, mode: spawnMode, ...
  }, { runId, childSessionKey, requesterSessionKey: requesterInternalKey });
}
```

子 Agent 完成时同样有 `subagent_ended` Hook（`src/agents/subagent-registry-completion.ts:78`）。插件可借此实现自定义级联 spawn。

---

## 三、限制与守卫（spawn 前的硬检查）

源码：`src/config/agent-limits.ts:1-31`

| 限制 | 默认值 | 含义 | 检查位置 |
|---|---|---|---|
| `DEFAULT_AGENT_MAX_CONCURRENT` | `4` | 顶层 Agent 并发 | `agent-limits.ts:5` |
| `DEFAULT_SUBAGENT_MAX_CONCURRENT` | `8` | 全局 subagent 并发 | `agent-limits.ts:7` |
| `DEFAULT_SUBAGENT_MAX_CHILDREN_PER_AGENT` | `5` | 单个 Agent 直接子数 | `agent-limits.ts:9` |
| `DEFAULT_SUBAGENT_MAX_SPAWN_DEPTH` | `1` | 最大嵌套深度 | `agent-limits.ts:13` |
| `DEFAULT_SUBAGENT_ARCHIVE_AFTER_MINUTES` | `60` | 完成态归档时间 | `agent-limits.ts:11` |

**Spawn 前两道硬闸**（`src/agents/subagent-spawn.ts:1161-1179`）：

```ts
const callerDepth = getSubagentDepthFromSessionStore(requesterInternalKey, { cfg });
const maxSpawnDepth = cfg.agents?.defaults?.subagents?.maxSpawnDepth ?? DEFAULT_SUBAGENT_MAX_SPAWN_DEPTH;
if (callerDepth >= maxSpawnDepth) {
  return { status: "forbidden",
    error: `sessions_spawn is not allowed at this depth (current depth: ${callerDepth}, max: ${maxSpawnDepth})` };
}

const maxChildren = cfg.agents?.defaults?.subagents?.maxChildrenPerAgent ?? DEFAULT_SUBAGENT_MAX_CHILDREN_PER_AGENT;
const activeChildren = countActiveRunsForSession(requesterInternalKey);
if (activeChildren >= maxChildren) {
  return { status: "forbidden",
    error: `sessions_spawn has reached max active children for this session (${activeChildren}/${maxChildren})` };
}
```

第三道：`agentId` 校验（`subagent-spawn.ts:1095-1100`）。无效 ID 直接拒绝，避免下游 `normalizeAgentId` 把错误字符串转成 ghost workspace（issue #31311）。

第四道：`requireAgentId` 强制（`subagent-spawn.ts:1184-1194`）。配置开启时必须显式指定 agentId。

---

## 四、Spawn 后的事件链

```
spawnSubagentDirect (subagent-spawn.ts:1075)
    │
    ├─ 1. 校验：agentId / depth / children / requireAgentId
    ├─ 2. 解析 model + thinking plan (subagent-spawn-plan.ts)
    ├─ 3. 解析 ownership / requesterOrigin (subagent-spawn-ownership.ts)
    ├─ 4. 物化附件 (subagent-attachments.ts)
    ├─ 5. 创建子 session (gateway: sessions.create)
    ├─ 6. fork 父上下文（若 context="fork"）
    ├─ 7. registerSubagentRun (subagent-registry.ts:1239)  → 写 SQLite
    ├─ 8. 触发 hook: subagent_spawned (subagent-spawn.ts:1707)
    ├─ 9. emitSessionLifecycleEvent (subagent-spawn.ts:1737)  → SSE 广播
    └─10. 返回 acceptedNote (subagent-spawn-accepted-note.ts)
```

完成后：
```
agent run 终止 → subagent-registry-completion.ts → 触发 subagent_ended
                                                  → steering queue 入队
                                                  → 主 Agent 唤醒，结果作 user message 注入
```

提示文本 `src/agents/subagent-spawn-accepted-note.ts:8`：

> "Auto-announce is push-based. After spawning children, do NOT call sessions_list, sessions_history, exec sleep, or any polling tool. Track expected child session keys ..."

— 系统设计为 push 模型，禁止主 Agent 轮询。

---

## 五、典型触发用例（基于源码契约还原）

| 用例 | 调用形态 | 内部路径 |
|---|---|---|
| 并行修三个 bug | 连续 3 次 `sessions_spawn({ task: "fix bug A/B/C", mode: "run" })` | `spawnSubagentDirect` × 3，受 `maxChildrenPerAgent=5` 限制 |
| 长期开新会话窗 | `sessions_spawn({ thread: true, mode: "session" })` | 创建 thread binding，`cleanup="keep"` |
| 切换专业 Agent | `sessions_spawn({ agentId: "research", task: "..." })` | 用目标 agent 的 model/skill/workspace |
| 切换模型 | `sessions_spawn({ model: "openai/gpt-5.5" })` | `resolveSubagentModelAndThinkingPlan` 写入子 session |
| 沙箱执行 | `sessions_spawn({ sandbox: "require" })` | 强制 sandbox，禁 cwd 覆盖 |
| ACP 协议子 | `sessions_spawn({ runtime: "acp" })` | 走 `acp-spawn.ts`，子运行在 host |
| Fork 父上下文 | `sessions_spawn({ context: "fork" })` | 子继承父 transcript |
| 跨 Agent 委托 | `sessions_spawn({ agentId: "other-agent" })` | 走该 Agent 的 toolset/policy |

---

## 六、不会触发多 Agent 的场景

1. **Embedded 模式默认禁用**（`openclaw-tools.ts:394`）
2. **深度已达上限**（默认 depth=1，子 Agent 无法再 spawn 孙子）
3. **active children 已满 5**（`subagent-spawn.ts:1173`）
4. **`mode="session"` 但 `thread=false`**（`subagent-spawn.ts:1109`）
5. **沙箱内请求 `runtime="acp"`**（`sessions-spawn-tool.ts:244-245`）
6. **`acp.enabled=false`** 时请求 ACP（`sessions-spawn-tool.ts:247-249`）
7. **policy 拒绝该工具**：`subagents.tools.allow/deny`（`agent-tools.policy.ts`）

---

## 七、关键源码索引

| 模块 | 路径 |
|---|---|
| 工具入口 | `src/agents/tools/sessions-spawn-tool.ts` |
| 核心 spawn 执行 | `src/agents/subagent-spawn.ts` |
| ACP spawn | `src/agents/acp-spawn.ts` |
| 类型定义 | `src/agents/subagent-spawn.types.ts` |
| 注册表 | `src/agents/subagent-registry.ts` |
| 注册表类型 | `src/agents/subagent-registry.types.ts` |
| 完成 hook | `src/agents/subagent-registry-completion.ts` |
| 结果回送队列 | `src/agents/agent-steering-queue.ts` |
| 限制常量 | `src/config/agent-limits.ts` |
| Cron session 识别 | `src/sessions/session-key-utils.ts:262` |
| 工具策略 | `src/agents/agent-tools.policy.ts` |
| Spawn 后提示 | `src/agents/subagent-spawn-accepted-note.ts` |
| Hook 触发 | `src/agents/subagent-spawn.ts:1707-1734` |

---

**总结**：触发多 Agent 的唯一直接入口是模型调用 `sessions_spawn` 工具。该工具受 6 道闸门（embedded、depth、children、agentId 合法性、requireAgentId、policy）和 4 个变体（runtime、mode、sandbox、context）控制。Cron 与 plugin hook 是间接触发场景：Cron 通过 session key 类型走特殊路径，hook 通过监听 `subagent_spawned`/`subagent_ended` 实现级联。

---

## 附：设计决策与被否定的方案

> 本节全部依据源码注释、代码结构、`VISION.md` 中明确陈述的内容，不推测。

---

### 决策 1：子 Agent 不允许直接 `sessions_send` 给任何人，只能通过 announce chain 回报

**源码依据** `src/agents/agent-tools.policy.ts:51-60`：

```ts
const SUBAGENT_TOOL_DENY_ALWAYS = [
  "gateway",
  "agents_list",
  // Status/scheduling - main agent coordinates
  "session_status",
  "cron",
  // Direct session sends - subagents communicate through announce chain
  "sessions_send",
];
```

注释明确说明：子 Agent 的结果通过 announce chain 回传，而非直接发消息。

**被否定的方案**：子 Agent 调用 `sessions_send` 直接向父 Agent 发消息。

**为什么否定**：两个问题——（1）子 Agent 不知道父 Agent 的会话 key，不应暴露；（2）子 Agent 完成时父 Agent 可能不在等待转，直接 send 会绕过父 Agent 的对话循环，造成消息插入冲突。`subagent-announce-dispatch.test.ts:117-118` 的注释也直接说了：

> "steering would risk duplicate or contradictory completion messages"

---

### 决策 2：Push 模型，父 Agent 不得轮询子 Agent

**源码依据** `src/agents/subagent-spawn-accepted-note.ts:8`：

```
SUBAGENT_SPAWN_ACCEPTED_NOTE =
  "Auto-announce is push-based. After spawning children, do NOT call sessions_list,
   sessions_history, exec sleep, or any polling tool. ..."
```

这段文字**直接注入给主 Agent 的上下文**，系统级别禁止轮询。

**被否定的方案**：父 Agent spawn 后调用 `sessions_list` 或 `sleep` 循环等子 Agent 完成。

**为什么否定**：每次轮询会消耗父 Agent 一个 turn，浪费 token；并且父 Agent 持有 turn 期间其他通道消息无法被处理。Push 模型下子 Agent 完成即通过 steering queue 注入一条 user message 唤醒父 Agent，无需任何主动等待。

---

### 决策 3：交付路径：steer（主路）+ direct（备路）双阶段

**源码依据** `src/agents/subagent-announce-dispatch.ts:62-132`：

```ts
// 非 completion-message 模式：先 steer，失败再 direct
if (!params.expectsCompletionMessage) {
  const primarySteerOutcome = await params.steer();
  ...
  const primaryDirect = await params.direct();
}

// completion-message 模式：先 direct，失败再 steer
// Completion handoff prefers direct delivery first so the completion agent's
// final visible message wins before falling back to steering.
const primaryDirect = await params.direct();
```

**被否定的方案**：只走 direct 或只走 steer 单一路径。

**为什么否定**：

- `expectsCompletionMessage=false`（普通子任务）：优先 steer 将结果注入父 Agent 上下文，父 Agent 可继续它的对话；direct 仅作失败降级，避免结果丢失。
- `expectsCompletionMessage=true`（子 Agent 负责最终回复用户）：优先 direct 让子 Agent 直接向用户发消息，父 Agent 此时已让出主权；再加 steer fallback 防止 direct 失败时结果完全丢失。`subagent-announce-dispatch.ts:112-113` 注释明确说：`// direct 优先，保证 completion agent 的最终可见消息优先生效`。

---

### 决策 4：默认 `maxSpawnDepth=1`，子 Agent 不能再 spawn 孙子 Agent

**源码依据** `src/config/agent-limits.ts:12-13`：

```ts
// Keep depth-1 subagents as leaves unless config explicitly opts into nesting.
export const DEFAULT_SUBAGENT_MAX_SPAWN_DEPTH = 1;
```

注释直接说明：leaf 是默认行为，嵌套是 opt-in。

**被否定的方案（VISION.md 明确拒绝）** `VISION.md:122-123`：

> "Agent-hierarchy frameworks (manager-of-managers / nested planner trees) as a default architecture"
> "Heavy orchestration layers that duplicate existing agent and tool infrastructure"

**为什么否定**：
1. 嵌套 planner 树会快速失控，debug 难度指数增长，每层错误向上传播。
2. 多数任务不需要多层嵌套，单层已够用；多层嵌套本质是把任务分解问题转嫁给运行时而非模型。
3. 资源消耗不可预测：三层 × 5 children = 最多 25 个并发 Agent。

OpenClaw 的立场：需要多层时通过 config 显式 opt-in（`agents.defaults.subagents.maxSpawnDepth`），而非默认暴露。

---

### 决策 5：ACP 与 subagent 是两种不同 runtime，互不替代

**源码依据** `src/agents/acp-binding-architecture.guardrail.test.ts:1`：

```
/** Guardrail tests that keep ACP/session binding flows off legacy thread-binding APIs. */
```

以及 `acp-binding-architecture.guardrail.test.ts:14-30` 通过自动化测试强制隔离：`acp-spawn.ts` 禁止调用 `getThreadBindingManager`，ACP session lifecycle 禁止调用 `unbindThreadBindingsBySessionKey`。

**被否定的方案**：ACP spawn 复用 subagent 的 thread-binding 管理器。

**为什么否定**：ACP 协议（OpenAI Codex）的会话绑定模型与 OpenClaw 原生 thread-binding 不同，混用会导致会话生命周期管理出错。守卫测试通过 import 扫描确保编译期就阻止错误使用。

---

### 决策 6：subagent 只能用已注册的 agentId，不能是任意字符串

**源码依据** `src/agents/subagent-spawn.ts:1092-1100`（注释带 issue 编号）：

```ts
// Reject malformed agentId before normalizeAgentId can mangle it.
// Without this gate, error-message strings like "Agent not found: xyz" pass
// through normalizeAgentId and become "agent-not-found--xyz", which later
// creates ghost workspace directories and triggers cascading cron loops (#31311).
if (requestedAgentId && !isValidAgentId(requestedAgentId)) {
  return { status: "error", error: `Invalid agentId: ...` };
}
```

**被否定的方案**：直接 `normalizeAgentId(requestedAgentId)` 容错处理任意字符串。

**为什么否定**：真实 bug（#31311）— 模型将错误消息文字传入 agentId，系统把 `"Agent not found: xyz"` 规范化为合法路径名，然后在 workspace 下创建以该字符串命名的目录，触发 cron 去监控这个目录，导致级联循环。

---

### 设计倾向汇总

| 被否定的方案 | 否定理由 | 源码位置 |
|---|---|---|
| 子 Agent 直接 `sessions_send` 回父 | 绕过对话循环，消息冲突 | `agent-tools.policy.ts:58` |
| 父 Agent 轮询等待子结果 | 消耗 turn、阻塞通道 | `subagent-spawn-accepted-note.ts:8` |
| 单一交付路径 | 失败无降级，结果丢失 | `subagent-announce-dispatch.ts:112-113` |
| 默认允许多层嵌套 | manager-of-managers 失控 | `VISION.md:122` + `agent-limits.ts:12` |
| ACP 复用 thread-binding 管理器 | 生命周期模型不同 | `acp-binding-architecture.guardrail.test.ts` |
| 容错 agentId（任意字符串转 ID） | 实际导致 ghost workspace + cron 循环 | `subagent-spawn.ts:1092-1100` (issue #31311) |
