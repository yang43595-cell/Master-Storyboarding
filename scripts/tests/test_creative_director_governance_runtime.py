"""Repeatable synthetic checks for creative-director knowledge governance."""
import argparse
import hashlib
import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts import stage_context_cli as cli
from scripts.core import candidate_test
from scripts.core.candidate_source_snapshot import (
    CandidateSnapshotError,
    inspect_candidate_snapshot,
    load_candidate_snapshot_rules,
)
from scripts.core.knowledge_blocks import KnowledgeBlock
from scripts.core.knowledge_selector import select_knowledge
from scripts.core.method_documents import MethodDocumentError, _validate_promotion_record
from scripts.core.project_state import new_project_state, write_project_state


LEGACY = (
    "CREATIVE-DIRECTION-METHOD",
    "projects/Master-Storyboarding/应用知识/编导/编导方法.md",
    "2026-10-23",
    "9dbaa9bda8fdc4f331b1de528d53f0e956570798c86a3da5236eed43a9a47114",
    "兼容现有 creative-director 编导方法绑定；历史晋升依据不重新分类。",
    "libfile_d4bb8cef26508191a0aa202b4ff481c7 v1；精确字节基准。",
)


def synthetic_snapshot(path: Path, *, unsafe_member: bool = False, status: str = "candidate") -> dict:
    rule_id = "MSB-KB-SYNTH-CANDIDATE"
    page = "wiki/concepts/synthetic.md"
    block_body = "只用于合成测试的候选方法。"
    metadata = {
        "id": rule_id, "canonical_key": "synthetic.candidate", "kind": "rule",
        "status": status, "authority": "wiki",
        "projects": ["Master-Storyboarding"], "stages": ["creative-director"],
        "topics": ["narrative"], "triggers": [], "conflicts_with": [],
        "priority": 1, "sources": [], "applies_when": "仅合成测试",
    }
    page_bytes = (
        "<!-- msb-knowledge\n" + json.dumps(metadata, ensure_ascii=False)
        + "\n-->\n" + block_body + "\n^" + rule_id.lower()
        + "\n<!-- /msb-knowledge -->\n"
    ).encode()
    summary = {"vault_head": "synthetic-vault-head", "consumer_repo_commit_from_acceptance": "synthetic-commit"}
    discovery = {"blocks": [{"id": rule_id, "path": page, "sources": [], "status": status,
                             "authority": "wiki", "stages": ["creative-director"]}]}
    manifest = [{"path": page, "size_bytes": len(page_bytes), "sha256": hashlib.sha256(page_bytes).hexdigest()}]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        def write_member(name, payload):
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, payload)
        write_member("metadata/summary.json", json.dumps(summary))
        write_member("metadata/dynamic-discovery.json", json.dumps(discovery))
        write_member("metadata/source-manifest.json", json.dumps(manifest))
        write_member("vault/" + page, page_bytes)
        if unsafe_member:
            write_member("../escape.txt", "unsafe")
    raw = path.read_bytes()
    return {
        "skill": "creative-director",
        "candidate_sources": [{
            "id": "SYNTHETIC", "archive_sha256": hashlib.sha256(raw).hexdigest(),
            "archive_bytes": len(raw), "vault_head": summary["vault_head"],
            "consumer_repo_commit": summary["consumer_repo_commit_from_acceptance"],
            "allowed_candidate_rule_ids": [rule_id],
        }],
    }


