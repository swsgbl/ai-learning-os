"""测试侧子进程统一执行入口：显式 UTF-8 文本模式（bash / python 通用）。

为什么存在：``subprocess`` 文本模式（``text=True``）的默认解码器是 locale
编码（zh-CN Windows 为 cp936/GBK，Python UTF-8 mode 关闭时生效），而测试
拉起的子进程——bash 脚本与测试桩、以及 ``sys.executable -m pytest`` 这类
python 子进程——输出里随时可能带非 locale 编码字节（仓库脚本/桩恒为
UTF-8 中文消息；回环连接失败的 traceback 亦含多字节内容）。命中非法
多字节序列时，``communicate`` 的 reader 线程抛 ``UnicodeDecodeError``，
stdout/stderr 直接退化为 ``None``，下游断言全灭（PR #74 全量 API 测试在
Windows 暴露的 test_release_candidate / test_smoke_search_script /
test_smoke_voice_cloud_script / test_pg_test_gate 失败根因，同一机制）。

统一约定（helper 层一次修复，不做逐断言兜底）：

- 通用入口 ``run_utf8``：任意 argv（bash、python、其他工具）的捕获输出
  调用一律经此——固定 ``encoding="utf-8"`` + ``errors="replace"``：
  UTF-8 输出按原样解出（中文断言锚点可命中）；偶发杂散非 UTF-8 字节
  （如 Windows 启动器/驱动本地化报错）退化为 U+FFFD 替换符，reader
  线程在任何字节序列下都不可能再抛解码错误。
- bash 语义入口 ``run_bash``：对 ``[BASH, ...]`` 形态 argv 的薄封装
  （不含 env 透传），调用点语义一目了然；非 bash 子进程请直接用
  ``run_utf8``，不要误用 bash 命名。
"""
from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path


def run_utf8(
    command: Sequence[str],
    *,
    timeout: float,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """运行子进程并捕获输出（UTF-8 文本模式，永不因解码炸成 None）。

    参数与各调用点原先的 ``subprocess.run(..., capture_output=True,
    text=True, check=False)`` 形态一一对应：``command`` 为 argv 列表，
    ``timeout``/``cwd``/``env`` 原样透传；返回值与 ``subprocess.run``
    相同（``.stdout``/``.stderr`` 恒为 str）。
    """
    return subprocess.run(
        list(command),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        check=False,
    )


def run_bash(
    command: Sequence[str], *, timeout: float, cwd: Path | str | None = None
) -> subprocess.CompletedProcess:
    """bash 子进程入口（``run_utf8`` 的薄封装，语义糖）。

    供 ``[BASH, ...]`` 形态的调用点使用；需要透传自定义环境（如
    ``sys.executable`` 子进程）时请直接用 ``run_utf8``。
    """
    return run_utf8(command, timeout=timeout, cwd=cwd)
