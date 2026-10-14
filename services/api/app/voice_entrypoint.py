"""M14-267：显式语音部署入口（服务端 URL 拆分 + 部署接线）。

M14-266 之后的窄部署接线切片：部署入口显式运行
``app.voice_entrypoint:app``（仅主 compose API 服务切换；镜像默认
CMD 与 ``app.main:app`` 原样不动）。本模块只做三件事：

1. **服务端 URL 拆分**：新增类型化设置 ``livekit_server_url``
   （env ``LIVEKIT_SERVER_URL``）——服务端容器地址，与浏览器面
   ``livekit_url``（token ws_url）/``public_livekit_url``（显式
   公开地址）**语义分离、互不回退、互不改写**；
2. **映射**：``_ServerUrlSettingsAdapter`` 把已加载设置映射为
   M14-266 ``VoiceAppSettingsLike``——恰读 ``livekit_server_url``/
   ``livekit_api_key``/``livekit_api_secret`` 三字段，**浏览器面
   两个字段被硬性挡在装配面之外**（适配器根本不读取）；
3. **复用包装**：``create_voice_entrypoint_app`` 透传 M14-266
   ``create_app_with_voice_subscriber``——凭据/URL/runtime 形状/
   token/净化逻辑全部复用，零重复零放宽。

边界（非声明）：
- **注册 ≠ 启动**：import/lifespan 期间零签发、零订阅者创建/
  启动、零房间/流构造、零 LiveKit SDK 调用（fake-only 测试
  锚定）；不加语音 HTTP 路由/readiness。
- **fail-closed**：``livekit_server_url`` 缺失/非法时模块导入
  即失败（既有 M14-265/266 校验路径）——绝不返回半装配 app、
  绝不静默回退浏览器 URL。
- **get_settings 恰一次**：仅模块级默认 app 使用；纯函数
  ``create_voice_entrypoint_app`` 接受显式设置，不触碰全局
  设置/环境变量/secret 文件。
- **零生产声明**：接线路径存在 ≠ 流式 ASR/VAD/barge-in 可用
  ≠ 生产语音就绪。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from fastapi import FastAPI

from app.core.config import get_settings
from app.voice_app import create_app_with_voice_subscriber


@runtime_checkable
class VoiceEntrypointSettingsLike(Protocol):
    """部署入口最小设置面：恰三属性（服务端 URL + 凭据对）。

    ``livekit_url``/``public_livekit_url``（浏览器面）故意不在
    面内——服务端订阅者装配绝不读取它们。
    """

    @property
    def livekit_server_url(self) -> str | None: ...

    @property
    def livekit_api_key(self) -> str: ...

    @property
    def livekit_api_secret(self) -> str: ...


class _ServerUrlSettingsAdapter:
    """``livekit_server_url`` → M14-266 ``VoiceAppSettingsLike``。

    只暴露恰三个属性（slots 锁死属性面）：服务端 URL 映射进
    ``livekit_url`` 字段；浏览器面设置被结构性忽略（本适配器
    根本不转发它们）。属性读取抛错由 M14-266 守卫统一净化。
    """

    __slots__ = ("_settings",)

    def __init__(self, settings: VoiceEntrypointSettingsLike) -> None:
        self._settings = settings

    @property
    def livekit_url(self) -> str | None:
        return self._settings.livekit_server_url

    @property
    def livekit_api_key(self) -> str:
        return self._settings.livekit_api_key

    @property
    def livekit_api_secret(self) -> str:
        return self._settings.livekit_api_secret


def create_voice_entrypoint_app(
    database_url: str | None = None,
    *,
    settings: VoiceEntrypointSettingsLike,
) -> FastAPI:
    """部署入口纯包装：设置映射 → M14-266 显式接线（零重复逻辑）。

    顺序契约：映射先于工厂装配；非法值经既有 M14-265/266 校验
    路径在任何 FastAPI app 创建之前失败——绝不返回半装配 app。
    接受显式设置对象（测试/调用方注入）；本函数不触碰全局设置。
    """
    return create_app_with_voice_subscriber(
        database_url, settings=_ServerUrlSettingsAdapter(settings)
    )


#: 部署入口默认 app：唯一允许的全局设置读取点（恰一次）。
#: compose 注入 LIVEKIT_SERVER_URL（默认 ws://livekit:7880）——
#: 缺失/非法时导入即失败（fail-closed，绝不静默无工厂启动）。
app = create_voice_entrypoint_app(None, settings=get_settings())


__all__ = [
    "VoiceEntrypointSettingsLike",
    "create_voice_entrypoint_app",
]
