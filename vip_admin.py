"""
StreamHub VIP Key Admin Panel (Local PC Tool)
Generates, lists, blocks, resets devices, and deletes VIP keys in Firebase Firestore (streamhub-vip).
Usage:
  python vip_admin.py            # Launches local Admin UI at http://127.0.0.1:5059
  python vip_admin.py --list     # CLI list keys
  python vip_admin.py --create 5 --label "5s Test" --devices 1
"""
import argparse
import datetime
import http.server
import json
import os
import re
import secrets
import socketserver
import string
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

def _fmt_duration_tag(sec):
    s = int(sec)
    if s < 60:
        return f"{s}S"
    if s < 3600:
        return f"{s // 60}M"
    if s < 86400:
        return f"{s // 3600}H"
    return f"{s // 86400}D"

def _gen_vip_code(duration_seconds, custom_code=""):
    c = re.sub(r'[^A-Z0-9_-]', '', str(custom_code or "").strip().upper())
    if c:
        if len(c) < 6 or len(c) > 40:
            raise ValueError("Custom key 6 se 40 characters ka hona chahiye")
        return c
    tag = _fmt_duration_tag(duration_seconds)
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
        "revoked": revoked,
        "device_ids": devs,
        "activated_at": act_iso,
        "created_at": _v(f.get("created_at")),
        "state": state,
        "remaining_sec": round(max(0.0, rem), 1),
    }

