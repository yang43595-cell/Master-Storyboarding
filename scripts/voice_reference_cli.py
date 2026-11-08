"""Manage reusable character voice references and project snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import wave
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

if __package__:
    from .core.io import project_lock as shared_project_lock
    from .core.artifacts import dependency_closure
    from .core.project_state import (normalize_project_state, reconcile_current_artifacts,
                                     render_project_state_markdown)
else:
    from core.io import project_lock as shared_project_lock
    from core.artifacts import dependency_closure
    from core.project_state import (normalize_project_state, reconcile_current_artifacts,
                                   render_project_state_markdown)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


class VoiceReferenceError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VoiceReferenceError(f"无法读取 JSON：{path}：{exc}") from exc
    if not isinstance(value, dict):
        raise VoiceReferenceError(f"JSON 顶层必须是对象：{path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audio_duration(path: Path) -> float:
    if not path.is_file() or path.stat().st_size <= 0:
        raise VoiceReferenceError(f"声音参考不存在或为空：{path}")
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        duration = float(result.stdout.strip())
    except (FileNotFoundError, subprocess.SubprocessError, ValueError):
        if path.suffix.lower() != ".wav":
            raise VoiceReferenceError(f"无法读取声音时长，请安装 ffprobe 或使用有效 WAV：{path}")
        try:
            with wave.open(str(path), "rb") as handle:
                duration = handle.getnframes() / float(handle.getframerate())
        except (wave.Error, OSError, ZeroDivisionError) as exc:
            raise VoiceReferenceError(f"声音参考不可读：{path}：{exc}") from exc
    if not 2 <= duration <= 30:
        raise VoiceReferenceError(f"声音参考时长必须为 2-30 秒，实际 {duration:.3f} 秒：{path}")
    return round(duration, 3)


def validate_id(value: str, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise VoiceReferenceError(f"{label} 只能包含字母、数字、点、下划线和连字符：{value}")
    return value


def empty_library() -> dict[str, Any]:
    return {"schema_version": "2.0", "updated_at": now(), "voices": []}


def load_library(path: Path) -> dict[str, Any]:
    library = read_json(path)
    version = library.get("schema_version")
    if version not in {"1.0", "2.0"} or not isinstance(library.get("voices"), list):
        raise VoiceReferenceError("声音库必须为 schema_version=1.0/2.0，且 voices 为数组")
    if version == "1.0":
        library["schema_version"] = "2.0"
        for item in library["voices"]:
            if not isinstance(item, dict):
                continue
            item.setdefault(
                "source",
                {
                    "kind": "legacy_unknown",
                    "source_ref": str(item.get("audio_path") or "legacy://unknown"),
                    "speaker_identity_basis": "legacy record without explicit identity evidence",
                },
            )
            item.setdefault(
                "authorization",
                {"status": "unknown", "authorization_ref": "legacy://unverified", "permitted_uses": []},
            )
    return library


def resolve_audio(base_file: Path, raw: str) -> Path:
    value = Path(raw)
    return value.resolve() if value.is_absolute() else (base_file.parent / value).resolve()


def validate_library(path: Path) -> list[dict[str, Any]]:
    library = load_library(path)
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in library["voices"]:
        if not isinstance(item, dict):
            raise VoiceReferenceError("voices 中每一项必须是对象")
        voice_id = validate_id(str(item.get("voice_id") or ""), "voice_id")
        if voice_id in seen:
            raise VoiceReferenceError(f"重复 voice_id：{voice_id}")
        seen.add(voice_id)
        audio = resolve_audio(path, str(item.get("audio_path") or ""))
        duration = audio_duration(audio)
        digest = file_sha256(audio)
        if abs(float(item.get("duration_seconds", -1)) - duration) > 0.05:
            raise VoiceReferenceError(f"声音母版时长记录不一致：{voice_id}")
        if item.get("sha256") != digest:
            raise VoiceReferenceError(f"声音母版已漂移：{voice_id}")
        source = item.get("source")
        authorization = item.get("authorization")
        if not isinstance(source, dict) or not str(source.get("source_ref") or "").strip() or not str(
            source.get("speaker_identity_basis") or ""
        ).strip():
            raise VoiceReferenceError(f"声音来源或说话者身份依据缺失：{voice_id}")
        if not isinstance(authorization, dict) or authorization.get("status") not in {
            "authorized", "restricted", "unknown"
        } or not str(authorization.get("authorization_ref") or "").strip():
            raise VoiceReferenceError(f"声音授权记录无效：{voice_id}")
        result.append(
            {
                "voice_id": voice_id,
                "audio_path": str(audio),
                "duration_seconds": duration,
                "authorization_status": authorization["status"],
            }
        )
    return result


def command_init(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.library).resolve()
    if path.exists():
        raise VoiceReferenceError(f"声音库已存在：{path}")
    write_json(path, empty_library())
    return {"library": str(path)}


def command_add(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.library).resolve()
    library = load_library(path)
    voice_id = validate_id(args.voice_id, "voice_id")
    source = Path(args.audio).resolve()
    duration = audio_duration(source)
    source_kind = str(getattr(args, "source_kind", "") or "").strip()
    source_ref = str(getattr(args, "source_ref", "") or "").strip()
    speaker_identity_basis = str(getattr(args, "speaker_identity_basis", "") or "").strip()
    authorization_status = str(getattr(args, "authorization_status", "") or "").strip()
    authorization_ref = str(getattr(args, "authorization_ref", "") or "").strip()
    permitted_uses = list(dict.fromkeys(getattr(args, "permitted_use", None) or []))
    if source_kind not in {"user_recording", "licensed_library", "generated_candidate", "other"}:
        raise VoiceReferenceError("导入声音必须提供有效 --source-kind")
    if not source_ref or not speaker_identity_basis:
        raise VoiceReferenceError("导入声音必须提供 --source-ref 和 --speaker-identity-basis")
    if authorization_status not in {"authorized", "restricted", "unknown"} or not authorization_ref:
        raise VoiceReferenceError("导入声音必须提供授权状态和 --authorization-ref")
    existing = next((item for item in library["voices"] if item.get("voice_id") == voice_id), None)
    if existing and not args.replace:
        raise VoiceReferenceError(f"voice_id 已存在；如需替换请显式使用 --replace：{voice_id}")
    target = path.parent / "audio" / f"{voice_id}{source.suffix.lower()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and source.resolve() != target.resolve() and not args.replace:
        raise VoiceReferenceError(f"声音库目标文件已存在；拒绝静默覆盖：{target}")
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    entry = {
        "voice_id": voice_id,
        "name": args.name,
        "audio_path": str(target.relative_to(path.parent)),
        "character_types": list(dict.fromkeys(args.character_type or [])),
        "duration_seconds": duration,
        "sha256": file_sha256(target),
        "source": {
            "kind": source_kind,
            "source_ref": source_ref,
            "speaker_identity_basis": speaker_identity_basis,
        },
        "authorization": {
            "status": authorization_status,
            "authorization_ref": authorization_ref,
            "permitted_uses": permitted_uses,
        },
    }
    if existing:
        library["voices"][library["voices"].index(existing)] = entry
    else:
        library["voices"].append(entry)
    library["updated_at"] = now()
    write_json(path, library)
    return entry


def command_list(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.library).resolve()
    library = load_library(path)
    return {"library": str(path), "voices": library["voices"]}


def command_validate(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.library).resolve()
    return {"library": str(path), "voices": validate_library(path)}


def project_manifest_path(project: Path) -> Path:
    legacy = project / "05_声音与配音"
    directory = legacy if legacy.is_dir() and not (project / "07_声音与配音").is_dir() else project / "07_声音与配音"
    return directory / "voice_reference_manifest.json"


def voice_scope_errors(assignment: dict[str, Any], segment: dict[str, Any] | None = None,
                       dialogue_ids: list[str] | None = None) -> list[str]:
    """声音范围的唯一判定：显式项目/场景/片段范围，排除项优先。"""
    applicable = assignment.get("applicable_scope")
    excluded = assignment.get("excluded_scope", [])
    for label, values in (("适用", applicable), ("排除", excluded)):
        if not isinstance(values, list) or (label == "适用" and not values):
            return [f"声音{label}范围必须为{'非空' if label == '适用' else ''}数组"]
        for token in values:
            if not isinstance(token, str) or not (token == "project" or re.fullmatch(
                r"(?:scene|segment):[A-Za-z0-9._-]+", token
            )):
                return [f"无效声音{label}范围：{token}；使用 project、scene:<ID> 或 segment:<ID>"]
    dialogue_scope = assignment.get("dialogue_scope")
    if dialogue_scope not in {"all_confirmed_dialogue_for_character", "specific_dialogue_ids"}:
        return ["声音对白范围无效"]
    permitted = assignment.get("dialogue_ids", [])
    if not isinstance(permitted, list) or (dialogue_scope == "specific_dialogue_ids" and not permitted):
        return ["声音指定对白范围必须至少提供一个 --dialogue-id"]
    if segment is None:
        return []
    context = {"project", f"segment:{segment.get('id', '')}"}
    if segment.get("scene_id"):
        context.add(f"scene:{segment['scene_id']}")
    if context.intersection(excluded):
        return [f"片段 {segment.get('id')} 位于声音排除范围"]
    if not context.intersection(applicable):
        return [f"片段 {segment.get('id')} 超出声音适用范围"]
    if dialogue_scope == "specific_dialogue_ids" and not set(dialogue_ids or []) <= set(permitted):
        return ["声音引用超出已确认对白范围"]
    return []


def load_project_manifest(project: Path) -> dict[str, Any]:
    path = project_manifest_path(project)
    if not path.exists():
        return {"schema_version": "2.0", "project_path": str(project), "updated_at": now(), "assignments": []}
    value = read_json(path)
    if value.get("schema_version") not in {"1.0", "2.0"} or not isinstance(value.get("assignments"), list):
        raise VoiceReferenceError(f"项目声音清单结构无效：{path}")
    if value.get("schema_version") == "1.0":
        value["schema_version"] = "2.0"
        for item in value["assignments"]:
            if not isinstance(item, dict):
                continue
            item.setdefault("source", {"kind": "legacy_unknown", "source_ref": "legacy://unknown", "speaker_identity_basis": "legacy record"})
            item.setdefault("authorization", {"status": "unknown", "authorization_ref": "legacy://unverified", "permitted_uses": []})
            item.setdefault("dialogue_scope", "all_confirmed_dialogue_for_character")
            item.setdefault("dialogue_ids", [])
            item.setdefault("applicable_scope", ["project"])
            item.setdefault("excluded_scope", [])
            item.setdefault("snapshot_created_at", str(value.get("updated_at") or "legacy://unknown"))
    return value


def next_project_state(
    project: Path, manifest: dict[str, Any], *, audio_policy: str | None = None
) -> tuple[Path, dict[str, Any]]:
    state_path = project / "00_项目管理" / "project_state.json"
    if not state_path.exists():
        return state_path, {}
    state = read_json(state_path)
    state["voice_reference_manifest_path"] = str(project_manifest_path(project).relative_to(project))
    state["voice_assignments"] = {
        item["character_id"]: item["voice_id"] for item in manifest["assignments"] if item.get("status") == "current"
    }
    if audio_policy is not None:
        state["audio_policy"] = audio_policy
    return state_path, state


def invalidate_character_segments(
    project: Path,
    character_id: str,
    old_voice: str,
    new_voice: str,
) -> tuple[list[str], dict[str, Any] | None]:
    """把引用该角色声音的生产段标为 stale。

    只读并返回全新 plan 字典（不落盘），由 _snapshot_core 一并原子提交。
    缺 production_plan.json 时返回 ([], None)；无受影响段时返回 ([], 原 plan)。
    """
    plan_path = project / "00_项目管理" / "production_plan.json"
    if not plan_path.exists():
        return [], None
    plan = read_json(plan_path)
    affected: list[str] = []
    changed = False
    for segment in plan.get("segments") or []:
        speakers = segment.get("speaking_characters") or []
        ids = {str(item.get("character_id")) for item in speakers if isinstance(item, dict)}
        if character_id not in ids:
            continue
        segment["status"] = "stale"
        segment.setdefault("stale_reasons", []).append(
            {"reason": (f"角色 {character_id} 声音参考内容或适用范围更新（{new_voice}）" if old_voice == new_voice
                        else f"角色 {character_id} 声音母版由 {old_voice} 更换为 {new_voice}"), "at": now()}
        )
        affected.append(str(segment.get("id") or ""))
        changed = True
    return affected, (plan if changed else None)


def commit_json_atomic(pairs: list[tuple[Path, dict[str, Any] | str]]) -> None:
    """全成全败：先写全部 .tmp，再逐个替换；任一失败恢复原内容。"""
    originals: dict[str, str | None] = {}
    tmp_pairs: list[tuple[Path, Path]] = []
    try:
        for path, value in pairs:
            originals[str(path)] = path.read_text(encoding="utf-8") if path.exists() else None
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
            tmp.write_text(text, encoding="utf-8")
            tmp_pairs.append((path, tmp))
    except OSError as exc:
        for _path, tmp in tmp_pairs:
            tmp.unlink(missing_ok=True)
        raise VoiceReferenceError(f"声音绑定 JSON 预备写入失败：{exc}") from exc
    try:
        for path, tmp in tmp_pairs:
            tmp.replace(path)
    except OSError as exc:
        for path, _tmp in tmp_pairs:
            original = originals[str(path)]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(original, encoding="utf-8")
        raise VoiceReferenceError(f"声音绑定 JSON 提交失败：{exc}") from exc


@contextmanager
def project_lock(project: Path) -> Iterator[None]:
    """Share the revision lock because voice binding also writes project state."""
    try:
        with shared_project_lock(project):
            yield
    except TimeoutError as exc:
        raise VoiceReferenceError(f"无法获取项目声音锁：{project}") from exc


def _snapshot_core(args: argparse.Namespace, *, mode: str) -> dict[str, Any]:
    if not args.confirmed_by_user:
        raise VoiceReferenceError("项目锁定声音母版前必须显式传入 --confirmed-by-user")
    if not str(args.approval_ref or "").strip():
        raise VoiceReferenceError("项目锁定声音母版前必须提供 --approval-ref")
    library_path = Path(args.library).resolve()
    library = load_library(library_path)
    project = Path(args.project).resolve()
    if not project.is_dir():
        raise VoiceReferenceError(f"项目目录不存在：{project}")
    character_id = validate_id(args.character_id, "character_id")
    voice = next((item for item in library["voices"] if item.get("voice_id") == args.voice_id), None)
    if voice is None:
        raise VoiceReferenceError(f"声音库中不存在 voice_id：{args.voice_id}")
    source = resolve_audio(library_path, voice["audio_path"])
    audio_duration(source)
    source_digest = file_sha256(source)
    if source_digest != voice.get("sha256"):
        raise VoiceReferenceError(f"声音母版已漂移：{args.voice_id}")
    authorization = voice.get("authorization") or {}
    if authorization.get("status") != "authorized":
        raise VoiceReferenceError(f"声音授权状态不是 authorized，不能建立 current 快照：{args.voice_id}")
    scope = {
        "dialogue_scope": str(getattr(args, "dialogue_scope", None) or "all_confirmed_dialogue_for_character"),
        "dialogue_ids": list(dict.fromkeys(getattr(args, "dialogue_id", None) or [])),
        "applicable_scope": list(dict.fromkeys(getattr(args, "applicable_scope", None) or ["project"])),
        "excluded_scope": list(dict.fromkeys(getattr(args, "excluded_scope", None) or [])),
    }
    scope_issues = voice_scope_errors(scope)
    if scope_issues:
        raise VoiceReferenceError("；".join(scope_issues))

    with project_lock(project):
        manifest = load_project_manifest(project)
        existing = next(
            (item for item in manifest["assignments"] if item.get("character_id") == character_id), None
        )
        unresolved_legacy = [
            str(item.get("character_id") or "")
            for item in manifest["assignments"]
            if item is not existing and (item.get("authorization") or {}).get("status") != "authorized"
        ]
        if unresolved_legacy:
            raise VoiceReferenceError(
                "项目仍有来源或授权未确认的旧声音绑定，拒绝写入 v2 current 清单："
                + ", ".join(unresolved_legacy)
            )
        old_voice = str(existing.get("voice_id")) if existing else None
        old_snapshot_raw = Path(str(existing.get("snapshot_path") or "")) if existing else None
        if existing and old_snapshot_raw is not None:
            old_path = old_snapshot_raw.resolve() if old_snapshot_raw.is_absolute() else (project / old_snapshot_raw).resolve()
            if not old_path.is_file() or file_sha256(old_path) != existing.get("sha256"):
                raise VoiceReferenceError(f"项目当前声音快照缺失或已漂移：{character_id}")
        force = bool(getattr(args, "replace", False)) or mode == "replace"
        replacing = existing is not None and (
            old_voice != args.voice_id or existing.get("sha256") != source_digest
            or any(existing.get(key) != value for key, value in scope.items())
        )

        if mode == "bind":
            if replacing:
                raise VoiceReferenceError(f"角色已绑定其他声音；如需更换请显式使用 replace：{character_id}")
        elif mode == "replace":
            if existing is None:
                raise VoiceReferenceError(f"角色尚未绑定声音，无法使用 replace：{character_id}")
            if not replacing:
                return {"assignment": dict(existing), "manifest": str(project_manifest_path(project)),
                        "invalidated_segments": [], "invalidated_artifacts": []}
        else:  # auto（snapshot 兼容）：bind 或 replace，replace 用 --replace 门禁
            if replacing and not force:
                raise VoiceReferenceError(f"角色声音编号、音频内容或范围已变化；如需更换请显式使用 --replace：{character_id}")

        revision = ""
        plan_path = project / "00_项目管理" / "production_plan.json"
        if plan_path.exists():
            try:
                revision = str(read_json(plan_path).get("revision") or "")
            except VoiceReferenceError:
                revision = ""
        if replacing:
            contract_issues = _validate_replace_contract(project, character_id)
            if contract_issues:
                raise VoiceReferenceError("换声契约校验未通过：" + "；".join(contract_issues))

        # 复制新音频到快照目标（新路径，不先动旧文件）。
        target_dir = project_manifest_path(project).parent / "声音母版"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{character_id}__{args.voice_id}{source.suffix.lower()}"
        if replacing:
            # 同编号换音频也保留旧快照；JSON 提交失败时旧绑定及文件仍可用。
            target = target.with_name(f"{target.stem}__{source_digest[:12]}{target.suffix}")
            version = 2
            while target.exists():
                target = target_dir / f"{character_id}__{args.voice_id}__{source_digest[:12]}_v{version}{source.suffix.lower()}"
                version += 1
        elif existing and old_snapshot_raw is not None:
            old_path = old_snapshot_raw.resolve() if old_snapshot_raw.is_absolute() else (project / old_snapshot_raw).resolve()
            if old_path.is_file():
                target = old_path
        if target.exists() and file_sha256(target) != source_digest:
            raise VoiceReferenceError(f"项目声音快照目标已存在且内容不同；拒绝静默覆盖：{target}")
        if not target.exists():
            shutil.copy2(source, target)

        entry = {
            "character_id": character_id,
            "character_name": args.character_name,
            "voice_id": args.voice_id,
            "voice_name": voice["name"],
            "source": voice["source"],
            "authorization": voice["authorization"],
            **scope,
            "snapshot_path": str(target.relative_to(project)),
            "duration_seconds": float(voice["duration_seconds"]),
            "sha256": file_sha256(target),
            "snapshot_created_at": now(),
            "status": "current",
            "confirmed_by_user": True,
            "approval_ref": args.approval_ref,
        }

        # 内存中准备全部 JSON，全成全败。
        pairs: list[tuple[Path, dict[str, Any]]] = []
        if existing:
            manifest["assignments"][manifest["assignments"].index(existing)] = entry
        else:
            manifest["assignments"].append(entry)
        manifest["updated_at"] = now()
        pairs.append((project_manifest_path(project), manifest))

        affected: list[str] = []
        if replacing:
            affected, new_plan = invalidate_character_segments(project, character_id, old_voice, args.voice_id)
            if new_plan is not None:
                pairs.append((project / "00_项目管理" / "production_plan.json", new_plan))
        state_path, state_dict = next_project_state(project, manifest)
        if state_dict:
            pairs.append((state_path, state_dict))

        # 锚点登记 + 换声按 depends_on 反图失效，并入同一原子提交。
        artifact_manifest, invalidated_artifacts = _prepare_voice_anchor(
            project,
            character_id,
            str(target.relative_to(project)),
            replacing=replacing,
            old_voice=old_voice or "",
            new_voice=args.voice_id,
            revision=revision,
        )
        if invalidated_artifacts or any(
            item.get("logical_id") == voice_anchor_id(character_id) for item in artifact_manifest["artifacts"]
        ):
            pairs.append((artifact_manifest_path(project), artifact_manifest))
        if state_dict:
            selected = reconcile_current_artifacts(normalize_project_state(state_dict), artifact_manifest)
            synchronized = {**state_dict, "current_artifacts": selected["current_artifacts"]}
            pairs = [(path, synchronized if path == state_path else value) for path, value in pairs]
            pairs.append((state_path.with_suffix(".md"),
                          render_project_state_markdown(normalize_project_state(synchronized))))
        commit_json_atomic(pairs)

        # 全部提交后才移动旧快照到历史，失败不回滚（新绑定已生效，旧文件成孤立备份）。
        if replacing and old_snapshot_raw is not None:
            old_path = old_snapshot_raw.resolve() if old_snapshot_raw.is_absolute() else (project / old_snapshot_raw).resolve()
            if old_path.is_file() and old_path != target.resolve():
                history = project / "99_历史与废弃" / "声音母版"
                history.mkdir(parents=True, exist_ok=True)
                history_target = history / old_path.name
                if history_target.exists():
                    history_target = history / f"{old_path.stem}_{datetime.now().strftime('%Y%m%d%H%M%S')}{old_path.suffix}"
                try:
                    shutil.move(str(old_path), str(history_target))
                except OSError:
                    pass

    return {
        "assignment": entry,
        "manifest": str(project_manifest_path(project)),
        "invalidated_segments": affected,
        "invalidated_artifacts": invalidated_artifacts,
    }


def command_bind(args: argparse.Namespace) -> dict[str, Any]:
    return _snapshot_core(args, mode="bind")


def command_replace(args: argparse.Namespace) -> dict[str, Any]:
    return _snapshot_core(args, mode="replace")


def command_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    return _snapshot_core(args, mode="auto")


def command_status(args: argparse.Namespace) -> dict[str, Any]:
    project = Path(args.project).resolve()
    if not project.is_dir():
        raise VoiceReferenceError(f"项目目录不存在：{project}")
    manifest = load_project_manifest(project)
    bindings = [
        {key: item.get(key) for key in ("character_id", "character_name", "voice_id", "voice_name", "dialogue_scope", "dialogue_ids", "applicable_scope", "excluded_scope", "snapshot_path", "status")}
        for item in manifest["assignments"]
    ]
    character_segments: dict[str, list[str]] = {}
    plan_path = project / "00_项目管理" / "production_plan.json"
    if plan_path.exists():
        plan = read_json(plan_path)
        for segment in plan.get("segments") or []:
            speakers = segment.get("speaking_characters") or []
            for item in speakers:
                if not isinstance(item, dict) or not item.get("character_id"):
                    continue
                cid = str(item.get("character_id"))
                character_segments.setdefault(cid, []).append(
                    {
                        "segment_id": str(segment.get("id") or ""),
                        "status": segment.get("status"),
                    }
                )
    return {
        "project": str(project),
        "bindings": bindings,
        "character_segments": character_segments,
    }


def artifact_manifest_path(project: Path) -> Path:
    return project / "00_项目管理" / "artifact_manifest.json"


def load_artifact_manifest(project: Path) -> dict[str, Any]:
    path = artifact_manifest_path(project)
    if not path.exists():
        return {"schema_version": "1.0", "artifacts": []}
    value = read_json(path)
    value.setdefault("artifacts", [])
    return value


def voice_anchor_id(character_id: str) -> str:
    return f"voice.character.{character_id}"


def _prepare_voice_anchor(
    project: Path,
    character_id: str,
    snapshot_path: str,
    *,
    replacing: bool,
    old_voice: str,
    new_voice: str,
    revision: str,
) -> tuple[dict[str, Any], list[str]]:
    """在内存中准备 artifact_manifest：确保锚点登记，换声时按 depends_on 反图失效。

    返回 (manifest, invalidated_logical_ids)；不落盘，由调用方并入原子提交。
    """
    manifest = load_artifact_manifest(project)
    anchor = voice_anchor_id(character_id)
    anchor_item = next((item for item in manifest["artifacts"] if item.get("logical_id") == anchor), None)
    if anchor_item is None:
        manifest.setdefault("artifacts", []).append(
            {
                "artifact_id": f"ART-VOICE-{character_id}",
                "logical_id": anchor,
                "type": "voice_reference",
                "path_or_url": snapshot_path,
                "revision": revision,
                "status": "current",
                "depends_on": [],
                "source_stage": "voice-reference-manager",
                "user_approved": True,
                "created_at": now(),
            }
        )
    else:
        # 换声：锚点必须跟踪最新快照，并刷新 revision / 确认态；绝不残留旧文件路径。
        anchor_item["path_or_url"] = snapshot_path
        anchor_item["revision"] = revision
        anchor_item["status"] = "current"
        anchor_item["source_stage"] = "voice-reference-manager"
        anchor_item["depends_on"] = anchor_item.get("depends_on") or []
        anchor_item["user_approved"] = True
        anchor_item.pop("stale_reasons", None)
    invalidated: list[str] = []
    if not replacing:
        return manifest, invalidated
    # depends_on 反图闭包：anchor → prompt.<seg> → video.<seg>
    impacted = dependency_closure(manifest, {anchor})
    for item in manifest["artifacts"]:
        logical_id = item.get("logical_id")
        if item.get("status") == "current" and logical_id in impacted and logical_id != anchor:
            invalidated.append(str(logical_id))
            item["status"] = "stale"
            item.setdefault("stale_reasons", []).append(
                {
                    "reason": (f"角色 {character_id} 声音参考内容或适用范围更新（{new_voice}）" if old_voice == new_voice
                               else f"角色 {character_id} 声音母版由 {old_voice} 更换为 {new_voice}"),
                    "revision": revision,
                    "at": now(),
                }
            )
    return manifest, invalidated


def _validate_replace_contract(project: Path, character_id: str) -> list[str]:
    """换声契约校验：锚点必须已登记；引用该角色的 current prompt.<seg> 必须声明 depends_on 锚点。

    缺失即阻止换声（绝不静默不失效、绝不回退全量失效）。
    """
    manifest = load_artifact_manifest(project)
    anchor = voice_anchor_id(character_id)
    issues: list[str] = []
    if not any(item.get("logical_id") == anchor for item in manifest["artifacts"]):
        issues.append(f"换声契约缺失：锚点 {anchor} 未在 artifact_manifest.json 登记")
    plan_path = project / "00_项目管理" / "production_plan.json"
    seg_ids: list[str] = []
    if plan_path.exists():
        plan = read_json(plan_path)
        for segment in plan.get("segments") or []:
            speakers = segment.get("speaking_characters") or []
            if any(
                str(item.get("character_id") or "") == character_id
                for item in speakers
                if isinstance(item, dict)
            ):
                seg_ids.append(str(segment.get("id") or ""))
    manifest_lids = {
        str(item.get("logical_id")): item
        for item in manifest["artifacts"]
        if item.get("logical_id")
    }
    for seg_id in seg_ids:
        prompt_lid = f"prompt.{seg_id}"
        prompt_item = manifest_lids.get(prompt_lid)
        if prompt_item is None:
            continue
        if prompt_item.get("status") == "current" and anchor not in (prompt_item.get("depends_on") or []):
            issues.append(f"换声契约缺失：{prompt_lid} 未声明 depends_on {anchor}")
    return issues


def derive_voice_sync(plan: dict[str, Any], manifest: dict[str, Any], manifest_rel_path: str) -> dict[str, Any]:
    """确定性推导 speaking_characters（voice-sync-plan，见 SKILL 规则）。

    - segment.voiceover_line_ids=[] → speaking_characters=[]（合法，不上传声音）。
    - 引用行缺 character_id → ERROR VOICE_LINE_UNATTRIBUTED（绝不猜归属）。
    - 角色未绑定 / 缺用户确认 → ERROR。
    - 任一角色非空时才写 audio_policy=voice_reference。
    """
    lines_by_id = {str(line.get("id") or ""): line for line in (plan.get("voiceover_lock") or {}).get("lines") or []}
    assignments: dict[str, dict[str, Any]] = {}
    for item in manifest.get("assignments") or []:
        if not isinstance(item, dict) or item.get("status") != "current":
            continue
        cid = str(item.get("character_id") or "")
        if cid:
            assignments[cid] = item
    errors: list[dict[str, Any]] = []
    any_speaking = False
    for segment in plan.get("segments") or []:
        line_ids = [str(x) for x in (segment.get("voiceover_line_ids") or [])]
        char_cues: dict[str, list[dict[str, Any]]] = {}
        char_names: dict[str, str] = {}
        for line_id in line_ids:
            line = lines_by_id.get(line_id)
            if line is None:
                errors.append(
                    {"code": "VOICE_LINE_UNKNOWN", "segment_id": segment.get("id"), "message": f"对白行不存在：{line_id}"}
                )
                continue
            char_id = str(line.get("character_id") or "").strip()
            if not char_id:
                errors.append(
                    {
                        "code": "VOICE_LINE_UNATTRIBUTED",
                        "segment_id": segment.get("id"),
                        "message": f"对白行未归属角色：{line_id}",
                    }
                )
                continue
            cue: dict[str, Any] = {"dialogue": str(line.get("text") or "")}
            time_range = line.get("time_range")
            if isinstance(time_range, list) and len(time_range) == 2:
                cue["time_range"] = time_range
            char_cues.setdefault(char_id, []).append(cue)
            char_names.setdefault(char_id, str(line.get("character_name") or ""))
        speakers: list[dict[str, Any]] = []
        for char_id, cues in char_cues.items():
            assignment = assignments.get(char_id)
            if assignment is None:
                errors.append(
                    {
                        "code": "VOICE_REFERENCE_BINDING_MISSING",
                        "segment_id": segment.get("id"),
                        "message": f"出声角色没有声音母版绑定：{char_id}",
                    }
                )
                continue
            if assignment.get("confirmed_by_user") is not True or not str(
                assignment.get("approval_ref") or ""
            ).strip():
                errors.append(
                    {
                        "code": "VOICE_REFERENCE_APPROVAL_MISSING",
                        "segment_id": segment.get("id"),
                        "message": f"角色声音母版缺少用户确认记录：{char_id}",
                    }
                )
                continue
            if (assignment.get("authorization") or {}).get("status") != "authorized":
                errors.append(
                    {
                        "code": "VOICE_REFERENCE_AUTHORIZATION_MISSING",
                        "segment_id": segment.get("id"),
                        "message": f"角色声音母版授权状态不可用于项目：{char_id}",
                    }
                )
                continue
            used = [line_id for line_id in line_ids
                    if str((lines_by_id.get(line_id) or {}).get("character_id") or "") == char_id]
            scope_issues = voice_scope_errors(assignment, segment, used)
            if scope_issues:
                errors.append({"code": "VOICE_REFERENCE_SCOPE_VIOLATION", "segment_id": segment.get("id"),
                               "message": f"角色 {char_id}：{'；'.join(scope_issues)}"})
                continue
            name = char_names.get(char_id) or str(assignment.get("character_name") or char_id)
            speakers.append({"character_id": char_id, "character_name": name, "dialogue_cues": cues})
        if speakers:
            any_speaking = True
        segment["speaking_characters"] = speakers
    if errors:
        return {"ok": False, "errors": errors}
    if any_speaking:
        plan["audio_policy"] = "voice_reference"
        plan["voice_reference_manifest_path"] = manifest_rel_path
    else:
        plan.pop("voice_reference_manifest_path", None)
    return {"ok": True, "plan": plan, "segments_with_voice": any_speaking}


def command_sync_plan(args: argparse.Namespace) -> dict[str, Any]:
    project = Path(args.project).resolve()
    if not project.is_dir():
        raise VoiceReferenceError(f"项目目录不存在：{project}")
    plan_path = Path(args.plan)
    plan_path = plan_path.resolve() if plan_path.is_absolute() else (project / plan_path).resolve()
    manifest_path = Path(args.manifest)
    manifest_path = manifest_path.resolve() if manifest_path.is_absolute() else (project / manifest_path).resolve()
    if not plan_path.is_file():
        raise VoiceReferenceError(f"生产计划不存在：{plan_path}")
    if not manifest_path.is_file():
        raise VoiceReferenceError(f"项目声音清单不存在：{manifest_path}")
    with project_lock(project):
        plan = read_json(plan_path)
        manifest = read_json(manifest_path)
        manifest_rel = str(manifest_path.relative_to(project))
        result = derive_voice_sync(plan, manifest, manifest_rel)
        if not result["ok"]:
            messages = "；".join(item["message"] for item in result["errors"])
            raise VoiceReferenceError(f"voice-sync-plan 存在硬错误：{messages}")
        updated_plan = result["plan"]
        # 同步 project_state.json：audio_policy / 声音清单路径 / 角色绑定投影，与计划一并原子提交。
        state_path, state_dict = next_project_state(project, manifest, audio_policy=updated_plan.get("audio_policy"))
        pairs: list[tuple[Path, dict[str, Any]]] = [(plan_path, updated_plan)]
        if state_dict:
            pairs.append((state_path, state_dict))
        commit_json_atomic(pairs)
    return {
        "plan": str(plan_path),
        "audio_policy": result["plan"].get("audio_policy"),
        "segments_with_voice": result["segments_with_voice"],
    }


def validate_project(project: Path) -> dict[str, Any]:
    manifest_path = project_manifest_path(project)
    manifest = read_json(manifest_path)
    seen: set[str] = set()
    seen_snapshots: set[str] = set()
    assignments: list[dict[str, Any]] = []
    for item in manifest.get("assignments") or []:
        character_id = validate_id(str(item.get("character_id") or ""), "character_id")
        if character_id in seen:
            raise VoiceReferenceError(f"同一角色存在多个 current 声音绑定：{character_id}")
        seen.add(character_id)
        if item.get("status") != "current" or item.get("confirmed_by_user") is not True or not str(
            item.get("approval_ref") or ""
        ).strip():
            raise VoiceReferenceError(f"项目声音绑定不是已确认 current：{character_id}")
        if (item.get("authorization") or {}).get("status") != "authorized":
            raise VoiceReferenceError(f"项目声音绑定缺少可用授权：{character_id}")
        scope_issues = voice_scope_errors(item)
        if scope_issues:
            raise VoiceReferenceError(f"角色 {character_id}：{'；'.join(scope_issues)}")
        raw_path = Path(str(item.get("snapshot_path") or ""))
        path = raw_path.resolve() if raw_path.is_absolute() else (project / raw_path).resolve()
        path_key = str(path).lower()
        if path_key in seen_snapshots:
            raise VoiceReferenceError(f"多个角色不能共享同一个声音快照：{path}")
        seen_snapshots.add(path_key)
        duration = audio_duration(path)
        if abs(float(item.get("duration_seconds", -1)) - duration) > 0.05:
            raise VoiceReferenceError(f"项目声音快照时长记录不一致：{character_id}")
        if file_sha256(path) != item.get("sha256"):
            raise VoiceReferenceError(f"项目声音快照已漂移：{character_id}")
        assignments.append(
            {
                "character_id": character_id,
                "voice_id": item.get("voice_id"),
                "dialogue_scope": item.get("dialogue_scope"),
                "applicable_scope": item.get("applicable_scope"),
                "snapshot_path": str(path),
            }
        )
    return {"manifest": str(manifest_path), "assignments": assignments}


def command_validate_project(args: argparse.Namespace) -> dict[str, Any]:
    project = Path(args.project).resolve()
    return validate_project(project)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="角色声音参考库与项目快照管理")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="创建空声音库")
    init.add_argument("--library", required=True)
    init.set_defaults(func=command_init)
    add = sub.add_parser("add", help="导入声音母版")
    add.add_argument("--library", required=True)
    add.add_argument("--voice-id", required=True)
    add.add_argument("--name", required=True)
    add.add_argument("--audio", required=True)
    add.add_argument("--character-type", action="append")
    add.add_argument("--source-kind", choices=["user_recording", "licensed_library", "generated_candidate", "other"], required=True)
    add.add_argument("--source-ref", required=True)
    add.add_argument("--speaker-identity-basis", required=True)
    add.add_argument("--authorization-status", choices=["authorized", "restricted", "unknown"], required=True)
    add.add_argument("--authorization-ref", required=True)
    add.add_argument("--permitted-use", action="append")
    add.add_argument("--replace", action="store_true")
    add.set_defaults(func=command_add)
    listing = sub.add_parser("list", help="列出声音母版")
    listing.add_argument("--library", required=True)
    listing.set_defaults(func=command_list)
    validate = sub.add_parser("validate", help="校验声音库")
    validate.add_argument("--library", required=True)
    validate.set_defaults(func=command_validate)
    def _bind_args(sub_parser: argparse.ArgumentParser) -> None:
        sub_parser.add_argument("--library", required=True)
        sub_parser.add_argument("--project", required=True)
        sub_parser.add_argument("--voice-id", required=True)
        sub_parser.add_argument("--character-id", required=True)
        sub_parser.add_argument("--character-name", required=True)
        sub_parser.add_argument("--confirmed-by-user", action="store_true")
        sub_parser.add_argument("--approval-ref", required=True)
        sub_parser.add_argument("--dialogue-scope", choices=["all_confirmed_dialogue_for_character", "specific_dialogue_ids"], default="all_confirmed_dialogue_for_character")
        sub_parser.add_argument("--dialogue-id", action="append")
        sub_parser.add_argument("--applicable-scope", action="append",
                               help="适用范围：project、scene:<ID>、segment:<ID>；可重复，未指定默认 project")
        sub_parser.add_argument("--excluded-scope", action="append",
                               help="排除范围：project、scene:<ID>、segment:<ID>；可重复，优先于适用范围")

    bind = sub.add_parser("bind", help="把声音母版首次绑定到项目角色")
    _bind_args(bind)
    bind.set_defaults(func=command_bind)

    replace = sub.add_parser("replace", help="把角色声音母版更换为另一声音并局部失效")
    _bind_args(replace)
    replace.set_defaults(func=command_replace)

    snapshot = sub.add_parser("snapshot", help="绑定或更换项目角色声音（bind/replace 兼容入口）")
    _bind_args(snapshot)
    snapshot.add_argument("--replace", action="store_true")
    snapshot.set_defaults(func=command_snapshot)

    status = sub.add_parser("status", help="查看项目角色声音绑定与受影响段")
    status.add_argument("--project", required=True)
    status.set_defaults(func=command_status)

    sync_plan = sub.add_parser("sync-plan", help="由 voiceover 行与声音库确定性推导 speaking_characters")
    sync_plan.add_argument("--project", required=True)
    sync_plan.add_argument("--plan", required=True)
    sync_plan.add_argument("--manifest", required=True)
    sync_plan.set_defaults(func=command_sync_plan)

    project_validate = sub.add_parser("validate-project", help="校验项目角色声音绑定")
    project_validate.add_argument("--project", required=True)
    project_validate.set_defaults(func=command_validate_project)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
    except VoiceReferenceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "data": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
