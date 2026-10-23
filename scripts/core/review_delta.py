"""Small, file-based review handoff; never imports agent conversations."""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path


DELTA_LIMIT = 64 * 1024


def text_change_view(before: str, after: str, old_path: str, new_path: str) -> dict:
    """Choose a lossless edit view when line diff repeats long unchanged prose.

    Offsets address Unicode code points in universal-newline decoded text, not
    byte positions. File fingerprints are still checked separately by the caller.
    Refinement is bounded per changed block; large blocks stay coarse and exact.
    """
    unified = {'diff_format': 'unified', 'diff': '\n'.join(difflib.unified_diff(
        before.splitlines(), after.splitlines(), fromfile=old_path, tofile=new_path, lineterm=''))}
    if before == after:
        return unified
    old_lines, new_lines = before.splitlines(keepends=True), after.splitlines(keepends=True)
    old_offsets, new_offsets = [0], [0]
    for line in old_lines:
        old_offsets.append(old_offsets[-1] + len(line))
    for line in new_lines:
        new_offsets.append(new_offsets[-1] + len(line))
    edits = []
    for tag, a, b, c, d in difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        x, y, p, q = old_offsets[a], old_offsets[b], new_offsets[c], new_offsets[d]
        left, right = before[x:y], after[p:q]
        operations = (difflib.SequenceMatcher(None, left, right).get_opcodes()
                      if len(left) + len(right) <= 16384 else [('replace', 0, len(left), 0, len(right))])
        for change, i, j, k, l in operations:
            if change != 'equal':
                edits.append({'before_start': x+i, 'before_end': x+j,
                              'after_start': p+k, 'after_end': p+l,
                              'before_line': before.count('\n', 0, x+i)+1,
                              'after_line': after.count('\n', 0, p+k)+1,
                              'removed': left[i:j], 'added': right[k:l],
                              'context_before': before[max(0, x+i-48):x+i],
                              'context_after': before[x+j:x+j+48]})
    restored = before
    for edit in reversed(edits):
        a, b = edit['before_start'], edit['before_end']
        if restored[a:b] != edit['removed']:
            raise ValueError('差异还原验证失败；不得发布不完整增量。')
        restored = restored[:a] + edit['added'] + restored[b:]
    if restored != after:
        raise ValueError('差异未能还原当前全文；不得发布不完整增量。')
    compact = {'diff_format': 'unicode-edits-v1', 'text_normalization': 'universal-newlines',
               'text_edits': edits}
    size = lambda value: len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
    # splitlines omits final newline changes; only the edit representation records those.
    newline_changed = before.endswith('\n') != after.endswith('\n')
    return compact if newline_changed or not unified['diff'] or size(compact) < size(unified) else unified


def _section_ranges(lines: list[str]) -> list[dict]:
    """Partition every line, including decisions outside SEG and fenced prompts."""
    headers = []
    fence = None
    for i, line in enumerate(lines):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
        elif marker:
            fence = marker[1]
        elif re.match(r"^##\s+", line):
            headers.append((i, line[3:].strip()))
    if lines and (not headers or headers[0][0] > 0):
        headers.insert(0, (0, "file_preamble"))
    return [{"section": label, "start_line": start + 1,
             "end_line": headers[i+1][0] if i+1 < len(headers) else len(lines)}
            for i, (start, label) in enumerate(headers)]


