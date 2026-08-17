#!/usr/bin/env python3
"""
Restore trailing underscores stripped from downloaded emoji filenames.

``download.py`` used to run emoji names through ``str.strip("_-")``, so a Slack
emoji named ``sus_`` was saved as ``sus.png`` instead of ``sus_.png``. This
script lists the workspace emoji, finds names that end with ``_``, and either:

- renames the stripped local file back to the real name, or
- re-downloads when the stripped name collides with another emoji (e.g. both
  ``sus`` and ``sus_`` exist, so ``sus.png`` belongs to ``sus``).

Auth is the same as ``slack/importer/download.py``.

Usage::

    python3 slack/importer/rename.py ./out --dryrun --token "$SLACK_TOKEN"

    python3 slack/importer/rename.py ./out \\
        --workspace myteam --cookie "$SLACK_D_COOKIE" --xoxc "$SLACK_XOXC"
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Prefer sibling helpers when run as ``python3 slack/importer/rename.py``.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from download import (  # noqa: E402
    KNOWN_EXTS,
    _download_one,
    _emoji_list,
    _existing_image,
    _parse_d_cookie,
)


def _legacy_safe_name(name: str) -> str:
    """Filename stem produced by the old ``_safe_name`` that stripped ``_``/``-``."""
    cleaned = "".join(c if c.isalnum() or c in "_-" else "-" for c in name)
    return cleaned.strip("_-") or "emoji"


def _already_named(directory: str, name: str) -> str | None:
    for ext in sorted(KNOWN_EXTS):
        path = os.path.join(directory, f"{name}{ext}")
        if os.path.isfile(path):
            return path
    return None


def _plan_actions(
    directory: str,
    listing: dict[str, str],
) -> list[tuple[str, ...]]:
    """
    Return actions for trailing-underscore emoji missing their correct file.

    Each action is either:
      ("rename", src_path, dest_path)
      ("download", name, url)
    """
    actions: list[tuple[str, ...]] = []
    names = set(listing)

    for name, src in sorted(listing.items()):
        if not name.endswith("_"):
            continue
        if not isinstance(src, str) or src.startswith("alias:"):
            continue
        if _already_named(directory, name):
            continue

        wrong_stem = _legacy_safe_name(name)
        collide = wrong_stem != name and wrong_stem in names
        src_path = None if collide else _existing_image(directory, wrong_stem)

        if src_path and not collide:
            ext = os.path.splitext(src_path)[1]
            dest_path = os.path.join(directory, f"{name}{ext}")
            if not os.path.exists(dest_path):
                actions.append(("rename", src_path, dest_path))
                continue

        # Collision (stripped name is another emoji) or missing stripped file:
        # the underscore emoji was never saved under its real name.
        actions.append(("download", name, src))

    return actions


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Rename or re-download emoji files that lost a trailing underscore "
            "(e.g. sus.png → sus_.png; collide with sus → re-download sus_)."
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
        help="Print renames/downloads without changing files",
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
    args = p.parse_args()

    directory = os.path.abspath(args.directory)
    if not os.path.isdir(directory):
        print(f"not a directory: {directory}", file=sys.stderr)
        return 1

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

    failed = 0
    for action in _plan_actions(directory, listing):
        kind = action[0]
        if kind == "rename":
            _, src_path, dest_path = action
            src_name = os.path.basename(src_path)
            dest_name = os.path.basename(dest_path)
            print(f"{src_name} -> {dest_name}")
            if not args.dry_run:
                try:
                    os.rename(src_path, dest_path)
                except OSError as e:
                    print(f"fail: {src_name}: {e}", file=sys.stderr)
                    failed += 1
        else:
            _, name, url = action
            print(f"download {name}")
            if not args.dry_run:
                status, msg = _download_one(name, url, directory, d_val, force=False)
                if status == "fail":
                    print(f"fail: {name}: {msg}", file=sys.stderr)
                    failed += 1

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
