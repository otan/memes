#!/usr/bin/env python3
"""
Upload images as custom emoji on a normal Slack workspace using the same Web API call
as the web app: POST https://<workspace>.slack.com/api/emoji.add (undocumented; fragile).

You need **both** of the following from an **already logged-in** browser session:

1. **``d`` cookie** (HttpOnly) — DevTools → Application → Cookies → your Slack host → ``d``.
   Can paste the raw value, or ``d=...``, or a ``Cookie`` header snippet that contains ``d=``.

2. **``xoxc-…`` token** — workspace “cookie token” sent as form field ``token``.

   Run the console on a **loaded** https://app.slack.com/client/… tab (same origin as
   ``localStorage``). Enterprise Grid IDs are often ``E…`` not ``T…``, and
   ``lastActiveTeamId`` is not always present, so the old one-liners often fail.

   **Use this snippet** (prints every ``xoxc`` if it cannot pick one team). Regex uses
   ``[/]client[/]`` so you can copy it from here without broken escapes:

   (() => {
     const raw = localStorage.getItem("localConfig_v2");
     if (!raw) {
       console.error("No localConfig_v2 — open https://app.slack.com/client/... while logged in.");
       return;
     }
     const cfg = JSON.parse(raw);
     const teams = cfg.teams || {};
     const m = document.location.pathname.match(/[/]client[/]([A-Z0-9]+)/);
     const urlId = m && m[1];
     const tok = (id) => (id && teams[id] && teams[id].token) || null;
     if (urlId && tok(urlId)) return console.log(tok(urlId));
     if (cfg.lastActiveTeamId && tok(cfg.lastActiveTeamId))
       return console.log(tok(cfg.lastActiveTeamId));
     const ids = Object.keys(teams);
     if (ids.length === 1 && tok(ids[0])) return console.log(tok(ids[0]));
     console.warn("Pick the line for your workspace. URL segment:", urlId, "team keys:", ids);
     for (const id of ids) {
       const t = tok(id);
       if (t && t.startsWith("xoxc-")) console.log(id, t);
     }
   })();

   **If that still prints nothing:** DevTools → **Network** → trigger any action → open a
   POST to ``*.slack.com/api/…`` → **Request** form data / payload → copy the ``token``
   field if it starts with ``xoxc-``. Or use automation, e.g. `maorfr/slack-token-extractor`.

Environment variables (optional): ``SLACK_WORKSPACE``, ``SLACK_D_COOKIE``, ``SLACK_XOXC``.

This is **not** the official OAuth ``admin.emoji.add`` flow. It only works for workspaces
where you can add emoji in the UI; rate limits apply.

``run_gimp.py`` prints one line per artifact as ``output:<absolute-path>``. When piping
into this script, keep only those lines (e.g. ``grep '^output:'``) so xargs does not see
GIMP or other noise; each argument may be ``output:/path/to/file.png`` — the ``output:``
prefix is stripped before opening the file.

Usage::

    ./run_emojis.sh /path/to/image.png | grep '^output:' | xargs python3 upload.py \\
        --workspace myteam --cookie "$SLACK_D_COOKIE" --xoxc "$SLACK_XOXC"
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import uuid
import urllib.error
import urllib.request

OUTPUT_PREFIX = "output:"


def _mime_for_path(path: str) -> str:
    m, _ = mimetypes.guess_type(path)
    return m or "application/octet-stream"


def _path_from_arg(arg: str) -> str:
    """Strip ``output:`` from lines emitted by ``run_gimp.run``; pass through plain paths."""
    s = arg.strip()
    if s.startswith(OUTPUT_PREFIX):
        return s[len(OUTPUT_PREFIX) :]
    return s


def _emoji_name(path: str) -> str:
    base = os.path.basename(path)
    stem = os.path.splitext(base)[0]
    cleaned = "".join(c if c.isalnum() or c in "_-" else "-" for c in stem.lower())
    return cleaned.strip("_-") or "emoji"


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


def _emoji_add(
    workspace: str,
    d_cookie: str,
    xoxc: str,
    name: str,
    image_path: str,
) -> tuple[bool, str]:
    with open(image_path, "rb") as f:
        image_bytes = f.read()

    url = f"https://{_normalize_workspace(workspace)}.slack.com/api/emoji.add"
    fields = {
        "token": xoxc,
        "name": name,
        "mode": "data",
    }
    fname = os.path.basename(image_path) or f"{name}.png"
    body, boundary = _multipart_body(fields, "image", fname, image_bytes, _mime_for_path(image_path))

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Cookie": f"d={d_cookie}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = resp.read().decode()
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode(errors='replace')}"
    except urllib.error.URLError as e:
        return False, str(e.reason)

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return False, payload

    if data.get("ok"):
        return True, name
    err = data.get("error", payload)
    return False, f"{err} — {payload}"


def main() -> int:
    p = argparse.ArgumentParser(
        description="Upload emoji via Slack web session (emoji.add + d cookie + xoxc token).",
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
        help="xoxc-… token for multipart field ``token``. Env: SLACK_XOXC",
    )
    p.add_argument(
        "--auth-json",
        default=os.environ.get("SLACK_AUTH_JSON"),
        help='JSON {"domain":"subdomain","token":"xoxc-...","cookie":"d-value"} (emojme style)',
    )
    p.add_argument(
        "files",
        nargs="+",
        help="Image paths, or output:/path lines from run_emojis.sh (prefix stripped)",
    )
    args = p.parse_args()

    workspace = args.workspace
    d_cookie = args.cookie
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

    if not workspace:
        print("Missing workspace: --workspace or SLACK_WORKSPACE or --auth-json domain", file=sys.stderr)
        return 1
    if not d_cookie:
        print("Missing cookie: --cookie or SLACK_D_COOKIE or --auth-json cookie", file=sys.stderr)
        return 1
    if not xoxc:
        print("Missing xoxc: --xoxc or SLACK_XOXC or --auth-json token", file=sys.stderr)
        return 1

    d_val = _parse_d_cookie(d_cookie)

    failed = 0
    for arg in args.files:
        path = _path_from_arg(arg)
        if not path:
            print(f"Empty path after {OUTPUT_PREFIX!r}: {arg!r}", file=sys.stderr)
            failed += 1
            continue
        if not os.path.isfile(path):
            print(f"Not a file: {path}", file=sys.stderr)
            failed += 1
            continue
        name = _emoji_name(path)
        ok, msg = _emoji_add(workspace, d_val, xoxc, name, path)
        if ok:
            print(f"ok: {name}")
        else:
            print(f"fail: {path} ({name}): {msg}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
