"""QQBot 私聊通道：WS 收消息 → publish_inbound → REST 发消息 ← subscribe_outbound"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
import websockets

from ..bus.messagebus import MessageBus, OutboundItem

log = logging.getLogger("mmsg.channel.qqbot")

_API_BASE = "https://api.sgroup.qq.com"
_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"


class QQBotChannel:
    def __init__(self, app_id: str, client_secret: str, bus: MessageBus):
        self._app_id = app_id
        self._secret = client_secret
        self._bus = bus
        self._client = httpx.AsyncClient(timeout=30)
        self._token: tuple[str, float] | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._bus.subscribe_outbound("qqbot:*", self._on_outbound)
        self._task = asyncio.create_task(self._ws_loop())
        log.info("QQBot channel started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._client.aclose()
        log.info("QQBot channel stopped")

    # ---- WebSocket receive ----

    async def _ws_loop(self) -> None:
        while True:
            try:
                token = await self._access_token()
                gw = await self._api("GET", "/gateway", token=token)
                await self._run_ws(str(gw["url"]), token)
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("gateway connect failed: %s", e)
                await asyncio.sleep(5)

    async def _run_ws(self, url: str, token: str) -> None:
        seq: int | None = None
        hb_task: asyncio.Task | None = None

        async with websockets.connect(url) as ws:
            async for raw in ws:
                msg = json.loads(raw)
                op = msg.get("op")
                d = msg.get("d") or {}
                if isinstance(msg.get("s"), int):
                    seq = msg["s"]

                if op == 10:
                    await ws.send(json.dumps({
                        "op": 2,
                        "d": {
                            "token": f"QQBot {token}",
                            "intents": 1 << 25,
                            "shard": [0, 1],
                        },
                    }))
                    interval = int(d.get("heartbeat_interval", 41250))
                    hb_task = asyncio.create_task(
                        self._heartbeat(ws, interval, lambda: seq)
                    )
                elif op == 0:
                    if msg.get("t") == "C2C_MESSAGE_CREATE":
                        await self._handle_c2c(d)
                elif op == 7:
                    break

        if hb_task:
            hb_task.cancel()

    async def _heartbeat(self, ws: Any, interval_ms: int, seq_fn: Any) -> None:
        while True:
            await asyncio.sleep(max(1, interval_ms / 1000))
            await ws.send(json.dumps({"op": 1, "d": seq_fn()}))

    async def _handle_c2c(self, data: dict) -> None:
        author = data.get("author") or {}
        openid = author.get("user_openid") or data.get("user_openid", "")
        text = (data.get("content") or "").strip()
        if not openid or not text:
            return
        log.info("C2C msg from %s: %s", openid, text[:60])
        await self._bus.publish_inbound(
            f"qqbot:{openid}",
            {"text": text, "openid": openid},
        )

    # ---- REST send ----

    async def _on_outbound(self, item: OutboundItem) -> None:
        openid = item.payload.get("openid")
        text = item.payload.get("text", "")
        if not openid or not text:
            return
        if not item.source.startswith("qqbot:"):
            return
        token = await self._access_token()
        await self._api("POST", f"/v2/users/{openid}/messages", {
            "markdown": {"content": text},
            "msg_type": 2,
            "msg_seq": int(time.time() * 1000) % 65536,
        }, token)

    # ---- Token & API helpers ----

    async def _access_token(self) -> str:
        now = time.time()
        if self._token and now < self._token[1] - 300:
            return self._token[0]
        r = await self._client.post(
            _TOKEN_URL,
            json={"appId": self._app_id, "clientSecret": self._secret},
        )
        r.raise_for_status()
        d = r.json()
        tk = str(d["access_token"])
        expires = now + int(d.get("expires_in", 7200))
        self._token = (tk, expires)
        return tk

    async def _api(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        token: str | None = None,
    ) -> dict:
        tk = token or await self._access_token()
        kw: dict[str, Any] = {
            "headers": {
                "Authorization": f"QQBot {tk}",
                "Content-Type": "application/json",
            },
        }
        if body:
            kw["json"] = body
        r = await self._client.request(method, f"{_API_BASE}{path}", **kw)
        r.raise_for_status()
        return r.json() if r.content else {}
