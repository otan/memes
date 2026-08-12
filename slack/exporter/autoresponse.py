#!/usr/bin/env python3
"""
Upload Slackbot custom autoresponses from a ``responses.json`` file.

Auth is the same as ``slack/importer/autoresponse.py``. On **Enterprise Grid**
this undocumented API is workspace-scoped — use a browser session against the
workspace subdomain with that workspace's ``xoxc`` (``T…`` team), not an org
token.

1. **Browser session** (recommended) — workspace subdomain, HttpOnly ``d``
   cookie, and ``xoxc-…``. Calls
   ``https://<workspace>.slack.com/api/slackbot.responses.add`` with both
   ``Authorization: Bearer`` and the ``d`` cookie. See ``emojis/upload.py``.

2. **Official OAuth** — ``xoxb-`` / ``xoxp-`` plus ``--workspace`` (required on
   Enterprise; ``slack.com`` alone returns ``enterprise_is_restricted``).

Environment variables (optional): ``SLACK_TOKEN``, ``SLACK_WORKSPACE``,
``SLACK_TEAM``, ``SLACK_D_COOKIE``, ``SLACK_XOXC``, ``SLACK_AUTH_JSON``.

Reads ``{input_dir}/responses.json`` (or a JSON file path). Entries whose
trigger set already exists in the workspace print ``duplicate:`` and are
skipped. HTTP 429 waits using the same Retry-After / 10s cap as the emoji
scripts. ``--dry-run`` lists duplicates vs would-upload and does not call
``slackbot.responses.add``.

Usage::

    python3 slack/exporter/autoresponse.py ./out --dry-run \\
        --workspace myteam --cookie "$SLACK_D_COOKIE" --xoxc "$SLACK_XOXC"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

RESPONSES_NAME = "responses.json"
SKIP_KEYS = frozenset({"ok", "error", "warning", "response_metadata", "needed", "provided"})
DUPLICATE_ERRORS = {
    "already_exists",
    "duplicate",
    "error_already_exists",
    "name_taken",
}
ENTERPRISE_HINT = """\
enterprise_is_restricted: slackbot responses are per-workspace on Enterprise Grid.

Use a workspace browser session (not an org token / slack.com):
  1. Slack → Tools & settings → Customize workspace → pick the workspace
  2. --workspace is the subdomain in that tab's URL (foo.slack.com → foo)
  3. Copy the xoxc for the T… team id in localConfig_v2 (not the E… entry)
  4. Pass --workspace + --cookie + --xoxc (optional: --team T…)
