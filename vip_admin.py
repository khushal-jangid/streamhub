"""
StreamHub VIP Key Admin Panel (Local PC Tool)
Generates, lists, blocks, resets devices, and deletes VIP keys in Firebase Firestore (streamhub-vip).
Supports 5 Official Plans (15D Rs29, 30D Rs49, 3M Rs119 [HD/FHD], 6M Rs199, 1Y Rs349 [4K Ultra]) + 1s-60s test keys.
Usage:
  python vip_admin.py            # Launches local Admin UI at http://127.0.0.1:5059
  python vip_admin.py --list     # CLI list keys
  python vip_admin.py --create 15552000 --price 199 --allow-4k --label "6 Months 4K Ultra"
"""
import argparse
import datetime
import http.server
import json
import os
import re
import secrets
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

FIREBASE_PROJECT_ID = "streamhub-vip"
FIRESTORE_DOC_BASE = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents"
ADMIN_PORT = 5059
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "vip_admin_config.json")
RULES_PATH = os.path.join(BASE_DIR, "firestore.rules")
VIP_4K_CUTOFF_SEC = 7776000  # Strictly > 90 days (3 months) unlocks 4K by default

VIP_OFFICIAL_PLANS = [
    {"id": "15d", "name": "15 Days Starter", "duration_seconds": 1296000, "price_inr": 29, "allow_4k": False, "tag": "15D"},
    {"id": "30d", "name": "1 Month Popular", "duration_seconds": 2592000, "price_inr": 49, "allow_4k": False, "tag": "30D"},
    {"id": "3m", "name": "3 Months Value HD", "duration_seconds": 7776000, "price_inr": 119, "allow_4k": False, "tag": "3M"},
    {"id": "6m", "name": "6 Months 4K Ultra", "duration_seconds": 15552000, "price_inr": 199, "allow_4k": True, "tag": "6M4K"},
    {"id": "1y", "name": "1 Year 4K Legend", "duration_seconds": 31536000, "price_inr": 349, "allow_4k": True, "tag": "1Y4K"},
]

_TOKEN_LOCK = threading.Lock()
_OAUTH_CACHE = {"token": "", "expires_at": 0.0, "sa_path": ""}

def _load_admin_cfg():
    try:
        if os.path.isfile(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {}

def _save_admin_cfg(patch):
    cur = _load_admin_cfg()
    cur.update(patch or {})
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)
    return cur

def _is_valid_sa_json(path):
    try:
        if not path or not os.path.isfile(path):
            return False
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d.get("type") == "service_account" and bool(d.get("private_key")) and bool(d.get("client_email"))
    except Exception:
        return False

def find_service_account_json():
    saved = _load_admin_cfg().get("service_account_path", "")
    if _is_valid_sa_json(saved):
        return saved
    search_dirs = [
        BASE_DIR,
        os.path.join(os.path.expanduser("~"), "Desktop"),
        os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop"),
        os.path.join(os.path.expanduser("~"), "Downloads"),
    ]
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        try:
            for fn in os.listdir(d):
                if fn.lower().endswith(".json") and any(k in fn.lower() for k in ("firebase", "adminsdk", "streamhub-vip", "service-account")):
                    fp = os.path.join(d, fn)
                    if _is_valid_sa_json(fp):
                        _save_admin_cfg({"service_account_path": fp})
                        return fp
        except Exception:
            continue
    return ""

