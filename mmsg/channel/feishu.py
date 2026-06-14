"""飞书 Bot Channel：WS 收消息 → publish_inbound → REST 发消息 ← subscribe_outbound

对标 QQBotChannel 的结构。使用 lark-oapi SDK 管理 token、WS 连接和 REST 调用。

注意：lark_oapi.ws 模块在 import 时会抓取 asyncio.get_event_loop() 作为模块级
变量，如果在主协程内 import 会拿到 running loop，后续 run_until_complete 会失败。
因此所有 WS 相关的 import 和 start 都放在 executor 线程内执行，确保 SDK 在
干净线程里创建自己的 event loop。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from ..bus.messagebus import MessageBus, OutboundItem

log = logging.getLogger("mmsg.channel.feishu")


class _LarkLogFilter(logging.Filter):
    """屏蔽 Lark SDK 在 WebSocket 正常关闭时输出的 ERROR 日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        # suppress: "receive message loop exit, err: sent 1000 (OK); then received 1000 (OK) bye"
        if record.levelno >= logging.ERROR and record.name == "Lark":
            msg = record.getMessage()
            if "received 1000 (OK)" in msg:
                return False
        return True


_lark_filter = _LarkLogFilter()
logging.getLogger("Lark").addFilter(_lark_filter)

# REST 相关类型（主线程 import 安全，不含 ws 模块）
_lark_Client: Any = None
_CreateMessageRequest: Any = None
_CreateMessageRequestBody: Any = None
_UpdateMessageRequest: Any = None
_UpdateMessageRequestBody: Any = None

# 流式编辑限制
_MAX_EDITS_PER_MESSAGE = 20


def _import_lark_rest():
    """惰性导入 lark-oapi REST 类型（主线程安全）。"""
    global _lark_Client, \
        _CreateMessageRequest, _CreateMessageRequestBody, \
        _UpdateMessageRequest, _UpdateMessageRequestBody
    if _lark_Client is not None:
        return
    from lark_oapi import Client as _lark_Client
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest as _CreateMessageRequest,
        CreateMessageRequestBody as _CreateMessageRequestBody,
        UpdateMessageRequest as _UpdateMessageRequest,
        UpdateMessageRequestBody as _UpdateMessageRequestBody,
    )


