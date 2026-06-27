import os
import sqlite3
import time
import glob
from pathlib import Path

from flask import jsonify, render_template_string

BASE_DIR = Path(__file__).resolve().parents[1]   # /opt/teslacam-plate-dashboard
DATA_DIR = Path(os.environ.get("PLATEWATCH_DATA_DIR", str(BASE_DIR / "data")))

def _guess_db_path() -> str:
    env = os.environ.get("PLATEWATCH_DB_PATH")
    if env and os.path.exists(env):
        return env

    candidates = [
        DATA_DIR / "platewatch.db",
        DATA_DIR / "plates.db",
        DATA_DIR / "app.db",
        DATA_DIR / "database.db",
    ]
    for c in candidates:
        if c.exists():
            return str(c)

    if DATA_DIR.exists():
        for n in DATA_DIR.iterdir():
            if n.is_file() and n.name.lower().endswith(".db"):
                return str(n)

    return str(DATA_DIR / "platewatch.db")


def _media_dirs():
    media = DATA_DIR / "media"
    thumbs = media / "thumbs"
    overlays = media / "overlays"
    return thumbs, overlays


def clear_database(keep_settings: bool = True) -> dict:
    db_path = _guess_db_path()
    if not os.path.exists(db_path):
        return {"ok": False, "error": f"DB not found: {db_path}"}

    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("PRAGMA busy_timeout=8000;")
    cur.execute("PRAGMA foreign_keys=OFF;")

    tables = [r["name"] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]

    keep = set()
    if keep_settings:
        for t in tables:
            if t.lower() in ("settings", "config", "configs"):
                keep.add(t)

    for t in tables:
        if t.startswith("sqlite_") or t.lower() in ("alembic_version", "schema_migrations"):
            keep.add(t)

    wipe = [t for t in tables if t not in keep]
    deleted = {}

    try:
        cur.execute("BEGIN;")
        for t in wipe:
            try:
                cur.execute(f'DELETE FROM "{t}";')
                deleted[t] = cur.rowcount if cur.rowcount is not None else 0
            except Exception:
                # ignore weird tables/views
                continue

        # Reset AUTOINCREMENT counters if present
        try:
            for t in wipe:
                cur.execute("DELETE FROM sqlite_sequence WHERE name = ?;", (t,))
        except Exception:
            pass

        conn.commit()

        # Optional shrink
        try:
            cur.execute("VACUUM;")
        except Exception:
            pass

    except Exception as e:
        conn.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()

    thumbs, overlays = _media_dirs()
    removed = 0
    for pat in (str(thumbs / "*.jpg"), str(overlays / "*.png"), str(overlays / "*.jpg")):
        for p in glob.glob(pat):
            try:
                os.remove(p)
                removed += 1
            except Exception:
                pass

    return {
        "ok": True,
        "db_path": db_path,
        "tables_wiped": sorted(deleted.keys()),
        "rows_deleted_est": deleted,
        "media_files_removed": removed,
        "kept_tables": sorted(keep),
        "ts": int(time.time()),
    }


ADMIN_PAGE = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Admin • Clear Database</title>
  <style>
    body{background:#0b1220;color:#e5e7eb;font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;margin:0}
    .wrap{max-width:920px;margin:40px auto;padding:0 18px}
    .card{background:#0f1b2e;border:1px solid rgba(255,255,255,.08);border-radius:18px;padding:18px}
    .btn{background:#ef4444;border:none;color:#fff;border-radius:12px;padding:10px 14px;font-weight:800;cursor:pointer}
    .btn:disabled{opacity:.45;cursor:not-allowed}
    input{background:#0b1220;border:1px solid rgba(255,255,255,.12);border-radius:12px;color:#e5e7eb;padding:10px 12px;width:240px}
    .muted{color:#93a4b8}
    .row{display:flex;gap:14px;align-items:center;flex-wrap:wrap}
    a{color:#60a5fa;text-decoration:none}
    pre{white-space:pre-wrap;background:#0b1220;border:1px solid rgba(255,255,255,.08);padding:12px;border-radius:12px}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="row" style="justify-content:space-between">
        <h2 style="margin:0">Clear Database</h2>
        <a href="/">← Back to Dashboard</a>
      </div>
      <p class="muted">
        Deletes detection/clip/plate rows (keeps settings if present) and removes generated thumbnails/overlays.
      </p>

      <div class="row">
        <label for="confirm">Type <b>CLEAR</b> to enable:</label>
        <input id="confirm" placeholder="CLEAR" />
        <button id="btn" class="btn" disabled>Clear Database</button>
      </div>

      <h3 style="margin-top:18px">Result</h3>
      <pre id="out" class="muted">Waiting…</pre>
    </div>
  </div>

<script>
  const confirmInput = document.getElementById('confirm');
  const btn = document.getElementById('btn');
  const out = document.getElementById('out');

  confirmInput.addEventListener('input', () => {
    btn.disabled = (confirmInput.value.trim().toUpperCase() !== 'CLEAR');
  });

  btn.addEventListener('click', async () => {
    btn.disabled = true;
    out.textContent = "Clearing…";
    try {
      const r = await fetch('/api/admin/clear_db', {method:'POST'});
      const j = await r.json();
      out.textContent = JSON.stringify(j, null, 2);
    } catch (e) {
      out.textContent = "ERROR: " + e;
    } finally {
      confirmInput.value = "";
      btn.disabled = true;
    }
  });
</script>
</body>
</html>
"""


def register_admin_routes(app):
    # idempotent: safe if called multiple times
    if "admin_clear_db_api" in app.view_functions or "admin_clear_db_page" in app.view_functions:
        return

    @app.get("/admin", endpoint="admin_clear_db_page")
    def admin_page():
        return render_template_string(ADMIN_PAGE)

    @app.post("/api/admin/clear_db", endpoint="admin_clear_db_api")
    def api_clear_db():
        return jsonify(clear_database(keep_settings=True))

    @app.get("/api/admin/routes", endpoint="admin_routes_list")
    def admin_routes_list():
        rules = []
        for r in app.url_map.iter_rules():
            rules.append({
                "rule": str(r),
                "methods": sorted([m for m in r.methods if m not in ("HEAD","OPTIONS")]),
                "endpoint": r.endpoint
            })
        rules.sort(key=lambda x: x["rule"])
        return jsonify({"ok": True, "count": len(rules), "routes": rules})