def _get_admin_access_token(sa_path=None):
    sp = sa_path or find_service_account_json()
    if not _is_valid_sa_json(sp):
        raise RuntimeError("Firebase Service Account JSON nahi mila — Settings me Service Account .json path set karein.")
    now = time.time()
    with _TOKEN_LOCK:
        if _OAUTH_CACHE["token"] and _OAUTH_CACHE["sa_path"] == sp and (_OAUTH_CACHE["expires_at"] - now) > 120:
            return _OAUTH_CACHE["token"]

    with open(sp, "r", encoding="utf-8") as f:
        sa = json.load(f)
    client_email = sa["client_email"]
    private_key = sa["private_key"]
    token_uri = sa.get("token_uri") or "https://oauth2.googleapis.com/token"

    import jwt
    iat = int(now)
    claim = {
        "iss": client_email,
        "scope": "https://www.googleapis.com/auth/datastore https://www.googleapis.com/auth/firebase",
        "aud": token_uri,
        "iat": iat,
        "exp": iat + 3600,
    }
    assertion = jwt.encode(claim, private_key, algorithm="RS256")
    if isinstance(assertion, bytes):
        assertion = assertion.decode("utf-8")

    form = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }).encode("utf-8")
    req = urllib.request.Request(token_uri, data=form, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        res = json.loads(r.read().decode("utf-8"))
    tok = res["access_token"]
    exp_in = int(res.get("expires_in", 3600) or 3600)
    with _TOKEN_LOCK:
        _OAUTH_CACHE.update({"token": tok, "expires_at": time.time() + exp_in, "sa_path": sp})
    return tok

def _admin_http(method, url, payload=None, sa_path=None):
    tok = _get_admin_access_token(sa_path=sa_path)
    hdrs = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    data = None
    if payload is not None:
        hdrs["Content-Type"] = "application/json; charset=utf-8"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            raw = r.read().decode("utf-8", "ignore")
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as he:
        raw = he.read().decode("utf-8", "ignore")
        try:
            err = json.loads(raw) if raw.strip() else {}
        except Exception:
            err = {"error": {"message": raw}}
        return he.code, err

def _fmt_duration_tag(sec, allow_4k=False):
    s = int(sec)
    if s < 60:
        base = f"{s}S"
    elif s < 3600:
        base = f"{s // 60}M"
    elif s < 86400:
        base = f"{s // 3600}H"
    else:
        days = s // 86400
        if days == 90:
            base = "3M"
        elif days == 180:
            base = "6M"
        elif days == 365:
            base = "1Y"
        else:
            base = f"{days}D"
    return f"{base}4K" if allow_4k else base

def _gen_vip_code(duration_seconds, allow_4k=False, custom_code=""):
    c = re.sub(r'[^A-Z0-9_-]', '', str(custom_code or "").strip().upper())
    if c:
        if len(c) < 6 or len(c) > 40:
            raise ValueError("Custom key 6 se 40 characters ka hona chahiye")
        return c
    tag = _fmt_duration_tag(duration_seconds, allow_4k=allow_4k)
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    rand1 = "".join(secrets.choice(alphabet) for _ in range(4))
    rand2 = "".join(secrets.choice(alphabet) for _ in range(4))
    return f"SHVIP-{tag}-{rand1}-{rand2}"[:40]

def _decode_doc(doc):
    f = (doc or {}).get("fields") or {}
    def _v(x):
        if not isinstance(x, dict): return None
        if "stringValue" in x: return str(x["stringValue"])
        if "integerValue" in x: return int(x["integerValue"])
        if "booleanValue" in x: return bool(x["booleanValue"])
        if "timestampValue" in x: return str(x["timestampValue"])
        if "nullValue" in x: return None
        if "arrayValue" in x: return [_v(i) for i in ((x.get("arrayValue") or {}).get("values") or [])]
        return None
    dur = int(_v(f.get("duration_seconds")) or 0)
    act_iso = _v(f.get("activated_at"))
    revoked = bool(_v(f.get("revoked")))
    devs = _v(f.get("device_ids")) or []
    explicit_4k = _v(f.get("allow_4k"))
    allow_4k = bool(explicit_4k) if explicit_4k is not None else (dur > VIP_4K_CUTOFF_SEC)
    price_inr = _v(f.get("price_inr"))
    now_ep = time.time()
    rem = 0.0
    state = "unused"
    if revoked:
        state = "blocked"
    elif act_iso:
        act_ep = datetime.datetime.fromisoformat(act_iso.replace("Z", "+00:00")).timestamp()
        rem = (act_ep + dur) - now_ep
        state = "active" if rem > 0 else "expired"
    return {
        "code": str(_v(f.get("code")) or os.path.basename(doc.get("name", ""))),
        "label": str(_v(f.get("label")) or ""),
        "duration_seconds": dur,
        "max_devices": int(_v(f.get("max_devices")) or 1),
        "allow_4k": allow_4k,
        "price_inr": int(price_inr) if price_inr is not None else None,
        "revoked": revoked,
        "device_ids": devs,
        "activated_at": act_iso,
        "created_at": _v(f.get("created_at")),
        "state": state,
        "remaining_sec": round(max(0.0, rem), 1),
    }

def create_vip_key(duration_seconds, max_devices=1, label="", custom_code="", allow_4k=None, price_inr=None):
    dur = int(duration_seconds)
    if dur < 1 or dur > 315360000:
        raise ValueError("Duration 1s se 315360000s ke beech hona chahiye")
    mdev = max(1, min(5, int(max_devices or 1)))
    eff_4k = (dur > VIP_4K_CUTOFF_SEC) if allow_4k is None else bool(allow_4k)
    code = _gen_vip_code(dur, allow_4k=eff_4k, custom_code=custom_code)
    lbl = str(label or f"{_fmt_duration_tag(dur, allow_4k=eff_4k)} VIP Pass").strip()[:120]
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fields = {
        "code": {"stringValue": code},
        "duration_seconds": {"integerValue": str(dur)},
        "max_devices": {"integerValue": str(mdev)},
        "revoked": {"booleanValue": False},
        "device_ids": {"arrayValue": {"values": []}},
        "activated_at": {"nullValue": None},
        "allow_4k": {"booleanValue": bool(eff_4k)},
        "label": {"stringValue": lbl},
        "created_at": {"timestampValue": now_iso},
    }
    if price_inr is not None and str(price_inr).strip() != "":
        pr = max(0, min(100000, int(price_inr)))
        fields["price_inr"] = {"integerValue": str(pr)}

    url = f"{FIRESTORE_DOC_BASE}/vip_keys?documentId={urllib.parse.quote(code, safe='')}"
    st, res = _admin_http("POST", url, payload={"fields": fields})
    if st not in (200, 201):
        msg = ((res or {}).get("error") or {}).get("message") or f"HTTP {st}"
        raise RuntimeError(f"Key create failed: {msg}")
    return _decode_doc(res)

def list_vip_keys():
    url = f"{FIRESTORE_DOC_BASE}/vip_keys?pageSize=200"
    st, res = _admin_http("GET", url)
    if st != 200:
        msg = ((res or {}).get("error") or {}).get("message") or f"HTTP {st}"
        raise RuntimeError(f"List keys failed: {msg}")
    items = [_decode_doc(d) for d in (res.get("documents") or [])]
    items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return items

def patch_vip_key(code, fields_dict, update_mask_paths):
    url = f"{FIRESTORE_DOC_BASE}/vip_keys/{urllib.parse.quote(code, safe='')}?" + "&".join(
        f"updateMask.fieldPaths={urllib.parse.quote(p, safe='')}" for p in update_mask_paths
    )
    st, res = _admin_http("PATCH", url, payload={"fields": fields_dict})
    if st != 200:
        msg = ((res or {}).get("error") or {}).get("message") or f"HTTP {st}"
        raise RuntimeError(f"Update failed: {msg}")
    return _decode_doc(res)

def delete_vip_key(code):
    url = f"{FIRESTORE_DOC_BASE}/vip_keys/{urllib.parse.quote(code, safe='')}"
    st, res = _admin_http("DELETE", url)
    if st not in (200, 204):
        msg = ((res or {}).get("error") or {}).get("message") or f"HTTP {st}"
        raise RuntimeError(f"Delete failed: {msg}")
    return True

ADMIN_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>StreamHub VIP Admin Panel</title>
<style>
  *{box-sizing:border-box;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
  body{margin:0;background:#09090b;color:#f4f4f5;padding:20px}
  .wrap{max-width:1060px;margin:0 auto}
  .card{background:#12131a;border:1px solid #27272a;border-radius:14px;padding:18px;margin-bottom:16px}
  h1{margin:0 0 6px;font-size:1.35rem;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .badge{font-size:.72rem;background:#eab308;color:#000;padding:3px 8px;border-radius:999px;font-weight:800}
  .sub{color:#a1a1aa;font-size:.86rem;margin-bottom:12px}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  .stats-row{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 4px}
  .stat-pill{background:#181a24;border:1px solid #27272a;border-radius:9px;padding:6px 11px;font-size:.78rem;color:#d4d4d8}
  .stat-pill b{color:#fde047}
  input,select{background:#09090b;color:#fff;border:1px solid #3f3f46;border-radius:9px;padding:9px 12px;font-size:.9rem}
  .btn{border:none;border-radius:9px;padding:9px 14px;font-weight:700;cursor:pointer;font-size:.84rem;background:#27272a;color:#fff}
  .btn:hover{background:#3f3f46}
  .btn-red{background:#e50914}.btn-red:hover{background:#c80812}
  .btn-gold{background:#eab308;color:#000}.btn-gold:hover{background:#ca8a04}
  .btn-green{background:#16a34a}.btn-sm{padding:5px 10px;font-size:.76rem}
  .plans-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:10px;margin:10px 0 14px}
  .plan-box{background:#181a24;border:1.5px solid #2d3142;border-radius:12px;padding:12px;cursor:pointer;text-align:left;color:#fff;transition:0.15s}
  .plan-box:hover{border-color:#52525b}
  .plan-box.active{border-color:#eab308;background:rgba(234,179,8,.11)}
  .plan-price{font-size:1.25rem;font-weight:900;color:#fde047;margin:2px 0}
  .plan-tag{font-size:.7rem;font-weight:800;padding:2px 7px;border-radius:999px;display:inline-block;margin-top:4px}
  .tag-hd{background:rgba(59,130,246,.2);color:#93c5fd}
  .tag-4k{background:rgba(234,179,8,.22);color:#fde047;border:1px solid rgba(234,179,8,.45)}
  .presets{display:flex;gap:7px;flex-wrap:wrap;margin:6px 0 12px}
  .preset{background:#1e202b;border:1px solid #334155;color:#e2e8f0;padding:5px 10px;border-radius:8px;cursor:pointer;font-weight:700;font-size:.76rem}
  .preset.active{background:#eab308;color:#000;border-color:#eab308}
  .msg{padding:10px 12px;border-radius:9px;margin-top:10px;font-size:.84rem;display:none}
  .msg.ok{display:block;background:rgba(34,197,94,.14);border:1px solid rgba(34,197,94,.4);color:#86efac}
  .msg.err{display:block;background:rgba(239,68,68,.14);border:1px solid rgba(239,68,68,.4);color:#fca5a5}
  table{width:100%;border-collapse:collapse;font-size:.84rem;margin-top:10px}
  th,td{padding:10px 8px;border-bottom:1px solid #27272a;text-align:left;vertical-align:middle}
  th{color:#a1a1aa;font-size:.75rem;text-transform:uppercase}
  code{font-family:ui-monospace,Consolas,monospace;font-weight:700;color:#fde047;font-size:.9rem}
  .st{padding:2px 8px;border-radius:999px;font-size:.72rem;font-weight:700;display:inline-block}
  .st-unused{background:rgba(59,130,246,.2);color:#93c5fd}
  .st-active{background:rgba(34,197,94,.2);color:#86efac}
  .st-expired{background:rgba(249,115,22,.2);color:#fdba74}
  .st-blocked{background:rgba(239,68,68,.2);color:#fca5a5}
  details summary{cursor:pointer;color:#a1a1aa;font-size:.83rem;font-weight:600}
  textarea{width:100%;height:130px;background:#09090b;color:#cbd5e1;border:1px solid #3f3f46;border-radius:8px;padding:8px;font-family:monospace;font-size:.74rem;margin-top:8px}
  #toast{position:fixed;bottom:18px;right:18px;background:#16a34a;color:#fff;padding:10px 14px;border-radius:10px;font-size:.82rem;font-weight:700;display:none;z-index:99;box-shadow:0 8px 24px rgba(0,0,0,.45)}
</style>
</head>
<body>
<div id="toast"></div>
<div class="wrap">
  <div class="card">
    <h1>StreamHub VIP Admin Panel <span class="badge">streamhub-vip • v2.6.1</span></h1>
    <div class="sub">15 Din / 30 Din / 3 Months (Sab HD/1080p Unlocked — 4K Locked) &amp; 6 Months / 1 Year (Sab + 4K Ultra HDR Unlocked). Default: 1 Key = 1 Phone.</div>
    <div class="row">
      <input id="saPath" style="flex:1;min-width:260px" placeholder="Service Account JSON path">
      <button class="btn" onclick="saveSa()">Connect JSON</button>
    </div>
    <div id="saStat" class="msg"></div>
  </div>

  <div class="card">
    <h2 style="margin:0 0 8px;font-size:1.05rem">1. Premium Plans Choose Karein (Click to Select)</h2>
    <div class="plans-grid" id="plansGrid">
      <button type="button" class="plan-box active" data-plan="15d" onclick="pickPlan(1296000,29,false,'15 Days Starter',this)">
        <div style="font-weight:800;font-size:.9rem">15 Din (Starter)</div>
        <div class="plan-price">&#8377;29</div>
        <div style="font-size:.74rem;color:#a1a1aa">All HD / 1080p</div>
        <span class="plan-tag tag-hd">4K LOCKED</span>
      </button>
      <button type="button" class="plan-box" data-plan="30d" onclick="pickPlan(2592000,49,false,'1 Month Popular',this)">
        <div style="font-weight:800;font-size:.9rem">1 Month (30 Din)</div>
        <div class="plan-price">&#8377;49</div>
        <div style="font-size:.74rem;color:#a1a1aa">All HD / 1080p</div>
        <span class="plan-tag tag-hd">4K LOCKED</span>
      </button>
      <button type="button" class="plan-box" data-plan="3m" onclick="pickPlan(7776000,119,false,'3 Months Value HD',this)">
        <div style="font-weight:800;font-size:.9rem">3 Months (90 Din)</div>
        <div class="plan-price">&#8377;119</div>
        <div style="font-size:.74rem;color:#a1a1aa">All HD / 1080p</div>
        <span class="plan-tag tag-hd">4K LOCKED</span>
      </button>
      <button type="button" class="plan-box" data-plan="6m" onclick="pickPlan(15552000,199,true,'6 Months 4K Ultra',this)">
        <div style="font-weight:800;font-size:.9rem">6 Months (180 Din)</div>
        <div class="plan-price">&#8377;199</div>
        <div style="font-size:.74rem;color:#a1a1aa">Full 4K HDR + Vault</div>
        <span class="plan-tag tag-4k">4K UNLOCKED</span>
      </button>
      <button type="button" class="plan-box" data-plan="1y" onclick="pickPlan(31536000,349,true,'1 Year 4K Legend',this)">
        <div style="font-weight:800;font-size:.9rem">1 Year (365 Din)</div>
        <div class="plan-price">&#8377;349</div>
        <div style="font-size:.74rem;color:#a1a1aa">All 4K HDR + Max</div>
        <span class="plan-tag tag-4k">4K UNLOCKED</span>
      </button>
    </div>

    <div style="font-size:.78rem;color:#a1a1aa;margin-top:4px">Quick Short Test Keys:</div>
    <div class="presets">
      <button class="preset" onclick="pickPlan(5,0,false,'5s HD Test (4K Lock)',this)">5s (HD Only)</button>
      <button class="preset" onclick="pickPlan(10,0,false,'10s HD Test (4K Lock)',this)">10s (HD Only)</button>
      <button class="preset" onclick="pickPlan(10,0,true,'10s 4K Test (All Unlock)',this)">10s (4K Unlock)</button>
      <button class="preset" onclick="pickPlan(86400,10,false,'1 Day Trial',this)">1 Day (&#8377;10)</button>
    </div>

    <div class="row">
      <label style="font-size:.8rem;color:#a1a1aa">Seconds:<br><input id="durSec" type="number" value="1296000" min="1" oninput="sync4kFromDur()" style="width:125px"></label>
      <label style="font-size:.8rem;color:#a1a1aa">Price &#8377;:<br><input id="priceInr" type="number" value="29" min="0" style="width:85px"></label>
      <label style="font-size:.8rem;color:#a1a1aa">Devices:<br><input id="maxDev" type="number" value="1" min="1" max="5" style="width:75px"></label>
      <label style="font-size:.8rem;color:#a1a1aa">Label:<br><input id="lbl" value="15 Days Starter" style="width:165px"></label>
      <label style="font-size:.8rem;color:#a1a1aa">4K Unlock:<br><select id="allow4k" style="width:135px"><option value="0">Locked (HD/FHD)</option><option value="1">Unlocked (4K)</option></select></label>
      <label style="font-size:.8rem;color:#a1a1aa">Custom Code:<br><input id="custCode" placeholder="Auto" style="width:135px;text-transform:uppercase"></label>
      <div style="align-self:flex-end"><button id="btnGenKey" class="btn btn-gold" onclick="genKey()">+ Generate Key</button></div>
    </div>
    <div id="genOut" class="msg"></div>
  </div>

  <div class="card">
    <div class="row" style="justify-content:space-between">
      <h2 style="margin:0;font-size:1.05rem">All VIP Keys</h2>
      <button class="btn btn-sm" onclick="loadKeys()">Refresh List</button>
    </div>
    <div id="statsRow" class="stats-row"></div>
    <div style="overflow-x:auto">
      <table><thead><tr><th>Key Code</th><th>Plan &amp; Price</th><th>Tier</th><th>Status</th><th>Devices</th><th>Actions</th></tr></thead><tbody id="tblBody"></tbody></table>
    </div>
  </div>

  <div class="card">
    <details>
      <summary>Firebase Firestore Rules Copy-Paste Helper</summary>
      <textarea id="rulesTxt" readonly></textarea>
      <div style="margin-top:8px"><button class="btn btn-green btn-sm" onclick="copyRules()">Copy Firestore Rules</button></div>
    </details>
  </div>
</div>
<script>
const CUTOFF_4K_SEC = 7776000;
function sync4kFromDur(){
  const s = +(document.getElementById('durSec').value || 0);
  document.getElementById('allow4k').value = s > CUTOFF_4K_SEC ? '1' : '0';
}
function pickPlan(s, price, is4k, lbl, btn){
  document.getElementById('durSec').value=s;
  document.getElementById('priceInr').value=price;
  document.getElementById('allow4k').value=is4k?'1':'0';
  document.getElementById('lbl').value=lbl;
  document.querySelectorAll('.plan-box,.preset').forEach(b=>b.classList.remove('active'));
  if(btn) btn.classList.add('active');
}
function fmtRem(s){ if(s<60) return Math.ceil(s)+'s'; if(s<3600) return Math.floor(s/60)+'m'; if(s<86400) return Math.floor(s/3600)+'h'; return Math.floor(s/86400)+'d'; }
function buildShareMsg(it){
  const qTxt = it.allow_4k ? 'Full 4K HDR + HD Unlocked' : 'All HD/1080p Unlocked (4K Locked - 6M+ me unlock)';
  const prTxt = (it.price_inr!==null&&it.price_inr!==undefined)?` (Rs ${it.price_inr})`:'';
  return `StreamHub VIP Key: ${it.code}\nPlan: ${it.label||'VIP Pass'}${prTxt}\nValidity: ${fmtRem(it.duration_seconds)} (${it.max_devices||1} Phone)\nQuality: ${qTxt}\nApp me 'VIP' button par click karke Key paste karein.`;
}
async function init(){
  const r=await (await fetch('/api/admin/init')).json();
  if(r.service_account_path) document.getElementById('saPath').value=r.service_account_path;
  if(r.rules_text) document.getElementById('rulesTxt').value=r.rules_text;
  const st=document.getElementById('saStat');
  if(r.connected){ st.className='msg ok'; st.textContent='Connected to streamhub-vip!'; loadKeys(); }
  else { st.className='msg err'; st.textContent='Service Account JSON select karein.'; }
}
async function saveSa(){
  const p=document.getElementById('saPath').value.trim();
  const r=await (await fetch('/api/admin/set_sa',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:p})})).json();
  const st=document.getElementById('saStat');
  if(r.success){ st.className='msg ok'; st.textContent='Connected cleanly!'; loadKeys(); }
  else { st.className='msg err'; st.textContent=r.error||'Failed'; }
}
let lastCreatedItem = null;
async function genKey(){
  const out=document.getElementById('genOut');
  const prVal=document.getElementById('priceInr').value;
  const r=await (await fetch('/api/admin/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    duration_seconds:+document.getElementById('durSec').value,
    price_inr:prVal===''?null:+prVal,
    allow_4k:document.getElementById('allow4k').value==='1',
    max_devices:+document.getElementById('maxDev').value,
    label:document.getElementById('lbl').value.trim(),
    custom_code:document.getElementById('custCode').value.trim()
  })})).json();
  if(r.success){
    lastCreatedItem = r.item;
    const c=r.item.code;
    const tLbl=r.item.allow_4k?'4K ULTRA':'HD/1080p Only (4K Lock)';
    out.className='msg ok';
    out.innerHTML=`Key Ready: <b style="font-size:1rem">${c}</b> (${r.item.label} • ${tLbl}) &nbsp; <button class="btn btn-sm" onclick="copyText('${c}')">Copy Key</button> <button class="btn btn-sm btn-green" onclick="copyLastMsg()">Copy Customer Msg</button>`;
    document.getElementById('custCode').value='';
    loadKeys();
  } else { out.className='msg err'; out.textContent=r.error||'Error'; }
}
function copyLastMsg(){ if(lastCreatedItem) copyText(buildShareMsg(lastCreatedItem)); }
let itemsByCode = {};
async function loadKeys(){
  const r=await (await fetch('/api/admin/list')).json();
  const tb=document.getElementById('tblBody'); tb.innerHTML='';
  const sr=document.getElementById('statsRow');
  if(!r.success){ sr.innerHTML=''; tb.innerHTML=`<tr><td colspan="6" style="color:#fca5a5">${r.error}</td></tr>`; return; }
  const items = r.items || [];
  itemsByCode = {};
  let cntAct=0, cntUn=0, cntExp=0;
  items.forEach(it=>{
    itemsByCode[it.code] = it;
    if(it.state==='active') cntAct++;
    else if(it.state==='unused') cntUn++;
    else if(it.state==='expired') cntExp++;
    const stCls='st-'+it.state;
    const stTxt=it.state==='active'?`Active (${fmtRem(it.remaining_sec)} left)`:it.state.toUpperCase();
    const prTxt=(it.price_inr!==null&&it.price_inr!==undefined)?`&#8377;${it.price_inr}`:'-';
    const tierBadge=it.allow_4k?'<span class="plan-tag tag-4k">4K ULTRA</span>':'<span class="plan-tag tag-hd">HD/1080p</span>';
    tb.innerHTML+=`<tr>
      <td><code>${it.code}</code></td>
      <td>${it.label||''}<br><small style="color:#71717a">${prTxt} • ${fmtRem(it.duration_seconds)}</small></td>
      <td>${tierBadge}</td>
      <td><span class="st ${stCls}">${stTxt}</span></td>
      <td>${(it.device_ids||[]).length} / ${it.max_devices}</td>
      <td>
        <button class="btn btn-sm" onclick="copyText('${it.code}')">Copy</button>
        <button class="btn btn-sm" onclick="copyText(buildShareMsg(itemsByCode['${it.code}']))">Msg</button>
        <button class="btn btn-sm" onclick="act('${it.code}','${it.allow_4k?'lock4k':'unlock4k'}')">${it.allow_4k?'Lock 4K':'Give 4K'}</button>
        <button class="btn btn-sm" onclick="act('${it.code}','reset')">Reset Dev</button>
        <button class="btn btn-sm" onclick="act('${it.code}','${it.revoked?'unblock':'block'}')">${it.revoked?'Unblock':'Block'}</button>
        <button class="btn btn-sm btn-red" onclick="act('${it.code}','delete')">Del</button>
      </td>
    </tr>`;
  });
  sr.innerHTML = `<span class="stat-pill">Total Keys: <b>${items.length}</b></span><span class="stat-pill">Active: <b>${cntAct}</b></span><span class="stat-pill">Unused: <b>${cntUn}</b></span><span class="stat-pill">Expired: <b>${cntExp}</b></span>`;
}
async function act(code,action){
  await fetch('/api/admin/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,action})});
  loadKeys();
}
let _toastTimer = null;
function showToast(msg){
  const el=document.getElementById('toast');
  if(!el) return;
  el.textContent=msg;
  el.style.display='block';
  clearTimeout(_toastTimer);
  _toastTimer=setTimeout(()=>{el.style.display='none';},2200);
}
function copyText(t){
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(t).then(()=>showToast('Copied cleanly!')).catch(()=>_fallbackCopy(t));
  } else { _fallbackCopy(t); }
}
function _fallbackCopy(t){
  const ta=document.createElement('textarea');
  ta.value=t; ta.style.position='fixed'; ta.style.opacity='0';
  document.body.appendChild(ta); ta.select();
  try{ document.execCommand('copy'); showToast('Copied cleanly!'); }catch(e){}
  document.body.removeChild(ta);
}
function copyRules(){ copyText(document.getElementById('rulesTxt').value); }
init();
</script></body></html>"""

class AdminHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass
    def _no_cache_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
    def _json(self, obj, code=200):
        b = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self._no_cache_headers()
        self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        if self.path == "/":
            b = ADMIN_HTML.encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(b))); self._no_cache_headers(); self.end_headers(); self.wfile.write(b)
            return
        if self.path == "/api/admin/init":
            sa = find_service_account_json()
            rules_txt = ""
            if os.path.isfile(RULES_PATH):
                with open(RULES_PATH, "r", encoding="utf-8") as f:
                    rules_txt = f.read()
            self._json({"success": True, "service_account_path": sa, "connected": bool(sa), "rules_text": rules_txt})
            return
        if self.path == "/api/admin/list":
            try:
                self._json({"success": True, "items": list_vip_keys()})
            except Exception as e:
                self._json({"success": False, "error": str(e)}, 400)
            return
        self.send_response(404); self.end_headers()
    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(ln).decode("utf-8")) if ln > 0 else {}
        try:
            if self.path == "/api/admin/set_sa":
                p = str(body.get("path") or "").strip().strip('"')
                if not _is_valid_sa_json(p):
                    self._json({"success": False, "error": "Invalid Firebase Service Account JSON path"}, 400); return
                _save_admin_cfg({"service_account_path": p})
                _get_admin_access_token(p)
                self._json({"success": True})
                return
            if self.path == "/api/admin/create":
                item = create_vip_key(
                    body.get("duration_seconds", 1296000),
                    body.get("max_devices", 1),
                    body.get("label", ""),
                    body.get("custom_code", ""),
                    allow_4k=body.get("allow_4k", None),
                    price_inr=body.get("price_inr", None),
                )
                self._json({"success": True, "item": item})
                return
            if self.path == "/api/admin/action":
                code = str(body.get("code") or "").strip()
                act = str(body.get("action") or "").strip()
                if act == "delete":
                    delete_vip_key(code)
                elif act == "reset":
                    patch_vip_key(code, {"device_ids": {"arrayValue": {"values": []}}, "activated_at": {"nullValue": None}}, ["device_ids", "activated_at"])
                elif act in ("block", "unblock"):
                    patch_vip_key(code, {"revoked": {"booleanValue": act == "block"}}, ["revoked"])
                elif act in ("unlock4k", "lock4k"):
                    patch_vip_key(code, {"allow_4k": {"booleanValue": act == "unlock4k"}}, ["allow_4k"])
                self._json({"success": True})
                return
        except Exception as e:
            self._json({"success": False, "error": str(e)}, 400)
            return
        self.send_response(404); self.end_headers()

def _is_port_running(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/admin/init", timeout=1.2) as r:
            return r.status == 200
    except Exception:
        return False

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="StreamHub VIP Admin Key Generator")
    ap.add_argument("--sa", help="Path to Firebase service account JSON")
    ap.add_argument("--create", type=int, help="Create key valid for N seconds")
    ap.add_argument("--devices", type=int, default=1, help="Max devices per key (default: 1)")
    ap.add_argument("--price", type=int, default=None, help="Plan price in INR")
    ap.add_argument("--allow-4k", dest="allow_4k", action="store_true", help="Force allow 4K Ultra")
    ap.add_argument("--no-4k", dest="allow_4k", action="store_false", help="Lock 4K Ultra")
    ap.set_defaults(allow_4k=None)
    ap.add_argument("--label", default="", help="Optional key label")
    ap.add_argument("--code", default="", help="Optional custom code")
    ap.add_argument("--list", action="store_true", help="List all VIP keys")
    ap.add_argument("--port", type=int, default=ADMIN_PORT, help="Local admin panel port")
    ap.add_argument("--no-browser", action="store_true", help="Do not auto-open browser")
    args = ap.parse_args()
    if args.sa:
        if not _is_valid_sa_json(args.sa):
            print("Invalid Service Account JSON:", args.sa); sys.exit(1)
        _save_admin_cfg({"service_account_path": os.path.abspath(args.sa)})
    if args.create:
        it = create_vip_key(args.create, args.devices, args.label, args.code, allow_4k=args.allow_4k, price_inr=args.price)
        print("CREATED VIP KEY:", it["code"], "| 4K:", it["allow_4k"], "| Rs:", it["price_inr"], "|", it["duration_seconds"], "s")
        sys.exit(0)
    if args.list:
        for it in list_vip_keys():
            print(f"{it['code']:22s} | 4K={str(it['allow_4k']):5s} | Rs{str(it['price_inr'] or '-'):4s} | {it['state']:8s} | {len(it['device_ids'])}/{it['max_devices']} | {it['label']}")
        sys.exit(0)
    url = f"http://127.0.0.1:{args.port}/"
    if _is_port_running(args.port):
        print(f"StreamHub VIP Admin Panel already running -> {url}")
        if not args.no_browser:
            webbrowser.open(url)
        sys.exit(0)
    print(f"StreamHub VIP Admin Panel -> {url}")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", args.port), AdminHandler) as srv:
        srv.serve_forever()
