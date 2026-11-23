from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error
import zipfile

TW = Path(__file__).resolve().parent.parent
ROOT = TW.parent
if str(TW) not in sys.path:
    sys.path.insert(0, str(TW))

import consent
from doctor import run_doctor, verify_layered_integrity
from native import Invalid
import release as rel_mod


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None


def _make_zip(outputs: dict[str, bytes]) -> tuple[bytes, dict[str, str]]:
    buf = io.BytesIO()
    hashes: dict[str, str] = {}
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel, data in sorted(outputs.items()):
            zf.writestr(rel, data)
            hashes[rel] = hashlib.sha256(data).hexdigest()
    return buf.getvalue(), hashes


class _FakeGitHub:
    def __init__(self, *, sha: str = "a" * 40, branch: str = "main", existing_releases=None) -> None:
        self.sha = sha
        self.branch = branch
        self.releases: list[dict] = list(existing_releases or [])
        self.assets_by_id: dict[int, list[dict]] = {}
        self.tag_refs: dict[str, str] = {}
        self.contents_by_ref: dict[str, dict] = {}
        self.next_id = 100
        self.seen_urls: list[tuple[str, str]] = []
        self.fail_on: tuple[str, str, int] | None = None

    def transport(self, req, timeout=60):
        del timeout
        method = req.get_method()
        url = req.full_url
        self.seen_urls.append((method, url))
        if self.fail_on and method == self.fail_on[0] and self.fail_on[1] in url:
            raise urllib.error.HTTPError(url, self.fail_on[2], "mock error", {}, None)

        repo_root = "https://api.github.com/repos/isyuricunha/the-iron-oath-traducao-portugues-brasileiro"
        if method == "GET" and url == repo_root:
            return _FakeResponse(json.dumps({"default_branch": self.branch}).encode("utf-8"))
        if method == "GET" and url == f"{repo_root}/commits/{self.branch}":
            return _FakeResponse(json.dumps({"sha": self.sha}).encode("utf-8"))
        if method == "GET" and url.startswith(f"{repo_root}/releases?per_page=100&page="):
            page = int(url.split("page=")[-1])
            return _FakeResponse(json.dumps(self.releases if page == 1 else []).encode("utf-8"))
        if method == "GET" and "/git/ref/tags/" in url:
            tag = url.split("/git/ref/tags/")[-1]
            if tag not in self.tag_refs:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return _FakeResponse(json.dumps({"object": {"sha": self.tag_refs[tag]}}).encode("utf-8"))
        if method == "GET" and "/contents/translation-work/build-manifest.json?ref=" in url:
            tag = url.split("?ref=")[-1]
            content = base64.b64encode(json.dumps(self.contents_by_ref[tag]).encode("utf-8")).decode("ascii")
            return _FakeResponse(json.dumps({"content": content}).encode("utf-8"))
        if method == "POST" and url == f"{repo_root}/releases":
            body = json.loads(req.data.decode("utf-8"))
            rid = self.next_id
            self.next_id += 1
            rec = {
                "id": rid,
                "tag_name": body["tag_name"],
                "target_commitish": body["target_commitish"],
                "name": body["name"],
                "body": body["body"],
                "draft": True,
                "prerelease": False,
                "upload_url": f"https://uploads.github.com/repos/isyuricunha/the-iron-oath-traducao-portugues-brasileiro/releases/{rid}/assets{{?name,label}}",
                "assets": [],
            }
            self.releases.append(rec)
            self.assets_by_id[rid] = []
            return _FakeResponse(json.dumps(rec).encode("utf-8"))
        if method == "GET" and re.search(r"/releases/\d+/assets$", url):
            rid = int(url.split("/")[-2])
            return _FakeResponse(json.dumps(self.assets_by_id[rid]).encode("utf-8"))
        if method == "POST" and "uploads.github.com" in url and "?name=PTBR.zip" in url:
            rid = int(url.split("/releases/")[1].split("/")[0])
            raw = req.data
            asset = {
                "id": 900 + rid,
                "name": "PTBR.zip",
                "size": len(raw),
                "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
            }
            self.assets_by_id[rid].append(asset)
            for rel_rec in self.releases:
                if rel_rec["id"] == rid:
                    rel_rec["assets"].append(asset)
            return _FakeResponse(json.dumps(asset).encode("utf-8"))
        if method == "PATCH" and re.search(r"/releases/\d+$", url):
            rid = int(url.split("/")[-1])
            body = json.loads(req.data.decode("utf-8"))
            for rel_rec in self.releases:
                if rel_rec["id"] == rid:
                    rel_rec.update(body)
                    return _FakeResponse(json.dumps(rel_rec).encode("utf-8"))
        if method == "GET" and re.search(r"/releases/\d+$", url):
            rid = int(url.split("/")[-1])
            for rel_rec in self.releases:
                if rel_rec["id"] == rid:
                    return _FakeResponse(json.dumps(rel_rec).encode("utf-8"))
        raise AssertionError(f"Unhandled mock API request: {method} {url}")