class GovernanceRuntimeTests(unittest.TestCase):
    def test_candidate_test_prepare_ensure_and_formal_adoption_reject(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "isolated-project"
            project.mkdir()
            write_project_state(project, new_project_state(current_stage="creative-director"))
            candidate_test.configure(project, "synthetic-run", "synthetic explicit authorization", [])
            args = argparse.Namespace(
                project=str(project), skill="creative-director", objective="synthetic lifecycle",
                request_json=None, vault=str(Path(tmp) / "empty-vault"), actor_id="test-agent",
                task_id="test-task", candidate_test=True,
            )
            prepared = cli.prepare(args)
            self.assertEqual(prepared["execution_mode"], "candidate_test")
            checked = cli.ensure_prepared(args)
            self.assertTrue(checked["candidate_checks_passed"])
            self.assertTrue(checked["production_blocked"])
            with self.assertRaisesRegex(ValueError, "禁止正式采用"):
                cli.adopt_package(argparse.Namespace(project=str(project)))

    def test_candidate_snapshot_index_and_exact_rule_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "source.zip"
            profile = synthetic_snapshot(archive)
            index = inspect_candidate_snapshot(profile, archive, "SYNTHETIC")
            self.assertEqual(index["eligible_candidate_ids"], ["MSB-KB-SYNTH-CANDIDATE"])
            self.assertFalse(index["production_release"])
            loaded = load_candidate_snapshot_rules(profile, archive, "SYNTHETIC", {"MSB-KB-SYNTH-CANDIDATE"})
            self.assertEqual([block.id for block in loaded], ["MSB-KB-SYNTH-CANDIDATE"])
            with self.assertRaisesRegex(CandidateSnapshotError, "白名单"):
                load_candidate_snapshot_rules(profile, archive, "SYNTHETIC", {"MSB-KB-MISSING"})

    def test_enabled_source_is_wrapped_as_candidate_only_for_trial(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "source.zip"
            profile = synthetic_snapshot(archive, status="enabled")
            index = inspect_candidate_snapshot(profile, archive, "SYNTHETIC")
            self.assertEqual(index["eligible_source_statuses"], {"MSB-KB-SYNTH-CANDIDATE": "enabled"})
            trial = load_candidate_snapshot_rules(profile, archive, "SYNTHETIC", {"MSB-KB-SYNTH-CANDIDATE"})
            self.assertEqual(trial[0].status, "candidate")
            self.assertEqual(trial[0].source_status, "enabled")

    def test_candidate_snapshot_rejects_hash_drift_and_unsafe_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "source.zip"
            profile = synthetic_snapshot(archive)
            archive.write_bytes(archive.read_bytes() + b"drift")
            with self.assertRaisesRegex(CandidateSnapshotError, "SHA-256"):
                inspect_candidate_snapshot(profile, archive, "SYNTHETIC")
            unsafe = Path(tmp) / "unsafe.zip"
            unsafe_profile = synthetic_snapshot(unsafe, unsafe_member=True)
            with self.assertRaisesRegex(CandidateSnapshotError, "不安全"):
                inspect_candidate_snapshot(unsafe_profile, unsafe, "SYNTHETIC")

    def test_legacy_exception_is_one_exact_approved_baseline(self):
        identifier, relative, version, checksum, scope, source_ref = LEGACY
        record = {"basis": "legacy_unclassified", "method_version": version,
                  "method_sha256": checksum, "scope": scope, "source_ref": source_ref}
        self.assertEqual(_validate_promotion_record(Path("."), record, identifier, version, checksum, relative),
                         "legacy_unclassified")
        with self.assertRaisesRegex(MethodDocumentError, "仅兼容代码固定"):
            _validate_promotion_record(Path("."), {**record, "scope": "fabricated"}, identifier, version, checksum, relative)
        with self.assertRaisesRegex(MethodDocumentError, "仅兼容代码固定"):
            _validate_promotion_record(Path("."), record, "NEW-METHOD", version, checksum, relative)

    def test_directed_and_experiment_records_bind_structure_and_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            method_hash = hashlib.sha256(b"synthetic method").hexdigest()
            base = {"method_version": "v1", "method_sha256": method_hash,
                    "scope": "creative-director", "source_ref": "provided-ref",
                    "conflict_resolution": "none", "rollback": "restore prior version", "supersedes": []}
            self.assertEqual(_validate_promotion_record(vault, {**base, "basis": "user_directed",
                "user_instruction_ref": "provided-user-reference"}, "M1", "v1", method_hash, "method.md"), "user_directed")
            evidence = {"schema_version": "1.0", "kind": "method-promotion-evidence", "method_id": "M1",
                        "method_version": "v1", "method_sha256": method_hash, "decision": "adopt",
                        "scope": "creative-director", "evaluation": {"criteria": ["declared"],
                        "observations": ["fabricated input is structurally accepted"], "limitations": ["not authenticated"]}}
            raw = json.dumps(evidence).encode()
            (vault / "evidence.json").write_bytes(raw)
            record = {**base, "basis": "experiment_validated", "evidence_path": "evidence.json",
                      "evidence_sha256": hashlib.sha256(raw).hexdigest()}
            self.assertEqual(_validate_promotion_record(vault, record, "M1", "v1", method_hash, "method.md"),
                             "experiment_validated")
            (vault / "evidence.json").write_bytes(raw + b"drift")
            with self.assertRaisesRegex(MethodDocumentError, "hash 不匹配"):
                _validate_promotion_record(vault, record, "M1", "v1", method_hash, "method.md")

    def test_candidate_selector_keeps_trial_separate(self):
        block = KnowledgeBlock(
            "MSB-KB-SYNTH-CANDIDATE", "synthetic.candidate", "rule", "candidate", "wiki",
            ("Master-Storyboarding",), ("creative-director",), ("narrative",), (), (), 1,
            (), "synthetic", "Candidate body", Path("candidate.md"), "wiki/candidate.md",
            "msb-kb-synth-candidate", hashlib.sha256(b"Candidate body").hexdigest(),
        )
        policy = {"project": "Master-Storyboarding", "budgets": {"max_blocks": 8, "max_chars": 6000,
                   "max_block_chars": 1800}, "discovery": {"allowed_statuses": ["enabled", "confirmed"],
                   "allowed_kinds": ["rule", "reference"]}}
        profile = {"topics": ["narrative"]}
        normal = select_knowledge([block], policy, "creative-director", {}, {}, set(), profile=profile)
        self.assertEqual(normal.selected, [])
        trial = select_knowledge([block], policy, "creative-director", {}, {}, set(), profile=profile,
                                 candidate_trial_ids={block.id})
        self.assertEqual(trial.selected, [])
        self.assertEqual([item.id for item in trial.trial_candidates], [block.id])


if __name__ == "__main__":
    unittest.main()