def section_impact(before: list[str], after: list[str]) -> dict:
    """Structural evidence only: never equate an empty SEG list with no impact."""
    old, new = _section_ranges(before), _section_ranges(after)
    def is_seg(row):
        return bool(re.fullmatch(r"SEG-\d{2,}", row["section"]))
    touched_old, touched_new = set(), set()
    for tag, a, b, c, d in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        for ranges, start, end, selected in ((old, a, b, touched_old), (new, c, d, touched_new)):
            # Empty-side deletion/insertion is not a changed surviving section.
            if start < end:
                selected.update(i for i, row in enumerate(ranges)
                                if row["start_line"] <= end and row["end_line"] > start)
    old_seg, new_seg = [r for r in old if is_seg(r)], [r for r in new if is_seg(r)]
    changed = [r for i, r in enumerate(new) if i in touched_new and is_seg(r)]
    new_ids = {r["section"] for r in new_seg}
    old_ids = {r["section"] for r in old_seg}
    deleted = [r for r in old_seg if r["section"] not in new_ids]
    # A partial deletion can leave no added lines. Map its old section to current.
    touched_ids = {old[i]["section"] for i in touched_old if is_seg(old[i])}
    changed = [r for r in new_seg if r in changed or r["section"] in touched_ids]
    neighbors = []
    for i, row in enumerate(old_seg):
        if row in deleted:
            for direction in (-1, 1):
                j = i + direction
                while 0 <= j < len(old_seg):
                    matches = [r for r in new_seg if r["section"] == old_seg[j]["section"]]
                    if matches:
                        neighbors.extend(r for r in matches if r not in neighbors)
                        break
                    j += direction
    for row in changed:
        i = new_seg.index(row)
        row_neighbors = [new_seg[j] for j in (i-1, i+1) if 0 <= j < len(new_seg)]
        neighbors.extend(r for r in row_neighbors if r not in neighbors)
    file_changes = {"before": [old[i] for i in sorted(touched_old) if not is_seg(old[i])],
                    "after": [new[i] for i in sorted(touched_new) if not is_seg(new[i])]}
    global_change = bool(file_changes["before"] or file_changes["after"])
    boundary = bool(before != after and (global_change or not old_seg or not new_seg
                    or any(r in deleted or r["section"] in touched_ids for r in old_seg[:1] + old_seg[-1:])
                    or any(r in changed for r in new_seg[:1] + new_seg[-1:])))
    return {"changed_sections": [{**r, "continuity_neighbors": [new_seg[j] for j in (new_seg.index(r)-1, new_seg.index(r)+1)
                              if 0 <= j < len(new_seg)]} for r in changed],
            "deleted_sections": deleted, "file_level_changes": file_changes,
            "continuity_neighbors": neighbors,
            "reordered_sections": [r["section"] for r in new_seg if r["section"] in old_ids]
                if [r["section"] for r in old_seg if r["section"] in new_ids] != [r["section"] for r in new_seg if r["section"] in old_ids] else [],
            "file_level_impact_review_required": global_change,
            "cross_file_context_review_required": boundary}


def fingerprint(path: Path) -> dict:
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def evidence_delta(scope: dict, paths: list[Path] | None) -> dict:
    """Report byte/path identity changes, never infer semantic review scope."""
    before = scope.get("direct_evidence", [])
    if not isinstance(before, list) or any(
        not isinstance(item, dict) or not all(isinstance(item.get(key), str) and item[key]
                                               for key in ("path", "sha256"))
        for item in before
    ) or len({item["path"] for item in before}) != len(before):
        raise ValueError("上轮直接证据身份无效；不得猜测旧版本。")
    # None is backward-compatible for direct library callers; the CLI always
    # passes its explicit current evidence list, including an empty withdrawal.
    after = before if paths is None else [fingerprint(path) for path in dict.fromkeys(paths)]
    old = {item["path"]: item for item in before}
    new = {item["path"]: item for item in after}
    changes = {
        "added": [new[path] for path in sorted(new.keys() - old.keys())],
        "removed": [old[path] for path in sorted(old.keys() - new.keys())],
        "changed": [{"path": path, "before_sha256": old[path]["sha256"],
                     "after_sha256": new[path]["sha256"]}
                    for path in sorted(old.keys() & new.keys())
                    if old[path]["sha256"] != new[path]["sha256"]],
    }
    return {"previous_direct_evidence": before, "current_direct_evidence": after,
            "previous_evidence_identity": "recorded" if "direct_evidence" in scope else "legacy_unknown",
            "direct_evidence_changes": changes,
            "evidence_impact": "requires_reviewer_judgment"}