class FeishuChannel:
    """飞书 Bot WebSocket 通道。

    - 通过 WS 长连接接收用户消息
    - 通过 REST API 发送回复
    - source 命名：feishu:{open_id}
    """

    def __init__(self, app_id: str, app_secret: str, bus: MessageBus):
        self._app_id = app_id
        self._app_secret = app_secret
        self._bus = bus
        self._rest_client: Any = None        # lark_oapi.Client
        self._ws_future: asyncio.Future | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutdown = threading.Event()   # 通知 executor 线程退出
        self._ws_client_ref: Any = None      # lark_oapi WsClient 引用，用于 stop 时控制
        self._ws_loop_ref: Any = None        # WS 线程内的事件循环
        # 流式输出状态：chat_id → {message_id, edit_count}
        self._streams: dict[str, dict[str, Any]] = {}

    # ── Lifecycle ────────────────────────────────────

    async def start(self) -> None:
        _import_lark_rest()

        self._loop = asyncio.get_running_loop()
        self._bus.subscribe_outbound("feishu:*", self._on_outbound)

        # REST client — 用于发送消息（内部自动管理 tenant_access_token）
        self._rest_client = _lark_Client.builder() \
            .app_id(self._app_id) \
            .app_secret(self._app_secret) \
            .build()

        # WS 启动全部放在 executor 线程内：
        #   - import lark_oapi.ws（避免模块级 loop 捕获主事件循环）
        #   - 构建 EventDispatcherHandler + WsClient
        #   - 调用阻塞的 ws_client.start()
        self._ws_future = asyncio.get_running_loop().run_in_executor(
            None, self._run_ws,
        )
        log.info("Feishu channel started (app_id=%s)", self._app_id)

    async def stop(self) -> None:
        # 1. 发信号：禁止 SDK 继续重连
        self._shutdown.set()
        if self._ws_client_ref is not None:
            try:
                self._ws_client_ref._auto_reconnect = False
            except Exception:
                pass

        # 2. 先关闭 WebSocket 连接，让 _receive_message_loop 的 recv() 收到
        #    ConnectionClosed 异常，走正常 _disconnect() 清理流程。否则直接
        #    loop.stop() 会中止正在 await recv() 的 task，后续 finally 清理
        #    尝试 call_soon 到已关闭的循环 → RuntimeError: Event loop is closed。
        if self._ws_client_ref is not None and self._ws_loop_ref is not None:
            try:
                conn = getattr(self._ws_client_ref, '_conn', None)
                if conn is not None:
                    fut = asyncio.run_coroutine_threadsafe(
                        conn.close(), self._ws_loop_ref,
                    )
                    fut.result(timeout=3)
            except Exception:
                pass

        # 3. 等待 _receive_message_loop 完成清理（_disconnect + 放弃重连）
        await asyncio.sleep(0.5)

        # 4. 取消 WS 线程事件循环内所有待处理任务，避免 "Task was destroyed
        #    but it is pending!" 警告（_ping_loop / _start_clear_cron 等）
        if self._ws_loop_ref is not None:
            try:
                def _cancel_all():
                    for t in asyncio.all_tasks(self._ws_loop_ref):
                        t.cancel()

                self._ws_loop_ref.call_soon_threadsafe(_cancel_all)
                # 给 task 一点时间响应用 cancel
                await asyncio.sleep(0.2)
            except Exception:
                pass

        # 5. 停止 WS 线程内的事件循环，让 ws.start() 返回
        if self._ws_loop_ref is not None:
            try:
                self._ws_loop_ref.call_soon_threadsafe(self._ws_loop_ref.stop)
            except Exception:
                pass

        # 6. 等待 executor 线程结束（不要 cancel，让线程自然退出）
        if self._ws_future is not None:
            try:
                await asyncio.wait_for(self._ws_future, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._ws_future = None

        log.info("Feishu channel stopped")

    # ── WS 线程入口 ──────────────────────────────────

    def _run_ws(self) -> None:
        """在 executor 线程内执行：import WS 模块 + 构建 + start。

        关键：lark_oapi.ws 在主线程 import REST 客户端时已被连带导入，模块级
        loop 捕获了主线程事件循环。这里先从 sys.modules 中移除 ws 相关模块，
        让它们在当前线程内重新执行模块代码，从而拿到一个干净的 event loop。
        """
        import sys

        # 还没开始就被叫停了
        if self._shutdown.is_set():
            return

        # 移除已缓存的 ws 模块，迫使在当前线程内重新初始化
        for key in list(sys.modules.keys()):
            if key.startswith("lark_oapi.ws"):
                del sys.modules[key]

        from lark_oapi.core.enum import LogLevel
        from lark_oapi.ws import Client as WsClient
        from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
        import lark_oapi.ws.client as _ws_client_module

        # 捕获 WS 线程自己的事件循环，供 stop() 跨线程关闭
        self._ws_loop_ref = _ws_client_module.loop

        # 静默 WS 正常关闭时的 ConnectionClosedOK task 异常
        from websockets.exceptions import ConnectionClosedOK as _CC_OK

        def _quiet_handler(loop, ctx):
            exc = ctx.get("exception")
            if isinstance(exc, _CC_OK):
                return
            loop.default_exception_handler(ctx)

        self._ws_loop_ref.set_exception_handler(_quiet_handler)

        shutdown_flag = self._shutdown

        def _on_reconnecting() -> None:
            if shutdown_flag.is_set():
                # 关闭中，不再重连，直接停掉事件循环
                try:
                    _ws_client_module.loop.call_soon_threadsafe(
                        _ws_client_module.loop.stop
                    )
                except Exception:
                    pass

        handler = EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(self._on_message_receive) \
            .build()

        ws = WsClient(
            app_id=self._app_id,
            app_secret=self._app_secret,
            event_handler=handler,
            log_level=LogLevel.ERROR,
        )
        ws.on_reconnecting = _on_reconnecting
        self._ws_client_ref = ws

        # start() 是阻塞的，在此线程内运行直到进程退出或 stop() 关闭事件循环
        ws.start()

    # ── Event callback（WS 线程内调用）───────────────

    def _on_message_receive(self, event: Any) -> None:
        """收到飞书 im.message.receive_v1 事件。

        注意：此方法在 WS client 的内部线程中被回调，不能直接 await。
        需要通过 run_coroutine_threadsafe 桥接到主事件循环。
        """
        try:
            msg = event.event.message
            sender = event.event.sender

            # 只处理文本消息
            if msg.message_type != "text":
                return

            # 提取消息内容（content 是 JSON 字符串，形如 {"text":"..."}）
            content_str = (msg.content or "").strip()
            if not content_str:
                return
            try:
                content_obj = json.loads(content_str)
                text = content_obj.get("text", "")
            except json.JSONDecodeError:
                text = content_str

            if not text:
                return

            # 提取用户标识
            sender_id = sender.sender_id
            open_id = getattr(sender_id, "open_id", None) or ""
            if not open_id:
                return

            chat_id = msg.chat_id or ""

            log.info("Feishu msg from %s (chat=%s): %s", open_id, chat_id, text[:60])

            source = f"feishu:{open_id}"
            payload = {
                "text": text,
                "open_id": open_id,
                "chat_id": chat_id,
                "message_id": msg.message_id or "",
                "chat_type": msg.chat_type or "",
            }

            # 跨线程安全地发布到 MessageBus
            asyncio.run_coroutine_threadsafe(
                self._bus.publish_inbound(source, payload),
                self._loop,
            )

        except Exception:
            log.exception("处理飞书消息事件异常")

    # ── Outbound handler（主事件循环内调用）──────────

    async def _on_outbound(self, item: OutboundItem) -> None:
        """收到 Agent 回复 → 调用飞书 REST API 流式发送/编辑消息。

        流式策略：
        - done=False 首条 → POST create，存入 message_id
        - done=False 后续 → PUT update 编辑已发送消息（上限 20 次）
        - done=True → PUT update 最终内容（若无流式记录则直接 create）
        """
        open_id = item.payload.get("open_id")
        chat_id = item.payload.get("chat_id", "")
        text = item.payload.get("text", "")
        done = item.payload.get("done", True)

        if not open_id or not text:
            return
        if not item.source.startswith("feishu:"):
            return

        source = item.source
        stream = self._streams.get(source)

        if not done:
            # ── 中间 chunk：流式刷新 ──
            if stream is None:
                # 首次：创建消息，记录 message_id
                msg_id = await self._create_text(chat_id, text)
                if msg_id:
                    self._streams[source] = {"message_id": msg_id, "edit_count": 0}
            elif stream["edit_count"] < _MAX_EDITS_PER_MESSAGE:
                # 编辑已发送消息，递进刷新
                await self._update_text(stream["message_id"], text)
                stream["edit_count"] += 1
            # 超过 20 次编辑则跳过，等 done=True 时发送最终结果
        else:
            # ── 最终 chunk：刷新最终内容并收尾 ──
            if stream is not None:
                if stream["edit_count"] < _MAX_EDITS_PER_MESSAGE:
                    await self._update_text(stream["message_id"], text)
                else:
                    # 编辑次数已超限，发一条新消息作为最终回复
                    await self._create_text(chat_id, text)
                self._streams.pop(source, None)
            else:
                # 无流式记录（单步直接回复），直接创建
                await self._create_text(chat_id, text)

    async def _create_text(self, chat_id: str, text: str) -> str | None:
        """发送一条文本消息，返回 message_id。"""
        try:
            body = _CreateMessageRequestBody.builder() \
                .receive_id(chat_id) \
                .msg_type("text") \
                .content(json.dumps({"text": text}, ensure_ascii=False)) \
                .build()

            request = _CreateMessageRequest.builder() \
                .receive_id_type("chat_id") \
                .request_body(body) \
                .build()

            resp = await self._rest_client.im.v1.message.acreate(request)
            msg_id = resp.data.message_id if resp.data else None
            return msg_id
        except Exception:
            log.exception("飞书创建消息失败")
            return None

    async def _update_text(self, message_id: str, text: str) -> bool:
        """编辑一条已发送的文本消息。返回是否成功。"""
        try:
            body = _UpdateMessageRequestBody.builder() \
                .msg_type("text") \
                .content(json.dumps({"text": text}, ensure_ascii=False)) \
                .build()

            request = _UpdateMessageRequest.builder() \
                .message_id(message_id) \
                .request_body(body) \
                .build()

            await self._rest_client.im.v1.message.aupdate(request)
            return True
        except Exception:
            log.exception("飞书编辑消息失败 %s", message_id)
            return False
