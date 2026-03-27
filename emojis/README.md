# emojis

* Run `brew install gimp`
* Get a transparent background, 64x64 (or 128x128) size of the target.
  * images should be an unindexed PNG. if they aren't, my quick trick is to convert to webp and back
* Do **not** run `python run_gimp.py` — `gimpfu` only exists inside GIMP. From this directory run:
```sh
./run_emojis.sh /path/to/your.png
```
  Or manually:
```sh
cd emojis
gimp -idf --batch-interpreter python-fu-eval \
    -b "import sys;sys.path=['.']+sys.path;import run_gimp;run_gimp.run('/Users/otan/Downloads/nathan.png')" \
    -b "pdb.gimp_quit(1)"
```
* The emojis should now be in the same directory
* Upload all at once (uses your **logged-in Slack browser session**, not OAuth):
  * You need the workspace **subdomain** (e.g. `acme` for acme.slack.com), the HttpOnly **`d`** cookie, and an **`xoxc-…`** token (same idea as [emojme](https://github.com/jackellenberger/emojme)—see `upload.py` docstring for where to copy them).
```sh
./run_emojis.sh /path/to/image.png | grep 'output:' | xargs python3 upload.py \
  --workspace YOUR_SUBDOMAIN \
  --cookie "$SLACK_D_COOKIE" \
  --xoxc "$SLACK_XOXC"
```
  `run_gimp.py` prints each file as `output:<path>` (e.g. `output:/tmp/foo-intensifies.gif`). Only lines starting with `output:` are real outputs—**`grep '^output:'`** drops anything else on stdout before `xargs`. `upload.py` strips the `output:` prefix and uploads the path you can also pass plain paths with no prefix by hand.
  Or set `SLACK_WORKSPACE`, `SLACK_D_COOKIE`, and `SLACK_XOXC` and omit the flags. `--auth-json` accepts an emojme-style blob: `{"domain":"…","token":"xoxc-…","cookie":"…"}`.

  xoxc:

```

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
```

This script was originally from William Ho.
