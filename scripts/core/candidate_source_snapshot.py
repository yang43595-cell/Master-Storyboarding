"""Read a pinned Vault snapshot only for explicitly isolated candidate trials."""

from __future__ import annotations

import hashlib
import json
import stat
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

from .knowledge_blocks import KnowledgeBlock, parse_knowledge_blocks


class CandidateSnapshotError(ValueError):
    pass


def inspect_candidate_snapshot(
    profile: dict[str, Any], archive_path: Path, source_id: str,
) -> dict[str, Any]:
    source = _source(profile, source_id)
    archive_path = Path(archive_path)
    raw = archive_path.read_bytes()
    actual_hash = hashlib.sha256(raw).hexdigest()
    if len(raw) != source["archive_bytes"] or actual_hash != source["archive_sha256"]:
        raise CandidateSnapshotError("候选来源快照大小或 SHA-256 与 profile 固定值不符")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            _validate_archive_members(archive)
            summary = json.loads(archive.read("metadata/summary.json"))
            discovery = json.loads(archive.read("metadata/dynamic-discovery.json"))
            if (summary.get("vault_head") != source["vault_head"]
                    or summary.get("consumer_repo_commit_from_acceptance") != source["consumer_repo_commit"]):
                raise CandidateSnapshotError("候选快照 Vault head 或 consumer commit 与 profile 不符")
            available = [block for block in discovery.get("blocks", [])
                         if isinstance(block, dict)
                         and "creative-director" in block.get("stages", [])]
            allow = set(source["allowed_candidate_rule_ids"])
            trial_sources = [block for block in available
                             if block.get("id") in allow
                             and block.get("status") in {"candidate", "enabled"}
                             and block.get("authority") == "wiki"]
            eligible = [block["id"] for block in trial_sources]
            return {
                "source_id": source_id,
                "archive_sha256": actual_hash,
                "vault_head": summary["vault_head"],
                "indexed_creative_blocks": len(available),
                "allowlisted_candidate_ids": sorted(allow),
                "eligible_candidate_ids": sorted(eligible),
                "eligible_source_statuses": {block["id"]: block["status"] for block in trial_sources},
                "excluded_count": len(available) - len(eligible),
                "production_release": False,
            }
    except (OSError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise CandidateSnapshotError(f"候选来源快照不可读取：{exc}") from exc


def load_candidate_snapshot_rules(
    profile: dict[str, Any], archive_path: Path, source_id: str,
    requested_ids: set[str],
) -> list[KnowledgeBlock]:
    if len(requested_ids) > 1:
        raise CandidateSnapshotError("一次隔离试用只允许指定一个候选规则 ID。")
    source = _source(profile, source_id)
    inspection = inspect_candidate_snapshot(profile, archive_path, source_id)
    if not requested_ids:
        return []
    allow = set(source["allowed_candidate_rule_ids"])
    if not requested_ids.issubset(allow):
        raise CandidateSnapshotError("请求的候选规则 ID 不在本 Skill 的显式候选白名单中")
    eligible = set(inspection["eligible_candidate_ids"])
    if not requested_ids.issubset(eligible):
        raise CandidateSnapshotError("来源必须是白名单内的 wiki/candidate 或 wiki/enabled；其他状态不可进入试用入口")

    result: list[KnowledgeBlock] = []
    try:
        with zipfile.ZipFile(archive_path) as archive, tempfile.TemporaryDirectory(prefix="msb-candidate-snapshot-") as temp:
            manifest = {item["path"]: item for item in json.loads(archive.read("metadata/source-manifest.json"))}
            discovery = json.loads(archive.read("metadata/dynamic-discovery.json"))
            wanted = {block["id"]: block for block in discovery["blocks"] if block.get("id") in requested_ids}
            if set(wanted) != requested_ids:
                raise CandidateSnapshotError("快照动态索引未包含请求的候选规则")
            root = Path(temp)
            for block_id, metadata in wanted.items():
                page = metadata.get("path")
                page_record = manifest.get(page)
                if not isinstance(page_record, dict):
                    raise CandidateSnapshotError(f"候选来源页无 manifest 记录：{page}")
                relative_members = [page]
                relative_members.extend("wiki/" + source_ref + ".md" for source_ref in metadata.get("sources", []))
                for relative in relative_members:
                    record = manifest.get(relative)
                    if not isinstance(record, dict):
                        raise CandidateSnapshotError(f"候选引用来源缺少 manifest 记录：{relative}")
                    member = "vault/" + relative
                    payload = archive.read(member)
                    if len(payload) != record["size_bytes"] or hashlib.sha256(payload).hexdigest() != record["sha256"]:
                        raise CandidateSnapshotError(f"候选来源字节与 manifest 不符：{relative}")
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(payload)
                parsed = parse_knowledge_blocks(root / page, root)
                exact = [item for item in parsed if item.id == block_id]
                source_status = metadata.get("status")
                if (len(exact) != 1 or exact[0].status != source_status
                        or source_status not in {"candidate", "enabled"}
                        or exact[0].authority != "wiki"):
                    raise CandidateSnapshotError(f"候选正文与索引状态不符：{block_id}")
                result.append(replace(
                    exact[0], status="candidate", source_status=source_status,
                    source_page_sha256=page_record["sha256"],
                ))
    except CandidateSnapshotError:
        raise
    except (OSError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise CandidateSnapshotError(f"候选来源正文无法校验：{exc}") from exc
    return result


def _source(profile: dict[str, Any], source_id: str) -> dict[str, Any]:
    sources = profile.get("candidate_sources", [])
    if not isinstance(sources, list):
        raise CandidateSnapshotError("profile.candidate_sources 必须是数组")
    matches = [item for item in sources if isinstance(item, dict) and item.get("id") == source_id]
    if len(matches) != 1:
        raise CandidateSnapshotError(f"profile 中没有唯一的候选来源：{source_id}")
    value = matches[0]
    for key in ("archive_sha256", "vault_head", "consumer_repo_commit"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise CandidateSnapshotError(f"候选来源缺少 {key}：{source_id}")
    if not isinstance(value.get("archive_bytes"), int) or value["archive_bytes"] <= 0:
        raise CandidateSnapshotError(f"候选来源 archive_bytes 无效：{source_id}")
    ids = value.get("allowed_candidate_rule_ids")
    if not isinstance(ids, list) or any(not isinstance(item, str) or not item for item in ids) or len(ids) != len(set(ids)):
        raise CandidateSnapshotError(f"候选来源白名单无效：{source_id}")
    return value


def _validate_archive_members(archive: zipfile.ZipFile) -> None:
    seen: set[str] = set()
    for item in archive.infolist():
        name = item.filename
        path = PurePosixPath(name)
        mode = item.external_attr >> 16
        if (name in seen or path.is_absolute() or "\\" in name or ":" in name
                or any(part in {"", ".", ".."} for part in path.parts)
                or stat.S_ISLNK(mode) or (mode and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)))
                or item.flag_bits & 1):
            raise CandidateSnapshotError(f"ZIP 成员路径或类型不安全：{name}")
        if item.file_size and item.compress_size == 0:
            raise CandidateSnapshotError(f"ZIP 压缩大小异常：{name}")
        if item.compress_size and item.file_size / item.compress_size > 200:
            raise CandidateSnapshotError(f"ZIP 压缩比例异常：{name}")
        seen.add(name)
