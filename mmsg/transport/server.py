"""传输层服务端：监听 TCP 端口，将 EventBus 事件以 JSON-lines 流推送给客户端，同时接收客户端发来的事件注入本地总线。"""

from __future__ import annotations

import asyncio
import logging

from ..core.bus import Event

log = logging.getLogger("mmsg.transport")


async def run_tcp_server(
    bus,
    host: str = "127.0.0.1",
    port: int = 9090,
) -> None:
    """启动传输服务，阻塞直到被取消。"""

    async def handle_client(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        addr = writer.get_extra_info("peername")
        log.info("客户端连接 %s", addr)

        async def send(evt) -> None:
            """把本地事件序列化为 JSON 行推给客户端。"""
            # 不回传客户端发来的事件，防止回音循环
            if evt.source in ("ui", "transport"):
                return
            try:
                writer.write(evt.model_dump_json().encode() + b"\n")
                await writer.drain()
            except Exception:
                pass

        unsub = bus.subscribe("*", send)
        try:
            while True:
                line = await reader.readline()
                if not line:  # EOF，客户端断开
                    break
                data = line.decode().strip()
                if not data:
                    continue
                try:
                    evt = Event.from_json(data)
                except Exception:
                    log.debug("无法解析客户端数据: %r", data)
                    continue
                await bus.publish(evt.type, evt.source, evt.payload)
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        finally:
            unsub()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            log.info("客户端断开 %s", addr)

    server = await asyncio.start_server(handle_client, host, port)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    log.info("传输服务启动 %s", addrs)
    await server.serve_forever()
