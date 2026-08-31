"""M4-01 冒烟：真实 LiveKit 容器上验证 token 签发→服务端房间 API→客户端加入。"""
from __future__ import annotations

import asyncio

KEY = "devkey"
SECRET = "ailos-local-dev-secret-0f4c9a1e7b2d"
URL = "http://127.0.0.1:7880"


async def main() -> None:
    import sys

    from livekit import api as lk_api
    from livekit import rtc

    sys.path.insert(0, ".")
    from app.domain.voice_tokens import build_voice_token, verify_voice_token

    # 1) 签发学生 token 并本地验证
    token = build_voice_token(
        "smoke-room",
        "smoke-student",
        api_key=KEY,
        api_secret=SECRET,
        ttl_seconds=600,
    )
    claims = verify_voice_token(token.token, api_key=KEY, api_secret=SECRET)
    print("1) token signed+verified:", claims.identity, claims.video.room)

    # 2) 服务端房间 API（RoomService 用同一套 key/secret）
    server = lk_api.LiveKitAPI(URL, KEY, SECRET)
    try:
        await server.room.create_room(lk_api.CreateRoomRequest(name="smoke-room"))
        rooms = await server.room.list_rooms(lk_api.ListRoomsRequest())
        names = [r.name for r in rooms.rooms]
        print("2) room service ok:", names)
        assert "smoke-room" in names
    finally:
        await server.aclose()

    # 3) 客户端用 token 真实加入房间（浏览器加入的等价服务端路径）
    room = rtc.Room()
    await room.connect("ws://127.0.0.1:7880", token.token)
    print("3) client connected:", room.name, "| remote participants:", room.remote_participants)
    await room.disconnect()
    print("4) disconnect clean")


asyncio.run(main())
