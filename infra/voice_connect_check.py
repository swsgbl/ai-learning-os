"""M9-08 真实语音客户端连通性验证（livekit.rtc 真客户端，非 health 200 冒充）。

流程：Room.connect(ws_url, token) → 断言 connection_state == CONNECTED
→ publish_data（数据通道）→ disconnect。任何一步失败 exit 1 并输出 FAIL。
"""
from __future__ import annotations

import argparse
import asyncio
import sys


async def _run(url: str, token: str, room_name: str) -> int:
    from livekit import rtc

    room = rtc.Room()

    def _on_connected() -> None:
        print("[voice-check] media/data plane negotiated")

    room.on("connection_state_changed")(
        lambda s: print(f"[voice-check] connection_state -> {s}")
    )
    try:
        await room.connect(url, token)
    except Exception as cause:  # noqa: BLE001
        print(f"[voice-check] FAIL: connect failed: {cause}")
        return 1
    try:
        from livekit.rtc import ConnectionState

        if room.connection_state != ConnectionState.CONN_CONNECTED:
            print(f"[voice-check] FAIL: connection_state={room.connection_state}")
            return 1
        print("[voice-check] PASS: connection_state == CONN_CONNECTED")

        # 数据通道：真实端到端 publish（可靠有序通道；发送成功=通道协商完成）
        await room.local_participant.publish_data(b"m9-08-voice-smoke", reliable=True)
        print("[voice-check] PASS: data channel publish accepted")
        await asyncio.sleep(0.3)
    finally:
        await room.disconnect()
        print("[voice-check] PASS: clean disconnect")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--room", required=True)
    args = parser.parse_args()
    return asyncio.run(_run(args.url, args.token, args.room))


if __name__ == "__main__":
    sys.exit(main())
