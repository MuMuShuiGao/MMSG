# MCP 接入设计共识

> 通过「grill-me」逐分支拍板的设计稿。把外部 MCP server 暴露的工具接入到现有 `tool_registry`，让 LLM 像调内置工具一样调它们。

## 起点问题

现状：`mmsg/tools/` 下工具全是手写硬编码（`ReadFileTool` / `HttpGetTool` 等），在 `mmsg/app.py::_register_plugins()` 静态注册。要扩工具能力，唯一路径是写 Python 类。

痛点：
1. 错过 MCP 生态 —— 官方 + 社区已经有 filesystem / git / github / sqlite / playwright 等几十款 server，重写不划算
2. 工具迭代要发版 —— 用户加个 server 就得改代码
3. 无标准跨 host —— 同一份工具配置在 Claude Desktop / Cursor / Cline 都能用，唯独 mmsg 不行

→ 引入 MCP client：以 `Tool` 适配器把外部 MCP server 暴露的工具透明注册到 `tool_registry`。

## 产品定位（前置约束）

目标：**让 mmsg 复用 MCP 生态的 tool 资产，零代码扩展工具能力**。

明确不做（一期）：
- ❌ MCP server 反向暴露（把 mmsg 工具/记忆暴露给外部 host）
- ❌ resources（server 暴露的可读数据）
- ❌ prompts（server 提供的 slash 命令模板）
- ❌ tools/list_changed 热更新
- ❌ image / EmbeddedResource 多模态透传
- ❌ OAuth flow（HTTP server 仅静态 header 认证）

→ 一期专注：**Client only，tools only**。

## 总览：MCP 工具与内置工具并存，调用路径统一

| 维度 | 内置工具（`ReadFileTool` 等） | MCP 工具（`MCPTool` 实例） |
|---|---|---|
| 注册方式 | `tool_registry.register(name)` 装饰器 | `MCPManager` 启动期 `register_instance` |
| 名称 | 自由（如 `read_file`） | 强制 `mcp__<server>__<tool>` 前缀 |
| schema | 类属性 `parameters` 写死 | 来自 MCP `tools/list` 的 `inputSchema` |
| risk | 类属性 | 实例属性，来自 server 配置 |
| 执行 | 类内 `run()` 直跑 | `client.call_tool()` 跨进程 |
| 生命周期 | 进程级 | 进程级 + 重连 |

→ Reasoner 看到的还是 `dict[str, Tool]`，对 LLM 透明，调用路径不分叉。

## 架构决策（按依赖顺序拍板）

### D1. 接入定位：Client only

agent 当 MCP 客户端消费外部 server。Server 反向暴露二期再说。

**理由**：
- `Tool` ABC + `tool_registry` 天然就是 tool 抽象层，加适配器子类成本最低
- 扩展面（外部 MCP 生态）远大于反向暴露的收益
- Server 模式涉及 memory / proactive 协议化，工程量大

### D2. Transport：stdio + Streamable HTTP

不做 WebSocket。stdio 覆盖官方生态 90%+，HTTP 覆盖远程 server，WebSocket 极少见。

### D3. SDK：官方 `mcp` Python SDK

加 `mcp = ["mcp>=1.0"]` 到 `pyproject.toml` 的 optional-deps，对齐现有 `qqbot` / `feishu` / `dashboard` 风格。

**理由**：
- 不手写 JSON-RPC 协议；spec 在演进（progress / elicitation / structured output），手写会债
- `lark-oapi` / `fastapi` 等先例表明项目偏好成熟上游

### D4. 适配粒度：一 MCP tool = 一 Tool 实例

每个 MCP tool 注册成独立 `MCPTool` 实例，`schema` 直接是 `inputSchema`，对 LLM 完全透明。

**淘汰方案**：「一 server = 一 Tool，参数带 tool_name」需要 LLM 两步思考，回归质量更差。

### D5. 命名冲突：强制前缀 `mcp__<server>__<tool>`

