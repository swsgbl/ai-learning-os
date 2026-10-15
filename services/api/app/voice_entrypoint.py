"""M14-267/M14-272e：显式语音部署入口（服务端 URL 拆分 + 管线装配）。

M14-267 交付窄部署入口（服务端 URL 拆分 + 受控订阅者工厂
注册）；M14-272e 把入口内部升级为**管线装配**
（``create_app_with_voice_pipeline``——M14-272a/c/d 的组装器 +
lifespan 安装 + live 路由数据面）。入口模块职责不变，只做四件事：

1. **服务端 URL 拆分**：类型化设置 ``livekit_server_url``
   （env ``LIVEKIT_SERVER_URL``）——服务端容器地址，与浏览器面
   ``livekit_url``/``public_livekit_url`` 语义分离、互不回退；
2. **映射**：``_ServerUrlSettingsAdapter`` 把已加载设置映射为
   M14-272c ``VoicePipelineSettingsLike``——恰读五属性
   （服务端 LiveKit 三件 + ASR 两件），浏览器面两字段被结构性
   忽略（适配器根本不读取）；
3. **复用包装**：``create_voice_entrypoint_app`` 透传
   ``create_app_with_voice_pipeline``——凭据/URL/ASR endpoint
   校验、工厂构造、token、runtime 形状、净化逻辑全部复用
   （M14-265/266/272c），零重复零放宽；
4. **部署接线**：主 compose API 显式运行本入口
   （``app.voice_entrypoint:app``）；ASR 端点经既有
   ``ASR_LOCAL_ENDPOINT`` 槽位注入（compose 拓扑默认
   ``http://host.docker.internal:8010/v1``——FunASR 为宿主机
   服务；transcriber 惰性：构造期零 HTTP，真实转写在会话
   close 冲刷时发生）。

边界（非声明）：
- **注册 ≠ 启动**：import/lifespan 期间零签发、零订阅者创建/
  启动、零房间/流构造、零 LiveKit SDK 调用、零 ASR HTTP；
- **fail-closed**：``livekit_server_url`` 或 ``asr_local_endpoint``
  缺失/非法时模块导入即失败（M14-265/266/272c 校验路径）——
  绝不返回半装配 app、绝不静默回退；
- **get_settings 恰一次**：仅模块级默认 app 使用；纯函数接受
  显式设置，不触碰全局设置/环境变量/secret 文件；
- **零生产声明**：接线路径存在 ≠ 流式 ASR/VAD/barge-in 全链
  已端到端验收 ≠ 生产语音就绪。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from fastapi import FastAPI

from app.core.config import get_settings
from app.voice_pipeline_entry import create_app_with_voice_pipeline


@runtime_checkable
class VoiceEntrypointSettingsLike(Protocol):
    """部署入口最小设置面：恰五属性（服务端 LiveKit 三件 + ASR 两件）。

    ``livekit_url``/``public_livekit_url``（浏览器面）故意不在
    面内——服务端订阅者装配绝不读取它们。
    """

    @property
    def livekit_server_url(self) -> str | None: ...

    @property
    def livekit_api_key(self) -> str: ...

    @property
    def livekit_api_secret(self) -> str: ...

    @property
    def voice_asr_endpoint(self) -> str | None: ...

    @property
    def asr_local_model(self) -> str | None: ...


class _ServerUrlSettingsAdapter:
    """``livekit_server_url`` → M14-272c ``VoicePipelineSettingsLike``。

    只暴露恰五个属性（slots 锁死属性面）：服务端 URL 映射进
    ``livekit_url`` 字段；ASR 两槽位直通（转写链必需——缺失由
    M14-272c fail-closed）；浏览器面设置被结构性忽略。属性读取
    抛错由上游守卫统一净化。
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

    @property
    def asr_local_endpoint(self) -> str | None:
        return self._settings.voice_asr_endpoint

    @property
    def asr_local_model(self) -> str | None:
        return self._settings.asr_local_model


def create_voice_entrypoint_app(
    database_url: str | None = None,
    *,
    settings: VoiceEntrypointSettingsLike,
) -> FastAPI:
    """部署入口纯包装：设置映射 → M14-272c 管线装配（零重复逻辑）。

    顺序契约：映射先于装配；非法值（服务端 URL/凭据/ASR 端点）
    经既有 M14-265/266/272c 校验路径在任何 FastAPI app 创建之前
    失败——绝不返回半装配 app。接受显式设置对象（测试/调用方
    注入）；本函数不触碰全局设置。
    """
    return create_app_with_voice_pipeline(
        database_url, settings=_ServerUrlSettingsAdapter(settings)
    )


#: 部署入口默认 app：唯一允许的全局设置读取点（恰一次）。
#: compose 注入 LIVEKIT_SERVER_URL（默认 ws://livekit:7880）与
#: ASR_LOCAL_ENDPOINT（默认 host.docker.internal 拓扑地址）——
#: 任一缺失/非法时导入即失败（fail-closed，绝不静默降级）。
app = create_voice_entrypoint_app(None, settings=get_settings())


__all__ = [
    "VoiceEntrypointSettingsLike",
    "create_voice_entrypoint_app",
]
