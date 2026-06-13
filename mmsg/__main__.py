"""统一 CLI 入口。

用法:
    mmsg serve              启动服务端（自动生成 config.toml，自动安装缺失依赖）
    mmsg cli                启动 TUI 客户端
    mmsg --print "问题"     单次批处理，直接输出结果
"""
from __future__ import annotations

import argparse
import asyncio
import sys


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="mmsg",
        description="MMSG Agent",
    )
    parser.add_argument(
        "--print", "-p",
        dest="query",
        metavar="QUERY",
        help="单次批处理模式：直接输出 agent 回答",
    )

    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="启动服务端")
    serve_parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    serve_parser.add_argument("--port", type=int, default=9090, help="监听端口（默认 9090）")

    subparsers.add_parser("cli", help="启动 TUI 客户端")

    args = parser.parse_args()

    if args.query:
        from .app import _batch
        try:
            asyncio.run(_batch(args.query))
        except KeyboardInterrupt:
            pass
    elif args.command == "serve":
        from .app import _serve
        try:
            asyncio.run(_serve(args.host, args.port))
        except KeyboardInterrupt:
            pass
    elif args.command == "cli":
        from .ui.cli import main as cli_main
        cli_main()
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
