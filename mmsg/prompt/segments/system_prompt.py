"""SystemPromptBuilder：聚合所有 segment builder，render() 输出最终 system prompt。

用法示例::

    from pathlib import Path
    from mmsg.prompt.segments import SystemPromptBuilder

    builder = SystemPromptBuilder(workspace=Path("~/my-agent"))
    prompt_text = builder.render()

    # 或者只用部分 segment：
    builder = SystemPromptBuilder(
        workspace=None,          # 不注入工作区信息
        include_tool_usage=False,
    )
"""

from __future__ import annotations

from pathlib import Path

from .generators import build_behavior, build_identity, build_tool_usage, build_workspace


class SystemPromptBuilder:
    """声明哪些 segment 要注入、动态段的参数是什么，render() 时一次性组装。"""

    def __init__(
        self,
        *,
        workspace: Path | None = None,
        include_behavior: bool = True,
        include_tool_usage: bool = True,
    ) -> None:
        self.workspace = workspace
        self.include_behavior = include_behavior
        self.include_tool_usage = include_tool_usage

    def render(self) -> str:
        """按 静态前缀 → 动态尾部 顺序拼接，最大化 KV cache 命中率。

        顺序：identity → behavior → tool_usage → workspace
        前三段全静态、跨请求不变，构成可被 prefix cache 命中的稳定前缀；
        workspace 含路径等动态值放最后，变动只影响尾部。
        """
        parts: list[str] = [build_identity()]

        if self.include_behavior:
            parts.append(build_behavior())

        if self.include_tool_usage:
            parts.append(build_tool_usage())

        if self.workspace is not None:
            parts.append(build_workspace(self.workspace))

        return "\n\n".join(p.strip() for p in parts if p.strip())
