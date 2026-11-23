"""Pure-Python Unity SerializedFile (v21 / Unity 2019.4) reader and writer.

Zero third-party or C-extension dependencies: parses and rebuilds uncompressed
SerializedFile containers (`resources.assets`, `level*`, `sharedassets*.assets`),
preserving every untouched object, typetree, script reference, and external table
byte-for-byte.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct
from pathlib import Path

CLASS_ID_TEXT_ASSET = 49
CLASS_ID_MONOBEHAVIOUR = 114


class UnityAssetError(ValueError):
    """Raised when an asset file header, metadata table, or object is malformed."""


@dataclass
class AssetObject:
    path_id: int
    type_id: int
    class_id: int
    raw: bytes


@dataclass
class SerializedAsset:
    version: int
    reserved: bytes
    prefix_bytes: bytes
    types: list[int]
    objects: list[AssetObject]
    suffix_bytes: bytes


def _read_cstr(buf: bytes, pos: int) -> tuple[str, int]:
    try:
        end = buf.index(b"\x00", pos)
    except ValueError as exc:
        raise UnityAssetError("unterminated string in serialized asset metadata") from exc
    return buf[pos:end].decode("utf-8", "replace"), end + 1


def _skip_typetree_blob(buf: bytes, pos: int, version: int) -> int:
    if pos + 8 > len(buf):
        raise UnityAssetError("truncated typetree header")
    num_nodes, str_len = struct.unpack_from("<ii", buf, pos)
    if num_nodes < 0 or str_len < 0:
        raise UnityAssetError("negative typetree dimensions")
    pos += 8
    node_size = 32 if version >= 19 else 24
    pos += num_nodes * node_size + str_len
    if pos > len(buf):
        raise UnityAssetError("truncated typetree blob")
    return pos


def parse_serialized_file(raw: bytes) -> SerializedAsset:
    """Parse a Unity SerializedFile (version 17-21, little-endian objects)."""
    if len(raw) < 20:
        raise UnityAssetError("asset file smaller than 20-byte header")
    meta_size, file_size, version, data_offset, endian = struct.unpack_from(">IIIIB", raw, 0)
    reserved = raw[17:20]
    if version < 17 or version > 21:
        raise UnityAssetError(f"unsupported SerializedFile version {version} (expected 17-21)")
    if endian != 0:
        raise UnityAssetError(f"unsupported big-endian SerializedFile ({endian})")
    if file_size > len(raw) or data_offset > len(raw) or 20 + meta_size > len(raw):
        raise UnityAssetError("corrupt SerializedFile header offsets")

    pos = 20
    _unity_ver, pos = _read_cstr(raw, pos)
    _target_platform, enable_tt = struct.unpack_from("<iB", raw, pos)
    pos += 5
    type_count = struct.unpack_from("<i", raw, pos)[0]
    if type_count < 0 or type_count > 100_000:
        raise UnityAssetError(f"implausible type count {type_count}")
    pos += 4

    types: list[int] = []
    for _ in range(type_count):
        class_id, _is_stripped, _script_idx = struct.unpack_from("<iBh", raw, pos)
        pos += 7
        if class_id == CLASS_ID_MONOBEHAVIOUR:
            pos += 16  # m_ScriptID hash128
        pos += 16  # m_OldTypeHash hash128
        if enable_tt:
            pos = _skip_typetree_blob(raw, pos, version)
            if version >= 21:
                dep_count = struct.unpack_from("<i", raw, pos)[0]
                if dep_count < 0 or dep_count > 100_000:
                    raise UnityAssetError(f"implausible type dependency count {dep_count}")
                pos += 4 + dep_count * 4
        types.append(class_id)

    prefix_bytes = raw[20:pos]

    obj_count = struct.unpack_from("<i", raw, pos)[0]
    if obj_count < 0 or obj_count > 5_000_000:
        raise UnityAssetError(f"implausible object count {obj_count}")
    pos += 4

    objects: list[AssetObject] = []
    for _ in range(obj_count):
        pos = (pos + 3) & ~3
        if pos + 20 > len(raw):
            raise UnityAssetError("truncated object table entry")
        path_id, byte_start, byte_size, type_id = struct.unpack_from("<qIIi", raw, pos)
        pos += 20
        if type_id < 0 or type_id >= len(types):
            raise UnityAssetError(f"object {path_id} has invalid type_id {type_id}")
        abs_start = data_offset + byte_start
        abs_end = abs_start + byte_size
        if abs_end > len(raw):
            raise UnityAssetError(f"object {path_id} extends past end of asset file")
        objects.append(
            AssetObject(
                path_id=path_id,
                type_id=type_id,
                class_id=types[type_id],
                raw=raw[abs_start:abs_end],
            )
        )

    suffix_bytes = raw[pos : 20 + meta_size]
    return SerializedAsset(
        version=version,
        reserved=reserved,
        prefix_bytes=prefix_bytes,
        types=types,
        objects=objects,
        suffix_bytes=suffix_bytes,
    )


def rebuild_serialized_file(asset: SerializedAsset, replacements: dict[int, bytes]) -> bytes:
    """Serialize asset with selected path_id raw payloads replaced."""
    meta = bytearray(asset.prefix_bytes)
    meta += struct.pack("<i", len(asset.objects))
    data = bytearray()
    for obj in sorted(asset.objects, key=lambda o: o.path_id):
        pad = (-len(data)) % 8
        if pad:
            data.extend(b"\x00" * pad)
        byte_start = len(data)
        payload = replacements.get(obj.path_id, obj.raw)
        data.extend(payload)
        meta.extend(b"\x00" * ((-len(meta)) % 4))
        meta += struct.pack("<qIIi", obj.path_id, byte_start, len(payload), obj.type_id)
    meta += asset.suffix_bytes

    metadata_size = len(meta)
    data_offset = (20 + metadata_size + 15) & ~15
    file_size = data_offset + len(data)
    out = bytearray(struct.pack(">IIIIB", metadata_size, file_size, asset.version, data_offset, 0) + asset.reserved)
    out += meta
    if data_offset > len(out):
        out.extend(b"\x00" * (data_offset - len(out)))
    out += data
    return bytes(out)


def load_asset(path: Path) -> SerializedAsset:
    return parse_serialized_file(path.read_bytes())


def read_text_asset(raw: bytes) -> tuple[str, str, bytes]:
    """Decode TextAsset raw bytes -> (m_Name, m_Script, trailing_bytes)."""
    if len(raw) < 8:
        raise UnityAssetError("TextAsset object too short")
    nlen = struct.unpack_from("<i", raw, 0)[0]
    if nlen < 0 or 4 + nlen > len(raw):
        raise UnityAssetError("bad TextAsset m_Name length")
    name = raw[4 : 4 + nlen].decode("utf-8", "replace")
    pos = (4 + nlen + 3) & ~3
    if pos + 4 > len(raw):
        raise UnityAssetError("missing TextAsset m_Script length")
    slen = struct.unpack_from("<i", raw, pos)[0]
    pos += 4
    if slen < 0 or pos + slen > len(raw):
        raise UnityAssetError("bad TextAsset m_Script length")
    script = raw[pos : pos + slen].decode("utf-8", "replace")
    pos = (pos + slen + 3) & ~3
    return name, script, raw[pos:]


def encode_text_asset(name: str, script: str, trailing: bytes = b"") -> bytes:
    """Serialize TextAsset raw bytes preserving trailing metadata."""
    nb = name.encode("utf-8")
    sb = script.encode("utf-8")
    out = bytearray()
    out += struct.pack("<i", len(nb)) + nb + b"\x00" * ((-len(nb)) % 4)
    out += struct.pack("<i", len(sb)) + sb + b"\x00" * ((-len(sb)) % 4)
    out += trailing
    return bytes(out)


def build_minimal_assets(objects_spec: list[tuple[int, int, bytes]]) -> bytes:
    """Build a minimal valid v21 SerializedFile for tests/preflight.

    objects_spec: list of (path_id, class_id, raw_bytes).
    """
    unique_classes: list[int] = []
    for _pid, cid, _raw in objects_spec:
        if cid not in unique_classes:
            unique_classes.append(cid)

    prefix = bytearray(b"2019.4.20f1\x00")
    prefix += struct.pack("<iB", 19, 0)  # Win64, enable_type_tree=False
    prefix += struct.pack("<i", len(unique_classes))
    for cid in unique_classes:
        prefix += struct.pack("<iBh", cid, 0, -1)
        if cid == CLASS_ID_MONOBEHAVIOUR:
            prefix += b"\x00" * 16
        prefix += b"\x00" * 16

    suffix = bytearray()
    suffix += struct.pack("<i", 0)  # script_types
    suffix += struct.pack("<i", 0)  # externals
    suffix += struct.pack("<i", 0)  # ref_types
    suffix += b"\x00"               # userInformation

    class_to_tid = {cid: idx for idx, cid in enumerate(unique_classes)}
    objs = [
        AssetObject(path_id=pid, type_id=class_to_tid[cid], class_id=cid, raw=raw)
        for pid, cid, raw in objects_spec
    ]
    asset = SerializedAsset(
        version=21,
        reserved=b"\x00\x00\x00",
        prefix_bytes=bytes(prefix),
        types=unique_classes,
        objects=objs,
        suffix_bytes=bytes(suffix),
    )
    return rebuild_serialized_file(asset, {})
