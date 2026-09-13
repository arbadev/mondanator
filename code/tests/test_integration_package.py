"""Packaging behavior on synthetic trees only; no real submission ZIP is built."""
import copy
import csv
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

from integration_fixtures import CODE, IntegrationCase, OUTPUT_FIELDS, SCHEMAS, prediction, request, write_csv
from buy_wait.data import CONTEXT_FILES
from evaluation.package import ArtifactError, USAGE_MARKER, build_code_zip


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


class PackageTests(IntegrationCase):
    def setUp(self):
        super().setUp()
        for table in ("financial_events.csv", "messages.csv", "images.csv"):
            write_csv(self.dataset / table, SCHEMAS[table], [])
        self.output = self.work / "output.csv"
        write_csv(self.output, OUTPUT_FIELDS, [prediction(request(1)), prediction(request(2))])
        self.identity = {"run_id": "synthetic-final", "request_count": 2,
                         "output_sha256": digest(self.output.read_bytes()), "accounting_complete": True}
        self.members = {
            "code/main.py": b"print('SYNTHETIC_BUNDLE_ONLY')\n",
            "README.md": b"# Synthetic packaging fixture, not an agent run\n",
            "requirements.txt": b"# No third-party dependencies in this synthetic program\n",
        }
        self.report()
        for relative, payload in self.members.items():
            path = self.work / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        self.manifest = {
            "schema_version": "buy-wait-final-artifacts/v1", "phase": "final", **self.identity,
            "critical_issues": 0,
            "proof_status_by_request": {"request_1": "resolved_under_policy", "request_2": "conservative_bound"},
            "dataset_hashes": {name: digest((self.dataset / name).read_bytes()) for name in (*CONTEXT_FILES, "requests.csv")},
            "archive_files": {name: digest(payload) for name, payload in self.members.items()},
        }
        self.save()

    def report(self):
        self.members["evaluation/usage_report.md"] = (
            USAGE_MARKER + json.dumps(self.identity, sort_keys=True) + " -->\n\n"
            "# Synthetic report fixture\nNot actual application accounting.\n"
        ).encode()

    def save(self):
        (self.work / "release.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def add_member(self, relative, payload):
        self.members[relative] = payload
        path = self.work / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        self.manifest["archive_files"][relative] = digest(payload)
        self.save()

    def test_only_explicit_members_one_report_and_clean_synthetic_execution(self):
        (self.work / "log.txt").write_text("SYNTHETIC_UNLISTED_LOG")
        (self.work / "unlisted.txt").write_text("SYNTHETIC_UNLISTED_FILE")
        archive = build_code_zip(self.work, "release.json")
        with zipfile.ZipFile(archive) as reader:
            self.assertEqual(set(reader.namelist()), set(self.members))
            self.assertEqual(sum(n.endswith("usage_report.md") for n in reader.namelist()), 1)
            self.assertTrue(all(reader.read(name) == payload for name, payload in self.members.items()))
            restored = self.work / "restored"
            reader.extractall(restored)  # Members were produced by the strict builder.
        run = subprocess.run([sys.executable, "code/main.py"], cwd=restored, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout, "SYNTHETIC_BUNDLE_ONLY\n")
        self.assertFalse((self.work / "chat_transcript").exists())

    def test_zip_is_deterministic_and_existing_artifact_is_never_overwritten(self):
        first = build_code_zip(self.work, "release.json")
        before = first.read_bytes()
        second = build_code_zip(self.work, "release.json", "second.zip")
        self.assertEqual(before, second.read_bytes())
        with self.assertRaises(ArtifactError):
            build_code_zip(self.work, "release.json")
        self.assertEqual(before, first.read_bytes())
        self.assertEqual(list(self.work.glob(".bw-package-*")), [])

    def test_forbidden_names_rejected_before_reading_any_env_member(self):
        original = Path.read_bytes
        def guard(path):
            if path.name.startswith(".env"):
                self.fail("attempted to read an environment file")
            return original(path)
        for name in (".env", "code/.env", "../escape.py", "code/../escape.py", "code\\bad.py",
                     "dataset/requests.csv", "code/cache/source.json", "code/credentials.json", "code/main.py:extra.py",
                     "code/evaluation/usage_report.md", "log.txt", "chat_transcript"):
            with self.subTest(name=name):
                manifest = copy.deepcopy(self.manifest)
                manifest["archive_files"][name] = "0" * 64
                (self.work / "bad.json").write_text(json.dumps(manifest))
                with patch.object(Path, "read_bytes", guard), self.assertRaises(ArtifactError):
                    build_code_zip(self.work, "bad.json")
        self.assertFalse((self.work / "code.zip").exists())

    def test_symlink_file_and_directory_members_are_rejected(self):
        outside = self.work / "synthetic-external.py"
        outside.write_text("SYNTHETIC_NOT_A_KEY")
        alias = self.work / "code/alias.py"
        alias.symlink_to(outside)
        self.manifest["archive_files"]["code/alias.py"] = digest(outside.read_bytes())
        self.save()
        with self.assertRaises(ArtifactError):
            build_code_zip(self.work, "release.json")
        del self.manifest["archive_files"]["code/alias.py"]
        alias.unlink()
        (self.work / "code/linked").symlink_to(self.work)
        self.manifest["archive_files"]["code/linked/synthetic-external.py"] = digest(outside.read_bytes())
        self.save()
        with self.assertRaises(ArtifactError):
            build_code_zip(self.work, "release.json")

    def test_changed_code_data_or_output_invalidates_frozen_hashes(self):
        for target in (self.work / "code/main.py", self.dataset / "financial_profiles.csv", self.output):
            old = target.read_bytes()
            try:
                target.write_bytes(old + b"\n")
                with self.subTest(target=target.name), self.assertRaises(ArtifactError):
                    build_code_zip(self.work, "release.json")
            finally:
                target.write_bytes(old)
        self.assertFalse((self.work / "code.zip").exists())

    def test_report_must_bind_same_run_and_output(self):
        for field, bad in (("run_id", "another-run"), ("output_sha256", "0" * 64), ("accounting_complete", False)):
            identity = {**self.identity, field: bad}
            payload = (USAGE_MARKER + json.dumps(identity) + " -->\n").encode()
            self.add_member("evaluation/usage_report.md", payload)
            with self.subTest(field=field), self.assertRaises(ArtifactError):
                build_code_zip(self.work, "release.json")

    def test_report_identity_rejects_coercion_and_unexpected_private_fields(self):
        for index, change in enumerate(({"accounting_complete": 1}, {"request_count": 2.0}, {"api_key": "SYNTHETIC_PRIVATE_FIELD"})):
            identity = {**self.identity, **change}
            self.add_member("evaluation/usage_report.md", (USAGE_MARKER + json.dumps(identity) + " -->\n").encode())
            with self.subTest(change=change), self.assertRaises(ArtifactError):
                build_code_zip(self.work, "release.json", f"bad-{index}.zip")

    def test_missing_duplicate_or_broken_report_identity_is_rejected(self):
        for payload in (b"# No identity\n", self.members["evaluation/usage_report.md"] * 2,
                        (USAGE_MARKER + "not-json -->\n").encode()):
            self.add_member("evaluation/usage_report.md", payload)
            with self.subTest(payload=payload[:20]), self.assertRaises(ArtifactError):
                build_code_zip(self.work, "release.json")

    def test_unknown_proofs_accounting_and_critical_defects_block_archive(self):
        for change in ({"phase": "sample"}, {"critical_issues": 1}, {"critical_issues": False},
                       {"accounting_complete": False}, {"request_count": 1},
                       {"proof_status_by_request": {"request_1": "unresolved", "request_2": "resolved_under_policy"}},
                       {"proof_status_by_request": {"request_1": "resolved_under_policy"}}):
            manifest = {**self.manifest, **change}
            (self.work / "bad.json").write_text(json.dumps(manifest))
            with self.subTest(change=change), self.assertRaises(ArtifactError):
                build_code_zip(self.work, "bad.json")
        self.assertFalse((self.work / "code.zip").exists())

    def test_sample_labels_cannot_be_pinned_or_bundled(self):
        self.manifest["dataset_hashes"]["sample_requests.csv"] = "0" * 64
        self.save()
        with self.assertRaises(ArtifactError):
            build_code_zip(self.work, "release.json")

    def test_credential_content_and_private_json_config_block_packaging(self):
        # Deliberately synthetic generated strings; no real credential is accessed.
        for payload in (("sk-or-v1-" + "X" * 64).encode(),
                        b'{"nested":{"api_key":"SYNTHETIC_CREDENTIAL"}}',
                        b'{"authorization":"SYNTHETIC_HEADER"}'):
            self.add_member("code/config.json", payload)
            with self.subTest(payload_length=len(payload)), self.assertRaises(ArtifactError):
                build_code_zip(self.work, "release.json")
        self.assertFalse((self.work / "code.zip").exists())

    def test_noncritical_safe_json_reproduction_snapshot_can_be_explicit(self):
        self.add_member("reproduction/facts.json", b'{"synthetic":true,"facts":[]}')
        archive = build_code_zip(self.work, "release.json")
        with zipfile.ZipFile(archive) as reader:
            self.assertEqual(reader.read("reproduction/facts.json"), b'{"synthetic":true,"facts":[]}')

    def test_destination_cannot_modify_dataset_or_escape_and_cli_is_local_only(self):
        for destination in ("dataset/code.zip", "../code.zip", "code/archive.zip", ".git/archive.zip"):
            with self.subTest(destination=destination), self.assertRaises(ArtifactError):
                build_code_zip(self.work, "release.json", destination)
        result = subprocess.run([sys.executable, str(CODE / "evaluation/package.py"), "--root", str(self.work),
                                 "--manifest", "release.json", "--output", "cli.zip"],
                                cwd=self.work, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(json.loads(result.stdout)["uploaded"], False)
        self.assertTrue((self.work / "cli.zip").is_file())