不论是否冲突，统一加前缀。

**理由**：
- `tool_registry.register` 同名直接 raise（`mmsg/core/plugin.py:21`），冲突必须处理
- 前缀可预测、不依赖加载顺序
- Claude Desktop 等成熟 host 也是这么做

### D6. 连接生命周期：启动连 + 心跳重连

启动期并发连所有 server，长驻；失败 server 走指数退避后台重连，成功后补注册。

**理由**：
- 现有 channel / agent loop 都是启动期连+长驻模式，一致
- stdio server crash 不重连 → 那个工具永久废，体验崩
- 懒连接首次调用延迟（npx 冷启 100ms~几秒）伤害手感

### D7. 配置格式：dotted-section dict（生态兼容）

```toml
[mcp.servers.filesystem]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/path"]
env = { FOO = "bar" }
risk = "write"            # 默认 network；可 safe/write/network
timeout = 30              # 可选，秒
enabled = true            # 可选，默认 true

[mcp.servers.github]
transport = "http"        # 默认 stdio
url = "https://example/mcp"
headers = { Authorization = "Bearer xxx" }
risk = "network"
```

**理由**：MCP 生态文档全是 JSON `mcpServers` 字典格式（Claude Desktop / Cursor / Cline 都这格式），TOML dotted-section 1:1 对应，复制即用。

### D8. 权限：server 配 risk + annotation 升级

每个 server 在配置里声明默认 `risk`。若某 tool 自带 `destructiveHint=true`，强制升到 `write`，避免用户误声明。

**理由**：MCP server 良莠不齐，annotation hint 不可信；配置 risk 才稳。但用户可能配错，annotation 作为安全网。

### D9. tool list 变更：静态 + 错误复用

启动期 `tools/list` 一次，运行时忽略 `notifications/tools/list_changed`。`MCPTool.run` 抛异常即可，复用 `Reasoner` 现有的 `try/except` 转错误字符串路径（`mmsg/agent/reason/engine.py:193-197`）。

**淘汰方案**：「热加载 + 动态改 registry」复杂、收益小，二期再说。

### D10. 接入 capability：tools only

resources / prompts 二期。理由：tools 对齐 `Tool` 抽象，resources / prompts 涉及 memory 层和 UI 层语义重设计。

### D11. 模块落位：`mmsg/tools/mcp/`

子包形态，复用现有 import 路径。日后扩 resources / prompts 再升级到 `mmsg/mcp/` 顶级。

### D12. server 启动失败：宽容跳过

任一 server 连不上 → 告警 + 后台重连，其他 server + 内置工具正常工作。

**理由**：MCP server 普遍脆弱（npm 抽风、网络抖动），让外部依赖把 agent 拖死违反「减少阻塞面」原则。和 `_start_channels` 隐含语义一致（QQBot 没配就不启）。

### D13. 调用结果：只取 TextContent

`CallToolResult.content` 里 `TextContent` 拼接为 `str`；image / resource 转占位文字 `[image: <mimeType>]` 或丢弃。

**理由**：当前 `OpenAIProvider` 单一文本路径，多模态是独立工程问题，不该跟 MCP 接入耦合。

### D14. 可观测性：复用 ToolCall 事件

不新增 `BeforeMCPCall` 事件类型。`MCPTool.run` 走 `tool.run()` 路径，`AgentEvent.BeforeToolCall` / `AfterToolCall` 自动覆盖；按 `mcp__` 名称前缀过滤即可分析。

### D15. 默认值

- 调用超时：**30s**，per-server `timeout` 可覆盖
- stdio 子进程 `cwd`：`workspace_path()`（filesystem server 等能直接对工作区操作）
- env：继承父进程 + 配置 env 覆盖（PATH 等必须继承否则找不到 npx）

## 模块结构