def revision_delta(previous: Path, baselines: list[Path], artifacts: list[Path],
                   producer_response: Path | dict, skill: str, required: list[dict], manual: list[dict],
                   evidence: list[Path] | None = None) -> tuple[dict, list[dict]]:
    """Filter old findings and calculate the actual diff, checking baseline identity."""
    review_bytes = previous.read_bytes()
    review = json.loads(review_bytes.decode('utf-8'))
    scope = review["scope"]
    if scope["skill"] != skill:
        raise ValueError("复审验收标准与上轮不同；暂停并明确标准变更，不得暗中扩大复审。")
    if scope.get("review_kind") not in {"production", "skill_contract"}:
        raise ValueError("上轮 review.json 缺少审核类型；旧格式不能静默用于复审。")
    if not isinstance(scope.get("task_basis_path"), str) or not isinstance(scope.get("task_basis_sha256"), str):
        raise ValueError("上轮 review.json 缺少任务依据指纹；旧格式不能静默用于复审。")
    frozen_manual = manual
    reviewer = review.get("reviewer_id")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("上轮 review.json 缺少真实 reviewer_id；不得猜测或自动替换。")
    lineage = review.get("reviewer_lineage", [reviewer])
    if (not isinstance(lineage, list) or not lineage or len(lineage) > 32
            or any(not isinstance(v, str) or not v or len(v) > 160 or "\n" in v for v in lineage)
            or lineage[-1] != reviewer):
        raise ValueError("reviewer_lineage 只能保留按序的真实 agent ID，末项须为当前 reviewer。")
    streak = review.get("wording_only_streak", 0)
    high = review.get("issue_id_high_watermark", 0)
    inherited_high = scope.get("issue_id_high_watermark", 0)
    if any(type(v) is not int or v < 0 for v in (streak, high, inherited_high)):
        raise ValueError("问题编号上限和措辞轮次必须为非负整数。")
    incoming = set(scope.get("input_issue_ids", []))
    old_artifacts = scope["artifacts"]
    if not baselines or len(baselines) != len(artifacts) or len(baselines) != len(old_artifacts):
        raise ValueError("--baseline 必须按上轮 scope.artifacts 顺序与当前 --artifact 一一对应。")
    response = json.loads(producer_response.read_text(encoding="utf-8")) if isinstance(producer_response, Path) else producer_response
    changes = response.get("responses") if isinstance(response, dict) else None
    if not isinstance(changes, list):
        raise ValueError("producer-response.json 必须含逐问题 ID 的 responses 数组。")
    issues, ids = [], set()
    check_ids = {c["id"] for c in [*required, *frozen_manual]}
    for item in review["issues"]:
        issue_id = item["id"]
        if not isinstance(issue_id, str) or not re.fullmatch(r"B-\d{3,}", issue_id) or issue_id in ids:
            raise ValueError("问题必须使用唯一稳定 ID，例如 B-012。")
        ids.add(issue_id)
        if issue_id not in incoming and int(issue_id[2:]) <= inherited_high:
            raise ValueError(f"不得复用已结束问题 ID：{issue_id}")
        if item["status"] not in {"resolved", "unresolved", "new-blocker", "suggestion"}:
            raise ValueError(f"未知问题状态：{issue_id}")
        if item["status"] in {"resolved", "suggestion"}:
            continue
        if item["check_id"] not in check_ids:
            raise ValueError(f"问题引用了未冻结的检查项：{issue_id}")
        fields = ("id", "check_id", "status", "target", "evidence", "impact")
        if any(not isinstance(item.get(k), str) or not item[k].strip() for k in fields):
            raise ValueError(f"问题缺少可核验对象、证据或影响：{issue_id}")
        issues.append({k: item[k] for k in fields})
    if not incoming.issubset(ids):
        raise ValueError("review.json 漏答本轮输入的未决 ID，不能将遗漏当作已解决。")
    pending = {i["id"] for i in issues}
    filtered_changes, change_ids = [], set()
    for item in changes:
        issue_id = item["id"]
        if issue_id in change_ids or issue_id not in pending:
            raise ValueError("生产者回复只能逐条对应上轮未决 ID，不能重复或附已解决历史。")
        change_ids.add(issue_id)
        if any(not isinstance(item.get(k), str) or not item[k].strip() for k in ("summary", "evidence")):
            raise ValueError(f"生产者回复缺少修改说明或证据位置：{issue_id}")
        filtered_changes.append({k: item[k] for k in ("id", "summary", "evidence")})
    if change_ids != pending:
        raise ValueError("生产者回复必须回应全部上轮未决 ID；未修复也须说明原因和证据。")
    diffs = []
    for baseline, current, old in zip(baselines, artifacts, old_artifacts):
        if baseline.stat().st_size + current.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("差异输入超过 8 MiB；请明确缩小审核对象，禁止静默截断。")
        # Hash and parse the very same bounded bytes, not separate file reads.
        with baseline.open('rb') as stream:
            before_bytes = stream.read(8 * 1024 * 1024 + 1)
        with current.open('rb') as stream:
            after_bytes = stream.read(8 * 1024 * 1024 + 1)
        if len(before_bytes) + len(after_bytes) > 8 * 1024 * 1024:
            raise ValueError('差异输入超过 8 MiB；请明确缩小审核对象，禁止静默截断。')
        old_hash = hashlib.sha256(before_bytes).hexdigest()
        new_hash = hashlib.sha256(after_bytes).hexdigest()
        if old_hash != old["sha256"]:
            raise ValueError(f"diff 基线与上轮被审版本不符：{baseline}")
        try:
            before_text = before_bytes.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
            after_text = after_bytes.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
            before, after = before_text.splitlines(), after_text.splitlines()
        except UnicodeError as exc:
            raise ValueError("增量文本 diff 不支持二进制候选；需明确的视觉/媒体对比方案，不得用哈希冒充内容复审。") from exc
        diffs.append({"baseline": str(baseline), "current": str(current), "before_sha256": old_hash,
                      "after_sha256": new_hash, **text_change_view(before_text, after_text, old['path'], str(current)),
                      **section_impact(before, after)})
    issue_updates = [{**issue, "producer_response": next(change for change in filtered_changes if change["id"] == issue["id"])} for issue in issues]
    delta = {
        "previous_review_sha256": hashlib.sha256(review_bytes).hexdigest(),
        "task_basis_path": scope["task_basis_path"],
        "task_basis_sha256": scope["task_basis_sha256"],
        **({"task_basis_identity": scope["task_basis_identity"]} if "task_basis_identity" in scope else {}),
        "review_kind": scope["review_kind"],
        "reviewer_id": reviewer,
        "reviewer_lineage": lineage,
        "wording_only_streak": streak,
        "issue_id_high_watermark": max(high, inherited_high, max((int(i[2:]) for i in ids), default=0)),
        "issue_updates": issue_updates, "actual_diff": diffs,
        **evidence_delta(scope, evidence),
    }
    if len((json.dumps(delta, ensure_ascii=False, indent=2) + "\n").encode("utf-8")) > DELTA_LIMIT:
        raise ValueError("复审增量超过 64 KiB；明确缩小本轮范围，禁止截断或附全部历史。")
    return delta, frozen_manual


