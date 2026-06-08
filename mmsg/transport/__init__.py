"""TCP 传输层：将 EventBus 事件通过 JSON-lines 跨进程传输。"""

from .client import connect_to_server
from .server import run_tcp_server

__all__ = ["connect_to_server", "run_tcp_server"]
