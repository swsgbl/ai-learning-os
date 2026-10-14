"""M14-265：语音应用层工厂装配（纯域层，fake-only 验证）。

M14-264 之后的下一个窄后端基础件：从**显式受控凭据**到
room-aware 服务端订阅者 token provider、再到
``ControlledVoiceSubscriberFactory`` 的纯域装配链——
``VoiceAppFactoryConfig``（frozen slots 显式配置）→
``build_room_aware_token_provider_factory``（凭据 → 恰确
(room, identity) 签发 ``ROLE_SERVER_SUBSCRIBER`` token 的
provider factory）→ ``build_voice_app_subscriber_factory``
（组合：复用 M14-264 的 URL 校验与 runtime 装配，绝不重复
实现或放宽其净化/异常链抑制行为）。**这不是应用运行时接线**：
本切片不连 ``create_app``/FastAPI lifespan/app.state、不读
应用配置/环境变量/.env/LiveKit 凭据、不发起真实 LiveKit 连接
或订阅、不新增 HTTP 路由/readiness——真实接线是后续受控切片
的职责。

安全契约：
- **凭据只进闭包**：API key/API secret 只作为 provider 闭包的
  捕获局部变量存在——绝不成为返回工厂的公开属性、绝不持久化
  为任何 token 之外的工厂/句柄状态值；token 只在订阅者启动
  局部由 ``room.connect`` 消费（M14-255/261/264 契约保持）。
- **签发面最小**：provider factory 恰以已校验 (room, identity)
  调用 ``build_voice_token(..., role=ROLE_SERVER_SUBSCRIBER)``
  且只返回 ``.token``——服务端订阅角色 grants 恒为 subscribe-only
  （join + subscribe；无发布/数据通道/管理面，voice_tokens 契约）。
- **配置失败通用净化**：错误消息一律白名单文案——绝不包含
  URL/API key/API secret/token 或协作者异常文本；统一采用
  守卫-后-``raise ... from None`` 模式抑制异常链（``__cause__``/
  ``__context__``/序列化 traceback 均无敏感值）。
- **签发失败不在装配层吞掉**：``build_voice_token`` 运行期失败
  沿既有 M14-261/M14-264 净化 failed-start 路径流转（订阅者转
  failed 终态 + 通用净化事件；句柄抛通用启动失败错误）——本
  模块不捕获、不改写、不包装该路径。
- **零副作用装配**：装配期零签发、零 runtime 协作者调用、零
  网络/磁盘/进程/定时器/线程/事件循环/日志/SDK 副作用；绝不
  触发订阅者创建或启动路径。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.domain.voice_runtime_factory import (
    build_room_aware_controlled_voice_subscriber_factory,
    validate_livekit_url,
)
from app.domain.voice_subscriber_factory import ControlledVoiceSubscriberFactory
from app.domain.voice_tokens import (
    DEFAULT_TTL_SECONDS,
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    ROLE_SERVER_SUBSCRIBER,
    build_voice_token,
)

#: 通用净化消息白名单（绝不包含 URL/凭据/token/协作者异常文本）。
_MESSAGE_CONFIG_INVALID = "config 非法：必须是 VoiceAppFactoryConfig 实例"
_MESSAGE_CREDENTIAL_INVALID = "API key/API secret 非法：必须为非空普通字符串"
_MESSAGE_TTL_INVALID = (
    f"ttl_seconds 非法：必须为非布尔整数，范围 {MIN_TTL_SECONDS}–{MAX_TTL_SECONDS}"
)


class VoiceAppFactoryError(RuntimeError):
    """装配错误（消息为通用白名单文案，绝不携带 URL/凭据/异常文本）。"""


@dataclass(frozen=True, slots=True)
class VoiceAppFactoryConfig:
    """语音应用层装配的显式不可变配置（只承载四项，别无其他）。

    frozen + slots：构造后不可变、无字典属性面；凭据字段
    ``repr=False`` 抑制——repr 只含非秘密字段（URL/TTL），绝不
    外泄 key/secret（防意外日志泄漏，Codex R1 修正）；凭据仅在
    装配期进入 provider 闭包——本对象本身不校验（校验在装配
    函数先行完成，失败在任何签发/协作者调用之前）。
    """

    livekit_url: str
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    ttl_seconds: int = DEFAULT_TTL_SECONDS


def _validate_credential(value: object) -> str:
    """凭据校验：非空普通字符串（非 str/空白/bytes 一律拒绝）。"""
    if not isinstance(value, str) or not value.strip():
        raise VoiceAppFactoryError(_MESSAGE_CREDENTIAL_INVALID)
    return value


def _validate_ttl_seconds(value: object) -> int:
    """TTL 校验：非布尔整数且在 MIN..MAX（闭区间）内。"""
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not MIN_TTL_SECONDS <= value <= MAX_TTL_SECONDS
    ):
        raise VoiceAppFactoryError(_MESSAGE_TTL_INVALID)
    return value


def build_room_aware_token_provider_factory(
    api_key: object,
    api_secret: object,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> Callable[[str, str], str]:
    """凭据 → room-aware token provider factory（零签发构造）。

    先行校验 key/secret（非空普通字符串）与 TTL（非布尔整数、
    30–86400）；构造期零签发、零 LiveKit 调用、零日志、零持久化。
    返回的 provider factory 恰以调用方传入的 (room, identity)
    签发 ``ROLE_SERVER_SUBSCRIBER`` token 并只返回 ``.token``；
    凭据只作闭包捕获局部变量（绝不成为返回值或工厂属性）。
    运行期 ``build_voice_token`` 失败不在本层吞掉——沿订阅者
    failed-start 净化路径流转。
    """
    canonical_key = _validate_credential(api_key)
    canonical_secret = _validate_credential(api_secret)
    canonical_ttl = _validate_ttl_seconds(ttl_seconds)

    def token_provider_factory(room: str, identity: str) -> str:
        return build_voice_token(
            room,
            identity,
            api_key=canonical_key,
            api_secret=canonical_secret,
            ttl_seconds=canonical_ttl,
            role=ROLE_SERVER_SUBSCRIBER,
        ).token

    return token_provider_factory


def build_voice_app_subscriber_factory(
    config: VoiceAppFactoryConfig,
) -> ControlledVoiceSubscriberFactory:
    """显式配置 → room-aware 受控订阅者工厂（纯组合，零协作者调用）。

    顺序契约：config 类型校验 → 凭据/TTL 校验并构造 provider
    factory（不调用）→ URL 校验（**复用** M14-264
    ``validate_livekit_url`` 的通用净化语义——错误类型/消息同源，
    绝不重复实现）→ 复用 M14-264
    ``build_room_aware_controlled_voice_subscriber_factory``（其
    URL/runtime 形状/异常链抑制行为原样保持，绝不放宽）。装配期
    零签发、零 runtime 协作者调用、绝不触发订阅者创建/启动。
    """
    if not isinstance(config, VoiceAppFactoryConfig):
        raise VoiceAppFactoryError(_MESSAGE_CONFIG_INVALID)
    token_provider_factory = build_room_aware_token_provider_factory(
        config.api_key, config.api_secret, config.ttl_seconds
    )
    canonical_url = validate_livekit_url(config.livekit_url)
    return build_room_aware_controlled_voice_subscriber_factory(
        canonical_url, token_provider_factory
    )


__all__ = [
    "VoiceAppFactoryConfig",
    "VoiceAppFactoryError",
    "build_room_aware_token_provider_factory",
    "build_voice_app_subscriber_factory",
]
