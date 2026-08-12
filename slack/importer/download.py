#!/usr/bin/env python3
"""
Download every custom emoji from a Slack workspace into an output directory.

Auth is one of:

1. **Official OAuth** — a bot (``xoxb-``) or user (``xoxp-``) token with the
   ``emoji:read`` scope. Calls ``https://slack.com/api/emoji.list``.

2. **Browser session** — same trio as ``emojis/upload.py`` / emojme: workspace
   subdomain, HttpOnly ``d`` cookie, and ``xoxc-…`` cookie token. Calls
   ``https://<workspace>.slack.com/api/emoji.list``. See that script's docstring
   for how to copy the cookie and token from a logged-in browser.

Environment variables (optional): ``SLACK_TOKEN``, ``SLACK_WORKSPACE``,
``SLACK_D_COOKIE``, ``SLACK_XOXC``, ``SLACK_AUTH_JSON``.

Alias emoji (``alias:othername``) are recorded in ``aliases.json`` and not
downloaded. Image files are named ``{emoji-name}.{ext}``. Existing images
are left alone (any matching extension) unless ``--force`` is passed.

Usage::

    python3 slack/importer/download.py ./out --token "$SLACK_TOKEN"

    python3 slack/importer/download.py ./out \\
        --workspace myteam --cookie "$SLACK_D_COOKIE" --xoxc "$SLACK_XOXC"
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

CONTENT_TYPE_EXT = {
    "image/png": ".png",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
}
KNOWN_EXTS = {".png", ".gif", ".jpg", ".jpeg", ".webp"}


def _retry_delay_seconds(headers) -> float:
    retry_after = headers.get("Retry-After")
    if not retry_after:
        return 10.0
    try:
        return min(float(retry_after), 10.0)
    except ValueError:
        return 10.0


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


def _safe_name(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "_-" else "-" for c in name)
    return cleaned.strip("_-") or "emoji"


def _ext_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    ext = os.path.splitext(path)[1].lower()
    return ext if ext in KNOWN_EXTS else ""


def _existing_image(out_dir: str, safe: str) -> str | None:
    """Return a path if ``safe`` is already saved under any known image extension."""
    for ext in sorted(KNOWN_EXTS):
        path = os.path.join(out_dir, f"{safe}{ext}")
        if os.path.isfile(path):
            return path
    return None


def _ext_from_headers(headers) -> str:
    ctype = (headers.get_content_type() if hasattr(headers, "get_content_type") else "") or ""
    ctype = ctype.split(";", 1)[0].strip().lower()
    if ctype in CONTENT_TYPE_EXT:
        return CONTENT_TYPE_EXT[ctype]
    guessed = mimetypes.guess_extension(ctype) or ""
    return guessed if guessed in KNOWN_EXTS else ""


def _urlopen(req: urllib.request.Request, *, what: str):
    while True:
        try:
            return urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                delay = _retry_delay_seconds(e.headers)
                print(f"rate limited for {what}; retrying in {delay:g}s", file=sys.stderr)
                time.sleep(delay)
                continue
            raise


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
    try:
        with _urlopen(req, what="emoji.list") as resp:
            payload = resp.read().decode()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"emoji.list HTTP {e.code}: {e.read().decode(errors='replace')}") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"emoji.list failed: {e.reason}") from e

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


def _download_one(
    name: str,
    url: str,
    out_dir: str,
    d_cookie: str | None,
    force: bool,
) -> tuple[str, str | None]:
    """Return (status, message). status is ok / skip / fail."""
    safe = _safe_name(name)
    existing = _existing_image(out_dir, safe)
    if existing and not force:
        return "skip", existing

    guessed_ext = _ext_from_url(url) or ".png"
    dest = os.path.join(out_dir, f"{safe}{guessed_ext}")

    headers = {}
    if d_cookie:
        headers["Cookie"] = f"d={d_cookie}"
    req = urllib.request.Request(url, method="GET", headers=headers)

    try:
        with _urlopen(req, what=name) as resp:
            content = resp.read()
            header_ext = _ext_from_headers(resp.headers)
    except urllib.error.HTTPError as e:
        return "fail", f"HTTP {e.code}: {e.read().decode(errors='replace')}"
    except urllib.error.URLError as e:
        return "fail", str(e.reason)

    if header_ext and header_ext != guessed_ext:
        dest = os.path.join(out_dir, f"{safe}{header_ext}")

    with open(dest, "wb") as f:
        f.write(content)
    return "ok", dest


def main() -> int:
    p = argparse.ArgumentParser(
        description="Download all custom Slack workspace emoji into a directory.",
    )
    p.add_argument(
        "output_dir",
        help="Directory to create (if needed) and write emoji files into",
    )
    p.add_argument(
        "--token",
        "-t",
        default=os.environ.get("SLACK_TOKEN"),
        help="OAuth token (xoxb-/xoxp-) with emoji:read, or xoxc- with --cookie/--workspace. Env: SLACK_TOKEN",
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
        "--force",
        action="store_true",
        help="Overwrite existing files instead of skipping them",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Parallel image downloads (default: 8)",
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
        listing = _emoji_list(token, workspace=workspace, d_cookie=d_val)
    elif use_oauth:
        listing = _emoji_list(token)
        d_val = None
    else:
        print(
            "Need auth: --token (xoxb-/xoxp- with emoji:read), or "
            "--workspace + --cookie + --xoxc (or --auth-json / env vars).",
            file=sys.stderr,
        )
        return 1

    os.makedirs(args.output_dir, exist_ok=True)

    aliases: dict[str, str] = {}
    downloads: list[tuple[str, str]] = []
    for name, src in listing.items():
        if not isinstance(src, str):
            print(f"skip: {name}: unexpected value {src!r}", file=sys.stderr)
            continue
        if src.startswith("alias:"):
            aliases[name] = src[len("alias:") :]
        else:
            downloads.append((name, src))

    if aliases:
        alias_path = os.path.join(args.output_dir, "aliases.json")
        with open(alias_path, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(aliases.items())), f, indent=2)
            f.write("\n")
        print(f"wrote {len(aliases)} aliases to {alias_path}")

    ok = skip = failed = 0
    workers = max(1, args.workers)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_download_one, name, url, args.output_dir, d_val, args.force): name
            for name, url in downloads
        }
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                status, msg = fut.result()
            except Exception as e:  # noqa: BLE001 — per-emoji isolation
                print(f"fail: {name}: {e}", file=sys.stderr)
                failed += 1
                continue
            if status == "ok":
                print(f"ok: {name} -> {msg}")
                ok += 1
            elif status == "skip":
                print(f"skip: {name} ({msg})")
                skip += 1
            else:
                print(f"fail: {name}: {msg}", file=sys.stderr)
                failed += 1

    print(
        f"done: {ok} downloaded, {skip} skipped, {failed} failed, "
        f"{len(aliases)} aliases, {len(listing)} listed"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