def lightweight_revision(previous: Path, prior_scope: dict, artifacts: list[Path],
                         skill: str, review_kind: str, required: list[dict],
                         manual: list[dict], changes: list[str],
                         evidence: list[Path] | None = None) -> dict:
    """Carry findings and declared edits; do not invent a diff without old bytes.

    The single current scope is a version guard, not an archive or a reviewer
    verdict. A prior report can embed that scope, but four-field findings do not
    have to duplicate it. The original reviewer owns the semantic recheck.
    """
    if previous.stat().st_size > DELTA_LIMIT:
        raise ValueError("上轮审核超过 64 KiB；只提供本轮关联发现，禁止传全部历史。")
    raw = previous.read_bytes()
    review = json.loads(raw.decode("utf-8"))
    if not isinstance(review, dict) or not isinstance(review.get("issues"), list):
        raise ValueError("轻复审需要上轮 issues 数组：check_id、target、evidence、impact。")
    embedded_scope = review.get("scope")
    # Only the current packet owns outstanding obligations and candidate version.
    # An older embedded scope is evidence to compare, never a replacement state.
    scope = prior_scope
    if (any(key in review for key in ("reviewer_lineage", "issue_id_high_watermark", "wording_only_streak"))
            or any(isinstance(item, dict) and "id" in item for item in review["issues"])
            or any(isinstance(value, dict) and any(key in value for key in ("input_issue_ids", "issue_id_high_watermark"))
                   for value in (scope, embedded_scope))):
        raise ValueError("上轮为正式留档结构；须提供 --baseline 与 --producer-response，不得降级丢弃未决 ID。")
    if not isinstance(scope, dict) or scope.get("skill") != skill or scope.get("review_kind") != review_kind:
        raise ValueError("复审缺少同一 Skill 和审核类型的上轮范围；不得猜测或扩大范围。")
    if not isinstance(scope.get("task_basis_identity"), str):
        raise ValueError("上轮缺少实质任务身份；旧正式记录须沿用正式留档输入，不能降级丢弃保护。")
    if "scope" in review:
        if not isinstance(embedded_scope, dict):
            raise ValueError("嵌入审核范围必须是当前 scope 对象。")
        fields = ("skill", "review_kind", "task_basis_path", "task_basis_identity", "artifacts", "direct_evidence")
        if (any(embedded_scope.get(key) != scope.get(key) for key in fields)
                or embedded_scope.get("input_findings", []) != scope.get("input_findings", [])):
            raise ValueError("嵌入审核范围已过期；必须回应当前候选版本与当前未决发现，不得重放旧轮次。")
    old_artifacts = scope.get("artifacts")
    if not isinstance(old_artifacts, list) or len(old_artifacts) != len(artifacts):
        raise ValueError("轻复审候选范围已改变；明确范围后重新首审。")
    current = [fingerprint(path) for path in artifacts]
    if any(old.get("path") != new["path"] for old, new in zip(old_artifacts, current)):
        raise ValueError("轻复审候选路径已改变；新路径须通过正式基线对比或重新首审。")
    changed = [item for old, item in zip(old_artifacts, current) if old.get("sha256") != item["sha256"]]
    if changed and not changes:
        raise ValueError("候选字节已改变；轻复审须用 --change-summary 写明改动位置、实际修改及直接影响。")
    if any(not isinstance(item, str) or not item.strip() for item in changes):
        raise ValueError("--change-summary 不得为空。")
    checks = {item["id"] for item in [*required, *manual]}
    findings = []
    pending = scope.get("input_findings", [])
    if not isinstance(pending, list) or any(not isinstance(item, dict) for item in pending):
        raise ValueError("当前范围的未决发现无效；不得用报告替换当前义务。")
    incoming = set()
    for item in pending:
        if any(not isinstance(item.get(key), str) or not item[key].strip() for key in ("check_id", "target", "evidence", "impact")):
            raise ValueError("当前范围的未决发现不完整。")
        key = (item["check_id"], item["target"])
        if item["check_id"] not in checks or key in incoming:
            raise ValueError("当前范围的未决发现重复或超出冻结清单。")
        incoming.add(key)
    answered = set()
    for item in review["issues"]:
        if not isinstance(item, dict):
            raise ValueError("每条审核发现必须是对象。")
        if any(not isinstance(item.get(key), str) or not item[key].strip() for key in ("check_id", "target")):
            raise ValueError("审核发现或关闭记录缺少 check_id、准确对象。")
        key = (item["check_id"], item["target"])
        if item["check_id"] not in checks:
            raise ValueError("审核发现或关闭记录引用了未冻结的检查项。")
        if key in answered:
            raise ValueError("同一 check_id、target 重复；对象须精确到不同问题位置，不得互相覆盖。")
        answered.add(key)
        status = item.get("status", "unresolved")
        if status not in {"resolved", "unresolved", "new-blocker", "suggestion"}:
            raise ValueError("未知问题状态；不能默认为已解决。")
        if any(not isinstance(item.get(field), str) or not item[field].strip() for field in ("evidence", "impact")):
            raise ValueError("审核发现或关闭记录缺少可复现证据和实际影响。")
        if status == "resolved":
            if key not in incoming:
                raise ValueError("关闭记录不属于当前未决范围；不得用旧解决项或其他对象关闭本轮问题。")
            continue
        if status == "suggestion" and key in incoming:
            raise ValueError("当前未决发现须明确解决或继续复核，不能用 suggestion 消除义务。")
        fields = ("check_id", "target", "evidence", "impact")
        if any(not isinstance(item.get(key), str) or not item[key].strip() for key in fields):
            raise ValueError("审核发现缺少 check_id、对象、证据或影响。")
        if status != "suggestion":
            findings.append({key: item[key] for key in fields})
    if not incoming.issubset(answered):
        raise ValueError("上轮报告漏答输入的未决发现；已解决也须列原 check_id、target 并标记 resolved。")
    result = {"task_basis_identity": scope["task_basis_identity"], "review_kind": review_kind,
              "previous_review_path": str(previous), "previous_review_sha256": hashlib.sha256(raw).hexdigest(),
              "unresolved_findings": findings, "changed_artifacts": changed, "current_artifacts": current,
              "declared_changes": changes, "change_evidence": "author_declared_not_computed_diff",
              **evidence_delta(scope, evidence)}
    if review.get("reviewer_id"):
        result["reviewer_id"] = review["reviewer_id"]
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > DELTA_LIMIT:
        raise ValueError("轻复审增量超过 64 KiB；明确缩小范围，禁止截断。")
    return result
