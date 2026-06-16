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

[core]
# 日志等级: DEBUG / INFO / WARNING / ERROR
log_level = "INFO"

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

[feishu]
# 飞书机器人配置（可选，留空则不启用）
app_id = ""
app_secret = ""

[proactive]
# 主动聊天配置（可选）
channel = ""
# 主动强度: strong(2h) / medium(4h) / weak(24h)
intensity = "medium"
# 静默免责时段（此时间段内不主动说话）
quiet_start = "00:00"
quiet_end = "07:00"
# 整理间隔（秒），默认 900（15 分钟），调试时可改为 30
consolidate_interval = 900

[embedding]
# Embedding API 配置
api_key = "sk-your-dashscope-key"
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
model = "text-embedding-v3"

[consolidator]
# 事实提取 consolidator 配置
min_new_msg = 10
min_hours = 2
poll_interval = 120

[merger]
# 事实合并 worker 配置
min_days = 3
poll_interval = 3600
similarity_threshold = 0.97
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


def feishu(key: str, default=""):
    return _load().get("feishu", {}).get(key, default)


def log_level(default: str = "INFO") -> str:
    return _load().get("core", {}).get("log_level", default)


def proactive(key: str, default=""):
    return _load().get("proactive", {}).get(key, default)


def embedding(key: str, default=None):
    return _load().get("embedding", {}).get(key, default)


def consolidator(key: str, default=None):
    return _load().get("consolidator", {}).get(key, default)


def merger(key: str, default=None):
    return _load().get("merger", {}).get(key, default)