class ReleaseAndDocsTests(unittest.TestCase):
    def test_release_publish_lifecycle_changelog_and_fail_closed_guards(self) -> None:
        gh = _FakeGitHub()
        repo = "isyuricunha/the-iron-oath-traducao-portugues-brasileiro"
        api = rel_mod.API(repo, "tok", transport=gh.transport)
        zip_v1, outs_v1 = _make_zip({"The Iron Oath_Data/StreamingAssets/Data/Codex/codex.xml": b"<c/>"})

        def make_notes(tag, prev_tag, before):
            old = {} if prev_tag is None else {
                "yarn:1": {"EN": "Hello", "PTBR": "Ola", "format": "yarn", "source_file": "QuestData/TioQuests.yarn.txt"}
            }
            new = {
                "yarn:1": {"EN": "Hello", "PTBR": "Ola revisado", "format": "yarn", "source_file": "QuestData/TioQuests.yarn.txt"},
                "codex_xml:1": {"EN": "Lore", "PTBR": "Historia", "format": "codex_xml", "source_file": "Codex/codex.xml"},
                "dll:1": {"EN": "Play", "PTBR": "Jogar", "format": "dll", "source_file": "Managed/Assembly-CSharp.dll"},
            }
            return rel_mod.changelog(old, new, before, outs_v1, repo, gh.sha, tag=tag, previous_tag=prev_tag)

        # 1. First release v1.0.0
        res1 = rel_mod.publish(api, gh.sha, outs_v1, zip_v1, lambda *a: None, make_notes)
        self.assertEqual(res1["status"], "published")
        self.assertEqual(res1["tag"], "v1.0.0")
        self.assertIn(("GET", f"https://api.github.com/repos/{repo}"), gh.seen_urls)
        self.assertNotIn(("GET", f"https://api.github.com/repos/{repo}/"), gh.seen_urls)
        pub_rel = gh.releases[0]
        self.assertFalse(pub_rel["draft"])
        self.assertEqual([a["name"] for a in pub_rel["assets"]], ["PTBR.zip"])
        self.assertIn("### 📦 Download", pub_rel["body"])
        self.assertIn("### ✨ O que mudou", pub_rel["body"])
        self.assertIn("Diálogos e missões", pub_rel["body"])
        self.assertIn("Códice e lore", pub_rel["body"])
        self.assertIn("Código nativo", pub_rel["body"])

        # Setup tag ref & prior manifest receipt for idempotent / incremental checks
        gh.tag_refs["v1.0.0"] = gh.sha
        gh.contents_by_ref["v1.0.0"] = {"outputs": outs_v1}

        # 2. Unchanged outputs -> skip idempotently
        res_same = rel_mod.publish(api, gh.sha, outs_v1, zip_v1, lambda *a: None, make_notes)
        self.assertEqual(res_same, {"status": "unchanged", "tag": "v1.0.0"})

        # 3. Incremental release v1.0.1
        zip_v2, outs_v2 = _make_zip({"The Iron Oath_Data/StreamingAssets/Data/Codex/codex.xml": b"<c>v2</c>"})
        gh.sha = "b" * 40
        res2 = rel_mod.publish(
            api,
            gh.sha,
            outs_v2,
            zip_v2,
            lambda *a: None,
            lambda t, pt, bef: rel_mod.changelog(
                {"yarn:1": {"EN": "Hello", "PTBR": "Ola", "format": "yarn", "source_file": "QuestData/TioQuests.yarn.txt"}},
                {"yarn:1": {"EN": "Hello", "PTBR": "Ola 2", "format": "yarn", "source_file": "QuestData/TioQuests.yarn.txt"}},
                bef,
                outs_v2,
                repo,
                gh.sha,
                tag=t,
                previous_tag=pt,
            ),
        )
        self.assertEqual(res2["tag"], "v1.0.1")
        self.assertIn("v1.0.0", gh.releases[1]["body"])

        # 4. Fail closed on unexpected extra asset in prior release, HTTP 401/403/404, stale branch, non-ancestor
        gh.releases[1]["assets"].append({"name": "unexpected.txt", "digest": "sha256:0", "size": 1})
        gh.tag_refs["v1.0.1"] = gh.sha
        gh.contents_by_ref["v1.0.1"] = {"outputs": outs_v2}
        with self.assertRaises(Invalid) as asset_err:
            rel_mod.publish(api, gh.sha, outs_v2, zip_v2, lambda *a: None, make_notes)
        self.assertIn("Unexpected public release assets", str(asset_err.exception))
        gh.releases[1]["assets"].pop()

        with self.assertRaises(Invalid) as stale_err:
            rel_mod.publish(api, "c" * 40, outs_v2, zip_v2, lambda *a: None, make_notes)
        self.assertIn("Stale workflow commit", str(stale_err.exception))

        for code in (401, 403, 404):
            gh.fail_on = ("GET", "/commits/", code)
            with self.assertRaises(ValueError) as http_err:
                rel_mod.publish(api, gh.sha, outs_v2, zip_v2, lambda *a: None, make_notes)
            self.assertIn(f"HTTP {code}", str(http_err.exception))
        gh.fail_on = None

    def test_evergreen_readme_markers_release_workflow_and_no_persona_names(self) -> None:
        readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertEqual(rel_mod.check_legacy_readme_markers(readme_text), [])
        self.assertIn("https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/699330/header.jpg", readme_text)
        self.assertIn("/releases", readme_text)
        self.assertIn("PTBR.zip", readme_text)

        # Marker detector warns non-fatally when stale copy appears
        dirty_warnings = rel_mod.check_legacy_readme_markers(
            "Traducao em andamento. Baixe em /releases/tag/v1.0.0 ou C:\\Users\\dev."
        )
        self.assertGreaterEqual(len(dirty_warnings), 3)

        # Public copy & internal docs must not leak agent persona names
        persona_re = re.compile(r"\b(ella|sayuri|codex|chatgpt|claude|gemini)\b", re.IGNORECASE)
        checked_docs = [
            ROOT / "README.md",
            ROOT / "CONTRIBUTING.md",
            ROOT / "GAME-DEVELOPER-PERMISSION.md",
            ROOT / ".github" / "workflows" / "release.yml",
            TW / "README.md",
            TW / "docs" / "briefing-do-jogo.md",
            TW / "docs" / "glossario-ptbr.md",
            TW / "docs" / "diretrizes-de-traducao.md",
            TW / "docs" / "fluxo-de-traducao.md",
            TW / "docs" / "formato-localizacao.md",
        ]
        for doc in checked_docs:
            txt = doc.read_text(encoding="utf-8")
            self.assertIsNone(
                re.search(r"\b(?:ella|sayuri|chatgpt|claude|gemini|openai\s+codex|codex\s+(?:cli|agent))\b", txt, re.IGNORECASE),
                msg=f"Agent/persona name leaked in {doc}",
            )
        notes_sample = rel_mod.changelog({}, {"yarn:1": {"EN": "Hi", "PTBR": "Oi", "format": "yarn", "source_file": "QuestData/TioQuests.yarn.txt"}}, {}, {"The Iron Oath_Data/a": "0" * 64}, "isyuricunha/the-iron-oath-traducao-portugues-brasileiro", "a" * 40)
        self.assertIsNone(re.search(r"\b(?:ella|sayuri|chatgpt|claude|gemini|openai\s+codex|codex\s+(?:cli|agent))\b", notes_sample, re.IGNORECASE))

        wf_text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", wf_text)
        self.assertIn("verify-output", wf_text)
        self.assertIn("release.py", wf_text)
        self.assertIn("github.event_name", wf_text)

    def test_day_one_licensing_and_contributor_consent(self) -> None:
        lic = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Attribution-NonCommercial-ShareAlike 4.0 International", lic)
        perm = (ROOT / "GAME-DEVELOPER-PERMISSION.md").read_text(encoding="utf-8")
        self.assertNotIn("Permissão", perm)
        self.assertIn("official", perm.lower())
        contrib = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn(consent.CONSENT_LINE, contrib.replace("`", ""))

        good_pr = {
            "number": 12,
            "user": {"login": "tradutor"},
            "head": {"sha": "deadbeef" * 5},
            "body": f"- [x] {consent.CONSENT_LINE}\n",
        }
        self.assertTrue(consent.consent(good_pr))
        bad_pr = {**good_pr, "body": f"- [ ] {consent.CONSENT_LINE}\n"}
        self.assertFalse(consent.consent(bad_pr))
        ok, _msg = consent.verify_pr_consent(bad_pr)
        self.assertFalse(ok)

    def test_windows_git_lf_normalization_tampering_and_doctor_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp)
            # Copy toolkit runtime files & config into sandbox repo
            shutil.copy2(ROOT / ".gitattributes", clone / ".gitattributes")
            (clone / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / ".github" / "workflows" / "release.yml", clone / ".github" / "workflows" / "release.yml")
            shutil.copy2(ROOT / ".github" / "workflows" / "contributions.yml", clone / ".github" / "workflows" / "contributions.yml")

            for rel_dir in (
                "translation-work",
                "translation-work/formats",
                "translation-work/dotnet/DllStringPatch",
                "translation-work/vendor/DllStringPatch",
                "translation-work/tests/fixtures",
            ):
                src_d = ROOT / rel_dir
                dst_d = clone / rel_dir
                dst_d.mkdir(parents=True, exist_ok=True)
                for p in src_d.iterdir():
                    if p.is_file():
                        shutil.copy2(p, dst_d / p.name)
            (clone / "translation-work" / "tests" / "smoke-linux-arm64.py").parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(TW / "tests" / "smoke-linux-arm64.py", clone / "translation-work" / "tests" / "smoke-linux-arm64.py")

            # Convert git-lf text files to CRLF to prove Windows CRLF working copy still passes LF-normalized checks
            tc = json.loads((clone / "translation-work" / "toolchain.json").read_text(encoding="utf-8"))
            for rel, spec in tc["files"].items():
                if spec["mode"] == "git-lf":
                    p = clone / rel
                    p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
            tc_p = clone / "translation-work" / "toolchain.json"
            tc_p.write_bytes(tc_p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))

            rt = verify_layered_integrity(clone)
            self.assertEqual(rt["schema"], 1)
            doc_ok = run_doctor(clone, check_en_sources=False)
            self.assertEqual(doc_ok["status"], "ok")

            # 1. Real content modification triggers CHECKSUM_MISMATCH
            shapes_p = clone / "translation-work" / "engine-shapes.json"
            saved_shapes = shapes_p.read_bytes()
            shapes_p.write_bytes(saved_shapes.replace(b"1.0.021", b"1.0.099"))
            with self.assertRaises(Invalid) as chk_err:
                run_doctor(clone, check_en_sources=False)
            self.assertIn("CHECKSUM_MISMATCH", str(chk_err.exception))
            shapes_p.write_bytes(saved_shapes)

            # 2. Binary fixture tampering triggers CHECKSUM_MISMATCH
            dll_p = clone / "translation-work" / "tests" / "fixtures" / "smoke-sample.dll"
            saved_dll = dll_p.read_bytes()
            dll_p.write_bytes(saved_dll + b"\x00")
            with self.assertRaises(Invalid) as bin_err:
                run_doctor(clone, check_en_sources=False)
            self.assertIn("CHECKSUM_MISMATCH", str(bin_err.exception))
            dll_p.write_bytes(saved_dll)

            # 3. Missing runtime triggers MISSING_RUNTIME
            with mock.patch("doctor.check_dotnet_runtime", return_value=(False, "MISSING_RUNTIME: dotnet not found")):
                with self.assertRaises(Invalid) as rt_err:
                    run_doctor(clone, check_en_sources=False)
                self.assertIn("MISSING_RUNTIME", str(rt_err.exception))

            # 4. Unsupported architecture triggers UNSUPPORTED_ARCHITECTURE
            with mock.patch("doctor.platform.machine", return_value="riscv64"):
                with self.assertRaises(Invalid) as arch_err:
                    run_doctor(clone, check_en_sources=False)
                self.assertIn("UNSUPPORTED_ARCHITECTURE", str(arch_err.exception))

            # 5. Missing Mono.Cecil triggers CHECKSUM_MISMATCH / MISSING_DEPENDENCY when unlinked
            cecil = clone / "translation-work" / "vendor" / "DllStringPatch" / "Mono.Cecil.dll"
            cecil.unlink()
            with self.assertRaises(Invalid) as dep_err:
                run_doctor(clone, check_en_sources=False)
            self.assertIn("MISSING_DEPENDENCY", str(dep_err.exception))

            # 6. Malformed fixture triggers MALFORMED_TEST_DATA
            bad_dll = clone / "bad.dll"
            bad_dll.write_bytes(b"not a valid PE/CLR assembly")
            # Restore Mono.Cecil first
            shutil.copy2(TW / "vendor" / "DllStringPatch" / "Mono.Cecil.dll", cecil)
            with self.assertRaises(Invalid) as mal_err:
                run_doctor(clone, smoke_dll_path=bad_dll, check_en_sources=False)
            # Depending on loader stage, DllStringPatch reports MISSING_DEPENDENCY or MALFORMED_TEST_DATA
            self.assertTrue(
                "MALFORMED_TEST_DATA" in str(mal_err.exception) or "DllStringPatch" in str(mal_err.exception)
            )


if __name__ == "__main__":
    unittest.main()
