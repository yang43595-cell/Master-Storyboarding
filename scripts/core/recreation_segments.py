"""Segment-scoped inputs for ordinary depth-guided prompt drafting."""

from pathlib import Path

from .artifacts import digest, project_path
from .io import read_json, sha256_json
from .director_inputs import job_path, return_binding
from .asset_refs import MASTER_DIR, confirmed_images
from .media_types import MEDIA_EXTENSIONS
from .product_reference_pack import resolve_product_pack
from .project_kickoff import load_kickoff

PLAN = Path("01_原始资料/复刻分段.json")
PROMPTS = Path("06_视频/复刻视频提示词.json")
CANVAS_RECEIPT = "06_视频/画布交付回执.json"


def checked_file(root, item, hashes):
    path = project_path(root, item["path"])
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"缺本项目参考 {item['path']}")
    if path not in hashes:
        hashes[path] = digest(path)
    if hashes[path] != item["sha256"]:
        raise ValueError(f"参考已换版 {item['path']}")


def product_inputs(root, product, plan, segment, hashes):
    if not product or product["mode"] == "none":
        raise ValueError("请用户先确认本段使用的准确商品")
    bundle = plan.get("product_pack") or {}
    if bundle.get("product_id") != product["product_id"]:
        raise ValueError("本段缺与用户所选商品一致的产品包")

    refs = plan["references"]
    packaging = bundle.get("packaging")
    if (not isinstance(packaging, list) or not packaging
            or any(not isinstance(key, str) or key not in refs for key in packaging)
            or len(set(packaging)) != len(packaging)):
        raise ValueError("产品包须登记用于画布交付的实物包装参考")
    action = segment["motion"]
    real_use = segment["mode"] == "product"
    operations = bundle.get("operations", [])
    if real_use and (product["product_usage"] != "real_use" or not isinstance(operations, list)
                     or action not in operations):
        raise ValueError("本段动作参考须为该产品包登记的同款真实操作视频")
    if not real_use and not set(packaging).intersection(segment["appearance"]):
        raise ValueError("商品展示段须引用所选产品包的包装外观")

    installed = resolve_product_pack(product)
    if installed:
        allowed = {(row["role"], row["sha256"]) for row in installed["references"]}
        if any(("packaging", refs[key]["sha256"]) not in allowed for key in packaging):
            raise ValueError("包装参考与开工锁定的产品包不符")
        if real_use and not any((role, refs[action]["sha256"]) in allowed for role in ("action", "technique")):
            raise ValueError("实操参考与开工锁定的产品包不符")
    else:
        evidence = bundle.get("source")
        if not isinstance(evidence, dict) or not evidence.get("path") or not evidence.get("sha256"):
            raise ValueError("项目专用实物包须登记可核的来源文件和 SHA-256")
        checked_file(root, evidence, hashes)

    for key in packaging:
        if Path(refs[key]["path"]).suffix.lower() not in (MEDIA_EXTENSIONS["image"] | MEDIA_EXTENSIONS["video"]):
            raise ValueError(f"产品包包装参考须为图像或视频：{key}")
        checked_file(root, refs[key], hashes)
    return packaging


def product_canvas_step(root, plan, segment, packing, canvas, entry):
    """Keep written prompts separate from product media actually read back on canvas."""
    result = dict(action="product-canvas", status="ready", skill="voiceover-to-video-script",
                  from_skill="depth-guided-recreation", segment_ids=[segment["id"]],
                  artifact_ids=[entry["artifact_id"]],
                  reason="把已核的产品包装放入素材区，绑定本段实操和所用实物参考并刷新回读")
    if not canvas or not canvas.get("target_url"):
        return {**result, "status": "waiting", "reason": "请用户选择视频平台和画布，再交付产品包"}
    bundle = plan["product_pack"]
    path = project_path(root, bundle.get("canvas_receipt", CANVAS_RECEIPT))
    if not path.is_relative_to(root) or not path.is_file():
        return result
    try:
        receipt = read_json(path)
    except (OSError, ValueError):
        return result
    if not isinstance(receipt, dict):
        return result
    observed_pack = receipt.get("product_pack", {})
    uploaded = observed_pack.get("packaging", {})
    segment_receipt = receipt.get("segments", {}).get(segment["id"], {})
    inputs = segment_receipt.get("verified_product_inputs", {})
    expected_inputs = set(segment["appearance"]) & set(packing)
    if segment["mode"] == "product":
        expected_inputs.add(segment["motion"])
    identities = segment_receipt.get("reference_identities", {})
    if (receipt.get("target_url") == canvas["target_url"]
            and receipt.get("target_project_id") == canvas.get("target_project_id")
            and receipt.get("phase") == "after_refresh"
            and receipt.get("plan_sha256") == digest(root / PLAN)
            and observed_pack.get("product_id") == bundle["product_id"]
            and all(uploaded.get(key, {}).get("node_id")
                    and uploaded[key].get("sha256") == plan["references"][key]["sha256"] for key in packing)
            and all(key in identities and inputs.get(key) == plan["references"][key]["sha256"]
                    for key in expected_inputs)):
        return {**result, "status": "complete", "reason": "产品包素材卡和本段实际输入已刷新回读"}
    return result


