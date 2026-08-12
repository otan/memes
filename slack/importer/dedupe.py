#!/usr/bin/env python3
"""
Remove numbered Slack collision copies of the same emoji.

Workspaces that imported the same pack twice end up with pairs like
``cursor-salute.png`` and ``cursor-salute-2815.png``: same picture, extra
``-<digits>`` on the name. Slack also rewrites PNG/GIF/JPEG metadata, so
the files are not byte-identical.

This script keeps the unsuffixed file and deletes the numbered one when the
decoded image data matches. Different pictures that happen to end in
``-3310`` (etc.) are left alone. Cross-format pairs (``.png`` vs ``.gif``)
are not compared and are left alone.

``aliases.json`` is updated so dropped names point at the name that was kept.

Usage::

    python3 slack/importer/dedupe.py ./out --dryrun
    python3 slack/importer/dedupe.py ./out
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
import zlib

KNOWN_EXTS = {".png", ".gif", ".jpg", ".jpeg", ".webp"}
ALIASES_NAME = "aliases.json"
# Slack name collisions look like name-2815 or name-110, not tails like -left.
COLLISION_SUFFIX = re.compile(r"^(.+)[-_](\d+)$")


def _png_pixels(data: bytes) -> bytes | None:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    i = 8
    ihdr: bytes | None = None
    idat = bytearray()
    n = len(data)
    while i + 8 <= n:
        length = struct.unpack(">I", data[i : i + 4])[0]
        ctype = data[i + 4 : i + 8]
        start, end = i + 8, i + 8 + length
        if end + 4 > n:
            return None
        chunk = data[start:end]
        if ctype == b"IHDR":
            ihdr = chunk
        elif ctype == b"IDAT":
            idat.extend(chunk)
        elif ctype == b"IEND":
            break
        i = end + 4
    if ihdr is None or not idat:
        return None
    try:
        pixels = zlib.decompress(bytes(idat))
    except zlib.error:
        return None
    return ihdr + pixels


def _copy_gif_subblocks(data: bytes, i: int, out: bytearray) -> int:
    n = len(data)
    while i < n:
        sz = data[i]
        out.append(sz)
        i += 1
        if sz == 0:
            return i
        if i + sz > n:
            return -1
        out.extend(data[i : i + sz])
        i += sz
    return -1


def _skip_gif_subblocks(data: bytes, i: int) -> int:
    n = len(data)
    while i < n:
        sz = data[i]
        i += 1
        if sz == 0:
            return i
        i += sz
        if i > n:
            return -1
    return -1


def _gif_frames(data: bytes) -> bytes | None:
    if data[:6] not in (b"GIF87a", b"GIF89a") or len(data) < 13:
        return None
    packed = data[10]
    gct = 3 * (2 << (packed & 7)) if packed & 0x80 else 0
    i = 13
    if i + gct > len(data):
        return None
    out = bytearray(data[6 : 13 + gct])
    n = len(data)
    while i < n:
        sep = data[i]
        if sep == 0x3B:
            break
        if sep == 0x21:
            if i + 2 > n:
                return None
            label = data[i + 1]
            if label == 0xFE:
                i = _skip_gif_subblocks(data, i + 2)
            else:
                out.append(0x21)
                out.append(label)
                i = _copy_gif_subblocks(data, i + 2, out)
            if i < 0:
                return None
            continue
        if sep == 0x2C:
            if i + 10 > n:
                return None
            packed_img = data[i + 9]
            lct = 3 * (2 << (packed_img & 7)) if packed_img & 0x80 else 0
            end = i + 10 + lct
            if end >= n:
                return None
            out.extend(data[i:end])
            out.append(data[end])  # LZW minimum code size
            i = _copy_gif_subblocks(data, end + 1, out)
            if i < 0:
                return None
            continue
        return None
    return bytes(out)


def _jpeg_payload(data: bytes) -> bytes | None:
    if data[:2] != b"\xff\xd8":
        return None
    out = bytearray(b"\xff\xd8")
    i = 2
    n = len(data)
    while i < n:
        if data[i] != 0xFF:
            out.extend(data[i:])
            break
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            break
        marker = data[i]
        i += 1
        if marker == 0xD9:
            out.extend(b"\xff\xd9")
            break
        if 0xD0 <= marker <= 0xD7:
            out.extend(bytes((0xFF, marker)))
            continue
        if i + 2 > n:
            return None
        seglen = struct.unpack(">H", data[i : i + 2])[0]
        if seglen < 2 or i + seglen > n:
            return None
        seg = data[i : i + seglen]
        i += seglen
        if marker == 0xFE or 0xE0 <= marker <= 0xEF:
            continue
        out.extend(bytes((0xFF, marker)))
        out.extend(seg)
        if marker == 0xDA:
            scan = i
            while i < n:
                if data[i] != 0xFF:
                    i += 1
                    continue
                if i + 1 >= n:
                    break
                nxt = data[i + 1]
                if nxt == 0x00 or 0xD0 <= nxt <= 0xD7:
                    i += 2
                    continue
                break
            out.extend(data[scan:i])
    return bytes(out)


def _webp_payload(data: bytes) -> bytes | None:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    parts: list[bytes] = []
    i = 12
    n = len(data)
    while i + 8 <= n:
        fourcc = data[i : i + 4]
        length = struct.unpack("<I", data[i + 4 : i + 8])[0]
        chunk = data[i + 8 : i + 8 + length]
        i += 8 + length + (length & 1)
        if i > n + (length & 1):
            return None
        if fourcc in (b"EXIF", b"XMP ", b"ICCP"):
            continue
        parts.append(fourcc + chunk)
    return b"".join(parts) if parts else None


def _fingerprint(path: str) -> tuple[str, str] | None:
    """Return (format, hex digest) of image pixels, ignoring metadata."""
    try:
        data = open(path, "rb").read()
    except OSError:
        return None
    ext = os.path.splitext(path)[1].lower()
    payload: bytes | None = None
    kind = ext.lstrip(".")
    if ext == ".png":
        payload = _png_pixels(data)
    elif ext == ".gif":
        payload = _gif_frames(data)
    elif ext in {".jpg", ".jpeg"}:
        payload = _jpeg_payload(data)
        kind = "jpg"
    elif ext == ".webp":
        payload = _webp_payload(data)
    if payload is None:
        payload = data
        kind = "raw"
    return kind, hashlib.sha256(payload).hexdigest()


def _list_images(directory: str) -> list[str]:
    names = []
    try:
        listing = os.listdir(directory)
    except OSError as e:
        raise SystemExit(f"cannot read {directory}: {e}") from e
    for name in listing:
        if name.startswith("."):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in KNOWN_EXTS:
            continue
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            names.append(path)
    return names


def _index_by_stem(paths: list[str]) -> dict[str, list[str]]:
    by_stem: dict[str, list[str]] = {}
    for path in paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        by_stem.setdefault(stem, []).append(path)
    return by_stem


def _same_image(path_a: str, path_b: str, cache: dict[str, tuple[str, str] | None]) -> bool | None:
    """True if same pixels, False if comparable and different, None if incomparable."""
    if path_a not in cache:
        cache[path_a] = _fingerprint(path_a)
    if path_b not in cache:
        cache[path_b] = _fingerprint(path_b)
    fa, fb = cache[path_a], cache[path_b]
    if fa is None or fb is None:
        return None
    if fa[0] != fb[0]:
        return None
    return fa[1] == fb[1]


def _load_aliases(path: str) -> dict[str, str]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"warning: could not read {path}: {e}", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _write_aliases(path: str, aliases: dict[str, str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(aliases.items())), f, indent=2)
        f.write("\n")


def _rewrite_aliases(aliases: dict[str, str], dropped_to_kept: dict[str, str]) -> dict[str, str]:
    merged = dict(aliases)
    merged.update(dropped_to_kept)
    rewritten: dict[str, str] = {}
    for name, target in merged.items():
        seen: set[str] = set()
        while target in dropped_to_kept and target not in seen:
            seen.add(target)
            target = dropped_to_kept[target]
        if name == target:
            continue
        rewritten[name] = target
    return rewritten


def find_dupes(directory: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (to_delete, different, incomparable) as (dup_path, keep_path) pairs."""
    paths = _list_images(directory)
    by_stem = _index_by_stem(paths)
    cache: dict[str, tuple[str, str] | None] = {}
    to_delete: list[tuple[str, str]] = []
    different: list[tuple[str, str]] = []
    incomparable: list[tuple[str, str]] = []

    for path in sorted(paths):
        stem = os.path.splitext(os.path.basename(path))[0]
        m = COLLISION_SUFFIX.match(stem)
        if not m:
            continue
        bases = by_stem.get(m.group(1), [])
        if not bases:
            continue
        matched: str | None = None
        saw_incomparable = False
        for base in sorted(bases):
            same = _same_image(base, path, cache)
            if same is True:
                matched = base
                break
            if same is None:
                saw_incomparable = True
            elif same is False and matched is None:
                matched = None
        if matched:
            to_delete.append((path, matched))
        elif saw_incomparable:
            incomparable.append((path, bases[0]))
        else:
            different.append((path, bases[0]))

    return to_delete, different, incomparable


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Delete Slack collision copies (name-2815, name-110) when they "
            "are the same image as the unsuffixed name."
        ),
    )
    p.add_argument(
        "directory",
        help="Directory of downloaded emoji files (from download.py)",
    )
    p.add_argument(
        "--dryrun",
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Print what would be removed without deleting or writing aliases",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Also list numbered names that were left alone",
    )
    args = p.parse_args()

    directory = os.path.abspath(args.directory)
    if not os.path.isdir(directory):
        print(f"not a directory: {directory}", file=sys.stderr)
        return 1

    to_delete, different, incomparable = find_dupes(directory)
    dropped_to_kept: dict[str, str] = {}

    for dup, keep in to_delete:
        print(f"drop: {os.path.basename(dup)} (same as {os.path.basename(keep)})")
        dropped_to_kept[os.path.splitext(os.path.basename(dup))[0]] = os.path.splitext(
            os.path.basename(keep)
        )[0]
        if not args.dry_run:
            try:
                os.remove(dup)
            except OSError as e:
                print(f"fail: {dup}: {e}", file=sys.stderr)
                return 1

    if args.verbose:
        for dup, keep in different:
            print(f"keep: {os.path.basename(dup)} (different image from {os.path.basename(keep)})")
        for dup, keep in incomparable:
            print(
                f"keep: {os.path.basename(dup)} "
                f"(cannot compare to {os.path.basename(keep)})"
            )

    alias_path = os.path.join(directory, ALIASES_NAME)
    if dropped_to_kept and not args.dry_run:
        aliases = _rewrite_aliases(_load_aliases(alias_path), dropped_to_kept)
        _write_aliases(alias_path, aliases)
        print(f"wrote {len(dropped_to_kept)} aliases to {alias_path}")

    print(
        f"done: {len(to_delete)} duplicate{'s' if len(to_delete) != 1 else ''} "
        f"{'would be removed' if args.dry_run else 'removed'}, "
        f"{len(different)} different, {len(incomparable)} incomparable"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
