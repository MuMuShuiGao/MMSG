"""读取 config.toml 配置。"""
from __future__ import annotations

import tomllib
from pathlib import Path

CONFIG_PATH = Path("config.toml")

with open(CONFIG_PATH, "rb") as f:
    _cfg = tomllib.load(f)


def workspace_path() -> Path:
    return Path(_cfg.get("workspace", {}).get("path", "~/.MMSG/workspace")).expanduser()


def llm(key: str, default=None):
    return _cfg.get("llm", {}).get(key, default)


def memory_backend() -> str:
    return _cfg.get("memory", {}).get("backend", "builtin")


def qqbot(key: str, default=""):
    return _cfg.get("qqbot", {}).get(key, default)
