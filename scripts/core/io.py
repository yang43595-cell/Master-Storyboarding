from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _replace_with_windows_retry(source: Path, destination: Path) -> None:
    """Retry the short sharing violation Windows can raise during concurrent replaces."""
    for attempt in range(5):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.01)


def _atomic_temp_path(path: Path) -> Path:
    """Keep atomic temp names bounded for deep Windows runtime directories."""
    return path.with_name(f".tmp-{os.getpid():x}-{uuid.uuid4().hex[:12]}")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = _atomic_temp_path(path)
    try:
        temp.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        _replace_with_windows_retry(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_text(path: Path, value: str) -> None:
    """Atomically write UTF-8 text with stable LF line endings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = _atomic_temp_path(path)
    try:
        temp.write_text(value, encoding="utf-8", newline="\n")
        _replace_with_windows_retry(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_text_if_changed(path: Path, value: str) -> bool:
    """Opt-in for derived context files; preserve normal business-write semantics."""
    try:
        if path.read_bytes() == value.encode('utf-8'):
            return False
    except FileNotFoundError:
        pass
    write_text(path, value)
    return True


def write_bytes(path: Path, value: bytes) -> None:
    """Atomically replace a binary file and always clean the unique temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = _atomic_temp_path(path)
    try:
        temp.write_bytes(value)
        _replace_with_windows_retry(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_text_sha256(path: Path) -> str:
    """文本文件的跨环境稳定哈希：先剥离 UTF-8 BOM，再把行结束统一为 LF。

    仓库文本按 LF 提交，但干净检出（Windows system core.autocrlf=true）会转成 CRLF，
    且编辑器可能反复开关 BOM（记事本 .NET/Excel 导出）；原始字节哈希会因此跨环境
    误报内容变化。本函数把 BOM 与换行这两类「编码伪差异」归一化后取哈希，保证
    LF/CRLF/带 BOM 三种形态内容等价。口播源哈希（VOICEOVER_HASH_MODE=normalized_text_v1）
    与 knowledge context 必读参考哈希统一使用此约定。
    """
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@contextmanager
def file_lock(lock_path: Path, *, timeout_seconds: float = 10.0) -> Iterator[None]:
    """Small cross-process lock using an atomic lock file."""

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"pid": os.getpid(), "created_at": utc_now()}))
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"无法获取黑箱状态锁：{lock_path}")
            try:
                if time.time() - lock_path.stat().st_mtime > 120:
                    lock_path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
            time.sleep(0.05)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)
