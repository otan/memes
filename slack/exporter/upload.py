#!/usr/bin/env python3
"""
Upload every image in a directory as custom Slack emoji.

Auth is the same as ``slack/importer/download.py``:

1. **Official OAuth** — a bot (``xoxb-``) or user (``xoxp-``) token. Calls
   ``https://slack.com/api/emoji.add`` (only works if that token can add emoji).

2. **Browser session** — workspace subdomain, HttpOnly ``d`` cookie, and
   ``xoxc-…`` cookie token. Calls ``https://<workspace>.slack.com/api/emoji.add``.
   See ``emojis/upload.py`` for how to copy the cookie and token.

Environment variables (optional): ``SLACK_TOKEN``, ``SLACK_WORKSPACE``,
``SLACK_D_COOKIE``, ``SLACK_XOXC``, ``SLACK_AUTH_JSON``.

Existing names print ``duplicate:`` and are skipped. HTTP 429 waits using the
same Retry-After / 10s cap as ``emojis/upload.py``. ``--dry-run`` lists
duplicates vs would-upload and does not call ``emoji.add``.

Usage::

    python3 slack/exporter/upload.py ./out --token "$SLACK_TOKEN"

    python3 slack/exporter/upload.py ./out --dry-run \\
        --workspace myteam --cookie "$SLACK_D_COOKIE" --xoxc "$SLACK_XOXC"
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request

IMAGE_EXTS = {".png", ".gif", ".jpg", ".jpeg", ".webp"}
DUPLICATE_ERRORS = {"error_name_taken", "error_name_taken_i18n", "name_taken"}


def _retry_delay_seconds(headers) -> float:
    retry_after = headers.get("Retry-After")
    if not retry_after:
        return 10.0

    try:
        return min(float(retry_after), 10.0)
    except ValueError:
        return 10.0


def _mime_for_path(path: str) -> str:
    m, _ = mimetypes.guess_type(path)
    return m or "application/octet-stream"


def _normalize_workspace(ws: str) -> str:
    ws = ws.strip().lower()
    if ws.endswith(".slack.com"):
        ws = ws[: -len(".slack.com")]
    return ws


def _parse_d_cookie(cookie: str) -> str:
    """Return the value of the ``d`` cookie whether given raw, as d=..., or in a Cookie header."""
    s = cookie.strip()
    if s.startswith("d="):
        return s[2:].split(";", 1)[0].strip()
    for part in s.split(";"):
        part = part.strip()
        if part.startswith("d="):
            return part[2:].strip()
    return s


def _emoji_name(path: str) -> str:
    base = os.path.basename(path)
    stem = os.path.splitext(base)[0]
    cleaned = "".join(c if c.isalnum() or c in "_-" else "-" for c in stem.lower())
    return cleaned.strip("_-") or "emoji"


def _multipart_body(
    fields: dict[str, str],
    file_field: str,
    filename: str,
    content: bytes,
    content_type: str,
) -> tuple[bytes, str]:
    boundary = f"----slackEmojiBoundary{uuid.uuid4().hex}"
    crlf = b"\r\n"
    parts: list[bytes] = []

    for key, val in fields.items():
        parts.append(f"--{boundary}".encode() + crlf)
        parts.append(f'Content-Disposition: form-data; name="{key}"'.encode() + crlf + crlf)
        parts.append(val.encode() + crlf)

    parts.append(f"--{boundary}".encode() + crlf)
    parts.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"'
        ).encode()
        + crlf
    )
    parts.append(f"Content-Type: {content_type}".encode() + crlf + crlf)
    parts.append(content + crlf)
    parts.append(f"--{boundary}--".encode() + crlf)

    return b"".join(parts), boundary


def _emoji_list(
    token: str,
    *,
    workspace: str | None = None,
    d_cookie: str | None = None,
) -> dict[str, str]:
    if workspace and d_cookie:
        url = f"https://{_normalize_workspace(workspace)}.slack.com/api/emoji.list"
        data = urllib.parse.urlencode({"token": token}).encode()
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": f"d={d_cookie}",
        }
    else:
        url = "https://slack.com/api/emoji.list"
        data = b""
        headers = {"Authorization": f"Bearer {token}"}

    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    while True:
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = resp.read().decode()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                delay = _retry_delay_seconds(e.headers)
                print(
                    f"rate limited for emoji.list; retrying in {delay:g}s",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            raise SystemExit(f"emoji.list HTTP {e.code}: {e.read().decode(errors='replace')}") from e
        except urllib.error.URLError as e:
            raise SystemExit(f"emoji.list failed: {e.reason}") from e
        break

    try:
        body = json.loads(payload)
    except json.JSONDecodeError:
        raise SystemExit(f"emoji.list returned non-JSON: {payload}") from None

    if not body.get("ok"):
        err = body.get("error", payload)
        raise SystemExit(f"emoji.list error: {err}")

    emoji = body.get("emoji")
    if not isinstance(emoji, dict):
        raise SystemExit("emoji.list response missing emoji map")
    return emoji


def _emoji_add(
    token: str,
    name: str,
    image_path: str,
    *,
    workspace: str | None = None,
    d_cookie: str | None = None,
) -> tuple[str, str]:
    with open(image_path, "rb") as f:
        image_bytes = f.read()

    fname = os.path.basename(image_path) or f"{name}.png"
    content_type = _mime_for_path(image_path)

    if workspace and d_cookie:
        url = f"https://{_normalize_workspace(workspace)}.slack.com/api/emoji.add"
        fields = {
            "token": token,
            "name": name,
            "mode": "data",
        }
        extra_headers = {"Cookie": f"d={d_cookie}"}
    else:
        url = "https://slack.com/api/emoji.add"
        fields = {
            "name": name,
            "mode": "data",
        }
        extra_headers = {"Authorization": f"Bearer {token}"}

    while True:
        body, boundary = _multipart_body(fields, "image", fname, image_bytes, content_type)
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                **extra_headers,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = resp.read().decode()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                delay = _retry_delay_seconds(e.headers)
                print(
                    f"rate limited for {name}; retrying in {delay:g}s",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            return "fail", f"HTTP {e.code}: {e.read().decode(errors='replace')}"
        except urllib.error.URLError as e:
            return "fail", str(e.reason)

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return "fail", payload

        if data.get("ok"):
            return "ok", name

        err = data.get("error", payload)
        if err in DUPLICATE_ERRORS:
            return "duplicate", name
        if err == "ratelimited":
            delay = 10.0
            print(
                f"rate limited for {name}; retrying in {delay:g}s",
                file=sys.stderr,
            )
            time.sleep(delay)
            continue
        return "fail", f"{err} — {payload}"


def _images_in_dir(input_dir: str) -> list[str]:
    if not os.path.isdir(input_dir):
        raise SystemExit(f"Not a directory: {input_dir}")
    paths: list[str] = []
    for entry in sorted(os.listdir(input_dir)):
        path = os.path.join(input_dir, entry)
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(entry)[1].lower()
        if ext in IMAGE_EXTS:
            paths.append(path)
    return paths


def main() -> int:
    p = argparse.ArgumentParser(
        description="Upload all images in a directory as Slack custom emoji.",
    )
    p.add_argument(
        "input_dir",
        help="Directory of emoji images named {emoji-name}.{ext}",
    )
    p.add_argument(
        "--token",
        "-t",
        default=os.environ.get("SLACK_TOKEN"),
        help="OAuth token (xoxb-/xoxp-), or xoxc- with --cookie/--workspace. Env: SLACK_TOKEN",
    )
    p.add_argument(
        "--workspace",
        "-w",
        default=os.environ.get("SLACK_WORKSPACE"),
        help="Workspace subdomain (e.g. acme for acme.slack.com). Env: SLACK_WORKSPACE",
    )
    p.add_argument(
        "--cookie",
        "-c",
        default=os.environ.get("SLACK_D_COOKIE"),
        help="Value of Slack ``d`` cookie (or d=… / Cookie header). Env: SLACK_D_COOKIE",
    )
    p.add_argument(
        "--xoxc",
        "-x",
        default=os.environ.get("SLACK_XOXC"),
        help="xoxc-… cookie token used as form field ``token``. Env: SLACK_XOXC",
    )
    p.add_argument(
        "--auth-json",
        default=os.environ.get("SLACK_AUTH_JSON"),
        help='JSON {"domain":"subdomain","token":"xoxc-...","cookie":"d-value"} (emojme style)',
    )
    p.add_argument(
        "--dry-run",
        "--dryrun",
        dest="dry_run",
        action="store_true",
        help="List duplicate vs would-upload; do not call emoji.add",
    )
    args = p.parse_args()

    workspace = args.workspace
    d_cookie = args.cookie
    token = args.token
    xoxc = args.xoxc

    if args.auth_json:
        try:
            blob = json.loads(args.auth_json)
        except json.JSONDecodeError as e:
            print(f"Invalid --auth-json: {e}", file=sys.stderr)
            return 1
        workspace = workspace or blob.get("domain") or blob.get("subdomain")
        xoxc = xoxc or blob.get("token")
        d_cookie = d_cookie or blob.get("cookie")

    token = token or xoxc
    d_val = _parse_d_cookie(d_cookie) if d_cookie else None
    use_session = bool(workspace and d_val and token and str(token).startswith("xoxc-"))
    use_oauth = bool(token) and not str(token).startswith("xoxc-")

    if use_session:
        session_workspace, session_cookie = workspace, d_val
    elif use_oauth:
        session_workspace, session_cookie = None, None
    else:
        print(
            "Need auth: --token (xoxb-/xoxp-), or "
            "--workspace + --cookie + --xoxc (or --auth-json / env vars).",
            file=sys.stderr,
        )
        return 1

    print("listing workspace emoji...", flush=True)
    existing = _emoji_list(token, workspace=session_workspace, d_cookie=session_cookie)
    paths = _images_in_dir(args.input_dir)
    if not paths:
        print(f"No images found in {args.input_dir}", file=sys.stderr)
        return 1

    total = len(paths)
    ok = dupes = failed = 0
    for i, path in enumerate(paths, start=1):
        name = _emoji_name(path)
        prefix = f"[{i}/{total}]"
        if name in existing:
            print(f"{prefix} duplicate: {name}", flush=True)
            dupes += 1
            continue
        if args.dry_run:
            print(f"{prefix} would-upload: {name}", flush=True)
            existing[name] = path
            ok += 1
            continue
        print(f"{prefix} uploading: {name}...", flush=True)
        status, msg = _emoji_add(
            token,
            name,
            path,
            workspace=session_workspace,
            d_cookie=session_cookie,
        )
        if status == "ok":
            print(f"{prefix} ok: {name}", flush=True)
            existing[name] = path
            ok += 1
        elif status == "duplicate":
            print(f"{prefix} duplicate: {name}", flush=True)
            existing[name] = path
            dupes += 1
        else:
            print(f"{prefix} fail: {path} ({name}): {msg}", file=sys.stderr, flush=True)
            failed += 1

    if args.dry_run:
        print(
            f"done (dry-run): {ok} would upload, {dupes} duplicate, {len(paths)} files",
            flush=True,
        )
        return 0
    print(
        f"done: {ok} uploaded, {dupes} duplicate, {failed} failed, {len(paths)} files",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
