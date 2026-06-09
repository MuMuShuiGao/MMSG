"""传输层客户端：连接服务端，双向中继事件。

读方向：反序列化 JSON-lines → 注入本地 EventBus。
写方向：订阅本地 EventBus → 序列化 → 发送给服务端。
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("mmsg.transport")


async def connect_to_server(
    bus,
    host: str = "127.0.0.1",
    port: int = 9090,
) -> asyncio.Task:
    """连接传输服务，返回的后台任务负责双向中继。"""
    reader, writer = await asyncio.open_connection(host, port)
    log.info("已连接传输服务 %s:%d", host, port)

    async def relay_to_server(evt) -> None:
        """本地事件 → 序列化 → 发给服务端。仅转发 UI 产生的事件。"""
        if evt.source != "ui":
            return
        try:
            writer.write(evt.model_dump_json().encode() + b"\n")
            await writer.drain()
        except Exception:
            pass

    bus.subscribe("*", relay_to_server)

    async def run() -> None:
        """读取服务端推送的 JSON 行，反序列化后注入本地总线。"""
        while True:
            line = await reader.readline()
            if not line:
                log.warning("服务端断开连接")
                break
            data = line.decode().strip()
            if not data:
                continue
            # 以 "transport.raw" 事件注入，由上层反序列化处理
            await bus.publish("transport.raw", "transport", {"data": data})

    return asyncio.create_task(run())
