#!/usr/bin/env python3
"""
Download every Slackbot custom autoresponse from a workspace into ``responses.json``.

Auth is the same as ``slack/importer/download.py``, but on **Enterprise Grid**
this undocumented API is workspace-scoped. Prefer a browser session against the
workspace subdomain (not the org / ``*.enterprise.slack.com`` host alone):

1. **Browser session** (recommended) — workspace subdomain, HttpOnly ``d``
   cookie, and the workspace's ``xoxc-…`` token (``T…`` team in
   ``localConfig_v2``, not the ``E…`` enterprise entry). Calls
   ``https://<workspace>.slack.com/api/slackbot.responses.list`` with both
   ``Authorization: Bearer`` and the ``d`` cookie. See ``emojis/upload.py`` for
   how to copy the cookie and token.

2. **Official OAuth** — a bot (``xoxb-``) or user (``xoxp-``) token. Still needs
   ``--workspace`` on Enterprise Grid; ``slack.com`` alone returns
   ``enterprise_is_restricted``.

Environment variables (optional): ``SLACK_TOKEN``, ``SLACK_WORKSPACE``,
``SLACK_TEAM``, ``SLACK_D_COOKIE``, ``SLACK_XOXC``, ``SLACK_AUTH_JSON``.

Writes ``{output_dir}/responses.json`` with a ``responses`` array of objects
containing at least ``triggers`` and ``responses``. Existing file is left alone
unless ``--force`` is passed.

Usage::

    python3 slack/importer/autoresponse.py ./out \\
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


def _normalize_entry(entry: dict) -> dict:
    triggers = entry.get("triggers") or []
    responses = entry.get("responses") or []
    if isinstance(triggers, str):
        triggers = [t.strip() for t in triggers.split(",") if t.strip()]
    if isinstance(responses, str):
        responses = [r for r in responses.split("\n") if r != ""]
    normalized: dict = {
        "triggers": list(triggers),
        "responses": list(responses),
    }
    for key in ("id", "creator", "editor", "edited", "date_created", "set_active"):
        if key in entry:
            normalized[key] = entry[key]
    return normalized


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


def _verify_workspace(
    token: str,
    *,
    workspace: str,
    d_cookie: str | None = None,
    team: str | None = None,
) -> dict:
    info = _api_call(
        "auth.test",
        token,
        {},
        workspace=workspace,
        d_cookie=d_cookie,
        team=team,
    )
    want = _normalize_workspace(workspace)
    got = _workspace_from_auth_url(str(info.get("url") or ""))
    team_id = info.get("team_id") or "?"
    team_name = info.get("team") or "?"
    print(f"authed: {team_name} ({team_id}) url={info.get('url')}", flush=True)
    if got and got != want:
        raise SystemExit(
            f"Token is authenticated to workspace {got!r}, but --workspace is {want!r}.\n"
            f"--workspace only sets the API host; the xoxc is bound to a team.\n"
            f"Switch to the {want} workspace in the Slack client, copy that T… xoxc, then retry."
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
    while True:
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

    return [_normalize_entry(item) for item in collected]


def main() -> int:
    p = argparse.ArgumentParser(
        description="Download all Slackbot custom autoresponses into responses.json.",
    )
    p.add_argument(
        "output_dir",
        help="Directory to create (if needed) and write responses.json into",
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
        "--force",
        action="store_true",
        help="Overwrite existing responses.json instead of skipping",
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
        assert workspace is not None
        _verify_workspace(token, workspace=workspace, d_cookie=d_val, team=team)
        listing = _responses_list(token, workspace=workspace, d_cookie=d_val, team=team)
    elif use_oauth:
        if not workspace:
            print(
                "Enterprise Grid and most workspaces need --workspace with this API.\n"
                "Pass --workspace <subdomain>, or use --workspace + --cookie + --xoxc.",
                file=sys.stderr,
            )
            return 1
        _verify_workspace(token, workspace=workspace, team=team)
        listing = _responses_list(token, workspace=workspace, team=team)
    else:
        print(
            "Need auth: --workspace + --cookie + --xoxc (recommended on Enterprise), or "
            "--token with --workspace (or --auth-json / env vars).",
            file=sys.stderr,
        )
        return 1

    os.makedirs(args.output_dir, exist_ok=True)
    out_path = os.path.join(args.output_dir, RESPONSES_NAME)
    if os.path.isfile(out_path) and not args.force:
        print(f"skip: {out_path} exists (pass --force to overwrite)")
        print(f"done: 0 written, {len(listing)} listed")
        return 0

    payload = {"responses": listing}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"ok: {len(listing)} autoresponses -> {out_path}")
    print(f"done: {len(listing)} written, {len(listing)} listed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
