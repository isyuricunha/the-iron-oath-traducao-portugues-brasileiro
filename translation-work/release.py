"""Verified, serialized GitHub Release publisher for The Iron Oath PT-BR."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from native import require
from storage import read_json
from toolkit import Project, ROOT

VERSION = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")

CATEGORY_LABELS = {
    "dialogo": "Diálogos e missões",
    "codice": "Códice e lore",
    "interface": "Interface e menus",
    "mundo": "Dados e mundo",
    "nativo": "Código nativo e interface programada",
}

LEGACY_README_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"\b(em\s+andamento|em\s+constru[çc][ãa]o|em\s+breve|ainda\s+n[ãa]o\s+publicad[ao]|ainda\s+n[ãa]o\s+h[áa]\s+release)\b",
        "pre-release placeholder wording detected in README.md",
    ),
    (
        r"/releases/(?:tag|download)/v\d+\.\d+\.\d+",
        "version-pinned release URL detected in README.md (prefer evergreen /releases link)",
    ),
    (
        r"\b(?:ella|sayuri|chatgpt|claude|gemini|openai\s+codex|codex\s+(?:cli|agent))\b",
        "agent or persona name detected in public README.md",
    ),
    (
        r"(?:[A-Za-z]:\\|/home/|/Users/)",
        "machine-specific filesystem path detected in README.md",
    ),
)


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd).decode("utf-8").strip()


def check_legacy_readme_markers(readme_text: str) -> list[str]:
    """Read-only, non-fatal check for stale pre-release or private markers in README.md."""
    warnings: list[str] = []
    for pattern, label in LEGACY_README_PATTERNS:
        if re.search(pattern, readme_text, re.IGNORECASE):
            warnings.append(label)
    return warnings


class API:
    def __init__(self, repository: str, token: str, transport=None) -> None:
        require(re.fullmatch(r"[\w.-]+/[\w.-]+", repository), "Invalid GitHub repository")
        require(bool(token), "Missing GitHub API token")
        self.repository, self.token = repository, token
        self.root = "https://api.github.com/repos/" + repository
        self.transport = transport or urllib.request.urlopen

    def request(self, method: str, suffix: str = "", body=None, raw: bool = False):
        url = suffix if suffix.startswith("https://") else self.root + suffix
        host = urllib.parse.urlparse(url).hostname
        require(host in ("api.github.com", "uploads.github.com"), "Untrusted GitHub API host")
        headers = {
            "Authorization": "Bearer " + self.token,
            "User-Agent": "the-iron-oath-ptbr-release",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if raw:
            data = body
            headers["Content-Type"] = "application/zip"
        else:
            data = None if body is None else json.dumps(body).encode("utf-8")
            if data is not None:
                headers["Content-Type"] = "application/json"

        # Retry GET only; write operations fail cleanly and resume via existing draft discovery.
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method=method)
                with self.transport(req, timeout=60) as response:
                    payload = response.read()
                    return json.loads(payload) if payload else None
            except urllib.error.HTTPError as error:
                code = error.code
                error.close()
                if method == "GET" and code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise ValueError(
                    f"GitHub {method} {url}: HTTP {code}; check permissions, repository and rate limits"
                ) from error
        raise ValueError("GitHub retry limit")

    def optional(self, suffix: str):
        # 404 is valid only for explicit ref existence lookup.
        try:
            return self.request("GET", suffix)
        except ValueError as error:
            if "HTTP 404;" in str(error):
                return None
            raise

    def releases(self) -> list[dict]:
        result: list[dict] = []
        for page in range(1, 101):
            items = self.request("GET", f"/releases?per_page=100&page={page}")
            result.extend(items)
            if len(items) < 100:
                return result
        raise ValueError("Unexpected release pagination limit")


def next_version(previous: str | None) -> str:
    if previous is None:
        return "v1.0.0"
    m = VERSION.fullmatch(previous)
    require(m is not None, "Invalid release version")
    a, b, c = map(int, m.groups())
    return f"v{a}.{b}.{c + 1}"


def rows_at(ref: str, cwd: Path = ROOT) -> dict[str, dict]:
    result: dict[str, dict] = {}
    tree = git("ls-tree", "-r", "--name-only", ref, "--", "translation-work/translations", cwd=cwd)
    for name in tree.splitlines():
        if re.fullmatch(r"translation-work/translations/batch-\d{4,}\.jsonl", name):
            content = git("show", f"{ref}:{name}", cwd=cwd)
            for line in content.splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                require(row["id"] not in result, "Duplicate release row identity")
                result[row["id"]] = row
    return result


def categorize_row(row: dict) -> str:
    fmt = row.get("format", "")
    src = row.get("source_file", "").replace("\\", "/")
    if fmt == "codex_xml" or src.endswith(
        ("WorldSim/codex.json", "WorldSim/houses.json", "WorldSim/religions.json")
    ):
        return "codice"
    if fmt == "yarn" or "/Dialogue/" in src or "/QuestData/" in src or "/BarkData/" in src:
        return "dialogo"
    if fmt in ("i2_mono", "scene_mono", "textasset_xml"):
        return "interface"
    if fmt == "dll":
        return "nativo"
    return "mundo"


def changelog(
    old: dict[str, dict],
    new: dict[str, dict],
    before: dict[str, str],
    after: dict[str, str],
    repository: str,
    sha: str,
    *,
    tag: str = "v1.0.0",
    previous_tag: str | None = None,
    source_version: str = "1.0.021",
    strip_diacritics: bool = True,
) -> str:
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    revised = sorted(
        k
        for k in set(old) & set(new)
        if old[k].get("PTBR") != new[k].get("PTBR") or old[k].get("EN") != new[k].get("EN")
    )
    changed_files = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))

    groups: dict[str, int] = {}
    for rid in added + revised:
        cat = categorize_row(new[rid])
        groups[cat] = groups.get(cat, 0) + 1

    repo_url = f"https://github.com/{repository}"
    lines = [
        f"## Tradução PT-BR de *The Iron Oath* — {tag}",
        "",
        "### 📦 Download",
        "",
        f"- Baixe o arquivo **`PTBR.zip`** anexado ao final desta página de lançamento ({len(after)} arquivo(s) pronto(s) para jogo).",
        f"- Versão nativa de referência suportada nesta compilação: **{source_version}** (Steam/GOG).",
        "",
        "### ✨ O que mudou",
        "",
    ]

    if previous_tag is None:
        lines.append(
            f"Lançamento completo da tradução PT-BR cobrindo **{len(new)}** entradas localizadas."
        )
    else:
        lines.append(
            f"Comparado à versão **{previous_tag}**: **{len(added)}** entrada(s) adicionada(s), "
            f"**{len(revised)}** revisada(s), **{len(removed)}** removida(s) "
            f"(total atual: **{len(new)}** entradas)."
        )
    lines.append("")

    for cat in ("dialogo", "codice", "interface", "mundo", "nativo"):
        count = groups.get(cat, 0)
        if count:
            lines.append(f"- **{CATEGORY_LABELS[cat]}**: {count} entrada(s)")
    if not groups:
        lines.append("- Reempacotamento técnico ou ajuste em arquivos de saída sem alteração textual nos lotes.")

    lines += [
        "",
        f"Arquivos de saída atualizados nesta versão (**{len(changed_files)}**):",
        "",
    ]
    for name in changed_files[:25]:
        lines.append(f"- `{name}`")
    if len(changed_files) > 25:
        lines.append(f"- ... e mais {len(changed_files) - 25} arquivo(s).")

    diacritic_note = (
        "Os textos compilados utilizam conversão automática sem acentuação para manter 100% de "
        "legibilidade nas fontes embutidas do jogo; os arquivos de trabalho preservam a grafia acentuada."
        if strip_diacritics
        else "Compilação gerada preservando diacríticos completos nos textos de saída."
    )

    lines += [
        "",
        "### 🛠️ Como instalar ou atualizar",
        "",
        "1. Feche completamente *The Iron Oath* se o jogo estiver aberto.",
        "2. Faça uma cópia de segurança da pasta `The Iron Oath_Data` localizada na raiz de instalação do jogo.",
        "3. Extraia o conteúdo de **`PTBR.zip`** diretamente na raiz do jogo, mesclando e substituindo os arquivos dentro de `The Iron Oath_Data/`.",
        "4. Para atualizar de uma versão anterior da tradução, basta extrair o novo `PTBR.zip` por cima dos arquivos antigos ou restaurar o backup original pela loja (Steam/GOG) antes de aplicar.",
        "",
        "### 🎮 Compatibilidade e observações",
        "",
        f"- **Áudio e legendas**: as vozes originais em inglês são preservadas; menus, interface, eventos, missões, códice e sistema estão em português brasileiro.",
        f"- **Compatibilidade de fontes**: {diacritic_note}",
        "- **Verificação**: a integridade estrutural e a segurança dos arquivos nativos foram validadas antes do empacotamento; a verificação visual dentro do jogo ocorre de forma independente.",
        "",
        "### 💬 Comunidade e suporte",
        "",
        "- Encontrou um termo estranho, corte visual ou problema após uma atualização do jogo? Abra uma Issue neste repositório.",
        "- Comunidade Vai Brasil no Discord: https://discord.gg/uFEwSMwUYb",
        "- Outras traduções e projetos: https://traducao.yuricunha.com/",
        "",
        "---",
        "",
        "Apoie traduções voluntárias via Pix: `pix@yuricunha.com`",
        "",
        f"[Instalação e detalhes]({repo_url}/blob/{sha}/README.md) · "
        f"[Licença (CC BY-NC-SA 4.0)]({repo_url}/blob/{sha}/LICENSE) · "
        f"[Permissão de integração oficial]({repo_url}/blob/{sha}/GAME-DEVELOPER-PERMISSION.md) · "
        f"[Contribuir]({repo_url}/blob/{sha}/CONTRIBUTING.md)",
    ]
    return "\n".join(lines)


def publish(api: API, sha: str, outputs: dict[str, str], zip_bytes: bytes, ancestry, notes) -> dict:
    """Validate package in-memory and publish/resume serialized SemVer GitHub Release."""
    require(bool(zip_bytes) and bool(outputs), "Empty/missing ZIP or output inventory")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)) and archive.testzip() is None, "Invalid/duplicate ZIP members")
        require(
            {name: hashlib.sha256(archive.read(name)).hexdigest() for name in names} == outputs,
            "ZIP differs from verified build",
        )

    digest = "sha256:" + hashlib.sha256(zip_bytes).hexdigest()
    meta = api.request("GET")  # Canonical repository metadata endpoint has no trailing slash
    branch = urllib.parse.quote(meta["default_branch"], safe="")
    current = api.request("GET", "/commits/" + branch)["sha"]
    require(current == sha, "Stale workflow commit; default branch has advanced")

    releases = api.releases()
    published: list[tuple[tuple[int, int, int], dict]] = []
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        m = VERSION.fullmatch(release["tag_name"])
        require(m is not None, "Ambiguous non-SemVer public release")
        published.append((tuple(map(int, m.groups())), release))
    published.sort(key=lambda item: item[0])

    previous = published[-1][1] if published else None
    before: dict[str, str] = {}
    if previous:
        prior_tag = previous["tag_name"]
        ref = api.request("GET", "/git/ref/tags/" + urllib.parse.quote(prior_tag, safe=""))
        ancestry(prior_tag, ref["object"]["sha"])
        prior_receipt = api.request(
            "GET", "/contents/translation-work/build-manifest.json?ref=" + urllib.parse.quote(prior_tag, safe="")
        )
        before = json.loads(base64.b64decode(prior_receipt["content"]))["outputs"]
        require(
            {asset["name"] for asset in previous["assets"]} == {"PTBR.zip"},
            "Unexpected public release assets; manual correction required",
        )
        if before == outputs:
            existing_asset = previous["assets"][0]
            require(
                existing_asset.get("digest") == digest and existing_asset["size"] == len(zip_bytes),
                "Existing public ZIP digest differs; manual review required",
            )
            return {"status": "unchanged", "tag": prior_tag}

    tag = next_version(previous["tag_name"] if previous else None)
    ref = api.optional("/git/ref/tags/" + tag)
    require(ref is None or ref["object"]["sha"] == sha, "Existing tag points to another commit; refusing retag")
    drafts = [r for r in releases if r["tag_name"] == tag]
    require(len(drafts) <= 1 and all(r["draft"] for r in drafts), "Conflicting release state")

    body = notes(tag, previous["tag_name"] if previous else None, before)
    release = (
        drafts[0]
        if drafts
        else api.request(
            "POST",
            "/releases",
            {
                "tag_name": tag,
                "target_commitish": sha,
                "name": f"{tag} — The Iron Oath PT-BR",
                "body": body,
                "draft": True,
                "prerelease": False,
            },
        )
    )
    require(release["target_commitish"] == sha, "Draft targets a different commit")
    assets = api.request("GET", f"/releases/{release['id']}/assets")
    require(all(a["name"] == "PTBR.zip" for a in assets) and len(assets) <= 1, "Unexpected draft assets; review before retry")

    if assets:
        require(
            assets[0].get("digest") == digest and assets[0]["size"] == len(zip_bytes),
            "Draft archive differs; review before retry",
        )
    else:
        upload = release["upload_url"].split("{")[0] + "?name=PTBR.zip"
        asset = api.request("POST", upload, zip_bytes, raw=True)
        require(
            asset["name"] == "PTBR.zip" and asset.get("digest") == digest and asset["size"] == len(zip_bytes),
            "Uploaded asset verification failed",
        )

    # Re-verify branch tip before exposing draft publicly
    require(api.request("GET", "/commits/" + branch)["sha"] == sha, "Branch advanced during upload; draft preserved")
    api.request("PATCH", f"/releases/{release['id']}", {"draft": False, "body": body})
    final = api.request("GET", f"/releases/{release['id']}")
    require(
        not final["draft"]
        and len(final["assets"]) == 1
        and final["assets"][0]["name"] == "PTBR.zip"
        and final["assets"][0].get("digest") == digest
        and final["assets"][0]["size"] == len(zip_bytes),
        "Published asset set/digest mismatch",
    )
    return {"status": "published", "tag": tag, "sha256": digest}


def main() -> None:
    project = Project()
    project.verify_output()
    require(not git("status", "--porcelain", "--untracked-files=no"), "Tracked checkout differs from triggering commit")
    project.package()
    sha = git("rev-parse", "HEAD")
    require(sha == os.environ["GITHUB_SHA"], "Unexpected workflow revision")

    receipt = read_json(project.path("translation-work/build-manifest.json"))
    archive = project.path("PTBR.zip").read_bytes()
    require(bool(archive), "Empty ZIP")
    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    api = API(repository, token)

    def ancestry(tag: str, remote_sha: str) -> None:
        require(git("rev-parse", tag + "^{}") == remote_sha, "Remote/local tag mismatch")
        require(
            subprocess.run(["git", "merge-base", "--is-ancestor", tag, sha], cwd=ROOT).returncode == 0,
            "Prior release is not an ancestor",
        )

    def notes(tag: str, prev_tag: str | None, before: dict[str, str]) -> str:
        return changelog(
            rows_at(prev_tag) if prev_tag else {},
            rows_at("HEAD"),
            before,
            receipt["outputs"],
            repository,
            sha,
            tag=tag,
            previous_tag=prev_tag,
            source_version=receipt.get("native_version", "1.0.021"),
            strip_diacritics=bool(receipt.get("strip_diacritics", True)),
        )

    result = publish(api, sha, receipt["outputs"], archive, ancestry, notes)
    readme_path = project.path("README.md")
    if readme_path.is_file():
        warnings = check_legacy_readme_markers(readme_path.read_text(encoding="utf-8", errors="ignore"))
        if warnings:
            result["warnings"] = warnings
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