def create_vip_key(duration_seconds, max_devices=1, label="", custom_code=""):
    dur = int(duration_seconds)
    if dur < 1 or dur > 315360000:
        raise ValueError("Duration 1s se 315360000s ke beech hona chahiye")
    mdev = max(1, min(5, int(max_devices or 1)))
    code = _gen_vip_code(dur, custom_code)
    lbl = str(label or f"{_fmt_duration_tag(dur)} VIP Pass").strip()[:120]
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "fields": {
            "code": {"stringValue": code},
            "duration_seconds": {"integerValue": str(dur)},
            "max_devices": {"integerValue": str(mdev)},
            "revoked": {"booleanValue": False},
            "device_ids": {"arrayValue": {"values": []}},
            "activated_at": {"nullValue": None},
            "label": {"stringValue": lbl},
            "created_at": {"timestampValue": now_iso},
        }
    }
    url = f"{FIRESTORE_DOC_BASE}/vip_keys?documentId={urllib.parse.quote(code, safe='')}"
    st, res = _admin_http("POST", url, payload=payload)
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
  .wrap{max-width:960px;margin:0 auto}
  .card{background:#12131a;border:1px solid #27272a;border-radius:14px;padding:18px;margin-bottom:16px}
  h1{margin:0 0 6px;font-size:1.35rem;display:flex;align-items:center;gap:10px}
  .badge{font-size:.72rem;background:#eab308;color:#000;padding:3px 8px;border-radius:999px;font-weight:800}
  .sub{color:#a1a1aa;font-size:.86rem;margin-bottom:12px}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  input,select{background:#09090b;color:#fff;border:1px solid #3f3f46;border-radius:9px;padding:9px 12px;font-size:.9rem}
  .btn{border:none;border-radius:9px;padding:9px 14px;font-weight:700;cursor:pointer;font-size:.84rem;background:#27272a;color:#fff}
  .btn:hover{background:#3f3f46}
  .btn-red{background:#e50914}.btn-red:hover{background:#c80812}
  .btn-gold{background:#eab308;color:#000}.btn-gold:hover{background:#ca8a04}
  .btn-green{background:#16a34a}.btn-sm{padding:5px 10px;font-size:.76rem}
  .presets{display:flex;gap:7px;flex-wrap:wrap;margin:10px 0 12px}
  .preset{background:#1e202b;border:1px solid #334155;color:#e2e8f0;padding:6px 11px;border-radius:8px;cursor:pointer;font-weight:700;font-size:.8rem}
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
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h1>StreamHub VIP Admin Panel <span class="badge">streamhub-vip</span></h1>
    <div class="sub">1-Click Test Keys (1s, 2s, 5s, 10s) ya Long Plans banaye aur copy karke WhatsApp/Telegram pe bhejein (Default: 1 Key = 1 Phone).</div>
    <div class="row">
      <input id="saPath" style="flex:1;min-width:260px" placeholder="Service Account JSON path (e.g. Desktop\\streamhub-vip-firebase-adminsdk.json)">
      <button class="btn" onclick="saveSa()">Connect JSON</button>
    </div>
    <div id="saStat" class="msg"></div>
  </div>

  <div class="card">
    <h2 style="margin:0 0 8px;font-size:1.05rem">Generate New VIP Key</h2>
    <div class="presets" id="presetBar">
      <button class="preset" onclick="pickSec(1,'1s Test',this)">1 sec</button>
      <button class="preset" onclick="pickSec(2,'2s Test',this)">2 sec</button>
      <button class="preset active" onclick="pickSec(5,'5s Test',this)">5 sec</button>
      <button class="preset" onclick="pickSec(10,'10s Test',this)">10 sec</button>
      <button class="preset" onclick="pickSec(60,'1 Min Test',this)">1 min</button>
      <button class="preset" onclick="pickSec(86400,'1 Day VIP',this)">1 Day</button>
      <button class="preset" onclick="pickSec(604800,'7 Days VIP',this)">7 Days</button>
      <button class="preset" onclick="pickSec(2592000,'30 Days VIP',this)">30 Days</button>
      <button class="preset" onclick="pickSec(31536000,'1 Year VIP',this)">1 Year</button>
    </div>
    <div class="row">
      <label style="font-size:.8rem;color:#a1a1aa">Seconds:<br><input id="durSec" type="number" value="5" min="1" style="width:120px"></label>
      <label style="font-size:.8rem;color:#a1a1aa">Devices:<br><input id="maxDev" type="number" value="1" min="1" max="5" style="width:85px"></label>
      <label style="font-size:.8rem;color:#a1a1aa">Label:<br><input id="lbl" value="5s Test" style="width:150px"></label>
      <label style="font-size:.8rem;color:#a1a1aa">Custom Code (optional):<br><input id="custCode" placeholder="Auto Generate" style="width:175px;text-transform:uppercase"></label>
      <div style="align-self:flex-end"><button class="btn btn-gold" onclick="genKey()">+ Generate Key</button></div>
    </div>
    <div id="genOut" class="msg"></div>
  </div>

  <div class="card">
    <div class="row" style="justify-content:space-between">
      <h2 style="margin:0;font-size:1.05rem">All VIP Keys</h2>
      <button class="btn btn-sm" onclick="loadKeys()">Refresh List</button>
    </div>
    <div style="overflow-x:auto">
      <table><thead><tr><th>Key Code</th><th>Plan</th><th>Status</th><th>Devices</th><th>Actions</th></tr></thead><tbody id="tblBody"></tbody></table>
    </div>
  </div>

  <div class="card">
    <details>
      <summary>Firebase Firestore Rules Copy-Paste Helper (One-Time Console Publish)</summary>
      <div class="sub" style="margin-top:6px">Firebase Console &rarr; Firestore Database &rarr; Rules tab me ye paste karke <b>Publish</b> daba dein:</div>
      <textarea id="rulesTxt" readonly></textarea>
      <div style="margin-top:8px"><button class="btn btn-green btn-sm" onclick="copyRules()">Copy Firestore Rules</button></div>
    </details>
  </div>
</div>
<script>
function pickSec(s, lbl, btn){
  document.getElementById('durSec').value=s;
  document.getElementById('lbl').value=lbl;
  document.querySelectorAll('.preset').forEach(b=>b.classList.remove('active'));
  if(btn) btn.classList.add('active');
}
async function init(){
  const r=await (await fetch('/api/admin/init')).json();
  if(r.service_account_path) document.getElementById('saPath').value=r.service_account_path;
  if(r.rules_text) document.getElementById('rulesTxt').value=r.rules_text;
  const st=document.getElementById('saStat');
  if(r.connected){ st.className='msg ok'; st.textContent='Connected to streamhub-vip!'; loadKeys(); }
  else { st.className='msg err'; st.textContent='Service Account JSON select karein (Firebase Project Settings -> Service accounts -> Generate new private key).'; }
}
async function saveSa(){
  const p=document.getElementById('saPath').value.trim();
  const r=await (await fetch('/api/admin/set_sa',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:p})})).json();
  const st=document.getElementById('saStat');
  if(r.success){ st.className='msg ok'; st.textContent='Connected cleanly!'; loadKeys(); }
  else { st.className='msg err'; st.textContent=r.error||'Failed'; }
}
async function genKey(){
  const out=document.getElementById('genOut');
  const r=await (await fetch('/api/admin/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    duration_seconds:+document.getElementById('durSec').value,
    max_devices:+document.getElementById('maxDev').value,
    label:document.getElementById('lbl').value.trim(),
    custom_code:document.getElementById('custCode').value.trim()
  })})).json();
  if(r.success){
    const c=r.item.code;
    out.className='msg ok';
    out.innerHTML=`Key Ready: <b style="font-size:1rem">${c}</b> (${r.item.label}) &nbsp; <button class="btn btn-sm" onclick="copyText('${c}')">Copy Key</button>`;
    document.getElementById('custCode').value='';
    loadKeys();
  } else { out.className='msg err'; out.textContent=r.error||'Error'; }
}
function fmtRem(s){ if(s<60) return Math.ceil(s)+'s'; if(s<3600) return Math.floor(s/60)+'m'; if(s<86400) return Math.floor(s/3600)+'h'; return Math.floor(s/86400)+'d'; }
async function loadKeys(){
  const r=await (await fetch('/api/admin/list')).json();
  const tb=document.getElementById('tblBody'); tb.innerHTML='';
  if(!r.success){ tb.innerHTML=`<tr><td colspan="5" style="color:#fca5a5">${r.error}</td></tr>`; return; }
  (r.items||[]).forEach(it=>{
    const stCls='st-'+it.state;
    const stTxt=it.state==='active'?`Active (${fmtRem(it.remaining_sec)} left)`:it.state.toUpperCase();
    tb.innerHTML+=`<tr>
      <td><code>${it.code}</code></td>
      <td>${it.label||''}<br><small style="color:#71717a">${it.duration_seconds}s</small></td>
      <td><span class="st ${stCls}">${stTxt}</span></td>
      <td>${(it.device_ids||[]).length} / ${it.max_devices}</td>
      <td>
        <button class="btn btn-sm" onclick="copyText('${it.code}')">Copy</button>
        <button class="btn btn-sm" onclick="act('${it.code}','reset')">Reset Device</button>
        <button class="btn btn-sm" onclick="act('${it.code}','${it.revoked?'unblock':'block'}')">${it.revoked?'Unblock':'Block'}</button>
        <button class="btn btn-sm btn-red" onclick="act('${it.code}','delete')">Del</button>
      </td>
    </tr>`;
  });
}
async function act(code,action){
  await fetch('/api/admin/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,action})});
  loadKeys();
}
function copyText(t){ navigator.clipboard.writeText(t); alert('Copied: '+t); }
function copyRules(){ navigator.clipboard.writeText(document.getElementById('rulesTxt').value); alert('Firestore rules copied!'); }
init();
</script></body></html>"""

class AdminHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass
    def _json(self, obj, code=200):
        b = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        if self.path == "/":
            b = ADMIN_HTML.encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
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
                item = create_vip_key(body.get("duration_seconds", 5), body.get("max_devices", 1), body.get("label", ""), body.get("custom_code", ""))
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
                self._json({"success": True})
                return
        except Exception as e:
            self._json({"success": False, "error": str(e)}, 400)
            return
        self.send_response(404); self.end_headers()

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="StreamHub VIP Admin Key Generator")
    ap.add_argument("--sa", help="Path to Firebase service account JSON")
    ap.add_argument("--create", type=int, help="Create key valid for N seconds (e.g. 5, 10, 2592000)")
    ap.add_argument("--devices", type=int, default=1, help="Max devices per key (default: 1)")
    ap.add_argument("--label", default="", help="Optional key label")
    ap.add_argument("--code", default="", help="Optional custom code")
    ap.add_argument("--list", action="store_true", help="List all VIP keys")
    args = ap.parse_args()
    if args.sa:
        if not _is_valid_sa_json(args.sa):
            print("Invalid Service Account JSON:", args.sa); sys.exit(1)
        _save_admin_cfg({"service_account_path": os.path.abspath(args.sa)})
    if args.create:
        it = create_vip_key(args.create, args.devices, args.label, args.code)
        print("CREATED VIP KEY:", it["code"], "|", it["duration_seconds"], "s | max_devices:", it["max_devices"])
        sys.exit(0)
    if args.list:
        for it in list_vip_keys():
            print(f"{it['code']:22s} | {it['state']:8s} | {len(it['device_ids'])}/{it['max_devices']} devs | {it['duration_seconds']}s | {it['label']}")
        sys.exit(0)
    print(f"StreamHub VIP Admin Panel -> http://127.0.0.1:{ADMIN_PORT}/")
    threading.Timer(0.6, lambda: webbrowser.open(f"http://127.0.0.1:{ADMIN_PORT}/")).start()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", ADMIN_PORT), AdminHandler) as srv:
        srv.serve_forever()
