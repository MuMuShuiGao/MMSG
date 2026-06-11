"""传输层服务端：监听 TCP 端口，可观测事件推给客户端，客户端消息注入 message_bus 队列。"""

from __future__ import annotations

import asyncio
import logging

from ..bus.eventbus import Event
from ..bus.messagebus import MessageBus, MESSAGE_INBOUND

log = logging.getLogger("mmsg.transport")


async def run_tcp_server(
    message_bus: MessageBus,
    host: str = "127.0.0.1",
    port: int = 9090,
) -> None:

    async def handle_client(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        addr = writer.get_extra_info("peername")
        log.info("客户端连接 %s", addr)

        async def relay_to_client(evt: Event) -> None:
            if evt.source in ("ui", "transport"):
                return
            try:
                writer.write(evt.model_dump_json().encode() + b"\n")
                await writer.drain()
            except Exception:
                pass

        unsub = message_bus.events.subscribe("*", relay_to_client)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                data = line.decode().strip()
                if not data:
                    continue
                try:
                    evt = Event.from_json(data)
                except Exception:
                    log.debug("无法解析客户端数据: %r", data)
                    continue

                if evt.type == MESSAGE_INBOUND:
                    await message_bus.publish_inbound(evt.source, evt.payload)
                else:
                    await message_bus.events.observe(evt.type, evt.source, evt.payload)
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