"""


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


def _api_url(method: str, workspace: str | None, team: str | None) -> str:
    if workspace:
        base = f"https://{_normalize_workspace(workspace)}.slack.com/api/{method}"
    else:
        base = f"https://slack.com/api/{method}"
    if team:
        return f"{base}?{urllib.parse.urlencode({'slack_route': team})}"
    return base


def _api_headers(token: str, d_cookie: str | None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Bearer {token}",
    }
    if d_cookie:
        headers["Cookie"] = f"d={d_cookie}"
    return headers


def _extract_responses(body: dict) -> list[dict]:
    raw = body.get("responses")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    out: list[dict] = []
    for key, val in body.items():
        if key in SKIP_KEYS or not isinstance(val, dict):
            continue
        if "triggers" in val or "responses" in val:
            out.append(val)
    return out


def _as_str_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _trigger_key(triggers) -> frozenset[str]:
    return frozenset(t.strip() for t in _as_str_list(triggers) if str(t).strip())


def _label(entry: dict) -> str:
    triggers = _as_str_list(entry.get("triggers"))
    if not triggers:
        return "(no triggers)"
    shown = ", ".join(triggers[:3])
    if len(triggers) > 3:
        shown += f", +{len(triggers) - 3} more"
    return shown


def _api_error(method: str, err: object) -> SystemExit:
    if err == "enterprise_is_restricted":
        return SystemExit(ENTERPRISE_HINT)
    return SystemExit(f"{method} error: {err}")


def _workspace_from_auth_url(url: str) -> str | None:
    if not url:
        return None
    host = urllib.parse.urlparse(url).hostname or ""
    if host.endswith(".slack.com"):
        return host[: -len(".slack.com")]
    return None


def _api_call(
    method: str,
    token: str,
    fields: dict[str, str],
    *,
    workspace: str | None = None,
    d_cookie: str | None = None,
    team: str | None = None,
) -> dict:
    url = _api_url(method, workspace, team)
    headers = _api_headers(token, d_cookie)
    body_fields = dict(fields)
    if d_cookie or str(token).startswith("xoxc-"):
        body_fields["token"] = token
    if team:
        body_fields["team"] = team
    data = urllib.parse.urlencode(body_fields).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with _urlopen(req, what=method) as resp:
            payload = resp.read().decode()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} HTTP {e.code}: {e.read().decode(errors='replace')}") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"{method} failed: {e.reason}") from e
    try:
        body = json.loads(payload)
    except json.JSONDecodeError:
        raise SystemExit(f"{method} returned non-JSON: {payload}") from None
    if not body.get("ok"):
        raise _api_error(method, body.get("error", payload))
    return body


def _auth_test(
    token: str,
    *,
    workspace: str | None = None,
    d_cookie: str | None = None,
    team: str | None = None,
) -> dict:
    return _api_call(
        "auth.test",
        token,
        {},
        workspace=workspace,
        d_cookie=d_cookie,
        team=team,
    )


def _verify_workspace(
    token: str,
    *,
    workspace: str,
    d_cookie: str | None = None,
    team: str | None = None,
) -> dict:
    info = _auth_test(token, workspace=workspace, d_cookie=d_cookie, team=team)
    want = _normalize_workspace(workspace)
    got = _workspace_from_auth_url(str(info.get("url") or ""))
    team_id = info.get("team_id") or info.get("team") or "?"
    team_name = info.get("team") or "?"
    print(
        f"authed: {team_name} ({team_id}) url={info.get('url')}",
        flush=True,
    )
    if got and got != want:
        raise SystemExit(
            f"Token is authenticated to workspace {got!r}, but --workspace is {want!r}.\n"
            f"--workspace only sets the API host; the xoxc is bound to a team.\n"
            f"Switch to the {want} workspace in the Slack client, copy that T… xoxc "
            f"(and optionally --team), then retry."
        )
    if team and team_id and team != team_id and str(team_id).startswith("T"):
        raise SystemExit(
            f"--team is {team!r} but auth.test team_id is {team_id!r}. "
            f"Use the T… id for {want}."
        )
    return info


def _responses_list(
    token: str,
    *,
    workspace: str | None = None,
    d_cookie: str | None = None,
    team: str | None = None,
) -> list[dict]:
    collected: list[dict] = []
    cursor: str | None = None
    page = 0
    while True:
        page += 1
        fields = {"limit": "1000"}
        if cursor:
            fields["cursor"] = cursor
        body = _api_call(
            "slackbot.responses.list",
            token,
            fields,
            workspace=workspace,
            d_cookie=d_cookie,
            team=team,
        )
        collected.extend(_extract_responses(body))
        meta = body.get("response_metadata") or {}
        next_cursor = meta.get("next_cursor") or body.get("next_cursor") or ""
        if not next_cursor:
            break
        cursor = str(next_cursor)

    return collected


def _responses_add(
    token: str,
    triggers: list[str],
    responses: list[str],
    *,
    workspace: str | None = None,
    d_cookie: str | None = None,
    team: str | None = None,
    set_active: bool = True,
) -> tuple[str, str]:
    fields = {
        "triggers": ", ".join(triggers),
        "responses": "\n".join(responses),
        "set_active": "true" if set_active else "false",
    }
    if d_cookie or str(token).startswith("xoxc-"):
        fields["token"] = token
    if team:
        fields["team"] = team

    url = _api_url("slackbot.responses.add", workspace, team)
    headers = _api_headers(token, d_cookie)
    label = ", ".join(triggers[:3]) or "(no triggers)"

    while True:
        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with _urlopen(req, what=label) as resp:
                payload = resp.read().decode()
        except urllib.error.HTTPError as e:
            return "fail", f"HTTP {e.code}: {e.read().decode(errors='replace')}"
        except urllib.error.URLError as e:
            return "fail", str(e.reason)

        try:
            body = json.loads(payload)
        except json.JSONDecodeError:
            return "fail", payload

        if body.get("ok"):
            return "ok", label

        err = body.get("error", payload)
        if err in DUPLICATE_ERRORS:
            return "duplicate", label
        if err == "enterprise_is_restricted":
            return "fail", ENTERPRISE_HINT.strip()
        if err == "ratelimited":
            delay = 10.0
            print(f"rate limited for {label}; retrying in {delay:g}s", file=sys.stderr)
            time.sleep(delay)
            continue
        return "fail", f"{err} — {payload}"


def _load_entries(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise SystemExit(f"cannot read {path}: {e}") from e
    except json.JSONDecodeError as e:
        raise SystemExit(f"invalid JSON in {path}: {e}") from e

    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        entries = _extract_responses(data)
    else:
        raise SystemExit(f"{path}: expected object or array of autoresponses")

    cleaned: list[dict] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            print(f"skip: entry {i}: not an object", file=sys.stderr)
            continue
        triggers = [t.strip() for t in _as_str_list(entry.get("triggers")) if str(t).strip()]
        responses = [r for r in _as_str_list(entry.get("responses")) if r != ""]
        if not triggers:
            print(f"skip: entry {i}: no triggers", file=sys.stderr)
            continue
        if not responses:
            print(f"skip: entry {i}: no responses", file=sys.stderr)
            continue
        set_active = entry.get("set_active", True)
        cleaned.append(
            {
                "triggers": triggers,
                "responses": responses,
                "set_active": bool(set_active),
            }
        )
    return cleaned


def _resolve_input_path(input_path: str) -> str:
    if os.path.isdir(input_path):
        return os.path.join(input_path, RESPONSES_NAME)
    return input_path


def main() -> int:
    p = argparse.ArgumentParser(
        description="Upload Slackbot custom autoresponses from responses.json.",
    )
    p.add_argument(
        "input_path",
        help="Directory containing responses.json, or a responses.json file path",
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
        help="Workspace subdomain (e.g. acme for acme.slack.com). Required on Enterprise. Env: SLACK_WORKSPACE",
    )
    p.add_argument(
        "--team",
        default=os.environ.get("SLACK_TEAM"),
        help="Workspace team id (T…). Helps on Enterprise Grid. Env: SLACK_TEAM",
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
        help="xoxc-… cookie token for the workspace (T…), not enterprise (E…). Env: SLACK_XOXC",
    )
    p.add_argument(
        "--auth-json",
        default=os.environ.get("SLACK_AUTH_JSON"),
        help='JSON {"domain":"subdomain","token":"xoxc-...","cookie":"d-value","team":"T..."}',
    )
    p.add_argument(
        "--dry-run",
        "--dryrun",
        dest="dry_run",
        action="store_true",
        help="List duplicate vs would-upload; do not call slackbot.responses.add",
    )
    args = p.parse_args()

    workspace = args.workspace
    d_cookie = args.cookie
    token = args.token
    xoxc = args.xoxc
    team = args.team

    if args.auth_json:
        try:
            blob = json.loads(args.auth_json)
        except json.JSONDecodeError as e:
            print(f"Invalid --auth-json: {e}", file=sys.stderr)
            return 1
        workspace = workspace or blob.get("domain") or blob.get("subdomain")
        xoxc = xoxc or blob.get("token")
        d_cookie = d_cookie or blob.get("cookie")
        team = team or blob.get("team") or blob.get("team_id")

    token = token or xoxc
    d_val = _parse_d_cookie(d_cookie) if d_cookie else None
    use_session = bool(workspace and d_val and token and str(token).startswith("xoxc-"))
    use_oauth = bool(token) and not str(token).startswith("xoxc-")

    if use_session:
        session_workspace, session_cookie = workspace, d_val
    elif use_oauth:
        if not workspace:
            print(
                "Enterprise Grid and most workspaces need --workspace with this API.\n"
                "Pass --workspace <subdomain>, or use --workspace + --cookie + --xoxc.",
                file=sys.stderr,
            )
            return 1
        session_workspace, session_cookie = workspace, None
    else:
        print(
            "Need auth: --workspace + --cookie + --xoxc (recommended on Enterprise), or "
            "--token with --workspace (or --auth-json / env vars).",
            file=sys.stderr,
        )
        return 1

    path = _resolve_input_path(args.input_path)
    if not os.path.isfile(path):
        print(f"Not a file: {path}", file=sys.stderr)
        return 1

    entries = _load_entries(path)
    if not entries:
        print(f"No autoresponses found in {path}", file=sys.stderr)
        return 1

    assert session_workspace is not None
    _verify_workspace(
        token,
        workspace=session_workspace,
        d_cookie=session_cookie,
        team=team,
    )

    print("listing workspace autoresponses...", flush=True)
    existing = _responses_list(
        token,
        workspace=session_workspace,
        d_cookie=session_cookie,
        team=team,
    )
    existing_keys = {_trigger_key(item.get("triggers")) for item in existing}
    existing_keys.discard(frozenset())
    print(f"found {len(existing)} existing autoresponses", flush=True)

    total = len(entries)
    ok = dupes = failed = 0
    for i, entry in enumerate(entries, start=1):
        key = _trigger_key(entry["triggers"])
        label = _label(entry)
        prefix = f"[{i}/{total}]"
        if key in existing_keys:
            print(f"{prefix} duplicate: {label}", flush=True)
            dupes += 1
            continue
        if args.dry_run:
            print(f"{prefix} would-upload: {label}", flush=True)
            existing_keys.add(key)
            ok += 1
            continue
        print(f"{prefix} uploading: {label}...", flush=True)
        status, msg = _responses_add(
            token,
            entry["triggers"],
            entry["responses"],
            workspace=session_workspace,
            d_cookie=session_cookie,
            team=team,
            set_active=entry.get("set_active", True),
        )
        if status == "ok":
            print(f"{prefix} ok: {label}", flush=True)
            existing_keys.add(key)
            ok += 1
        elif status == "duplicate":
            print(f"{prefix} duplicate: {label}", flush=True)
            existing_keys.add(key)
            dupes += 1
        else:
            print(f"{prefix} fail: {label}: {msg}", file=sys.stderr, flush=True)
            failed += 1

    if args.dry_run:
        print(
            f"done (dry-run): {ok} would upload, {dupes} duplicate, {len(entries)} entries",
            flush=True,
        )
        return 0
    print(
        f"done: {ok} uploaded, {dupes} duplicate, {failed} failed, {len(entries)} entries",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
