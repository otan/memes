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
  console.log("URL segment:", urlId);
  for (const id of Object.keys(teams)) {
    const t = tok(id);
    if (t && t.startsWith("xoxc-")) {
      const kind = id.startsWith("T") ? "WORKSPACE (use this)" : id.startsWith("E") ? "ENTERPRISE (skip)" : "other";
      console.log(kind, id, t);
    }
  }
})();