def written_prompts(root, entry):
    if not (root / PROMPTS).is_file():
        return {}
    draft = read_json(root / PROMPTS)
    if draft.get("narrative_id") != entry["artifact_id"]:
        return {}
    same_plan = draft.get("plan_sha256") == digest(root / PLAN)
    prompts = {}
    for row in draft["segments"]:
        if row["id"] in prompts:
            raise ValueError("提示词段号重复")
        if same_plan or row.get("segment_sha256"):
            prompts[row["id"]] = (row["prompt"], None if same_plan else row["segment_sha256"])
    return prompts


def group_steps(steps):
    grouped = {}
    for step in steps:
        key = (step["action"], step["status"])
        if key not in grouped:
            grouped[key] = step.copy()
            grouped[key]["segment_ids"] = []
        grouped[key]["segment_ids"].extend(step["segment_ids"])
        if step["reason"] not in grouped[key]["reason"]:
            grouped[key]["reason"] += "；" + step["reason"]
    return list(grouped.values())


def segment_steps(root, narrative, entry, *, plan=None, kickoff=None):
    if not (root / PLAN).exists():
        return [
            dict(
                action="recreation",
                status="ready",
                skill="depth-guided-recreation",
                reason=f"按采用稿定段并登记参考到 {PLAN}",
                artifact_ids=[entry["artifact_id"]],
            )
        ]
    proposed = plan is not None
    if not proposed:
        plan = read_json(root / PLAN)
    if (plan["narrative_id"], plan["narrative_sha256"]) != (entry["artifact_id"], entry["checksum"]):
        raise ValueError("复刻分段与当前编导稿不符")
    dialogue = {r["dialogue_id"]: r for r in narrative["dialogues"]}
    paragraphs = {r["paragraph_id"] for r in narrative["story_paragraphs"]}
    refs = plan["references"]
    kickoff = kickoff or load_kickoff(root) or {}
    product = kickoff.get("product")
    product_paragraphs = {p for a in narrative.get("asset_requirements", []) if a.get("category") == "REAL_PRODUCT"
                          for p in a.get("source_paragraph_ids", [])}
    cursor = plan["first_frame"]
    fps = plan["fps"]
    if not isinstance(fps, (int, float)) or not 0 < fps <= 240:
        raise ValueError("原片帧率无效")
    ids, lines, hashes, steps, deliveries = set(), [], {}, [], []
    written = {} if proposed else written_prompts(root, entry)
    masters = None
    for row in plan["segments"]:
        sid = row["id"]
        start, end = row["frames"]
        if (
            type(start) is not int
            or type(end) is not int
            or sid in ids
            or not sid
            or cursor != start
            or not 0 < end - start <= 30 * fps
        ):
            raise ValueError("复刻段重复、不连续或超过30秒")
        if not row["paragraph_ids"] or set(row["paragraph_ids"]) - paragraphs:
            raise ValueError(f"{sid} 缺准确编导段")
        ids.add(sid)
        cursor = end
        for line in row["dialogue_ids"]:
            if dialogue[line]["source_paragraph_id"] not in row["paragraph_ids"]:
                raise ValueError(f"{sid} 对白与编导段不符")
            lines.append(line)
        step = dict(
            action="video",
            skill="voiceover-to-video-script",
            from_skill="depth-guided-recreation",
            status="ready",
            reason=f"按 {PLAN} 的 {sid} 直接写词",
            segment_ids=[sid],
            artifact_ids=[entry["artifact_id"]],
        )
        if row["mode"] == "rewrite":
            if not row.get("change"):
                raise ValueError("局部改戏须写清戏剧冲突")
            step.update(action="director", skill="storyboard-planner", reason=row["change"])
        else:
            try:
                packaging = None
                if row["mode"] == "product" or (product and product.get("product_usage") == "display"
                        and product_paragraphs.intersection(row["paragraph_ids"])):
                    packaging = product_inputs(root, product, plan, row, hashes)
                ticket = job_path(root, sid)
                saved_job = read_json(ticket) if ticket.is_file() else None
                current_job = (
                    saved_job
                    and saved_job["input"]["source_narrative"]["artifact_id"] == entry["artifact_id"]
                    and saved_job["input"]["source_narrative"]["checksum"] == entry["checksum"]
                )
                if current_job or row.get("change"):
                    if not ticket.is_file():
                        raise ValueError("改戏须先按 rewrite 派单")
                    job = saved_job
                    if (
                        job["input"]["source_narrative"]["checksum"] != entry["checksum"]
                        or job["input"]["source_narrative"]["artifact_id"] != entry["artifact_id"]
                        or not row.get("director_return")
                        or row["director_return"] != return_binding(row, refs, job["job_sha256"])
                    ):
                        raise ValueError("局部导演改法缺失或已变化，请按原段回传")
                if row["mode"] not in ("depth", "product") or refs[row["motion"]]["role"] != row["mode"]:
                    raise ValueError("主参考职责与段落不符")
                window = row["reference_frames"]
                if len(window) != 2 or not all(type(frame) is int for frame in window):
                    raise ValueError("参考帧界无效")
                if not 0 <= window[0] < window[1] <= refs[row["motion"]]["frame_count"]:
                    raise ValueError("动作参考窗越界")
                if row["mode"] == "depth" and window[1] - window[0] != end - start:
                    raise ValueError("深度窗与剧情窗帧数不同")
                motion = refs[row["motion"]]
                if Path(motion["path"]).suffix.lower() not in {".mp4", ".mov", ".webm", ".mkv", ".m4v"}:
                    raise ValueError("动作参考须为视频")
                if (
                    row["mode"] == "depth"
                    and "source_start_frame" in motion
                    and motion["source_start_frame"] + window[0] != start
                ):
                    raise ValueError("深度参考窗与原片起点不符")
                for key in [row["motion"], *row["appearance"]]:
                    ref = refs[key]
                    checked_file(root, ref, hashes)
                    path = project_path(root, ref["path"])
                    if path.is_relative_to(root / MASTER_DIR) or (
                        key in row["appearance"] and not path.is_relative_to(root / "01_原始资料")
                    ):
                        if masters is None:
                            masters = {
                                Path(o["path"])
                                for a in confirmed_images(root).values()
                                if a["status"] == "confirmed"
                                for o in a["outputs"]
                            }
                        if path not in masters:
                            raise ValueError(f"资产确认已失效 {key}")
                if sid in written:
                    prompt, previous_basis = written[sid]
                    basis = sha256_json(
                        [entry["checksum"], fps, row, [refs[k] for k in [row["motion"], *row["appearance"]]]]
                    )
                    if previous_basis is not None and previous_basis != basis:
                        steps.append(step)
                        continue
                    if (
                        not isinstance(prompt, str)
                        or not prompt.strip()
                        or any("@" + k not in prompt for k in [row["motion"], *row["appearance"]])
                        or any(dialogue[k]["text"] not in prompt for k in row["dialogue_ids"])
                    ):
                        step["reason"] = f"{sid} 提示词须补齐参考或原对白"
                    else:
                        step.update(status="complete", reason="分段提示词已交稿")
                        if packaging:
                            deliveries.append(product_canvas_step(root, plan, row, packaging, kickoff.get("canvas"), entry))
            except (OSError, KeyError, ValueError) as exc:
                step.update(status="waiting", reason=str(exc))
        steps.append(step)
    if not ids or cursor != plan["end_frame"] or sorted(lines) != sorted(dialogue):
        raise ValueError("复刻段未覆盖选定全片或对白遗漏、重复")
    if any(basis is None and sid not in ids for sid, (_, basis) in written.items()):
        raise ValueError("提示词含无来源段号")
    return group_steps(steps + deliveries)
