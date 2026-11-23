"""Require affirmative, attributable contributor consent; never infer it from submission."""
from __future__ import annotations

import json
import os
from pathlib import Path

REQUIRED_PERMISSION = "CC-BY-NC-SA-4.0 + official-game-commercial-integration"
CONSENT_LINE = (
    "Li CONTRIBUTING.md e GAME-DEVELOPER-PERMISSION.md. Autorizo de forma não exclusiva "
    "e gratuita o uso, edição e redistribuição das minhas contribuições originais deste PR "
    "sob CC BY-NC-SA 4.0 e a concessão da permissão adicional de integração oficial comercial "
    "à desenvolvedora/editora original de *The Iron Oath*, com créditos, sem pagamentos ou "
    "novas aprovações individuais. Declaro deter os direitos necessários."
)


def consent(pr: dict) -> bool:
    """Return True only when PR metadata and checked consent box are present."""
    if not isinstance(pr, dict):
        return False
    body = pr.get("body") or ""
    login = (pr.get("user") or {}).get("login")
    head_sha = (pr.get("head") or {}).get("sha")
    if not (pr.get("number") and login and head_sha):
        return False
    return any(
        line.strip().replace("`", "") in (f"- [x] {CONSENT_LINE}", f"- [X] {CONSENT_LINE}")
        for line in body.splitlines()
    )


def validate_consent_record(record: dict) -> bool:
    """Validate an auditable contributor-consent record for commercial integration."""
    if not isinstance(record, dict):
        return False
    return (
        bool(record.get("identity"))
        and bool(record.get("pull_request"))
        and bool(record.get("scope"))
        and record.get("permission") == REQUIRED_PERMISSION
        and record.get("affirmative") is True
    )


def verify_pr_consent(pr: dict, root: Path | None = None) -> tuple[bool, str]:
    """Validate PR attestation and optional matching consent record file."""
    if not consent(pr):
        return (
            False,
            "Consentimento afirmativo ausente. Leia CONTRIBUTING.md e marque a declaração no PR.",
        )
    if root is not None:
        rec_path = root / "translation-work" / "contributor-consents" / f"PR-{pr['number']}.json"
        if rec_path.is_file():
            try:
                rec = json.loads(rec_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return False, f"Registro de consentimento inválido em {rec_path.name}: {exc}"
            if not validate_consent_record(rec) or rec.get("identity") != pr["user"]["login"]:
                return False, f"Registro de consentimento incompleto ou incompatível em {rec_path.name}."
    return (
        True,
        f"Consentimento registrado no PR #{pr['number']}: {pr['user']['login']} / {pr['head']['sha']}; revisão de direitos ainda necessária.",
    )


if __name__ == "__main__":
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_path or not Path(event_path).is_file():
        raise SystemExit("GITHUB_EVENT_PATH ausente ou inacessível.")
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    pr = event.get("pull_request") or {}
    ok, msg = verify_pr_consent(pr, Path(__file__).resolve().parent.parent.parent)
    if not ok:
        raise SystemExit(msg)
    print(msg)
