"""Localize selected TextMeshPro labels in the original Windows 1.0.021 prefab bundle.

Usage: python translation-work/patch-prefab-labels.py ORIGINAL_PREFABS OUTPUT_PREFABS
Requires UnityPy in the build environment. The game does not require Python.
"""
from pathlib import Path
import argparse
import hashlib
import struct

from formats.scene_mono_format import apply_edits
from native import strip_diacritics


ORIGINAL_SHA256 = "eccce0f458424b529c5994b76b22ad994fd865c262d005f288b0998453dba0f9"
TMP_SCRIPT = (0, -5755350681981302373)
TEXT_OFFSET = 72
LABELS = {
    "Choose Difficulty": "Escolher Dificuldade",
    "Close": "Fechar",
    "Gear Durability": "Durabilidade",
    "Infirmary": "Enfermaria",
    "Inn": "Estalagem",
    "Journal": "Diário",
    "Leave City": "Sair da Cidade",
    "Loot Found": "Espólio Encontrado",
    "Marketplace": "Mercado",
    "Next": "Próximo",
    "Previous": "Anterior",
    "Provisions Refunded": "Provisões Reembolsadas",
    "Rarity": "Raridade",
    "Repair Materials": "Materiais de Reparo",
    "Statistics": "Estatísticas",
}


def text_field(raw: bytes) -> tuple[str, int] | None:
    if len(raw) < TEXT_OFFSET + 4 or struct.unpack_from("<iq", raw, 16) != TMP_SCRIPT:
        return None
    length = struct.unpack_from("<i", raw, TEXT_OFFSET)[0]
    start = TEXT_OFFSET + 4
    end = start + length
    if not 0 <= length <= len(raw) - start:
        return None
    return raw[start:end].decode("utf-8"), (end + 3) & ~3


def patch(
    source: Path,
    output: Path,
    *,
    strip_accents: bool = True,
    expected_sha256: str | None = ORIGINAL_SHA256,
) -> int:
    try:
        import UnityPy
    except ImportError as exc:
        raise RuntimeError("UnityPy is required to patch the prefabs bundle") from exc

    if source.resolve() == output.resolve() or output.exists():
        raise ValueError("Output must be a new file distinct from the original")
    original = source.read_bytes()
    if expected_sha256 and hashlib.sha256(original).hexdigest() != expected_sha256:
        raise ValueError("Expected an original Windows 1.0.021 prefabs bundle")
    environment = UnityPy.load(original)
    changes = {}
    found = set()
    for obj in environment.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        raw = obj.get_raw_data()
        field = text_field(raw)
        if field is None or field[0] not in LABELS:
            continue
        label, end = field
        translated = strip_diacritics(LABELS[label]) if strip_accents else LABELS[label]
        replacement = apply_edits(raw, [(TEXT_OFFSET, end, label)], {label: translated})
        changes[obj.assets_file.name, obj.path_id] = replacement
        obj.set_raw_data(replacement)
        found.add(label)
    if LABELS.keys() != found:
        raise ValueError(f"Missing prefab labels: {sorted(LABELS.keys() - found)}")

    bundle, = environment.files.values()
    result = bundle.save(packer="lz4")
    verified = UnityPy.load(result)
    original_objects = {(obj.assets_file.name, obj.path_id): obj for obj in UnityPy.load(original).objects}
    verified_objects = {(obj.assets_file.name, obj.path_id): obj for obj in verified.objects}
    if original_objects.keys() != verified_objects.keys():
        raise ValueError("Prefab object inventory changed")
    for key, obj in verified_objects.items():
        if obj.get_raw_data() != changes.get(key, original_objects[key].get_raw_data()):
            raise ValueError(f"Unexpected prefab object data: {key}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result)
    return len(changes)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(f"Patched and verified {patch(args.source, args.output)} prefab labels")
