"""读取 config.toml 配置（惰性加载）。"""
from __future__ import annotations

import tomllib
from pathlib import Path

CONFIG_PATH = Path("config.toml")
_cfg: dict | None = None

_DEFAULT_TEMPLATE = """\
# ============================================================
# MMSG 配置文件
# 请根据你的环境修改以下配置，然后重新运行 mmsg serve
# ============================================================

[workspace]
# 工作目录（数据存储位置）
path = "~/.MMSG/workspace"

[llm]
# API Key
api_key = "sk-your-api-key-here"
# API 地址
base_url = "https://api.deepseek.com/v1"
# 模型名称
model = "deepseek-v4-flash"

[memory]
# 记忆后端
backend = "default"

[qqbot]
# QQ 机器人配置（可选，留空则不启用）
app_id = ""
secret = ""
"""


def _load() -> dict:
    global _cfg
    if _cfg is not None:
        return _cfg
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"配置文件 {CONFIG_PATH} 不存在。请运行: mmsg serve"
        )
    with open(CONFIG_PATH, "rb") as f:
        _cfg = tomllib.load(f)
    return _cfg


def config_exists() -> bool:
    """检查 config.toml 是否存在。"""
    return CONFIG_PATH.exists()


def init_config(path: str | Path | None = None) -> Path:
    """生成默认 config.toml 模板文件。已存在则跳过。"""
    target = Path(path) if path else CONFIG_PATH
    if target.exists():
        return target
    target.write_text(_DEFAULT_TEMPLATE, encoding="utf-8")
    return target


def workspace_path() -> Path:
    return Path(_load().get("workspace", {}).get("path", "~/.MMSG/workspace")).expanduser()


def llm(key: str, default=None):
    return _load().get("llm", {}).get(key, default)


def memory_backend() -> str:
    return _load().get("memory", {}).get("backend", "default")


def qqbot(key: str, default=""):
    return _load().get("qqbot", {}).get(key, default)