```
mmsg/tools/mcp/
├── __init__.py        # 暴露 MCPManager
├── manager.py         # MCPManager: 多 server 编排、注册、生命周期
├── client.py          # 单 server 连接封装：ClientSession + 重连
└── adapter.py         # MCPTool(Tool): 适配器
```

### `MCPManager`

职责：
- 读 `[mcp.servers.*]` 配置
- 并发启动所有 server，失败者跳过 + 告警，成功者把每个 MCP tool 包成 `MCPTool` 注册到 `tool_registry`
- 后台任务：失败 server 指数退避重连
- `aclose()`：关闭所有 `ClientSession`、停子进程

### `MCPClient`

职责：
- 维护单个 `ClientSession`（stdio 或 HTTP）
- `list_tools()` / `call_tool(name, args, timeout)`
- 心跳检测，连接断 → 抛异常通知 manager 触发重连

### `MCPTool(Tool)`

```python
class MCPTool(Tool):
    def __init__(self, *, server: str, mcp_tool, client, risk: str, timeout: float):
        self.name = f"mcp__{server}__{mcp_tool.name}"
        self.description = mcp_tool.description or ""
        self.parameters = mcp_tool.inputSchema or {"type": "object", "properties": {}}
        self.risk = self._effective_risk(risk, mcp_tool.annotations)
        self._client = client
        self._timeout = timeout

    async def run(self, **kwargs):
        result = await self._client.call_tool(
            self._raw_name, kwargs, timeout=self._timeout,
        )
        return self._extract_text(result)
```

## 启动流程改动

`mmsg/app.py::_serve()` 在 `_register_plugins()` 之后、`PermissionGate` 挂载之前：

```python
mcp_manager = MCPManager(tool_registry, workspace=workspace_path())
await mcp_manager.start()   # 并发连所有 server，失败跳过+告警，成功注册 MCPTool
```

shutdown 路径：`mcp_manager.aclose()`（也可由 `tool_registry.aclose()` 间接关，但显式更清楚）。

## 必要的小改动（已发现）

### `mmsg/tools/permission.py:33`

```python
risk: str = getattr(type(tool), "risk", "safe")
```

→ 改成读实例优先：

```python
risk: str = getattr(tool, "risk", getattr(type(tool), "risk", "safe"))
```

理由：MCPTool `risk` 来自 server 配置，是实例属性而非类属性。

### `mmsg/core/plugin.py::ToolRegistry`

现有 `register(name)` 是装饰器路径，要求装饰类。MCPTool 是动态实例，需新增：

```python
def register_instance(self, name: str, instance: Any) -> None:
    if name in self._instances:
        raise ValueError(f"tool '{name}' already registered")
    self._instances[name] = instance
    self._items[name] = type(instance)
```

## 不做的事（边界明确）

- 不做 MCP server 反向暴露
- 不做 resources / prompts
- 不做 tools/list_changed 热更新
- 不做 image / resource 多模态透传
- 不做 OAuth flow

## 实施顺序

1. 加依赖（`pyproject.toml` optional-deps `mcp`）+ 骨架（manager / client / adapter 空壳）
2. `PermissionGate` 改实例 risk
3. `ToolRegistry.register_instance`
4. stdio 连一个 `@modelcontextprotocol/server-filesystem` 跑通
5. HTTP transport
6. 失败容忍 + 指数退避重连
7. 文档（README 加配置示例）

## 风险与开放问题

- **stdio 子进程僵尸**：Windows 下 `subprocess` 终止不彻底是老问题，`aclose()` 要 `terminate()` + 超时 `kill()` 兜底
- **`npx` 冷启动**：首次连接可能 10s+，启动期 timeout 要给足（建议 connect timeout 60s，与 call timeout 30s 区分）
- **Server 鉴权**：HTTP server 配置里写 token 是明文，二期可加 env var 占位 `${GITHUB_TOKEN}` 解析
- **多实例并发调用**：单 `ClientSession` 是否线程/协程安全取决于 SDK 实现，要测；不安全则需要 per-server lock 或连接池
