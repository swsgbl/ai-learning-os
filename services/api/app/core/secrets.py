"""M15-008 provider 密钥加密盒：AUTH_SECRET 派生密钥的 AES-256-GCM。

边界（与 Android KeystoreTokenStore 同款语义，服务端版）：
- 密钥由 AUTH_SECRET 经 HKDF-SHA256 派生（info 域分离，不直接当 AES key）；
- 密文落库形态只有 base64(iv ‖ ciphertext‖tag)，无明文；
- AUTH_SECRET 缺失/解密失败一律抛异常（fail-closed：治理端点显式 503，
  绝不降级为明文存储或静默吞错）。
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_IV_BYTES = 12
_HKDF_SALT = b"aios-provider-secrets"
_HKDF_INFO = b"aios-provider-secrets-v1"


class SecretBoxError(RuntimeError):
    """加密盒不可用或密文不可解（调用方按 fail-closed 处理）。"""


def derive_key(auth_secret: str) -> bytes:
    """HKDF-SHA256(auth_secret) → 32B AES-256 key（域分离，不做密钥复用）。"""
    if not auth_secret:
        raise SecretBoxError("AUTH_SECRET 未配置：provider 密钥在线存储不可用")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    ).derive(auth_secret.encode("utf-8"))


class SecretBox:
    def __init__(self, auth_secret: str) -> None:
        self._aes = AESGCM(derive_key(auth_secret))

    def encrypt_to_b64(self, plaintext: str) -> str:
        iv = os.urandom(_IV_BYTES)
        blob = iv + self._aes.encrypt(iv, plaintext.encode("utf-8"), None)
        return base64.b64encode(blob).decode("ascii")

    def decrypt_from_b64(self, stored: str) -> str:
        try:
            blob = base64.b64decode(stored.encode("ascii"), validate=True)
        except Exception as cause:
            raise SecretBoxError("provider 密钥密文损坏（base64 非法）") from cause
        if len(blob) <= _IV_BYTES:
            raise SecretBoxError("provider 密钥密文损坏（长度不足）")
        try:
            plain = self._aes.decrypt(blob[:_IV_BYTES], blob[_IV_BYTES:], None)
        except Exception as cause:
            raise SecretBoxError("provider 密钥解密失败（AUTH_SECRET 变更或密文损坏）") from cause
        return plain.decode("utf-8")
