#!/usr/bin/env python3
"""Portable checks for the Danse public artifact and its hidden control surface."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_pages_builder():
    spec = importlib.util.spec_from_file_location(
        "danse_pages_artifact_test", ROOT / "scripts/build-pages.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PAGES = load_pages_builder()
TEST_COMMIT = "a" * 40


class Markup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.by_id: dict[str, tuple[str, dict[str, str | None]]] = {}
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.scripts: list[str] = []
        self._script: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        self.tags.append((tag, values))
        if "id" in values and values["id"] is not None:
            self.by_id[values["id"]] = (tag, values)
        if tag == "script":
            self._script = []

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            self.scripts.append("".join(self._script))
            self._script = None


def write(path: Path, data: bytes = b"fixture\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def public_fixture(root: Path) -> None:
    for relative in PAGES.RUNTIME_FILES:
        write(root / relative)
    vendor_leaf = "vision_bundle.mjs"
    vendor_data = b"export const localFixture = true;\n"
    write(root / PAGES.VENDOR_BASE / vendor_leaf, vendor_data)
    vendor_manifest = {
        "schema": "danse.vendor.v1",
        "package": {
            "name": "fixture",
            "version": "1",
            "source": "https://example.invalid/fixture.tgz",
            "integrity": "sha512-fixture",
            "sha512": "0" * 128,
            "license": "Apache-2.0",
        },
        "model": {
            "name": "fixture",
            "version": "1",
            "source": "https://example.invalid/fixture.task",
            "license": "Apache-2.0",
        },
        "patch": {
            "reason": "fixture is deterministic",
            "transformations": ["fixture"],
            "upstreamSha256": {vendor_leaf: hashlib.sha256(vendor_data).hexdigest()},
        },
        "files": [{
            "path": vendor_leaf,
            "bytes": len(vendor_data),
            "sha256": hashlib.sha256(vendor_data).hexdigest(),
        }],
    }
    write(
        root / PAGES.VENDOR_MANIFEST,
        (json.dumps(vendor_manifest, sort_keys=True) + "\n").encode(),
    )
    manifest = {
        "schema": "danse.corpus.v1",
        "room": {"file": "room.webp"},
        "tiers": {
            tier: {
                "local": False,
                "plates": f"plates/{tier}/<id>.webp",
                "mattes": f"mattes/{tier}/<id>.webp",
            }
            for tier in PAGES.PUBLIC_TIERS
        },
        "score": "score-2017.json",
        "frames": [{"id": "FRAME"}],
    }
    write(root / "corpus/manifest.json", (json.dumps(manifest) + "\n").encode())
    write(root / "corpus/room.webp")
    write(root / "corpus/score-2017.json")
    for tier in PAGES.PUBLIC_TIERS:
        for kind in ("plates", "mattes"):
            write(root / f"corpus/{kind}/{tier}/FRAME.webp")
    write(root / "submission/text/stale.md", b"must stay private\n")
    write(root / "pipeline/private.py", b"must stay private\n")
    write(root / "README.md", b"repository documentation\n")
    write(root / "corpus/tier-receipts/browse.json", b"internal receipt\n")


def release_fixture(root: Path) -> Path:
    public_path = root / "media/assets/accessibility.md"
    release_path = root / "media/assets/master.mov"
    write(public_path, b"Cleared public accessibility copy.\n")
    write(release_path, b"Release-only master bytes.\n")

    def source(path: Path) -> dict:
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        return {
            "path": relative,
            "destination": relative,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }

    manifest = {
        "schema": "danse.release.v1",
        "release_id": "pages-fixture",
        "status": "public-approved",
        "media": [
            {
                "id": "accessibility-copy",
                "required_for": ["public", "release"],
                "status": "ready",
                "source": source(public_path),
                "clearance": {
                    "status": "cleared",
                    "owner": "Pages fixture",
                    "evidence": {
                        "path": "rights/evidence/pages-fixture.json",
                        "sha256": "0" * 64,
                        "summary": "Fixture-only clearance identity",
                    },
                },
            },
            {
                "id": "score-driven-master",
                "required_for": ["release"],
                "status": "ready",
                "source": source(release_path),
                "clearance": {
                    "status": "cleared",
                    "owner": "Pages fixture",
                    "evidence": {
                        "path": "rights/evidence/pages-fixture.json",
                        "sha256": "0" * 64,
                        "summary": "Fixture-only clearance identity",
                    },
                },
            },
        ],
        "credits": [],
        "gates": [],
    }
    path = root / PAGES.RELEASE_MANIFEST
    write(path, (json.dumps(manifest, indent=2) + "\n").encode())
    return path


class ProductionArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        supplied = os.environ.get("DANSE_PAGES_ARTIFACT")
        expected = os.environ.get("DANSE_PAGES_SOURCE_SHA")
        cls._temporary = None
        if supplied:
            cls.output = Path(supplied)
            cls.manifest = PAGES.verify_artifact(cls.output, expected)
        else:
            cls._temporary = tempfile.TemporaryDirectory()
            cls.output = Path(cls._temporary.name) / "pages"
            cls.manifest = PAGES.build(ROOT, cls.output, TEST_COMMIT)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._temporary is not None:
            cls._temporary.cleanup()

    def test_artifact_inventory_is_exactly_the_allowlist_and_digest_manifest(self) -> None:
        allowed = set(PAGES.source_files(ROOT))
        recorded = {record["path"] for record in self.manifest["files"]}
        actual = PAGES.artifact_inventory(self.output)
        self.assertEqual(recorded, allowed)
        self.assertEqual(actual, allowed | {PAGES.ARTIFACT_MANIFEST})
        self.assertEqual(
            self.manifest["source"]["repository"], "organvm/the-thing-without-a-name"
        )

    def test_only_declared_browse_and_screen_derivatives_are_public(self) -> None:
        corpus = json.loads((ROOT / "corpus/manifest.json").read_text())
        frame_count = len(corpus["frames"])
        paths = {record["path"] for record in self.manifest["files"]}
        for tier in PAGES.PUBLIC_TIERS:
            for kind in ("plates", "mattes"):
                prefix = f"corpus/{kind}/{tier}/"
                self.assertEqual(sum(path.startswith(prefix) for path in paths), frame_count)
        self.assertEqual(
            {path for path in paths if path.startswith("engine/")}, set(PAGES.ENGINE_MODULES)
        )
        self.assertNotIn("engine/query.js", paths)
        self.assertNotIn("engine/tier.js", paths)
        self.assertNotIn("corpus/tier-receipts/browse.json", paths)
        self.assertNotIn("corpus/tier-receipts/screen.json", paths)

    def test_pose_runtime_is_local_and_bound_to_its_vendor_manifest(self) -> None:
        paths = {record["path"] for record in self.manifest["files"]}
        vendor = json.loads((ROOT / PAGES.VENDOR_MANIFEST).read_text())
        declared = {
            f"{PAGES.VENDOR_BASE}/{record['path']}" for record in vendor["files"]
        }
        self.assertEqual(
            {path for path in paths if path.startswith(f"{PAGES.VENDOR_BASE}/")},
            declared | {PAGES.VENDOR_MANIFEST},
        )
        camera = (self.output / "interaction/camera.js").read_text(encoding="utf-8")
        bundle = (self.output / PAGES.VENDOR_BASE / "vision_bundle.mjs").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("cdn.jsdelivr", camera)
        self.assertNotIn("storage.googleapis.com", camera)
        self.assertNotIn("odml.pa.googleapis.com", bundle)
        self.assertIn('./vendor/mediapipe/vision_bundle.mjs', camera)

    def test_repository_docs_harnesses_and_future_project_route_are_absent(self) -> None:
        paths = PAGES.artifact_inventory(self.output)
        forbidden = {
            ".github/workflows/pages.yml",
            "AGENTS.md",
            "LINEAGE.json",
            "README.md",
            "done.sh",
            "film.html",
            "interaction-test.html",
            "join.html",
            "probe.html",
            "pyproject.toml",
            "reference/T-2017-full.png",
            "scripts/check-danse.py",
            "submission/screendance-2027.yaml",
            "verify.html",
        }
        self.assertTrue(paths.isdisjoint(forbidden))
        self.assertFalse(any(path.startswith("pipeline/") for path in paths))
        self.assertFalse(any(path.startswith("submission/") for path in paths))
        self.assertFalse(any(path.startswith("music/") for path in paths))
        self.assertFalse(any(path.startswith("project/") for path in paths))
        self.assertFalse(any(path.startswith("rights/") for path in paths))

    def test_every_recorded_sha256_and_byte_count_verifies(self) -> None:
        verified = PAGES.verify_artifact(
            self.output, os.environ.get("DANSE_PAGES_SOURCE_SHA") or TEST_COMMIT
        )
        self.assertEqual(verified, self.manifest)

    def test_deployment_requires_public_rights_before_artifact_upload(self) -> None:
        workflow = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        rights = workflow.index("scripts/check-rights.py")
        upload = workflow.index("actions/upload-pages-artifact")
        deploy = workflow.index("actions/deploy-pages")
        self.assertLess(rights, upload)
        self.assertLess(upload, deploy)
        self.assertIn("--phase public", workflow)
        self.assertIn("--release-manifest release/manifest.json", workflow)


class ArtifactBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.base = Path(self._temporary.name)
        self.root = self.base / "repo"
        self.output = self.base / "pages"
        public_fixture(self.root)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_unlisted_files_are_not_copied(self) -> None:
        PAGES.build(self.root, self.output, TEST_COMMIT)
        inventory = PAGES.artifact_inventory(self.output)
        self.assertNotIn("README.md", inventory)
        self.assertFalse(any(path.startswith("submission/") for path in inventory))
        self.assertFalse(any(path.startswith("pipeline/") for path in inventory))
        self.assertFalse(any(path.startswith("rights/") for path in inventory))
        self.assertFalse(any(path.startswith("corpus/tier-receipts/") for path in inventory))

    def test_only_cleared_public_release_assets_are_copied_and_digested(self) -> None:
        release_fixture(self.root)
        manifest = PAGES.build(self.root, self.output, TEST_COMMIT)
        paths = {record["path"] for record in manifest["files"]}
        self.assertIn("media/assets/accessibility.md", paths)
        self.assertNotIn("media/assets/master.mov", paths)
        self.assertNotIn(PAGES.RELEASE_MANIFEST, paths)
        record = next(
            row for row in manifest["files"] if row["path"] == "media/assets/accessibility.md"
        )
        published = self.output / record["path"]
        self.assertEqual(record["bytes"], published.stat().st_size)
        self.assertEqual(record["sha256"], hashlib.sha256(published.read_bytes()).hexdigest())

    def test_public_release_asset_identity_and_destination_fail_closed(self) -> None:
        release_manifest = release_fixture(self.root)
        manifest = json.loads(release_manifest.read_text())
        public = manifest["media"][0]
        public["source"]["sha256"] = "0" * 64
        release_manifest.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(PAGES.ArtifactError, "source identity is stale"):
            PAGES.build(self.root, self.output, TEST_COMMIT)

        manifest = json.loads(release_manifest.read_text())
        payload = (self.root / "media/assets/accessibility.md").read_bytes()
        public = manifest["media"][0]
        public["source"] = {
            "path": "media/assets/accessibility.md",
            "destination": "submission/private.md",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }
        release_manifest.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(PAGES.ArtifactError, "outside its public destination"):
            PAGES.build(self.root, self.output, TEST_COMMIT)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_public_release_asset_symlink_fails_closed(self) -> None:
        release_fixture(self.root)
        public = self.root / "media/assets/accessibility.md"
        outside = self.base / "outside-release.md"
        write(outside, public.read_bytes())
        public.unlink()
        public.symlink_to(outside)
        with self.assertRaisesRegex(PAGES.ArtifactError, "symlink"):
            PAGES.build(self.root, self.output, TEST_COMMIT)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_allowlisted_source_symlink_fails_closed(self) -> None:
        target = self.base / "outside.html"
        write(target, b"outside\n")
        (self.root / "index.html").unlink()
        (self.root / "index.html").symlink_to(target)
        with self.assertRaisesRegex(PAGES.ArtifactError, "symlink"):
            PAGES.build(self.root, self.output, TEST_COMMIT)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_allowlisted_source_directory_symlink_fails_closed(self) -> None:
        public = self.root / "corpus/plates/screen"
        outside = self.base / "outside-screen"
        write(outside / "FRAME.webp", b"outside\n")
        shutil.rmtree(public)
        public.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(PAGES.ArtifactError, "symlink"):
            PAGES.build(self.root, self.output, TEST_COMMIT)

    def test_manifest_path_escape_fails_closed(self) -> None:
        path = self.root / "corpus/manifest.json"
        manifest = json.loads(path.read_text())
        manifest["tiers"]["browse"]["plates"] = "../../submission/<id>.webp"
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(PAGES.ArtifactError, "must declare"):
            PAGES.build(self.root, self.output, TEST_COMMIT)

    def test_unsafe_frame_id_fails_closed(self) -> None:
        path = self.root / "corpus/manifest.json"
        manifest = json.loads(path.read_text())
        manifest["frames"][0]["id"] = "../private"
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(PAGES.ArtifactError, "unsafe corpus frame id"):
            PAGES.build(self.root, self.output, TEST_COMMIT)

    def test_tampered_artifact_digest_fails_closed(self) -> None:
        PAGES.build(self.root, self.output, TEST_COMMIT)
        (self.output / "arrival.js").write_bytes(b"tampered\n")
        with self.assertRaisesRegex(PAGES.ArtifactError, "digest mismatch"):
            PAGES.verify_artifact(self.output, TEST_COMMIT)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_symlink_in_built_artifact_fails_closed(self) -> None:
        PAGES.build(self.root, self.output, TEST_COMMIT)
        target = self.base / "outside.html"
        write(target, b"outside\n")
        published = self.output / "index.html"
        published.unlink()
        published.symlink_to(target)
        with self.assertRaisesRegex(PAGES.ArtifactError, "non-regular file"):
            PAGES.verify_artifact(self.output, TEST_COMMIT)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_symlinked_artifact_root_fails_closed(self) -> None:
        PAGES.build(self.root, self.output, TEST_COMMIT)
        alias = self.base / "pages-alias"
        alias.symlink_to(self.output, target_is_directory=True)
        with self.assertRaisesRegex(PAGES.ArtifactError, "root must not be a symlink"):
            PAGES.verify_artifact(alias, TEST_COMMIT)

    def test_wrong_deployed_source_sha_fails_closed(self) -> None:
        PAGES.build(self.root, self.output, TEST_COMMIT)
        with self.assertRaisesRegex(PAGES.ArtifactError, "does not match expected"):
            PAGES.verify_artifact(self.output, "b" * 40)

    def test_tampered_pose_vendor_source_fails_closed(self) -> None:
        vendor = self.root / PAGES.VENDOR_BASE / "vision_bundle.mjs"
        vendor.write_bytes(b"tampered\n")
        with self.assertRaisesRegex(PAGES.ArtifactError, "pose vendor digest mismatch"):
            PAGES.build(self.root, self.output, TEST_COMMIT)

    def test_digest_valid_pose_vendor_with_external_runtime_fails_closed(self) -> None:
        vendor = self.root / PAGES.VENDOR_BASE / "vision_bundle.mjs"
        data = b'fetch("https://odml.pa.googleapis.com/v1/log");\n'
        vendor.write_bytes(data)
        manifest_path = self.root / PAGES.VENDOR_MANIFEST
        manifest = json.loads(manifest_path.read_text())
        manifest["files"][0]["bytes"] = len(data)
        manifest["files"][0]["sha256"] = hashlib.sha256(data).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(PAGES.ArtifactError, "forbidden runtime CDN"):
            PAGES.build(self.root, self.output, TEST_COMMIT)

    def test_pose_vendor_module_cannot_import_outside_public_boundary(self) -> None:
        vendor = self.root / PAGES.VENDOR_BASE / "vision_bundle.mjs"
        data = b'import "../../../submission/private.js";\n'
        vendor.write_bytes(data)
        manifest_path = self.root / PAGES.VENDOR_MANIFEST
        manifest = json.loads(manifest_path.read_text())
        manifest["files"][0]["bytes"] = len(data)
        manifest["files"][0]["sha256"] = hashlib.sha256(data).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(PAGES.ArtifactError, "imports non-public dependency"):
            PAGES.build(self.root, self.output, TEST_COMMIT)


class InterfaceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.markup = Markup()
        cls.markup.feed(cls.html)
        cls.script = "\n".join(cls.markup.scripts)

    def test_hud_is_really_hidden_and_disclosed_by_an_accessible_touch_target(self) -> None:
        tag, hud = self.markup.by_id["hud"]
        self.assertEqual(tag, "section")
        self.assertIn("hidden", hud)
        self.assertEqual(hud["aria-hidden"], "true")
        tag, toggle = self.markup.by_id["hud-toggle"]
        self.assertEqual(tag, "button")
        self.assertEqual(toggle["type"], "button")
        self.assertEqual(toggle["aria-controls"], "hud")
        self.assertEqual(toggle["aria-expanded"], "false")
        self.assertEqual(toggle["aria-label"], "Show Danse controls")
        self.assertIn("#hud[hidden] { display: none; }", self.html)
        self.assertIn("min-width: 48px; min-height: 48px", self.html)

    def test_keyboard_and_touch_controls_keep_aria_state_in_sync(self) -> None:
        self.assertIn("function setHudVisible(visible)", self.script)
        self.assertIn('hud.setAttribute("aria-hidden", String(!visible))', self.script)
        self.assertIn('hudToggle.setAttribute("aria-expanded", String(visible))', self.script)
        self.assertIn('hudToggle.addEventListener("click"', self.script)
        self.assertIn('const key = e.key.toLowerCase()', self.script)
        self.assertIn('if (key === "h")', self.script)
        self.assertIn('if (e.key === "Escape")', self.script)
        self.assertIn("keyboard-instructions", self.markup.by_id)
        self.assertIn("touch-instructions", self.markup.by_id)

    def test_share_feedback_has_its_own_polite_live_region(self) -> None:
        tag, toast = self.markup.by_id["toast"]
        self.assertEqual(tag, "div")
        self.assertEqual(toast["role"], "status")
        self.assertEqual(toast["aria-live"], "polite")
        self.assertEqual(toast["aria-atomic"], "true")
        self.assertIn("hidden", toast)
        self.assertIn('const toast = el("toast")', self.script)
        self.assertNotIn('el("keys")', self.script)

    def test_optional_score_failure_announces_fallback_without_disabling_the_artwork(self) -> None:
        self.assertIn("await MusicalScore.loadOptional(scoreUrl", self.script)
        self.assertIn("scoreLoadFailure = error", self.script)
        self.assertIn("continuing with the default artwork", self.script)
        self.assertIn(
            'if (scoreLoadFailure) flash("Musical score unavailable · continuing with the default artwork", 8000)',
            self.script,
        )
        film = (ROOT / "film.html").read_text(encoding="utf-8")
        self.assertIn("scoreUrl ? await MusicalScore.load(scoreUrl) : null", film)
        self.assertNotIn("MusicalScore.loadOptional", film)

    def test_canvas_has_a_text_description_and_canonical_metadata(self) -> None:
        tag, canvas = self.markup.by_id["stage"]
        self.assertEqual(tag, "canvas")
        self.assertEqual(canvas["role"], "img")
        self.assertEqual(canvas["aria-describedby"], "stage-description")
        self.assertTrue(canvas["aria-label"])
        self.assertIn("stage-description", self.markup.by_id)
        canonical = [
            attrs
            for tag, attrs in self.markup.tags
            if tag == "link" and attrs.get("rel") == "canonical"
        ]
        self.assertEqual(
            canonical[0]["href"], "https://organvm.github.io/the-thing-without-a-name/"
        )
        descriptions = [
            attrs
            for tag, attrs in self.markup.tags
            if tag == "meta" and attrs.get("name") == "description"
        ]
        self.assertIn("Anthony J. Padavano", descriptions[0]["content"])
        self.assertIn("<title>Danse — a room that never repeats</title>", self.html)

    def test_layout_uses_mobile_safe_areas_and_reduced_motion_holds_a_frame(self) -> None:
        self.assertIn("viewport-fit=cover", self.html)
        self.assertIn("env(safe-area-inset-bottom)", self.html)
        self.assertIn("@media (max-width: 640px)", self.html)
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.html)
        self.assertIn('matchMedia("(prefers-reduced-motion: reduce)")', self.script)
        self.assertIn(
            "let heldAt = reducedMotion.matches ? Arrival.now(river) : null", self.script
        )
        self.assertIn('reducedMotion.addEventListener("change"', self.script)

    def test_local_interaction_is_explicit_private_and_has_fallbacks(self) -> None:
        _, video = self.markup.by_id["pose-video"]
        self.assertIn("hidden", video)
        self.assertEqual(video["aria-hidden"], "true")
        for button_id in ("camera-start", "camera-retry", "fallback-start", "interaction-stop"):
            tag, attrs = self.markup.by_id[button_id]
            self.assertEqual(tag, "button")
            self.assertEqual(attrs["type"], "button")
        status_tag, status = self.markup.by_id["interaction-status"]
        self.assertEqual(status_tag, "p")
        self.assertEqual(status["role"], "status")
        self.assertEqual(status["aria-live"], "polite")
        for control_id in ("fallback-x", "fallback-y", "fallback-open", "fallback-reach"):
            tag, attrs = self.markup.by_id[control_id]
            self.assertEqual(tag, "input")
            self.assertEqual(attrs["type"], "range")
        self.assertIn('el("camera-start").addEventListener("click"', self.script)
        self.assertNotIn("getUserMedia(", self.script)
        self.assertIn("frames stay in memory on this device", self.html)
        self.assertIn("raw landmarks are discarded immediately", self.html)

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_inline_module_has_valid_javascript_syntax(self) -> None:
        modules = [
            script
            for script, (_, attrs) in zip(self.markup.scripts, [tag for tag in self.markup.tags if tag[0] == "script"])
            if attrs.get("type") == "module"
        ]
        self.assertEqual(len(modules), 1)
        with tempfile.TemporaryDirectory() as temporary:
            module = Path(temporary) / "index.mjs"
            module.write_text(modules[0], encoding="utf-8")
            done = subprocess.run(
                ["node", "--check", str(module)], capture_output=True, text=True, check=False
            )
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
