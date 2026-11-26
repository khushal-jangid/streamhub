import http.server
import socket
import socketserver
import urllib.request
import urllib.parse
import json
import re
import base64
import hashlib
import html as _html
import io
import os
import sys
import struct
import zlib
import subprocess
import shutil
import uuid
import gzip as _gzip
import tempfile
import pathlib
import webbrowser
import time
import threading
import concurrent.futures
from collections import OrderedDict

try:
    import certifi as _certifi
    _ca_path = _certifi.where()
    if _ca_path and os.path.exists(_ca_path):
        os.environ.setdefault("SSL_CERT_FILE", _ca_path)
        import ssl as _ssl
        _orig_ssl_ctx = _ssl.create_default_context
        def _certifi_ssl_ctx(*args, **kwargs):
            if not args and "cafile" not in kwargs and "capath" not in kwargs and "cadata" not in kwargs:
                kwargs["cafile"] = _ca_path
            return _orig_ssl_ctx(*args, **kwargs)
        _ssl._create_default_https_context = _certifi_ssl_ctx
except Exception:
    pass

# Built-in DoH (Cloudflare 1.1.1.1 / Google 8.8.8.8) bypasses ISP 4G SIM DNS blocks & carrier IPv6 stalls
_ORIG_GETADDRINFO = socket.getaddrinfo
_DOH_LOCK = threading.Lock()
_DOH_CACHE = {}  # {host_lower: (ts, [ipv4, ...])}
_DOH_INFLIGHT = set()
_DOH_PRIORITY_DOMAINS = (
    'hdhub4u', '4khdhub', 'hblinks', 'hubcloud', 'hubdrive', 'hubcdn',
    'pingora.fyi', 'gamerxyt', 'hdstream4u', 'gadgetsweb', 'cryptonewz', 'fsl'
)

def _is_ip_literal(h):
    try:
        socket.inet_pton(socket.AF_INET, h)
        return True
    except Exception:
        pass
    try:
        socket.inet_pton(socket.AF_INET6, h)
        return True
    except Exception:
        return False

def _is_public_ipv4(ip):
    if not ip or '.' not in ip:
        return False
    parts = ip.split('.')
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except Exception:
        return False
    if a in (0, 10, 127) or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31) or (a == 169 and b == 254):
        return False
    return True

def _doh_lookup_ipv4(host):
    hl = str(host or '').strip().lower()
    if not hl:
        return []
    now = time.time()
    with _DOH_LOCK:
        hit = _DOH_CACHE.get(hl)
        if hit:
            ttl = 300 if hit[1] else 8
            if (now - hit[0]) < ttl:
                return list(hit[1])
        if hl in _DOH_INFLIGHT:
            return []
        _DOH_INFLIGHT.add(hl)
    ips = []
    try:
        endpoints = [
            (f"https://1.1.1.1/dns-query?name={urllib.parse.quote(hl)}&type=A", {'Accept': 'application/dns-json', 'User-Agent': 'StreamHub/1.0'}),
            (f"https://1.0.0.1/dns-query?name={urllib.parse.quote(hl)}&type=A", {'Accept': 'application/dns-json', 'User-Agent': 'StreamHub/1.0'}),
            (f"https://8.8.8.8/resolve?name={urllib.parse.quote(hl)}&type=A", {'User-Agent': 'StreamHub/1.0'}),
        ]
        for u, hdrs in endpoints:
            try:
                req = urllib.request.Request(u, headers=hdrs)
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    data = json.loads(resp.read().decode('utf-8', 'ignore'))
                for ans in (data.get('Answer') or []):
                    if ans.get('type') == 1:
                        ip = str(ans.get('data') or '').strip()
                        if _is_public_ipv4(ip) and ip not in ips:
                            ips.append(ip)
                if ips:
                    break
            except Exception:
                continue
    finally:
        with _DOH_LOCK:
            _DOH_INFLIGHT.discard(hl)
            _DOH_CACHE[hl] = (time.time(), ips if ips else [])
    return ips

def _smart_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if not isinstance(host, str) or not host or '.' not in host:
        return _ORIG_GETADDRINFO(host, port, family, type, proto, flags)
    hl = host.strip().lower()
    if hl in ('localhost', '1.1.1.1', '8.8.8.8', '1.0.0.1', '8.8.4.4') or _is_ip_literal(hl):
        return _ORIG_GETADDRINFO(host, port, family, type, proto, flags)
    need_doh = any(d in hl for d in _DOH_PRIORITY_DOMAINS) and family in (0, socket.AF_INET)
    if need_doh:
        doh_ips = _doh_lookup_ipv4(hl)
        if doh_ips:
            out = []
            for ip in doh_ips:
                try:
                    out.extend(_ORIG_GETADDRINFO(ip, port, socket.AF_INET, type, proto, flags))
                except Exception:
                    pass
            if out:
                return out
    try:
        res = _ORIG_GETADDRINFO(host, port, family, type, proto, flags)
        has_public_v4 = any(r[0] == socket.AF_INET and _is_public_ipv4(r[4][0]) for r in res)
        if not res or (family in (0, socket.AF_INET) and not has_public_v4 and not hl.endswith('.local')):
            doh_ips = _doh_lookup_ipv4(hl)
            if doh_ips:
                out = []
                for ip in doh_ips:
                    out.extend(_ORIG_GETADDRINFO(ip, port, socket.AF_INET, type, proto, flags))
                if out:
                    return out
        # Prefer IPv4 first on dual-stack mobile SIM networks to prevent IPv6 connect stalls
        if family == 0 and len(res) > 1:
            res = sorted(res, key=lambda r: 0 if r[0] == socket.AF_INET else 1)
        return res
    except Exception:
        if family in (0, socket.AF_INET):
            doh_ips = _doh_lookup_ipv4(hl)
            if doh_ips:
                out = []
                for ip in doh_ips:
                    out.extend(_ORIG_GETADDRINFO(ip, port, socket.AF_INET, type, proto, flags))
                if out:
                    return out
        raise

socket.getaddrinfo = _smart_getaddrinfo

try:
    import qrcode
except ImportError:
    qrcode = None

# Windows console encoding fix to prevent UnicodeEncodeError crashes
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

APP_VERSION = "2.6.2"
DEFAULT_UPDATE_URL = "khushal-jangid/streamhub"
STREAMHUB_UPDATE_URL = os.environ.get("STREAMHUB_UPDATE_URL", "").strip()
_ENV_DATA_DIR = os.environ.get("STREAMHUB_DATA_DIR", "").strip()
IS_FROZEN = bool(getattr(sys, 'frozen', False))
if _ENV_DATA_DIR:
    _APP_DATA_DIR = _ENV_DATA_DIR
    os.makedirs(_APP_DATA_DIR, exist_ok=True)
elif IS_FROZEN:
    _APP_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "StreamHub")
    os.makedirs(_APP_DATA_DIR, exist_ok=True)
else:
    _APP_DATA_DIR = os.path.dirname(os.path.abspath(__file__))
_PROGRESS_FILE = os.path.join(_APP_DATA_DIR, "streamhub_progress.json")
_CONFIG_FILE = os.path.join(_APP_DATA_DIR, "streamhub_config.json")
_OFFLINE_DIR = os.path.join(_APP_DATA_DIR, "offline_movies")
_OFFLINE_META_FILE = os.path.join(_APP_DATA_DIR, "offline_downloads.json")
_OFFLINE_LOCK = threading.Lock()
_OFFLINE_ACTIVE_WORKERS = set()
_OFFLINE_ACTIVE_STREAMS = {}
_OFFLINE_PAUSED_IDS = set()
_OFFLINE_DELETED_IDS = set()
_SNAPSHOT_DIR = os.path.join(os.path.expanduser("~"), "Pictures", "StreamHub Snapshots")

PORT = 5050
_ACTIVE_PORT = PORT
_SERVER_REF = None
_WINDOW_REF = None
_WINDOW_HWND = 0
_APP_QUITTING = False
_DESKTOP_MODE = False
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
HDHUB4U_BASE_URL = "https://new17.hdhub4u.cl"
HDHUB4U_SEARCH_API = "https://search.pingora.fyi/collections/post/documents/search"

def _find(name, *fallbacks):
    found = shutil.which(name)
    if found:
        return found
    for f in fallbacks:
        if f and os.path.exists(f):
            return f
    return fallbacks[0] if fallbacks else name

VLC_PATH = _find("vlc",
    r"C:\Program Files\VideoLAN\VLC\vlc.exe",
    r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe")
VLC_HTTP_PORT = 5058
VLC_HTTP_PASS = '4kmovie'

LANG_MAP = {
    'hin': 'Hindi',     'hi': 'Hindi',     'hindi': 'Hindi',
    'eng': 'English',   'en': 'English',   'english': 'English',
    'tam': 'Tamil',     'ta': 'Tamil',     'tamil': 'Tamil',
    'tel': 'Telugu',    'te': 'Telugu',    'telugu': 'Telugu',
    'kan': 'Kannada',   'kn': 'Kannada',   'kannada': 'Kannada',
    'mal': 'Malayalam', 'ml': 'Malayalam', 'malayalam': 'Malayalam',
    'mar': 'Marathi',   'mr': 'Marathi',   'marathi': 'Marathi',
    'ben': 'Bengali',   'bn': 'Bengali',   'bengali': 'Bengali',
    'pan': 'Punjabi',   'pa': 'Punjabi',   'punjabi': 'Punjabi',
    'jpn': 'Japanese',  'ja': 'Japanese',  'japanese': 'Japanese',
    'kor': 'Korean',    'ko': 'Korean',    'korean': 'Korean',
    'spa': 'Spanish',   'es': 'Spanish',   'spanish': 'Spanish',
    'fre': 'French',    'fra': 'French',    'fr': 'French',
    'ger': 'German',    'deu': 'German',    'de': 'German',
    'und': 'Track'
}

APP_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">'
    '<defs>'
    '<linearGradient id="sBg" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0%" stop-color="#14080d"/><stop offset="100%" stop-color="#05060b"/>'
    '</linearGradient>'
    '<linearGradient id="sRib" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0%" stop-color="#ff2e39"/><stop offset="50%" stop-color="#e50914"/><stop offset="100%" stop-color="#9e050d"/>'
    '</linearGradient>'
    '</defs>'
    '<rect width="192" height="192" rx="44" fill="url(#sBg)"/>'
    '<g transform="translate(36,26)">'
    '<path d="M98 18 L44 18 C25 18 18 30 18 46 C18 63 29 72 51 77 L69 81 C82 84 85 89 85 95 C85 103 77 107 62 107 L19 107 L19 125 L76 125 C97 125 104 111 104 94 C104 75 91 66 68 61 L50 57 C38 54 35 49 35 43 C35 36 42 33 56 33 L98 33 Z" fill="url(#sRib)"/>'
    '</g>'
    '</svg>'
)
_PNG_ICON_CACHE = {}

def make_app_icon_png(size=192):
    """Generates a cached Netflix-inspired Crimson Ribbon S PWA icon using Pillow."""
    if size in _PNG_ICON_CACHE:
        return _PNG_ICON_CACHE[size]
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (size, size), (5, 6, 11, 255))
        draw = ImageDraw.Draw(img)
        pad = max(8, size // 14)
        draw.rounded_rectangle([pad, pad, size - pad, size - pad], radius=size // 5, fill=(14, 16, 25, 255))
        # Subtle dark-crimson top/bottom ribbon depth slabs + crisp foreground ribbon S bars
        s = size / 192.0
        # Back shadow ribbon bars
        draw.rounded_rectangle([int(50*s), int(44*s), int(142*s), int(64*s)], radius=int(8*s), fill=(158, 5, 13, 255))
        draw.rounded_rectangle([int(50*s), int(128*s), int(142*s), int(148*s)], radius=int(8*s), fill=(158, 5, 13, 255))
        # Main glowing Netflix crimson S ribbon segments
        draw.rounded_rectangle([int(46*s), int(38*s), int(144*s), int(60*s)], radius=int(10*s), fill=(229, 9, 20, 255))
        draw.rounded_rectangle([int(46*s), int(38*s), int(72*s), int(102*s)], radius=int(10*s), fill=(255, 42, 54, 255))
        draw.rounded_rectangle([int(46*s), int(84*s), int(144*s), int(108*s)], radius=int(10*s), fill=(229, 9, 20, 255))
        draw.rounded_rectangle([int(118*s), int(84*s), int(144*s), int(154*s)], radius=int(10*s), fill=(255, 42, 54, 255))
        draw.rounded_rectangle([int(46*s), int(132*s), int(144*s), int(154*s)], radius=int(10*s), fill=(229, 9, 20, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()
        _PNG_ICON_CACHE[size] = data
        return data
    except Exception:
        return base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

# ── HTTP Helpers ───────────────────────────────────────────────────────────────

def fetch_html(url, referer=None, extra_headers=None, timeout=15):
    headers = {
        'User-Agent': USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,hi;q=0.8',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
    }
    if referer:
        headers['Referer'] = referer
    if extra_headers:
        headers.update(extra_headers)
    attempts = 2 if any(h in url.lower() for h in ('hubdrive.', 'hubcloud.', 'hblinks.', 'gamerxyt.')) else 1
    last_err = None
    for idx in range(attempts):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                final_url = resp.url
                try:
                    raw = _gzip.decompress(raw)
                except Exception:
                    pass
                return raw.decode('utf-8', errors='ignore'), final_url
        except Exception as e:
            last_err = e
            if idx + 1 < attempts and ('timed out' in str(e).lower() or isinstance(e, (TimeoutError, socket.timeout))):
                time.sleep(0.3)
                continue
            raise
    raise last_err

def make_safe_url(url):
    """Ensures URL has spaces, brackets, and non-ASCII characters percent-encoded without double-encoding."""
    try:
        if not url or not url.startswith('http'):
            return url
        return urllib.parse.quote(url.strip(), safe=':/?&=%+-_.~!()')
    except Exception:
        return url


def normalize_url(url):
    """Normalizes old/dead HubCloud and HubDrive domains to currently active working domains."""
    if not url or not isinstance(url, str):
        return url
    url = url.strip()
    # Normalize old/dead HubCloud domains (.ist, .lol, .cx, .lat, .ink, .buzz, .club, .me, etc.) to .mov
    url = re.sub(r'https?://(?:www\.)?hubcloud\.(?:ist|lol|cx|lat|ink|buzz|club|me|link|online)/', 'https://hubcloud.mov/', url, flags=re.I)
    # Normalize old/dead HubDrive domains (.lat, .club, .me) to .space
    url = re.sub(r'https?://(?:www\.)?hubdrive\.(?:lat|club|me|ist|cx)/', 'https://hubdrive.space/', url, flags=re.I)
    return url

def rot13(s):
    res = []
    for c in s:
        if 'a' <= c <= 'z':
            res.append(chr((ord(c) - ord('a') + 13) % 26 + ord('a')))
        elif 'A' <= c <= 'Z':
            res.append(chr((ord(c) - ord('A') + 13) % 26 + ord('A')))
        else:
            res.append(c)
    return ''.join(res)

def decode_greenmotors(greenmotors_url):
    """De-obfuscates greenmotors.club / greenmotors.cc token to extract direct HubCloud drive link."""
    try:
        req = urllib.request.Request(greenmotors_url, headers={'User-Agent': USER_AGENT})
        with urllib.request.urlopen(req, timeout=12) as r:
            html = r.read().decode('utf-8', errors='ignore')
        # Pattern 1: standard s('o', '...') format
        m = re.search(r"s\('o',\s*['\"]([^'\"]+)['\"]", html)
        if not m:
            # Pattern 2: alternate obfuscation key 'o' with base64 value
            m = re.search(r"['\"]o['\"]\s*[,:]?\s*['\"]([A-Za-z0-9+/=]{20,})['\"]", html)
        if not m:
            # Pattern 3: any long base64 function argument
            m = re.search(r"\(\s*['\"]([A-Za-z0-9+/=]{80,})['\"]\s*\)", html)
        if not m:
            return None
        token = m.group(1)
        d1 = base64.b64decode(token).decode('utf-8', errors='ignore')
        d2 = base64.b64decode(d1).decode('utf-8', errors='ignore')
        d3 = rot13(d2)
        d4 = base64.b64decode(d3).decode('utf-8', errors='ignore')
        data = json.loads(d4)
        hubcloud_url = base64.b64decode(data['o']).decode('utf-8', errors='ignore')
        return normalize_url(hubcloud_url)
    except Exception as e:
        print(f"[GreenMotors] Decode error on {greenmotors_url[:50]}:", e)
        return None

def is_range_capable(url):
    """Returns True if the upstream host supports HTTP byte-range (206) seeking."""
    return bool(url) and 'video-downloads.googleusercontent.com' not in url

def open_upstream_range(url, range_hdr=None, referer=None, hedges=(3, 6, 8), timeout=12):
    """Opens an upstream stream, using staged hedged retries on workers.dev to bypass 403 quota rotation."""
    safe_url = make_safe_url(url)
    is_worker = '.workers.dev/' in safe_url

    def _single_attempt():
        hdrs = {'User-Agent': USER_AGENT}
        # Never forward gamerxyt/hubcloud Referer to workers.dev or R2 (can trigger 403)
        if referer and not is_worker and 'r2.cloudflarestorage.com' not in safe_url:
            hdrs['Referer'] = referer
        if range_hdr:
            hdrs['Range'] = range_hdr
        req = urllib.request.Request(safe_url, headers=hdrs)
        try:
            r = urllib.request.urlopen(req, timeout=timeout)
            return r, r.status
        except urllib.error.HTTPError as he:
            if he.code in (206, 416):
                return he, he.code
            he.close()
            raise

    if not is_worker:
        last_err = None
        for _ in range(2):
            try:
                return _single_attempt()
            except urllib.error.HTTPError as he:
                last_err = he
                if he.code in (400, 401, 403, 404, 410):
                    raise
            except Exception as e:
                last_err = e
        raise last_err

    # Staged parallel hedge on workers.dev: return first 200/206/416 winner immediately without waiting for losers
    last_err = None
    for n in hedges:
        cond = threading.Condition()
        cancel_evt = threading.Event()
        state = {'winner': None, 'done': 0, 'err': None, 'perm_errs': 0}

        def _worker():
            if cancel_evt.is_set():
                with cond:
                    state['done'] += 1
                    cond.notify_all()
                return
            try:
                resp, st = _single_attempt()
                with cond:
                    if not cancel_evt.is_set() and state['winner'] is None:
                        state['winner'] = (resp, st)
                        cancel_evt.set()
                        cond.notify_all()
                        return
                try:
                    resp.close()
                except Exception:
                    pass
            except urllib.error.HTTPError as he:
                with cond:
                    state['err'] = he
                    if he.code in (400, 401, 403, 404, 410):
                        state['perm_errs'] += 1
            except Exception as exc:
                with cond:
                    state['err'] = exc
            with cond:
                state['done'] += 1
                cond.notify_all()

        for _ in range(n):
            threading.Thread(target=_worker, daemon=True).start()

        deadline = time.time() + timeout + 1.0
        with cond:
            while state['winner'] is None and state['done'] < n:
                rem = deadline - time.time()
                if rem <= 0:
                    break
                cond.wait(timeout=rem)
            cancel_evt.set()
            if state['winner'] is not None:
                return state['winner']
            last_err = state['err'] or RuntimeError('Workers.dev retry failed')
            if state['perm_errs'] == n:
                break
    raise last_err

def check_link_alive(url, referer=None):
    """Fast check whether a stream URL actually works or returns 403 / quota error."""
    try:
        safe_url = make_safe_url(url)
        if '.workers.dev/' in safe_url:
            # Probe mid-file range with 4-way hedge so CF byte-0 cache doesn't fool us
            try:
                resp, st = open_upstream_range(safe_url, range_hdr='bytes=1048576-1049599', hedges=(4,), timeout=4)
                with resp:
                    return st in (200, 206)
            except Exception:
                return False
        headers = {
            'User-Agent': USER_AGENT,
            'Range': 'bytes=0-100'
        }
        if referer and 'r2.cloudflarestorage.com' not in safe_url:
            headers['Referer'] = referer
        req = urllib.request.Request(safe_url, headers=headers)
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = resp.read(100)
            if b'NotEntitled' in data or b'<Error>' in data or b'downloadQuotaExceeded' in data:
                return False
            return resp.status in (200, 206)
    except Exception:
        return False


# ── High-Speed Stream Cache & Hedged Seek Proxy ────────────────────────────────

HEAD_CACHE_BYTES = 512 * 1024       # 512 KB: fast <1s startup, covers MKV tracks/duration header
TAIL_CACHE_BYTES = 512 * 1024       # 512 KB: MKV Cues seek-index near EOF (fetched async)
LRU_BLOCK_SIZE   = 2 * 1024 * 1024  # 2 MB aligned LRU blocks for instant recent seeks
MAX_LRU_BLOCKS   = 12               # ~24 MB sliding RAM cache per movie (safe for Android)
MAX_SESSIONS     = 2

_BW_LOCK = threading.Lock()
_BW_EWMA_BPS = 0.0
_BW_UPDATED_AT = 0.0

def _record_stream_bw(n_bytes, elapsed_sec):
    global _BW_EWMA_BPS, _BW_UPDATED_AT
    if n_bytes < 32768 or elapsed_sec <= 0.001:
        return
    inst_bps = min(float(n_bytes) / float(elapsed_sec), 150_000_000.0)
    now = time.time()
    with _BW_LOCK:
        if (now - _BW_UPDATED_AT) > 45.0 or _BW_EWMA_BPS <= 0.0:
            _BW_EWMA_BPS = inst_bps
        else:
            _BW_EWMA_BPS = (_BW_EWMA_BPS * 0.65) + (inst_bps * 0.35)
        _BW_UPDATED_AT = now

def get_network_speed_payload():
    with _BW_LOCK:
        stale = (time.time() - _BW_UPDATED_AT) > 45.0
        bps = 0 if stale else int(_BW_EWMA_BPS)
    mbps = round((bps * 8.0) / 1_000_000.0, 2) if bps > 0 else 0.0
    allow_4k = is_4k_unlocked()
    if bps <= 0 or bps < 1_100_000:
        rec = "480p"
    elif bps < 2_800_000:
        rec = "720p"
    elif bps < 7_500_000 or not allow_4k:
        rec = "1080p"
    else:
        rec = "4K UHD"
    return {"success": True, "bps": bps, "mbps": mbps, "recommended_quality": rec, "allow_4k": allow_4k, "stale": stale}

def _read_ebml_vint(buf, pos, keep_flag=False):
    if pos >= len(buf):
        return None, pos
    b = buf[pos]
    if b == 0:
        return None, pos
    length = 1
    mask = 0x80
    while mask and not (b & mask):
        length += 1
        mask >>= 1
    if length > 8 or pos + length > len(buf):
        return None, pos
    val = b if keep_flag else (b & (mask - 1))
    for i in range(1, length):
        val = (val << 8) | buf[pos + i]
    unknown_sz = (not keep_flag) and (val == (1 << (7 * length)) - 1)
    return (None if unknown_sz else val), pos + length

def _iter_ebml(buf, start, end):
    p = start
    while p < end:
        eid, p2 = _read_ebml_vint(buf, p, keep_flag=True)
        if eid is None or p2 >= end:
            break
        sz, p3 = _read_ebml_vint(buf, p2, keep_flag=False)
        el_end = end if sz is None else min(end, p3 + sz)
        yield eid, p3, el_end
        if el_end <= p3 or el_end >= end:
            break
        p = el_end

def _format_mkv_codec(cid):
    c = (cid or '').upper()
    if c == 'A_EAC3': return 'DDP / E-AC-3'
    if c == 'A_AC3': return 'Dolby AC3'
    if c.startswith('A_TRUEHD'): return 'Dolby TrueHD'
    if 'DTS/EXPRESS' in c: return 'DTS Express'
    if c.startswith('A_DTS'): return 'DTS-HD / DTS'
    if c.startswith('A_AAC'): return 'AAC'
    if c == 'A_FLAC': return 'FLAC'
    if c == 'A_OPUS': return 'Opus'
    if c == 'A_MPEG/L3': return 'MP3'
    if 'HEVC' in c: return 'HEVC (H.265)'
    if 'AVC' in c: return 'H.264'
    if 'AV1' in c: return 'AV1'
    if c.startswith('S_TEXT/UTF8'): return 'SRT / UTF-8'
    if c.startswith('S_TEXT/ASS') or c.startswith('S_TEXT/SSA'): return 'ASS / SSA'
    if c == 'S_HDMV/PGS': return 'PGS Image'
    if c == 'S_VOBSUB': return 'VobSub'
    return cid

def _format_ch(ch):
    if not ch: return ''
    if ch == 8: return '7.1'
    if ch == 6: return '5.1'
    if ch == 2: return '2.0'
    if ch == 1: return 'Mono'
    return f'{ch}ch'

def parse_mkv_media_info(head_buf, tail_buf=b''):
    """Extracts embedded video/audio/subtitle track metadata & duration from cached MKV EBML headers."""
    info = {"audio_tracks": [], "subtitle_tracks": [], "video": {}, "duration_sec": 0}
    if not head_buf or not head_buf.startswith(b'\x1a\x45\xdf\xa3'):
        return info

    timecode_scale = 1_000_000
    dur_val = None
    tracks_slice = None

    def _scan_segment_children(buf, s_start, s_end):
        nonlocal timecode_scale, dur_val, tracks_slice
        for ceid, cbs, cbe in _iter_ebml(buf, s_start, s_end):
            if ceid == 0x1549A966:  # Segment Info
                for ieid, ibs, ibe in _iter_ebml(buf, cbs, cbe):
                    raw = buf[ibs:ibe]
                    if ieid == 0x2AD7B1 and raw:
                        timecode_scale = int.from_bytes(raw, 'big')
                    elif ieid == 0x4489:
                        if len(raw) == 8:
                            dur_val = struct.unpack('>d', raw)[0]
                        elif len(raw) == 4:
                            dur_val = struct.unpack('>f', raw)[0]
            elif ceid == 0x1654AE6B:  # Tracks
                tracks_slice = (buf, cbs, cbe)
                if dur_val is not None:
                    break

    try:
        for eid, bs, be in _iter_ebml(head_buf, 0, len(head_buf)):
            if eid == 0x18538067:
                _scan_segment_children(head_buf, bs, be)
                break
        if tracks_slice is None and tail_buf:
            idx = tail_buf.find(b'\x16\x54\xae\x6b')
            if idx != -1:
                _, cbs, cbe = next(_iter_ebml(tail_buf, idx, len(tail_buf)))
                tracks_slice = (tail_buf, cbs, cbe)
    except Exception:
        pass

    if dur_val and timecode_scale:
        try:
            info['duration_sec'] = max(0, int((dur_val * timecode_scale) / 1_000_000_000))
        except Exception:
            pass

    if tracks_slice:
        tbuf, tstart, tend = tracks_slice
        a_idx = 0
        s_idx = 0
        for teid, tbs, tbe in _iter_ebml(tbuf, tstart, tend):
            if teid != 0xAE:
                continue
            tr = {}
            for feid, fbs, fbe in _iter_ebml(tbuf, tbs, tbe):
                raw = tbuf[fbs:fbe]
                if feid == 0xD7 and raw:
                    tr['num'] = int.from_bytes(raw, 'big')
                elif feid == 0x83 and raw:
                    tr['type'] = int.from_bytes(raw, 'big')
                elif feid == 0x86:
                    tr['codec'] = raw.decode('utf-8', 'replace').rstrip('\x00')
                elif feid == 0x22B59C:
                    tr['lang'] = raw.decode('utf-8', 'replace').rstrip('\x00').lower()
                elif feid == 0x22B59D:
                    tr['lang_bcp'] = raw.decode('utf-8', 'replace').rstrip('\x00').lower()
                elif feid == 0x536E:
                    tr['name'] = raw.decode('utf-8', 'replace').rstrip('\x00').strip()
                elif feid == 0xE0:
                    for veid, vbs, vbe in _iter_ebml(tbuf, fbs, fbe):
                        vraw = tbuf[vbs:vbe]
                        if veid == 0xB0 and vraw:
                            tr['width'] = int.from_bytes(vraw, 'big')
                        elif veid == 0xBA and vraw:
                            tr['height'] = int.from_bytes(vraw, 'big')
                elif feid == 0xE1:
                    for aeid, abs_, abe in _iter_ebml(tbuf, fbs, fbe):
                        araw = tbuf[abs_:abe]
                        if aeid == 0x9F and araw:
                            tr['channels'] = int.from_bytes(araw, 'big')
                        elif aeid == 0xB5 and len(araw) in (4, 8):
                            tr['freq'] = int(struct.unpack('>d' if len(araw) == 8 else '>f', araw)[0])

            ttype = tr.get('type')
            lcode = (tr.get('lang_bcp') or tr.get('lang') or 'und').split('-')[0]
            lang_name = LANG_MAP.get(lcode) or (lcode.upper() if lcode != 'und' else 'Track')
            codec_lbl = _format_mkv_codec(tr.get('codec', ''))
            raw_name = tr.get('name', '')

            if ttype == 1 and not info['video']:
                res_txt = f"{tr.get('width', '')}x{tr.get('height', '')}".strip('x')
                info['video'] = {
                    "codec": codec_lbl,
                    "resolution": res_txt,
                    "width": tr.get('width', 0),
                    "height": tr.get('height', 0),
                }
            elif ttype == 2:
                a_idx += 1
                ch_txt = _format_ch(tr.get('channels'))
                freq_txt = f"{round(tr['freq']/1000)}kHz" if tr.get('freq') else ''
                spec = " ".join(x for x in (codec_lbl, ch_txt) if x)
                meta = " • ".join(x for x in (spec, freq_txt) if x)
                desc_part = f" — {raw_name}" if raw_name and lang_name.lower() not in raw_name.lower() else (f" ({raw_name})" if raw_name else "")
                label = f"Track {a_idx}: {lang_name} [{meta}]{desc_part}" if meta else f"Track {a_idx}: {lang_name}{desc_part}"
                info['audio_tracks'].append({
                    "track_num": a_idx,
                    "stream_num": tr.get('num', a_idx),
                    "lang": lang_name,
                    "codec": codec_lbl,
                    "channels": ch_txt,
                    "label": label,
                })
            elif ttype == 17:
                s_idx += 1
                sub_title = raw_name or lang_name
                if lang_name != 'Track' and lang_name.lower() not in sub_title.lower():
                    sub_title = f"{lang_name} ({sub_title})"
                info['subtitle_tracks'].append({
                    "track_num": s_idx,
                    "stream_num": tr.get('num', s_idx),
                    "lang": lang_name,
                    "codec": codec_lbl,
                    "label": f"Sub {s_idx}: {sub_title} [{codec_lbl}]",
                })
    return info

class StreamSession:
    """Caches MKV header/tail probes and recent byte blocks (including remote ZIP episode slices)."""
    def __init__(self, stream_url, seek_url='', base_offset=0, sub_size=None, compress_method=0, backup_urls=None):
        self.stream_url = stream_url
        self.seek_url = seek_url
        self.base_offset = int(base_offset or 0)
        self.sub_size = int(sub_size) if sub_size is not None else None
        self.compress_method = int(compress_method or 0)
        self.backup_urls = []
        self.merge_backup_urls(backup_urls)
        self.is_sub = (self.sub_size is not None or self.base_offset > 0)
        self.total_size = self.sub_size
        self.content_type = 'video/x-matroska'
        self.head_buf = b''
        self.tail_buf = b''
        self.media_info = None
        self.tail_start = None
        self.tail_failed_at = 0.0
        self.lru = OrderedDict()
        self.lru_lock = threading.Lock()
        self.init_lock = threading.Lock()
        self.initialized = False

    def merge_backup_urls(self, urls):
        """Registers additional range-capable mirror URLs for mid-stream seek failover."""
        for u in (urls or []):
            if u and is_range_capable(u) and u not in self.backup_urls:
                self.backup_urls.append(u)

    def seek_candidates(self, primary_seek=''):
        """Returns ordered unique range-capable candidate URLs for HTTP 206 seeking."""
        out = []
        for u in ([primary_seek, self.seek_url, self.stream_url] + list(self.backup_urls)):
            if u and is_range_capable(u) and u not in out:
                out.append(u)
        return out

    def open_seek_range(self, primary_seek, range_hdr, cur_pos=0, timeout=12):
        """Opens a byte-range request, rejecting silent HTTP 200 full-file fallbacks and failing over across mirrors."""
        last_err = None
        last_416 = None
        for cand_url in self.seek_candidates(primary_seek):
            try:
                resp, st = open_upstream_range(cand_url, range_hdr=range_hdr, hedges=(3, 6, 8), timeout=timeout)
                if cur_pos > 0 and st == 200:
                    # Upstream ignored Range and returned byte 0; never corrupt a seek response
                    try:
                        resp.close()
                    except Exception:
                        pass
                    last_err = RuntimeError(f"Host ignored Range at offset {cur_pos}")
                    continue
                if st == 416:
                    if last_416 is None:
                        last_416 = (resp, st, cand_url)
                    else:
                        try:
                            resp.close()
                        except Exception:
                            pass
                    continue
                if st in (200, 206):
                    if last_416 is not None:
                        try:
                            last_416[0].close()
                        except Exception:
                            pass
                    return resp, st, cand_url
                try:
                    resp.close()
                except Exception:
                    pass
            except Exception as exc:
                last_err = exc
        if last_416 is not None:
            return last_416
        if last_err:
            raise last_err
        raise RuntimeError("No range-capable mirror available")

    def upstream_range(self, start_b, end_b=None):
        s = self.base_offset + start_b
        if end_b is not None:
            return f'bytes={s}-{self.base_offset + end_b}'
        if self.sub_size is not None:
            return f'bytes={s}-{self.base_offset + self.sub_size - 1}'
        return f'bytes={s}-'

    def warm_async(self):
        if not self.initialized:
            threading.Thread(target=self.ensure_init, daemon=True).start()

    def _read_exact(self, resp, n_bytes):
        chunks = []
        got = 0
        while got < n_bytes:
            d = resp.read(min(262144, n_bytes - got))
            if not d:
                break
            chunks.append(d)
            got += len(d)
        return b''.join(chunks)

    def ensure_init(self):
        if self.initialized:
            return
        with self.init_lock:
            if self.initialized:
                return
            if self.compress_method == 8:
                self.initialized = True
                return
            seek_probe = {'total': None}

            def _fetch_head():
                try:
                    t_st = time.time()
                    head_len = min(HEAD_CACHE_BYTES, self.sub_size) if self.sub_size is not None else HEAD_CACHE_BYTES
                    if is_range_capable(self.stream_url):
                        resp, st = open_upstream_range(self.stream_url, range_hdr=self.upstream_range(0, head_len - 1), hedges=(3, 6), timeout=10)
                    else:
                        resp, st = open_upstream_range(self.stream_url, timeout=12)
                    with resp:
                        if st not in (200, 206):
                            return
                        if not self.is_sub:
                            self.content_type = resp.headers.get('Content-Type', 'video/x-matroska')
                            cr = resp.headers.get('Content-Range', '')
                            cl = resp.headers.get('Content-Length', '')
                            m = re.search(r'bytes\s+\d+-\d+/(\d+)', cr)
                            if m:
                                self.total_size = int(m.group(1))
                            elif cl:
                                self.total_size = int(cl)
                        self.head_buf = self._read_exact(resp, head_len)
                        _record_stream_bw(len(self.head_buf), time.time() - t_st)
                except Exception as e:
                    print("[StreamSession] Head preload note:", e)

            def _probe_seek_total():
                if self.is_sub or not self.seek_url or self.seek_url == self.stream_url:
                    return
                try:
                    resp, st = open_upstream_range(self.seek_url, range_hdr='bytes=1048576-1049599', hedges=(4,), timeout=5)
                    with resp:
                        if st not in (200, 206):
                            return
                        cr = resp.headers.get('Content-Range', '')
                        m = re.search(r'bytes\s+\d+-\d+/(\d+)', cr)
                        if m:
                            seek_probe['total'] = int(m.group(1))
                except Exception:
                    pass

            t_head = threading.Thread(target=_fetch_head, daemon=True)
            t_probe = threading.Thread(target=_probe_seek_total, daemon=True)
            t_head.start()
            t_probe.start()
            t_head.join()
            t_probe.join()

            if not self.is_sub and seek_probe['total']:
                self.total_size = seek_probe['total']

            self.media_info = parse_mkv_media_info(self.head_buf, self.tail_buf)
            self.initialized = True

            # Fetch MKV Cues tail asynchronously so playback starts immediately
            cands = self.seek_candidates(self.seek_url or '')
            tail_url = cands[0] if cands else ''
            if self.total_size and tail_url:
                threading.Thread(target=self._fetch_tail, args=(tail_url,), daemon=True).start()

    def _fetch_tail(self, tail_url):
        ts = max(0, self.total_size - TAIL_CACHE_BYTES)
        try:
            resp, st, _used_url = self.open_seek_range(tail_url, self.upstream_range(ts, self.total_size - 1), cur_pos=ts, timeout=10)
            with resp:
                if st not in (200, 206):
                    self.tail_failed_at = time.time()
                    return
                if not self.is_sub:
                    cr = resp.headers.get('Content-Range', '')
                    m = re.search(r'bytes\s+\d+-\d+/(\d+)', cr)
                    if m:
                        actual_total = int(m.group(1))
                        if actual_total <= self.total_size:
                            self.total_size = actual_total
                self.tail_start = ts
                self.tail_buf = resp.read()
        except Exception:
            self.tail_failed_at = time.time()

    def put_lru_bytes(self, offset, data):
        """Stores aligned 2MB blocks touched by sequential streaming."""
        if not data:
            return
        end_pos = offset + len(data)
        with self.lru_lock:
            cur = offset
            pos = 0
            while cur < end_pos:
                blk_idx = cur // LRU_BLOCK_SIZE
                blk_off = cur % LRU_BLOCK_SIZE
                take = min(LRU_BLOCK_SIZE - blk_off, end_pos - cur)
                if blk_off == 0 and take == LRU_BLOCK_SIZE:
                    self.lru[blk_idx] = data[pos:pos + take]
                    self.lru.move_to_end(blk_idx)
                    while len(self.lru) > MAX_LRU_BLOCKS:
                        self.lru.popitem(last=False)
                cur += take
                pos += take

    def get_lru_slice(self, s, max_len):
        blk_idx = s // LRU_BLOCK_SIZE
        blk_off = s % LRU_BLOCK_SIZE
        with self.lru_lock:
            blk = self.lru.get(blk_idx)
            if blk and blk_off < len(blk):
                self.lru.move_to_end(blk_idx)
                return blk[blk_off:blk_off + max_len]
        return None


_SESSIONS = OrderedDict()
_SESSIONS_LOCK = threading.Lock()

def get_stream_session(stream_url, seek_url='', base_offset=0, sub_size=None, compress_method=0, backup_urls=None):
    key = (stream_url, seek_url or '', int(base_offset or 0), int(sub_size) if sub_size is not None else None, int(compress_method or 0))
    with _SESSIONS_LOCK:
        sess = _SESSIONS.get(key)
        if sess is None:
            sess = StreamSession(stream_url, seek_url or '', base_offset, sub_size, compress_method, backup_urls=backup_urls)
            _SESSIONS[key] = sess
            while len(_SESSIONS) > MAX_SESSIONS:
                _SESSIONS.popitem(last=False)
        else:
            if backup_urls:
                sess.merge_backup_urls(backup_urls)
            _SESSIONS.move_to_end(key)
        return sess


_SHORT_STREAMS = OrderedDict()
_SHORT_LOCK = threading.Lock()
MAX_SHORT_STREAMS = 200

def register_short_stream(stream_url, seek_url='', base_offset=0, sub_size=None, compress_method=0, title='4K Stream', size='4K UHD', backup_urls=None):
    """Registers stream parameters under a compact deterministic ID for short mobile VLC & QR URLs."""
    key = f"{stream_url}|{seek_url or ''}|{int(base_offset or 0)}|{sub_size or ''}|{int(compress_method or 0)}"
    sid = hashlib.md5(key.encode('utf-8')).hexdigest()[:10]
    info = {
        "stream_url": stream_url,
        "seek_url": seek_url or '',
        "base_offset": int(base_offset or 0),
        "sub_size": int(sub_size) if sub_size is not None else None,
        "compress_method": int(compress_method or 0),
        "title": title,
        "size": size,
        "backup_urls": list(backup_urls or []),
    }
    with _SHORT_LOCK:
        _SHORT_STREAMS[sid] = info
        _SHORT_STREAMS.move_to_end(sid)
        while len(_SHORT_STREAMS) > MAX_SHORT_STREAMS:
            _SHORT_STREAMS.popitem(last=False)
    return sid

def get_short_stream(sid):
    with _SHORT_LOCK:
        info = _SHORT_STREAMS.get(sid)
        if info:
            _SHORT_STREAMS.move_to_end(sid)
        return info


# ── Local Parallel-Prefetch Sliced HLS Proxy (Zero-Crash libVLC & Fast Browser Seek) ──

MAX_HLS_SESSIONS = 12
MAX_HLS_SEG_CACHE = 16

class _HlsSegSlot:
    """Thread-safe shared streaming slot so prefetched & active readers share one upstream socket."""
    def __init__(self):
        self.chunks = []
        self.done = False
        self.err = None
        self.ctype = 'video/MP2T'
        self.cond = threading.Condition()

class HLSSession:
    """Parses HLS playlists, serves sliced VOD playlists for crash-free seeks, and prefetches segments."""
    def __init__(self, master_url, sid=''):
        self.master_url = master_url
        self.sid = sid or ''
        self.media_url = master_url
        self.segments = []
        self.total_sec = 0.0
        self.total_ms = 0
        self.target_duration = 12
        self.init_lock = threading.Lock()
        self.initialized = False
        self.seg_lock = threading.Lock()
        self.seg_cache = OrderedDict()
        self.in_flight = {}

    def warm_async(self):
        if not self.initialized:
            threading.Thread(target=self.ensure_init, daemon=True).start()

    def _fetch_text(self, url, timeout=10):
        headers = {'User-Agent': USER_AGENT, 'Referer': 'https://hdstream4u.com/'}
        req = urllib.request.Request(make_safe_url(url), headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='ignore')

    def ensure_init(self):
        if self.initialized and self.segments:
            return
        with self.init_lock:
            if self.initialized and self.segments:
                return
            try:
                raw_txt = self._fetch_text(self.master_url, timeout=10)
                media_url = self.master_url
                media_txt = raw_txt
                if '#EXT-X-STREAM-INF' in raw_txt:
                    lines = raw_txt.splitlines()
                    best_bw = -1
                    best_uri = ''
                    cur_bw = 0
                    for ln in lines:
                        s = ln.strip()
                        if s.startswith('#EXT-X-STREAM-INF'):
                            m_bw = re.search(r'BANDWIDTH=(\d+)', s)
                            cur_bw = int(m_bw.group(1)) if m_bw else 0
                        elif s and not s.startswith('#'):
                            cand = urllib.parse.urljoin(self.master_url, s)
                            if cur_bw >= best_bw:
                                best_bw = cur_bw
                                best_uri = cand
                            cur_bw = 0
                    if best_uri:
                        media_url = best_uri
                        media_txt = self._fetch_text(media_url, timeout=10)

                self.media_url = media_url
                parsed = []
                cur_sec = 0.0
                pending_dur = 10.0
                pending_disc = False
                active_map = None
                active_key = None
                max_dur = 10

                for ln in media_txt.splitlines():
                    s = ln.strip()
                    if not s:
                        continue
                    if s.startswith('#EXT-X-TARGETDURATION:'):
                        try:
                            max_dur = max(4, int(float(s.split(':', 1)[1].strip())))
                        except Exception:
                            pass
                    elif s.startswith('#EXT-X-MAP:'):
                        m_u = re.search(r'URI="([^"]+)"', s)
                        if m_u:
                            active_map = urllib.parse.urljoin(media_url, m_u.group(1))
                    elif s.startswith('#EXT-X-KEY:'):
                        m_u = re.search(r'URI="([^"]+)"', s)
                        if m_u:
                            abs_k = urllib.parse.urljoin(media_url, m_u.group(1))
                            active_key = (s, abs_k)
                        elif 'METHOD=NONE' in s:
                            active_key = None
                    elif s.startswith('#EXT-X-DISCONTINUITY'):
                        pending_disc = True
                    elif s.startswith('#EXTINF:'):
                        try:
                            pending_dur = max(0.2, float(s.split(':', 1)[1].split(',', 1)[0]))
                        except Exception:
                            pending_dur = 10.0
                    elif not s.startswith('#'):
                        seg_url = urllib.parse.urljoin(media_url, s)
                        parsed.append({
                            'idx': len(parsed),
                            'start_sec': cur_sec,
                            'dur': pending_dur,
                            'url': seg_url,
                            'disc': pending_disc,
                            'map_url': active_map,
                            'key_info': active_key,
                        })
                        cur_sec += pending_dur
                        pending_dur = 10.0
                        pending_disc = False

                self.segments = parsed
                self.total_sec = cur_sec
                self.total_ms = int(round(cur_sec * 1000))
                self.target_duration = max(max_dur, 12)
                self.initialized = True
                if parsed:
                    self._schedule_prefetch(0)
                    self._schedule_prefetch(1)
            except Exception as e:
                print('[HLSSession] init error:', e)

    def _query_ident(self, sid_override=''):
        s = sid_override or self.sid
        if s:
            return f"sid={urllib.parse.quote(s, safe='')}"
        return f"url={urllib.parse.quote(self.master_url, safe='')}"

    def slice_for_ms(self, t_ms, sid_override='', origin=''):
        self.ensure_init()
        if not self.segments:
            return {
                'success': False,
                'total_ms': 0,
                'seg_idx': 0,
                'seg_start_ms': 0,
                'intra_sec': 0.0,
                'playlist_url': f"{origin}/api/hls/playlist.m3u8?{self._query_ident(sid_override)}&t_ms=0",
            }
        tgt_sec = max(0.0, float(t_ms or 0) / 1000.0)
        if self.total_sec > 2.0 and tgt_sec >= self.total_sec - 1.0:
            tgt_sec = max(0.0, self.total_sec - 2.0)
        chosen = self.segments[0]
        for seg in self.segments:
            if seg['start_sec'] + seg['dur'] > tgt_sec:
                chosen = seg
                break
            chosen = seg
        intra = max(0.0, tgt_sec - chosen['start_sec'])
        seg_start_ms = int(round(chosen['start_sec'] * 1000))
        self._schedule_prefetch(chosen['idx'])
        self._schedule_prefetch(chosen['idx'] + 1)
        return {
            'success': True,
            'total_ms': self.total_ms,
            'segment_count': len(self.segments),
            'seg_idx': chosen['idx'],
            'seg_start_ms': seg_start_ms,
            'intra_sec': round(intra, 2),
            'playlist_url': f"{origin}/api/hls/playlist.m3u8?{self._query_ident(sid_override)}&t_ms={int(t_ms or 0)}",
        }

    def render_playlist(self, start_idx=0, sid_override=''):
        self.ensure_init()
        if not self.segments:
            return '', 0
        start_idx = max(0, min(len(self.segments) - 1, int(start_idx)))
        ident = self._query_ident(sid_override)
        lines = [
            '#EXTM3U',
            '#EXT-X-VERSION:3',
            f'#EXT-X-TARGETDURATION:{self.target_duration}',
            '#EXT-X-MEDIA-SEQUENCE:0',
            '#EXT-X-PLAYLIST-TYPE:VOD',
        ]
        prev_key = None
        prev_map = None
        for idx, seg in enumerate(self.segments[start_idx:]):
            if idx > 0 and seg['disc']:
                lines.append('#EXT-X-DISCONTINUITY')
            if seg['key_info'] != prev_key and seg['key_info']:
                raw_k, abs_k = seg['key_info']
                prox_k = f"/api/hls/asset?{ident}&u={urllib.parse.quote(abs_k, safe='')}"
                lines.append(re.sub(r'URI="[^"]+"', f'URI="{prox_k}"', raw_k))
                prev_key = seg['key_info']
            if seg['map_url'] != prev_map and seg['map_url']:
                prox_m = f"/api/hls/asset?{ident}&u={urllib.parse.quote(seg['map_url'], safe='')}"
                lines.append(f'#EXT-X-MAP:URI="{prox_m}"')
                prev_map = seg['map_url']
            lines.append(f"#EXTINF:{seg['dur']:.3f},")
            lines.append(f"/api/hls/seg?{ident}&i={seg['idx']}")
        lines.append('#EXT-X-ENDLIST')
        return '\n'.join(lines) + '\n', int(round(self.segments[start_idx]['start_sec'] * 1000))

    def _run_fetch_slot(self, idx, slot):
        try:
            seg = self.segments[idx]
            url = make_safe_url(seg['url'])
            headers = {'User-Agent': USER_AGENT, 'Referer': 'https://hdstream4u.com/'}
            last_exc = None
            for _attempt in range(3):
                got_any = False
                try:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=18) as resp:
                        ctype = resp.headers.get('Content-Type') or 'video/MP2T'
                        with slot.cond:
                            slot.ctype = ctype
                        read_fn = getattr(resp, 'read1', resp.read)
                        while True:
                            chunk = read_fn(32768)
                            if not chunk:
                                break
                            got_any = True
                            with slot.cond:
                                slot.chunks.append(chunk)
                                slot.cond.notify_all()
                    if got_any or _attempt == 2:
                        break
                except Exception as exc:
                    last_exc = exc
                    if got_any:
                        break
                    time.sleep(0.25)
            full_bytes = b''.join(slot.chunks)
            if not full_bytes and last_exc:
                raise last_exc
            if full_bytes:
                with self.seg_lock:
                    self.seg_cache[idx] = (slot.ctype, full_bytes)
                    self.seg_cache.move_to_end(idx)
                    while len(self.seg_cache) > MAX_HLS_SEG_CACHE:
                        self.seg_cache.popitem(last=False)
            with slot.cond:
                slot.done = True
                slot.cond.notify_all()
        except Exception as exc:
            with slot.cond:
                slot.err = exc
                slot.done = True
                slot.cond.notify_all()
        finally:
            with self.seg_lock:
                self.in_flight.pop(idx, None)

    def _start_or_get_slot(self, idx):
        with self.seg_lock:
            cached = self.seg_cache.get(idx)
            if cached is not None:
                self.seg_cache.move_to_end(idx)
                return cached, None
            slot = self.in_flight.get(idx)
            if slot is None:
                slot = _HlsSegSlot()
                self.in_flight[idx] = slot
                threading.Thread(target=self._run_fetch_slot, args=(idx, slot), daemon=True).start()
            return None, slot

    def _schedule_prefetch(self, idx):
        if idx < 0 or idx >= len(self.segments):
            return
        with self.seg_lock:
            if idx in self.seg_cache or idx in self.in_flight:
                return
        self._start_or_get_slot(idx)

    def stream_segment_to(self, idx, send_head_fn, wfile):
        """Streams HLS segment bytes immediately via shared slot while prefetching next 2 segments."""
        self.ensure_init()
        if idx < 0 or idx >= len(self.segments):
            return False
        self._schedule_prefetch(idx + 1)
        self._schedule_prefetch(idx + 2)
        cached, slot = self._start_or_get_slot(idx)
        if cached is not None:
            ctype, data = cached
            send_head_fn(ctype, len(data))
            for pos in range(0, len(data), 65536):
                wfile.write(data[pos:pos + 65536])
            wfile.flush()
            return True

        sent_headers = False
        chunk_idx = 0
        while True:
            with slot.cond:
                while chunk_idx >= len(slot.chunks) and not slot.done:
                    slot.cond.wait(timeout=18.0)
                    if chunk_idx >= len(slot.chunks) and not slot.done:
                        break
                new_chunks = slot.chunks[chunk_idx:]
                chunk_idx = len(slot.chunks)
                done = slot.done
                err = slot.err
                ctype = slot.ctype
            if not sent_headers:
                if err and not new_chunks:
                    return False
                if not new_chunks and not done:
                    return False
                send_head_fn(ctype, None)
                sent_headers = True
            for ch in new_chunks:
                wfile.write(ch)
            if new_chunks:
                wfile.flush()
            if done:
                return True


_HLS_SESSIONS = OrderedDict()
_HLS_LOCK = threading.Lock()

def get_hls_session(master_url, sid=''):
    with _HLS_LOCK:
        sess = _HLS_SESSIONS.get(master_url)
        if sess is None:
            sess = HLSSession(master_url, sid=sid)
            _HLS_SESSIONS[master_url] = sess
            while len(_HLS_SESSIONS) > MAX_HLS_SESSIONS:
                _HLS_SESSIONS.popitem(last=False)
        else:
            if sid and not sess.sid:
                sess.sid = sid
            _HLS_SESSIONS.move_to_end(master_url)
        return sess

def get_lan_ip():
    """Detects the PC's active LAN IPv4 address so phones on the same Wi-Fi can connect."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith('127.'):
            return ip
    except Exception:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip and not ip.startswith('127.') and ':' not in ip:
                return ip
    except Exception:
        pass
    return '127.0.0.1'

def make_qr_svg(text):
    """Generates a crisp inline SVG QR code for the given URL/text."""
    if not qrcode:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text x="8" y="55" font-size="10">pip install qrcode</text></svg>'
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    m = qr.get_matrix()
    n = len(m)
    rects = [f'<rect x="{x}" y="{y}" width="1" height="1"/>' for y, row in enumerate(m) for x, val in enumerate(row) if val]
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges" style="background:#fff;border-radius:10px;display:block;width:100%;height:100%;"><g fill="#050811">{"".join(rects)}</g></svg>'


def format_bytes(n):
    try:
        n = float(n)
        for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
            if n < 1024.0:
                return f"{n:.2f} {unit}" if unit in ('GB', 'TB') else f"{n:.1f} {unit}"
            n /= 1024.0
    except Exception:
        pass
    return "4K UHD"


def parse_remote_zip(zip_url):
    """Reads EOCD & Central Directory from a remote ZIP (incl. ZIP64) using minimal HTTP Range requests."""
    if not is_range_capable(zip_url):
        return 0, []
    try:
        # 1. Determine total ZIP archive size
        resp, st = open_upstream_range(zip_url, range_hdr='bytes=0-0', hedges=(3, 6), timeout=12)
        with resp:
            if st not in (200, 206):
                return 0, []
            cr = resp.headers.get('Content-Range', '')
            m = re.search(r'/(\d+)', cr)
            total = int(m.group(1)) if m else int(resp.headers.get('Content-Length', 0))
        if total < 22:
            return 0, []

        # 2. Fetch last 64KB (EOCD + ZIP64 EOCD Locator)
        tail_s = max(0, total - 65536)
        r_tail, st2 = open_upstream_range(zip_url, range_hdr=f'bytes={tail_s}-{total - 1}', hedges=(3, 6), timeout=12)
        with r_tail:
            if st2 not in (200, 206):
                return total, []
            tail = r_tail.read()

        eocd_idx = tail.rfind(b'PK\x05\x06')
        if eocd_idx == -1:
            return total, []
        _, _, _, n_tot, cd_size, cd_off, _ = struct.unpack_from('<HHHHIIH', tail, eocd_idx + 4)

        # Check ZIP64 EOCD Locator right before standard EOCD
        if eocd_idx >= 20 and tail[eocd_idx - 20:eocd_idx - 16] == b'PK\x06\x07':
            z64_off = struct.unpack_from('<Q', tail, eocd_idx - 12)[0]
            loc = z64_off - tail_s
            if 0 <= loc <= len(tail) - 56 and tail[loc:loc + 4] == b'PK\x06\x06':
                _, _, _, _, _, _, _, n_tot64, cd_size64, cd_off64 = struct.unpack_from('<4sQHHIIQQQQ', tail, loc)
                cd_size, cd_off, n_tot = cd_size64, cd_off64, n_tot64

        if cd_size <= 0 or cd_off + cd_size > total:
            return total, []

        # 3. Slice or fetch Central Directory
        if cd_off >= tail_s and cd_off + cd_size <= total:
            cd = tail[cd_off - tail_s : cd_off - tail_s + cd_size]
        else:
            r_cd, st3 = open_upstream_range(zip_url, range_hdr=f'bytes={cd_off}-{cd_off + cd_size - 1}', hedges=(3, 6), timeout=12)
            with r_cd:
                if st3 not in (200, 206):
                    return total, []
                cd = r_cd.read()

        raw_entries = []
        pos = 0
        vid_exts = ('.mkv', '.mp4', '.avi', '.mov', '.ts', '.m4v', '.webm')
        while pos + 46 <= len(cd) and cd[pos:pos + 4] == b'PK\x01\x02':
            _, _, flags, method, _, _, _, csize, usize, fnlen, exlen, cmlen, _, _, _, lhoff = struct.unpack_from('<HHHHHHIIIHHHHHII', cd, pos + 4)
            fname = cd[pos + 46:pos + 46 + fnlen].decode('utf-8', errors='replace')
            extra = cd[pos + 46 + fnlen:pos + 46 + fnlen + exlen]
            epos = 0
            while epos + 4 <= len(extra):
                eid, esz = struct.unpack_from('<HH', extra, epos)
                if eid == 0x0001:
                    ed = extra[epos + 4:epos + 4 + esz]
                    eo = 0
                    if usize == 0xFFFFFFFF and eo + 8 <= len(ed):
                        usize = struct.unpack_from('<Q', ed, eo)[0]; eo += 8
                    if csize == 0xFFFFFFFF and eo + 8 <= len(ed):
                        csize = struct.unpack_from('<Q', ed, eo)[0]; eo += 8
                    if lhoff == 0xFFFFFFFF and eo + 8 <= len(ed):
                        lhoff = struct.unpack_from('<Q', ed, eo)[0]; eo += 8
                epos += 4 + esz
            pos += 46 + fnlen + exlen + cmlen
            if not fname.endswith('/') and usize > 0 and fname.lower().endswith(vid_exts) and method in (0, 8):
                raw_entries.append({
                    'fname': fname,
                    'short_name': os.path.basename(fname),
                    'method': method,
                    'usize': usize,
                    'csize': csize,
                    'lhoff': lhoff,
                })

        if not raw_entries:
            return total, []

        # 4. Resolve exact data_start from Local File Header in parallel
        def _resolve_lfh(item):
            lh = item['lhoff']
            try:
                rl, stl = open_upstream_range(zip_url, range_hdr=f'bytes={lh}-{lh + 255}', hedges=(3, 6), timeout=10)
                with rl:
                    if stl not in (200, 206):
                        return None
                    hdr = rl.read(256)
                if len(hdr) < 30 or hdr[:4] != b'PK\x03\x04':
                    return None
                _, _, _, _, _, _, _, _, _, fnl, exl = struct.unpack_from('<IHHHHHIIIHH', hdr, 0)
                item['data_start'] = lh + 30 + fnl + exl
                return item
            except Exception:
                return None

        resolved = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            for res in ex.map(_resolve_lfh, raw_entries):
                if res:
                    resolved.append(res)

        def _nat_key(e):
            return [int(p) if p.isdigit() else p.lower() for p in re.split(r'(\d+)', e['short_name'])]
        resolved.sort(key=_nat_key)
        return total, resolved
    except Exception as e:
        print("[RemoteZIP] Parse error:", e)
        return 0, []

def resolve_gpdl(gpdl_url, referer=None):
    """Follows GPDL redirect to cryptonewz and extracts direct Google UserContent CDN link."""
    try:
        headers = {
            'User-Agent': USER_AGENT,
            'Referer': referer or 'https://gamerxyt.com/'
        }
        req = urllib.request.Request(gpdl_url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            final_url = resp.url
            parsed = urllib.parse.urlparse(final_url)
            qs = urllib.parse.parse_qs(parsed.query)
            if 'link' in qs and qs['link'][0].startswith('http'):
                return qs['link'][0]
            html = resp.read().decode('utf-8', errors='ignore')
            m = re.search(r'href=["\'](https?://video-downloads\.googleusercontent\.com/[^"\']+)["\']', html)
            if m:
                return m.group(1)
            m = re.search(r'id=["\']downloadBtn["\'][^>]*href=["\'](https?://[^"\']+)["\']', html)
            if m:
                return m.group(1)
    except urllib.error.HTTPError:
        pass
    except Exception as e:
        print("[GPDL] Resolve note:", e)
    return None

def unpack_packer(text):
    """De-obfuscates Dean Edwards packed JavaScript to extract hidden HLS video URLs."""
    m = re.search(r'eval\((function\(p,a,c,k,e,d\).+?)\)\s*</script>', text, re.DOTALL)
    if not m:
        m = re.search(r'eval\((function\(p,a,c,k,e,d\).+?)\)', text, re.DOTALL)
    if m:
        raw = m.group(1)
        m_args = re.search(r"\}\('(.*)',\s*(\d+),\s*(\d+),\s*'([^']+)'\.split\('\|'\)", raw, re.DOTALL)
        if m_args:
            p, a, c, k = m_args.groups()
            a, c, k = int(a), int(c), k.split('|')
            def baseN(num, b):
                return ((num == 0) and "0") or (baseN(num // b, b).lstrip("0") + "0123456789abcdefghijklmnopqrstuvwxyz"[num % b])
            while c > 0:
                c -= 1
                key = baseN(c, a)
                val = k[c] if c < len(k) and k[c] else key
                p = re.sub(r'\b' + key + r'\b', val, p)
            return p
    return None

def resolve_hdstream4u(url):
    """Resolves hdstream4u.com file URLs to adaptive HLS master.m3u8 streams."""
    try:
        headers = {'User-Agent': USER_AGENT, 'Referer': f'{HDHUB4U_BASE_URL}/'}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode('utf-8', errors='ignore')
        unpacked = unpack_packer(text)
        if unpacked:
            m3u8s = re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', unpacked)
            if m3u8s:
                return m3u8s[0]
            hlss = re.findall(r'https?://[^\s"\'<>]+\.txt[^\s"\'<>]*', unpacked)
            if hlss:
                return hlss[0]
        m = re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', text)
        if m:
            return m[0]
    except Exception as e:
        print("[HDStream4u] error:", e)
    return None

def parse_m3u8_audio_tracks(m3u8_url):
    """Directly extracts audio track metadata from HLS master playlist."""
    tracks = []
    try:
        headers = {'User-Agent': USER_AGENT, 'Referer': 'https://hdstream4u.com/'}
        req = urllib.request.Request(m3u8_url, headers=headers)
        with urllib.request.urlopen(req, timeout=6) as resp:
            content = resp.read().decode('utf-8', errors='ignore')
        track_counter = 1
        for m in re.finditer(r'#EXT-X-MEDIA:TYPE=AUDIO.*?NAME="([^"]+)".*?LANGUAGE="([^"]*)"', content):
            name = m.group(1)
            lang = m.group(2)
            tracks.append({
                "track_num": track_counter,
                "stream_index": track_counter - 1,
                "lang": name,
                "codec": "AAC",
                "channels": "Stereo",
                "label": f"Track {track_counter}: {name} ({lang.upper() if lang else 'Audio'})"
            })
            track_counter += 1
    except Exception as e:
        print("[M3U8 Audio Probe] error:", e)
    return tracks

# ── HDHub4u Catalog, Categories & Search Scraper ──────────────────────────────

ALLOWED_HDHUB_CATEGORIES = {
    '', 'bollywood-movies', 'hollywood-movies', 'hindi-dubbed',
    'south-hindi-movies', 'web-series', 'tv-shows', 'dual-audio',
    'animated-movies', 'crunchyroll', 'netflix', 'amazon-prime-video',
    'jiohotstar', 'action-movies', 'thriller', 'horror-movies',
    'comedy-movies', 'sci-fi', 'imax', 'bluray', 'hd-movies'
}

def _is_hdhub4u_series(title, url=''):
    t = f"{title or ''} {url or ''}"
    return bool(re.search(
        r'\b(?:season|series|all\s*episodes?|full\s*series|tv\s*show|s0\d|s1\d|s2\d|bigg\s*boss)\b|web-series',
        t, re.I
    ))

def _extract_hdhub4u_badges(title):
    t = title or ''
    parts = []
    if re.search(r'\b(?:2160p|4k|ds4k)\b', t, re.I):
        parts.append('4K UHD')
    elif '1080p' in t.lower():
        parts.append('1080p')
    elif '720p' in t.lower():
        parts.append('720p')

    if 'dual audio' in t.lower() or ('hindi' in t.lower() and 'english' in t.lower()):
        parts.append('Hindi & English')
    elif 'hindi' in t.lower():
        parts.append('Hindi')

    yr_m = re.search(r'\b(19\d{2}|20\d{2})\b', t)
    if yr_m:
        parts.append(yr_m.group(1))
    return " • ".join(parts) if parts else "HDHub4u"

def _normalize_hdhub_post_url(href):
    if not href:
        return ''
    href = href.strip()
    if href.startswith('//'):
        return 'https:' + href
    if href.startswith('/'):
        return f"{HDHUB4U_BASE_URL}{href}"
    pu = urllib.parse.urlparse(href)
    if 'hdhub4u' in pu.netloc.lower() and pu.path:
        return f"{HDHUB4U_BASE_URL}{pu.path}"
    return href

FOURK_BASE_URL = "https://4khdhub.one"
ALLOWED_4K_CATEGORIES = {
    '', '2160p-HDR', 'hindi-movies', 'english-movies',
    'hindi-series', 'english-series', 'korean-series',
    'anime', 'movies', 'series', 'netflix', 'amazon_prime_video', 'jiohotstar'
}
_FOURK_LIST_CACHE = {}
_FOURK_LIST_LOCK = threading.Lock()

def _is_4khdhub_url(url):
    return bool(re.search(r'4khdhub\.[a-z]+/', str(url or ''), re.I))

def _parse_4khdhub_cards(html):
    """Extracts movie and web-series cards from 4khdhub.one HTML with minimal metadata tags."""
    results = []
    cards = re.findall(
        r'<a\s+[^>]*href=["\'](/[^/\'"\s]+-(?:movie|series|tv|show)-\d+/?|https?://4khdhub\.[a-z]+/[^/\'"\s]+-(?:movie|series|tv|show)-\d+/?)["\'][^>]*class=["\'][^"\']*movie-card[^"\']*["\'][^>]*>(.*?)</a>',
        html, re.DOTALL | re.I
    )
    seen = set()
    for link, inner in cards:
        full_link = link if link.startswith('http') else f"{FOURK_BASE_URL}{link}"
        if full_link in seen:
            continue
        seen.add(full_link)
        is_series = bool(re.search(r'-(?:series|tv|show)-\d+', full_link, re.I))
        img_m = re.search(r'<img\b[^>]*>', inner, re.I)
        thumb = ''
        alt_txt = ''
        if img_m:
            tag = img_m.group(0)
            for attr in ('data-src', 'data-lazy-src', 'src'):
                sm = re.search(rf'\b{attr}=["\']([^"\']+)["\']', tag, re.I)
                if sm and not sm.group(1).startswith('data:'):
                    thumb = sm.group(1).strip()
                    break
            am = re.search(r'\balt=["\']([^"\']+)["\']', tag, re.I)
            if am:
                alt_txt = am.group(1).strip()
        title = alt_txt
        if not title:
            clean_text = re.sub(r'<[^>]+>', ' ', inner).strip()
            title = re.sub(r'\s+', ' ', clean_text)
        badges = [_clean_tag_text(b) for b in re.findall(r'<span\s+class=["\'][^"\']*quality-badge[^"\']*["\'][^>]*>(.*?)</span>', inner)]
        badges = [b for b in badges if b]
        badges_str = " • ".join(badges) if badges else "4K UHD"
        if is_series and 'series' not in badges_str.lower():
            badges_str = f"SERIES • {badges_str}"
        card_tag = "4K UHD" if any(k in (title + " " + badges_str).lower() for k in ('2160p', '4k', 'uhd', 'hdr', 'remux', 'dv')) else "HD"
        results.append({
            "title": title or "Untitled 4K Release",
            "permalink": full_link,
            "thumbnail": thumb,
            "badges": badges_str,
            "card_tag": card_tag,
            "year": badges_str,
            "is_series": is_series,
            "source": "4khdhub",
        })
    return results

def get_latest_4k_movies(page=1, category=''):
    """Fetches paginated 4K HDR movies & web series from 4khdhub.one with short TTL cache."""
    p = max(1, int(page or 1))
    cat = (category or '').strip()
    if cat not in ALLOWED_4K_CATEGORIES:
        cat = ''
    cache_key = (p, cat)
    now = time.time()
    with _FOURK_LIST_LOCK:
        cached = _FOURK_LIST_CACHE.get(cache_key)
        if cached and (now - cached[0]) < 180:
            return cached[1]
    try:
        if cat:
            url = f"{FOURK_BASE_URL}/category/{cat}/" if p == 1 else f"{FOURK_BASE_URL}/category/{cat}/page/{p}/"
        else:
            url = FOURK_BASE_URL if p == 1 else f"{FOURK_BASE_URL}/page/{p}/"
        html, _ = fetch_html(url, timeout=10)
        items = _parse_4khdhub_cards(html)
        if items:
            with _FOURK_LIST_LOCK:
                _FOURK_LIST_CACHE[cache_key] = (time.time(), items)
        return items
    except Exception as e:
        print("[4KHub Latest] error:", e)
        return []

def search_4khdhub(query, page=1, limit=24):
    """Searches 4khdhub.one for 4K movies and web series."""
    q = (query or '').strip()
    if not q:
        return get_latest_4k_movies(page=page)[:max(1, int(limit or 24))]
    try:
        p = max(1, int(page or 1))
        url = f"{FOURK_BASE_URL}/?s={urllib.parse.quote(q)}" if p == 1 else f"{FOURK_BASE_URL}/page/{p}/?s={urllib.parse.quote(q)}"
        html, _ = fetch_html(url, timeout=9)
        return _parse_4khdhub_cards(html)[:max(1, int(limit or 24))]
    except Exception as e:
        print("[4KHub Search] error:", e)
        return []

def _parse_4khdhub_series(post_url, html):
    """Parser for 4khdhub.one web series (Season ZIP packs + Individual Episode streams + per-episode qualities)."""
    t_m = re.search(r'<title>(.*?)</title>', html, re.I | re.S)
    series_title = _clean_tag_text(t_m.group(1)) if t_m else "4K Web Series"
    series_title = re.sub(r'\s*[-|].*$', '', series_title).strip()

    packs = []
    cp_start = html.find('id="complete-pack"')
    if cp_start == -1:
        cp_start = html.find("id='complete-pack'")
    ep_start = html.find('id="episodes"')
    if ep_start == -1:
        ep_start = html.find("id='episodes'")

    if cp_start != -1:
        cp_sec = html[cp_start:ep_start] if ep_start > cp_start else html[cp_start:]
        for blk in re.split(r'<div[^>]*class=["\'][^"\']*\bdownload-item\b[^"\']*["\'][^>]*>', cp_sec)[1:]:
            links = re.findall(r'href=["\'](https?://(?:greenmotors\.|hubcloud\.|hubdrive\.|hubcdn\.|hblinks\.)[a-z]+/[^"\'\s]+)["\']', blk, re.I)
            if not links:
                continue
            hdr_m = re.search(r'<div[^>]*class=["\'][^"\']*download-header[^"\']*["\'][^>]*>(.*?)</div>', blk, re.S)
            hdr_txt = _clean_tag_text(re.sub(r'<code.*?>.*?</code>', '', hdr_m.group(1), flags=re.S)) if hdr_m else ""
            ep_num_m = re.search(r'class=["\']episode-number[^"\']*["\'][^>]*>(.*?)</div>', blk, re.S)
            s_tag = _clean_tag_text(ep_num_m.group(1)) if ep_num_m else ""
            sz_m = re.search(r'([\d.,]+\s*(?:GB|MB|TB))', blk, re.I)
            sz_str = sz_m.group(1).strip() if sz_m else ""
            label = f"[Season Pack] {s_tag + ' • ' if s_tag and s_tag not in hdr_txt else ''}{hdr_txt or 'Complete Season'}"
            if sz_str and sz_str not in label:
                label += f" [{sz_str}]"
            packs.append({
                "title": label,
                "size": sz_str,
                "link": links[0],
                "fallback_links": links,
            })

    seasons = []
    if ep_start != -1:
        ep_sec = html[ep_start:]
        for s_blk in re.split(r'<div[^>]*class=["\'][^"\']*episode-header[^"\']*["\'][^>]*>', ep_sec)[1:]:
            sn_m = re.search(r'class=["\']episode-number["\'][^>]*>(.*?)</div>', s_blk, re.S)
            st_m = re.search(r'<h3[^>]*class=["\']episode-title["\'][^>]*>(.*?)</h3>', s_blk, re.S)
            meta_m = re.search(r'<div[^>]*class=["\']episode-meta["\'][^>]*>(.*?)</div>', s_blk, re.S)
            sn_str = _clean_tag_text(sn_m.group(1)) if sn_m else ""
            st_str = _clean_tag_text(st_m.group(1)) if st_m else "Season Episodes"
            meta_badges = re.findall(r'<span[^>]*>(.*?)</span>', meta_m.group(1), re.S) if meta_m else []
            meta_str = " • ".join(_clean_tag_text(b) for b in meta_badges if _clean_tag_text(b))

            eps = []
            for e_blk in re.split(r'<div[^>]*class=["\'][^"\']*episode-download-item[^"\']*["\'][^>]*>', s_blk)[1:]:
                links = re.findall(r'href=["\'](https?://(?:greenmotors\.|hubcloud\.|hubdrive\.|hubcdn\.|hblinks\.)[a-z]+/[^"\'\s]+)["\']', e_blk, re.I)
                if not links:
                    continue
                ft_m = re.search(r'class=["\']episode-file-title["\'][^>]*>(.*?)</div>', e_blk, re.S)
                epb_m = re.search(r'class=["\']badge-psa["\'][^>]*>(.*?)</span>', e_blk, re.S)
                sz_m = re.search(r'class=["\']badge-size["\'][^>]*>(.*?)</span>', e_blk, re.S)
                ep_label = _clean_tag_text(epb_m.group(1)) if epb_m else f"EP-{len(eps)+1:02d}"
                ep_sz = _clean_tag_text(sz_m.group(1)) if sz_m else ""
                ep_name = _clean_tag_text(ft_m.group(1)) if ft_m else ep_label
                eps.append({
                    "ep_label": ep_label,
                    "title": ep_name,
                    "size": ep_sz,
                    "link": links[0],
                    "fallback_links": links,
                })

            if eps:
                g_title = f"{sn_str + ' • ' if sn_str and not st_str.startswith(sn_str) else ''}{st_str}"
                if meta_str:
                    g_title += f" ({meta_str})"
                seasons.append({"title": g_title, "season_num": sn_str or st_str, "episodes": eps})

    # Batch decode greenmotors tokens upfront for instant episode play + attach cross-quality variants
    all_gm = [u for p in packs for u in (p.get('fallback_links') or [p['link']]) if 'greenmotors.' in u]
    all_gm += [u for s in seasons for e in s['episodes'] for u in (e.get('fallback_links') or [e['link']]) if 'greenmotors.' in u]
    gm_map = _decode_greenmotors_map(all_gm) if all_gm else {}

    def _pick_decoded(link_list):
        for u in (link_list or []):
            cand = gm_map.get(u, u)
            if cand and _is_playable_resolved_host(cand):
                return cand
        return link_list[0] if link_list else ''

    for p in packs:
        dec = _pick_decoded(p.get('fallback_links') or [p['link']])
        if dec:
            p['link'] = dec
    for s in seasons:
        for e in s['episodes']:
            dec = _pick_decoded(e.get('fallback_links') or [e['link']])
            if dec:
                e['link'] = dec

    # Map matching (season, ep_label) across quality tiers so In-Player Quality & Auto ABR work for series episodes too!
    ep_variants_map = {}
    for s in seasons:
        s_tag = re.search(r'(?:S|Season\s*)0*(\d+)', str(s.get('title') or ''), re.I)
        s_key = s_tag.group(1) if s_tag else '1'
        for e in s['episodes']:
            k = (s_key, str(e.get('ep_label') or '').upper())
            q_lbl = f"{s.get('title', '')} • {e.get('size', '')}".strip(' •')
            ep_variants_map.setdefault(k, []).append({'title': q_lbl, 'link': e['link']})
    for s in seasons:
        s_tag = re.search(r'(?:S|Season\s*)0*(\d+)', str(s.get('title') or ''), re.I)
        s_key = s_tag.group(1) if s_tag else '1'
        for e in s['episodes']:
            k = (s_key, str(e.get('ep_label') or '').upper())
            opts = ep_variants_map.get(k) or []
            if len(opts) > 1:
                e['qualities'] = opts

    # Order seasons so fast streaming tiers (720p/1080p) appear before giant 4K packs
    def _sea_sort_key(s):
        st_l = str(s.get('title') or '').lower()
        s_m = re.search(r'(?:s|season\s*)0*(\d+)', st_l)
        sn = int(s_m.group(1)) if s_m else 1
        q_ord = 0 if '480p' in st_l else (1 if '720p' in st_l else (2 if '1080p' in st_l else 3))
        return (sn, q_ord)
    seasons.sort(key=_sea_sort_key)

    return {
        "success": True,
        "is_series": True,
        "title": series_title,
        "seasons": seasons,
        "packs": packs,
        "qualities": [{"title": p["title"], "link": p["link"]} for p in packs],
    }

def parse_4khdhub_movie(post_url):
    """Parses a 4khdhub.one movie or web-series post page into StreamHub detail format."""
    try:
        html, _ = fetch_html(post_url, timeout=12)
        meta = _extract_hdhub4u_post_meta(html)
        if '-series-' in post_url.lower() or '-tv-' in post_url.lower() or 'id="episodes"' in html or 'id="complete-pack"' in html:
            series_res = _parse_4khdhub_series(post_url, html)
            if series_res.get("seasons") or series_res.get("packs"):
                series_res["meta"] = meta
                return series_res

        parsed_items = []
        seen = set()
        dl_blocks = re.findall(r'<div[^>]*class=["\'][^"\']*download-item[^"\']*["\'][^>]*>(.*?)</div>\s*</div>\s*</div>', html, re.DOTALL)
        if not dl_blocks:
            dl_blocks = re.findall(r'<div[^>]*class=["\'][^"\']*download-item[^"\']*["\'][^>]*>(.*?)(?=<div[^>]*class=["\'][^"\']*download-item|\Z)', html, re.DOTALL)

        for block in dl_blocks:
            gm_links = re.findall(r'href=["\'](https?://(?:greenmotors\.|hubcloud\.|hubdrive\.|hubcdn\.|hblinks\.|hdstream4u\.)[a-z]+/[^"\'\s]+)["\']', block, re.I)
            if not gm_links:
                continue
            g_link = gm_links[0]
            if g_link in seen:
                continue
            seen.add(g_link)
            header_m = re.search(r'<div[^>]*class=["\'][^"\']*download-header[^"\']*["\'][^>]*>(.*?)</div>', block, re.DOTALL)
            item_title = ""
            if header_m:
                text_part = re.sub(r'<code.*?>.*?</code>', '', header_m.group(1), flags=re.DOTALL)
                item_title = _clean_tag_text(text_part)
            sz_m = re.search(r'([\d.,]+\s*(?:GB|MB|TB))', block, re.I)
            size_str = sz_m.group(1).strip() if sz_m else ""
            badges = re.findall(r'<span[^>]*class=["\'][^"\']*badge[^"\']*["\'][^>]*>(.*?)</span>', block)
            clean_badges = [_clean_tag_text(b) for b in badges if '4khdhub' not in b.lower()]
            label = item_title or " • ".join(clean_badges) or "4K UHD Stream"
            if size_str and size_str not in label:
                label += f" [{size_str}]"
            parsed_items.append((label, gm_links))

        all_gm = [u for _, links in parsed_items for u in links if 'greenmotors.' in u]
        gm_map = _decode_greenmotors_map(all_gm) if all_gm else {}
        qualities = []
        for label, links in parsed_items:
            resolved = None
            for u in links:
                cand = gm_map.get(u, u)
                if cand and _is_playable_resolved_host(cand):
                    resolved = cand
                    break
            if resolved:
                tag = "[4K HDR]" if ('2160p' in label.lower() or '4k' in label.lower()) else "[HD Stream]"
                qualities.append({"title": f"{tag} {label}", "link": resolved})

        qualities.sort(key=lambda q: _rank_quality_label(q['title']), reverse=True)
        res = {"success": True, "meta": meta, "qualities": qualities}
        if not qualities:
            res["error"] = "This 4K title has no active streaming link available"
        return res
    except Exception as e:
        return {"success": False, "error": str(e), "qualities": []}

def _parse_hdhub4u_cards(html):
    """Extracts movie and web-series cards from HDHub4u HTML pages."""
    results = []
    seen = set()
    m_rm = re.search(r'<ul[^>]+class=["\'][^"\']*recent-movies[^"\']*["\'][^>]*>(.*?)</ul>', html, re.S | re.I)
    block = m_rm.group(1) if m_rm else html
    items = re.findall(r'<li[^>]*>(.*?)</li>', block, re.S | re.I)
    if not items and m_rm is None:
        items = re.findall(r'(<figure>.*?</figcaption>)', html, re.S | re.I)

    for li in items:
        a_m = re.search(r'<a[^>]+href=["\']([^"\'#]+)["\']([^>]*)>', li, re.I)
        if not a_m:
            continue
        href = a_m.group(1).strip()
        if '/category/' in href or '/tag/' in href or '/page/' in href:
            continue
        full_link = _normalize_hdhub_post_url(href)
        if not full_link or full_link.rstrip('/') == HDHUB4U_BASE_URL.rstrip('/') or full_link in seen:
            continue

        img_m = re.search(r'<img\b[^>]*>', li, re.I)
        thumb = ''
        alt_txt = ''
        if img_m:
            tag = img_m.group(0)
            for attr in ('data-src', 'data-lazy-src', 'src'):
                sm = re.search(rf'\b{attr}=["\']([^"\']+)["\']', tag, re.I)
                if sm and not sm.group(1).startswith('data:'):
                    thumb = sm.group(1).strip()
                    break
            am = re.search(r'\balt=["\']([^"\']+)["\']', tag, re.I)
            if am:
                alt_txt = am.group(1).strip()

        cap_m = re.search(r'<figcaption[^>]*>(.*?)</figcaption>', li, re.S | re.I)
        cap_txt = _clean_tag_text(cap_m.group(1)) if cap_m else ''
        aria_m = re.search(r'aria-label=["\']([^"\']+)["\']', a_m.group(2), re.I)
        raw_title = cap_txt or alt_txt or (aria_m.group(1).strip() if aria_m else '')
        title = _html.unescape(raw_title).strip()
        if not title:
            continue

        seen.add(full_link)
        is_series = _is_hdhub4u_series(title, full_link)
        badges_str = _extract_hdhub4u_badges(title)
        if is_series and 'SERIES' not in badges_str:
            badges_str = f"SERIES • {badges_str}"
        card_tag = '4K UHD' if re.search(r'\b(?:2160p|4k|ds4k)\b', title, re.I) else 'HD'
        results.append({
            "title": title,
            "permalink": full_link,
            "thumbnail": thumb,
            "badges": badges_str,
            "year": badges_str,
            "card_tag": card_tag,
            "is_series": is_series
        })
    return results

def _pingora_query(query, page=1, limit=24):
    """Queries Pingora Typesense index for HDHub4u posts and rewrites links to the verified active mirror."""
    results = []
    seen = set()
    p = max(1, int(page or 1))
    sort_spec = 'sort_by_date:desc' if query.strip() == '*' else '_text_match:desc,sort_by_date:desc'
    api_url = (
        f'{HDHUB4U_SEARCH_API}?q={urllib.parse.quote(query)}'
        f'&query_by=post_title,category,stars,director,imdb_id'
        f'&query_by_weights=4,2,2,2,4'
        f'&sort_by={urllib.parse.quote(sort_spec, safe=":,_")}'
        f'&limit={int(limit)}'
        f'&highlight_fields=none'
        f'&use_cache=true'
        f'&page={p}'
    )
    headers = {
        'User-Agent': USER_AGENT,
        'Accept': '*/*',
        'Referer': f'{HDHUB4U_BASE_URL}/',
        'Origin': HDHUB4U_BASE_URL
    }
    req = urllib.request.Request(api_url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        hits = data.get('hits', [])
        for h in hits:
            doc = h.get('document', {})
            raw_permalink = doc.get('permalink', '')
            pu = urllib.parse.urlparse(raw_permalink)
            fixed_permalink = f"{HDHUB4U_BASE_URL}{pu.path}" if pu.path else raw_permalink
            if fixed_permalink in seen:
                continue
            seen.add(fixed_permalink)
            title = _html.unescape(doc.get('post_title', 'Untitled')).strip()
            yr = str(doc.get('release_year', '') or '').strip()
            is_ser = _is_hdhub4u_series(title, fixed_permalink)
            badges = _extract_hdhub4u_badges(title)
            if yr and yr not in badges:
                badges = f"{badges} • {yr}"
            card_tag = '4K UHD' if re.search(r'\b(?:2160p|4k|ds4k)\b', title, re.I) else 'HD'
            results.append({
                "title":     title,
                "permalink": fixed_permalink,
                "thumbnail": doc.get('post_thumbnail', doc.get('featured_image', '')),
                "badges":    badges,
                "year":      yr or badges,
                "card_tag":  card_tag,
                "is_series": is_ser,
                "imdb_id":   str(doc.get('imdb_id', '') or '').strip(),
            })
    return results

_LIVE_MIRROR_LOCK = threading.Lock()
_LIVE_MIRROR_CHECKED_TS = 0.0
_DETAILS_CACHE_LOCK = threading.Lock()
_DETAILS_CACHE = OrderedDict()  # {slug_key: (ts, dict)}
_POST_VALID_CACHE = {}          # {slug_key: (ts, bool)}

def _hdhub_slug_key(url):
    try:
        pu = urllib.parse.urlparse(str(url or '').strip())
        path = pu.path.strip('/').lower()
        return path or str(url or '').strip().lower()
    except Exception:
        return str(url or '').strip().lower()

def _discover_live_hdhub4u_base(force=False):
    """Finds the active HDHub4u domain if the current one ever rotates (rate-limited)."""
    global HDHUB4U_BASE_URL, _LIVE_MIRROR_CHECKED_TS
    with _LIVE_MIRROR_LOCK:
        now = time.time()
        if not force and (now - _LIVE_MIRROR_CHECKED_TS) < 120:
            return HDHUB4U_BASE_URL
        _LIVE_MIRROR_CHECKED_TS = now
        cur_m = re.search(r'new(\d+)\.hdhub4u', HDHUB4U_BASE_URL)
        start_n = int(cur_m.group(1)) if cur_m else 17
        candidates = (
            [HDHUB4U_BASE_URL]
            + [f"https://new{n}.hdhub4u.cl" for n in range(start_n, start_n + 6)]
            + ["https://hdhub4u.tv", "https://hdhub4u.cl"]
        )
        seen_c = set()
        for cand in candidates:
            if cand in seen_c:
                continue
            seen_c.add(cand)
            try:
                html, final_u = fetch_html(cand + "/", timeout=6)
                fu = urllib.parse.urlparse(final_u)
                if 'hdhub4u' in fu.netloc.lower() and ('recent-movies' in html or 'movie-thumb' in html) and len(_parse_hdhub4u_cards(html)) > 0:
                    HDHUB4U_BASE_URL = f"{fu.scheme}://{fu.netloc}"
                    return HDHUB4U_BASE_URL
            except Exception:
                continue
        return HDHUB4U_BASE_URL

def _fetch_hdhub4u_html(post_url):
    """Fetches an HDHub4u page/post using the verified live mirror domain first (never stalls on dead archived domains)."""
    global HDHUB4U_BASE_URL
    pu = urllib.parse.urlparse(post_url)
    is_hdhub = 'hdhub4u' in pu.netloc.lower() and bool(pu.path)
    target_url = f"{HDHUB4U_BASE_URL}{pu.path}" + (f"?{pu.query}" if pu.query else "") if is_hdhub else post_url
    is_listing = is_hdhub and (pu.path in ('', '/') or pu.path.startswith(('/page/', '/category/')))
    try:
        html, final_url = fetch_html(target_url, timeout=10)
        fu = urllib.parse.urlparse(final_url)
        if 'hdhub4u' in fu.netloc.lower():
            HDHUB4U_BASE_URL = f"{fu.scheme}://{fu.netloc}"
        if is_listing and 'recent-movies' not in html and len(_parse_hdhub4u_cards(html)) == 0:
            raise RuntimeError("Listing page missing movie cards (possible mirror/block)")
        return html, final_url
    except urllib.error.HTTPError as he:
        if he.code == 404:
            raise
        if is_hdhub:
            live_base = _discover_live_hdhub4u_base(force=True)
            fallback = f"{live_base}{pu.path}" + (f"?{pu.query}" if pu.query else "")
            if fallback != target_url:
                return fetch_html(fallback, timeout=10)
        raise
    except Exception:
        if is_hdhub:
            live_base = _discover_live_hdhub4u_base(force=True)
            fallback = f"{live_base}{pu.path}" + (f"?{pu.query}" if pu.query else "")
            if fallback != target_url:
                return fetch_html(fallback, timeout=10)
            if post_url != target_url:
                return fetch_html(post_url, timeout=10)
        raise

def _check_post_has_links(item):
    """Verifies a catalog/search item resolves to an active page with playable links (cached)."""
    url = (item or {}).get('permalink', '')
    if not url:
        return False
    slug = _hdhub_slug_key(url)
    now = time.time()
    cached = _POST_VALID_CACHE.get(slug)
    if cached:
        ts, valid = cached
        ttl = 3600 if valid else 1800
        if (now - ts) < ttl:
            return valid
    det = parse_movie_details(url)
    has_links = bool(det.get("success")) and (
        len(det.get("qualities") or []) > 0 or len(det.get("seasons") or []) > 0
    )
    err_str = str(det.get("error", "") or "")
    # Cache both valid posts and deterministic dead posts (404 / 0 links)
    if has_links or ('404' in err_str) or det.get("success"):
        _POST_VALID_CACHE[slug] = (now, has_links)
    return has_links

def _filter_playable_items(items, max_workers=10):
    """Filters out 404 / dead / no-link posts cleanly, falling back if transient network errors drop all cards."""
    if not items:
        return []
    flags = [True] * len(items)
    had_valid = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as ex:
        fut_map = {ex.submit(_check_post_has_links, it): idx for idx, it in enumerate(items)}
        for fut in concurrent.futures.as_completed(fut_map):
            idx = fut_map[fut]
            try:
                ok = bool(fut.result())
                flags[idx] = ok
                if ok:
                    had_valid = True
            except Exception:
                flags[idx] = False
    filtered = [it for idx, it in enumerate(items) if flags[idx]]
    if filtered or had_valid:
        return filtered
    # If transient mobile carrier block/timeout caused 0/N to verify, return non-404 items rather than blank catalog
    out = []
    for it in items:
        slug = _hdhub_slug_key((it or {}).get('permalink', ''))
        cached = _POST_VALID_CACHE.get(slug)
        if cached and cached[1] is False:
            continue
        out.append(it)
    return out or list(items)

def _merge_card_lists(primary, secondary):
    """Merges two movie lists while preserving newest order and deduplicating by post slug."""
    out = []
    seen = set()
    for lst in (primary or [], secondary or []):
        for it in lst:
            slug = _hdhub_slug_key(it.get('permalink', ''))
            if not slug or slug in seen:
                continue
            seen.add(slug)
            out.append(it)
    return out

def get_latest_hdhub4u_movies(page=1, category='', filter_links=True, max_items=48):
    """Fetches paginated movies & web series from HDHub4u + Pingora live index with 4KHDHub fallback."""
    p = max(1, int(page or 1))
    cat = (category or '').strip()
    if cat not in ALLOWED_HDHUB_CATEGORIES:
        cat = ''

    cards = []
    ping_cards = []
    if not cat:
        def _fetch_site():
            url = f"{HDHUB4U_BASE_URL}/" if p == 1 else f"{HDHUB4U_BASE_URL}/page/{p}/"
            html, _ = _fetch_hdhub4u_html(url)
            return _parse_hdhub4u_cards(html)

        def _fetch_ping():
            return _pingora_query('*', page=p, limit=36)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            f_site = ex.submit(_fetch_site)
            f_ping = ex.submit(_fetch_ping)
            try:
                cards = f_site.result() or []
            except Exception as e:
                print("[HDHub4u Latest] error:", e)
            try:
                ping_cards = f_ping.result() or []
            except Exception as e:
                print("[HDHub4u Pingora] error:", e)
        merged = _merge_card_lists(ping_cards if p == 1 else cards, cards if p == 1 else ping_cards)
    else:
        try:
            url = f"{HDHUB4U_BASE_URL}/category/{cat}/" if p == 1 else f"{HDHUB4U_BASE_URL}/category/{cat}/page/{p}/"
            html, _ = _fetch_hdhub4u_html(url)
            cards = _parse_hdhub4u_cards(html)
        except Exception as e:
            print(f"[HDHub4u Cat {cat}] error:", e)
        if not cards:
            try:
                q_cat = cat.replace('-movies', '').replace('-', ' ')
                cards = _pingora_query(q_cat, page=p, limit=32)
            except Exception as e:
                print(f"[HDHub4u Cat Pingora {cat}] error:", e)
        merged = cards

    if not merged and not cat:
        try:
            merged = fetch_4khdhub_catalog(page=p) or []
        except Exception:
            pass

    if max_items and len(merged) > max_items:
        merged = merged[:max_items]
    if filter_links and merged:
        return _filter_playable_items(merged)
    return merged

_HOME_HUB_LOCK = threading.Lock()
_HOME_HUB_CACHE = {"ts": 0.0, "data": None}

def get_home_hub(force_refresh=False):
    """Fetches Netflix/Prime/Hotstar/Genre horizontal rails + full home catalog from HDHub4u in parallel (2m cache)."""
    now = time.time()
    if not force_refresh:
        with _HOME_HUB_LOCK:
            if _HOME_HUB_CACHE["data"] and (now - _HOME_HUB_CACHE["ts"]) < 120:
                return _HOME_HUB_CACHE["data"]

    _discover_live_hdhub4u_base(force=force_refresh)
    specs = [
        ("latest", "", 48, True),
        ("netflix", "netflix", 20, False),
        ("prime", "amazon-prime-video", 20, False),
        ("hotstar", "jiohotstar", 20, False),
        ("hollywood", "hollywood-movies", 20, False),
        ("bollywood", "bollywood-movies", 20, False),
        ("web_series", "web-series", 20, False),
        ("hindi_dubbed", "hindi-dubbed", 20, False),
        ("south_hindi", "south-hindi-movies", 20, False),
    ]
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        fut_map = {ex.submit(get_latest_hdhub4u_movies, 1, cat, flt, cap): key for key, cat, cap, flt in specs}
        for fut in concurrent.futures.as_completed(fut_map):
            k = fut_map[fut]
            try:
                out[k] = fut.result() or []
            except Exception:
                out[k] = []

    if not out.get("latest"):
        try:
            fb_cards = fetch_4khdhub_catalog(page=1) or []
            if fb_cards:
                out["latest"] = fb_cards
        except Exception:
            pass

    if out.get("latest"):
        with _HOME_HUB_LOCK:
            _HOME_HUB_CACHE["data"] = out
            _HOME_HUB_CACHE["ts"] = time.time()
    return out

def search_hdhub4u(query, page=1, limit=24):
    """Searches all HDHub4u movies & web series via Pingora live index and filters out dead/no-link posts."""
    q = (query or '').strip()
    lim = max(1, min(48, int(limit or 24)))
    if not q:
        items = get_latest_hdhub4u_movies(page=page, filter_links=True)
        return items[:lim] if lim < len(items) else items
    try:
        fetch_lim = min(48, max(lim + 10, int(lim * 1.5)))
        raw_hits = _pingora_query(q, page=page, limit=fetch_lim)
        valid_hits = _filter_playable_items(raw_hits)
        return valid_hits[:lim]
    except Exception as e:
        print("[HDHub4u Search] error:", e)
        return []

def vlc_remote_call(cmd=None, val=None):
    """Queries or sends a playback control command to local PC VLC HTTP interface."""
    allowed_cmds = {
        'pl_pause', 'pl_stop', 'pl_play', 'seek', 'volume',
        'fullscreen', 'audio_track', 'subtitle_track', 'rate',
        'audiodelay', 'subdelay', 'crop_cycle', 'aspect_cycle',
        'load_sub', 'night_boost', 'snapshot', 'topmost', 'skip_intro', 'next_ep'
    }
    url = f"http://127.0.0.1:{VLC_HTTP_PORT}/requests/status.json"
    if cmd:
        if cmd not in allowed_cmds:
            return {"connected": False, "error": "Unsupported VLC command"}
        qs = f"?command={urllib.parse.quote(cmd, safe='')}"
        if val is not None and str(val) != '':
            qs += f"&val={urllib.parse.quote(str(val), safe='')}"
        url += qs

    try:
        auth = "Basic " + base64.b64encode(f":{VLC_HTTP_PASS}".encode('utf-8')).decode('ascii')
        req = urllib.request.Request(url, headers={"Authorization": auth})
        with urllib.request.urlopen(req, timeout=2.6 if cmd == 'snapshot' else 1.8) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
            st = json.loads(raw)
    except Exception as e:
        return {"connected": False, "error": str(e)}

    cat = st.get("information", {}).get("category", {}) if isinstance(st.get("information"), dict) else {}
    meta = cat.get("meta", {}) if isinstance(cat, dict) else {}
    audio_streams = []
    subtitle_streams = []
    for k, sdata in (cat.items() if isinstance(cat, dict) else []):
        if not k.startswith("Stream ") or not isinstance(sdata, dict):
            continue
        try:
            sid_num = int(k.split(" ", 1)[1])
        except Exception:
            continue
        stype = sdata.get("Type", "")
        lang = sdata.get("Language", "")
        desc = sdata.get("Description", "")
        codec = (sdata.get("Codec", "") or "").split("(")[0].strip()
        lbl_parts = [x for x in (lang or desc, codec) if x]
        lbl = " • ".join(lbl_parts) or f"{stype} {sid_num}"
        if desc and lang and lang.lower() not in desc.lower():
            lbl = f"{lang} ({desc})"
        if stype == "Audio":
            audio_streams.append({"id": sid_num, "label": lbl})
        elif stype == "Subtitle":
            subtitle_streams.append({"id": sid_num, "label": lbl})

    audio_streams.sort(key=lambda x: x["id"])
    subtitle_streams.sort(key=lambda x: x["id"])
    vol_raw = st.get("volume", 256) or 0
    return {
        "connected": True,
        "state": st.get("state", "stopped"),
        "time": int(st.get("time", 0) or 0),
        "length": int(st.get("length", 0) or 0),
        "seekable": bool(st.get("seekable", True)),
        "volume_pct": max(0, min(200, round(vol_raw * 100 / 256))),
        "fullscreen": bool(st.get("fullscreen", False)),
        "rate": st.get("rate", 1.0),
        "crop": st.get("crop", "Default"),
        "aspect_ratio": st.get("aspect_ratio", "Auto"),
        "audiodelay_ms": int(st.get("audiodelay_ms", 0) or 0),
        "subdelay_ms": int(st.get("subdelay_ms", 0) or 0),
        "night_boost": bool(st.get("night_boost", False)),
        "topmost": bool(st.get("topmost", False)),
        "snapshot_path": str(st.get("snapshot_path", "") or ""),
        "title": meta.get("filename") or meta.get("title") or "Playing in PC VLC",
        "audio_streams": audio_streams,
        "subtitle_streams": subtitle_streams,
    }

# ── Feature 1, 3, 4, 7 Support: Progress Store, Subtitles, Ratings & App Update ──

_PROGRESS_LOCK = threading.Lock()
_PROGRESS_DATA = None

def _read_progress_disk():
    global _PROGRESS_DATA
    with _PROGRESS_LOCK:
        if _PROGRESS_DATA is not None:
            return dict(_PROGRESS_DATA)
        try:
            if os.path.exists(_PROGRESS_FILE):
                with open(_PROGRESS_FILE, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    _PROGRESS_DATA = raw
                    return dict(_PROGRESS_DATA)
        except Exception:
            pass
        _PROGRESS_DATA = {}
        return {}

def _write_progress_disk(data):
    try:
        items = sorted(data.items(), key=lambda kv: kv[1].get('updated_at', 0), reverse=True)[:200]
        trimmed = dict(items)
        tmp = _PROGRESS_FILE + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(trimmed, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _PROGRESS_FILE)
        return trimmed
    except Exception:
        return data

def save_playback_progress(payload):
    global _PROGRESS_DATA
    key = str(payload.get('key', '') or '').strip()
    if not key:
        return {}
    pos = max(0, int(payload.get('position_sec', 0) or 0))
    dur = max(0, int(payload.get('duration_sec', 0) or 0))
    parent_key = str(payload.get('parent_key', '') or '').strip()
    if dur > 60 and pos >= dur - 25:
        pct = 100.0
    elif dur > 30:
        pct = min(99.5, round(pos * 100.0 / dur, 1))
    else:
        pct = min(95.0, max(2.0, round(pos / 60.0, 1))) if pos >= 5 else 0.0

    rec = {
        "key": key,
        "position_sec": pos,
        "duration_sec": dur,
        "pct": pct,
        "completed": bool(dur > 60 and pos >= dur - 25),
        "title": str(payload.get('title', '') or '')[:180],
        "ep_label": str(payload.get('ep_label', '') or '')[:60],
        "season_idx": int(payload.get('season_idx', -1)) if payload.get('season_idx') is not None else -1,
        "ep_idx": int(payload.get('ep_idx', -1)) if payload.get('ep_idx') is not None else -1,
        "zip_index": int(payload.get('zip_index', -1)) if payload.get('zip_index') is not None else -1,
        "sid": str(payload.get('sid', '') or ''),
        "updated_at": time.time(),
    }
    data = _read_progress_disk()
    with _PROGRESS_LOCK:
        if _PROGRESS_DATA is None:
            _PROGRESS_DATA = data
        _PROGRESS_DATA[key] = rec
        if parent_key and parent_key != key:
            parent_rec = dict(rec)
            parent_rec["key"] = parent_key
            parent_rec["active_ep_key"] = key
            _PROGRESS_DATA[parent_key] = parent_rec
        _PROGRESS_DATA = _write_progress_disk(_PROGRESS_DATA)
    return rec

def delete_playback_progress(key=None):
    global _PROGRESS_DATA
    _read_progress_disk()
    with _PROGRESS_LOCK:
        if key:
            _PROGRESS_DATA.pop(key, None)
        else:
            _PROGRESS_DATA = {}
        _PROGRESS_DATA = _write_progress_disk(_PROGRESS_DATA)

_SUB_DIR = os.path.join(tempfile.gettempdir(), "streamhub_subs")
_SUB_LOCK = threading.Lock()
_SUB_CACHE = OrderedDict()

def _extract_imdb_id(val):
    m = re.search(r'(tt\d{6,9})', str(val or ''), re.I)
    return m.group(1).lower() if m else ""

def _clean_media_search_query(raw_title):
    s = str(raw_title or '')
    s = re.sub(r'^(?:💎|📺|📦|⚡)\s*', '', s).strip()
    s = re.sub(r'\bS\d{1,2}\s*E?\d{0,2}\b.*$', '', s, flags=re.I)
    s = re.sub(r'\b(?:Season|Part)\s*\d+.*$', '', s, flags=re.I)
    s = re.sub(r'\b(?:2160p|4K|DS4K|UHD|1080p|720p|480p|WEB-?DL|WEBRip|BluRay|HDRip|Dual\s*Audio|Hindi|English|ESub|x264|x265|HEVC|10bit|DDP?5\.1).*$', '', s, flags=re.I)
    s = re.sub(r'\(\s*(?:19|20)\d{2}\s*\).*$', '', s)
    s = re.sub(r'\s*\b(?:19|20)\d{2}\b\s*$', '', s)
    s = re.sub(r'[|\[\]_]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()[:70]

def _srt_to_vtt(srt_text):
    txt = str(srt_text or '').lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    if txt.lstrip().startswith('WEBVTT'):
        return txt
    out_lines = ["WEBVTT", ""]
    lines = txt.split('\n')
    n = len(lines)
    for idx, line in enumerate(lines):
        if line.strip().isdigit() and idx + 1 < n and '-->' in lines[idx + 1]:
            continue
        if '-->' in line:
            line = re.sub(r'(\d{1,2}:\d{2}:\d{2}),(\d{3})', r'\1.\2', line)
            line = re.sub(r'\b(\d):(\d{2}:\d{2}\.\d{3})', r'0\1:\2', line)
        out_lines.append(line)
    return "\n".join(out_lines) + "\n"

_RATING_LOCK = threading.Lock()
_RATING_CACHE = {}

def _cinemeta_fetch_json(url, timeout=3.5):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8', errors='replace'))

def _resolve_imdb_and_rating(title='', imdb_id='', year='', is_series=False):
    tt = _extract_imdb_id(imdb_id)
    clean_q = _clean_media_search_query(title)
    cache_k = tt or f"{'s' if is_series else 'm'}:{clean_q.lower()}"
    now = time.time()
    with _RATING_LOCK:
        hit = _RATING_CACHE.get(cache_k)
        if hit and now - hit['ts'] < 3600:
            return hit

    resolved_tt = tt
    rating_val = None
    kinds = ('series', 'movie') if is_series else ('movie', 'series')

    try:
        if tt:
            for knd in kinds:
                try:
                    data = _cinemeta_fetch_json(f"https://v3-cinemeta.strem.io/meta/{knd}/{tt}.json")
                    meta = data.get('meta') or {}
                    r_raw = str(meta.get('imdbRating', '') or '').strip()
                    if r_raw:
                        rating_val = float(r_raw)
                        break
                except Exception:
                    continue
        if not resolved_tt and clean_q:
            q_enc = urllib.parse.quote(clean_q, safe='')
            for knd in kinds:
                try:
                    data = _cinemeta_fetch_json(f"https://v3-cinemeta.strem.io/catalog/{knd}/top/search={q_enc}.json")
                    metas = data.get('metas') or []
                    if not metas:
                        continue
                    best = metas[0]
                    if year:
                        ym = re.search(r'(19|20)\d{2}', str(year))
                        if ym:
                            y_str = ym.group(0)
                            for m_it in metas[:5]:
                                if y_str in str(m_it.get('releaseInfo', '')):
                                    best = m_it
                                    break
                    resolved_tt = _extract_imdb_id(best.get('imdb_id') or best.get('id') or '')
                    r_raw = str(best.get('imdbRating', '') or '').strip()
                    if r_raw:
                        rating_val = float(r_raw)
                    if resolved_tt:
                        break
                except Exception:
                    continue
    except Exception:
        pass

    res = {"imdb_id": resolved_tt, "rating": rating_val, "ts": now}
    with _RATING_LOCK:
        _RATING_CACHE[cache_k] = res
        if resolved_tt:
            _RATING_CACHE[resolved_tt] = res
    return res

def fetch_imdb_ratings_batch(items):
    out = {}
    todo = (items or [])[:24]
    def _one(it):
        key = str(it.get('key', '') or '')
        if not key:
            return None
        r = _resolve_imdb_and_rating(
            title=it.get('title', ''),
            imdb_id=it.get('imdb_id', ''),
            year=it.get('year', ''),
            is_series=bool(it.get('is_series')),
        )
        return key, r

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(_one, todo):
            if res:
                k, r = res
                out[k] = {"imdb_id": r.get("imdb_id", ""), "rating": r.get("rating")}
    return out

_SUB_LANG_NAMES = {
    "eng": "English", "en": "English", "hin": "Hindi", "hi": "Hindi",
    "urd": "Urdu", "tam": "Tamil", "tel": "Telugu", "mal": "Malayalam",
    "kan": "Kannada", "ben": "Bengali", "mar": "Marathi", "pan": "Punjabi",
    "spa": "Spanish", "fre": "French", "fra": "French", "ger": "German",
    "deu": "German", "ita": "Italian", "por": "Portuguese", "ara": "Arabic",
    "jpn": "Japanese", "kor": "Korean", "chi": "Chinese", "rus": "Russian",
}

def search_online_subtitles(query='', imdb_id='', season=None, episode=None):
    is_ser = bool(season and episode) or bool(re.search(r'\bS\d{1,2}\s*E\d{1,2}\b', str(query or ''), re.I))
    m_se = re.search(r'\bS(\d{1,2})\s*E(\d{1,2})\b', str(query or ''), re.I)
    if (not season or not episode) and m_se:
        season = int(m_se.group(1))
        episode = int(m_se.group(2))
    tt = _extract_imdb_id(imdb_id)
    if not tt and query:
        tt = _resolve_imdb_and_rating(title=query, is_series=is_ser).get('imdb_id', '')
    if not tt:
        return {"success": False, "imdb_id": "", "subtitles": [], "error": "Movie IMDb ID not found — you can attach a local .srt file"}

    if season and episode:
        url = f"https://opensubtitles-v3.strem.io/subtitles/series/{tt}:{int(season)}:{int(episode)}.json"
    else:
        url = f"https://opensubtitles-v3.strem.io/subtitles/movie/{tt}.json"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode('utf-8', errors='replace'))
        raw_subs = data.get('subtitles') or []
        prio = {'hin': 0, 'hi': 0, 'eng': 1, 'en': 1}
        raw_subs.sort(key=lambda s: prio.get(str(s.get('lang', '')).lower(), 9))
        subs = []
        for s in raw_subs[:25]:
            s_url = str(s.get('url', '') or '').strip()
            if not s_url:
                continue
            l_code = str(s.get('lang', 'und') or 'und').lower()
            l_name = _SUB_LANG_NAMES.get(l_code, l_code.upper())
            sid_lbl = str(s.get('id', '') or '')
            subs.append({
                "id": sid_lbl,
                "lang_code": l_code,
                "lang": l_name,
                "url": s_url,
                "label": f"{l_name} ({sid_lbl})" if sid_lbl else l_name,
            })
        return {"success": True, "imdb_id": tt, "subtitles": subs}
    except Exception as e:
        return {"success": False, "imdb_id": tt, "subtitles": [], "error": str(e)}

def save_subtitle_asset(sub_url='', srt_text='', label='Subtitle', lang='English'):
    raw_text = str(srt_text or '')
    if sub_url and not raw_text:
        req = urllib.request.Request(sub_url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, identity"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            blob = resp.read()
            if resp.headers.get('Content-Encoding', '').lower() == 'gzip' or blob[:2] == b'\x1f\x8b':
                try:
                    blob = _gzip.decompress(blob)
                except Exception:
                    pass
        try:
            raw_text = blob.decode('utf-8-sig')
        except Exception:
            raw_text = blob.decode('latin-1', errors='replace')

    if not raw_text.strip():
        return {"success": False, "error": "Subtitle file is empty"}

    os.makedirs(_SUB_DIR, exist_ok=True)
    sub_key = hashlib.md5(f"{sub_url}|{label}|{raw_text[:256]}".encode('utf-8', 'ignore')).hexdigest()[:12]
    srt_path = os.path.join(_SUB_DIR, f"sub_{sub_key}.srt")
    with open(srt_path, 'w', encoding='utf-8') as f:
        f.write(raw_text)
    vtt_text = _srt_to_vtt(raw_text)
    with _SUB_LOCK:
        _SUB_CACHE[sub_key] = {
            "sub_key": sub_key,
            "srt_path": srt_path,
            "vtt_text": vtt_text,
            "label": label,
            "lang": lang,
        }
        if len(_SUB_CACHE) > 24:
            _SUB_CACHE.popitem(last=False)
    return {
        "success": True,
        "sub_key": sub_key,
        "srt_path": srt_path,
        "vtt_url": f"/api/sub.vtt?id={sub_key}",
        "label": label,
        "lang": lang,
    }

def get_cached_sub_vtt(sub_key):
    with _SUB_LOCK:
        item = _SUB_CACHE.get(sub_key)
        return item["vtt_text"] if item else None

_CONFIG_LOCK = threading.Lock()
_UPDATE_LOCK = threading.Lock()
_UPDATE_JOB = {
    "state": "idle",
    "pct": 0,
    "downloaded_bytes": 0,
    "total_bytes": 0,
    "message": "",
    "error": "",
    "target_version": "",
    "staged_path": "",
}

def _load_app_config():
    with _CONFIG_LOCK:
        try:
            if os.path.exists(_CONFIG_FILE):
                with open(_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    return d
        except Exception:
            pass
        return {}

def _save_app_config(updates):
    with _CONFIG_LOCK:
        cur = {}
        try:
            if os.path.exists(_CONFIG_FILE):
                with open(_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    cur = d
        except Exception:
            pass
        cur.update(updates or {})
        try:
            os.makedirs(_APP_DATA_DIR, exist_ok=True)
            tmp = _CONFIG_FILE + ".tmp"
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(cur, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _CONFIG_FILE)
        except Exception:
            pass
        return cur

def _normalize_update_source(raw):
    s = str(raw or '').strip()
    if not s:
        return ""
    if s.startswith(('http://', 'https://')):
        m_gh = re.match(r'^https?://github\.com/([^/]+/[^/]+)/?(?:releases/?)?$', s, re.I)
        if m_gh:
            return f"https://api.github.com/repos/{m_gh.group(1).strip('/')}/releases/latest"
        return s
    if re.match(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$', s):
        return f"https://api.github.com/repos/{s}/releases/latest"
    return s

def _get_effective_update_url():
    cfg = _load_app_config()
    raw = STREAMHUB_UPDATE_URL or str(cfg.get("update_url", "") or "").strip() or DEFAULT_UPDATE_URL
    return _normalize_update_source(raw), raw

def _parse_ver_tuple(v):
    return tuple(int(x) for x in re.findall(r'\d+', str(v or '0'))[:4]) or (0,)

def get_app_info_payload():
    eff_url, raw_url = _get_effective_update_url()
    lan_ip = get_lan_ip()
    try:
        apk_ver_code = int(os.environ.get("STREAMHUB_APK_VERSION_CODE", "0") or 0)
    except Exception:
        apk_ver_code = 0
    ota_active = os.path.exists(os.path.join(_APP_DATA_DIR, "ota", "streamhub_app.py"))
    return {
        "version": APP_VERSION,
        "frozen": IS_FROZEN,
        "is_android": apk_ver_code > 0,
        "apk_version_code": apk_ver_code,
        "ota_active": ota_active,
        "exe_path": sys.executable if IS_FROZEN else os.path.abspath(__file__),
        "data_dir": _APP_DATA_DIR,
        "port": _ACTIVE_PORT,
        "lan_url": f"http://{lan_ip}:{_ACTIVE_PORT}",
        "libvlc_ready": bool(_find_libvlc_dir()),
        "update_configured": bool(eff_url),
        "update_url": raw_url or eff_url,
        "platform": sys.platform,
        "window_mode": "desktop" if _DESKTOP_MODE else "server",
        "window_hwnd": int(_WINDOW_HWND or 0),
    }

def _build_update_feed_candidates(eff_url):
    """Build ordered manifest URLs, bypassing api.github.com 60 req/hr rate limits."""
    urls = []
    u = str(eff_url or '').strip()
    if not u:
        return urls
    m_api = re.match(r'^https?://api\.github\.com/repos/([^/]+/[^/]+)/releases/latest/?$', u, re.I)
    if m_api:
        repo = m_api.group(1)
        urls.append(f"https://github.com/{repo}/releases/latest/download/version.json")
        urls.append(f"https://raw.githubusercontent.com/{repo}/main/dist/version.json")
    urls.append(u)
    return urls

def check_app_update():
    info = get_app_info_payload()
    eff_url, _ = _get_effective_update_url()
    if not eff_url:
        return {
            **info,
            "success": True,
            "current_version": APP_VERSION,
            "latest_version": APP_VERSION,
            "update_available": False,
            "download_url": "",
            "apk_download_url": "",
            "apk_sha256": "",
            "py_download_url": "",
            "py_sha256": "",
            "min_apk_version_code": 0,
            "ota_supported": False,
            "sha256": "",
            "notes": "",
            "message": f"StreamHub v{APP_VERSION} ready! Set a GitHub repo (owner/repo) or version.json URL to push updates.",
        }
    try:
        headers = {
            "User-Agent": f"StreamHub/{APP_VERSION}",
            "Accept": "application/json",
            "Cache-Control": "no-cache",
        }
        remote = None
        last_err = None
        for cand_url in _build_update_feed_candidates(eff_url):
            try:
                req = urllib.request.Request(cand_url, headers=headers)
                with urllib.request.urlopen(req, timeout=8) as resp:
                    parsed = json.loads(resp.read().decode('utf-8', errors='replace'))
                if isinstance(parsed, dict) and (parsed.get('version') or ('tag_name' in parsed and 'assets' in parsed)):
                    remote = parsed
                    break
            except Exception as ex:
                last_err = ex
        if remote is None:
            raise last_err or RuntimeError("Update manifest unreachable")

        latest = ""
        dl_url = ""
        apk_dl_url = ""
        apk_sha256 = ""
        py_dl_url = ""
        py_sha256 = ""
        min_apk_code = 0
        sha256 = ""
        notes = ""
        if isinstance(remote, dict) and 'tag_name' in remote and 'assets' in remote:
            latest = str(remote.get('tag_name', '') or '').lstrip('vV').strip() or APP_VERSION
            notes = str(remote.get('body', '') or '').strip()
            assets = remote.get('assets') or []
            manifest_asset = next((a for a in assets if str(a.get('name', '')).lower() == 'version.json'), None)
            if manifest_asset and manifest_asset.get('browser_download_url'):
                try:
                    m_req = urllib.request.Request(manifest_asset['browser_download_url'], headers=headers)
                    with urllib.request.urlopen(m_req, timeout=6) as mr:
                        m_json = json.loads(mr.read().decode('utf-8', errors='replace'))
                    sha256 = str(m_json.get('sha256', '') or '').strip().lower()
                    apk_sha256 = str(m_json.get('apk_sha256', '') or '').strip().lower()
                    py_sha256 = str(m_json.get('py_sha256', '') or '').strip().lower()
                    min_apk_code = int(m_json.get('min_apk_version_code', 0) or 0)
                    if m_json.get('version'):
                        latest = str(m_json['version']).lstrip('vV').strip()
                    if m_json.get('download_url'):
                        dl_url = str(m_json['download_url']).strip()
                    if m_json.get('apk_download_url'):
                        apk_dl_url = str(m_json['apk_download_url']).strip()
                    if m_json.get('py_download_url'):
                        py_dl_url = str(m_json['py_download_url']).strip()
                    if m_json.get('notes'):
                        notes = str(m_json['notes']).strip()
                except Exception:
                    pass
            if not dl_url:
                exe_asset = next((a for a in assets if str(a.get('name', '')).lower().endswith('.exe')), None)
                if exe_asset:
                    dl_url = str(exe_asset.get('browser_download_url', '') or '').strip()
            # ── APK asset — Android users ke liye ──────────────────────────
            if not apk_dl_url:
                apk_asset = next((a for a in assets if str(a.get('name', '')).lower().endswith('.apk')), None)
                if apk_asset:
                    apk_dl_url = str(apk_asset.get('browser_download_url', '') or '').strip()
            # ── OTA Python asset — Android instant feature update ke liye ─
            if not py_dl_url:
                py_asset = next((a for a in assets if str(a.get('name', '')).lower() == 'streamhub_app.py'), None)
                if py_asset:
                    py_dl_url = str(py_asset.get('browser_download_url', '') or '').strip()
        elif isinstance(remote, dict):
            latest = str(remote.get('version', APP_VERSION) or APP_VERSION).lstrip('vV').strip()
            dl_url = str(remote.get('download_url', '') or '').strip()
            apk_dl_url = str(remote.get('apk_download_url', '') or '').strip()
            apk_sha256 = str(remote.get('apk_sha256', '') or '').strip().lower()
            py_dl_url = str(remote.get('py_download_url', '') or '').strip()
            py_sha256 = str(remote.get('py_sha256', '') or '').strip().lower()
            min_apk_code = int(remote.get('min_apk_version_code', 0) or 0)
            sha256 = str(remote.get('sha256', '') or '').strip().lower()
            notes = str(remote.get('notes', '') or '').strip()

        cur_apk_code = int(info.get("apk_version_code", 0) or 0)
        ota_supported = bool(py_dl_url) and (cur_apk_code > 0) and (min_apk_code <= cur_apk_code)
        avail = bool(latest) and (_parse_ver_tuple(latest) > _parse_ver_tuple(APP_VERSION))
        return {
            **info,
            "success": True,
            "current_version": APP_VERSION,
            "latest_version": latest or APP_VERSION,
            "update_available": avail,
            "download_url": dl_url,
            "apk_download_url": apk_dl_url,
            "apk_sha256": apk_sha256,
            "py_download_url": py_dl_url,
            "py_sha256": py_sha256,
            "min_apk_version_code": min_apk_code,
            "ota_supported": ota_supported,
            "sha256": sha256,
            "notes": notes[:600],
            "message": f"New update v{latest} available!" if avail else f"StreamHub v{APP_VERSION} is up to date",
        }
    except Exception as e:
        return {**info, "success": False, "current_version": APP_VERSION, "update_available": False, "error": str(e)}

def get_update_status():
    with _UPDATE_LOCK:
        return dict(_UPDATE_JOB)

def start_app_update(download_url='', expected_sha256='', target_version=''):
    dl = str(download_url or '').strip()
    sha_exp = str(expected_sha256 or '').strip().lower()
    tgt_ver = str(target_version or '').strip()
    if not dl:
        chk = check_app_update()
        dl = chk.get('download_url', '')
        sha_exp = sha_exp or chk.get('sha256', '')
        tgt_ver = tgt_ver or chk.get('latest_version', '')
    if not dl:
        return {"success": False, "error": "Update download_url not found"}

    with _UPDATE_LOCK:
        if _UPDATE_JOB.get("state") in ("downloading", "applying"):
            return {"success": True, **_UPDATE_JOB}
        _UPDATE_JOB.update({
            "state": "downloading",
            "pct": 0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "message": "Downloading StreamHub update...",
            "error": "",
            "target_version": tgt_ver,
            "staged_path": "",
        })

    def _worker():
        os.makedirs(_APP_DATA_DIR, exist_ok=True)
        staged = os.path.join(_APP_DATA_DIR, "StreamHub_update.exe")
        h = hashlib.sha256()
        try:
            req = urllib.request.Request(dl, headers={"User-Agent": f"StreamHub/{APP_VERSION}"})
            with urllib.request.urlopen(req, timeout=45) as resp:
                tot = int(resp.headers.get('Content-Length', 0) or 0)
                with _UPDATE_LOCK:
                    _UPDATE_JOB["total_bytes"] = tot
                done = 0
                with open(staged, 'wb') as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        h.update(chunk)
                        done += len(chunk)
                        pct = min(99, int(done * 100 / tot)) if tot > 0 else 50
                        with _UPDATE_LOCK:
                            _UPDATE_JOB["downloaded_bytes"] = done
                            _UPDATE_JOB["pct"] = pct

            if done < 1024 * 1024:
                raise RuntimeError("Downloaded file is smaller than a valid StreamHub.exe")
            with open(staged, 'rb') as rf:
                if rf.read(2) != b'MZ':
                    raise RuntimeError("Downloaded file is not a valid Windows .exe")
            actual_sha = h.hexdigest().lower()
            if sha_exp and actual_sha != sha_exp:
                raise RuntimeError("Update SHA-256 verification failed")

            with _UPDATE_LOCK:
                _UPDATE_JOB.update({
                    "state": "ready_restart" if IS_FROZEN else "staged",
                    "pct": 100,
                    "staged_path": staged,
                    "message": "Update verified! Restarting app..." if IS_FROZEN else f"Update verified & saved at {staged}",
                })

            if IS_FROZEN:
                current_exe = sys.executable
                ps_path = os.path.join(tempfile.gettempdir(), "streamhub_self_update.ps1")
                log_path = os.path.join(_APP_DATA_DIR, "update.log")
                ps_body = (
                    "$ErrorActionPreference = 'SilentlyContinue'\n"
                    f"$src = '{staged.replace(chr(39), chr(39)*2)}'\n"
                    f"$dst = '{current_exe.replace(chr(39), chr(39)*2)}'\n"
                    f"$log = '{log_path.replace(chr(39), chr(39)*2)}'\n"
                    "Start-Sleep -Milliseconds 1200\n"
                    "$moved = $false\n"
                    "for ($i = 0; $i -lt 30; $i++) {\n"
                    "  try {\n"
                    "    Move-Item -LiteralPath $src -Destination $dst -Force -ErrorAction Stop\n"
                    "    $moved = $true; break\n"
                    "  } catch {\n"
                    "    Start-Sleep -Milliseconds 500\n"
                    "  }\n"
                    "}\n"
                    "if ($moved) {\n"
                    "  Start-Process -FilePath $dst\n"
                    "} else {\n"
                    "  'Move failed' | Out-File -FilePath $log -Encoding utf8\n"
                    "}\n"
                    "Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue\n"
                )
                with open(ps_path, 'w', encoding='utf-8') as pf:
                    pf.write(ps_body)
                clean_env = os.environ.copy()
                for k in list(clean_env.keys()):
                    if k.startswith(("_MEI", "_PYI_", "TCL_", "TK_")):
                        clean_env.pop(k, None)
                clean_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
                _stop_previous_players()
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", ps_path],
                    env=clean_env,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
                def _exit_soon():
                    global _APP_QUITTING
                    time.sleep(0.45)
                    _APP_QUITTING = True
                    try:
                        if _WINDOW_REF is not None:
                            _WINDOW_REF.destroy()
                    except Exception:
                        pass
                    try:
                        if _SERVER_REF:
                            _SERVER_REF.shutdown()
                    except Exception:
                        pass
                    os._exit(0)
                threading.Thread(target=_exit_soon, daemon=True).start()
        except Exception as e:
            try:
                if os.path.exists(staged):
                    os.remove(staged)
            except Exception:
                pass
            with _UPDATE_LOCK:
                _UPDATE_JOB.update({"state": "error", "error": str(e), "message": f"Update error: {e}"})

    threading.Thread(target=_worker, daemon=True).start()
    return {"success": True, **get_update_status()}

def apply_ota_py_update(py_url='', expected_sha256='', target_version=''):
    """Downloads & verifies streamhub_app.py OTA update for Android APK (no 75MB APK reinstall needed)."""
    try:
        cur_apk_code = int(os.environ.get("STREAMHUB_APK_VERSION_CODE", "0") or 0)
    except Exception:
        cur_apk_code = 0
    if cur_apk_code <= 0:
        return {"success": False, "error": "OTA Python update is only supported in Android APK mode"}
    url = str(py_url or '').strip()
    sha_exp = str(expected_sha256 or '').strip().lower()
    tgt_ver = str(target_version or '').strip()
    if not url:
        chk = check_app_update()
        url = str(chk.get("py_download_url", "") or "").strip()
        sha_exp = sha_exp or str(chk.get("py_sha256", "") or "").strip().lower()
        tgt_ver = tgt_ver or str(chk.get("latest_version", "") or "").strip()
        min_code = int(chk.get("min_apk_version_code", 0) or 0)
        if min_code > cur_apk_code:
            return {"success": False, "error": "This update requires installing the new APK"}
    if not url:
        return {"success": False, "error": "OTA script download_url not found"}

    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"StreamHub/{APP_VERSION}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
        if len(raw) < 50_000:
            return {"success": False, "error": "Downloaded update file is incomplete"}
        actual_sha = hashlib.sha256(raw).hexdigest().lower()
        if sha_exp and actual_sha != sha_exp:
            return {"success": False, "error": "OTA SHA-256 verification failed"}
        code_text = raw.decode('utf-8')
        if "def start_android_server" not in code_text:
            return {"success": False, "error": "Invalid StreamHub engine module"}
        compile(code_text, "streamhub_app.py", "exec")

        ota_dir = os.path.join(_APP_DATA_DIR, "ota")
        os.makedirs(ota_dir, exist_ok=True)
        py_tmp = os.path.join(ota_dir, "streamhub_app.py.tmp")
        py_dst = os.path.join(ota_dir, "streamhub_app.py")
        meta_tmp = os.path.join(ota_dir, "ota_meta.json.tmp")
        meta_dst = os.path.join(ota_dir, "ota_meta.json")
        with open(py_tmp, 'wb') as f:
            f.write(raw)
        with open(meta_tmp, 'w', encoding='utf-8') as mf:
            json.dump({
                "version": tgt_ver or APP_VERSION,
                "apk_version_code": cur_apk_code,
                "sha256": actual_sha,
            }, mf, indent=2)
        os.replace(py_tmp, py_dst)
        os.replace(meta_tmp, meta_dst)
        return {
            "success": True,
            "version": tgt_ver,
            "message": f"Instant update v{tgt_ver} installed! Restarting app...",
        }
    except Exception as e:
        return {"success": False, "error": f"OTA update error: {e}"}

def run_setup_action(action):
    if sys.platform != 'win32':
        return {"success": False, "error": "Windows only helper"}
    if action == 'shortcut':
        try:
            target = sys.executable if IS_FROZEN else sys.executable
            args = "" if IS_FROZEN else f'"{os.path.abspath(__file__)}"'
            lnk_path = os.path.join(os.path.expanduser("~"), "Desktop", "StreamHub.lnk")
            ico_cand = os.path.join(os.path.dirname(sys.executable if IS_FROZEN else os.path.abspath(__file__)), "streamhub.ico")
            if not os.path.exists(ico_cand):
                os.makedirs(_APP_DATA_DIR, exist_ok=True)
                ico_cand = os.path.join(_APP_DATA_DIR, "streamhub.ico")
                from PIL import Image
                imgs = [Image.open(io.BytesIO(make_app_icon_png(sz))) for sz in (256, 128, 64, 32, 16)]
                imgs[0].save(ico_cand, format="ICO", sizes=[(im.width, im.height) for im in imgs])
            ps = (
                f'$w=New-Object -ComObject WScript.Shell;'
                f'$s=$w.CreateShortcut("{lnk_path}");'
                f'$s.TargetPath="{target}";'
                + (f'$s.Arguments=\'{args}\';' if args else '')
                + f'$s.WorkingDirectory="{os.path.dirname(sys.executable if IS_FROZEN else os.path.abspath(__file__))}";'
                + f'$s.IconLocation="{ico_cand}";'
                + '$s.Description="StreamHub Ultra 4K Cinema";$s.Save()'
            )
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                return {"success": False, "error": r.stderr or "Shortcut create failed"}
            return {"success": True, "message": "StreamHub shortcut created on Desktop!"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    if action == 'firewall':
        try:
            exe_target = sys.executable
            ps = (
                f"Start-Process netsh -Verb RunAs -Wait -ArgumentList "
                f"'advfirewall firewall add rule name=\"StreamHub App\" dir=in action=allow program=\"{exe_target}\" enable=yes'"
            )
            subprocess.Popen(["powershell", "-NoProfile", "-Command", ps])
            return {"success": True, "message": "Windows Firewall prompt opened — click Allow / Yes!"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    return {"success": False, "error": "Unknown setup action"}

def _clean_tag_text(raw_html):
    txt = _html.unescape(re.sub(r'<[^>]+>', ' ', raw_html))
    return re.sub(r'\s+', ' ', txt).strip()

_PLAYABLE_HOSTS = ('hubcloud', 'hubdrive', 'hubcdn', 'hdstream4u', 'hblinks')
_DIRECT_STREAM_DOMAINS = ('r2.dev', 'r2.cloudflarestorage.com', 'workers.dev', 'pixeldrain', 'googleusercontent.com')
_SKIP_LINK_WORDS = ('admin', 'login', 'register', 'telegram', 'whatsapp', 'facebook', 'twitter', 'instagram', '4khdhub', 'snvhost', 'imdb.com', 'how-to')

def _is_playable_resolved_host(url):
    u = str(url or '').strip().lower()
    if not u.startswith('http'):
        return False
    if any(bad in u for bad in ('hdhub4u.', 'how-to-download', 'telegram', 'whatsapp', 'openload', 'indishare', '9xplay', '9xupload', 'bdupload', 'uploadbaz', 'upfile', 'clicknupload', 'imgur', 'linkscare', 'keeplinks', 'hindifire')):
        return False
    pu = urllib.parse.urlparse(u)
    host = pu.netloc.lower()
    if any(h in host for h in _PLAYABLE_HOSTS) or any(h in host for h in _DIRECT_STREAM_DOMAINS):
        return True
    if pu.path.lower().endswith(('.mkv', '.mp4', '.m3u8')):
        return True
    return False

def _rank_quality_label(label):
    t = (label or '').lower()
    if '2160p' in t or '4k' in t:
        return 95
    if 'hdstream4u' in t or 'hls' in t:
        return 88
    if '1080p' in t and ('web-dl' in t or 'pack' in t):
        return 85
    if '1080p' in t:
        return 80
    if '720p' in t:
        return 60
    if '480p' in t:
        return 30
    return 50

def _decode_greenmotors_map(urls):
    """Decodes greenmotors links in parallel and returns {orig_url: decoded_url}."""
    unique = [u for u in dict.fromkeys(urls) if 'greenmotors.' in (u or '')]
    if not unique:
        return {}
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        fut_map = {ex.submit(decode_greenmotors, u): u for u in unique}
        for fut in concurrent.futures.as_completed(fut_map):
            orig = fut_map[fut]
            try:
                res = fut.result()
                if res:
                    out[orig] = res
            except Exception:
                pass
    return out

def _parse_hdhub4u_series_post(post_url, html):
    """Parses HDHub4u Web Series pages (Season packs + Individual Episode & HLS links)."""
    t_m = re.search(r'<title>(.*?)</title>', html, re.I | re.S)
    series_title = _clean_tag_text(t_m.group(1)) if t_m else "HDHub4u Web Series"
    series_title = re.sub(r'\s*[-|]\s*HDHub4u.*$', '', series_title, flags=re.I).strip()

    main_m = re.search(r'<main[^>]*>(.*?)</main>', html, re.S | re.I)
    body = main_m.group(1) if main_m else html

    packs = []
    seen_pack = set()
    ep_dl_rows = []
    ep_hls_rows = []
    ep_counter = 0

    for hm in re.finditer(r'<h[1-6][^>]*>(.*?)</h[1-6]>', body, re.S | re.I):
        block = hm.group(1)
        plain = _clean_tag_text(block)
        anchors = []
        for am in re.finditer(r'<a[^>]+href=["\'](https?://[^\s"\'<>]+)["\'][^>]*>(.*?)</a>', block, re.S | re.I):
            href = am.group(1).strip()
            if any(k in href.lower() for k in _SKIP_LINK_WORDS):
                continue
            anchors.append((_clean_tag_text(am.group(2)), href))
        if not anchors:
            continue

        ep_m = re.search(r'\bEPiSODE\s*0*(\d+)\b', plain, re.I)
        if ep_m:
            ep_counter += 1
            ep_num = int(ep_m.group(1))
            ep_lbl = f"EP-{ep_num:02d}"
            dl_link = None
            hls_link = None
            for atxt, href in anchors:
                lh = href.lower()
                if 'hdstream4u.com' in lh and not hls_link:
                    hls_link = href
                elif (any(h in lh for h in ('greenmotors.', 'hblinks.', 'hubcloud.', 'hubdrive.', 'hubcdn.'))) and not dl_link:
                    dl_link = href
            if dl_link:
                ep_dl_rows.append({
                    "ep_label": ep_lbl,
                    "title": f"Episode {ep_num} (4K / HD MKV)",
                    "size": "Select Best Quality",
                    "link": dl_link,
                })
            if hls_link:
                ep_hls_rows.append({
                    "ep_label": ep_lbl,
                    "title": f"Episode {ep_num} (Instant HLS Stream)",
                    "size": "Adaptive HLS",
                    "link": hls_link,
                })
            continue

        # Season / Complete Pack rows above single episodes
        for atxt, href in anchors:
            pu = urllib.parse.urlparse(href)
            if not pu.path.strip('/') and 'greenmotors.' not in href:
                continue
            lh = href.lower()
            if not (any(h in lh for h in _PLAYABLE_HOSTS) or 'greenmotors.' in lh):
                continue
            if '/packs/' not in lh and not any(q in atxt.lower() for q in ('pack', '480p', '720p', '1080p', '2160p', '4k', 'hevc', 'gb', 'mb')):
                continue
            if href in seen_pack:
                continue
            seen_pack.add(href)
            sz_m = re.search(r'([\d.,]+\s*(?:GB|MB|TB))', atxt, re.I)
            packs.append({
                "title": atxt or "Season Pack",
                "size": sz_m.group(1).strip() if sz_m else "",
                "link": href
            })

    if packs:
        gm_map = _decode_greenmotors_map([p["link"] for p in packs])
        cleaned_packs = []
        for p in packs:
            if 'greenmotors.' in p["link"]:
                dec = gm_map.get(p["link"])
                if not dec or not _is_playable_resolved_host(dec):
                    continue
                p["link"] = dec
            cleaned_packs.append(p)
        packs = cleaned_packs
        packs.sort(key=lambda p: _rank_quality_label(p["title"]), reverse=True)

    if ep_dl_rows:
        gm_ep_map = _decode_greenmotors_map([e["link"] for e in ep_dl_rows])
        cleaned_eps = []
        for e in ep_dl_rows:
            if 'greenmotors.' in e["link"]:
                dec = gm_ep_map.get(e["link"])
                if not dec or not _is_playable_resolved_host(dec):
                    continue
                e["link"] = dec
            cleaned_eps.append(e)
        ep_dl_rows = cleaned_eps

    seasons = []
    if ep_dl_rows:
        seasons.append({"title": "Single Episodes — 4K / HD MKV", "episodes": ep_dl_rows})
    if ep_hls_rows:
        seasons.append({"title": "Single Episodes — Instant HLS (HDStream4u)", "episodes": ep_hls_rows})

    if not seasons and not packs:
        return None
    return {
        "success": True,
        "is_series": True,
        "title": series_title,
        "seasons": seasons,
        "packs": packs,
        "qualities": [{"title": p["title"], "link": p["link"]} for p in packs]
    }

def _extract_hdhub4u_post_meta(body):
    """Extracts IMDb rating, YouTube trailer, storyline synopsis, TMDB poster & screenshots from HDHub4u post HTML."""
    imdb_rating = ""
    imdb_url = ""
    m_imdb = re.search(r'href=["\'](https?://(?:www\.)?imdb\.com/title/[^"\']+)["\'][^>]*>([^<]+)<', body, re.I)
    if m_imdb:
        imdb_url = m_imdb.group(1).strip()
        imdb_rating = _clean_tag_text(m_imdb.group(2))

    trailer_url = ""
    m_yt = re.search(r'https?://(?:www\.)?(?:youtube\.com/embed/|youtu\.be/|youtube\.com/watch\?v=)([a-zA-Z0-9_-]{8,15})', body, re.I)
    if m_yt:
        trailer_url = f"https://www.youtube.com/embed/{m_yt.group(1)}?autoplay=1&rel=0"

    poster = ""
    m_pos = re.search(r'https?://image\.tmdb\.org/t/p/[^"\'\s<>]+', body, re.I)
    if m_pos:
        poster = m_pos.group(0)

    screenshots = []
    for sm in re.finditer(r'https?://catimages\.org/images/[^"\'\s<>]+', body, re.I):
        su = sm.group(0)
        if su not in screenshots:
            screenshots.append(su)
        if len(screenshots) >= 4:
            break

    clean_html = re.sub(r'<(?:script|style)[^>]*>.*?</(?:script|style)>', ' ', body, flags=re.S | re.I)
    plain = _html.unescape(re.sub(r'<[^>]+>', '\n', clean_html))
    storyline = ""
    for line in plain.splitlines():
        l = re.sub(r'\s+', ' ', line).strip()
        if re.match(r'^Storyline\b', l, re.I):
            storyline = re.sub(r'^Storyline\s*:?\s*', '', l, flags=re.I)
            storyline = re.sub(r'\s*Download\s+.*$', '', storyline, flags=re.I).strip()
            break
    if not storyline:
        m_st = re.search(r'Storyline\s*:?\s*(.{25,360})', re.sub(r'\s+', ' ', plain), re.I)
        if m_st:
            storyline = re.sub(r'\s*Download\s+.*$', '', m_st.group(1), flags=re.I).strip()

    return {
        "imdb_rating": imdb_rating,
        "imdb_url": imdb_url,
        "imdb_id": _extract_imdb_id(imdb_url),
        "trailer_url": trailer_url,
        "poster": poster,
        "storyline": storyline[:340],
        "screenshots": screenshots
    }

def parse_movie_details(post_url):
    """Parses HDHub4u and 4KHDHub movie/web-series posts (Movies + Season Packs + Episodes + HLS), cached 15m."""
    slug = _hdhub_slug_key(post_url)
    now = time.time()
    with _DETAILS_CACHE_LOCK:
        cached = _DETAILS_CACHE.get(slug)
        if cached and (now - cached[0]) < 900:
            _DETAILS_CACHE.move_to_end(slug)
            return cached[1]
    if _is_4khdhub_url(post_url):
        res_4k = parse_4khdhub_movie(post_url)
        if res_4k.get("qualities") or res_4k.get("seasons") or res_4k.get("packs"):
            with _DETAILS_CACHE_LOCK:
                _DETAILS_CACHE[slug] = (time.time(), res_4k)
                while len(_DETAILS_CACHE) > 400:
                    _DETAILS_CACHE.popitem(last=False)
        return res_4k
    try:
        html, _ = _fetch_hdhub4u_html(post_url)
        main_m = re.search(r'<main[^>]*>(.*?)</main>', html, re.S | re.I)
        body = main_m.group(1) if main_m else html
        meta = _extract_hdhub4u_post_meta(body)

        if re.search(r'\bEPiSODE\s*\d+|hubdrive\.[^"\']+/packs/', body, re.I):
            series_res = _parse_hdhub4u_series_post(post_url, html)
            if series_res:
                series_res["meta"] = meta
                with _DETAILS_CACHE_LOCK:
                    _DETAILS_CACHE[slug] = (time.time(), series_res)
                    while len(_DETAILS_CACHE) > 400:
                        _DETAILS_CACHE.popitem(last=False)
                return series_res

        qualities = []
        seen = set()

        m_stream = re.search(r'https?://hdstream4u\.com/file/([a-zA-Z0-9]+)', body)
        if m_stream:
            hdstream_link = m_stream.group(0)
            seen.add(hdstream_link)
            qualities.append({
                "title": "⚡ [HLS Fast Stream] HDStream4u (Instant Browser & VLC Seek)",
                "link": hdstream_link
            })

        gm_candidates = []
        for m in re.finditer(r'<a[^>]+href=["\'](https?://[^\s"\'<>]+)["\'][^>]*>(.*?)</a>', body, re.I | re.DOTALL):
            href = m.group(1).strip()
            text = _clean_tag_text(m.group(2))
            if href in seen:
                continue
            pu = urllib.parse.urlparse(href)
            if not pu.path.strip('/') and 'greenmotors.' not in href:
                continue
            if any(k in href.lower() for k in _SKIP_LINK_WORDS):
                continue
            if 'greenmotors.' in href:
                seen.add(href)
                gm_candidates.append((text or "Download Link", href))
                continue
            if not any(d in pu.netloc.lower() for d in _PLAYABLE_HOSTS):
                continue
            if any(bad in href.lower() for bad in ('/how-to', '/contact', '/dmca', '/about', '/faq')):
                continue
            seen.add(href)
            qualities.append({"title": text or "Download Link", "link": href})

        gm_map = _decode_greenmotors_map([lnk for _, lnk in gm_candidates])
        for lbl, lnk in gm_candidates:
            hc = gm_map.get(lnk)
            if hc and _is_playable_resolved_host(hc):
                qualities.append({"title": lbl, "link": hc})

        qualities.sort(key=lambda q: _rank_quality_label(q['title']), reverse=True)
        res = {"success": True, "meta": meta, "qualities": qualities}
        if not qualities:
            res["error"] = "No active streaming link available for this movie"
        with _DETAILS_CACHE_LOCK:
            _DETAILS_CACHE[slug] = (time.time(), res)
            while len(_DETAILS_CACHE) > 400:
                _DETAILS_CACHE.popitem(last=False)
        return res
    except Exception as e:
        return {"success": False, "error": str(e), "qualities": []}


def _extract_hblinks_options(html):
    """Extracts all playable stream options with clean labels from an HBLInks archive page."""
    candidates = []
    seen = set()
    for hm in re.finditer(r'<h[2-6][^>]*>(.*?)</h[2-6]>', html, re.S | re.I):
        row_txt = _clean_tag_text(hm.group(1))
        for am in re.finditer(r'<a[^>]+href=["\'](https?://[^"\'\s<>]+)["\'][^>]*>(.*?)</a>', hm.group(1), re.S | re.I):
            href = am.group(1).strip()
            atxt = _clean_tag_text(am.group(2))
            lh = href.lower()
            if href in seen or not any(h in lh for h in ('hubcloud', 'hubdrive', 'hubcdn', 'gdflix', 'filepress')):
                continue
            seen.add(href)
            host_bonus = 6 if 'hubcloud' in lh else (4 if 'hubdrive' in lh else (2 if 'hubcdn' in lh else 0))
            lbl = row_txt if (row_txt and len(row_txt) > len(atxt)) else (atxt or row_txt or 'HD Stream')
            candidates.append({'title': lbl, 'link': href, 'host_bonus': host_bonus})
    return candidates

def _pick_best_hblinks_url(html, pref_quality=None, pref_lang=None):
    """Picks fastest-matching quality mirror from HBLInks (respecting auto 480p-start & host reliability)."""
    opts = _extract_hblinks_options(html)
    if not opts:
        return None
    if pref_quality is None or pref_lang is None:
        try:
            uc = get_user_settings()
            if pref_quality is None:
                pref_quality = (uc.get('download_prefs') or {}).get('stream_quality') or 'auto'
            if pref_lang is None:
                pref_lang = (uc.get('profile') or {}).get('preferred_language') or 'Auto'
        except Exception:
            pref_quality = pref_quality or 'auto'
            pref_lang = pref_lang or 'Auto'
    opts.sort(
        key=lambda o: _score_quality_candidate(o['title'], pref_quality, pref_lang, link=o['link']) + o['host_bonus'],
        reverse=True
    )
    return opts[0]['link']

def _resolve_hubdrive_pack(html, base_url):
    """Parses a HubDrive /packs/<id> page into direct individual episode MKV links (+ full ZIP pack option)."""
    pu = urllib.parse.urlparse(base_url)
    origin = f"{pu.scheme}://{pu.netloc}"
    t_m = re.search(r'<title>(.*?)</title>', html, re.I | re.S)
    pack_title = _clean_tag_text(t_m.group(1)) if t_m else "HubDrive Pack"
    pack_title = re.sub(r'^HubDrive\s*\|\s*', '', pack_title, flags=re.I).strip()

    episodes = []
    zip_packs = []
    seen = set()
    rows = re.split(r'<div[^>]*class=["\'][^"\']*\bpack-clean-file\b[^"\']*["\']', html, flags=re.I)[1:]
    if not rows:
        for fm in re.finditer(r'href=["\'](/file/[a-zA-Z0-9]+)["\']', html):
            full = f"{origin}{fm.group(1)}"
            if full not in seen:
                seen.add(full)
                episodes.append({"ep_label": f"EP-{len(episodes)+1:02d}", "title": f"Episode {len(episodes)+1}", "size": "", "link": full})
    else:
        for row in rows:
            fm = re.search(r'(/file/[a-zA-Z0-9]+)', row)
            if not fm:
                continue
            full = f"{origin}{fm.group(1)}"
            if full in seen:
                continue
            seen.add(full)
            nm_m = re.search(r'pack-clean-title[^>]*>(.*?)</div>', row, re.S | re.I)
            sz_m = re.search(r'pack-clean-size[^>]*>(.*?)</div>', row, re.S | re.I)
            fname = _clean_tag_text(nm_m.group(1)) if nm_m else fm.group(1)
            fsize = _clean_tag_text(sz_m.group(1)) if sz_m else ""
            if fname.lower().endswith('.zip') or 'pack-archive' in row:
                zip_packs.append({"title": f"ZIP Archive: {fname}" + (f" [{fsize}]" if fsize else ""), "size": fsize, "link": full})
            else:
                ep_m = re.search(r'\bS\d{1,2}E(\d{1,3})\b|\bE(\d{1,3})\b', fname, re.I)
                ep_lbl = f"EP-{int(ep_m.group(1) or ep_m.group(2)):02d}" if ep_m else f"EP-{len(episodes)+1:02d}"
                episodes.append({"ep_label": ep_lbl, "title": fname, "size": fsize, "link": full})

    seasons = [{"title": pack_title or "Pack Episodes", "episodes": episodes}] if episodes else []
    if not seasons and len(zip_packs) == 1:
        return resolve_hubcloud(zip_packs[0]["link"])
    if not seasons and not zip_packs:
        return {"success": False, "error": "No file found in HubDrive pack."}
    return {
        "success": True,
        "is_series": True,
        "title": pack_title,
        "seasons": seasons,
        "packs": zip_packs,
        "qualities": [{"title": z["title"], "link": z["link"]} for z in zip_packs]
    }

def _resolve_hubcloud_search_recover(html, current_url):
    """Unpacks HubCloud search-recover.php pages (used by multi-episode series packs) via their JSON search API."""
    q_m = re.search(r'const\s+Q_INITIAL\s*=\s*"([^"]*)"', html)
    ac_m = re.search(r'const\s+FROM_AC_TOKEN\s*=\s*"([^"]*)"', html)
    if not q_m or not ac_m:
        return None
    base_api = current_url.split('?')[0]
    api_u = (
        f"{base_api}?api=search&q={urllib.parse.quote(q_m.group(1))}"
        f"&page=1&from_ac={urllib.parse.quote(ac_m.group(1))}"
    )
    jraw, _ = fetch_html(api_u, referer=current_url, extra_headers={'Accept': 'application/json'})
    data = json.loads(jraw)
    hits = data.get('hits') or []
    if not hits:
        return {"success": False, "error": "No file found in HubCloud pack search."}
    if len(hits) == 1 and hits[0].get('url'):
        return resolve_hubcloud(hits[0]['url'])
    episodes = []
    packs = []
    for idx, h in enumerate(hits):
        fname = (h.get('file_name') or h.get('name') or f"File {idx + 1}").strip()
        fsize = (h.get('size') or '').strip()
        furl = (h.get('url') or '').strip()
        if not furl:
            continue
        if fname.lower().endswith('.zip') or 'zip' in (h.get('mimeType') or '').lower():
            packs.append({"title": f"{fname}" + (f" [{fsize}]" if fsize else ""), "size": fsize, "link": furl})
        else:
            ep_m = re.search(r'\bS\d{1,2}\s*E(\d{1,3})\b|\bEp\.?\s*(\d{1,3})\b|\bE(\d{1,3})\b', fname, re.I)
            ep_lbl = f"EP-{int(ep_m.group(1) or ep_m.group(2) or ep_m.group(3)):02d}" if ep_m else f"FILE-{len(episodes)+1:02d}"
            episodes.append({"ep_label": ep_lbl, "title": fname, "size": fsize, "link": furl})
    if not episodes and len(packs) == 1:
        return resolve_hubcloud(packs[0]['link'])
    seasons = []
    if episodes:
        seasons.append({"title": f"{q_m.group(1)} (Individual Files)", "episodes": episodes})
    if not seasons and packs:
        seasons.append({"title": f"{q_m.group(1)} (Season ZIP Archives)", "episodes": [{"ep_label": f"PACK-{i+1:02d}", "title": p['title'], "size": p['size'], "link": p['link']} for i, p in enumerate(packs)]})
    return {
        "success": True,
        "is_series": True,
        "title": q_m.group(1),
        "seasons": seasons,
        "packs": packs,
        "qualities": [{"title": p["title"], "link": p["link"]} for p in packs]
    }

# ── Main Link Resolver ─────────────────────────────────────────────────────────

def resolve_hubcloud(input_url):
    input_url = normalize_url(input_url)
    """Resolves HubCloud / HubDrive / HBLInks / HubCDN / HDHub4u / HDStream4u links into seekable streams."""
    try:
        if not input_url or not input_url.startswith('http'):
            return {"success": False, "error": "Invalid URL — must start with http/https"}

        # If it's a greenmotors link, decode first
        if 'greenmotors.' in input_url:
            decoded = decode_greenmotors(input_url)
            if decoded:
                return resolve_hubcloud(decoded)
            return {"success": False, "error": "Could not decode GreenMotors link."}

        # If it's an HDHub4u or 4KHDHub post link
        if re.search(r'hdhub4u\.[a-z]+/', input_url, re.I) or _is_4khdhub_url(input_url):
            details = parse_movie_details(input_url)
            if details.get('is_series'):
                return details
            if details.get('qualities'):
                dl_links = [q for q in details['qualities'] if 'hdstream4u.com' not in q['link']]
                chosen = (dl_links[0] if dl_links else details['qualities'][0])['link']
                print(f"[Resolver] Followed post to: {chosen}")
                return resolve_hubcloud(chosen)

        # Direct HDStream4u HLS (.m3u8) fast stream support
        if 'hdstream4u.com' in input_url:
            m3u8 = resolve_hdstream4u(input_url)
            if m3u8:
                tracks = parse_m3u8_audio_tracks(m3u8)
                hls_sid = register_short_stream(m3u8, '', 0, None, -1, 'HDStream4u HLS Stream', 'Adaptive HLS')
                get_hls_session(m3u8, sid=hls_sid).warm_async()
                proxy_pl = f"/api/hls/playlist.m3u8?sid={hls_sid}&t_ms=0"
                return {
                    "success": True,
                    "title": "HDStream4u HLS Stream",
                    "size": "Adaptive HLS",
                    "sid": hls_sid,
                    "is_hls": True,
                    "compress_method": -1,
                    "stream_url": m3u8,
                    "seek_url": "",
                    "audio_tracks": tracks,
                    "watch_online_url": f"/watch?url={urllib.parse.quote(proxy_pl, safe='')}&t=StreamHub+Player",
                    "mirrors": [{
                        "name": "⚡ [HLS Fast Stream] Adaptive M3U8 (Instant Seeking & Browser Audio)",
                        "url": m3u8,
                        "alive": True,
                        "type": "hls",
                        "sid": hls_sid
                    }]
                }
            return {"success": False, "error": "Could not resolve HDStream4u link."}

        html, current_url = fetch_html(input_url)

        # Step 0a: HubDrive /packs/ season pack HTML page -> list individual episodes + zip option
        if 'hubdrive.' in current_url.lower() and '/packs/' in current_url.lower():
            return _resolve_hubdrive_pack(html, current_url)

        # Step 0a-2: HubCloud search-recover.php multi-file/pack search -> list episodes / resolve direct archive
        if 'search-recover.php' in current_url.lower():
            rec = _resolve_hubcloud_search_recover(html, current_url)
            if rec:
                return rec

        # Step 0b: HBLInks multi-quality hub -> follow best quality (4K > 1080p > 720p > 480p)
        if 'hblinks.' in current_url.lower():
            best_target = _pick_best_hblinks_url(html)
            if best_target:
                print(f"[Resolver] Picked best HBLInks quality: {best_target}")
                return resolve_hubcloud(best_target)

        # Step 1: Follow HubDrive -> HubCloud before extracting title/size (HubCloud has clean id="size" and filename)
        m_hubcloud = re.search(r'href=["\'](https?://hubcloud\.[a-z]+/drive/[^"\'\s]+)["\']', html)
        if m_hubcloud and '/drive/admin' not in m_hubcloud.group(1):
            target_hubcloud = m_hubcloud.group(1)
            print(f"[Resolver] Followed HubDrive to: {target_hubcloud}")
            html, current_url = fetch_html(target_hubcloud, referer=input_url)

        # Title and file size detection (prefer HubCloud page metadata over HubDrive wrapper CSS/header)
        title_m = re.search(r'<title>(.*?)</title>', html, re.I)
        raw_title = _html.unescape(title_m.group(1)).strip() if title_m else "4K Movie Stream"
        movie_title = re.sub(
            r'^\s*(?:HDHub4u|4KHDHub|HubCloud|HubDrive|StreamHub)\s*[|:-]\s*',
            '', raw_title, flags=re.I
        ).strip()
        movie_title = re.sub(
            r'\s*[-|]\s*(HDHub4u|4KHDHub|HubCloud|HubDrive|StreamHub|Watch Online|Free Download)(\.[a-z]+)?(\.mkv|\.mp4)?.*$',
            '', movie_title, flags=re.I
        ).strip()
        movie_title = re.sub(r'(\.mkv|\.mp4)$', '', movie_title, flags=re.I).strip()
        movie_title = re.sub(r'[\U00010000-\U0010ffff]', '', movie_title).strip() or "StreamHub Video"
        size_m = re.search(r'id=["\']size["\'][^>]*>\s*([\d.,]+\s*(?:GB|MB|KB|TB))', html, re.I)
        if not size_m:
            size_m = re.search(r'\b([\d.,]+\s*(?:GB|TB|MB))\b', html, re.I)
        file_size = size_m.group(1).strip() if size_m else "4K Ultra HD"
        is_zip = ('.zip' in raw_title.lower() or '.zip' in movie_title.lower() or 'application/x-z' in html.lower() or 'application/zip' in html.lower())

        # Step 2: Follow HubCloud -> GamerXYT
        m_gamer = re.search(r'href=["\'](https?://gamerxyt\.com/hubcloud\.php[^"\'\s]+)["\']', html)
        if m_gamer:
            gamer_url = m_gamer.group(1).replace('&amp;', '&')
            print(f"[Resolver] Followed HubCloud to GamerXYT: {gamer_url[:60]}")
            html, current_url = fetch_html(gamer_url, referer=current_url)

        # Step 3: Collect candidate mirror links first, then resolve & probe ALL in parallel (~2s vs 12s+)
        raw_specs = []
        seen_urls = set()

        def _queue_spec(m_type, url, name_tmpl, referer_val=None, needs_gpdl=False):
            u = str(url or '').strip()
            if not u or u in seen_urls:
                return
            seen_urls.add(u)
            raw_specs.append((m_type, u, name_tmpl, referer_val, needs_gpdl))

        # A. Check for pixel / gpdl HubCloud links (Google 10Gbps CDN)
        for cdn_url in re.findall(r'href=["\'](https?://(?:pixel|gpdl)\.hubcloud\.[a-z]+/[^"\'\s]+)["\']', html):
            _queue_spec('google_cdn', cdn_url.replace('&amp;', '&'), '[10Gbps] Google High-Speed Server (Original 4K 60FPS MKV)', current_url, True)

        # B. Check for direct cryptonewz links
        for m in re.finditer(r'href=["\'](https?://cryptonewz\.online/dl\.php\?link=[^"\'\s]+)["\']', html):
            _queue_spec('google_cdn', m.group(1).replace('&amp;', '&'), '[10Gbps] Google High-Speed Server (Original 4K 60FPS MKV)', current_url, True)

        # C. Check for direct video-downloads.googleusercontent.com links
        for m in re.finditer(r'href=["\'](https?://video-downloads\.googleusercontent\.com/[^"\'\s]+)["\']', html):
            _queue_spec('google_cdn', make_safe_url(m.group(1).replace('&amp;', '&')), '[10Gbps] Google High-Speed Server (Original 4K 60FPS MKV)', None, False)

        # D. Check for HubCDN direct ?r= base64 (R2 or Google CDN)
        m_r = re.search(r'[?&]r=([a-zA-Z0-9+/=]+)', input_url + "\n" + current_url + "\n" + html)
        if m_r:
            try:
                dec = base64.b64decode(m_r.group(1)).decode('utf-8', errors='ignore')
                m_link = re.search(r'link=(https?://[^\s&"\']+)', dec)
                if m_link:
                    direct_url = make_safe_url(m_link.group(1))
                    is_r2 = 'r2.dev' in direct_url
                    _queue_spec('r2_dev' if is_r2 else 'google_cdn', direct_url, '⚡ [Full Seeking] Cloudflare R2 Direct Server' if is_r2 else '[10Gbps] Google High-Speed Server', None, False)
            except Exception as e:
                print("[Resolver] Error decoding hubcdn ?r=:", e)

        # E. Check for R2 Cloudflare storage links (Native HTTP 206 Range Seeking)
        for m in re.finditer(r'href=["\'](https?://(?:[a-zA-Z0-9\-_.]+\.r2\.cloudflarestorage\.com|pub-[a-zA-Z0-9]+\.r2\.dev)/[^"\']+)["\']', html):
            _queue_spec('r2', make_safe_url(m.group(1).replace('&amp;', '&')), '⚡ [Full Seeking] Cloudflare R2 Server', None, False)

        # F. Check for workers.dev links (Match full href first to preserve filenames with spaces!)
        w_matches = list(re.finditer(r'<a[^>]*href=["\'](https?://[a-zA-Z0-9\-_.]+\.workers\.dev/[^"\']+)["\'][^>]*>(.*?)</a>', html, re.S | re.I))
        if not w_matches:
            w_matches = list(re.finditer(r'(https?://[a-zA-Z0-9\-_.]+\.workers\.dev/[^"\' <>\s]{10,})', html))
        for m in w_matches:
            w_url = make_safe_url(m.group(1).replace('&amp;', '&'))
            anchor_txt = _clean_tag_text(m.group(2)) if m.lastindex and m.lastindex >= 2 else ''
            is_split_zip = is_zip and ('zipdisk' in anchor_txt.lower() or 'part ' in anchor_txt.lower() or 'ddl2.' in w_url.lower())
            if is_split_zip:
                _queue_spec('zipdisk', w_url, f'📦 [{anchor_txt or "ZipDisk Split Server"}]', None, False)
            else:
                _queue_spec('worker', w_url, '⚡ [Full Seeking] Cloudflare Worker Server (Original 4K 60FPS MKV)', None, False)

        # G. Check for hbplay.pages.dev base64 links (decodes to high-speed R2 stream)
        for m in re.finditer(r'href=["\']https?://hbplay\.pages\.dev/\?u=([a-zA-Z0-9+/=]+)["\']', html):
            try:
                dec_u = make_safe_url(base64.b64decode(m.group(1)).decode('utf-8', errors='ignore'))
                if dec_u.startswith('http'):
                    _queue_spec('r2', dec_u, '⚡ [Full Seeking] Cloudflare R2 Server', None, False)
            except Exception:
                pass

        # H. Check for PixelDrain links (supports native HTTP 206 seeking)
        for m in re.finditer(r'href=["\'](https?://pixeldrain\.(?:dev|com)/u/([a-zA-Z0-9]+))["\']', html):
            pd_api = f"https://pixeldrain.dev/api/file/{m.group(2)}"
            _queue_spec('pixeldrain', pd_api, '⚡ [Full Seeking] PixelDrain High-Speed Server', None, False)

        def _probe_spec(spec):
            m_type, raw_u, nm_base, ref_u, needs_gpdl = spec
            eff_u = resolve_gpdl(raw_u, referer=ref_u) if needs_gpdl else raw_u
            if not eff_u:
                return None
            eff_u = make_safe_url(eff_u)
            alive = check_link_alive(eff_u)
            if m_type == 'pixeldrain' and not alive:
                return None
            suffix = ' [OK]' if alive else (' (Limited Quota / Auto-Retry)' if m_type == 'worker' else (' (Disabled / 403)' if m_type == 'r2' else ' [FAIL]'))
            return {'name': nm_base + suffix, 'url': eff_u, 'alive': alive, 'type': m_type}

        mirrors = []
        seen_final = set()
        if raw_specs:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(raw_specs))) as ex:
                for probed in ex.map(_probe_spec, raw_specs):
                    if probed and probed['url'] not in seen_final:
                        seen_final.add(probed['url'])
                        mirrors.append(probed)

        # Default Audio Tracks
        audio_tracks = [
            {"track_num": 1, "stream_index": 0, "lang": "Hindi", "codec": "DDP5.1", "channels": "5.1", "label": "Track 1: Hindi (Default / Org DDP 5.1)"},
            {"track_num": 2, "stream_index": 1, "lang": "English", "codec": "DTS-HD/Atmos", "channels": "7.1", "label": "Track 2: English (Dolby Atmos / DTS-HD 7.1)"},
            {"track_num": 3, "stream_index": 2, "lang": "Multi-Audio", "codec": "Native Switcher", "channels": "Auto", "label": "Track 3: VLC Native Audio Switcher"}
        ]

        # ── Remote ZIP Season Pack Handler (Stream & Seek Episodes Inside .zip Without Full Download!) ──
        if is_zip:
            zip_candidates = [m for m in mirrors if m['type'] in ('r2', 'r2_dev', 'worker', 'pixeldrain') and m['alive']]
            if not zip_candidates:
                zip_candidates = [m for m in mirrors if m['type'] == 'zipdisk' and m['alive']]

            zip_entries = []
            # If using full R2/Worker archive, parse first working; if ZipDisk split parts, merge parts!
            for cand in zip_candidates:
                _, raw_ents = parse_remote_zip(cand['url'])
                if raw_ents:
                    for ent in raw_ents:
                        zip_entries.append((cand['url'], ent))
                    if cand['type'] != 'zipdisk':
                        break

            if zip_entries:
                # Re-sort combined entries naturally by episode name
                zip_entries.sort(key=lambda pair: [int(p) if p.isdigit() else p.lower() for p in re.split(r'(\d+)', pair[1]['short_name'])])
                formatted_entries = []
                for idx, (arc_url, ent) in enumerate(zip_entries):
                    cm = ent['method']
                    off = ent['data_start']
                    sz = ent['usize']
                    seek_u = arc_url if cm == 0 else ''
                    ep_sid = register_short_stream(arc_url, seek_u, off, sz, cm, ent['short_name'], format_bytes(sz))
                    formatted_entries.append({
                        "index": idx,
                        "name": ent['short_name'],
                        "full_name": ent['fname'],
                        "size": format_bytes(sz),
                        "size_bytes": sz,
                        "method": cm,
                        "seekable": (cm == 0),
                        "stream_url": arc_url,
                        "seek_url": seek_u,
                        "base_offset": off,
                        "sub_size": sz,
                        "compress_method": cm,
                        "sid": ep_sid,
                        "watch_online_url": f"/watch?url={urllib.parse.quote('/s/' + ep_sid + '.mkv', safe='')}&t={urllib.parse.quote(ent['short_name'])}"
                    })

                for m in mirrors:
                    if m.get('alive') and m.get('type') != 'zipdisk':
                        m['sid'] = register_short_stream(m['url'], m.get('seek_url') or '', 0, None, 0, movie_title, file_size)

                ep0 = formatted_entries[0]
                if ep0['compress_method'] == 0:
                    get_stream_session(ep0['stream_url'], ep0['seek_url'], ep0['base_offset'], ep0['sub_size'], ep0['compress_method']).warm_async()

                return {
                    "success":          True,
                    "is_zip":           True,
                    "zip_title":        movie_title,
                    "zip_size":         file_size,
                    "title":            ep0['name'],
                    "size":             ep0['size'],
                    "sid":              ep0['sid'],
                    "stream_url":       ep0['stream_url'],
                    "seek_url":         ep0['seek_url'],
                    "base_offset":      ep0['base_offset'],
                    "sub_size":         ep0['sub_size'],
                    "compress_method":  ep0['compress_method'],
                    "zip_entries":      formatted_entries,
                    "audio_tracks":     audio_tracks,
                    "watch_online_url": ep0['watch_online_url'],
                    "mirrors":          mirrors
                }

        # ── Smart Mirror Prioritization for Speed (250Mbps+) + Seeking in VLC ──
        r2_m         = next((m for m in mirrors if m['type'] in ('r2', 'r2_dev') and m['alive']), None)
        r2_any_m     = next((m for m in mirrors if m['type'] in ('r2', 'r2_dev')), None)
        google_cdn_m = next((m for m in mirrors if m['type'] == 'google_cdn' and m['alive']), None)
        pd_m         = next((m for m in mirrors if m['type'] == 'pixeldrain' and m['alive']), None)
        pd_any_m     = next((m for m in mirrors if m['type'] == 'pixeldrain'), None)
        worker_m     = next((m for m in mirrors if m['type'] == 'worker' and m['alive']), None)
        any_worker_m = next((m for m in mirrors if m['type'] == 'worker'), None)

        alive_range_urls = [
            m['url'] for m in mirrors
            if m.get('alive') and m.get('type') != 'zipdisk' and is_range_capable(m.get('url', ''))
        ]
        extra_range_urls = [
            m['url'] for m in mirrors
            if not m.get('alive') and m.get('type') != 'zipdisk' and is_range_capable(m.get('url', '')) and m['url'] not in alive_range_urls
        ]
        fallback_range_urls = alive_range_urls + extra_range_urls

        # Prefer R2 (30MB/s + native 206) > Google CDN paired with Range mirror (alive OR hedged worker/pd/r2) > PixelDrain > Worker
        if r2_m:
            best_stream_url = r2_m['url']
            seek_stream_url = r2_m['url']
        elif google_cdn_m:
            best_stream_url = google_cdn_m['url']
            seek_cand = worker_m or pd_m or any_worker_m or pd_any_m or r2_any_m
            seek_stream_url = seek_cand['url'] if seek_cand else None
        elif pd_m:
            best_stream_url = pd_m['url']
            seek_stream_url = pd_m['url']
        elif worker_m:
            best_stream_url = worker_m['url']
            seek_stream_url = worker_m['url']
        else:
            working_mirrors = [m for m in mirrors if m['alive']]
            best_stream_url = working_mirrors[0]['url'] if working_mirrors else (mirrors[0]['url'] if mirrors else None)
            seek_cand = r2_any_m or worker_m or pd_m or any_worker_m or pd_any_m
            seek_stream_url = seek_cand['url'] if seek_cand else (best_stream_url if is_range_capable(best_stream_url) else None)

        if not best_stream_url:
            return {
                "success": False,
                "error": "No working stream link found. The server may be offline."
            }

        # Pre-warm MKV Header (from fast CDN) & MKV Cues Tail (from seek mirror) in background!
        get_stream_session(best_stream_url, seek_stream_url or '', backup_urls=fallback_range_urls).warm_async()

        for m in mirrors:
            if m.get('alive') and m.get('type') != 'zipdisk':
                m_seek = (seek_stream_url or '') if m.get('type') == 'google_cdn' else (m.get('seek_url') or '')
                m['sid'] = register_short_stream(m['url'], m_seek, 0, None, 0, movie_title, file_size, backup_urls=fallback_range_urls)

        sid = register_short_stream(best_stream_url, seek_stream_url or '', 0, None, 0, movie_title, file_size, backup_urls=fallback_range_urls)
        watch_online_url = f"/watch?url={urllib.parse.quote('/s/' + sid + '.mkv', safe='')}&t={urllib.parse.quote(movie_title)}"

        return {
            "success":          True,
            "title":            movie_title,
            "size":             file_size,
            "sid":              sid,
            "seekable":         bool(seek_stream_url or is_range_capable(best_stream_url)),
            "stream_url":       best_stream_url,
            "seek_url":         seek_stream_url,
            "audio_tracks":     audio_tracks,
            "watch_online_url": watch_online_url,
            "mirrors":          mirrors
        }

    except Exception as e:
        return {"success": False, "error": str(e)}

# ── Frontend HTML/CSS/JS ───────────────────────────────────────────────────────

WATCH_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>__MOVIE_TITLE__ - StreamHub Player</title>
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  body { background: #000; color: #f8fafc; display: flex; flex-direction: column; height: 100vh; height: 100dvh; overflow: hidden; }
  .top-bar {
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    padding: 10px 16px;
    background: rgba(10, 12, 18, 0.9);
    backdrop-filter: blur(14px);
    border-bottom: 1px solid rgba(255,255,255,0.08);
    z-index: 10;
  }
  .top-left { display: flex; align-items: center; gap: 10px; min-width: 0; flex: 1; }
  .btn-back {
    display: inline-flex; align-items: center; gap: 6px;
    background: rgba(255,255,255,0.07); color: #f8fafc;
    border: 1px solid rgba(255,255,255,0.12);
    padding: 8px 13px; border-radius: 10px; font-size: 0.84rem; font-weight: 600;
    text-decoration: none; flex-shrink: 0; transition: background 0.18s;
  }
  .btn-back:hover { background: rgba(255,255,255,0.14); }
  .badge-4k {
    background: rgba(225, 29, 72, 0.18); color: #fda4af;
    border: 1px solid rgba(225, 29, 72, 0.45);
    font-size: 0.7rem; font-weight: 800; letter-spacing: 0.5px;
    padding: 4px 8px; border-radius: 6px; flex-shrink: 0;
  }
  .top-bar h1 { font-size: 0.95rem; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #f8fafc; }
  .video-container { flex: 1; position: relative; background: #000; display: flex; align-items: center; justify-content: center; }
  video { width: 100%; height: 100%; max-height: 100%; outline: none; }
</style>
</head>
<body>
  <div class="top-bar">
    <div class="top-left">
      <a href="/" class="btn-back" aria-label="Back to StreamHub">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 12H5"/><path d="m12 19-7-7 7-7"/></svg>
        Back
      </a>
      <span class="badge-4k">STREAMHUB</span>
      <h1>__MOVIE_TITLE__</h1>
    </div>
  </div>
  <div class="video-container">
    <video id="videoPlayer" controls autoplay playsinline></video>
  </div>
  <script>
    const streamUrl = __STREAM_URL_JSON__;
    const video = document.getElementById('videoPlayer');
    if (streamUrl) {
      if (streamUrl.includes('.m3u8') && typeof Hls !== 'undefined' && Hls.isSupported()) {
        const hls = new Hls({
          enableWorker: true,
          lowLatencyMode: false,
          backBufferLength: 90,
          maxBufferLength: 24,
          fragLoadingTimeOut: 35000,
          manifestLoadingTimeOut: 20000,
        });
        hls.loadSource(streamUrl);
        hls.attachMedia(video);
        hls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));
        hls.on(Hls.Events.ERROR, (_ev, data) => {
          if (data && data.fatal) {
            if (data.type === Hls.ErrorTypes.NETWORK_ERROR) hls.startLoad();
            else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
          }
        });
      } else {
        video.src = streamUrl;
        video.onerror = () => {
          const msg = document.createElement('div');
          msg.style.cssText = 'position:absolute;bottom:64px;left:50%;transform:translateX(-50%);background:rgba(15,18,28,0.92);border:1px solid rgba(255,255,255,0.2);color:#fde68a;padding:10px 16px;border-radius:10px;font-size:0.85rem;text-align:center;max-width:90%;';
          msg.textContent = 'Your browser cannot natively play this 4K/MKV codec — please use StreamHub In-Built Player or Phone VLC app.';
          video.parentElement.appendChild(msg);
        };
        video.play().catch(() => {});
      }
    }
  </script>
</body>
</html>"""

PHONE_PLAY_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#07080d">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png">
<link rel="apple-touch-icon" href="/icon-192.png">
<title>__MOVIE_TITLE_HTML__ - Phone VLC Player</title>
<style>
  :root {
    --bg: #07080d; --surface: #10121c; --surface-2: #181b29;
    --border: rgba(255,255,255,0.09); --accent: #e11d48; --vlc: #f97316;
    --text: #f8fafc; --muted: #94a3b8; --emerald: #10b981;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  body {
    background: radial-gradient(circle at 50% -10%, rgba(225,29,72,0.22), transparent 55%), var(--bg);
    color: var(--text); min-height: 100vh; min-height: 100dvh;
    padding: 20px 14px calc(24px + env(safe-area-inset-bottom));
    display: flex; align-items: center; justify-content: center;
  }
  .shell {
    width: 100%; max-width: 480px; background: var(--surface);
    border: 1px solid var(--border); border-radius: 22px; padding: 24px 20px;
    box-shadow: 0 24px 60px rgba(0,0,0,0.65);
  }
  .brand-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
  .brand { display: inline-flex; align-items: center; gap: 8px; font-size: 0.82rem; font-weight: 800; letter-spacing: 0.4px; color: #f8fafc; }
  .brand-dot { width: 26px; height: 26px; border-radius: 8px; background: linear-gradient(135deg, var(--accent), var(--vlc)); display: grid; place-items: center; }
  .live-pill { font-size: 0.72rem; font-weight: 700; color: #6ee7b7; background: rgba(16,185,129,0.14); border: 1px solid rgba(16,185,129,0.35); padding: 4px 10px; border-radius: 999px; }
  h1 { font-size: 1.25rem; font-weight: 800; line-height: 1.35; margin-bottom: 12px; word-break: break-word; }
  .badges { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; }
  .badge { display: inline-flex; align-items: center; gap: 5px; background: var(--surface-2); border: 1px solid var(--border); color: #e2e8f0; padding: 5px 10px; border-radius: 8px; font-size: 0.77rem; font-weight: 600; }
  .badge-seek { background: rgba(249,115,22,0.14); color: #fdba74; border-color: rgba(249,115,22,0.35); }
  .btn {
    display: flex; align-items: center; justify-content: center; gap: 10px;
    width: 100%; min-height: 50px; padding: 14px 16px; border-radius: 13px;
    font-size: 0.95rem; font-weight: 700; text-decoration: none; border: none;
    cursor: pointer; margin-bottom: 10px; text-align: center; transition: transform 0.12s, opacity 0.18s;
  }
  .btn:active { transform: scale(0.985); }
  .btn-vlc { background: linear-gradient(135deg, #ea580c, #e11d48); color: #fff; font-weight: 800; font-size: 1rem; min-height: 54px; box-shadow: 0 10px 28px rgba(225,29,72,0.35); }
  .btn-m3u { background: rgba(99,102,241,0.18); color: #c7d2fe; border: 1px solid rgba(99,102,241,0.4); }
  .btn-web { background: rgba(16,185,129,0.14); color: #6ee7b7; border: 1px solid rgba(16,185,129,0.35); }
  .btn-sec { background: var(--surface-2); color: #e2e8f0; border: 1px solid var(--border); font-size: 0.88rem; }
  .note { background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 12px; padding: 12px; font-size: 0.8rem; color: var(--muted); line-height: 1.5; margin-top: 8px; }
  .note b { color: var(--text); }
  .url-preview { margin-top: 10px; font-size: 0.74rem; color: var(--muted); word-break: break-all; font-family: ui-monospace, Consolas, monospace; background: #090a10; padding: 9px 11px; border-radius: 9px; border: 1px solid var(--border); }
  .toast { position: fixed; left: 50%; bottom: calc(20px + env(safe-area-inset-bottom)); transform: translateX(-50%) translateY(20px); opacity: 0; pointer-events: none; background: #181c2b; color: #f8fafc; border: 1px solid rgba(255,255,255,0.16); padding: 10px 16px; border-radius: 999px; font-size: 0.84rem; font-weight: 600; box-shadow: 0 12px 30px rgba(0,0,0,0.5); transition: opacity 0.2s, transform 0.2s; z-index: 99; }
  .toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
</style>
</head>
<body>
  <div class="shell">
    <div class="brand-row">
      <div class="brand">
        <span class="brand-dot" aria-hidden="true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="6 4 20 12 6 20 6 4"/></svg>
        </span>
        4KMOVIE MOBILE PLAYER
      </div>
      <span class="live-pill">Wi-Fi Ready</span>
    </div>

    <h1>__MOVIE_TITLE_HTML__</h1>
    <div class="badges">
      <span class="badge">Size: __MOVIE_SIZE_HTML__</span>
      <span class="badge badge-seek">__SEEK_BADGE_HTML__</span>
    </div>

    <a id="btnOpenVlc" class="btn btn-vlc" href="#">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="6 4 20 12 6 20 6 4"/></svg>
      Open in Phone VLC App
    </a>
    <a class="btn btn-m3u" href="__PLAYLIST_HREF__" download="streamhub.m3u8">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
      Open via .m3u8 Playlist (Fallback)
    </a>
    <a class="btn btn-web" href="__WATCH_HREF__">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
      Watch in Phone Browser
    </a>
    <button class="btn btn-sec" onclick="copyStreamLink()">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
      Copy Direct Stream URL
    </button>
    <a class="btn btn-sec" href="/">Browse All StreamHub Movies &amp; Series</a>

    <div class="note">
      <b>Tip:</b> If VLC does not launch directly, tap <b>Open via .m3u8 Playlist</b> or copy the stream link and paste it into VLC under <i>More &rarr; New Stream</i>.
    </div>
    <div class="url-preview" id="urlPreview">__STREAM_PATH_HTML__</div>
  </div>
  <div id="toast" class="toast" role="status" aria-live="polite"></div>

<script>
  const SID = __SID_JSON__;
  const DIRECT_URL = __DIRECT_URL_JSON__;
  const STREAM_PATH = '/s/' + SID + '.mkv';
  const FULL_URL = DIRECT_URL || (window.location.origin + STREAM_PATH);
  document.getElementById('urlPreview').textContent = FULL_URL;

  function showToast(msg) {
    const el = document.getElementById('toast');
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(window._tTimer);
    window._tTimer = setTimeout(() => el.classList.remove('show'), 2800);
  }

  function buildVlcHref() {
    const ua = navigator.userAgent || '';
    if (/iphone|ipad|ipod/i.test(ua)) {
      return 'vlc-x-callback://x-callback-url/stream?url=' + encodeURIComponent(FULL_URL);
    }
    if (DIRECT_URL && /^https?:\/\//i.test(DIRECT_URL)) {
      const noScheme = DIRECT_URL.replace(/^https?:\/\//i, '');
      const sch = DIRECT_URL.startsWith('https://') ? 'https' : 'http';
      return 'intent://' + noScheme + '#Intent;package=org.videolan.vlc;type=video/*;scheme=' + sch + ';end;';
    }
    return 'intent://' + window.location.host + STREAM_PATH + '#Intent;package=org.videolan.vlc;type=video/*;scheme=http;end;';
  }

  document.getElementById('btnOpenVlc').href = buildVlcHref();

  function copyStreamLink() {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(FULL_URL)
        .then(() => showToast('Stream Link copied — paste in VLC New Stream'))
        .catch(() => prompt('Copy link for VLC Network Stream:', FULL_URL));
    } else {
      prompt('Copy link for VLC Network Stream:', FULL_URL);
    }
  }
</script>
</body>
</html>"""

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#05060b">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png">
<link rel="icon" type="image/svg+xml" href="/icon.svg">
<link rel="apple-touch-icon" href="/icon-192.png">
<title>StreamHub — Netflix &bull; Prime &bull; Hotstar 4K OTT Experience</title>
<script>try{var _t=localStorage.getItem('sh_theme');if(_t&&_t!=='netflix')document.documentElement.setAttribute('data-theme',_t);}catch(e){}</script>
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
<style>
:root {
    --bg: #09090b;
    --bg-elevated: #101014;
    --surface: #121216;
    --surface-solid: #141418;
    --surface-2: #1c1c22;
    --surface-hover: #25252d;
    --border: rgba(255, 255, 255, 0.08);
    --border-strong: rgba(255, 255, 255, 0.16);
    --netflix: #e50914;
    --netflix-dark: #b20710;
    --prime: #00a8e1;
    --hotstar: #3b82f6;
    --accent: #e50914;
    --accent-hover: #f40612;
    --vlc: #dc2626;
    --vlc-2: #b91c1c;
    --indigo: #a1a1aa;
    --emerald: #22c55e;
    --gold: #eab308;
    --cta-fg: #ffffff;
    --text: #f4f4f5;
    --text-muted: #a1a1aa;
    --radius-lg: 14px;
    --radius-md: 10px;
    --radius-sm: 7px;
    --shadow-lg: 0 12px 32px rgba(0, 0, 0, 0.55);
}

html[data-theme="amoled"] {
    --bg: #000000;
    --bg-elevated: #060608;
    --surface: #09090b;
    --surface-solid: #0b0b0e;
    --surface-2: #141418;
    --surface-hover: #1e1e24;
    --border: rgba(255, 255, 255, 0.08);
}

html[data-theme="prime"] {
    --bg: #070b12;
    --bg-elevated: #0d131f;
    --surface: #0f1623;
    --surface-solid: #111927;
    --surface-2: #182336;
    --surface-hover: #223048;
    --netflix: #00a8e1;
    --netflix-dark: #0284c7;
    --accent: #00a8e1;
    --accent-hover: #38bdf8;
}

html[data-theme="gold"] {
    --bg: #0a090b;
    --bg-elevated: #121115;
    --surface: #141317;
    --surface-solid: #17161b;
    --surface-2: #211f26;
    --surface-hover: #2b2932;
    --netflix: #eab308;
    --netflix-dark: #ca8a04;
    --accent: #eab308;
    --accent-hover: #facc15;
    --cta-fg: #09090b;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; background: var(--bg); }
body {
    font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    line-height: 1.45;
    padding-bottom: 48px;
    overflow-x: hidden;
    -webkit-font-smoothing: antialiased;
}

button, input, select, a { font-family: inherit; }
button:focus-visible, input:focus-visible, select:focus-visible, a:focus-visible {
    outline: 2px solid rgba(255, 255, 255, 0.85);
    outline-offset: 2px;
}

/* ── Minimal Brand Intro Splash ───────────────────────────── */
.intro-splash {
    position: fixed;
    inset: 0;
    z-index: 200;
    background: #060608;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 18px;
    transition: opacity 0.45s cubic-bezier(0.22, 1, 0.36, 1), visibility 0.45s;
    cursor: pointer;
}
.intro-splash.hide {
    opacity: 0;
    visibility: hidden;
    pointer-events: none;
}
.intro-logo-wrap {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    animation: introZoom 1.25s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}
.intro-s-svg {
    width: clamp(92px, 16vw, 134px);
    height: clamp(108px, 18vw, 156px);
    filter: drop-shadow(0 0 38px rgba(229, 9, 20, 0.65)) drop-shadow(0 16px 32px rgba(0,0,0,0.85));
}
.intro-wordmark {
    margin-top: 8px;
    font-size: clamp(1.1rem, 2.4vw, 1.55rem);
    font-weight: 900;
    letter-spacing: 0.42em;
    padding-left: 0.42em;
    color: #fff;
    text-transform: uppercase;
    opacity: 0;
    animation: wordFade 0.6s ease 0.22s forwards;
}
.intro-sub {
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.24em;
    color: rgba(255,255,255,0.58);
    text-transform: uppercase;
    opacity: 0;
    animation: wordFade 0.6s ease 0.36s forwards;
}
.intro-skip {
    position: absolute;
    bottom: 26px;
    right: 26px;
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.18);
    color: rgba(255,255,255,0.82);
    padding: 7px 14px;
    border-radius: 999px;
    font-size: 0.76rem;
    font-weight: 700;
    cursor: pointer;
}
.intro-skip:hover { background: rgba(255,255,255,0.16); color: #fff; }
@keyframes introZoom {
    0%   { opacity: 0; transform: scale(0.76); }
    45%  { opacity: 1; transform: scale(1.03); }
    100% { opacity: 1; transform: scale(1.10); }
}
@keyframes wordFade {
    from { opacity: 0; transform: translateY(8px); }
    to   { opacity: 1; transform: translateY(0); }
}

/* ── Sticky Minimal Topbar ───────────────────────────────── */
.topbar {
    position: sticky;
    top: 0;
    z-index: 60;
    background: rgba(9, 9, 11, 0.97);
    border-bottom: 1px solid var(--border);
    transition: background 0.2s, border-color 0.2s;
}
.topbar.scrolled {
    background: rgba(9, 9, 11, 0.98);
    border-bottom-color: var(--border-strong);
}
.topbar-inner {
    max-width: 1420px;
    margin: 0 auto;
    padding: 9px 18px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    min-width: 0;
}
.brand-group { display: flex; align-items: center; gap: 14px; flex-shrink: 0; min-width: 0; }
.brand {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    text-decoration: none;
    color: var(--text);
    cursor: pointer;
}
.brand-s-mark {
    width: 24px;
    height: 28px;
    flex-shrink: 0;
}
.brand-name { font-size: 1.08rem; font-weight: 800; letter-spacing: -0.4px; color: #fff; }
.brand-tag { display: none; }

.top-nav-links { display: flex; align-items: center; gap: 2px; }
.top-nav-btn {
    border: none;
    background: transparent;
    color: var(--text-muted);
    padding: 6px 10px;
    border-radius: 7px;
    font-size: 0.79rem;
    font-weight: 600;
    cursor: pointer;
    transition: color 0.16s, background 0.16s;
    white-space: nowrap;
}
.top-nav-btn:hover, .top-nav-btn.active { color: #fff; background: rgba(255, 255, 255, 0.08); }

/* Search Pill + Suggestions + Voice Mic */
.nav-search {
    position: relative;
    flex: 1 1 180px;
    min-width: 140px;
    max-width: 300px;
    display: flex;
    align-items: center;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 9px;
    padding: 2px 3px 2px 11px;
    transition: border-color 0.18s;
}
.btn-mic {
    width: 28px; height: 28px; border-radius: 6px; border: none;
    background: transparent; color: var(--text-muted);
    display: grid; place-items: center; cursor: pointer; flex-shrink: 0;
    margin-right: 2px; transition: color 0.15s, background 0.15s;
}
.btn-mic:hover { color: #fff; background: rgba(255,255,255,0.08); }
.btn-mic.listening { color: #fff; background: var(--netflix); animation: micPulse 1.1s infinite; }
@keyframes micPulse { 0%, 100% { box-shadow: 0 0 0 0 rgba(229,9,20,0.55); } 50% { box-shadow: 0 0 0 6px rgba(229,9,20,0); } }
.suggest-dropdown {
    display: none; position: absolute; top: calc(100% + 6px); left: 0; right: 0;
    min-width: 260px; max-width: min(420px, 94vw); max-height: 360px; overflow-y: auto;
    background: var(--surface-solid);
    border: 1px solid var(--border-strong); border-radius: 12px;
    box-shadow: 0 16px 36px rgba(0,0,0,0.75); z-index: 95; padding: 6px;
}
.suggest-dropdown.open { display: block; }
.suggest-item {
    display: flex; align-items: center; gap: 10px; padding: 7px 8px;
    border-radius: 8px; cursor: pointer; transition: background 0.14s; text-align: left;
}
.suggest-item:hover { background: rgba(255,255,255,0.06); }
.suggest-thumb { width: 34px; height: 50px; border-radius: 5px; object-fit: cover; background: var(--surface-2); flex-shrink: 0; }
.suggest-meta { min-width: 0; flex: 1; }
.suggest-title { font-size: 0.8rem; font-weight: 600; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.suggest-sub { font-size: 0.7rem; color: var(--text-muted); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nav-search:focus-within {
    border-color: var(--border-strong);
}
.nav-search svg { color: var(--text-muted); flex-shrink: 0; }
.nav-search input {
    flex: 1;
    min-width: 0;
    border: none;
    background: transparent;
    color: var(--text);
    padding: 7px 8px;
    font-size: 0.84rem;
    outline: none;
}
.nav-search input::placeholder { color: #71717a; }
.btn-search-go {
    background: var(--accent);
    color: var(--cta-fg);
    border: none;
    border-radius: 7px;
    height: 30px;
    padding: 0 12px;
    font-size: 0.76rem;
    font-weight: 700;
    cursor: pointer;
    flex-shrink: 0;
}
.btn-search-go:hover { background: var(--accent-hover); }

.nav-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.mode-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 0.73rem;
    font-weight: 600;
    color: var(--text-muted);
    background: var(--surface-2);
    border: 1px solid var(--border);
    padding: 5px 10px;
    border-radius: 8px;
}
.dot-live {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--emerald);
}
.btn-qr-nav, .btn-install-nav, .btn-remote-nav {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 34px;
    padding: 0 10px;
    border-radius: 8px;
    background: var(--surface-2);
    color: var(--text);
    border: 1px solid var(--border);
    font-size: 0.76rem;
    font-weight: 600;
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s;
}
.btn-qr-nav:hover, .btn-install-nav:hover, .btn-remote-nav:hover {
    background: var(--surface-hover);
    border-color: var(--border-strong);
}
.mobile-search-bar { display: none; padding: 0 14px 10px; }

/* ── Minimal Cinema Hero Spotlight ───────────────────────── */
.hero-stage {
    position: relative;
    max-width: 1420px;
    margin: 12px auto 0;
    padding: 0 20px;
}
.hero-card {
    position: relative;
    min-height: 410px;
    border-radius: 16px;
    overflow: hidden;
    background: var(--surface);
    border: 1px solid var(--border);
    display: flex;
    align-items: flex-end;
}
.hero-backdrop {
    position: absolute;
    inset: 0;
    background-size: cover;
    background-position: center 22%;
    filter: brightness(0.68) contrast(1.06);
    transition: background-image 0.5s ease, opacity 0.4s ease;
}
.hero-scrim {
    position: absolute;
    inset: 0;
    background:
        linear-gradient(90deg, rgba(9,9,11,0.96) 0%, rgba(9,9,11,0.82) 45%, rgba(9,9,11,0.28) 80%, rgba(9,9,11,0.55) 100%),
        linear-gradient(to top, #09090b 0%, rgba(9,9,11,0.22) 45%, transparent 100%);
}
.hero-inner {
    position: relative;
    z-index: 2;
    width: 100%;
    padding: 34px 38px 32px;
    display: grid;
    grid-template-columns: minmax(0, 1.35fr) auto;
    gap: 28px;
    align-items: center;
}
.hero-kicker {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 10px;
}
.hero-s-tag {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 0.7rem;
    font-weight: 800;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--text);
}
.prime-check {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 0.72rem;
    font-weight: 600;
    color: var(--text-muted);
    background: rgba(255, 255, 255, 0.08);
    border: 1px solid var(--border);
    padding: 2px 8px;
    border-radius: 6px;
}
.hotstar-4k {
    font-size: 0.7rem;
    font-weight: 700;
    color: #fff;
    background: rgba(255, 255, 255, 0.12);
    border: 1px solid var(--border);
    padding: 2px 8px;
    border-radius: 6px;
}
.hero-title {
    font-size: clamp(1.55rem, 3.2vw, 2.7rem);
    font-weight: 800;
    line-height: 1.12;
    letter-spacing: -0.025em;
    color: #fff;
    margin-bottom: 8px;
    max-width: 720px;
}
.hero-meta-row {
    display: flex;
    align-items: center;
    gap: 9px;
    flex-wrap: wrap;
    font-size: 0.82rem;
    color: var(--text-muted);
    font-weight: 500;
    margin-bottom: 18px;
}
.hero-meta-dot { width: 3px; height: 3px; border-radius: 50%; background: rgba(255,255,255,0.35); }
.hero-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.btn-nf-play {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    height: 44px;
    padding: 0 22px;
    border-radius: 8px;
    border: none;
    background: #ffffff;
    color: #09090b;
    font-size: 0.92rem;
    font-weight: 700;
    cursor: pointer;
    transition: background 0.15s;
}
.btn-nf-play:hover { background: #e4e4e7; }
.btn-nf-glass {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    height: 44px;
    padding: 0 16px;
    border-radius: 8px;
    background: rgba(24, 24, 27, 0.78);
    color: #f4f4f5;
    border: 1px solid rgba(255, 255, 255, 0.18);
    font-size: 0.86rem;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
}
.btn-nf-glass:hover { background: rgba(39, 39, 42, 0.92); border-color: rgba(255,255,255,0.28); }

.hero-poster-frame {
    width: 186px;
    aspect-ratio: 2/3;
    border-radius: 10px;
    overflow: hidden;
    border: 1px solid rgba(255, 255, 255, 0.14);
    box-shadow: 0 16px 40px rgba(0, 0, 0, 0.72);
    cursor: pointer;
    transition: transform 0.2s;
}
.hero-poster-frame:hover { transform: translateY(-2px); }
.hero-poster-frame img { width: 100%; height: 100%; object-fit: cover; display: block; }

.hero-nav-arrow {
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    z-index: 4;
    width: 36px;
    height: 48px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: rgba(9, 9, 11, 0.72);
    color: #fff;
    display: grid;
    place-items: center;
    cursor: pointer;
    opacity: 0;
    transition: opacity 0.18s, background 0.18s;
}
.hero-card:hover .hero-nav-arrow { opacity: 1; }
.hero-nav-arrow:hover { background: var(--accent); }
.hero-prev { left: 12px; }
.hero-next { right: 12px; }
.hero-dots {
    position: absolute;
    bottom: 14px;
    right: 24px;
    z-index: 4;
    display: flex;
    gap: 6px;
}
.hero-dot {
    width: 16px;
    height: 3px;
    border-radius: 99px;
    border: none;
    background: rgba(255, 255, 255, 0.26);
    cursor: pointer;
    transition: width 0.2s, background 0.2s;
}
.hero-dot.active { width: 28px; background: var(--accent); }

/* ── Main App Shell Container ────────────────────────────── */
.app-shell {
    max-width: 1420px;
    margin: 0 auto;
    padding: 16px 20px 28px;
    display: flex;
    flex-direction: column;
    gap: 22px;
}

.glass-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 18px;
    scroll-margin-top: 80px;
}

/* ── Compact Utility Bar (Link Resolver + Phone QR) ──────── */
.command-grid {
    display: grid;
    grid-template-columns: minmax(0, 1.85fr) minmax(290px, 0.9fr);
    gap: 14px;
    align-items: stretch;
}
.hero-link-card {
    display: flex;
    flex-direction: column;
    justify-content: center;
    padding: 16px 18px;
    background: var(--surface);
}
.section-eyebrow {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
    margin-bottom: 10px;
}
.section-eyebrow h2 {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: 0.92rem;
    font-weight: 800;
}
.pill-subtle {
    font-size: 0.7rem;
    font-weight: 700;
    color: var(--text-muted);
    background: rgba(255,255,255,0.05);
    border: 1px solid var(--border);
    padding: 3px 9px;
    border-radius: 999px;
}
.link-input-wrap { display: flex; gap: 10px; align-items: stretch; }
.input-group {
    flex: 1;
    display: flex;
    align-items: center;
    background: #070910;
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-md);
    padding: 0 8px 0 13px;
    min-height: 44px;
    transition: border-color 0.2s, box-shadow 0.2s;
}
.input-group:focus-within {
    border-color: var(--netflix);
    box-shadow: 0 0 0 3px rgba(229, 9, 20, 0.16);
}
.input-group input {
    flex: 1;
    min-width: 0;
    border: none;
    background: transparent;
    color: var(--text);
    font-size: 0.88rem;
    outline: none;
    padding: 8px 6px 8px 0;
}
.input-group input::placeholder { color: #64748b; }
.btn-paste {
    display: inline-flex;
    align-items: center;
    height: 30px;
    padding: 0 10px;
    border-radius: 7px;
    background: var(--surface-2);
    color: #cbd5e1;
    border: 1px solid var(--border);
    font-size: 0.74rem;
    font-weight: 700;
    cursor: pointer;
    flex-shrink: 0;
}
.btn-paste:hover { background: var(--surface-hover); color: #fff; }
.btn-primary {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    min-height: 44px;
    padding: 0 20px;
    border-radius: var(--radius-md);
    border: none;
    background: var(--netflix);
    color: var(--cta-fg);
    font-size: 0.86rem;
    font-weight: 700;
    cursor: pointer;
    flex-shrink: 0;
    transition: background 0.15s, opacity 0.18s;
}
.btn-primary:hover { background: var(--accent-hover); }
.btn-primary:disabled { opacity: 0.55; cursor: not-allowed; }

.status-bar {
    display: none;
    align-items: center;
    gap: 10px;
    margin-top: 10px;
    padding: 9px 13px;
    border-radius: 8px;
    background: var(--surface-2);
    border: 1px solid var(--border);
    font-size: 0.82rem;
    color: var(--text);
}
.spinner {
    width: 16px; height: 16px;
    border: 2px solid rgba(255,255,255,0.2);
    border-top-color: var(--netflix);
    border-radius: 50%;
    animation: spin 0.75s linear infinite;
    flex-shrink: 0;
}
@keyframes spin { to { transform: rotate(360deg); } }
.error-box {
    background: rgba(239, 68, 68, 0.10);
    border: 1px solid rgba(239, 68, 68, 0.35);
    border-radius: 8px;
    padding: 10px 14px;
    margin-top: 10px;
    font-size: 0.82rem;
    color: #fca5a5;
}

.qr-card {
    display: flex;
    align-items: center;
    padding: 14px 16px;
    background: var(--surface);
    border: 1px solid var(--border);
}
#phoneQrExpanded { display: flex; align-items: center; gap: 14px; width: 100%; }
.qr-thumb-btn {
    width: 82px; height: 82px; min-width: 82px;
    background: #fff; padding: 5px; border-radius: 10px; border: none; cursor: pointer;
}
.qr-thumb-btn img { width: 100%; height: 100%; display: block; border-radius: 5px; }
.qr-card-meta { flex: 1; min-width: 0; }
.qr-card-title { font-size: 0.86rem; font-weight: 700; color: var(--text); margin-bottom: 4px; }
.qr-url-pill {
    display: inline-block; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    background: var(--bg); border: 1px solid var(--border); color: var(--text-muted);
    font-family: ui-monospace, Consolas, monospace; font-size: 0.74rem; font-weight: 600;
    padding: 4px 8px; border-radius: 6px; margin-bottom: 7px;
}
.qr-btn-row { display: flex; gap: 6px; flex-wrap: wrap; }

/* ── Horizontal Cinema Poster Rails ─────────────────────── */
.ott-rails-wrap { display: flex; flex-direction: column; gap: 22px; }
.ott-rail { position: relative; }
.rail-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 10px;
    padding: 0 2px;
}
.rail-title-wrap { display: flex; align-items: center; gap: 8px; }
.rail-accent-bar { width: 3px; height: 16px; border-radius: 2px; background: var(--netflix); }
.rail-accent-prime { background: var(--prime); }
.rail-accent-hotstar { background: var(--gold); }
.rail-title { font-size: 1.02rem; font-weight: 700; letter-spacing: -0.2px; color: #fff; }
.rail-explore-btn {
    border: none;
    background: transparent;
    color: var(--text-muted);
    font-size: 0.78rem;
    font-weight: 600;
    cursor: pointer;
    padding: 4px 8px;
    border-radius: 6px;
}
.rail-explore-btn:hover { color: #fff; background: rgba(255, 255, 255, 0.06); }

.rail-track-wrap { position: relative; }
.rail-row {
    display: flex;
    gap: 12px;
    overflow-x: auto;
    scroll-behavior: smooth;
    padding: 4px 2px 10px;
    scrollbar-width: none;
}
.rail-row::-webkit-scrollbar { display: none; }
.rail-card {
    flex: 0 0 158px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
    cursor: pointer;
    position: relative;
    transition: transform 0.18s ease, border-color 0.18s;
    display: flex;
    flex-direction: column;
}
.rail-card:hover {
    transform: translateY(-2px);
    border-color: var(--border-strong);
    z-index: 3;
}
.rail-arrow {
    position: absolute;
    top: 38%;
    transform: translateY(-50%);
    z-index: 5;
    width: 34px;
    height: 56px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: rgba(9, 9, 11, 0.85);
    color: #fff;
    display: grid;
    place-items: center;
    cursor: pointer;
    opacity: 0;
    transition: opacity 0.18s, background 0.18s;
}
.rail-track-wrap:hover .rail-arrow { opacity: 1; }
.rail-arrow:hover { background: var(--netflix); }
.rail-prev { left: 4px; }
.rail-next { right: 4px; }

.prime-dot-mini {
    position: absolute;
    top: 8px;
    left: 8px;
    z-index: 2;
    display: inline-flex;
    align-items: center;
    gap: 4px;
    background: rgba(9, 9, 11, 0.85);
    border: 1px solid var(--border);
    color: var(--text);
    font-size: 0.62rem;
    font-weight: 700;
    padding: 2px 6px;
    border-radius: 5px;
}

/* ── Small Buttons & Details Theater ─────────────────────── */
.btn-sm {
    display: inline-flex; align-items: center; justify-content: center; gap: 5px;
    min-height: 34px; padding: 6px 11px; border-radius: 7px;
    font-size: 0.75rem; font-weight: 600; border: none; cursor: pointer; text-decoration: none;
    transition: background 0.15s, border-color 0.15s, opacity 0.15s;
}
.btn-accent-sm { background: var(--vlc-2); color: #fff; }
.btn-vlc-sm { background: var(--accent); color: var(--cta-fg); }
.btn-vlc-sm:hover { background: var(--accent-hover); }
.btn-indigo-sm { background: var(--surface-2); color: var(--text); border: 1px solid var(--border-strong); }
.btn-indigo-sm:hover { background: var(--surface-hover); }
.btn-copy-sm { background: var(--surface-2); color: var(--text-muted); border: 1px solid var(--border); }
.btn-copy-sm:hover { background: var(--surface-hover); color: #fff; }

.details-layout { display: flex; gap: 20px; align-items: flex-start; }
.detail-poster {
    width: 145px; aspect-ratio: 2/3; object-fit: cover; border-radius: 14px;
    background: var(--surface-2); border: 1px solid rgba(255,255,255,0.16); flex-shrink: 0;
    box-shadow: 0 14px 36px rgba(0,0,0,0.65);
}
.details-main { flex: 1; min-width: 0; }
.details-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
.details-header h3 { font-size: 1.24rem; font-weight: 900; color: #fff; line-height: 1.26; }
.detail-meta-strip { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.imdb-pill {
    display: inline-flex; align-items: center; gap: 5px;
    background: rgba(251, 191, 36, 0.16); color: var(--gold);
    border: 1px solid rgba(251, 191, 36, 0.42);
    padding: 3px 9px; border-radius: 7px; font-size: 0.76rem; font-weight: 800; text-decoration: none;
}
.trailer-pill {
    display: inline-flex; align-items: center; gap: 5px;
    background: rgba(229, 9, 20, 0.18); color: #fca5a5;
    border: 1px solid rgba(229, 9, 20, 0.45);
    padding: 3px 10px; border-radius: 7px; font-size: 0.76rem; font-weight: 800; cursor: pointer;
}
.trailer-pill:hover { background: var(--netflix); color: #fff; }
.detail-story { font-size: 0.84rem; color: #cbd5e1; line-height: 1.5; margin-bottom: 12px; }
.screenshots-strip { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 8px; margin-bottom: 12px; }
.screenshots-strip img { height: 74px; width: auto; border-radius: 8px; border: 1px solid var(--border); object-fit: cover; }

.btn-close-card {
    width: 34px; height: 34px; border-radius: 9px; background: var(--surface-2);
    color: var(--text-muted); border: 1px solid var(--border); display: grid; place-items: center; cursor: pointer; flex-shrink: 0;
}
.btn-close-card:hover { color: #fff; background: var(--surface-hover); }

.select-control {
    width: 100%; max-width: 100%; min-width: 0; min-height: 44px; padding: 10px 14px; border-radius: var(--radius-md);
    background: #070910; color: var(--text); border: 1px solid var(--border-strong);
    font-size: 0.9rem; outline: none; overflow: hidden; text-overflow: ellipsis;
}
.select-control:focus { border-color: var(--netflix); }
.quality-row { display: flex; gap: 10px; flex-wrap: wrap; align-items: stretch; width: 100%; max-width: 100%; }
.quality-row .select-control { flex: 1 1 240px; min-width: 0; width: 100%; max-width: 100%; }

/* ── Player Theater Card (#resultsCard) ──────────────────── */
.theater-grid { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(250px, 0.72fr); gap: 20px; align-items: start; }
.theater-title { font-size: 1.28rem; font-weight: 900; letter-spacing: -0.3px; line-height: 1.28; margin-bottom: 10px; word-break: break-word; }
.badge-row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
.badge {
    display: inline-flex; align-items: center; gap: 6px;
    background: var(--surface-2); border: 1px solid var(--border); color: #cbd5e1;
    padding: 5px 10px; border-radius: 8px; font-size: 0.77rem; font-weight: 700;
}
.badge-gold { background: rgba(251, 191, 36, 0.14); color: var(--gold); border-color: rgba(251, 191, 36, 0.35); }
.badge-green { background: rgba(16, 185, 129, 0.14); color: #6ee7b7; border-color: rgba(16, 185, 129, 0.35); }

.vlc-banner {
    width: 100%; min-height: 48px; padding: 12px 18px; border-radius: 10px; border: none;
    background: var(--netflix); color: var(--cta-fg);
    font-size: 0.94rem; font-weight: 700; cursor: pointer;
    display: flex; align-items: center; justify-content: center; gap: 9px;
    transition: background 0.15s;
}
.vlc-banner:hover { background: var(--accent-hover); }
.action-grid { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; }
.action-grid .btn-action { flex: 1 1 155px; }
.btn-action {
    display: inline-flex; align-items: center; justify-content: center; gap: 7px;
    min-height: 44px; padding: 10px 12px; border-radius: 10px;
    background: var(--surface-2); color: var(--text); border: 1px solid var(--border);
    font-size: 0.82rem; font-weight: 600; text-decoration: none; cursor: pointer;
}
.btn-action:hover { background: var(--surface-hover); border-color: var(--border-strong); }
.btn-action-web { background: var(--surface-2); color: var(--text); border-color: var(--border-strong); }

.movie-qr-box {
    display: flex; flex-direction: column; align-items: center; text-align: center; gap: 12px;
    padding: 18px 16px; border-radius: 14px;
    background: var(--surface-2);
    border: 1px solid var(--border);
}
.qr-frame-lg { width: 156px; height: 156px; background: #fff; padding: 8px; border-radius: 14px; box-shadow: 0 10px 28px rgba(0,0,0,0.55); }
.qr-frame-lg img { width: 100%; height: 100%; display: block; border-radius: 6px; }
.movie-qr-heading { font-size: 0.94rem; font-weight: 800; color: #bae6fd; }
.movie-qr-sub { font-size: 0.75rem; color: var(--text-muted); word-break: break-word; line-height: 1.4; }
.movie-qr-actions { display: flex; gap: 8px; width: 100%; }
.movie-qr-actions > * { flex: 1; }

.ep-list { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; max-height: 380px; overflow-y: auto; padding-right: 4px; }
.ep-row {
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
    padding: 11px 14px; background: #080b12; border: 1px solid var(--border);
    border-radius: 11px; flex-wrap: wrap;
}
.ep-row:hover { border-color: var(--border-strong); background: var(--surface-2); }
.ep-row.active { border-color: var(--netflix); background: rgba(229, 9, 20, 0.12); }
.ep-info { flex: 1; min-width: 190px; }
.ep-title { font-size: 0.87rem; font-weight: 700; color: var(--text); word-break: break-word; }
.ep-meta { font-size: 0.75rem; color: var(--text-muted); margin-top: 3px; }
.ep-actions { display: flex; gap: 6px; flex-wrap: wrap; }

.mirrors-list { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--border); }
.mirror-item {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 14px; background: #080b12; border: 1px solid var(--border);
    border-radius: 10px; margin-bottom: 8px; gap: 10px; flex-wrap: wrap;
}
.mirror-item.active { border-color: var(--vlc); background: rgba(249, 115, 22, 0.08); }
.mirror-name { font-weight: 600; font-size: 0.85rem; flex: 1; min-width: 190px; }

/* ── Browse & Poster Grid ────────────────────────────────── */
.catalog-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
.catalog-title-wrap h2 { font-size: 1.15rem; font-weight: 900; letter-spacing: -0.3px; }
.status-sub { font-size: 0.82rem; color: var(--text-muted); margin-top: 2px; }
.filter-pills { display: flex; gap: 6px; background: #070910; padding: 4px; border-radius: 11px; border: 1px solid var(--border); }
.filter-btn {
    border: none; background: transparent; color: var(--text-muted);
    padding: 7px 14px; min-height: 36px; border-radius: 8px; font-size: 0.8rem; font-weight: 700; cursor: pointer;
}
.filter-btn.active { background: var(--netflix); color: #fff; }
.filter-btn:not(.active):hover { color: #fff; }

.search-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(168px, 1fr)); gap: 14px; }
.movie-card {
    position: relative; background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; overflow: hidden; cursor: pointer;
    contain: layout style;
    transition: transform 0.16s ease, border-color 0.16s;
    display: flex; flex-direction: column;
}
.movie-card:hover { transform: translateY(-2px); border-color: var(--border-strong); }
.poster-wrap { position: relative; width: 100%; aspect-ratio: 2/3; background: var(--surface-2); overflow: hidden; }
.poster-wrap img { position: relative; z-index: 0; width: 100%; height: 100%; object-fit: cover; display: block; transition: opacity 0.2s ease; }
.poster-shade {
    position: absolute; inset: 0; z-index: 1;
    background: linear-gradient(to top, rgba(9,9,11,0.92) 0%, rgba(9,9,11,0.12) 45%, rgba(9,9,11,0.22) 100%);
    pointer-events: none;
}
.card-tag-left, .card-tag-right {
    position: absolute; top: 7px; z-index: 3; font-size: 0.62rem; font-weight: 700; padding: 2px 6px; border-radius: 5px;
    display: inline-flex; align-items: center; gap: 3px; line-height: 1.15; pointer-events: none;
}
.card-tag-left { left: 7px; background: rgba(9,9,11,0.82); color: var(--text); border: 1px solid rgba(255,255,255,0.14); }
.card-tag-right { right: 7px; background: var(--netflix); color: #fff; }
.hover-play {
    position: absolute; inset: 0; margin: auto; width: 42px; height: 42px; border-radius: 50%;
    background: rgba(255, 255, 255, 0.94); color: #09090b; display: grid; place-items: center;
    opacity: 0; transform: scale(0.9); transition: opacity 0.18s, transform 0.18s;
    pointer-events: none;
}
.movie-card:hover .hover-play, .rail-card:hover .hover-play { opacity: 1; transform: scale(1); }
.movie-card-info { padding: 9px 10px 10px; flex: 1; display: flex; flex-direction: column; justify-content: space-between; gap: 4px; }
.movie-card-title {
    font-size: 0.82rem; font-weight: 600; line-height: 1.3; color: var(--text);
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.movie-card-sub { font-size: 0.69rem; font-weight: 500; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; min-width: 0; }
.movie-card-sub-row { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.card-dl-btn {
    width: 24px; height: 24px; border-radius: 6px; border: 1px solid var(--border);
    background: var(--surface-2); color: var(--text-muted); display: inline-flex;
    align-items: center; justify-content: center; cursor: pointer; flex-shrink: 0;
    transition: color 0.15s, border-color 0.15s, background 0.15s;
}
.card-dl-btn:hover { color: #fff; border-color: var(--border-strong); background: var(--surface-hover); }
body.sh-vip-locked .card-dl-btn,
body.sh-vip-dl-locked .card-dl-btn {
    border-color: rgba(234, 179, 8, 0.42);
    color: #fde047;
    background: rgba(234, 179, 8, 0.08);
}
body.sh-vip-locked #btnDownloadQuality,
body.sh-vip-dl-locked #btnDownloadQuality,
body.sh-vip-locked #btnOfflineDownloadActive,
body.sh-vip-dl-locked #btnOfflineDownloadActive {
    border-color: rgba(234, 179, 8, 0.45);
    color: #fde047;
}

/* ── Modals & Full-Page Views ────────────────────────────── */
.modal-overlay {
    position: fixed; top: 0; right: 0; bottom: 0; left: 0; z-index: 95; background: rgba(0, 0, 0, 0.84);
    display: none; align-items: center; justify-content: center; padding: 16px;
}
.sh-view-hidden { display: none !important; }
.modal-overlay.sh-full-page {
    position: relative !important;
    top: auto !important; right: auto !important; bottom: auto !important; left: auto !important;
    inset: auto !important;
    z-index: 10 !important;
    width: 100% !important;
    min-height: 100vh;
    background: var(--bg) !important;
    padding: 0 0 32px !important;
}
.modal-overlay.sh-full-page.open { display: none !important; }
.modal-overlay.sh-full-page.sh-page-visible { display: block !important; }
.modal-overlay.sh-full-page .settings-dialog {
    width: 100% !important;
    max-width: 720px !important;
    height: auto !important;
    max-height: none !important;
    margin: 0 auto !important;
    display: block !important;
    overflow: visible !important;
    background: var(--bg) !important;
    border: none !important;
    border-radius: 0 !important;
    box-shadow: none !important;
}
.modal-overlay.sh-full-page .settings-head {
    display: flex !important;
    position: relative !important;
    background: var(--bg-elevated) !important;
    padding: 14px 16px !important;
    border-bottom: 1px solid var(--border) !important;
}
.modal-overlay.sh-full-page .settings-body {
    display: flex !important;
    flex-direction: column !important;
    overflow: visible !important;
    height: auto !important;
    min-height: 75vh !important;
    background: var(--bg) !important;
    padding: 16px 14px 44px !important;
}
.modal-overlay.open { display: flex; }
.modal-dialog {
    width: 100%; max-width: 440px; max-height: 92vh; overflow-y: auto;
    background: var(--surface-solid); border: 1px solid var(--border-strong); color: var(--text);
    border-radius: 16px; padding: 22px 20px; text-align: center; box-shadow: 0 24px 64px rgba(0,0,0,0.85);
}
.modal-qr-frame { width: 220px; height: 220px; margin: 14px auto; background: #fff; padding: 10px; border-radius: 14px; }
.modal-qr-frame img { width: 100%; height: 100%; display: block; border-radius: 6px; }
.modal-steps {
    text-align: left; background: rgba(255,255,255,0.03); border: 1px solid var(--border);
    border-radius: 10px; padding: 12px 14px; font-size: 0.8rem; color: var(--text-muted); line-height: 1.55; margin-top: 14px;
}
.modal-steps b { color: var(--text); }

/* ── Dedicated Settings, Startup Popup & Offline UI ──────── */
.settings-dialog {
    width: 100%; max-width: 700px; height: min(92vh, 880px); max-height: 92dvh;
    display: flex; flex-direction: column; overflow: hidden;
    background: var(--bg-elevated); color: var(--text);
    border: 1px solid var(--border-strong); border-radius: 16px; text-align: left;
    box-shadow: 0 24px 64px rgba(0,0,0,0.88);
}
.settings-head {
    flex-shrink: 0;
    background: var(--surface-solid);
    padding: 14px 18px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
}
.settings-body {
    flex: 1 1 auto; min-height: 0; overflow-y: auto; -webkit-overflow-scrolling: touch;
    padding: 16px 18px 28px; display: flex; flex-direction: column; gap: 12px;
    background: var(--bg);
}
.settings-card {
    flex-shrink: 0;
    background: var(--surface-solid);
    border: 1px solid var(--border);
    border-radius: 12px; padding: 14px 15px; color: var(--text);
}
.settings-card-title {
    font-size: 0.85rem; font-weight: 700; color: var(--text);
    display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 11px;
}
.settings-menu-item {
    flex-shrink: 0; width: 100%;
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    padding: 13px 14px; border-radius: 12px;
    background: var(--surface-solid);
    border: 1px solid var(--border); color: var(--text);
    cursor: pointer; text-align: left; transition: border-color 0.15s, background 0.15s;
}
.settings-menu-item:hover, .settings-menu-item:active {
    border-color: var(--border-strong); background: var(--surface-2);
}
.settings-menu-item.highlight {
    border-color: rgba(255, 255, 255, 0.16);
}
.settings-menu-left { display: flex; align-items: center; gap: 12px; min-width: 0; flex: 1; }
.settings-menu-ico {
    width: 38px; height: 38px; border-radius: 10px; flex-shrink: 0;
    background: var(--surface-2); border: 1px solid var(--border);
    color: var(--text); display: grid; place-items: center;
}
.settings-menu-title { font-size: 0.9rem; font-weight: 700; color: var(--text); line-height: 1.25; }
.settings-menu-sub { font-size: 0.73rem; color: var(--text-muted); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.settings-menu-chev { color: var(--text-muted); flex-shrink: 0; display: grid; place-items: center; }
.settings-row {
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    padding: 10px 0; border-top: 1px solid var(--border); flex-wrap: wrap;
}
.settings-row:first-of-type { border-top: none; padding-top: 0; }
.settings-label { font-size: 0.82rem; font-weight: 600; color: var(--text); }
.settings-sub { font-size: 0.72rem; color: var(--text-muted); margin-top: 2px; line-height: 1.35; }
.avatar-grid { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.avatar-chip {
    width: 40px; height: 40px; border-radius: 10px; border: 1px solid var(--border);
    background: var(--surface-2); color: var(--text-muted); cursor: pointer; display: grid; place-items: center;
    transition: border-color 0.14s, background 0.14s, color 0.14s;
}
.avatar-chip.active { border-color: var(--accent); background: rgba(229,9,20,0.16); color: #fff; }
.theme-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(136px, 1fr)); gap: 10px; }
.theme-card {
    background: var(--surface-2); border: 1px solid var(--border); border-radius: 10px;
    padding: 11px 12px; cursor: pointer; text-align: left; color: var(--text); transition: border-color 0.16s;
}
.theme-card:hover { border-color: var(--border-strong); }
.theme-card.active { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
.theme-dots { display: flex; gap: 6px; margin-bottom: 7px; }
.theme-dot { width: 15px; height: 15px; border-radius: 50%; border: 1px solid rgba(255,255,255,0.22); }
.theme-name { font-size: 0.8rem; font-weight: 700; color: #ffffff; }
.theme-desc { font-size: 0.69rem; color: var(--text-muted); margin-top: 2px; }
.storage-bar-track {
    width: 100%; height: 7px; border-radius: 999px; background: rgba(255,255,255,0.08);
    overflow: hidden; margin: 8px 0 6px;
}
.storage-bar-fill { height: 100%; width: 0%; background: var(--accent); transition: width 0.3s ease; border-radius: 999px; }
.dl-item-card {
    display: flex; gap: 12px; align-items: flex-start;
    background: var(--surface-solid); border: 1px solid var(--border);
    border-radius: 12px; padding: 12px; margin-bottom: 10px;
}
.dl-poster {
    width: 54px; height: 78px; border-radius: 8px; object-fit: cover;
    background: var(--surface-2); flex-shrink: 0;
}
.dl-q-pill, .dl-lang-pill {
    background: var(--surface-2); border: 1px solid var(--border); color: var(--text);
    padding: 8px 12px; border-radius: 8px; font-size: 0.78rem; font-weight: 600; cursor: pointer;
    transition: border-color 0.15s, background 0.15s;
}
.dl-q-pill.active, .dl-lang-pill.active {
    background: var(--accent); border-color: var(--accent); color: #fff;
}
.btn-icon-nav {
    display: inline-flex; align-items: center; justify-content: center; gap: 6px;
    height: 34px; padding: 0 10px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--surface-2); color: var(--text); font-size: 0.76rem; font-weight: 600;
    cursor: pointer; position: relative; white-space: nowrap;
}
.btn-icon-nav:hover { border-color: var(--border-strong); background: var(--surface-hover); }
.nav-dl-badge {
    min-width: 17px; height: 17px; padding: 0 5px; border-radius: 999px;
    background: var(--accent); color: #fff; font-size: 0.64rem; font-weight: 800;
    display: inline-flex; align-items: center; justify-content: center;
}
.live-dl-banner {
    display: none; position: fixed; right: 16px; bottom: 18px; z-index: 85;
    width: min(370px, calc(100vw - 24px));
    background: var(--surface-solid); border: 1px solid var(--border-strong); border-radius: 12px;
    padding: 11px 13px; box-shadow: 0 14px 36px rgba(0,0,0,0.82); color: var(--text);
}
.live-dl-banner.show { display: block; animation: toastIn 0.2s ease; }

/* Startup Update Available Modal */
#startupUpdateModal.modal-overlay.sh-full-page.sh-page-visible {
    display: flex !important;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    min-height: 100dvh;
    padding: 20px 16px !important;
}
.vip-nav-pill {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 5px 10px; border-radius: 999px; font-size: 0.72rem; font-weight: 800;
    cursor: pointer; border: 1px solid rgba(234, 179, 8, 0.45);
    background: rgba(234, 179, 8, 0.16); color: #fde047;
    white-space: nowrap; flex-shrink: 1; min-width: 0; max-width: 155px;
}
.vip-nav-pill svg { flex-shrink: 0; }
#btnVipNavLabel { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.vip-nav-pill.unlocked {
    border-color: rgba(34, 197, 94, 0.5);
    background: rgba(34, 197, 94, 0.16);
    color: #86efac;
}
.vip-nav-pill.hd-only {
    border-color: rgba(56, 189, 248, 0.5);
    background: rgba(56, 189, 248, 0.16);
    color: #7dd3fc;
}
.vip-card-lock {
    display: none;
    position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
    z-index: 3; width: 42px; height: 42px; border-radius: 50%;
    background: rgba(10, 12, 20, 0.88); border: 1.5px solid rgba(234, 179, 8, 0.78);
    color: #facc15; align-items: center; justify-content: center;
    box-shadow: 0 6px 20px rgba(0,0,0,0.75); pointer-events: none;
}
.vip-4k-lock {
    display: none;
    position: absolute; top: calc(50% + 26px); left: 50%; transform: translateX(-50%);
    z-index: 3; padding: 2px 7px; border-radius: 6px; white-space: nowrap;
    background: rgba(10, 12, 20, 0.92); border: 1px solid rgba(234, 179, 8, 0.78);
    color: #fde047; font-size: 0.62rem; font-weight: 900; letter-spacing: 0.35px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.7); pointer-events: none;
}
.vip-tag-hd { display: none; }
body.sh-vip-4k-locked .sh-4k-opt-card .vip-tag-full { display: none; }
body.sh-vip-4k-locked .sh-4k-opt-card .vip-tag-hd { display: inline; color: #7dd3fc; }
body.sh-vip-locked .vip-card-lock,
body.sh-vip-4k-locked .sh-4k-card-locked .vip-card-lock { display: inline-flex; }
body.sh-vip-4k-locked .sh-4k-card-locked .vip-4k-lock { display: inline-flex; }
.vip-plans-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
    gap: 12px; margin-bottom: 12px;
}
.vip-plan-item {
    background: var(--bg); border: 1px solid var(--border); border-radius: 14px;
    padding: 14px; text-align: left; cursor: pointer; color: var(--text);
    display: flex; flex-direction: column; justify-content: space-between; gap: 10px;
    transition: border-color 0.15s, background 0.15s, transform 0.15s;
}
.vip-plan-item:hover { border-color: rgba(234, 179, 8, 0.55); transform: translateY(-1px); }
.vip-plan-item.ultra { border-color: rgba(234, 179, 8, 0.45); background: rgba(234, 179, 8, 0.06); }
.vip-plan-item.selected { border-color: #eab308; background: rgba(234, 179, 8, 0.14); box-shadow: 0 0 0 1px rgba(234, 179, 8, 0.35); }
.vip-plan-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.vip-plan-name { font-size: 0.96rem; font-weight: 800; color: #fff; }
.vip-plan-badge {
    font-size: 0.66rem; font-weight: 800; padding: 3px 8px; border-radius: 999px;
    background: rgba(148, 163, 184, 0.14); color: #cbd5e1; border: 1px solid rgba(148, 163, 184, 0.25);
    white-space: nowrap;
}
.vip-plan-badge.dl { background: rgba(56, 189, 248, 0.16); color: #7dd3fc; border-color: rgba(56, 189, 248, 0.35); }
.vip-plan-badge.ultra { background: rgba(234, 179, 8, 0.18); color: #fde047; border-color: rgba(234, 179, 8, 0.45); }
.vip-plan-price-row { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.vip-plan-price { font-size: 1.38rem; font-weight: 900; color: #fde047; line-height: 1.1; }
.vip-plan-validity { font-size: 0.72rem; font-weight: 600; color: var(--text-muted); }
.vip-plan-feats { list-style: none; display: flex; flex-direction: column; gap: 5px; font-size: 0.74rem; line-height: 1.35; }
.vip-feat-yes { color: #86efac; }
.vip-feat-no { color: #94a3b8; opacity: 0.85; }
.vip-plan-sub { font-size: 0.7rem; color: var(--text-muted); line-height: 1.35; }
.vip-plan-sub.gold { color: #fde047; font-weight: 700; }
.vip-plan-buy-btn {
    display: flex; align-items: center; justify-content: center; gap: 6px; width: 100%;
    min-height: 38px; padding: 8px 12px; border-radius: 10px; text-decoration: none;
    font-size: 0.78rem; font-weight: 800; color: #fff; cursor: pointer;
    background: linear-gradient(135deg, #e11d48, #be123c); border: 1px solid rgba(255,255,255,0.14);
}
.vip-plan-item.ultra .vip-plan-buy-btn {
    background: linear-gradient(135deg, #d97706, #b45309);
}
.vip-plan-buy-btn:hover { opacity: 0.94; }
body.sh-vip-locked .hover-play,
body.sh-vip-4k-locked .sh-4k-card-locked .hover-play { display: none !important; }
body.sh-vip-locked .poster-wrap img,
body.sh-vip-locked .shelf-poster,
body.sh-vip-4k-locked .sh-4k-card-locked .poster-wrap img,
body.sh-vip-4k-locked .sh-4k-card-locked .shelf-poster { filter: brightness(0.76); }
.vip-key-input {
    width: 100%; background: var(--bg); border: 1px solid var(--border-strong);
    color: #fff; border-radius: 10px; padding: 12px 14px; font-size: 0.96rem;
    font-family: ui-monospace, Consolas, monospace; font-weight: 700;
    letter-spacing: 1px; text-transform: uppercase; outline: none; margin-bottom: 10px;
}
.vip-key-input:focus { border-color: #eab308; }
.vip-err-box {
    display: none; font-size: 0.78rem; color: #fca5a5;
    background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.35);
    border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; line-height: 1.4;
}
.startup-update-card {
    width: 100%; max-width: 400px; text-align: left;
    background: var(--surface-solid); border: 1px solid var(--border-strong);
    border-radius: 16px; padding: 20px 18px; color: var(--text);
    box-shadow: 0 24px 64px rgba(0, 0, 0, 0.9);
}
.startup-update-head {
    display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 12px;
}
.startup-update-ico {
    width: 40px; height: 40px; border-radius: 10px; flex-shrink: 0;
    background: rgba(229, 9, 20, 0.14); border: 1px solid rgba(229, 9, 20, 0.35);
    color: var(--netflix); display: grid; place-items: center;
}
.startup-ver-row {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    margin-bottom: 10px; font-family: ui-monospace, Consolas, monospace; font-size: 0.76rem;
}
.startup-ver-pill {
    padding: 3px 8px; border-radius: 6px; background: var(--surface-2);
    border: 1px solid var(--border); color: var(--text-muted); font-weight: 700;
}
.startup-ver-pill.new {
    background: rgba(34, 197, 94, 0.14); border-color: rgba(34, 197, 94, 0.34); color: #86efac;
}
.startup-update-notes {
    font-size: 0.78rem; color: var(--text-muted); line-height: 1.5;
    background: var(--bg); border: 1px solid var(--border); border-radius: 10px;
    padding: 10px 12px; max-height: 150px; overflow-y: auto; white-space: pre-line;
    margin-bottom: 12px;
}
.startup-update-actions {
    display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
}
@media (max-width: 767px) {
    .modal-overlay.fullscreen-mobile { padding: 0; }
    .modal-overlay.fullscreen-mobile .settings-dialog {
        max-width: 100%; height: 100vh; height: 100dvh; max-height: 100dvh; border-radius: 0; border: none;
    }
    .settings-head, .settings-body { padding-left: 14px; padding-right: 14px; }
    .btn-icon-nav { height: 33px; padding: 0 8px; min-width: 34px; }
    .btn-icon-nav .nav-lbl-hide-sm { display: none; }
    .live-dl-banner { left: 12px; right: 12px; width: auto; bottom: calc(74px + env(safe-area-inset-bottom)); }
}
.trailer-frame-wrap { width: 100%; aspect-ratio: 16/9; border-radius: 14px; overflow: hidden; background: #000; margin-top: 12px; }
.trailer-frame-wrap iframe { width: 100%; height: 100%; border: none; }

.toast-wrap {
    position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); z-index: 120;
    display: flex; flex-direction: column; gap: 8px; pointer-events: none; max-width: calc(100vw - 28px);
}
.toast {
    display: flex; align-items: center; gap: 10px; background: rgba(16, 20, 34, 0.96);
    border: 1px solid var(--border-strong); color: var(--text); padding: 11px 16px; border-radius: 12px;
    font-size: 0.86rem; font-weight: 600; box-shadow: 0 14px 36px rgba(0,0,0,0.65); animation: toastIn 0.2s ease;
}
.toast-ok { border-color: rgba(16, 185, 129, 0.55); }
.toast-err { border-color: rgba(239, 68, 68, 0.55); color: #fecaca; }
@keyframes toastIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }

.mobile-dock {
    display: none; position: fixed; left: 0; right: 0; bottom: 0; z-index: 65;
    background: #09090b; border-top: 1px solid var(--border);
    padding: 6px 8px calc(6px + env(safe-area-inset-bottom));
}
.mobile-dock-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 2px; }
.dock-btn {
    display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px;
    min-height: 46px; border: none; background: transparent; color: var(--text-muted);
    font-size: 0.68rem; font-weight: 600; border-radius: 8px; cursor: pointer;
}
.dock-btn.active, .dock-btn:active { color: #fff; }

.shelf-row { display: flex; gap: 12px; overflow-x: auto; padding-bottom: 6px; scrollbar-width: thin; -webkit-overflow-scrolling: touch; }
.shelf-card {
    flex: 0 0 140px; background: #080b12; border: 1px solid var(--border); border-radius: 12px;
    overflow: hidden; cursor: pointer; position: relative; transition: transform 0.16s, border-color 0.16s;
}
.shelf-card:hover { transform: translateY(-2px); border-color: var(--netflix); }
.shelf-poster { width: 100%; aspect-ratio: 2/3; object-fit: cover; display: block; background: var(--surface-2); }
.shelf-fallback { width: 100%; aspect-ratio: 2/3; background: linear-gradient(135deg, #1b1e2e, #0e1019); display: grid; place-items: center; color: var(--gold); font-weight: 800; }
.shelf-info { padding: 8px 9px; font-size: 0.75rem; font-weight: 700; line-height: 1.3; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.shelf-del {
    position: absolute; top: 6px; right: 6px; width: 24px; height: 24px; border-radius: 50%; border: none;
    background: rgba(5,6,11,0.82); color: #e2e8f0; display: grid; place-items: center; cursor: pointer; font-size: 0.75rem;
}
.shelf-del:hover { background: var(--netflix); color: #fff; }
.fav-btn {
    position: absolute; bottom: 8px; right: 8px; z-index: 3; width: 32px; height: 32px; border-radius: 50%;
    background: rgba(9,9,11,0.86); border: 1px solid rgba(255,255,255,0.18);
    color: #cbd5e1; display: grid; place-items: center; cursor: pointer;
}
.fav-btn.faved { background: var(--netflix); border-color: #fb7185; color: #fff; }
.watched-pill {
    position: absolute; bottom: 8px; left: 8px; z-index: 3; background: rgba(16,185,129,0.9);
    color: #fff; font-size: 0.64rem; font-weight: 800; padding: 3px 7px; border-radius: 6px;
}
.watched-chip { background: rgba(16,185,129,0.16); color: #6ee7b7; border: 1px solid rgba(16,185,129,0.35); font-size: 0.68rem; font-weight: 800; padding: 2px 7px; border-radius: 5px; margin-left: 6px; }
.cta-row { display: flex; gap: 10px; align-items: stretch; flex-wrap: wrap; }
.cta-row .vlc-banner { flex: 2 1 250px; }
.btn-next-ep {
    flex: 1 1 170px; min-height: 52px; padding: 12px 16px; border-radius: 12px;
    border: 1px solid rgba(251, 191, 36, 0.45); background: rgba(251, 191, 36, 0.14); color: #fde68a;
    font-size: 0.9rem; font-weight: 800; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; gap: 8px;
}
.track-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 16px; }
.cat-strip { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 10px; margin-bottom: 12px; scrollbar-width: none; }
.cat-strip::-webkit-scrollbar { display: none; }
.cat-chip {
    flex: 0 0 auto; min-height: 36px; padding: 6px 14px; border-radius: 999px;
    border: 1px solid var(--border); background: #090c14; color: var(--text-muted);
    font-size: 0.8rem; font-weight: 700; cursor: pointer; transition: background 0.15s, color 0.15s;
}
.cat-chip.active { background: var(--netflix); border-color: var(--netflix); color: #fff; }
.cat-chip:not(.active):hover { background: var(--surface-2); color: #fff; }
.load-more-wrap { display: flex; justify-content: center; margin-top: 20px; }
.remote-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin-top: 14px; }
.remote-btn {
    min-height: 48px; padding: 8px 4px; border-radius: 11px; border: 1px solid var(--border-strong);
    background: var(--surface-2); color: var(--text); font-size: 0.8rem; font-weight: 800; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
}
.remote-btn-play { background: linear-gradient(135deg, var(--vlc), var(--netflix)); border: none; color: #fff; }
.range-slider { width: 100%; accent-color: var(--netflix); cursor: pointer; height: 6px; }

/* ── Responsive & Overflow Safety Guards (Phone / Tablet / PC) ── */
html, body { max-width: 100%; overflow-x: hidden; }
.hero-stage, .hero-card, .hero-inner > *, .app-shell, .app-shell > *, .command-grid > *, .ott-rails-wrap, .ott-rail, .rail-track-wrap, .glass-card, .theater-main, .details-main, .link-input-wrap, .input-group, .quality-row {
    min-width: 0;
    max-width: 100%;
}

@media (max-width: 1380px) {
    .mode-chip { display: none; }
}

/* Tablet Breakpoint (768px - 1080px): 2-tier glass navbar + adaptable grids */
@media (min-width: 768px) and (max-width: 1080px) {
    .topbar-inner { flex-wrap: wrap; row-gap: 8px; padding: 10px 16px; }
    .brand-group { display: contents; }
    .brand { order: 1; }
    .nav-search { order: 2; max-width: 300px; flex: 1 1 200px; }
    .nav-actions { order: 3; }
    .top-nav-links {
        order: 4; flex: 0 0 100%; width: 100%;
        display: flex; align-items: center; gap: 4px; overflow-x: auto; scrollbar-width: none;
        padding-top: 6px; border-top: 1px solid rgba(255, 255, 255, 0.07);
    }
    .top-nav-links::-webkit-scrollbar { display: none; }
    .hero-card { min-height: 360px; }
    .hero-inner { padding: 26px 28px 34px; gap: 20px; }
    .hero-poster-frame { width: 152px; }
    .rail-card { flex: 0 0 150px; }
    .search-grid { grid-template-columns: repeat(auto-fill, minmax(154px, 1fr)); gap: 13px; }
    .command-grid { grid-template-columns: minmax(0, 1.45fr) minmax(240px, 0.85fr); }
}

@media (max-width: 920px) and (min-width: 768px) {
    .command-grid { grid-template-columns: 1fr; }
    .theater-grid { grid-template-columns: 1fr; }
    .movie-qr-box { flex-direction: row; text-align: left; }
    .qr-frame-lg { width: 120px; height: 120px; min-width: 120px; }
    .movie-qr-actions { width: auto; }
}

/* Phone Breakpoint (<= 767px) */
@media (max-width: 767px) {
    body { padding-bottom: calc(82px + env(safe-area-inset-bottom)); }
    .topbar-inner { padding: 8px 10px; gap: 6px; }
    .brand-group { min-width: 0; flex: 1 1 auto; gap: 8px; }
    .brand { min-width: 0; gap: 6px; }
    .top-nav-links { display: none; }
    .brand-tag { display: none; }
    .brand-s-mark { width: 22px; height: 27px; }
    .brand-name { font-size: 0.96rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .nav-actions { gap: 4px; flex-shrink: 0; }
    .vip-nav-pill { padding: 4px 8px; font-size: 0.68rem; gap: 4px; max-width: 112px; }
    #btnNavUpdatePill { padding: 4px 7px !important; font-size: 0.67rem !important; min-height: 28px; flex-shrink: 0; }
    .btn-icon-nav, .btn-install-nav, .btn-remote-nav, .btn-qr-nav { flex-shrink: 0; }
    .nav-search { display: none; }
    .mobile-search-bar { display: block; padding: 0 12px 9px; }
    .mobile-search-bar .nav-search { display: flex; max-width: 100%; width: 100%; }
    .hero-stage { padding: 0 12px; margin-top: 8px; }
    .hero-card { min-height: 300px; border-radius: 16px; }
    .hero-inner { padding: 20px 14px 40px; grid-template-columns: 1fr; }
    .hero-title { font-size: clamp(1.32rem, 5.8vw, 1.85rem); }
    .hero-meta-row { font-size: 0.78rem; margin-bottom: 14px; }
    .btn-nf-play, .btn-nf-glass { height: 40px; padding: 0 13px; font-size: 0.82rem; }
    .hero-poster-frame, .hero-nav-arrow { display: none; }
    .hero-dots { bottom: 12px; right: 50%; transform: translateX(50%); }
    .app-shell { padding: 12px; gap: 14px; }
    .glass-card { padding: 14px; border-radius: 15px; scroll-margin-top: 118px; }
    .command-grid, .theater-grid { grid-template-columns: 1fr; }
    .link-input-wrap { flex-direction: column; }
    .input-group input { font-size: 0.94rem; }
    .quality-row { flex-direction: column; }
    .quality-row .select-control { flex: 1 1 auto; width: 100%; min-width: 0; }
    .quality-row .btn-primary { width: 100%; }
    .vlc-banner { font-size: 0.92rem; padding: 12px 14px; }
    .action-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .action-grid .btn-action { flex: none; width: 100%; min-height: 42px; padding: 8px 9px; font-size: 0.78rem; }
    .details-layout { flex-direction: column; gap: 12px; }
    .detail-poster { width: 96px; }
    .ep-row { flex-direction: column; align-items: stretch; padding: 10px 11px; }
    .ep-info { min-width: 0; }
    .ep-actions { width: 100%; }
    .ep-actions > * { flex: 1 1 auto; }
    .movie-qr-box { flex-direction: column; text-align: center; }
    #phoneQrExpanded { display: none !important; }
    #phoneQrCollapsed { display: flex !important; flex-wrap: nowrap; }
    .rail-card { flex: 0 0 132px; }
    .rail-arrow { display: none; }
    .search-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .mobile-dock { display: block; }
    .track-grid { grid-template-columns: 1fr; }
    .btn-install-nav, .btn-remote-nav, .btn-qr-nav { height: 33px; padding: 0 8px; min-width: 33px; justify-content: center; }
    .btn-install-nav span, .btn-remote-nav span, .btn-qr-nav span { display: none; }
    .toast-wrap { bottom: calc(74px + env(safe-area-inset-bottom)); }
}

@media (max-width: 390px) {
    .brand { gap: 5px; }
    .brand-name { font-size: 0.88rem; }
    .vip-nav-pill { padding: 4px 7px; font-size: 0.65rem; max-width: 96px; }
    #btnNavUpdatePill svg { display: none; }
    .btn-icon-nav, .btn-install-nav, .btn-remote-nav, .btn-qr-nav { height: 31px; min-width: 31px; padding: 0 7px; }
    .action-grid { grid-template-columns: 1fr; }
}

@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { transition-duration: 0.01ms !important; animation-duration: 0.01ms !important; scroll-behavior: auto !important; }
}

/* Progress Bars, Resume Pill, Smart Filters, Subtitle Finder & Pro Inline Controls */
.card-progress-track {
    position: absolute; left: 0; right: 0; bottom: 0; height: 4px;
    background: rgba(255, 255, 255, 0.18); z-index: 4; pointer-events: none;
}
.card-progress-fill { height: 100%; background: var(--netflix); transition: width 0.25s; }
.resume-pill {
    position: absolute; bottom: 8px; left: 8px; z-index: 3;
    background: rgba(229, 9, 20, 0.94); color: #fff;
    font-size: 0.62rem; font-weight: 800; padding: 3px 7px; border-radius: 6px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
}
.imdb-mini { color: var(--gold); font-weight: 800; margin-left: 5px; }
.btn-start-over {
    flex: 0 0 auto; min-height: 52px; padding: 12px 15px; border-radius: 12px;
    border: 1px solid var(--border-strong); background: var(--surface-2); color: var(--text);
    font-size: 0.85rem; font-weight: 800; cursor: pointer; display: inline-flex; align-items: center; gap: 6px;
}
.btn-start-over:hover { border-color: var(--netflix); color: #fff; }
.smart-strip { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; margin-bottom: 14px; }
.smart-pill {
    min-height: 31px; padding: 4px 11px; border-radius: 8px;
    border: 1px solid var(--border); background: var(--surface-solid);
    color: var(--text-muted); font-size: 0.74rem; font-weight: 600; cursor: pointer;
    display: inline-flex; align-items: center; gap: 5px; transition: border-color 0.15s, color 0.15s, background 0.15s;
}
.smart-pill:hover { border-color: var(--border-strong); color: #fff; }
.smart-pill.active { background: var(--netflix); border-color: var(--netflix); color: #fff; }
.sub-finder-box {
    background: rgba(8, 11, 18, 0.82); border: 1px solid var(--border);
    border-radius: 12px; padding: 12px; margin-top: 14px;
}
.sub-finder-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.sub-finder-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.sub-finder-row .input-control {
    flex: 1 1 180px; min-width: 0; background: #0b0e17; border: 1px solid var(--border-strong);
    color: #fff; border-radius: 9px; padding: 7px 10px; font-size: 0.82rem; outline: none;
}
.sub-results-list { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; max-height: 170px; overflow-y: auto; }
.sub-item {
    display: flex; align-items: center; justify-content: space-between; gap: 8px;
    background: rgba(255,255,255,0.04); border: 1px solid var(--border); border-radius: 8px; padding: 6px 10px;
}
.sub-item-lbl { font-size: 0.78rem; font-weight: 600; color: #e2e8f0; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sync-strip { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 9px; padding-top: 9px; border-top: 1px solid rgba(255,255,255,0.07); font-size: 0.75rem; color: var(--text-muted); }
.inline-controls-bar { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
.inline-next-banner {
    display: none; position: absolute; right: 14px; bottom: 52px; z-index: 25;
    background: rgba(11, 14, 24, 0.94); border: 1px solid rgba(229,9,20,0.6);
    border-radius: 12px; padding: 9px 12px; align-items: center; gap: 10px;
    box-shadow: 0 10px 28px rgba(0,0,0,0.7);
}
.inline-next-banner.open { display: flex; }
@media (max-width: 767px) {
    .suggest-dropdown { min-width: 0; width: 100%; max-width: 100%; }
    .sub-finder-box, .smart-strip, .cta-row { min-width: 0; max-width: 100%; }
    .btn-start-over { flex: 1 1 120px; min-height: 44px; justify-content: center; }
}
</style>
</head>
<body>

<!-- Netflix-Style Folded Crimson Ribbon "S" Intro Splash -->
<div class="intro-splash" id="introSplash" onclick="dismissIntro()" aria-label="StreamHub Intro">
    <div class="intro-logo-wrap">
        <svg class="intro-s-svg" viewBox="0 0 120 140" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
            <defs>
                <linearGradient id="splashSDiag" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0%" stop-color="#ff2e39"/>
                    <stop offset="52%" stop-color="#e50914"/>
                    <stop offset="100%" stop-color="#a8060e"/>
                </linearGradient>
                <linearGradient id="splashSShadow" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stop-color="#690308"/>
                    <stop offset="100%" stop-color="#9c050d"/>
                </linearGradient>
            </defs>
            <path d="M96 18 L96 42 L34 42 C24 42 24 26 24 26 C24 18 32 18 40 18 Z" fill="url(#splashSShadow)"/>
            <path d="M24 122 L24 98 L86 98 C96 98 96 114 96 114 C96 122 88 122 80 122 Z" fill="url(#splashSShadow)"/>
            <path d="M98 18 L44 18 C25 18 18 30 18 46 C18 63 29 72 51 77 L69 81 C82 84 85 89 85 95 C85 103 77 107 62 107 L19 107 L19 125 L76 125 C97 125 104 111 104 94 C104 75 91 66 68 61 L50 57 C38 54 35 49 35 43 C35 36 42 33 56 33 L98 33 Z" fill="url(#splashSDiag)"/>
            <polygon points="35,51 84,81 88,67 39,40" fill="rgba(255,255,255,0.18)"/>
        </svg>
        <div class="intro-wordmark">STREAMHUB</div>
        <div class="intro-sub">4K Cinema &bull; Prime &bull; Hotstar Hub</div>
    </div>
    <button class="intro-skip" type="button" onclick="event.stopPropagation();dismissIntro()">Skip Intro &rsaquo;</button>
</div>

<!-- Sticky OTT Glass Navbar -->
<header class="topbar" id="topNavbar">
    <div class="topbar-inner">
        <div class="brand-group">
            <a class="brand" href="#heroSpotlight" onclick="scrollToEl('heroSpotlight');return false;">
                <svg class="brand-s-mark" viewBox="0 0 120 140" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                    <defs>
                        <linearGradient id="navSRib" x1="0" y1="0" x2="1" y2="1">
                            <stop offset="0%" stop-color="#ff2e39"/>
                            <stop offset="55%" stop-color="#e50914"/>
                            <stop offset="100%" stop-color="#9e050d"/>
                        </linearGradient>
                    </defs>
                    <path d="M96 18 L96 42 L34 42 C24 42 24 26 24 26 C24 18 32 18 40 18 Z" fill="#730309"/>
                    <path d="M24 122 L24 98 L86 98 C96 98 96 114 96 114 C96 122 88 122 80 122 Z" fill="#730309"/>
                    <path d="M98 18 L44 18 C25 18 18 30 18 46 C18 63 29 72 51 77 L69 81 C82 84 85 89 85 95 C85 103 77 107 62 107 L19 107 L19 125 L76 125 C97 125 104 111 104 94 C104 75 91 66 68 61 L50 57 C38 54 35 49 35 43 C35 36 42 33 56 33 L98 33 Z" fill="url(#navSRib)"/>
                </svg>
                <span class="brand-name">StreamHub</span>
                <span class="brand-tag">ULTRA</span>
            </a>

            <nav class="top-nav-links" aria-label="Quick OTT sections">
                <button class="top-nav-btn active" onclick="scrollToEl('heroSpotlight')">Home</button>
                <button class="top-nav-btn" onclick="jumpCategory('hollywood-movies')">Movies</button>
                <button class="top-nav-btn" onclick="jumpCategory('web-series')">Web Series</button>
                <button class="top-nav-btn" onclick="jumpCategory('netflix')">Netflix</button>
                <button class="top-nav-btn" onclick="jumpCategory('amazon-prime-video')">Prime</button>
                <button class="top-nav-btn" onclick="jumpCategory('jiohotstar')">Hotstar</button>
                <button class="top-nav-btn" onclick="openHub4kModal()">4K Cinema</button>
                <button class="top-nav-btn" onclick="scrollToEl('browseSection')">All Movies</button>
            </nav>
        </div>

        <!-- PC Search Bar -->
        <div class="nav-search" role="search">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input type="text" id="searchInput" autocomplete="off" placeholder="Search HDHub4u (Avengers, Dhurandhar, Series)..." oninput="onSearchTyping(this.value, 'desktop')" onfocus="if(this.value.trim().length>=2) onSearchTyping(this.value, 'desktop')" onkeydown="if(event.key==='Enter'){hideSuggestBoxes(); searchMovie();}">
            <button class="btn-mic" id="btnVoiceSearch" type="button" onclick="startVoiceSearch(false)" title="Voice Search" aria-label="Voice Search">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>
            </button>
            <button class="btn-search-go" onclick="hideSuggestBoxes(); searchMovie()">Search</button>
            <div class="suggest-dropdown" id="desktopSuggestBox" role="listbox" aria-label="Live search suggestions"></div>
        </div>

        <div class="nav-actions">
            <button type="button" class="vip-nav-pill" id="btnVipNavPill" onclick="openVipUnlockModal()" title="VIP Key Pass Status">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex-shrink:0;"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
                <span id="btnVipNavLabel">VIP Locked</span>
            </button>
            <button class="smart-pill active" id="btnNavUpdatePill" onclick="openUpdateModal(false)" style="display:none;padding:4px 10px;font-size:0.72rem;" title="New Update Available">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polyline points="16 12 12 8 8 12"/><line x1="12" y1="16" x2="12" y2="8"/></svg>
                <span id="btnNavUpdateLabel">Update</span>
            </button>
            <span class="mode-chip" id="deviceModeChip" onclick="openSettingsModal()" style="cursor:pointer;" title="Profile, Theme &amp; Status"><span id="deviceModeText">PC VLC Mode</span></span>
            <button class="btn-install-nav" id="btnInstallApp" onclick="triggerInstallApp()" title="Install App" aria-label="Install App">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                <span>Install</span>
            </button>
            <button class="btn-remote-nav" id="btnNavRemote" onclick="openRemoteModal()" title="Control PC VLC from Phone" aria-label="Open PC VLC Remote Control">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="6" y="2" width="12" height="20" rx="3"/><circle cx="12" cy="9" r="2"/><line x1="10" y1="15" x2="14" y2="15"/></svg>
                <span>Remote</span>
            </button>
            <button class="btn-qr-nav" id="btnNavQr" onclick="openQrModal()" aria-label="Open Phone QR Scanner Modal">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3z"/><path d="M20 14v7h-7"/></svg>
                <span>Phone QR</span>
            </button>
            <button class="btn-icon-nav" id="btnNavDownloads" onclick="openDownloadsModal()" title="Offline Downloads" aria-label="Open Offline Downloads">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                <span class="nav-lbl-hide-sm">Downloads</span>
                <span class="nav-dl-badge" id="navDownloadsBadge" style="display:none;">0</span>
            </button>
        </div>
    </div>

    <!-- Mobile Search Strip -->
    <div class="mobile-search-bar">
        <div class="nav-search" role="search">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input type="text" id="mobileSearchInput" autocomplete="off" placeholder="Search movies &amp; web series..." oninput="onSearchTyping(this.value, 'mobile')" onfocus="if(this.value.trim().length>=2) onSearchTyping(this.value, 'mobile')" onkeydown="if(event.key==='Enter'){hideSuggestBoxes(); searchMovie(this.value);}">
            <button class="btn-mic" id="btnMobileVoiceSearch" type="button" onclick="startVoiceSearch(true)" title="Voice Search" aria-label="Mobile Voice Search">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>
            </button>
            <button class="btn-search-go" onclick="hideSuggestBoxes(); searchMovie(document.getElementById('mobileSearchInput').value)">Search</button>
            <div class="suggest-dropdown" id="mobileSuggestBox" role="listbox" aria-label="Mobile live search suggestions"></div>
        </div>
    </div>
</header>

<!-- Full-Bleed Netflix / Prime / Hotstar Hero Spotlight -->
<section class="hero-stage" id="heroSpotlight" aria-label="Featured Releases">
    <div class="hero-card" onmouseenter="pauseHeroAuto()" onmouseleave="resumeHeroAuto()">
        <div class="hero-backdrop" id="heroBackdrop"></div>
        <div class="hero-scrim"></div>
        <button class="hero-nav-arrow hero-prev" onclick="prevHero()" aria-label="Previous featured">&#10094;</button>
        <button class="hero-nav-arrow hero-next" onclick="nextHero()" aria-label="Next featured">&#10095;</button>

        <div class="hero-inner">
            <div>
                <div class="hero-kicker">
                    <span class="hero-s-tag">
                        <svg width="13" height="16" viewBox="0 0 120 140" aria-hidden="true"><path d="M98 18 L44 18 C25 18 18 30 18 46 C18 63 29 72 51 77 L69 81 C82 84 85 89 85 95 C85 103 77 107 62 107 L19 107 L19 125 L76 125 C97 125 104 111 104 94 C104 75 91 66 68 61 L50 57 C38 54 35 49 35 43 C35 36 42 33 56 33 L98 33 Z" fill="#e50914"/></svg>
                        <span id="heroTypeBadge">FEATURED</span>
                    </span>
                    <span class="prime-check" style="display:none;">Instant Seek</span>
                    <span class="hotstar-4k" id="heroQualityBadge">4K UHD</span>
                </div>
                <h1 class="hero-title" id="heroTitle">Loading Latest HDHub4u Releases...</h1>
                <div class="hero-meta-row" id="heroMetaRow">
                    <span>Dual Audio &bull; Hindi &amp; English &bull; Zero Download</span>
                </div>
                <div class="hero-actions">
                    <button class="btn-nf-play" id="btnHeroPlay" onclick="playHeroCurrent()">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" style="flex-shrink:0;"><polygon points="6 4 20 12 6 20 6 4"/></svg>
                        <span id="btnHeroPlayLabel">Watch Now</span>
                    </button>
                    <button class="btn-nf-glass" id="btnHeroFav" onclick="toggleHeroFavorite()">+ My List</button>
                    <button class="btn-nf-glass" id="btnHeroPasteLink" onclick="focusLinkInput()">Paste Link</button>
                    <button class="btn-nf-glass" id="btnHeroDownload" onclick="downloadHeroCurrent()" style="display:none;">Download</button>
                </div>
            </div>

            <div class="hero-poster-frame" onclick="playHeroCurrent()" title="Click to Watch">
                <img id="heroPosterImg" src="" alt="Featured Poster" style="display:none;">
            </div>
        </div>

        <div class="hero-dots" id="heroDots"></div>
    </div>
</section>

<main class="app-shell">
    <!-- Top Compact Command Strip: Direct Link Resolver + Phone QR -->
    <section class="command-grid" id="linkSection">
        <div class="glass-card hero-link-card">
            <div class="section-eyebrow">
                <h2>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
                    Direct Stream Link
                </h2>
                <span class="pill-subtle">HDHub4u &bull; HubCloud &bull; HLS</span>
            </div>
            <div class="link-input-wrap">
                <div class="input-group">
                    <input type="text" id="urlInput" placeholder="Paste HDHub4u, HubCloud, or HLS link..." onkeydown="if(event.key==='Enter') resolveLink()">
                    <button class="btn-paste" type="button" onclick="pasteToInput()" title="Paste from Clipboard">Paste</button>
                </div>
                <button class="btn-primary" id="btnResolve" onclick="resolveLink()">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="6 4 20 12 6 20 6 4"/></svg>
                    Resolve &amp; Play
                </button>
            </div>
            <div class="status-bar" id="resolveStatus">
                <span class="spinner" aria-hidden="true"></span>
                <span id="resolveStatusText">Resolving HDHub4u, HubCloud &amp; High-Speed CDN Servers...</span>
            </div>
            <div id="errorBox" class="error-box" style="display:none"></div>
        </div>

        <!-- Phone QR Connection Card -->
        <aside class="glass-card qr-card" id="phoneQrCard">
            <div id="phoneQrCollapsed" style="display:none;width:100%;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;">
                <span style="display:inline-flex;align-items:center;gap:8px;font-size:0.86rem;font-weight:700;color:#bae6fd;">
                    <span class="dot-live"></span>
                    Phone VLC Mode Active
                </span>
                <button class="btn-sm btn-indigo-sm" onclick="openQrModal()">Show Share QR</button>
            </div>
            <div id="phoneQrExpanded">
                <button class="qr-thumb-btn" onclick="openQrModal()" title="Click to enlarge QR code" aria-label="Enlarge Phone QR code">
                    <img id="appQrImg" src="/api/qr.svg" alt="Scan QR to open StreamHub on Phone">
                </button>
                <div class="qr-card-meta">
                    <div class="qr-card-title">Watch on Phone VLC</div>
                    <div class="qr-url-pill" id="lanUrlText">http://...</div>
                    <div class="qr-btn-row">
                        <button class="btn-sm btn-indigo-sm" onclick="openQrModal()">Enlarge QR</button>
                        <button class="btn-sm btn-copy-sm" onclick="copyLanUrl()">Copy URL</button>
                    </div>
                </div>
            </div>
        </aside>
    </section>

    <!-- Continue Watching & My List Shelf -->
    <section class="glass-card" id="librarySection" style="display:none">
        <div class="catalog-head" style="margin-bottom:10px;">
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                <h2 style="font-size:1rem;font-weight:900;">Continue Watching &amp; My List</h2>
                <div class="filter-pills">
                    <button class="filter-btn active" id="libTabHistory" onclick="setLibraryTab('history')">History</button>
                    <button class="filter-btn" id="libTabFavs" onclick="setLibraryTab('favs')">My List</button>
                </div>
            </div>
            <div style="display:flex;gap:8px;align-items:center;">
                <button class="btn-sm btn-indigo-sm" id="libOpenDownloadsBtn" onclick="openDownloadsModal()">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                    Downloads <span id="libDlCount">(0)</span>
                </button>
                <button class="btn-sm btn-copy-sm" onclick="clearLibraryTab()">Clear</button>
            </div>
        </div>
        <div class="shelf-row" id="libraryShelf"></div>
    </section>

    <!-- Movie Qualities / Web Series Episode & IMDb Info Card -->
    <section class="glass-card" id="qualitiesCard" style="display:none">
        <div class="details-layout">
            <img id="detailPoster" class="detail-poster" src="" alt="" style="display:none;">
            <div class="details-main">
                <div class="details-header">
                    <h3 id="movieCardTitle"></h3>
                    <button class="btn-close-card" onclick="closeQualities()" aria-label="Close panel">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    </button>
                </div>

                <div class="detail-meta-strip" id="detailMetaStrip" style="display:none;">
                    <a id="detailImdbBadge" class="imdb-pill" target="_blank" href="#" style="display:none;"></a>
                    <button id="btnWatchTrailer" class="trailer-pill" type="button" onclick="openTrailerModal()" style="display:none;">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="6 4 20 12 6 20 6 4"/></svg>
                        Watch Trailer
                    </button>
                </div>
                <p id="detailStoryline" class="detail-story" style="display:none;"></p>
                <div id="detailScreenshots" class="screenshots-strip" style="display:none;"></div>

                <div id="movieQualityBox">
                    <label style="display:block;font-size:0.82rem;font-weight:700;color:var(--text-muted);margin-bottom:8px;">Select Quality &amp; Language (Stream or Offline Download):</label>
                    <div class="quality-row">
                        <select id="qualitySelect" class="select-control"></select>
                        <button id="btnStreamQuality" class="btn-primary" onclick="useSelectedQuality()">
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="6 4 20 12 6 20 6 4"/></svg>
                            <span>Stream Quality</span>
                        </button>
                        <button id="btnDownloadQuality" class="btn-secondary" type="button" onclick="openDownloadPickerFromDetails()" style="min-height:44px;padding:0 16px;border-radius:11px;display:inline-flex;align-items:center;gap:7px;font-weight:800;white-space:nowrap;">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                            <span>Download</span>
                        </button>
                    </div>
                </div>

                <div id="seriesPanel" style="display:none">
                    <label style="display:block;font-size:0.82rem;font-weight:700;color:var(--text-muted);margin-bottom:8px;">Select Season Pack or Episode Stream:</label>
                    <select id="seasonSelect" class="select-control" onchange="renderSeriesEpisodes()"></select>
                    <div id="episodesList" class="ep-list"></div>
                </div>
            </div>
        </div>
    </section>

    <!-- Active Cinema Player Workspace (#resultsCard) -->
    <section class="glass-card" id="resultsCard" style="display:none">
        <div class="theater-grid">
            <div class="theater-main">
                <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:10px;">
                    <h2 class="theater-title" id="resTitle" style="flex:1;"></h2>
                    <button class="btn-sm btn-copy-sm" id="btnFavActive" onclick="toggleActiveFavorite()">Save Fav</button>
                </div>
                <div class="badge-row">
                    <span class="badge" id="resSize">Size: —</span>
                    <span class="badge badge-green" id="resStatusBadge">Stream Ready</span>
                    <span class="badge badge-gold" id="resSeekBadge">HTTP 206 Seeking Active</span>
                    <span class="badge" id="resTrackBadge">Detecting Tracks...</span>
                    <span class="badge badge-gold" id="resVideoBadge"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><rect x="2" y="4" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/></svg> <span>HD Stream</span></span>
                </div>

                <!-- Primary In-Built Player CTA + Start Over + Binge Next Episode -->
                <div class="cta-row">
                    <button class="vlc-banner" id="btnMainVlc" onclick="launchVlcCurrent()">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="6 4 20 12 6 20 6 4"/></svg>
                        <span id="btnMainVlcText">Play in StreamHub In-Built Player (0% Loss 4K HDR)</span>
                    </button>
                    <button class="btn-start-over" id="btnStartOver" onclick="launchVlcFromStart()" style="display:none;" title="Play from beginning (0:00)">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>
                        Start Over
                    </button>
                    <button class="btn-next-ep" id="btnNextEpisode" onclick="playNextEpisode()" style="display:none;">
                        <span id="btnNextEpisodeText">Next Episode</span>
                        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="5 4 15 12 5 20 5 4"/><line x1="19" y1="5" x2="19" y2="19"/></svg>
                    </button>
                </div>

                <!-- Secondary Playback Actions -->
                <div class="action-grid">
                    <button id="btnInlineWeb" class="btn-action btn-action-web" type="button" onclick="openInlinePlayer()" style="display:none;">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="6 4 20 12 6 20 6 4"/></svg>
                        Play Inside Browser
                    </button>
                    <a id="btnWatchOnline" class="btn-action" target="_blank" href="#" style="display:none;">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
                        Full Browser Tab
                    </a>
                    <button id="btnExternalVlc" class="btn-action" type="button" onclick="launchExternalVlcCurrent()">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                        External VLC
                    </button>
                    <a id="btnDownloadPlaylist" class="btn-action" href="#" download="streamhub.m3u8">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                        .m3u8 Playlist
                    </a>
                    <button id="btnOfflineDownloadActive" class="btn-action" type="button" onclick="openDownloadPickerFromActive()">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                        Save Offline
                    </button>
                    <button class="btn-action" onclick="copyActiveStreamUrl()">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                        Copy Stream URL
                    </button>
                </div>

                <!-- ZIP Season Pack Episodes List -->
                <div id="zipEpisodesPanel" style="display:none;margin-top:18px;">
                    <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:8px;">
                        <h3 id="zipPanelHeading" style="font-size:0.94rem;color:var(--gold);font-weight:800;">Episodes Inside Season ZIP Pack</h3>
                        <button class="btn-sm btn-indigo-sm" onclick="downloadSeasonPlaylist()">Full Season .m3u8</button>
                    </div>
                    <div id="zipEpisodesList" class="ep-list"></div>
                </div>

                <!-- Real Embedded Audio & Subtitle Track Pickers -->
                <div class="track-grid">
                    <div>
                        <label for="audioTrackSelect" style="display:block;font-size:0.82rem;font-weight:700;color:var(--text-muted);margin-bottom:6px;">Audio Track (PC VLC):</label>
                        <select id="audioTrackSelect" class="select-control">
                            <option value="1">Track 1: Default</option>
                        </select>
                    </div>
                    <div>
                        <label for="subTrackSelect" style="display:block;font-size:0.82rem;font-weight:700;color:var(--text-muted);margin-bottom:6px;">Subtitle Track (PC VLC):</label>
                        <select id="subTrackSelect" class="select-control">
                            <option value="auto">Auto / Default Subtitles</option>
                            <option value="off">Subtitles Off</option>
                        </select>
                    </div>
                </div>

                <!-- Live .srt Subtitle Finder + Audio/Subtitle Sync -->
                <div class="sub-finder-box" id="subFinderBox">
                    <div class="sub-finder-head">
                        <span style="font-size:0.8rem;font-weight:800;color:#bae6fd;">Live .srt Subtitle Finder &amp; Sync</span>
                        <span id="activeSubBadge" style="font-size:0.72rem;color:var(--text-muted);">Shortcuts: Audio J/K • Sub G/H</span>
                    </div>
                    <div class="sub-finder-row">
                        <input type="text" id="subQueryInput" class="input-control" placeholder="Movie / Episode title (to search .srt subtitles)...">
                        <button type="button" id="btnFindSubs" class="btn-sm btn-indigo-sm" onclick="findOnlineSubtitles()">Find Online .srt</button>
                        <label class="btn-sm btn-copy-sm" style="cursor:pointer;margin:0;">
                            Local .srt
                            <input type="file" id="localSrtInput" accept=".srt,.vtt" style="display:none" onchange="handleLocalSrtFile(this)">
                        </label>
                    </div>
                    <div id="subResultsList" class="sub-results-list" style="display:none;"></div>
                    <div class="sync-strip">
                        <span>Audio Sync:</span>
                        <button type="button" class="btn-sm btn-copy-sm" onclick="sendVlcCmd('audiodelay','-50')">-50ms (J)</button>
                        <button type="button" class="btn-sm btn-copy-sm" onclick="sendVlcCmd('audiodelay','+50')">+50ms (K)</button>
                        <span style="margin-left:6px;">Sub Sync:</span>
                        <button type="button" class="btn-sm btn-copy-sm" onclick="sendVlcCmd('subdelay','-50')">-50ms (G)</button>
                        <button type="button" class="btn-sm btn-copy-sm" onclick="sendVlcCmd('subdelay','+50')">+50ms (H)</button>
                    </div>
                </div>

                <!-- Detected Mirrors -->
                <div class="mirrors-list">
                    <h3 style="font-size:0.82rem;color:var(--text-muted);margin-bottom:10px;text-transform:uppercase;letter-spacing:0.6px;font-weight:800;">Detected High-Speed CDN Mirrors</h3>
                    <div id="mirrorsContainer"></div>
                </div>
            </div>

            <!-- Per-Movie / Per-Episode Phone VLC QR Sidebar -->
            <aside class="movie-qr-box" id="movieQrCard" style="display:none;">
                <div class="qr-frame-lg">
                    <img id="movieQrImg" src="" alt="Scan QR to Play in Phone VLC">
                </div>
                <div>
                    <div class="movie-qr-heading">Send to Phone VLC</div>
                    <div class="movie-qr-sub" id="movieQrSub"></div>
                </div>
                <div class="movie-qr-actions">
                    <a id="btnMoviePhoneOpen" class="btn-sm btn-vlc-sm" target="_blank" href="#">Open Phone Page</a>
                    <button class="btn-sm btn-copy-sm" onclick="copyPhoneLink()">Copy Link</button>
                </div>
            </aside>
        </div>
    </section>

    <!-- Netflix / Prime / JioHotstar Horizontal Poster Rails -->
    <section class="ott-rails-wrap" id="ottRailsWrap" aria-label="Curated OTT Rows"></section>

    <!-- All Movies & Web Series Catalog Grid -->
    <section class="glass-card" id="browseSection">
        <div class="catalog-head">
            <div class="catalog-title-wrap">
                <h2 id="browseHeading">All Movies &amp; Web Series</h2>
                <div class="status-sub" id="searchStatus">Loading latest movies &amp; series from HDHub4u...</div>
            </div>
            <div class="filter-pills" role="tablist" aria-label="Filter catalog">
                <button class="filter-btn active" id="filterAll" onclick="setCatalogFilter('all')">All</button>
                <button class="filter-btn" id="filterMovies" onclick="setCatalogFilter('movies')">Movies</button>
                <button class="filter-btn" id="filterSeries" onclick="setCatalogFilter('series')">Web Series</button>
                <button class="filter-btn" id="btnRefreshFeed" onclick="refreshHomeFeed()" title="Load Latest Releases" style="display:inline-flex;align-items:center;gap:5px;">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                    Refresh
                </button>
            </div>
        </div>
        <div class="cat-strip" id="categoryStrip" role="tablist" aria-label="Browse Categories">
            <button class="cat-chip active" data-cat="" onclick="selectCategory(this, '')">Trending</button>
            <button class="cat-chip" data-cat="hollywood-movies" onclick="selectCategory(this, 'hollywood-movies')">Hollywood</button>
            <button class="cat-chip" data-cat="bollywood-movies" onclick="selectCategory(this, 'bollywood-movies')">Bollywood</button>
            <button class="cat-chip" data-cat="hindi-dubbed" onclick="selectCategory(this, 'hindi-dubbed')">Hindi Dubbed</button>
            <button class="cat-chip" data-cat="south-hindi-movies" onclick="selectCategory(this, 'south-hindi-movies')">South Hindi</button>
            <button class="cat-chip" data-cat="web-series" onclick="selectCategory(this, 'web-series')">Web Series</button>
            <button class="cat-chip" data-cat="dual-audio" onclick="selectCategory(this, 'dual-audio')">Dual Audio</button>
            <button class="cat-chip" data-cat="animated-movies" onclick="selectCategory(this, 'animated-movies')">Animated</button>
            <button class="cat-chip" data-cat="netflix" onclick="selectCategory(this, 'netflix')">Netflix</button>
            <button class="cat-chip" data-cat="amazon-prime-video" onclick="selectCategory(this, 'amazon-prime-video')">Prime Video</button>
            <button class="cat-chip" data-cat="jiohotstar" onclick="selectCategory(this, 'jiohotstar')">JioHotstar</button>
            <button class="cat-chip" data-cat="action-movies" onclick="selectCategory(this, 'action-movies')">Action</button>
        </div>
        <div class="smart-strip" id="smartFilterStrip" aria-label="Smart filters">
            <button class="smart-pill" data-sf="4k" onclick="toggleSmartFilter('4k', this)">4K UHD</button>
            <button class="smart-pill" data-sf="hindi" onclick="toggleSmartFilter('hindi', this)">Hindi / Dual Audio</button>
            <button class="smart-pill" data-sf="recent" onclick="toggleSmartFilter('recent', this)">2025–2026</button>
            <button class="smart-pill" data-sf="imdb7" onclick="toggleSmartFilter('imdb7', this)">IMDb 7+</button>
            <button class="smart-pill" id="btnSmartClear" onclick="clearSmartFilters()" style="display:none;">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                Clear
            </button>
            <button class="smart-pill" id="btnHomeDownloadsPill" onclick="openDownloadsModal()" style="display:inline-flex;align-items:center;gap:5px;">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                Downloads <span id="homeDlCountPill">(0)</span>
            </button>
            <button class="smart-pill" id="btnHomeHub4kPill" onclick="openHub4kModal()" style="display:inline-flex;align-items:center;gap:5px;">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M7 4v16"/><path d="M17 4v16"/><path d="M2 12h20"/></svg>
                4K Cinema Vault
            </button>
            <button class="smart-pill" id="btnAppVersionChip" onclick="openSettingsModal()" style="margin-left:auto;" title="Settings, Profile, Themes &amp; Updates">v2.5.5 &bull; Settings</button>
        </div>
        <div class="search-grid" id="searchGrid"></div>
        <div class="load-more-wrap">
            <button class="btn-primary" id="btnLoadMore" onclick="loadMoreCatalog()" style="display:none;">Load More Movies (Page 2)</button>
        </div>
    </section>
</main>

<!-- YouTube Trailer Modal -->
<div class="modal-overlay" id="trailerModalOverlay" onclick="if(event.target===this) closeTrailerModal()" role="dialog" aria-modal="true" aria-labelledby="trailerModalTitle">
    <div class="modal-dialog" style="max-width:740px;text-align:left;">
        <div style="display:flex;align-items:center;justify-content:space-between;">
            <h2 id="trailerModalTitle" style="font-size:1.05rem;font-weight:800;color:#fff;">Official Trailer</h2>
            <button class="btn-close-card" onclick="closeTrailerModal()" aria-label="Close trailer">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
        </div>
        <div class="trailer-frame-wrap">
            <iframe id="trailerIframe" src="" allowfullscreen allow="autoplay; encrypted-media"></iframe>
        </div>
    </div>
</div>

<!-- PC VLC Remote Control Modal (Control PC playback from Phone) -->
<div class="modal-overlay" id="remoteModalOverlay" onclick="if(event.target===this) closeRemoteModal()" role="dialog" aria-modal="true" aria-labelledby="remoteModalTitle">
    <div class="modal-dialog" style="max-width:460px;text-align:left;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">
            <div>
                <h2 id="remoteModalTitle" style="font-size:1.05rem;font-weight:800;color:#fdba74;">PC VLC Remote Control</h2>
                <div id="remoteStatusChip" style="font-size:0.76rem;color:var(--text-muted);margin-top:2px;">Connecting to PC VLC...</div>
            </div>
            <button class="btn-close-card" onclick="closeRemoteModal()" aria-label="Close remote">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
        </div>

        <div id="remoteNowPlaying" style="font-size:0.88rem;font-weight:700;color:var(--text);background:#070910;border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin-bottom:14px;word-break:break-word;">
            Start playback in VLC on your PC to see live controls here.
        </div>

        <div>
            <input type="range" id="remoteSeekBar" class="range-slider" min="0" max="100" value="0" onchange="remoteSeekTo(this.value)">
            <div style="display:flex;justify-content:space-between;font-size:0.75rem;color:var(--text-muted);margin-top:4px;font-family:ui-monospace,Consolas,monospace;">
                <span id="remoteTimeCur">00:00:00</span>
                <span id="remoteTimeDur">00:00:00</span>
            </div>
        </div>

        <div class="remote-grid">
            <button class="remote-btn" onclick="sendVlcCmd('seek','-60')">-60s</button>
            <button class="remote-btn" onclick="sendVlcCmd('seek','-10')">-10s</button>
            <button class="remote-btn remote-btn-play" id="btnRemotePlayPause" onclick="sendVlcCmd('pl_pause')">Play/Pause</button>
            <button class="remote-btn" onclick="sendVlcCmd('seek','+10')">+10s</button>
            <button class="remote-btn" onclick="sendVlcCmd('seek','+60')">+60s</button>
        </div>

        <div style="display:flex;align-items:center;gap:10px;margin-top:14px;">
            <span style="font-size:0.78rem;color:var(--text-muted);min-width:56px;">Vol: <b id="remoteVolText">100%</b></span>
            <input type="range" id="remoteVolBar" class="range-slider" min="0" max="150" value="100" onchange="remoteSetVolume(this.value)">
        </div>

        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;">
            <button class="btn-sm btn-copy-sm" style="flex:1;" onclick="sendVlcCmd('fullscreen')">Fullscreen</button>
            <button class="btn-sm btn-copy-sm" onclick="sendVlcCmd('rate','1.0')">1x</button>
            <button class="btn-sm btn-copy-sm" onclick="sendVlcCmd('rate','1.25')">1.25x</button>
            <button class="btn-sm btn-copy-sm" onclick="sendVlcCmd('rate','1.5')">1.5x</button>
            <button class="btn-sm btn-accent-sm" onclick="sendVlcCmd('pl_stop')">Stop</button>
        </div>

        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;">
            <button class="btn-sm btn-copy-sm" onclick="sendVlcCmd('skip_intro')">Skip +85s</button>
            <button class="btn-sm btn-copy-sm" id="btnRemoteNight" onclick="sendVlcCmd('night_boost')">Night EQ</button>
            <button class="btn-sm btn-copy-sm" id="btnRemoteTop" onclick="sendVlcCmd('topmost')">Mini Top</button>
            <button class="btn-sm btn-copy-sm" onclick="sendVlcCmd('snapshot')">Snapshot</button>
            <button class="btn-sm btn-copy-sm" onclick="sendVlcCmd('crop_cycle')">Crop (C)</button>
            <button class="btn-sm btn-copy-sm" onclick="sendVlcCmd('aspect_cycle')">Ratio (A)</button>
        </div>

        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:8px;font-size:0.74rem;color:var(--text-muted);">
            <span>Aud: <b id="remoteAudDelay">0ms</b></span>
            <button class="btn-sm btn-copy-sm" onclick="sendVlcCmd('audiodelay','-50')">-50ms</button>
            <button class="btn-sm btn-copy-sm" onclick="sendVlcCmd('audiodelay','+50')">+50ms</button>
            <span style="margin-left:4px;">Sub: <b id="remoteSubDelay">0ms</b></span>
            <button class="btn-sm btn-copy-sm" onclick="sendVlcCmd('subdelay','-50')">-50ms</button>
            <button class="btn-sm btn-copy-sm" onclick="sendVlcCmd('subdelay','+50')">+50ms</button>
        </div>

        <div class="track-grid" style="margin-top:14px;">
            <div>
                <label style="display:block;font-size:0.75rem;color:var(--text-muted);margin-bottom:4px;">Live Audio Stream:</label>
                <select id="remoteAudioSelect" class="select-control" onchange="if(this.value) sendVlcCmd('audio_track', this.value)"></select>
            </div>
            <div>
                <label style="display:block;font-size:0.75rem;color:var(--text-muted);margin-bottom:4px;">Live Subtitle Stream:</label>
                <select id="remoteSubSelect" class="select-control" onchange="if(this.value) sendVlcCmd('subtitle_track', this.value)"></select>
            </div>
        </div>
    </div>
</div>

<!-- Frosted Connect-Phone QR Modal -->
<div class="modal-overlay" id="qrModalOverlay" onclick="if(event.target===this) closeQrModal()" role="dialog" aria-modal="true" aria-labelledby="qrModalTitle">
    <div class="modal-dialog">
        <div style="display:flex;align-items:center;justify-content:space-between;">
            <h2 id="qrModalTitle" style="font-size:1.08rem;font-weight:800;color:#bae6fd;">Connect Phone via Wi-Fi QR</h2>
            <button class="btn-close-card" onclick="closeQrModal()" aria-label="Close modal">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
        </div>
        <div class="modal-qr-frame">
            <img id="modalQrImg" src="/api/qr.svg" alt="Phone Wi-Fi QR Code">
        </div>
        <div class="qr-url-pill" id="modalLanUrl" style="font-size:0.84rem;padding:6px 12px;">http://...</div>
        <div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:8px;">
            <button class="btn-sm btn-vlc-sm" onclick="copyLanUrl()">Copy Phone URL</button>
            <button class="btn-sm btn-indigo-sm" onclick="triggerInstallApp()">Install Phone App</button>
            <button class="btn-sm btn-copy-sm" onclick="loadLanQr(true)">Refresh IP</button>
        </div>
        <div class="modal-steps">
            1. Keep PC &amp; Phone on the <b>same Wi-Fi or Hotspot</b>.<br>
            2. Scan QR code with your phone camera to open <b>StreamHub</b>.<br>
            3. Tap browser menu &rarr; <b>Add to Home Screen</b> to install with the crimson <b>S</b> icon!
        </div>
    </div>
</div>

<!-- Inline Browser Cinema Player Modal (HLS + Web Streams) -->
<div class="modal-overlay" id="inlineWebPlayerModal" onclick="if(event.target===this) closeInlinePlayer()" role="dialog" aria-modal="true" aria-labelledby="inlinePlayerTitle">
    <div class="modal-dialog" style="max-width:920px;width:96%;text-align:left;padding:16px;">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px;">
            <div style="min-width:0;">
                <h2 id="inlinePlayerTitle" style="font-size:1rem;font-weight:800;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">StreamHub Inline Player</h2>
                <div id="inlinePlayerNote" style="font-size:0.74rem;color:var(--text-muted);">HLS instant browser playback (use PC In-Built Player for MKV DDP5.1 audio)</div>
            </div>
            <button class="btn-close-card" onclick="closeInlinePlayer()" aria-label="Close inline player">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
        </div>
        <div class="trailer-frame-wrap" style="margin-top:0;">
            <video id="inlineVideoEl" controls autoplay playsinline crossorigin="anonymous" style="width:100%;height:100%;outline:none;">
                <track id="inlineSubTrack" kind="subtitles" srclang="en" label="StreamHub Sub" default>
            </video>
            <div class="inline-next-banner" id="inlineNextBanner">
                <span id="inlineNextText" style="font-size:0.8rem;font-weight:700;color:#fde68a;">Next Episode in 5s</span>
                <button type="button" class="btn-sm btn-vlc-sm" onclick="triggerInlineNextNow()">Play Now</button>
                <button type="button" class="btn-sm btn-copy-sm" onclick="cancelInlineNext()">Cancel</button>
            </div>
        </div>
        <div class="inline-controls-bar" id="inlineControlsBar">
            <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
                <button type="button" class="btn-sm btn-indigo-sm" onclick="skipInlineIntro(85)">Skip Intro (+85s • S)</button>
                <button type="button" class="btn-sm btn-vlc-sm" id="btnInlineNextEp" onclick="triggerInlineNextNow()" style="display:none;">Next Episode &rsaquo;</button>
            </div>
            <div style="display:flex;gap:8px;align-items:center;font-size:0.75rem;color:var(--text-muted);">
                <span id="inlineSubStatus">Subtitles: None</span>
            </div>
        </div>
    </div>
</div>

<!-- Main Settings Menu Hub Page (Tapping any item opens its own dedicated page) -->
<div class="modal-overlay fullscreen-mobile sh-full-page" id="settingsModalOverlay" onclick="if(event.target===this) closeSettingsModal()" role="dialog" aria-modal="true" aria-labelledby="settingsModalTitle">
    <div class="settings-dialog">
        <div class="settings-head">
            <div style="display:flex;align-items:center;gap:12px;">
                <span id="settingsHeadAvatar" style="width:40px;height:40px;border-radius:10px;background:var(--surface-2);border:1px solid var(--border-strong);color:#fff;display:grid;place-items:center;">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="2" width="20" height="20" rx="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/></svg>
                </span>
                <div>
                    <h2 id="settingsModalTitle" style="font-size:1.05rem;font-weight:800;color:#fff;">Settings</h2>
                    <div id="settingsHeadSub" style="font-size:0.73rem;color:var(--text-muted);margin-top:1px;">StreamHub User • v2.5.4</div>
                </div>
            </div>
            <button class="btn-close-card" onclick="closeSettingsModal()" aria-label="Close settings">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
        </div>
        <div class="settings-body">
            <button type="button" class="settings-menu-item highlight" onclick="closeSettingsModal(); openVipUnlockModal();">
                <div class="settings-menu-left">
                    <span class="settings-menu-ico" style="color:#facc15;">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>
                    </span>
                    <div style="min-width:0;flex:1;">
                        <div class="settings-menu-title">VIP Access Key</div>
                        <div class="settings-menu-sub" id="menuSubVip">Locked • Tap to Enter Key</div>
                    </div>
                </div>
                <span class="settings-menu-chev"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></span>
            </button>

            <button type="button" class="settings-menu-item" onclick="openSettingsSubPage('profilePageModal')">
                <div class="settings-menu-left">
                    <span class="settings-menu-ico">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                    </span>
                    <div style="min-width:0;flex:1;">
                        <div class="settings-menu-title">Profile &amp; Audio Language</div>
                        <div class="settings-menu-sub" id="menuSubProfile">StreamHub User • Audio: Auto</div>
                    </div>
                </div>
                <span class="settings-menu-chev"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></span>
            </button>

            <button type="button" class="settings-menu-item" onclick="openSettingsSubPage('themePageModal')">
                <div class="settings-menu-left">
                    <span class="settings-menu-ico">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 3v18"/><path d="M12 8l6-3"/><path d="M12 13l7-3"/></svg>
                    </span>
                    <div style="min-width:0;flex:1;">
                        <div class="settings-menu-title">App Appearance</div>
                        <div class="settings-menu-sub" id="menuSubTheme">Netflix Crimson</div>
                    </div>
                </div>
                <span class="settings-menu-chev"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></span>
            </button>

            <button type="button" class="settings-menu-item" onclick="openSettingsSubPage('dlSettingsPageModal')">
                <div class="settings-menu-left">
                    <span class="settings-menu-ico">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>
                    </span>
                    <div style="min-width:0;flex:1;">
                        <div class="settings-menu-title">Download &amp; Storage</div>
                        <div class="settings-menu-sub" id="menuSubDlPrefs">Used: 0 MB / 4 GB • 720p • Expire 7d</div>
                    </div>
                </div>
                <span class="settings-menu-chev"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></span>
            </button>

            <button type="button" class="settings-menu-item" onclick="openDownloadsModal(true)">
                <div class="settings-menu-left">
                    <span class="settings-menu-ico">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                    </span>
                    <div style="min-width:0;flex:1;">
                        <div class="settings-menu-title">My Downloads</div>
                        <div class="settings-menu-sub" id="menuSubMyDownloads">0 Ready • 0 Active</div>
                    </div>
                </div>
                <span class="settings-menu-chev"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></span>
            </button>

            <button type="button" class="settings-menu-item highlight" onclick="openUpdateModal(true)">
                <div class="settings-menu-left">
                    <span class="settings-menu-ico">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
                    </span>
                    <div style="min-width:0;flex:1;">
                        <div class="settings-menu-title" style="display:flex;align-items:center;gap:8px;">
                            <span>App Updates</span>
                            <span id="settingsUpdateBadge" style="font-size:0.68rem;padding:2px 7px;border-radius:6px;background:var(--surface-2);border:1px solid var(--border);color:var(--text-muted);font-weight:700;">v2.5.4</span>
                        </div>
                        <div class="settings-menu-sub" id="settingsUpdateSub">Check latest APK / OTA updates</div>
                    </div>
                </div>
                <span class="settings-menu-chev"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></span>
            </button>

            <button type="button" class="settings-menu-item" onclick="openSettingsSubPage('toolsPageModal')">
                <div class="settings-menu-left">
                    <span class="settings-menu-ico">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>
                    </span>
                    <div style="min-width:0;flex:1;">
                        <div class="settings-menu-title">Catalog &amp; Tools</div>
                        <div class="settings-menu-sub">Refresh feed cache &amp; device utilities</div>
                    </div>
                </div>
                <span class="settings-menu-chev"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></span>
            </button>
        </div>
    </div>
</div>

<!-- Sub-Page 1: Dedicated Profile & Language Page -->
<div class="modal-overlay fullscreen-mobile sh-full-page" id="profilePageModal" onclick="if(event.target===this) closeSubPage('profilePageModal')" role="dialog" aria-modal="true" aria-labelledby="profilePageTitle">
    <div class="settings-dialog">
        <div class="settings-head">
            <div style="display:flex;align-items:center;gap:10px;">
                <button type="button" class="btn-sm btn-copy-sm" onclick="closeSubPage('profilePageModal')">Back</button>
                <h2 id="profilePageTitle" style="font-size:1rem;font-weight:700;color:#fff;">Profile &amp; Language</h2>
            </div>
            <button class="btn-close-card" onclick="closeAllSettingsPages()" aria-label="Close">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
        </div>
        <div class="settings-body">
            <section class="settings-card">
                <div class="settings-card-title"><span>Profile Icon</span></div>
                <div class="avatar-grid" id="avatarPickerGrid">
                    <button type="button" class="avatar-chip active" data-av="film" onclick="pickProfileAvatar(this)" aria-label="Cinema Icon"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="20" rx="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/></svg></button>
                    <button type="button" class="avatar-chip" data-av="tv" onclick="pickProfileAvatar(this)" aria-label="TV Icon"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="20" height="15" rx="2"/><polyline points="17 2 12 7 7 2"/></svg></button>
                    <button type="button" class="avatar-chip" data-av="star" onclick="pickProfileAvatar(this)" aria-label="Star Icon"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg></button>
                    <button type="button" class="avatar-chip" data-av="compass" onclick="pickProfileAvatar(this)" aria-label="Discover Icon"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"/></svg></button>
                    <button type="button" class="avatar-chip" data-av="shield" onclick="pickProfileAvatar(this)" aria-label="Shield Icon"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg></button>
                    <button type="button" class="avatar-chip" data-av="user" onclick="pickProfileAvatar(this)" aria-label="User Icon"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg></button>
                </div>
                <div class="settings-row">
                    <div style="flex:1 1 180px;">
                        <div class="settings-label">Profile Name</div>
                        <div class="settings-sub">Your name and watch profile</div>
                    </div>
                    <input type="text" id="setProfileName" class="select-control" style="max-width:220px;padding:8px 12px;" placeholder="Your Name" maxlength="32">
                </div>
                <div class="settings-row">
                    <div style="flex:1 1 180px;">
                        <div class="settings-label">Preferred Audio Language</div>
                        <div class="settings-sub">Prioritized first during stream resolution and downloads</div>
                    </div>
                    <select id="setProfileLang" class="select-control" style="max-width:190px;padding:8px 12px;">
                        <option value="Auto">Auto (Default)</option>
                        <option value="Hindi">Hindi</option>
                        <option value="English">English</option>
                        <option value="Dual Audio">Dual Audio (Hindi+Eng)</option>
                        <option value="Tamil">Tamil</option>
                        <option value="Telugu">Telugu</option>
                        <option value="Punjabi">Punjabi</option>
                        <option value="Bengali">Bengali</option>
                        <option value="Malayalam">Malayalam</option>
                        <option value="Kannada">Kannada</option>
                    </select>
                </div>
                <button type="button" class="vlc-banner" style="min-height:44px;font-size:0.9rem;justify-content:center;margin-top:12px;" onclick="saveProfileSettings()">Save Profile</button>
            </section>
        </div>
    </div>
</div>

<!-- Sub-Page 2: Dedicated App Theme Page -->
<div class="modal-overlay fullscreen-mobile sh-full-page" id="themePageModal" onclick="if(event.target===this) closeSubPage('themePageModal')" role="dialog" aria-modal="true" aria-labelledby="themePageTitle">
    <div class="settings-dialog">
        <div class="settings-head">
            <div style="display:flex;align-items:center;gap:10px;">
                <button type="button" class="btn-sm btn-copy-sm" onclick="closeSubPage('themePageModal')">Back</button>
                <h2 id="themePageTitle" style="font-size:1rem;font-weight:700;color:#fff;">App Appearance</h2>
            </div>
            <button class="btn-close-card" onclick="closeAllSettingsPages()" aria-label="Close">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
        </div>
        <div class="settings-body">
            <section class="settings-card">
                <div class="settings-card-title">
                    <span>Select App Theme</span>
                    <span id="activeThemeBadge" style="font-size:0.74rem;color:var(--text-muted);font-weight:600;">Netflix Crimson</span>
                </div>
                <div class="theme-grid" id="themePickerGrid">
                    <button type="button" class="theme-card active" data-theme-id="netflix" onclick="applyAndSaveTheme('netflix')">
                        <div class="theme-dots"><span class="theme-dot" style="background:#09090b;"></span><span class="theme-dot" style="background:#e50914;"></span></div>
                        <div class="theme-name">Netflix Crimson</div>
                        <div class="theme-desc">Matte obsidian with red accent</div>
                    </button>
                    <button type="button" class="theme-card" data-theme-id="amoled" onclick="applyAndSaveTheme('amoled')">
                        <div class="theme-dots"><span class="theme-dot" style="background:#000000;"></span><span class="theme-dot" style="background:#e50914;"></span></div>
                        <div class="theme-name">AMOLED Pure Black</div>
                        <div class="theme-desc">Pitch black OLED battery saver</div>
                    </button>
                    <button type="button" class="theme-card" data-theme-id="prime" onclick="applyAndSaveTheme('prime')">
                        <div class="theme-dots"><span class="theme-dot" style="background:#070b12;"></span><span class="theme-dot" style="background:#00a8e1;"></span></div>
                        <div class="theme-name">Prime Sapphire</div>
                        <div class="theme-desc">Deep slate with cyan accent</div>
                    </button>
                    <button type="button" class="theme-card" data-theme-id="gold" onclick="applyAndSaveTheme('gold')">
                        <div class="theme-dots"><span class="theme-dot" style="background:#0a090b;"></span><span class="theme-dot" style="background:#eab308;"></span></div>
                        <div class="theme-name">Theater Gold</div>
                        <div class="theme-desc">Warm obsidian with brass accent</div>
                    </button>
                </div>
            </section>
        </div>
    </div>
</div>

<!-- Sub-Page 3: Dedicated Download & Storage Settings Page -->
<div class="modal-overlay fullscreen-mobile sh-full-page" id="dlSettingsPageModal" onclick="if(event.target===this) closeSubPage('dlSettingsPageModal')" role="dialog" aria-modal="true" aria-labelledby="dlSettingsPageTitle">
    <div class="settings-dialog">
        <div class="settings-head">
            <div style="display:flex;align-items:center;gap:10px;">
                <button type="button" class="btn-sm btn-copy-sm" onclick="closeSubPage('dlSettingsPageModal')">Back</button>
                <h2 id="dlSettingsPageTitle" style="font-size:1rem;font-weight:700;color:#fff;">Download &amp; Storage</h2>
            </div>
            <button class="btn-close-card" onclick="closeAllSettingsPages()" aria-label="Close">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
        </div>
        <div class="settings-body">
            <section class="settings-card">
                <div class="settings-card-title">
                    <span>Private App Storage</span>
                    <span style="display:inline-flex;align-items:center;gap:4px;font-size:0.69rem;color:#86efac;background:rgba(34,197,94,0.12);border:1px solid rgba(34,197,94,0.28);padding:2px 8px;border-radius:6px;">
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
                        Gallery Hidden
                    </span>
                </div>
                <div style="font-size:0.75rem;color:var(--text-muted);line-height:1.45;">
                    Movies are saved in the app's private folder (<code style="color:var(--text);">filesDir/offline_movies</code>) — hidden from Gallery and File Manager, and automatically removed if the app is uninstalled. Offline downloads require a 3 Months+ VIP Plan.
                </div>
                <div class="storage-bar-track"><div class="storage-bar-fill" id="setStorageBarFill"></div></div>
                <div style="display:flex;justify-content:space-between;font-size:0.74rem;color:var(--text);font-weight:600;margin-bottom:10px;">
                    <span id="setStorageText">Used: 0 MB / Limit: 4 GB</span>
                    <span id="setDownloadCountText">0 Offline Items</span>
                </div>
                <div class="settings-row">
                    <div>
                        <div class="settings-label">Storage Cap Limit</div>
                        <div class="settings-sub">Watched movies are cleaned up first when the limit is reached</div>
                    </div>
                    <select id="setStorageCap" class="select-control" style="max-width:165px;padding:8px 10px;" onchange="saveDownloadPrefs()">
                        <option value="2">2 GB (Low Space)</option>
                        <option value="4">4 GB (Balanced)</option>
                        <option value="8">8 GB</option>
                        <option value="16">16 GB</option>
                        <option value="0">Unlimited</option>
                    </select>
                </div>
                <div class="settings-row">
                    <div>
                        <div class="settings-label">Smart Auto-Delete (Watched)</div>
                        <div class="settings-sub">Automatically delete after watching 95%+ to save space</div>
                    </div>
                    <label style="display:inline-flex;align-items:center;gap:8px;cursor:pointer;font-size:0.8rem;font-weight:600;">
                        <input type="checkbox" id="setAutoDelWatched" checked onchange="saveDownloadPrefs()" style="width:18px;height:18px;accent-color:var(--accent);">
                        <span>On</span>
                    </label>
                </div>
                <div class="settings-row">
                    <div>
                        <div class="settings-label">Auto-Expire Days</div>
                        <div class="settings-sub">Days before older offline files are automatically cleaned</div>
                    </div>
                    <select id="setExpireDays" class="select-control" style="max-width:150px;padding:8px 10px;" onchange="saveDownloadPrefs()">
                        <option value="7">7 Days</option>
                        <option value="14">14 Days</option>
                        <option value="30">30 Days</option>
                        <option value="0">Never Expire</option>
                    </select>
                </div>
                <div class="settings-row">
                    <div>
                        <div class="settings-label">Streaming Quality Mode</div>
                        <div class="settings-sub">Auto starts fast at 480p and adapts up based on connection speed</div>
                    </div>
                    <select id="setDefaultStreamQuality" class="select-control" style="max-width:175px;padding:8px 10px;" onchange="saveDownloadPrefs()">
                        <option value="auto">Auto (480p -> Speed)</option>
                        <option value="480p">480p Data Saver</option>
                        <option value="720p">720p HD</option>
                        <option value="1080p">1080p Full HD</option>
                        <option value="4k">4K UHD (High Speed)</option>
                    </select>
                </div>
                <div class="settings-row">
                    <div>
                        <div class="settings-label">Default Download Quality</div>
                        <div class="settings-sub">480p or 720p is recommended for smaller file sizes</div>
                    </div>
                    <select id="setDefaultDlQuality" class="select-control" style="max-width:165px;padding:8px 10px;" onchange="saveDownloadPrefs()">
                        <option value="480p">480p (Low Size)</option>
                        <option value="720p">720p HD (Rec.)</option>
                        <option value="1080p">1080p Full HD</option>
                        <option value="ask">Ask Every Time</option>
                    </select>
                </div>
                <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;">
                    <button type="button" class="btn-sm btn-indigo-sm" style="flex:1 1 160px;min-height:40px;" onclick="closeSubPage('dlSettingsPageModal'); openDownloadsModal(true);">Open My Downloads</button>
                    <button type="button" class="btn-sm btn-copy-sm" style="min-height:40px;" onclick="clearAllOfflineDownloads()">Clear Storage</button>
                </div>
            </section>
        </div>
    </div>
</div>

<!-- Sub-Page 4: Dedicated Refresh Movies & Tools Page -->
<div class="modal-overlay fullscreen-mobile sh-full-page" id="toolsPageModal" onclick="if(event.target===this) closeSubPage('toolsPageModal')" role="dialog" aria-modal="true" aria-labelledby="toolsPageTitle">
    <div class="settings-dialog">
        <div class="settings-head">
            <div style="display:flex;align-items:center;gap:10px;">
                <button type="button" class="btn-sm btn-copy-sm" onclick="closeSubPage('toolsPageModal')">Back</button>
                <h2 id="toolsPageTitle" style="font-size:1rem;font-weight:700;color:#fff;">Catalog &amp; Tools</h2>
            </div>
            <button class="btn-close-card" onclick="closeAllSettingsPages()" aria-label="Close">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
        </div>
        <div class="settings-body">
            <section class="settings-card">
                <div class="settings-card-title"><span>Catalog &amp; Cache</span></div>
                <div style="font-size:0.78rem;color:var(--text-muted);margin-bottom:12px;line-height:1.45;">
                    Press Refresh to load the newest movies and series added to the catalog immediately.
                </div>
                <button type="button" class="vlc-banner" style="min-height:44px;font-size:0.9rem;justify-content:center;" onclick="closeAllSettingsPages(); clearCacheAndRefresh();">Refresh Latest Movies</button>
                <div id="pcSetupToolsCard" style="margin-top:14px;padding-top:14px;border-top:1px solid var(--border);">
                    <div style="font-size:0.78rem;font-weight:700;color:var(--gold);margin-bottom:8px;">PC Setup Utilities</div>
                    <div style="display:flex;gap:8px;flex-wrap:wrap;">
                        <button type="button" class="btn-sm btn-copy-sm" style="flex:1 1 140px;" onclick="runSetupAction('shortcut')">Add Desktop Shortcut</button>
                        <button type="button" class="btn-sm btn-copy-sm" style="flex:1 1 140px;" onclick="runSetupAction('firewall')">Fix Phone Wi-Fi Firewall</button>
                        <button type="button" class="btn-sm btn-copy-sm" style="flex:1 1 120px;" onclick="closeAllSettingsPages(); openRemoteModal();">PC Remote</button>
                        <button type="button" class="btn-sm btn-copy-sm" style="flex:1 1 120px;" onclick="closeAllSettingsPages(); openQrModal();">Phone QR</button>
                    </div>
                </div>
            </section>
            <section class="settings-card">
                <div class="settings-card-title"><span>Custom Stream Link (Advanced)</span></div>
                <div style="font-size:0.78rem;color:var(--text-muted);margin-bottom:10px;line-height:1.45;">
                    Paste a direct HubCloud, HDHub4u, or .m3u8 URL here to play it directly in the player.
                </div>
                <input type="text" id="toolsCustomUrlInput" class="select-control" placeholder="Paste HubCloud, HDHub4u, or .m3u8 link..." style="width:100%;margin-bottom:10px;">
                <button type="button" class="vlc-banner" style="min-height:44px;font-size:0.9rem;justify-content:center;" onclick="playToolsCustomLink()">Play Custom Stream</button>
            </section>
        </div>
    </div>
</div>

<!-- Sub-Page 5: App Updates Dedicated Page -->
<div class="modal-overlay fullscreen-mobile sh-full-page" id="updateModalOverlay" onclick="if(event.target===this) closeUpdateModal()" role="dialog" aria-modal="true" aria-labelledby="updateModalTitle">
    <div class="settings-dialog">
        <div class="settings-head">
            <div style="display:flex;align-items:center;gap:10px;min-width:0;">
                <button type="button" id="btnUpdateBackToSettings" class="btn-sm btn-copy-sm" onclick="backFromUpdateToSettings()" title="Back to Settings">Back</button>
                <div style="min-width:0;">
                    <h2 id="updateModalTitle" style="font-size:1rem;font-weight:700;color:#fff;">App Updates</h2>
                    <div id="updateMetaLine" style="font-size:0.73rem;color:var(--text-muted);margin-top:2px;">Version loading...</div>
                </div>
            </div>
            <button class="btn-close-card" onclick="closeAllSettingsPages()" aria-label="Close update page">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
        </div>

        <div class="settings-body">
            <div class="settings-card">
                <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:8px;">
                    <span style="font-size:0.86rem;font-weight:700;color:#fff;">Release Status</span>
                    <button type="button" id="btnCheckUpdateBig" class="btn-sm btn-indigo-sm" onclick="checkAppUpdate(true, true)">Check for Updates</button>
                </div>
                <div id="updateStatusBox" style="font-size:0.82rem;color:var(--text-muted);line-height:1.45;">Checking for updates...</div>
                <div id="updateDownloadBox" style="display:none;margin-top:10px;padding-top:10px;border-top:1px solid var(--border);">
                    <div id="updateNotesText" style="font-size:0.78rem;color:var(--text-muted);margin-bottom:10px;white-space:pre-line;line-height:1.45;"></div>
                    <div id="updateProgWrap" style="display:none;background:var(--surface-2);border-radius:999px;height:7px;overflow:hidden;margin-bottom:10px;">
                        <div id="updateProgFill" style="height:100%;width:0%;background:var(--netflix);transition:width 0.25s;"></div>
                    </div>
                    <button type="button" id="btnApplyUpdate" class="btn-sm btn-vlc-sm" onclick="applyAppUpdateNow()" style="width:100%;min-height:40px;font-size:0.84rem;display:none;">Download &amp; Install Update</button>
                    <button type="button" id="btnOtaPyUpdate" class="btn-sm btn-vlc-sm" onclick="applyOtaPyUpdateNow()" style="display:none;width:100%;min-height:42px;font-size:0.85rem;margin-bottom:8px;">Install Instant Update (~0.7 MB)</button>
                    <button type="button" id="btnApkUpdate" class="btn-sm btn-vlc-sm" onclick="applyAndroidApkUpdateNow()" style="display:none;width:100%;min-height:42px;font-size:0.85rem;">Download &amp; Install APK</button>
                    <a id="linkApkExternal" href="#" target="_blank" rel="noopener" onclick="if(window.StreamHubAndroid&&window.StreamHubAndroid.openExternalUrl){window.StreamHubAndroid.openExternalUrl(this.href);return false;}" style="display:none;margin-top:8px;text-align:center;font-size:0.76rem;color:var(--text-muted);text-decoration:underline;">Open APK Link in Browser</a>
                </div>
            </div>

            <details class="settings-card">
                <summary style="cursor:pointer;font-size:0.78rem;color:var(--text-muted);font-weight:600;">Advanced: Update Source (GitHub Repo / URL)</summary>
                <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;">
                    <input type="text" id="updateFeedInput" class="input-control" style="flex:1 1 200px;min-width:0;background:var(--bg);border:1px solid var(--border-strong);color:#fff;border-radius:8px;padding:8px 10px;font-size:0.82rem;outline:none;" placeholder="e.g. khushal-jangid/streamhub">
                    <button type="button" id="btnCheckNow" class="btn-sm btn-indigo-sm" onclick="saveUpdateFeedAndCheck()">Save &amp; Check</button>
                </div>
            </details>
        </div>
    </div>
</div>

<!-- Sub-Page 6 / Downloads Dedicated Page -->
<div class="modal-overlay fullscreen-mobile sh-full-page" id="downloadsModalOverlay" onclick="if(event.target===this) closeDownloadsModal()" role="dialog" aria-modal="true" aria-labelledby="downloadsModalTitle">
    <div class="settings-dialog">
        <div class="settings-head">
            <div style="display:flex;align-items:center;gap:10px;min-width:0;flex:1;">
                <button type="button" class="btn-sm btn-copy-sm" onclick="closeDownloadsModal()">Back</button>
                <div style="min-width:0;">
                    <h2 id="downloadsModalTitle" style="font-size:1rem;font-weight:700;color:#fff;">My Downloads</h2>
                    <div style="font-size:0.7rem;color:#86efac;margin-top:1px;">Private App Storage • Offline Playback</div>
                </div>
            </div>
            <div style="display:flex;gap:8px;align-items:center;flex-shrink:0;">
                <button type="button" class="btn-sm btn-copy-sm" onclick="openSettingsSubPage('dlSettingsPageModal');" title="Storage Limit &amp; Auto-Delete Settings" aria-label="Download storage settings">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
                </button>
                <button class="btn-close-card" onclick="closeAllSettingsPages()" aria-label="Close downloads">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
            </div>
        </div>
        <div class="settings-body">
            <div id="downloadsVipBanner" class="settings-card" style="display:none;padding:12px 14px;border-color:rgba(234,179,8,0.45);background:rgba(234,179,8,0.08);">
                <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;">
                    <div style="min-width:0;flex:1 1 200px;">
                        <div style="font-size:0.84rem;font-weight:800;color:#fde047;">Offline Downloads Locked (3 Months+ Required)</div>
                        <div id="downloadsVipBannerSub" style="font-size:0.74rem;color:var(--text-muted);margin-top:2px;line-height:1.4;">15 Days and 1 Month plans include streaming only. Upgrade to 3 Months (&#8377;119), 6 Months (&#8377;199), or 1 Year (&#8377;349) to unlock offline downloads.</div>
                    </div>
                    <button type="button" class="btn-sm btn-vlc-sm" style="min-height:36px;padding:0 12px;" onclick="openVipUnlockModal(false, 'download')">Upgrade 3M+ Plan</button>
                </div>
            </div>
            <div class="settings-card" style="padding:12px 14px;">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;font-size:0.76rem;font-weight:600;color:var(--text);">
                    <span id="dlModalStorageText">Used: 0 MB / 4 GB Limit</span>
                    <span id="dlModalPolicyText" style="color:var(--text-muted);font-size:0.71rem;">Auto-Delete Watched: ON • Expire: 7d</span>
                </div>
                <div class="storage-bar-track"><div class="storage-bar-fill" id="dlModalStorageFill"></div></div>
            </div>
            <div id="downloadsListWrap"></div>
        </div>
    </div>
</div>

<!-- Sub-Page 7: Dedicated 4K Cinema (4KHDHub) Vault Page -->
<div class="modal-overlay fullscreen-mobile sh-full-page" id="hub4kPageModal" onclick="if(event.target===this) closeHub4kModal()" role="dialog" aria-modal="true" aria-labelledby="hub4kPageTitle">
    <div class="settings-dialog" style="max-width:1120px !important;">
        <div class="settings-head">
            <div style="display:flex;align-items:center;gap:10px;min-width:0;flex:1;">
                <button type="button" class="btn-sm btn-copy-sm" onclick="closeHub4kModal()">Back</button>
                <div style="min-width:0;">
                    <h2 id="hub4kPageTitle" style="font-size:1rem;font-weight:700;color:#fff;">4K Cinema</h2>
                    <div id="hub4kStatus" style="font-size:0.72rem;color:var(--text-muted);margin-top:1px;">4K HDR &bull; Remux &bull; Anime &bull; Korean Series</div>
                </div>
            </div>
            <button class="btn-close-card" onclick="closeHub4kModal()" aria-label="Close 4K Cinema">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
        </div>
        <div class="settings-body">
            <div class="settings-card" style="padding:12px;">
                <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                    <input type="text" id="hub4kSearchInput" class="select-control" style="flex:1 1 200px;min-height:40px;padding:8px 12px;" placeholder="Search 4K movies, remux, anime, Korean series..." onkeydown="if(event.key==='Enter') searchHub4k();">
                    <button type="button" class="btn-sm btn-vlc-sm" style="min-height:40px;padding:0 16px;" onclick="searchHub4k()">Search</button>
                    <button type="button" class="btn-sm btn-copy-sm" style="min-height:40px;padding:0 12px;" onclick="resetHub4kBrowse()">Latest</button>
                </div>
                <div class="cat-strip" id="hub4kCategoryStrip" style="margin:11px 0 0;padding-bottom:2px;" role="tablist" aria-label="4K Categories">
                    <button class="cat-chip active" data-cat4k="" onclick="selectHub4kCategory(this, '')">All Latest</button>
                    <button class="cat-chip" data-cat4k="2160p-HDR" onclick="selectHub4kCategory(this, '2160p-HDR')">2160p HDR</button>
                    <button class="cat-chip" data-cat4k="hindi-movies" onclick="selectHub4kCategory(this, 'hindi-movies')">Hindi Movies</button>
                    <button class="cat-chip" data-cat4k="english-movies" onclick="selectHub4kCategory(this, 'english-movies')">English Movies</button>
                    <button class="cat-chip" data-cat4k="hindi-series" onclick="selectHub4kCategory(this, 'hindi-series')">Hindi Series</button>
                    <button class="cat-chip" data-cat4k="english-series" onclick="selectHub4kCategory(this, 'english-series')">English Series</button>
                    <button class="cat-chip" data-cat4k="korean-series" onclick="selectHub4kCategory(this, 'korean-series')">Korean Series</button>
                    <button class="cat-chip" data-cat4k="anime" onclick="selectHub4kCategory(this, 'anime')">Anime</button>
                    <button class="cat-chip" data-cat4k="netflix" onclick="selectHub4kCategory(this, 'netflix')">Netflix</button>
                    <button class="cat-chip" data-cat4k="amazon_prime_video" onclick="selectHub4kCategory(this, 'amazon_prime_video')">Prime Video</button>
                    <button class="cat-chip" data-cat4k="jiohotstar" onclick="selectHub4kCategory(this, 'jiohotstar')">JioHotstar</button>
                </div>
                <div class="filter-pills" style="margin-top:10px;width:fit-content;" role="tablist" aria-label="Filter 4K Cinema">
                    <button class="filter-btn active" id="hub4kFilterAll" onclick="setHub4kFilter('all')">All</button>
                    <button class="filter-btn" id="hub4kFilterMovies" onclick="setHub4kFilter('movies')">Movies</button>
                    <button class="filter-btn" id="hub4kFilterSeries" onclick="setHub4kFilter('series')">Web Series</button>
                </div>
            </div>
            <div class="search-grid" id="hub4kGrid"></div>
            <div class="load-more-wrap">
                <button type="button" class="btn-primary" id="btnLoadMore4k" onclick="loadMoreHub4k()" style="display:none;">Load More 4K Titles</button>
            </div>
        </div>
    </div>
</div>

<!-- Download Quality & Audio Language Picker Modal -->
<div class="modal-overlay" id="downloadPickerModal" onclick="if(event.target===this) closeDownloadPicker()" role="dialog" aria-modal="true" aria-labelledby="dlPickerTitle">
    <div class="modal-dialog" style="max-width:500px;width:96%;text-align:left;padding:18px;">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:12px;">
            <div style="display:flex;gap:11px;align-items:center;min-width:0;">
                <img id="dlPickerPoster" src="" alt="" style="width:46px;height:66px;border-radius:8px;object-fit:cover;background:var(--surface-2);display:none;flex-shrink:0;">
                <div style="min-width:0;">
                    <div style="font-size:0.68rem;font-weight:700;color:var(--accent);text-transform:uppercase;letter-spacing:0.6px;">Offline Download</div>
                    <h2 id="dlPickerTitle" style="font-size:0.96rem;font-weight:700;color:#fff;line-height:1.25;">Movie Title</h2>
                    <div id="dlPickerSub" style="font-size:0.72rem;color:var(--text-muted);margin-top:2px;">Low storage usage + Gallery hidden</div>
                </div>
            </div>
            <button class="btn-close-card" onclick="closeDownloadPicker()" aria-label="Close download picker">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
        </div>

        <div style="margin-bottom:12px;">
            <div style="font-size:0.75rem;font-weight:700;color:var(--text-muted);margin-bottom:7px;">1. Select Video Quality (480p/720p saves space)</div>
            <div id="dlPickerQualities" style="display:flex;flex-direction:column;gap:7px;max-height:210px;overflow-y:auto;"></div>
        </div>

        <div style="margin-bottom:14px;">
            <div style="font-size:0.75rem;font-weight:700;color:var(--text-muted);margin-bottom:7px;">2. Preferred Audio Language</div>
            <div id="dlPickerLangs" style="display:flex;gap:7px;flex-wrap:wrap;"></div>
        </div>

        <div id="dlPickerStorageHint" style="font-size:0.73rem;color:var(--text-muted);background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:9px 11px;margin-bottom:12px;">
            Saves inside private app storage only • Auto-cleans when watched
        </div>

        <button type="button" id="btnStartDownloadConfirm" class="vlc-banner" style="min-height:44px;font-size:0.9rem;justify-content:center;" onclick="confirmStartDownload()">
            Start Private Download
        </button>
    </div>
</div>

<!-- Startup Update Available Popup -->
<div class="modal-overlay fullscreen-mobile sh-full-page" id="startupUpdateModal" onclick="if(event.target===this) closeStartupUpdateModal(true)" role="dialog" aria-modal="true" aria-labelledby="startupUpdTitle">
    <div class="startup-update-card">
        <div class="startup-update-head">
            <div style="display:flex;align-items:center;gap:11px;min-width:0;">
                <span class="startup-update-ico">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                </span>
                <div style="min-width:0;">
                    <div style="font-size:0.68rem;font-weight:700;color:var(--netflix);letter-spacing:0.7px;text-transform:uppercase;">Update Available</div>
                    <h2 id="startupUpdTitle" style="font-size:1rem;font-weight:800;color:#fff;line-height:1.25;">New StreamHub Release</h2>
                </div>
            </div>
            <button class="btn-close-card" onclick="closeStartupUpdateModal(true)" aria-label="Dismiss update popup">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
        </div>

        <div class="startup-ver-row">
            <span class="startup-ver-pill" id="startupUpdOldVer">v2.5.3</span>
            <span style="color:var(--text-muted);">-&gt;</span>
            <span class="startup-ver-pill new" id="startupUpdNewVer">v2.5.4</span>
        </div>

        <div class="startup-update-notes" id="startupUpdNotes">New improvements and bug fixes ready to install.</div>

        <div id="startupUpdProgWrap" style="display:none;background:var(--surface-2);border-radius:999px;height:6px;overflow:hidden;margin-bottom:10px;">
            <div id="startupUpdProgFill" style="height:100%;width:0%;background:var(--netflix);transition:width 0.25s;"></div>
        </div>
        <div id="startupUpdStatus" style="display:none;font-size:0.74rem;color:var(--text-muted);margin-bottom:10px;"></div>

        <div class="startup-update-actions">
            <button type="button" id="btnStartupUpdateNow" class="vlc-banner" style="flex:1 1 170px;min-height:42px;font-size:0.86rem;" onclick="triggerStartupUpdateNow()">Update Now</button>
            <button type="button" class="btn-sm btn-copy-sm" style="min-height:42px;padding:0 14px;font-size:0.82rem;" onclick="closeStartupUpdateModal(true)">Later</button>
        </div>
    </div>
</div>

<!-- VIP Access Key Full Page -->
<div class="modal-overlay fullscreen-mobile sh-full-page" id="vipUnlockModal" onclick="if(event.target===this) closeVipUnlockModal()" role="dialog" aria-modal="true" aria-labelledby="vipUnlockTitle">
    <div class="settings-dialog" style="max-width:980px !important;">
        <div class="settings-head">
            <div style="display:flex;align-items:center;gap:10px;min-width:0;flex:1;">
                <button type="button" class="btn-sm btn-copy-sm" onclick="closeVipUnlockModal()">Back</button>
                <span class="startup-update-ico" style="width:36px;height:36px;background:rgba(234,179,8,0.16);border-color:rgba(234,179,8,0.45);color:#facc15;flex-shrink:0;">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>
                </span>
                <div style="min-width:0;">
                    <div style="font-size:0.68rem;font-weight:700;color:#facc15;letter-spacing:0.7px;text-transform:uppercase;">StreamHub Membership</div>
                    <h2 id="vipUnlockTitle" style="font-size:1.05rem;font-weight:800;color:#fff;line-height:1.2;">VIP Access Key &amp; Plans</h2>
                </div>
            </div>
            <button type="button" class="btn-close-card" onclick="closeVipUnlockModal()" aria-label="Close VIP Access Key page">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
        </div>

        <div class="settings-body">
            <section class="settings-card">
                <div class="settings-card-title">
                    <span>Redeem VIP Access Key</span>
                    <div style="display:flex;gap:6px;flex-wrap:wrap;">
                        <span class="startup-ver-pill" id="vipModalStatePill">Status: Locked</span>
                        <span class="startup-ver-pill" id="vipModalDevicePill">1 Key = 1 Device</span>
                    </div>
                </div>
                <div id="vipModalDesc" style="font-size:0.8rem;color:var(--text-muted);margin-bottom:12px;line-height:1.5;">
                    15 Days &amp; 1 Month unlock HD/1080p streaming only. 3 Months unlocks Offline Movie &amp; Series Downloads. 6 Months &amp; 1 Year unlock everything including 4K UHD &amp; 4K Cinema Vault.
                </div>

                <div id="vipActiveWrap" style="display:none;margin-bottom:12px;">
                    <div id="vipActiveBox" class="startup-update-notes" style="color:#86efac;border-color:rgba(34,197,94,0.34);background:rgba(34,197,94,0.08);"></div>
                    <div class="startup-update-actions">
                        <button type="button" class="btn-sm btn-indigo-sm" style="flex:1 1 140px;min-height:40px;" onclick="refreshVipStatus(true)">Verify Server</button>
                        <button type="button" class="btn-sm btn-copy-sm" style="flex:1 1 140px;min-height:40px;color:#fca5a5;border-color:rgba(239,68,68,0.35);" onclick="lockVipForTest()">Lock App (Test)</button>
                    </div>
                </div>

                <div id="vipKeyEntryWrap">
                    <label for="vipKeyInput" style="display:block;font-size:0.76rem;font-weight:700;color:var(--text-muted);margin-bottom:6px;">Paste or Enter Your Access Key</label>
                    <input type="text" id="vipKeyInput" class="vip-key-input" placeholder="Enter VIP Key (e.g. SHVIP-3M-XXXX or SHVIP-6M4K-XXXX)" autocomplete="off" autocapitalize="characters" onkeydown="if(event.key==='Enter') submitVipKey()">
                    <div id="vipModalErr" class="vip-err-box"></div>
                    <div class="startup-update-actions">
                        <button type="button" id="btnVipActivate" class="vlc-banner" style="flex:1 1 180px;min-height:44px;font-size:0.9rem;justify-content:center;" onclick="submitVipKey()">Unlock with Key</button>
                        <button type="button" class="btn-sm btn-copy-sm" style="min-height:44px;padding:0 16px;" onclick="closeVipUnlockModal()">Close</button>
                    </div>
                </div>
            </section>

            <section class="settings-card">
                <div class="settings-card-title">
                    <span id="vipPlansSectionTitle">Choose a VIP Plan</span>
                    <span id="vipPlansSectionSub" style="font-size:0.72rem;color:var(--text-muted);font-weight:600;">1 Key = 1 Device &bull; Instant Email Request</span>
                </div>
                <div class="vip-plans-grid" id="vipPlansGrid"></div>
                <div id="vipSelectedPlanNote" style="display:none;font-size:0.78rem;color:#fde047;background:rgba(234,179,8,0.1);border:1px solid rgba(234,179,8,0.3);border-radius:10px;padding:10px 12px;line-height:1.45;"></div>
            </section>

            <section class="settings-card">
                <div class="settings-card-title"><span>How to Buy &amp; Activate Your Key</span></div>
                <div style="font-size:0.78rem;color:var(--text-muted);line-height:1.55;margin-bottom:10px;">
                    1. Select any plan card above and tap <strong style="color:#fff;">Buy Plan via Email</strong>.<br>
                    2. Your default email app opens automatically with <strong style="color:#fde047;">khushalsharma4543@gmail.com</strong>, your chosen plan details, and your Device ID pre-filled.<br>
                    3. Send the email, receive payment &amp; activation details, then enter your VIP Access Key at the top of this page to unlock immediately.
                </div>
                <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
                    <a id="vipSupportMailLink" href="mailto:khushalsharma4543@gmail.com?subject=StreamHub%20VIP%20Access%20Key%20Request" onclick="return buyVipPlanViaEmail(selectedPlanIdUi || '6m', event)" class="btn-sm btn-vlc-sm" style="min-height:38px;padding:0 14px;text-decoration:none;">Email Support (khushalsharma4543@gmail.com)</a>
                    <button type="button" class="btn-sm btn-copy-sm" style="min-height:38px;padding:0 12px;" onclick="copyTextFallback('khushalsharma4543@gmail.com', 'Support email copied to clipboard')">Copy Support Email</button>
                </div>
            </section>
        </div>
    </div>
</div>

<!-- Mobile Bottom App Dock (<768px) -->
<nav class="mobile-dock" aria-label="Mobile quick navigation">
    <div class="mobile-dock-grid">
        <button class="dock-btn active" onclick="scrollToEl('heroSpotlight')">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>
            <span>Home</span>
        </button>
        <button class="dock-btn" onclick="scrollToEl('browseSection')">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
            <span>Catalog</span>
        </button>
        <button class="dock-btn" id="dockBtnDownloads" onclick="openDownloadsModal()" style="position:relative;">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            <span>Downloads</span>
            <span class="nav-dl-badge" id="dockDownloadsBadge" style="display:none;position:absolute;top:2px;right:10px;min-width:16px;height:16px;font-size:0.6rem;">0</span>
        </button>
        <button class="dock-btn" id="dockBtnRemote" onclick="isAndroidApp() ? openHub4kModal() : openRemoteModal()">
            <svg id="dockIconRemote" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            <span id="dockLblRemote">Remote</span>
        </button>
        <button class="dock-btn" id="dockBtnQr" onclick="isAndroidApp() ? openSettingsModal() : openQrModal()" style="position:relative;">
            <svg id="dockIconQr" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3z"/><path d="M20 14v7h-7"/></svg>
            <span id="dockLblQr">QR</span>
            <span id="dockSettingsDot" style="display:none;position:absolute;top:4px;right:14px;width:6px;height:6px;border-radius:50%;background:var(--netflix);"></span>
        </button>
    </div>
</nav>

<!-- Live In-App Download Progress Notification Banner -->
<div class="live-dl-banner" id="liveDownloadBanner" role="status" aria-live="polite">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
        <div id="liveDlTitle" style="font-size:0.79rem;font-weight:700;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;">Downloading...</div>
        <button type="button" onclick="hideLiveDownloadBanner()" style="background:transparent;border:none;color:var(--text-muted);cursor:pointer;padding:2px 4px;display:grid;place-items:center;" title="Minimize notification" aria-label="Minimize">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
    </div>
    <div class="storage-bar-track" style="height:5px;margin:7px 0 5px;"><div class="storage-bar-fill" id="liveDlProgFill"></div></div>
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;">
        <div id="liveDlMeta" style="font-size:0.71rem;color:var(--text-muted);font-weight:600;">0% • 0 MB</div>
        <div style="display:flex;gap:6px;">
            <button type="button" id="liveDlToggleBtn" class="btn-sm btn-indigo-sm" style="padding:4px 9px;font-size:0.7rem;" onclick="toggleLiveDownloadPause()">Pause</button>
            <button type="button" class="btn-sm btn-vlc-sm" style="padding:4px 9px;font-size:0.7rem;" onclick="openDownloadsModal()">View</button>
        </div>
    </div>
</div>

<!-- Non-Blocking Toast Container -->
<div class="toast-wrap" id="toastContainer" aria-live="polite"></div>

<script>
    let activeStreamUrl = "";
    let activeSeekUrl = "";
    let activeBaseOff = 0;
    let activeSubSize = null;
    let activeCompressMethod = 0;
    let activeSid = "";
    let activeTitle = "";
    let lanBaseUrl = "";
    let activeSeriesData = null;
    let activeSeriesSeasonIdx = 0;
    let activeSeriesEpIdx = -1;
    let activeZipEntries = [];
    let activeZipIndex = -1;
    let activeZipTitle = "";
    let activeSourceLink = "";
    let activeResolvedMirrors = [];
    let currentDetailsQualities = [];
    let userSettingsState = {
        profile: { name: "StreamHub User", avatar: "film", preferred_language: "Auto" },
        theme: "netflix",
        download_prefs: { storage_limit_gb: 4, auto_delete_watched: true, auto_expire_days: 7, default_quality: "720p" },
        storage: { used_bytes: 0, limit_gb: 4, limit_bytes: 4294967296, active_count: 0, completed_count: 0, paused_count: 0, total_count: 0 }
    };
    let startupPopupShownForVer = "";
    let startupUpdRetried = false;
    const _SH_ICONS = {
        download: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
        play: '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="6 4 20 12 6 20 6 4"/></svg>',
        pause: '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>',
        trash: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
        close: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
        film: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="2" width="20" height="20" rx="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/></svg>',
        tv: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="7" width="20" height="15" rx="2"/><polyline points="17 2 12 7 7 2"/></svg>',
        star: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>',
        compass: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"/></svg>',
        shield: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
        user: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>'
    };
    const _LEGACY_AV_MAP = { '🎬': 'film', '🍿': 'tv', '🔥': 'star', '👑': 'shield', '🦁': 'compass', '⚡': 'user' };
    function _shAvatarSvg(av) {
        const key = _LEGACY_AV_MAP[av] || av;
        return _SH_ICONS[key] || _SH_ICONS.film;
    }
    let offlineDownloadsList = [];
    let offlinePollTimer = null;
    let lastWakeLockActive = null;
    let dlPickerState = { title: "", poster: "", ep_label: "", media_key: "", parent_key: "", options: [], selectedIdx: 0, selectedLang: "Auto" };
    let catalogItems = [];
    let ottHubData = null;
    let heroItems = [];
    let heroIdx = 0;
    let heroTimer = null;
    let activeTrailerUrl = "";
    let catalogFilter = "all";
    let browsePage = 1;
    let browseCategory = "";
    let browseSearchQuery = "";
    let currentMovieMeta = null;
    let libTab = "history";
    let deferredInstallPrompt = null;
    let remoteTimer = null;
    let remoteLastLen = 0;
    let progressMap = {};
    let smartFilters = { fourK: false, hindi: false, recent: false, imdb7: false };
    let ratingsMap = {};
    let activeImdbId = "";
    let activeMediaKey = "";
    let activeParentKey = "";
    let activeEpLabel = "";
    let activeSubFilePath = "";
    let activeSubKey = "";
    let activeSubVttUrl = "";
    let activeSubLabel = "";
    let suggestTimer = null;
    let suggestAbort = null;
    let inlineNextTimer = null;
    let inlineNextSec = 0;
    let inlineNextCancelled = false;
    let lastInlineSaveTs = 0;
    let voiceRecog = null;
    let lastBrowseScrollY = 0;
    let lastBackExitTs = 0;

    function esc(s) {
        return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function cleanDisplayTitle(t) {
        return String(t || '').replace(/^(?:💎|📺|📦)\s*/u, '').trim();
    }

    function cleanHeroTitle(t) {
        let s = cleanDisplayTitle(t);
        s = s.replace(/\s*(?:DS4K|4K|2160p|1080p|720p|480p|WEB-DL|WEBRip|BluRay|HDRip|Dual Audio).*$/i, '').trim();
        s = s.replace(/\s*\|.*$/, '').trim();
        return s || cleanDisplayTitle(t);
    }

    function dismissIntro() {
        const el = document.getElementById('introSplash');
        if (el) el.classList.add('hide');
    }

    function isAndroidApp() {
        return Boolean(window.StreamHubAndroid && typeof window.StreamHubAndroid.playMedia === 'function');
    }

    function isMobileDevice() {
        return /Android|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini|Mobile/i.test(navigator.userAgent || '');
    }

    function returnToHomeBrowse() {
        const desktopInp = document.getElementById('searchInput');
        const mobileInp = document.getElementById('mobileSearchInput');
        if (desktopInp) desktopInp.value = '';
        if (mobileInp) mobileInp.value = '';
        browseSearchQuery = '';
        browseCategory = '';
        catalogFilter = 'all';
        smartFilters = { fourK: false, hindi: false, recent: false, imdb7: false };
        document.querySelectorAll('#smartFilterStrip .smart-pill[data-sf]').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('#categoryStrip .cat-chip').forEach(b => {
            b.classList.toggle('active', b.getAttribute('data-cat') === '');
        });
        const fAll = document.getElementById('filterAll');
        if (fAll) fAll.classList.add('active');
        const fMov = document.getElementById('filterMovies');
        if (fMov) fMov.classList.remove('active');
        const fSer = document.getElementById('filterSeries');
        if (fSer) fSer.classList.remove('active');
        const bh = document.getElementById('browseHeading');
        if (bh) bh.textContent = 'All Movies & Web Series';
        if (ottHubData && Array.isArray(ottHubData.latest) && ottHubData.latest.length) {
            _applyHomeHubData(ottHubData, false);
        } else {
            loadHomeHub();
        }
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    window.__shAndroidHandleBack = function() {
        const modals = ['vipUnlockModal', 'startupUpdateModal', 'downloadPickerModal', 'inlineWebPlayerModal', 'trailerModalOverlay', 'qrModalOverlay', 'remoteModalOverlay', 'hub4kPageModal', 'profilePageModal', 'themePageModal', 'dlSettingsPageModal', 'toolsPageModal', 'updateModalOverlay', 'downloadsModalOverlay', 'settingsModalOverlay'];
        for (const id of modals) {
            const el = document.getElementById(id);
            if (el && el.classList.contains('open')) {
                if (id === 'vipUnlockModal') closeVipUnlockModal();
                else if (id === 'startupUpdateModal') closeStartupUpdateModal(true);
                else if (id === 'hub4kPageModal') closeHub4kModal();
                else if (id === 'downloadPickerModal') closeDownloadPicker();
                else if (id === 'inlineWebPlayerModal') closeInlinePlayer();
                else if (id === 'trailerModalOverlay') closeTrailerModal();
                else if (id === 'qrModalOverlay') closeQrModal();
                else if (id === 'updateModalOverlay') closeUpdateModal();
                else if (id === 'downloadsModalOverlay') closeDownloadsModal();
                else if (id === 'settingsModalOverlay') closeSettingsModal();
                else closeSubPage(id);
                return true;
            }
        }
        const sugOpen = ['desktopSuggestBox', 'mobileSuggestBox'].some(id => {
            const s = document.getElementById(id);
            return s && s.classList.contains('open');
        });
        if (sugOpen) {
            hideSuggestBoxes();
            return true;
        }
        const rc = document.getElementById('resultsCard');
        if (rc && rc.style.display !== 'none') {
            rc.style.display = 'none';
            const qc = document.getElementById('qualitiesCard');
            if (qc && qc.style.display !== 'none') {
                qc.scrollIntoView({ behavior: 'smooth', block: 'start' });
            } else if (lastBrowseScrollY > 0) {
                window.scrollTo({ top: lastBrowseScrollY, behavior: 'smooth' });
            }
            return true;
        }
        const qc = document.getElementById('qualitiesCard');
        if (qc && qc.style.display !== 'none') {
            closeQualities();
            if (lastBrowseScrollY > 0) {
                window.scrollTo({ top: lastBrowseScrollY, behavior: 'smooth' });
            }
            return true;
        }
        const anySf = smartFilters.fourK || smartFilters.hindi || smartFilters.recent || smartFilters.imdb7;
        if (browseSearchQuery || browseCategory || catalogFilter !== 'all' || anySf) {
            returnToHomeBrowse();
            return true;
        }
        if (window.scrollY > 360) {
            window.scrollTo({ top: 0, behavior: 'smooth' });
            return true;
        }
        const now = Date.now();
        if (now - lastBackExitTs > 2200) {
            lastBackExitTs = now;
            showToast('Press Back once more to exit');
            return true;
        }
        return false;
    };

    window.__shRefreshProgressFromAndroid = function() {
        try { syncProgressFromServer(); } catch (e) {}
        try { refreshDownloadsState(); } catch (e) {}
    };

    window.__shPlayNextFromAndroid = async function(encodedPayloadJson) {
        try {
            const nxt = JSON.parse(decodeURIComponent(encodedPayloadJson || ''));
            if (!nxt || typeof nxt !== 'object') return;
            if (typeof nxt.zip_index === 'number' && activeZipEntries.length && nxt.zip_index >= 0 && nxt.zip_index < activeZipEntries.length) {
                activeZipIndex = nxt.zip_index;
                renderZipEpisodesList(true);
            } else if (typeof nxt.ep_idx === 'number' && typeof nxt.season_idx === 'number' && activeSeriesData && activeSeriesData.seasons) {
                activeSeriesSeasonIdx = nxt.season_idx;
                activeSeriesEpIdx = nxt.ep_idx;
                renderSeriesEpisodes();
            }
            if (!nxt.next_episode) {
                const chainNxt = getNextEpisodePayload();
                if (chainNxt) nxt.next_episode = chainNxt;
            }
            await launchAndroidPlayerWithPayload(nxt, false);
        } catch (e) {}
    };

    function showToast(msg, kind, onClick) {
        const wrap = document.getElementById('toastContainer');
        const el = document.createElement('div');
        el.className = 'toast ' + (kind === 'err' ? 'toast-err' : 'toast-ok');
        el.textContent = msg;
        if (typeof onClick === 'function') {
            el.style.cursor = 'pointer';
            el.onclick = () => { try { onClick(); } catch (e) {} el.remove(); };
        }
        wrap.appendChild(el);
        setTimeout(() => {
            el.style.opacity = '0';
            setTimeout(() => el.remove(), 220);
        }, typeof onClick === 'function' ? 6000 : 3200);
    }

    function readStore(key, fallback) {
        try {
            const raw = localStorage.getItem(key);
            return raw ? JSON.parse(raw) : fallback;
        } catch (e) { return fallback; }
    }
    function writeStore(key, val) {
        try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) {}
    }
    function getItemKey(item) {
        return (item && (item.permalink || item.url || item.sid || item.title)) || '';
    }
    function saveToHistory(item) {
        const key = getItemKey(item);
        if (!key) return;
        let list = readStore('4kmovie_history', []).filter(x => getItemKey(x) !== key);
        list.unshift({
            title: cleanDisplayTitle(item.title || 'StreamHub Stream'),
            permalink: item.permalink || '',
            url: item.url || '',
            thumbnail: item.thumbnail || '',
            badges: item.badges || '',
            card_tag: item.card_tag || '',
            source: item.source || '',
            is_series: !!item.is_series,
            ts: Date.now()
        });
        writeStore('4kmovie_history', list.slice(0, 24));
        renderLibraryShelf();
    }
    function isFavorited(item) {
        const key = getItemKey(item);
        return key ? readStore('4kmovie_favs', []).some(x => getItemKey(x) === key) : false;
    }
    function toggleFavorite(item, ev) {
        if (ev) ev.stopPropagation();
        const key = getItemKey(item);
        if (!key) return;
        let favs = readStore('4kmovie_favs', []);
        const exists = favs.some(x => getItemKey(x) === key);
        if (exists) {
            favs = favs.filter(x => getItemKey(x) !== key);
            showToast('Removed from My List');
        } else {
            favs.unshift({
                title: cleanDisplayTitle(item.title || 'StreamHub Stream'),
                permalink: item.permalink || '',
                url: item.url || '',
                thumbnail: item.thumbnail || '',
                badges: item.badges || '',
                card_tag: item.card_tag || '',
                source: item.source || '',
                is_series: !!item.is_series,
                ts: Date.now()
            });
            favs = favs.slice(0, 40);
            showToast('Saved to My List / Favorites');
        }
        writeStore('4kmovie_favs', favs);
        renderLibraryShelf();
        renderCatalogGrid();
        if (ottHubData) renderOttRails(ottHubData);
        updateHeroFavBtn();
        updateFavActiveButton();
    }
    function toggleActiveFavorite() {
        const item = currentMovieMeta || {
            title: activeTitle,
            url: document.getElementById('urlInput').value.trim(),
            thumbnail: document.getElementById('detailPoster').src || ''
        };
        toggleFavorite(item);
    }
    function updateFavActiveButton() {
        const btn = document.getElementById('btnFavActive');
        if (!btn) return;
        const item = currentMovieMeta || { title: activeTitle, url: document.getElementById('urlInput').value.trim() };
        const fav = isFavorited(item);
        btn.textContent = fav ? 'Saved Fav' : 'Save Fav';
        btn.style.background = fav ? 'rgba(229,9,20,0.24)' : '';
        btn.style.color = fav ? '#fca5a5' : '';
    }
    function markWatched(key) {
        if (!key) return;
        const map = readStore('4kmovie_watched', {});
        map[key] = Date.now();
        writeStore('4kmovie_watched', map);
        renderCatalogGrid();
        if (ottHubData) renderOttRails(ottHubData);
    }
    function isWatched(key) {
        if (!key) return false;
        return !!readStore('4kmovie_watched', {})[key];
    }
    function setLibraryTab(tab) {
        libTab = tab;
        document.getElementById('libTabHistory').classList.toggle('active', tab === 'history');
        document.getElementById('libTabFavs').classList.toggle('active', tab === 'favs');
        renderLibraryShelf();
    }
    function clearLibraryTab() {
        writeStore(libTab === 'favs' ? '4kmovie_favs' : '4kmovie_history', []);
        renderLibraryShelf();
        renderCatalogGrid();
        showToast(libTab === 'favs' ? 'My List cleared' : 'History cleared');
    }
    function removeLibraryItem(idx, ev) {
        if (ev) ev.stopPropagation();
        const key = libTab === 'favs' ? '4kmovie_favs' : '4kmovie_history';
        const list = readStore(key, []);
        list.splice(idx, 1);
        writeStore(key, list);
        renderLibraryShelf();
        renderCatalogGrid();
        updateHeroFavBtn();
    }
    function fmtResumeShort(sec) {
        sec = Math.max(0, Math.floor(sec || 0));
        const h = Math.floor(sec / 3600);
        const m = Math.floor((sec % 3600) / 60);
        const s = sec % 60;
        if (h > 0) return `${h}h ${m}m`;
        if (m > 0) return `${m}m`;
        return `${s}s`;
    }
    function getProgressForKey(k) {
        return k ? (progressMap[k] || null) : null;
    }
    function getProgressForItem(item) {
        if (!item) return null;
        const p1 = getProgressForKey(item.permalink);
        if (p1) return p1;
        return getProgressForKey(getItemKey(item));
    }
    async function syncProgressFromServer() {
        const local = readStore('streamhub_progress', {});
        if (local && typeof local === 'object') progressMap = local;
        try {
            const resp = await fetch('/api/progress');
            const data = await resp.json();
            if (data && data.items && typeof data.items === 'object') {
                progressMap = Object.assign({}, progressMap, data.items);
                writeStore('streamhub_progress', progressMap);
            }
        } catch (e) {}
        renderLibraryShelf();
        renderCatalogGrid();
        if (ottHubData) renderOttRails(ottHubData);
        refreshResumeButton();
    }
    async function saveProgressClient(payload) {
        if (!payload || !payload.key) return;
        const pos = Math.max(0, Math.floor(payload.position_sec || 0));
        const dur = Math.max(0, Math.floor(payload.duration_sec || 0));
        let pct = 0;
        if (dur > 60 && pos >= dur - 25) pct = 100;
        else if (dur > 30) pct = Math.min(99.5, Math.round(pos * 1000 / dur) / 10);
        else if (pos >= 5) pct = Math.min(95, Math.max(2, Math.round(pos / 6) / 10));
        const rec = { ...payload, position_sec: pos, duration_sec: dur, pct, updated_at: Date.now() / 1000 };
        progressMap[payload.key] = rec;
        if (payload.parent_key && payload.parent_key !== payload.key) {
            progressMap[payload.parent_key] = { ...rec, key: payload.parent_key, active_ep_key: payload.key };
        }
        writeStore('streamhub_progress', progressMap);
        refreshResumeButton();
        try {
            await fetch('/api/progress', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
        } catch (e) {}
    }

    function renderLibraryShelf() {
        const sec = document.getElementById('librarySection');
        const shelf = document.getElementById('libraryShelf');
        const hist = readStore('4kmovie_history', []);
        const favs = readStore('4kmovie_favs', []);
        if (!hist.length && !favs.length) { sec.style.display = 'none'; return; }
        sec.style.display = 'block';
        const list = libTab === 'favs' ? favs : hist;
        shelf.innerHTML = '';
        if (!list.length) {
            shelf.innerHTML = '<div style="color:var(--text-muted);font-size:0.84rem;padding:12px 4px;">No saved items in this tab yet.</div>';
            return;
        }
        list.forEach((item, idx) => {
            const card = document.createElement('div');
            const is4kExclusive = _is4kExclusiveItem(item);
            card.className = 'shelf-card' + (is4kExclusive ? ' sh-4k-card-locked' : '');
            const prog = getProgressForItem(item);
            const pct = prog ? Number(prog.pct || 0) : 0;
            const pBar = pct > 1
                ? `<div class="card-progress-track"><div class="card-progress-fill" style="width:${Math.min(100, pct)}%"></div></div>`
                : '';
            const rTag = (pct > 1 && pct < 95 && prog.position_sec >= 10)
                ? `<span class="resume-pill">${esc(fmtResumeShort(prog.position_sec))}</span>`
                : '';
            const thumb = item.thumbnail
                ? `<img class="shelf-poster" src="${esc(item.thumbnail)}" alt="${esc(item.title)}" loading="lazy">`
                : `<div class="shelf-fallback">S</div>`;
            card.innerHTML = `
                <div style="position:relative;">${thumb}<span class="vip-card-lock" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg></span>${is4kExclusive ? '<span class="vip-4k-lock" aria-hidden="true">4K 6M+</span>' : ''}${rTag}${pBar}</div>
                <button class="shelf-del" onclick="removeLibraryItem(${idx}, event)" title="Remove">&times;</button>
                <div class="shelf-info">${esc(cleanHeroTitle(item.title))}</div>
            `;
            card.onclick = () => {
                openTitleNetflixStyle(item);
            };
            shelf.appendChild(card);
        });
    }

    if ('serviceWorker' in navigator) {
        window.addEventListener('load', () => {
            navigator.serviceWorker.register('/sw.js').catch(() => {});
        });
    }
    window.addEventListener('beforeinstallprompt', (e) => {
        e.preventDefault();
        deferredInstallPrompt = e;
    });
    async function triggerInstallApp() {
        if (deferredInstallPrompt) {
            deferredInstallPrompt.prompt();
            await deferredInstallPrompt.userChoice;
            deferredInstallPrompt = null;
            return;
        }
        if (/iphone|ipad|ipod/i.test(navigator.userAgent || '')) {
            showToast('Tap the Safari Share button and choose "Add to Home Screen"');
        } else {
            showToast('Open the browser menu (3 dots) -> choose "Add to Home Screen / Install app"');
        }
    }

    window.addEventListener('scroll', () => {
        const nav = document.getElementById('topNavbar');
        if (nav) nav.classList.toggle('scrolled', window.scrollY > 24);
    });

    window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            dismissIntro();
            closeStartupUpdateModal(true);
            closeHub4kModal();
            closeTrailerModal();
            closeQrModal();
            closeRemoteModal();
            closeUpdateModal();
            hideSuggestBoxes();
        }
    });

    document.addEventListener('click', (e) => {
        if (!e.target.closest || !e.target.closest('.nav-search')) hideSuggestBoxes();
    });

    window.addEventListener('DOMContentLoaded', () => {
        setTimeout(dismissIntro, 1380);
        if (window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone) {
            const bi = document.getElementById('btnInstallApp');
            if (bi) bi.style.display = 'none';
        }
        if (isAndroidApp()) {
            dismissIntro();
            ['linkSection', 'btnHeroPasteLink', 'btnInstallApp', 'btnNavRemote', 'btnNavQr', 'phoneQrCard', 'movieQrCard', 'btnWatchOnline', 'btnInlineWeb', 'pcSetupToolsCard'].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.style.display = 'none';
            });
            const heroDlBtn = document.getElementById('btnHeroDownload');
            if (heroDlBtn) heroDlBtn.style.display = 'inline-flex';
            const chipTxt = document.getElementById('deviceModeText');
            if (chipTxt) chipTxt.textContent = 'Android Cinema';
            const mainTxt = document.getElementById('btnMainVlcText');
            if (mainTxt) mainTxt.textContent = 'Play in StreamHub Cinema Player';
            const lr = document.getElementById('dockLblRemote');
            if (lr) lr.textContent = '4K Hub';
            const ir = document.getElementById('dockIconRemote');
            if (ir) {
                ir.innerHTML = '<rect x="2" y="4" width="20" height="16" rx="2"/><line x1="7" y1="4" x2="7" y2="20"/><line x1="17" y1="4" x2="17" y2="20"/><line x1="2" y1="12" x2="22" y2="12"/>';
            }
            const lq = document.getElementById('dockLblQr');
            if (lq) lq.textContent = 'Settings';
            const iq = document.getElementById('dockIconQr');
            if (iq) {
                iq.innerHTML = '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>';
            }
        } else if (isMobileDevice()) {
            togglePhoneQr(false);
            const chipTxt = document.getElementById('deviceModeText');
            if (chipTxt) chipTxt.textContent = 'Phone VLC Mode';
            const mainTxt = document.getElementById('btnMainVlcText');
            if (mainTxt) mainTxt.textContent = 'Play in Phone VLC App (1-Tap Seekable)';
        }
        document.body.classList.add('sh-vip-locked');
        refreshVipStatus(false);
        syncProgressFromServer();
        initSettingsAndDownloads();
        checkAppUpdate(false);
        setTimeout(() => checkAppUpdate(true, false, true), 700);
        loadLanQr();
        loadHomeHub();
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('show_updates') === '1') {
            dismissIntro();
            setTimeout(() => openUpdateModal(false), 120);
        } else if (urlParams.get('show_settings') === '1') {
            dismissIntro();
            setTimeout(() => openSettingsModal(), 120);
        } else if (urlParams.get('show_downloads') === '1') {
            dismissIntro();
            setTimeout(() => openDownloadsModal(), 120);
        } else if (urlParams.get('open') === 'qr') {
            dismissIntro();
            setTimeout(() => openQrModal(), 120);
        }
    });

    function scrollToEl(id) {
        const el = document.getElementById(id);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function focusSearchMobile() {
        const el = document.getElementById('mobileSearchInput') || document.getElementById('searchInput');
        if (el) { el.focus(); window.scrollTo({ top: 0, behavior: 'smooth' }); }
    }

    function focusLinkInput() {
        scrollToEl('linkSection');
        setTimeout(() => document.getElementById('urlInput').focus(), 200);
    }

    async function pasteToInput() {
        try {
            if (navigator.clipboard && navigator.clipboard.readText) {
                const txt = (await navigator.clipboard.readText() || '').trim();
                if (txt) {
                    document.getElementById('urlInput').value = txt;
                    showToast('Link pasted from clipboard');
                    return;
                }
            }
        } catch (e) {}
        document.getElementById('urlInput').focus();
        showToast('Long-press or press Ctrl+V in the input box to paste');
    }

    function closeQualities() {
        document.getElementById('qualitiesCard').style.display = 'none';
    }

    function openQrModal() {
        document.getElementById('modalQrImg').src = `/api/qr.svg?t=${Date.now()}`;
        document.getElementById('modalLanUrl').textContent = lanBaseUrl || window.location.origin;
        document.getElementById('qrModalOverlay').classList.add('open');
    }

    function closeQrModal() {
        document.getElementById('qrModalOverlay').classList.remove('open');
    }

    function openTrailerModal() {
        if (!activeTrailerUrl) return;
        document.getElementById('trailerIframe').src = activeTrailerUrl;
        document.getElementById('trailerModalOverlay').classList.add('open');
    }

    function closeTrailerModal() {
        const ov = document.getElementById('trailerModalOverlay');
        const ifr = document.getElementById('trailerIframe');
        if (ifr) ifr.src = '';
        if (ov) ov.classList.remove('open');
    }

    function _upgradeBackdropUrl(url) {
        return String(url || '').replace(/\/t\/p\/w\d+\//i, '/t/p/w780/');
    }

    function updateHeroFavBtn() {
        const btn = document.getElementById('btnHeroFav');
        const m = heroItems[heroIdx];
        if (!btn || !m) return;
        btn.textContent = isFavorited(m) ? '✓ In My List' : '+ My List';
    }

    function renderHero() {
        if (!heroItems.length) return;
        const m = heroItems[heroIdx % heroItems.length];
        const bgUrl = _upgradeBackdropUrl(m.thumbnail || '');
        const bd = document.getElementById('heroBackdrop');
        const pImg = document.getElementById('heroPosterImg');
        if (bd && bgUrl) bd.style.backgroundImage = `url("${bgUrl}")`;
        if (pImg && m.thumbnail) {
            pImg.src = _upgradeBackdropUrl(m.thumbnail);
            pImg.alt = cleanHeroTitle(m.title);
            pImg.style.display = 'block';
        }
        document.getElementById('heroTypeBadge').textContent = m.is_series ? 'SERIES' : 'FILM';
        document.getElementById('heroQualityBadge').textContent = m.card_tag || (_is4kText(m.title) ? '4K UHD' : 'FULL HD');
        document.getElementById('heroTitle').textContent = cleanHeroTitle(m.title);

        const parts = String(m.badges || '').split('•').map(s => s.trim()).filter(Boolean);
        document.getElementById('heroMetaRow').innerHTML = parts.length
            ? parts.map(p => `<span>${esc(p)}</span>`).join('<span class="hero-meta-dot"></span>')
            : '<span>HDHub4u &bull; Dual Audio &bull; Instant VLC Seek</span>';

        const dots = document.getElementById('heroDots');
        dots.innerHTML = '';
        heroItems.forEach((_, idx) => {
            const d = document.createElement('button');
            d.className = 'hero-dot' + (idx === heroIdx ? ' active' : '');
            d.ariaLabel = 'Slide ' + (idx + 1);
            d.onclick = () => goHero(idx);
            dots.appendChild(d);
        });
        updateHeroFavBtn();
        _syncHeroVipState();
    }

    function goHero(idx) {
        if (!heroItems.length) return;
        heroIdx = ((idx % heroItems.length) + heroItems.length) % heroItems.length;
        renderHero();
    }
    function nextHero() { goHero(heroIdx + 1); }
    function prevHero() { goHero(heroIdx - 1); }
    function playHeroCurrent() {
        const m = heroItems[heroIdx];
        if (m) openTitleNetflixStyle(m);
    }
    function toggleHeroFavorite() {
        const m = heroItems[heroIdx];
        if (m) toggleFavorite(m);
    }
    function resumeHeroAuto() {
        clearInterval(heroTimer);
        if (heroItems.length > 1) heroTimer = setInterval(nextHero, 6500);
    }
    function pauseHeroAuto() {
        clearInterval(heroTimer);
    }

    function createPosterCard(m, cardClass) {
        const card = document.createElement('article');
        const rawBadges = String(m.badges || 'HD').replace(/^(?:📺\s*)?SERIES\s*•\s*/ui, '').trim();
        const titleTxt = cleanHeroTitle(m.title);
        const faved = isFavorited(m);
        const watched = isWatched(m.permalink);
        const qTag = m.card_tag || (_is4kText(m.title) ? '4K UHD' : 'HD');
        const is4kExclusive = _is4kExclusiveItem(m);
        const isMulti4k = _isMultiQuality4kItem(m);
        card.className = cardClass + (is4kExclusive ? ' sh-4k-card-locked' : (isMulti4k ? ' sh-4k-opt-card' : ''));
        card.tabIndex = 0;
        const prog = getProgressForItem(m);
        const pct = prog ? Number(prog.pct || 0) : 0;
        const resumeBadge = (pct > 1 && pct < 95 && prog.position_sec >= 10)
            ? `<span class="resume-pill">Resume ${esc(fmtResumeShort(prog.position_sec))}</span>`
            : (watched || pct >= 95 ? '<span class="watched-pill">Watched</span>' : '');
        const progBar = pct > 1
            ? `<div class="card-progress-track"><div class="card-progress-fill" style="width:${Math.min(100, pct)}%"></div></div>`
            : '';
        const rInfo = ratingsMap[m.permalink] || (m.imdb_id ? ratingsMap[m.imdb_id] : null);
        const rBadge = (rInfo && rInfo.rating) ? `<span class="imdb-mini">IMDb ${rInfo.rating}</span>` : '';

        const qTagHtml = isMulti4k
            ? `<span class="vip-tag-full">${esc(qTag)}</span><span class="vip-tag-hd">HD &bull; 4K Locked</span>`
            : esc(qTag);
        card.innerHTML = `
            <div class="poster-wrap">
                <img src="${esc(m.thumbnail || '')}" alt="${esc(titleTxt)}" loading="lazy">
                <div class="poster-shade"></div>
                <span class="card-tag-left">${qTagHtml}</span>
                ${m.is_series ? '<span class="card-tag-right">SERIES</span>' : ''}
                ${resumeBadge}
                ${progBar}
                <button class="fav-btn ${faved ? 'faved' : ''}" type="button" aria-label="Toggle My List" title="Add to My List">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="${faved ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2.2"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
                </button>
                <div class="hover-play" aria-hidden="true">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><polygon points="7 4 20 12 7 20 7 4"/></svg>
                </div>
                <span class="vip-card-lock" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg></span>
                ${is4kExclusive ? '<span class="vip-4k-lock" aria-hidden="true">4K 6M+</span>' : ''}
            </div>
            <div class="movie-card-info">
                <div class="movie-card-title">${esc(titleTxt)}</div>
                <div class="movie-card-sub-row">
                    <div class="movie-card-sub">${esc(rawBadges || 'HDHub4u')}${rBadge}</div>
                    <button type="button" class="card-dl-btn" aria-label="Download offline" title="Offline Download">${_SH_ICONS.download}</button>
                </div>
            </div>
        `;
        card.querySelector('.fav-btn').onclick = (e) => toggleFavorite(m, e);
        const dlBtn = card.querySelector('.card-dl-btn');
        if (dlBtn) dlBtn.onclick = (e) => quickOpenMovieDownload(m, e);
        card.onclick = () => openTitleNetflixStyle(m);
        card.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openTitleNetflixStyle(m); } };
        return card;
    }

    function mergeUniqueLists(a, b) {
        const out = [];
        const seen = new Set();
        [...(a || []), ...(b || [])].forEach(x => {
            if (x && x.permalink && !seen.has(x.permalink)) {
                seen.add(x.permalink);
                out.push(x);
            }
        });
        return out;
    }

    function scrollRail(btn, dir) {
        const row = btn && btn.parentElement ? btn.parentElement.querySelector('.rail-row') : null;
        if (row) row.scrollBy({ left: dir * 640, behavior: 'smooth' });
    }

    function renderOttRails(data) {
        const wrap = document.getElementById('ottRailsWrap');
        if (!wrap || !data) return;
        const railSpecs = [
            { title: 'Trending Now on StreamHub', items: (data.latest || []).slice(0, 20), cat: '', accent: '' },
            { title: 'Netflix Hits on HDHub4u', items: data.netflix || [], cat: 'netflix', accent: '' },
            { title: 'Amazon Prime Video Picks', items: data.prime || [], cat: 'amazon-prime-video', accent: 'rail-accent-prime' },
            { title: 'JioHotstar & Bollywood Specials', items: mergeUniqueLists(data.hotstar, data.bollywood), cat: 'bollywood-movies', accent: 'rail-accent-hotstar' },
            { title: 'Hollywood Blockbusters', items: data.hollywood || [], cat: 'hollywood-movies', accent: '' },
            { title: 'Top Web Series Binge', items: data.web_series || [], cat: 'web-series', accent: '' },
            { title: 'Hindi Dubbed & South Action', items: mergeUniqueLists(data.hindi_dubbed, data.south_hindi), cat: 'hindi-dubbed', accent: 'rail-accent-prime' }
        ];
        wrap.innerHTML = '';
        railSpecs.forEach(spec => {
            if (!spec.items || !spec.items.length) return;
            const sec = document.createElement('section');
            sec.className = 'ott-rail';
            sec.innerHTML = `
                <div class="rail-head">
                    <div class="rail-title-wrap">
                        <span class="rail-accent-bar ${spec.accent}"></span>
                        <h2 class="rail-title">${esc(spec.title)}</h2>
                    </div>
                    <button class="rail-explore-btn" onclick="jumpCategory('${esc(spec.cat)}')">Explore All &rsaquo;</button>
                </div>
                <div class="rail-track-wrap">
                    <button class="rail-arrow rail-prev" onclick="scrollRail(this, -1)" aria-label="Scroll left">&#10094;</button>
                    <div class="rail-row"></div>
                    <button class="rail-arrow rail-next" onclick="scrollRail(this, 1)" aria-label="Scroll right">&#10095;</button>
                </div>
            `;
            const row = sec.querySelector('.rail-row');
            spec.items.forEach(m => row.appendChild(createPosterCard(m, 'rail-card')));
            wrap.appendChild(sec);
        });
    }

    function _applyHomeHubData(data, fromCache) {
        if (!data || !Array.isArray(data.latest) || !data.latest.length) return false;
        ottHubData = data;
        heroItems = data.latest.filter(x => x.thumbnail).slice(0, 6);
        if (!heroItems.length) heroItems = data.latest.slice(0, 6);
        heroIdx = 0;
        renderHero();
        resumeHeroAuto();
        renderOttRails(data);

        if (!browseSearchQuery && !browseCategory) {
            catalogItems = data.latest;
            browsePage = 1;
            const status = document.getElementById('searchStatus');
            const btnMore = document.getElementById('btnLoadMore');
            if (status) status.textContent = `${catalogItems.length} playable releases loaded` + (fromCache ? ' (Refreshing...)' : '');
            renderCatalogGrid();
            if (btnMore && catalogItems.length >= 6) {
                btnMore.style.display = 'inline-flex';
                btnMore.textContent = 'Load More Movies (Page 2)';
            }
        }
        return true;
    }

    async function loadHomeHub(forceRefresh) {
        let hadCache = false;
        if (!forceRefresh) {
            try {
                const cachedRaw = localStorage.getItem('sh_home_cache_v2');
                if (cachedRaw) {
                    const cached = JSON.parse(cachedRaw);
                    if (cached && _applyHomeHubData(cached, true)) hadCache = true;
                }
            } catch (e) {}
        }

        try {
            const resp = await fetch('/api/home_hub' + (forceRefresh ? '?refresh=1' : ''));
            const data = await resp.json();
            if (!_applyHomeHubData(data, false)) {
                if (!hadCache) return loadLatest4K(1, '', false);
                return;
            }
            try { localStorage.setItem('sh_home_cache_v2', JSON.stringify(data)); } catch (e) {}
            if (forceRefresh) showToast('Latest movies & links refreshed!');
        } catch (e) {
            if (!hadCache) loadLatest4K(1, '', false);
        }
    }

    async function refreshHomeFeed() {
        const btn = document.getElementById('btnRefreshFeed');
        if (btn) { btn.disabled = true; btn.textContent = 'Refreshing...'; }
        await loadHomeHub(true);
        if (btn) { btn.disabled = false; btn.textContent = '↻ Refresh'; }
    }

    function jumpCategory(catSlug) {
        const stripBtn = document.querySelector(`#categoryStrip .cat-chip[data-cat="${catSlug}"]`);
        selectCategory(stripBtn, catSlug);
        scrollToEl('browseSection');
    }

    function setCatalogFilter(mode) {
        catalogFilter = mode;
        document.getElementById('filterAll').classList.toggle('active', mode === 'all');
        document.getElementById('filterMovies').classList.toggle('active', mode === 'movies');
        document.getElementById('filterSeries').classList.toggle('active', mode === 'series');
        renderCatalogGrid();
    }

    function selectCategory(btn, catSlug) {
        document.querySelectorAll('#categoryStrip .cat-chip').forEach(b => b.classList.remove('active'));
        if (btn) btn.classList.add('active');
        browseSearchQuery = '';
        loadLatest4K(1, catSlug, false);
    }

    async function loadLatest4K(page = 1, cat = '', append = false) {
        browsePage = page;
        browseCategory = cat;
        browseSearchQuery = '';
        const status = document.getElementById('searchStatus');
        const btnMore = document.getElementById('btnLoadMore');
        const catLabel = cat ? cat.replace(/_/g, ' ').replace(/-/g, ' ').toUpperCase() : 'All Movies & Web Series';
        if (!append) {
            document.getElementById('browseHeading').textContent = catLabel;
            status.textContent = 'Loading releases from HDHub4u...';
        } else {
            btnMore.disabled = true;
            btnMore.textContent = 'Loading Page ' + page + '...';
        }
        try {
            const url = `/api/latest?page=${page}${cat ? '&cat=' + encodeURIComponent(cat) : ''}`;
            const resp = await fetch(url);
            const data = await resp.json();
            const items = Array.isArray(data) ? data : [];
            if (append) {
                const seen = new Set(catalogItems.map(x => x.permalink));
                items.forEach(x => { if (!seen.has(x.permalink)) catalogItems.push(x); });
            } else {
                catalogItems = items;
            }
            if (!heroItems.length && items.length) {
                heroItems = items.slice(0, 6);
                renderHero();
                resumeHeroAuto();
            }
            status.textContent = catalogItems.length
                ? `${catalogItems.length} releases loaded`
                : 'No releases found';
            renderCatalogGrid();
            btnMore.disabled = false;
            if (items.length >= 6) {
                btnMore.style.display = 'inline-flex';
                btnMore.textContent = `Load More Movies (Page ${browsePage + 1})`;
            } else {
                btnMore.style.display = 'none';
            }
        } catch (e) {
            btnMore.disabled = false;
            status.textContent = 'Could not load releases';
        }
    }

    async function searchMovie(overrideQ, page = 1, append = false) {
        const desktopInp = document.getElementById('searchInput');
        const mobileInp = document.getElementById('mobileSearchInput');
        const query = (overrideQ !== undefined ? overrideQ : desktopInp.value).trim();
        if (!query) {
            showToast('Enter a Movie or Web Series name', 'err');
            return;
        }
        desktopInp.value = query;
        if (mobileInp) mobileInp.value = query;
        browseSearchQuery = query;
        browsePage = page;

        const status = document.getElementById('searchStatus');
        const grid = document.getElementById('searchGrid');
        const btnMore = document.getElementById('btnLoadMore');
        if (!append) {
            document.getElementById('browseHeading').textContent = `Search: "${query}"`;
            status.textContent = 'Searching HDHub4u...';
            grid.innerHTML = '';
            scrollToEl('browseSection');
        } else {
            btnMore.disabled = true;
            btnMore.textContent = 'Loading Page ' + page + '...';
        }

        try {
            const resp = await fetch(`/api/search?q=${encodeURIComponent(query)}&page=${page}`);
            const data = await resp.json();
            const items = Array.isArray(data) ? data : [];
            if (append) {
                const seen = new Set(catalogItems.map(x => x.permalink));
                items.forEach(x => { if (!seen.has(x.permalink)) catalogItems.push(x); });
            } else {
                catalogItems = items;
            }
            if (!catalogItems.length) {
                status.textContent = 'No results found — please try a different title.';
                btnMore.style.display = 'none';
                return;
            }
            status.textContent = `${catalogItems.length} results loaded`;
            renderCatalogGrid();
            if (smartFilters.imdb7) ensureRatingsLoaded(catalogItems);
            btnMore.disabled = false;
            if (items.length >= 6) {
                btnMore.style.display = 'inline-flex';
                btnMore.textContent = `Load More Results (Page ${browsePage + 1})`;
            } else {
                btnMore.style.display = 'none';
            }
        } catch (e) {
            btnMore.disabled = false;
            status.textContent = 'Search error: ' + e.message;
        }
    }

    function loadMoreCatalog() {
        const nextPage = browsePage + 1;
        if (browseSearchQuery) {
            searchMovie(browseSearchQuery, nextPage, true);
        } else {
            loadLatest4K(nextPage, browseCategory, true);
        }
    }

    function renderMovies(movies) {
        catalogItems = Array.isArray(movies) ? movies : [];
        renderCatalogGrid();
    }

    function renderCatalogGrid() {
        const grid = document.getElementById('searchGrid');
        grid.innerHTML = '';
        const list = catalogItems.filter(m => {
            if (catalogFilter === 'movies' && m.is_series) return false;
            if (catalogFilter === 'series' && !m.is_series) return false;
            const hay = `${m.title || ''} ${m.badges || ''} ${m.year || ''} ${m.card_tag || ''}`;
            if (smartFilters.fourK && !/2160p|4k|ds4k|uhd/i.test(hay)) return false;
            if (smartFilters.hindi && !/hindi|dual\s*audio|hin-eng|multi\s*audio/i.test(hay)) return false;
            if (smartFilters.recent && !/(?:2025|2026)/.test(hay)) return false;
            if (smartFilters.imdb7) {
                const ri = ratingsMap[m.permalink] || (m.imdb_id ? ratingsMap[m.imdb_id] : null);
                if (!ri || !ri.rating || Number(ri.rating) < 7.0) return false;
            }
            return true;
        });
        const anySf = smartFilters.fourK || smartFilters.hindi || smartFilters.recent || smartFilters.imdb7;
        const clrBtn = document.getElementById('btnSmartClear');
        if (clrBtn) clrBtn.style.display = anySf ? 'inline-flex' : 'none';
        if (!list.length) {
            grid.innerHTML = '<div style="grid-column:1/-1;color:var(--text-muted);padding:24px 0;text-align:center;font-size:0.9rem;">No items match this filter yet.</div>';
            return;
        }
        list.forEach(m => grid.appendChild(createPosterCard(m, 'movie-card')));
    }

    async function ensureRatingsLoaded(items) {
        const missing = (items || []).filter(m => m && m.permalink && !ratingsMap[m.permalink]).slice(0, 24);
        if (!missing.length) return;
        const status = document.getElementById('searchStatus');
        const prevTxt = status ? status.textContent : '';
        if (status) status.textContent = 'Checking real IMDb ratings...';
        try {
            const payload = missing.map(m => ({
                key: m.permalink,
                title: m.title || '',
                imdb_id: m.imdb_id || '',
                year: m.year || '',
                is_series: !!m.is_series
            }));
            const resp = await fetch('/api/ratings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ items: payload })
            });
            const data = await resp.json();
            if (data && data.ratings) {
                Object.assign(ratingsMap, data.ratings);
            }
        } catch (e) {}
        if (status) status.textContent = prevTxt || 'Ratings updated';
        renderCatalogGrid();
    }

    function toggleSmartFilter(key, btnEl) {
        if (key === '4k') smartFilters.fourK = !smartFilters.fourK;
        else if (key === 'hindi') smartFilters.hindi = !smartFilters.hindi;
        else if (key === 'recent') smartFilters.recent = !smartFilters.recent;
        else if (key === 'imdb7') smartFilters.imdb7 = !smartFilters.imdb7;
        if (btnEl) btnEl.classList.toggle('active');
        if (key === 'imdb7' && smartFilters.imdb7) {
            ensureRatingsLoaded(catalogItems);
        }
        renderCatalogGrid();
    }

    function clearSmartFilters() {
        smartFilters = { fourK: false, hindi: false, recent: false, imdb7: false };
        document.querySelectorAll('#smartFilterStrip .smart-pill[data-sf]').forEach(b => b.classList.remove('active'));
        renderCatalogGrid();
    }

    function hideSuggestBoxes() {
        ['desktopSuggestBox', 'mobileSuggestBox'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.classList.remove('open');
        });
    }

    function onSearchTyping(val, src) {
        const q = String(val || '').trim();
        const boxId = src === 'mobile' ? 'mobileSuggestBox' : 'desktopSuggestBox';
        const box = document.getElementById(boxId);
        if (!box) return;
        clearTimeout(suggestTimer);
        if (q.length < 2) {
            box.classList.remove('open');
            box.innerHTML = '';
            return;
        }
        suggestTimer = setTimeout(async () => {
            if (suggestAbort) { try { suggestAbort.abort(); } catch (e) {} }
            suggestAbort = new AbortController();
            try {
                const resp = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=8`, { signal: suggestAbort.signal });
                const items = await resp.json();
                if (!Array.isArray(items) || !items.length) {
                    box.innerHTML = '<div style="padding:10px;font-size:0.78rem;color:var(--text-muted);">No quick match found — press Enter for full search</div>';
                    box.classList.add('open');
                    return;
                }
                box.innerHTML = '';
                items.slice(0, 8).forEach(m => {
                    const row = document.createElement('div');
                    row.className = 'suggest-item';
                    row.innerHTML = `
                        <img class="suggest-thumb" src="${esc(m.thumbnail || '')}" alt="" loading="lazy">
                        <div class="suggest-meta">
                            <div class="suggest-title">${esc(cleanHeroTitle(m.title))}</div>
                            <div class="suggest-sub">${esc(m.card_tag || 'HD')} ${m.is_series ? '• SERIES' : ''} • ${esc(m.badges || '')}</div>
                        </div>
                    `;
                    row.onclick = () => {
                        hideSuggestBoxes();
                        openTitleNetflixStyle(m);
                    };
                    box.appendChild(row);
                });
                box.classList.add('open');
            } catch (e) {}
        }, 230);
    }

    function startVoiceSearch(isMobile) {
        const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
        const btn = document.getElementById(isMobile ? 'btnMobileVoiceSearch' : 'btnVoiceSearch');
        const inp = document.getElementById(isMobile ? 'mobileSearchInput' : 'searchInput');
        if (!SR) {
            showToast('Voice Search is not supported in this browser (works in Chrome/Edge on desktop or mobile)', 'err');
            return;
        }
        if (voiceRecog) { try { voiceRecog.stop(); } catch (e) {} voiceRecog = null; }
        const rec = new SR();
        voiceRecog = rec;
        rec.lang = 'en-IN';
        rec.interimResults = false;
        rec.maxAlternatives = 1;
        if (btn) btn.classList.add('listening');
        showToast('Mic ON — speak a Movie or Series title...');
        rec.onresult = (ev) => {
            const spoken = (ev.results && ev.results[0] && ev.results[0][0] && ev.results[0][0].transcript) || '';
            if (spoken && inp) {
                inp.value = spoken;
                hideSuggestBoxes();
                searchMovie(spoken);
            }
        };
        rec.onerror = () => { if (btn) btn.classList.remove('listening'); };
        rec.onend = () => { if (btn) btn.classList.remove('listening'); voiceRecog = null; };
        try { rec.start(); } catch (e) { if (btn) btn.classList.remove('listening'); }
    }

    function renderDetailMeta(meta) {
        const strip = document.getElementById('detailMetaStrip');
        const imdb = document.getElementById('detailImdbBadge');
        const trBtn = document.getElementById('btnWatchTrailer');
        const story = document.getElementById('detailStoryline');
        const shots = document.getElementById('detailScreenshots');
        const poster = document.getElementById('detailPoster');
        if (!strip) return;

        meta = meta || {};
        activeImdbId = meta.imdb_id || (currentMovieMeta && currentMovieMeta.imdb_id) || '';
        if (meta.poster && (!poster.src || poster.style.display === 'none')) {
            poster.src = meta.poster;
            poster.style.display = 'block';
        }
        let hasStrip = false;
        if (meta.imdb_rating) {
            imdb.textContent = 'IMDb ' + meta.imdb_rating;
            imdb.href = meta.imdb_url || '#';
            imdb.style.display = 'inline-flex';
            hasStrip = true;
            if (currentMovieMeta && currentMovieMeta.permalink) {
                const numR = parseFloat(String(meta.imdb_rating).replace(/[^0-9.]/g, ''));
                if (!isNaN(numR)) ratingsMap[currentMovieMeta.permalink] = { imdb_id: activeImdbId, rating: numR };
            }
        } else {
            imdb.style.display = 'none';
        }
        if (meta.trailer_url) {
            activeTrailerUrl = meta.trailer_url;
            trBtn.style.display = 'inline-flex';
            hasStrip = true;
        } else {
            activeTrailerUrl = '';
            trBtn.style.display = 'none';
        }
        strip.style.display = hasStrip ? 'flex' : 'none';

        if (meta.storyline) {
            story.textContent = meta.storyline;
            story.style.display = 'block';
        } else {
            story.style.display = 'none';
        }

        if (meta.screenshots && meta.screenshots.length) {
            shots.innerHTML = meta.screenshots.map(s => `<img src="${esc(s)}" alt="Preview" loading="lazy">`).join('');
            shots.style.display = 'flex';
        } else {
            shots.innerHTML = '';
            shots.style.display = 'none';
        }
    }

    function _pickLightestPackIdx(packs) {
        if (!packs || !packs.length) return -1;
        let bestIdx = 0;
        let bestScore = -9999;
        packs.forEach((p, i) => {
            const tl = String(p.title || '').toLowerCase();
            let s = 0;
            if (/720p/.test(tl)) s += 120;
            else if (/1080p/.test(tl)) s += 90;
            else if (/480p/.test(tl)) s += 80;
            else if (_is4kText(tl)) s -= (vipState.active && !vipState.allow_4k ? 500 : 40);
            const mSz = String(p.title || p.size || '').match(/(\d+(?:\.\d+)?)\s*(gb|mb)/i);
            if (mSz) {
                const gb = mSz[2].toUpperCase() === 'MB' ? parseFloat(mSz[1]) / 1024 : parseFloat(mSz[1]);
                if (gb >= 35) s -= 100;
                else if (gb >= 15) s -= 35;
                else if (gb <= 6) s += 25;
            }
            if (/\.zip/i.test(tl)) s -= 10;
            if (s > bestScore) { bestScore = s; bestIdx = i; }
        });
        return bestIdx;
    }

    function playZipEpisodeDirect(zipIdx) {
        const ep = activeZipEntries[zipIdx];
        if (!ep) return;
        const is4kZipEp = _is4kVaultItem(currentMovieMeta) || (_is4kText(ep.name) && !_hasHdNon4kText(ep.name));
        const okVip = is4kZipEp
            ? require4kAccess(() => playZipEpisodeDirect(zipIdx))
            : requireVipAccess(() => playZipEpisodeDirect(zipIdx));
        if (!okVip) return;
        activeZipIndex = zipIdx;
        markWatched(ep.sid || ep.name);
        renderSeriesEpisodes();
        const sTitle = cleanDisplayTitle((activeSeriesData && activeSeriesData.title) || (currentMovieMeta && currentMovieMeta.title) || 'Web Series');
        const pKey = (currentMovieMeta && currentMovieMeta.permalink) || '';
        const epLbl = `Ep ${zipIdx + 1}`;
        const mKey = pKey ? `${pKey}::zip::${ep.name}` : (ep.sid || ep.name);
        const prevProg = getProgressForKey(mKey) || getProgressForKey(ep.sid || ep.name);
        const startSec = (prevProg && prevProg.position_sec >= 10 && prevProg.pct < 95) ? Math.floor(prevProg.position_sec) : 0;
        const reqPayload = {
            stream_url: ep.stream_url,
            seek_url: ep.seek_url || '',
            base_offset: ep.base_offset || 0,
            sub_size: (ep.sub_size !== undefined && ep.sub_size !== null) ? ep.sub_size : null,
            compress_method: ep.compress_method || 0,
            sid: ep.sid || '',
            title: `${sTitle} • ${ep.name}`,
            ep_label: epLbl,
            media_key: mKey,
            parent_key: pKey,
            start_time: startSec,
            preferred_language: (userSettingsState.profile && userSettingsState.profile.preferred_language) || 'Auto',
            preferred_quality: (userSettingsState.download_prefs && userSettingsState.download_prefs.stream_quality) || 'auto',
            next_episode: buildNextZipPayload(zipIdx + 1, 0)
        };
        if (isAndroidApp()) {
            launchAndroidPlayerWithPayload(reqPayload, false);
            return;
        }
        playZipEntry(zipIdx);
    }

    async function unpackSeriesPackInline(packIdx, silentAuto) {
        const pack = activeSeriesData && (activeSeriesData.packs || [])[packIdx];
        if (!silentAuto) {
            const is4kPack = _is4kVaultItem(currentMovieMeta) || (pack && _is4kText(pack.title) && !_hasHdNon4kText(pack.title));
            const okVip = is4kPack
                ? require4kAccess(() => unpackSeriesPackInline(packIdx, false))
                : requireVipAccess(() => unpackSeriesPackInline(packIdx, false));
            if (!okVip) return;
        }
        if (!pack || !pack.link) return;
        const list = document.getElementById('episodesList');
        const seasonSelect = document.getElementById('seasonSelect');
        if (list) {
            list.innerHTML = `<div class="ep-row"><div class="ep-info"><div class="ep-title">Unpacking episodes from ${esc(cleanDisplayTitle(pack.title))}...</div><div class="ep-meta">Loading individual episodes for 1-tap playback</div></div></div>`;
        }
        try {
            const resp = await fetch('/api/resolve', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: pack.link })
            });
            const data = await resp.json();
            if (!data || !data.success) {
                if (data && data.vip_required) {
                    refreshVipStatus(false);
                    if (!silentAuto) window.openVipUnlockModal(false, Boolean(data.upgrade_4k_required));
                }
                if (!silentAuto) showToast((data && data.error) || 'Failed to load pack episodes', 'err');
                if (list) list.innerHTML = `<div class="ep-row"><div class="ep-info"><div class="ep-title">Could not unpack season pack — please try another pack</div><div class="ep-meta">${esc((data && data.error) || 'Server unavailable')}</div></div></div>`;
                return;
            }
            if (data.is_series && data.seasons && data.seasons.length) {
                activeSeriesData.seasons = data.seasons;
                seasonSelect.innerHTML = '';
                data.seasons.forEach((s, idx) => {
                    const opt = document.createElement('option');
                    opt.value = 'season:' + idx;
                    opt.textContent = `${cleanDisplayTitle(s.title)} (${(s.episodes || []).length} Episodes)`;
                    seasonSelect.appendChild(opt);
                });
                if (activeSeriesData.packs && activeSeriesData.packs.length) {
                    const opt = document.createElement('option');
                    opt.value = 'packs';
                    opt.textContent = `Season Packs (${activeSeriesData.packs.length} Packs)`;
                    seasonSelect.appendChild(opt);
                }
                seasonSelect.value = 'season:0';
                renderSeriesEpisodes();
                return;
            }
            if (data.is_zip && data.zip_entries && data.zip_entries.length) {
                activeZipEntries = data.zip_entries;
                activeZipTitle = data.zip_title || pack.title || 'Season Pack';
                activeSeriesData._unpackedZipTitle = cleanDisplayTitle(pack.title);
                if (!Array.from(seasonSelect.options).some(o => o.value === 'zip_unpacked')) {
                    const opt = document.createElement('option');
                    opt.value = 'zip_unpacked';
                    opt.textContent = `Pack Episodes (${activeZipEntries.length} Episodes)`;
                    seasonSelect.insertBefore(opt, seasonSelect.firstChild);
                }
                seasonSelect.value = 'zip_unpacked';
                renderSeriesEpisodes();
                return;
            }
            // Direct single movie/episode fallback inside pack
            if (isAndroidApp() && data.stream_url) {
                launchAndroidDirectStream({
                    stream_url: data.stream_url,
                    seek_url: data.seek_url || '',
                    sid: data.sid || '',
                    compress_method: data.compress_method || 0,
                    title: cleanDisplayTitle(data.title || pack.title || 'Episode'),
                    media_key: pack.link
                });
                return;
            }
            if (!silentAuto) {
                document.getElementById('urlInput').value = pack.link;
                resolveLink();
            }
        } catch (e) {
            if (!silentAuto) showToast('Pack load error: ' + e.message, 'err');
        }
    }

    function renderSeriesPanel(details, posterUrl) {
        activeSeriesData = details;
        activeSeriesSeasonIdx = 0;
        activeSeriesEpIdx = -1;
        const panel = document.getElementById('qualitiesCard');
        const title = document.getElementById('movieCardTitle');
        const poster = document.getElementById('detailPoster');
        const movieBox = document.getElementById('movieQualityBox');
        const seriesBox = document.getElementById('seriesPanel');
        const seasonSelect = document.getElementById('seasonSelect');

        if (posterUrl) { poster.src = posterUrl; poster.style.display = 'block'; }
        else { poster.style.display = 'none'; }
        if (details.title) title.textContent = cleanDisplayTitle(details.title);
        renderDetailMeta(details.meta);
        panel.style.display = 'block';
        movieBox.style.display = 'none';
        seriesBox.style.display = 'block';
        seasonSelect.innerHTML = '';

        (details.seasons || []).forEach((s, idx) => {
            const opt = document.createElement('option');
            opt.value = 'season:' + idx;
            opt.textContent = `${cleanDisplayTitle(s.title)} (${s.episodes.length} Episodes)`;
            seasonSelect.appendChild(opt);
        });

        if (details.packs && details.packs.length) {
            const opt = document.createElement('option');
            opt.value = 'packs';
            opt.textContent = `Season Packs (${details.packs.length} Packs — Direct Episode Streaming)`;
            seasonSelect.appendChild(opt);
        }

        renderSeriesEpisodes();
        panel.scrollIntoView({ behavior: 'smooth', block: 'start' });

        // Auto-unpack pack-only series (e.g. Blade Runner 2099 / The Good Doctor) immediately
        if ((!details.seasons || !details.seasons.length) && details.packs && details.packs.length) {
            const bestP = _pickLightestPackIdx(details.packs);
            if (bestP >= 0) unpackSeriesPackInline(bestP, true);
        } else if (details.seasons && details.seasons.length && details.seasons[0].episodes && details.seasons[0].episodes.length) {
            // Silently pre-warm Episode 1 in background while user views episodes
            const firstEp = details.seasons[0].episodes[0];
            if (firstEp && firstEp.link && /^https?:\/\//i.test(firstEp.link) && !/\.test\//i.test(firstEp.link)) {
                fetch('/api/prewarm', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: firstEp.link })
                }).catch(() => {});
            }
        }
    }

    function renderSeriesEpisodes() {
        const val = document.getElementById('seasonSelect').value;
        const list = document.getElementById('episodesList');
        list.innerHTML = '';
        if (!activeSeriesData) return;

        if (val === 'zip_unpacked' && activeZipEntries.length) {
            activeZipEntries.forEach((ep, zIdx) => {
                const row = document.createElement('div');
                row.className = 'ep-row' + (zIdx === activeZipIndex ? ' active' : '');
                const seekTag = ep.seekable ? 'Seekable' : 'Sequential';
                const wTag = isWatched(ep.sid || ep.name) ? '<span class="watched-chip">Watched</span>' : '';
                const is4kLockedEp = Boolean(vipState.active && !vipState.allow_4k && (_is4kVaultItem(currentMovieMeta) || (_is4kText(ep.name) && !_hasHdNon4kText(ep.name))));
                const k4Tag = is4kLockedEp ? '<span class="watched-chip" style="background:rgba(234,179,8,0.2);color:#fde047;border-color:rgba(234,179,8,0.45);">4K 6M+</span>' : '';
                row.innerHTML = `
                    <div class="ep-info">
                        <div class="ep-title">${zIdx + 1}. ${esc(ep.name)}${wTag}${k4Tag}</div>
                        <div class="ep-meta">${esc(ep.size || 'HD')} &bull; ${seekTag}</div>
                    </div>
                    <div class="ep-actions">
                        <button class="btn-sm btn-copy-sm" onclick="openDownloadPickerForZipEp(${zIdx})" title="Download Offline" aria-label="Download Offline">${_SH_ICONS.download}</button>
                        <button class="btn-sm btn-vlc-sm" onclick="playZipEpisodeDirect(${zIdx})">${is4kLockedEp ? 'Upgrade 4K' : 'Play'}</button>
                    </div>
                `;
                list.appendChild(row);
            });
            return;
        }

        if (val === 'packs') {
            (activeSeriesData.packs || []).forEach((p, pIdx) => {
                const row = document.createElement('div');
                row.className = 'ep-row';
                const wTag = isWatched(p.link) ? '<span class="watched-chip">Watched</span>' : '';
                const is4kLockedPk = Boolean(vipState.active && !vipState.allow_4k && (_is4kVaultItem(currentMovieMeta) || (_is4kText(p.title) && !_hasHdNon4kText(p.title))));
                const k4Tag = is4kLockedPk ? '<span class="watched-chip" style="background:rgba(234,179,8,0.2);color:#fde047;border-color:rgba(234,179,8,0.45);">4K 6M+</span>' : '';
                row.innerHTML = `
                    <div class="ep-info">
                        <div class="ep-title">${esc(cleanDisplayTitle(p.title))}${wTag}${k4Tag}</div>
                        <div class="ep-meta">Season Pack — tap to load episodes</div>
                    </div>
                    <div class="ep-actions">
                        <button class="btn-sm btn-vlc-sm" onclick="unpackSeriesPackInline(${pIdx}, false)">${is4kLockedPk ? 'Upgrade 4K' : 'Open Pack'}</button>
                    </div>
                `;
                list.appendChild(row);
            });
            return;
        }

        const idx = parseInt((val || 'season:0').split(':')[1] || '0', 10);
        activeSeriesSeasonIdx = idx;
        const season = (activeSeriesData.seasons || [])[idx];
        if (!season) return;
        season.episodes.forEach((ep, epIdx) => {
            const row = document.createElement('div');
            row.className = 'ep-row' + (epIdx === activeSeriesEpIdx ? ' active' : '');
            const wTag = isWatched(ep.link) ? '<span class="watched-chip">Watched</span>' : '';
            const offDone = offlineDownloadsList.some(d => d.status === 'completed' && d.hdhub_link === ep.link);
            const offTag = offDone ? '<span class="watched-chip" style="background:rgba(16,185,129,0.2);color:#6ee7b7;border-color:rgba(16,185,129,0.4);">Offline</span>' : '';
            const is4kLockedEp = Boolean(vipState.active && !vipState.allow_4k && (_is4kVaultItem(currentMovieMeta) || (_is4kText(ep.title) && !_hasHdNon4kText(ep.title))));
            const k4Tag = is4kLockedEp ? '<span class="watched-chip" style="background:rgba(234,179,8,0.2);color:#fde047;border-color:rgba(234,179,8,0.45);">4K 6M+</span>' : '';
            const qBadge = (ep.qualities && ep.qualities.length > 1) ? ` • ${ep.qualities.length} Qualities` : '';
            row.innerHTML = `
                <div class="ep-info">
                    <div class="ep-title">${esc(ep.ep_label)} &bull; ${esc(ep.title)}${wTag}${offTag}${k4Tag}</div>
                    <div class="ep-meta">${ep.size ? 'Size: ' + esc(ep.size) : 'HD / 4K Stream'}${qBadge}</div>
                </div>
                <div class="ep-actions">
                    <button class="btn-sm btn-copy-sm" onclick="openDownloadPickerForEpisode(${idx}, ${epIdx})" title="Download Offline" aria-label="Download Offline">${_SH_ICONS.download}</button>
                    <button class="btn-sm btn-vlc-sm" onclick="pickSeriesLink('${encodeURIComponent(ep.link)}', ${idx}, ${epIdx})">${is4kLockedEp ? 'Upgrade 4K' : 'Play'}</button>
                </div>
            `;
            list.appendChild(row);
        });
    }

    function _isLikelySeriesItem(m) {
        if (!m) return false;
        if (m.is_series) return true;
        const t = String(m.title || '');
        const tag = String(m.card_tag || '');
        return /\b(?:season\s*\d+|s\d{1,2}\s*[-–]?\s*e?\d{0,2}|all\s*episodes|complete\s*series|web\s*series)\b/i.test(t) || /series/i.test(tag);
    }

    function launchAndroidDirectStream(cfg) {
        if (!isAndroidApp()) return false;
        const profLang = (userSettingsState.profile && userSettingsState.profile.preferred_language) || 'Auto';
        const defQual = (userSettingsState.download_prefs && userSettingsState.download_prefs.stream_quality) || 'auto';
        const payload = Object.assign({
            is_pending: true,
            target: '',
            server_origin: window.location.origin,
            preferred_language: profLang,
            preferred_quality: defQual,
            audio_track: 1,
            sub_track: 'auto',
            start_time: 0
        }, cfg || {});
        window.StreamHubAndroid.playMedia(JSON.stringify(payload));
        return true;
    }

    function openTitleNetflixStyle(movie) {
        if (!movie) return;
        const okVip = _is4kExclusiveItem(movie)
            ? require4kAccess(() => openTitleNetflixStyle(movie))
            : requireVipAccess(() => openTitleNetflixStyle(movie));
        if (!okVip) return;
        if (!movie.permalink && movie.url) {
            if (isAndroidApp()) {
                saveToHistory(movie);
                launchAndroidDirectStream({
                    hdhub_link: movie.url,
                    title: cleanHeroTitle(movie.title || 'StreamHub Cinema'),
                    media_key: movie.url
                });
                return;
            }
            currentMovieMeta = movie;
            document.getElementById('urlInput').value = movie.url;
            resolveLink();
            return;
        }
        if (isAndroidApp() && !_isLikelySeriesItem(movie)) {
            currentMovieMeta = movie;
            saveToHistory(movie);
            markWatched(movie.permalink);
            const prevProg = getProgressForItem(movie);
            const startSec = (prevProg && prevProg.position_sec >= 10 && prevProg.pct < 95) ? Math.floor(prevProg.position_sec) : 0;
            launchAndroidDirectStream({
                movie_permalink: movie.permalink,
                title: cleanHeroTitle(movie.title || 'StreamHub Cinema'),
                media_key: movie.permalink,
                start_time: startSec
            });
            return;
        }
        loadMovieDetails(movie);
    }

    async function quickOpenMovieDownload(movie, ev) {
        if (ev) { ev.stopPropagation(); ev.preventDefault(); }
        const okVip = _is4kExclusiveItem(movie)
            ? require4kAccess(() => quickOpenMovieDownload(movie))
            : requireDownloadAccess(() => quickOpenMovieDownload(movie));
        if (!okVip) return;
        if (!movie || !movie.permalink) {
            showToast('Download link is not available', 'err');
            return;
        }
        showToast('Loading download qualities...');
        try {
            const resp = await fetch('/api/movie?url=' + encodeURIComponent(movie.permalink));
            const details = await resp.json();
            if (details && details.is_series) {
                loadMovieDetails(movie);
                return;
            }
            const opts = (details && details.qualities ? details.qualities : []).map(q => ({
                label: q.title,
                hdhub_link: q.link,
                size_label: _inferQualityTag(q.title).size,
                quality_label: _inferQualityTag(q.title).res
            }));
            if (!opts.length) {
                showToast((details && details.error) || 'No downloadable links found', 'err');
                return;
            }
            openDownloadPicker({
                title: movie.title || details.title || 'Movie',
                poster: movie.thumbnail || (details.meta && details.meta.poster) || '',
                media_key: movie.permalink || movie.title,
                source: movie.source || '',
                options: opts
            });
        } catch (e) {
            showToast('Could not load download options: ' + e.message, 'err');
        }
    }

    function downloadHeroCurrent() {
        const m = heroItems[heroIdx];
        if (m) quickOpenMovieDownload(m);
    }

    function playToolsCustomLink() {
        const inp = document.getElementById('toolsCustomUrlInput');
        const url = (inp && inp.value || '').trim();
        if (!url) { showToast('Please paste a HubCloud, HDHub4u, or .m3u8 link first', 'err'); return; }
        const okVip = /4khdhub\./i.test(url)
            ? require4kAccess(() => playToolsCustomLink())
            : requireVipAccess(() => playToolsCustomLink());
        if (!okVip) return;
        closeAllSettingsPages();
        if (isAndroidApp()) {
            launchAndroidDirectStream({
                hdhub_link: url,
                title: 'Custom Stream',
                media_key: url
            });
            return;
        }
        const mainInp = document.getElementById('urlInput');
        if (mainInp) mainInp.value = url;
        resolveLink();
    }

    function pickSeriesLink(encodedLink, sIdx, epIdx) {
        const link = decodeURIComponent(encodedLink);
        const sea = (sIdx >= 0 && activeSeriesData && activeSeriesData.seasons) ? activeSeriesData.seasons[sIdx] : null;
        const epObj = (sea && epIdx >= 0 && sea.episodes) ? sea.episodes[epIdx] : null;
        const is4kEp = _is4kVaultItem(currentMovieMeta) || (epObj && _is4kText(epObj.title) && !_hasHdNon4kText(epObj.title));
        const okVip = is4kEp
            ? require4kAccess(() => pickSeriesLink(encodedLink, sIdx, epIdx))
            : requireVipAccess(() => pickSeriesLink(encodedLink, sIdx, epIdx));
        if (!okVip) return;
        const pKey = (currentMovieMeta && currentMovieMeta.permalink) || '';
        const sTitle = cleanDisplayTitle((activeSeriesData && activeSeriesData.title) || (currentMovieMeta && currentMovieMeta.title) || 'Web Series');
        let epLbl = '';
        let nxtPayload = null;
        if (sIdx >= 0 && epIdx >= 0) {
            activeSeriesSeasonIdx = sIdx;
            activeSeriesEpIdx = epIdx;
            markWatched(link);
            renderSeriesEpisodes();
            epLbl = (epObj && epObj.ep_label) || `Ep ${epIdx + 1}`;
            nxtPayload = buildNextSeriesPayload(sIdx, epIdx + 1, 0);
        } else {
            markWatched(link);
        }
        if (isAndroidApp() && sIdx >= 0 && epIdx >= 0) {
            const sea = (activeSeriesData && activeSeriesData.seasons || [])[sIdx];
            const epObjCur = sea && sea.episodes ? sea.episodes[epIdx] : null;
            const epKey = pKey ? `${pKey}::ep::${epLbl}` : link;
            const prevProg = getProgressForKey(epKey) || getProgressForKey(link);
            const startSec = (prevProg && prevProg.position_sec >= 10 && prevProg.pct < 95) ? Math.floor(prevProg.position_sec) : 0;
            launchAndroidDirectStream({
                hdhub_link: link,
                movie_permalink: pKey,
                title: sTitle,
                ep_label: epLbl,
                media_key: epKey,
                parent_key: pKey,
                start_time: startSec,
                quality_options: (epObjCur && epObjCur.qualities) ? epObjCur.qualities : [],
                next_episode: nxtPayload
            });
            return;
        }
        document.getElementById('urlInput').value = link;
        resolveLink();
    }

    async function loadMovieDetails(movie) {
        lastBrowseScrollY = window.scrollY || 0;
        currentMovieMeta = movie;
        activeImdbId = movie.imdb_id || '';
        activeSeriesData = null;
        activeSeriesSeasonIdx = 0;
        activeSeriesEpIdx = -1;
        activeZipEntries = [];
        activeZipIndex = -1;
        activeSubFilePath = '';
        activeSubKey = '';
        activeSubVttUrl = '';
        activeSubLabel = '';
        saveToHistory(movie);
        const panel = document.getElementById('qualitiesCard');
        const title = document.getElementById('movieCardTitle');
        const poster = document.getElementById('detailPoster');
        const select = document.getElementById('qualitySelect');
        const movieBox = document.getElementById('movieQualityBox');
        const seriesBox = document.getElementById('seriesPanel');

        title.textContent = cleanDisplayTitle(movie.title);
        if (movie.thumbnail) { poster.src = movie.thumbnail; poster.style.display = 'block'; }
        else { poster.style.display = 'none'; }
        renderDetailMeta(null);

        const prevProg = getProgressForItem(movie);
        if (prevProg && prevProg.position_sec >= 10 && prevProg.pct < 95) {
            const epHint = prevProg.ep_label ? `${prevProg.ep_label} • ` : '';
            showToast(`Last watched: ${epHint}${fmtResumeShort(prevProg.position_sec)}`);
        }

        panel.style.display = 'block';
        movieBox.style.display = 'block';
        seriesBox.style.display = 'none';
        panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
        select.innerHTML = '<option>Loading qualities, IMDb &amp; CDN mirrors...</option>';
        try {
            const resp = await fetch('/api/movie?url=' + encodeURIComponent(movie.permalink));
            const details = await resp.json();
            if (details.is_series) {
                renderSeriesPanel(details, movie.thumbnail);
                return;
            }
            if (isAndroidApp() && details.qualities && details.qualities.length) {
                panel.style.display = 'none';
                const allQual4kOnly = _is4kVaultItem(movie) || details.qualities.every(q => _is4kText(q.title) && !_hasHdNon4kText(q.title));
                if (vipState.active && !vipState.allow_4k && allQual4kOnly) {
                    require4kAccess(() => loadMovieDetails(movie));
                    return;
                }
                const prevP = getProgressForItem(movie);
                const startSec = (prevP && prevP.position_sec >= 10 && prevP.pct < 95) ? Math.floor(prevP.position_sec) : 0;
                launchAndroidDirectStream({
                    movie_permalink: movie.permalink,
                    title: cleanHeroTitle(details.title || movie.title || 'StreamHub Cinema'),
                    media_key: movie.permalink,
                    start_time: startSec
                });
                return;
            }
            renderDetailMeta(details.meta);
            select.innerHTML = '';
            currentDetailsQualities = details.qualities || [];
            if (!details.qualities || !details.qualities.length) {
                select.innerHTML = '<option value="">No working link available for this movie</option>';
                showToast(details.error || 'Streaming link is not available for this movie', 'err');
                return;
            }
            const prefLang = String((userSettingsState.profile && userSettingsState.profile.preferred_language) || 'Auto').toLowerCase();
            let prefIdx = -1;
            let firstUnlockedIdx = -1;
            details.qualities.forEach((q, qIdx) => {
                const opt = document.createElement('option');
                const is4kQual = _is4kVaultItem(movie) || (_is4kText(q.title) && !_hasHdNon4kText(q.title));
                const locked4kQual = Boolean(vipState.active && !vipState.allow_4k && is4kQual);
                opt.value = q.link;
                opt.textContent = locked4kQual ? `[4K Locked - 6M+] ${q.title}` : q.title;
                opt.dataset.locked4k = locked4kQual ? '1' : '';
                select.appendChild(opt);
                if (!locked4kQual && firstUnlockedIdx < 0) firstUnlockedIdx = qIdx;
                if (!locked4kQual && prefIdx < 0 && prefLang && prefLang !== 'auto' && String(q.title || '').toLowerCase().includes(prefLang)) {
                    prefIdx = qIdx;
                }
            });
            if (prefIdx >= 0) select.selectedIndex = prefIdx;
            else if (firstUnlockedIdx >= 0) select.selectedIndex = firstUnlockedIdx;
        } catch (e) {
            select.innerHTML = '<option value="">Error loading qualities</option>';
            showToast('Could not load movie links: ' + e.message, 'err');
        }
    }

    function useSelectedQuality() {
        const select = document.getElementById('qualitySelect');
        if (!select || !select.value) {
            showToast('No active streaming link available for this movie', 'err');
            return;
        }
        const selOpt = select.options[select.selectedIndex];
        const selLocked = Boolean(
            vipState.active && !vipState.allow_4k && (
                (selOpt && selOpt.dataset && selOpt.dataset.locked4k === '1') ||
                _is4kVaultItem(currentMovieMeta) ||
                (selOpt && _is4kText(selOpt.textContent) && !_hasHdNon4kText(selOpt.textContent))
            )
        );
        if (selLocked) {
            require4kAccess(() => useSelectedQuality());
            return;
        }
        document.getElementById('urlInput').value = select.value;
        resolveLink();
    }

    function formatDur(sec) {
        if (!sec || sec <= 0) return '';
        const h = Math.floor(sec / 3600);
        const m = Math.floor((sec % 3600) / 60);
        return h > 0 ? `${h}h ${String(m).padStart(2,'0')}m` : `${m}m`;
    }

    function inferQualityLabel(titleStr, cm) {
        const t = String(titleStr || '').toLowerCase();
        if (cm === -1) return 'HLS Adaptive HD';
        if (/2160p|4k|uhd|ds4k/.test(t)) return '4K 2160p UHD';
        if (/1080p/.test(t)) return '1080p Full HD';
        if (/720p/.test(t)) return '720p HD';
        return 'HD Stream';
    }

    function setVideoQualityBadge(label, isGold) {
        const vbadge = document.getElementById('resVideoBadge');
        if (!vbadge) return;
        vbadge.className = isGold ? 'badge badge-gold' : 'badge badge-green';
        vbadge.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><rect x="2" y="4" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/></svg> <span>${esc(label)}</span>`;
        vbadge.style.display = 'inline-flex';
    }

    async function fetchRealMediaInfo(sid) {
        if (!sid) return;
        const badge = document.getElementById('resTrackBadge');
        badge.textContent = 'Scanning MKV Tracks...';
        try {
            const resp = await fetch('/api/media_info?sid=' + encodeURIComponent(sid));
            const info = await resp.json();
            if (activeSid !== sid || !info || !info.success) return;

            const audioSel = document.getElementById('audioTrackSelect');
            const subSel = document.getElementById('subTrackSelect');
            const atracks = info.audio_tracks || [];
            const stracks = info.subtitle_tracks || [];

            if (atracks.length) {
                audioSel.innerHTML = '';
                atracks.forEach(t => {
                    const opt = document.createElement('option');
                    opt.value = t.track_num;
                    opt.textContent = t.label;
                    audioSel.appendChild(opt);
                });
                autoSelectPreferredAudioOption();
            }
            subSel.innerHTML = '<option value="auto">Auto / Default Subtitles</option><option value="off">Subtitles Off</option>';
            stracks.forEach(s => {
                const opt = document.createElement('option');
                opt.value = String(s.track_num - 1);
                opt.textContent = s.label;
                subSel.appendChild(opt);
            });

            if (atracks.length || stracks.length) {
                badge.textContent = `${atracks.length} Audio • ${stracks.length} Subtitles`;
            } else {
                badge.textContent = 'Standard Stream';
            }

            const v = info.video || {};
            const durTxt = formatDur(info.duration_sec);
            const vParts = [v.resolution, v.codec, durTxt].filter(Boolean);
            if (vParts.length) {
                setVideoQualityBadge(vParts.join(' • '), /4k|2160/i.test(vParts.join(' ')));
            }
        } catch (e) {
            badge.textContent = 'Multi-Audio';
        }
    }

    function updateNextEpisodeButton() {
        const btn = document.getElementById('btnNextEpisode');
        const txt = document.getElementById('btnNextEpisodeText');
        if (!btn) return;
        if (activeZipEntries.length && activeZipIndex >= 0 && activeZipIndex + 1 < activeZipEntries.length) {
            btn.style.display = 'inline-flex';
            txt.textContent = `Next Ep (${activeZipIndex + 2}/${activeZipEntries.length})`;
            return;
        }
        if (activeSeriesData && activeSeriesData.seasons && activeSeriesEpIdx >= 0) {
            const season = activeSeriesData.seasons[activeSeriesSeasonIdx];
            if (season && season.episodes && activeSeriesEpIdx + 1 < season.episodes.length) {
                btn.style.display = 'inline-flex';
                txt.textContent = `Next: ${season.episodes[activeSeriesEpIdx + 1].ep_label}`;
                return;
            }
        }
        btn.style.display = 'none';
    }

    function playNextEpisode() {
        if (activeZipEntries.length && activeZipIndex >= 0 && activeZipIndex + 1 < activeZipEntries.length) {
            playZipEntry(activeZipIndex + 1);
            return;
        }
        if (activeSeriesData && activeSeriesData.seasons && activeSeriesEpIdx >= 0) {
            const season = activeSeriesData.seasons[activeSeriesSeasonIdx];
            if (season && season.episodes && activeSeriesEpIdx + 1 < season.episodes.length) {
                const nextIdx = activeSeriesEpIdx + 1;
                pickSeriesLink(encodeURIComponent(season.episodes[nextIdx].link), activeSeriesSeasonIdx, nextIdx);
            }
        }
    }

    function getActiveResumeSec() {
        const p = getProgressForKey(activeMediaKey) || (activeParentKey === activeMediaKey ? getProgressForKey(activeParentKey) : null);
        if (!p || p.pct >= 95 || !p.position_sec || p.position_sec < 10) return 0;
        return Math.floor(p.position_sec);
    }

    function refreshResumeButton() {
        const mainTxt = document.getElementById('btnMainVlcText');
        const startOverBtn = document.getElementById('btnStartOver');
        if (!mainTxt) return;
        const rSec = getActiveResumeSec();
        if (rSec >= 10) {
            mainTxt.textContent = isAndroidApp()
                ? `Resume (${fmtResumeShort(rSec)}) in Cinema Player`
                : (isMobileDevice()
                    ? `Resume (${fmtResumeShort(rSec)}) in Phone VLC`
                    : `Resume (${fmtResumeShort(rSec)}) in In-Built Player`);
            if (startOverBtn) startOverBtn.style.display = (!isAndroidApp() && isMobileDevice()) ? 'none' : 'inline-flex';
        } else {
            mainTxt.textContent = isAndroidApp()
                ? 'Play in StreamHub Cinema Player'
                : (isMobileDevice()
                    ? 'Play in Phone VLC App (1-Tap Seekable)'
                    : 'Play in StreamHub In-Built Player (0% Loss 4K HDR)');
            if (startOverBtn) startOverBtn.style.display = 'none';
        }
    }

    function buildNextZipPayload(idx, depth) {
        if (!activeZipEntries.length || idx < 0 || idx >= activeZipEntries.length || depth > 15) return null;
        const ep = activeZipEntries[idx];
        const pKey = (currentMovieMeta && currentMovieMeta.permalink) || '';
        const mKey = pKey ? `${pKey}::zip::${ep.name}` : (ep.sid || ep.name);
        const trk = document.getElementById('audioTrackSelect') ? (parseInt(document.getElementById('audioTrackSelect').value || '1', 10) || 1) : 1;
        const subEl = document.getElementById('subTrackSelect');
        return {
            zip_index: idx,
            stream_url: ep.stream_url,
            seek_url: ep.seek_url || '',
            base_offset: ep.base_offset || 0,
            sub_size: (ep.sub_size !== undefined && ep.sub_size !== null) ? ep.sub_size : null,
            compress_method: ep.compress_method || 0,
            sid: ep.sid || '',
            audio_track: trk,
            sub_track: subEl ? subEl.value : 'auto',
            title: cleanDisplayTitle(ep.name),
            ep_label: `Ep ${idx + 1}`,
            media_key: mKey,
            parent_key: pKey,
            next_episode: buildNextZipPayload(idx + 1, depth + 1),
        };
    }

    function buildNextSeriesPayload(sIdx, epIdx, depth) {
        if (!activeSeriesData || !activeSeriesData.seasons || depth > 15) return null;
        const season = activeSeriesData.seasons[sIdx];
        if (!season || !season.episodes || epIdx < 0 || epIdx >= season.episodes.length) return null;
        const nxt = season.episodes[epIdx];
        const pKey = (currentMovieMeta && currentMovieMeta.permalink) || '';
        const trk = document.getElementById('audioTrackSelect') ? (parseInt(document.getElementById('audioTrackSelect').value || '1', 10) || 1) : 1;
        const subEl = document.getElementById('subTrackSelect');
        const epLbl = nxt.ep_label || `Ep ${epIdx + 1}`;
        return {
            season_idx: sIdx,
            ep_idx: epIdx,
            hdhub_link: nxt.link,
            movie_permalink: pKey,
            title: `${cleanDisplayTitle(activeSeriesData.title || activeTitle)} • ${epLbl}`,
            ep_label: epLbl,
            audio_track: trk,
            sub_track: subEl ? subEl.value : 'auto',
            media_key: pKey ? `${pKey}::ep::${epLbl}` : nxt.link,
            parent_key: pKey,
            quality_options: (nxt && nxt.qualities) ? nxt.qualities : [],
            next_episode: buildNextSeriesPayload(sIdx, epIdx + 1, depth + 1),
        };
    }

    function getNextEpisodePayload() {
        if (activeZipEntries.length && activeZipIndex >= 0 && activeZipIndex + 1 < activeZipEntries.length) {
            return buildNextZipPayload(activeZipIndex + 1, 0);
        }
        if (activeSeriesData && activeSeriesData.seasons && activeSeriesEpIdx >= 0) {
            return buildNextSeriesPayload(activeSeriesSeasonIdx, activeSeriesEpIdx + 1, 0);
        }
        return null;
    }

    function setActiveEntry(streamUrl, seekUrl, title, size, off, sz, cm, watchOnlineUrl, sid) {
        activeStreamUrl = streamUrl;
        activeSeekUrl = seekUrl || "";
        activeBaseOff = off || 0;
        activeSubSize = (sz !== undefined && sz !== null) ? sz : null;
        activeCompressMethod = cm || 0;
        activeSid = sid || "";
        activeTitle = cleanDisplayTitle(title || "4K Movie Stream");

        activeParentKey = (currentMovieMeta && currentMovieMeta.permalink) || '';
        if (activeZipEntries.length && activeZipIndex >= 0) {
            activeEpLabel = `Ep ${activeZipIndex + 1}`;
            activeMediaKey = activeParentKey ? `${activeParentKey}::zip::${activeZipEntries[activeZipIndex].name}` : (activeSid || activeTitle);
        } else if (activeSeriesData && activeSeriesEpIdx >= 0) {
            const season = (activeSeriesData.seasons || [])[activeSeriesSeasonIdx];
            const epObj = season && season.episodes ? season.episodes[activeSeriesEpIdx] : null;
            activeEpLabel = (epObj && epObj.ep_label) || `Ep ${activeSeriesEpIdx + 1}`;
            activeMediaKey = activeParentKey ? `${activeParentKey}::ep::${activeEpLabel}` : (activeSid || activeTitle);
        } else {
            activeEpLabel = '';
            activeMediaKey = activeParentKey || activeSid || activeTitle;
        }

        const subInp = document.getElementById('subQueryInput');
        if (subInp) {
            const cleanQ = cleanHeroTitle((currentMovieMeta && currentMovieMeta.title) || activeTitle);
            subInp.value = activeEpLabel ? `${cleanQ} ${activeEpLabel}` : cleanQ;
        }
        const subList = document.getElementById('subResultsList');
        if (subList) { subList.style.display = 'none'; subList.innerHTML = ''; }
        const subBadge = document.getElementById('activeSubBadge');
        if (subBadge) subBadge.textContent = activeSubLabel ? `Loaded: ${activeSubLabel}` : 'Shortcuts: Audio J/K • Sub G/H';

        document.getElementById('resTitle').textContent = activeTitle;
        document.getElementById('resSize').textContent = 'Size: ' + (size || '4K UHD');
        const canHttpSeek = (activeCompressMethod === -1) || (activeCompressMethod !== 8 && (Boolean(activeSeekUrl) || !String(activeStreamUrl || '').includes('video-downloads.googleusercontent.com')));
        const seekBadgeEl = document.getElementById('resSeekBadge');
        seekBadgeEl.textContent = (activeCompressMethod === -1)
            ? 'HLS Instant Seek'
            : ((activeCompressMethod === 8) ? 'Sequential ZIP Stream' : (canHttpSeek ? 'HTTP 206 Seeking Active' : 'Sequential Stream (Use HLS/R2 for Seek)'));
        seekBadgeEl.className = canHttpSeek ? 'badge badge-gold' : 'badge';

        const btnPl = document.getElementById('btnDownloadPlaylist');
        if (btnPl) {
            if (activeSid) {
                btnPl.href = `/api/playlist.m3u8?sid=${encodeURIComponent(activeSid)}&t=${encodeURIComponent(activeTitle)}`;
            } else {
                const seekQ = activeSeekUrl ? `&seek_url=${encodeURIComponent(activeSeekUrl)}` : '';
                const zipQ = (activeBaseOff || activeSubSize !== null || activeCompressMethod)
                    ? `&off=${activeBaseOff}&sz=${activeSubSize || 0}&cm=${activeCompressMethod}` : '';
                btnPl.href = `/api/playlist.m3u8?url=${encodeURIComponent(activeStreamUrl)}${seekQ}${zipQ}&t=${encodeURIComponent(activeTitle)}`;
            }
        }

        const btnOnline = document.getElementById('btnWatchOnline');
        const btnInline = document.getElementById('btnInlineWeb');
        if (watchOnlineUrl && !isAndroidApp()) {
            btnOnline.href = watchOnlineUrl;
            btnOnline.style.display = 'inline-flex';
            if (btnInline) btnInline.style.display = 'inline-flex';
        } else {
            btnOnline.style.display = 'none';
            if (btnInline) btnInline.style.display = 'none';
        }

        updateMovieQr(activeSid, activeTitle);
        updateFavActiveButton();
        updateNextEpisodeButton();
        refreshResumeButton();
        const inferredQ = inferQualityLabel(activeTitle, activeCompressMethod);
        setVideoQualityBadge(inferredQ, /4k|2160/i.test(inferredQ));
        if (activeCompressMethod === -1) {
            document.getElementById('resTrackBadge').textContent = 'HLS Adaptive Audio';
        } else if (activeSid && activeCompressMethod === 0) {
            fetchRealMediaInfo(activeSid);
        }
    }

    async function resolveLink() {
        const inpEl = document.getElementById('urlInput');
        const url = (inpEl ? inpEl.value : '').trim();
        if (!url) { showToast('Please paste a HubCloud, HubDrive, or HDHub4u link first', 'err'); return; }
        const okVip = /4khdhub\./i.test(url)
            ? require4kAccess(() => resolveLink())
            : requireVipAccess(() => resolveLink());
        if (!okVip) return;
        const status = document.getElementById('resolveStatus');
        const errBox = document.getElementById('errorBox');
        const btn = document.getElementById('btnResolve');
        const resCard = document.getElementById('resultsCard');

        status.style.display = 'flex';
        document.getElementById('resolveStatusText').textContent = 'Resolving 4K Ultra HD, Web Series & High-Speed CDN Servers...';
        errBox.style.display = 'none';
        btn.disabled = true;
        resCard.style.display = 'none';

        try {
            const resp = await fetch('/api/resolve', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url })
            });
            const data = await resp.json();
            btn.disabled = false;

            if (!data.success) {
                status.style.display = 'none';
                if (data && data.vip_required) {
                    refreshVipStatus(false);
                    openVipUnlockModal(false, Boolean(data.upgrade_4k_required));
                }
                errBox.style.display = 'block';
                errBox.innerHTML = '<b>Error:</b> ' + esc(data.error || 'Could not resolve link');
                return;
            }

            if (data.is_series) {
                status.style.display = 'none';
                renderSeriesPanel(data, currentMovieMeta ? currentMovieMeta.thumbnail : '');
                return;
            }

            saveToHistory({
                title: data.zip_title || data.title,
                permalink: currentMovieMeta ? currentMovieMeta.permalink : '',
                url: url,
                thumbnail: currentMovieMeta ? currentMovieMeta.thumbnail : ''
            });
            if (currentMovieMeta && currentMovieMeta.permalink) markWatched(currentMovieMeta.permalink);

            status.style.display = 'none';
            resCard.style.display = 'block';
            resCard.scrollIntoView({ behavior: 'smooth', block: 'start' });

            activeZipEntries = data.zip_entries || [];
            activeZipIndex = (data.is_zip && activeZipEntries.length) ? 0 : -1;
            activeZipTitle = data.zip_title || data.title || 'Season Pack';
            activeSourceLink = url;
            activeResolvedMirrors = data.mirrors || [];

            setActiveEntry(
                data.stream_url,
                data.seek_url || "",
                data.title,
                data.size,
                data.base_offset || 0,
                data.sub_size !== undefined ? data.sub_size : null,
                data.compress_method || 0,
                data.watch_online_url,
                data.sid || ""
            );

            renderZipEpisodesList(data.is_zip);

            // Audio tracks
            const audioSelect = document.getElementById('audioTrackSelect');
            if (data.audio_tracks && data.audio_tracks.length) {
                audioSelect.innerHTML = '';
                data.audio_tracks.forEach(t => {
                    const opt = document.createElement('option');
                    opt.value = t.track_num;
                    opt.textContent = t.label;
                    audioSelect.appendChild(opt);
                });
                autoSelectPreferredAudioOption();
            }

            // Render Mirrors
            const mirrorsContainer = document.getElementById('mirrorsContainer');
            mirrorsContainer.innerHTML = '';
            (data.mirrors || []).forEach(m => {
                const item = document.createElement('div');
                item.className = 'mirror-item' + (m.url === activeStreamUrl ? ' active' : '');
                const mSeek = (m.type === 'google_cdn' && data.seek_url) ? data.seek_url : (m.seek_url || '');
                const canPlay = m.alive && m.type !== 'zipdisk' && !data.is_zip;
                const mSid = m.sid || '';
                const cleanName = cleanDisplayTitle(m.name).replace(/^⚡\s*/u, '');
                const vlcBtn = canPlay
                    ? `<button class="btn-sm btn-vlc-sm" onclick="playInVlcCustom('${encodeURIComponent(m.url)}', null, '${encodeURIComponent(mSeek)}', 0, null, 0, '${esc(mSid)}')">${isAndroidApp() ? 'Play' : (isMobileDevice() ? 'Phone VLC' : 'Play VLC')}</button>`
                    : (data.is_zip && m.alive ? `<span class="badge badge-green">Active Pack Server</span>` : `<button class="btn-sm btn-copy-sm" style="opacity:0.45;cursor:not-allowed;" disabled>Offline (403)</button>`);

                item.innerHTML = `
                    <div class="mirror-name">
                        <span style="color:${m.alive ? '#10b981' : '#ef4444'};font-weight:900;margin-right:6px;">&bull;</span>
                        <span>${esc(cleanName)}</span>
                    </div>
                    <div class="ep-actions">
                        ${vlcBtn}
                        <button class="btn-sm btn-copy-sm" onclick="copyCustomUrl('${encodeURIComponent(m.url)}', '${esc(mSid)}')">Copy</button>
                    </div>
                `;
                mirrorsContainer.appendChild(item);
            });

        } catch (e) {
            btn.disabled = false;
            status.style.display = 'none';
            errBox.style.display = 'block';
            errBox.innerHTML = '<b>Network error:</b> ' + esc(e.message);
        }
    }

    function renderZipEpisodesList(isZip) {
        const zipPanel = document.getElementById('zipEpisodesPanel');
        const zipList = document.getElementById('zipEpisodesList');
        if (isZip && activeZipEntries.length) {
            zipPanel.style.display = 'block';
            document.getElementById('zipPanelHeading').textContent =
                `${cleanDisplayTitle(activeZipTitle)} (${activeZipEntries.length} Episodes — Direct Streaming)`;
            zipList.innerHTML = '';
            activeZipEntries.forEach((ep, i) => {
                const row = document.createElement('div');
                row.className = 'ep-row' + (i === activeZipIndex ? ' active' : '');
                row.id = 'zip-ep-' + i;
                const seekTag = ep.seekable ? 'Seekable' : 'Sequential';
                const wTag = isWatched(ep.sid || ep.name) ? '<span class="watched-chip">Watched</span>' : '';
                const playLbl = isAndroidApp() ? 'Play' : (isMobileDevice() ? 'Phone VLC' : 'Play VLC');
                row.innerHTML = `
                    <div class="ep-info">
                        <div class="ep-title">${i + 1}. ${esc(ep.name)}${wTag}</div>
                        <div class="ep-meta">${esc(ep.size)} &bull; ${seekTag}</div>
                    </div>
                    <div class="ep-actions">
                        <button class="btn-sm btn-copy-sm" onclick="openDownloadPickerForZipEp(${i})" title="Download Episode Offline" aria-label="Download Episode Offline">${_SH_ICONS.download}</button>
                        <button class="btn-sm btn-vlc-sm" onclick="playZipEntry(${i})">${playLbl}</button>
                        <a class="btn-sm btn-copy-sm" target="_blank" href="${esc(ep.watch_online_url)}">Web</a>
                    </div>
                `;
                zipList.appendChild(row);
            });
        } else {
            zipPanel.style.display = 'none';
            zipList.innerHTML = '';
        }
    }

    function playZipEntry(idx) {
        const ep = activeZipEntries[idx];
        if (!ep) return;
        activeZipIndex = idx;
        markWatched(ep.sid || ep.name);
        renderZipEpisodesList(true);
        setActiveEntry(ep.stream_url, ep.seek_url, ep.name, ep.size, ep.base_offset, ep.sub_size, ep.compress_method, ep.watch_online_url, ep.sid);
        if (!isAndroidApp() && isMobileDevice() && ep.sid) {
            openInPhoneVlc(ep.sid, ep.name);
            return;
        }
        const trk = document.getElementById('audioTrackSelect').value || 1;
        playInVlcCustom(encodeURIComponent(ep.stream_url), trk, encodeURIComponent(ep.seek_url || ''), ep.base_offset, ep.sub_size, ep.compress_method, ep.sid);
    }

    function downloadSeasonPlaylist() {
        if (!activeZipEntries.length) return;
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '/api/season_playlist.m3u8';
        const inp = document.createElement('input');
        inp.type = 'hidden';
        inp.name = 'payload';
        inp.value = JSON.stringify({ title: activeZipTitle, entries: activeZipEntries });
        form.appendChild(inp);
        document.body.appendChild(form);
        form.submit();
        form.remove();
    }

    async function loadLanQr(notify) {
        try {
            const resp = await fetch('/api/lan_info');
            const info = await resp.json();
            if (info && info.url) {
                lanBaseUrl = info.url;
                const el = document.getElementById('lanUrlText');
                if (el) el.textContent = info.url;
                const ml = document.getElementById('modalLanUrl');
                if (ml) ml.textContent = info.url;
                const img = document.getElementById('appQrImg');
                if (img) img.src = `/api/qr.svg?t=${Date.now()}`;
                const mimg = document.getElementById('modalQrImg');
                if (mimg) mimg.src = `/api/qr.svg?t=${Date.now()}`;
                if (activeSid) updateMovieQr(activeSid, activeTitle);
                if (notify) showToast('Wi-Fi IP updated: ' + info.url);
            }
        } catch (e) {}
    }

    function togglePhoneQr(show) {
        const exp = document.getElementById('phoneQrExpanded');
        const col = document.getElementById('phoneQrCollapsed');
        if (!exp || !col) return;
        exp.style.display = show ? 'flex' : 'none';
        col.style.display = show ? 'none' : 'flex';
    }

    function updateMovieQr(sid, title) {
        const box = document.getElementById('movieQrCard');
        if (!box) return;
        if (!sid || isAndroidApp()) {
            box.style.display = 'none';
            return;
        }
        const rel = '/phone/' + sid;
        const full = (lanBaseUrl || window.location.origin) + rel;
        document.getElementById('movieQrImg').src = `/api/qr.svg?text=${encodeURIComponent(rel)}`;
        document.getElementById('movieQrSub').textContent = `${title || '4K Stream'} • ${full}`;
        document.getElementById('btnMoviePhoneOpen').href = rel;
        box.style.display = 'flex';
    }

    function phoneVlcDeepLink(sid, cmOverride, rawUrlOverride) {
        const cm = (cmOverride !== undefined) ? cmOverride : activeCompressMethod;
        const rawU = rawUrlOverride || activeStreamUrl;
        if (cm === -1 && rawU && /^https?:\/\//i.test(rawU)) {
            if (/iphone|ipad|ipod/i.test(navigator.userAgent || '')) {
                return 'vlc-x-callback://x-callback-url/stream?url=' + encodeURIComponent(rawU);
            }
            const noScheme = rawU.replace(/^https?:\/\//i, '');
            const sch = rawU.startsWith('https://') ? 'https' : 'http';
            return 'intent://' + noScheme + '#Intent;package=org.videolan.vlc;type=video/*;scheme=' + sch + ';end;';
        }
        const path = '/s/' + sid + '.mkv';
        const fullUrl = window.location.origin + path;
        if (/iphone|ipad|ipod/i.test(navigator.userAgent || '')) {
            return 'vlc-x-callback://x-callback-url/stream?url=' + encodeURIComponent(fullUrl);
        }
        return 'intent://' + window.location.host + path + '#Intent;package=org.videolan.vlc;type=video/*;scheme=http;end;';
    }

    function openInPhoneVlc(sid, title, cm, rawUrl) {
        if (!sid && !(cm === -1 && rawUrl)) return;
        markWatched(sid || rawUrl);
        if (isAndroidApp()) {
            launchAndroidPlayerWithPayload({
                sid: sid || '',
                stream_url: rawUrl || '',
                compress_method: cm || 0,
                title: title || activeTitle || 'StreamHub Cinema',
                start_time: getActiveResumeSec(),
                media_key: activeMediaKey || sid || title || '',
                parent_key: activeParentKey || '',
                ep_label: activeEpLabel || '',
                subtitle_file: activeSubFilePath || '',
                subtitle_key: activeSubKey || '',
                next_episode: getNextEpisodePayload()
            }, false);
            return;
        }
        window.location.href = phoneVlcDeepLink(sid, cm, rawUrl);
    }

    function applySubtitleToInlineVideo() {
        const track = document.getElementById('inlineSubTrack');
        const subSt = document.getElementById('inlineSubStatus');
        const vid = document.getElementById('inlineVideoEl');
        if (!track) return;
        if (activeSubVttUrl) {
            track.src = activeSubVttUrl;
            track.label = activeSubLabel || 'StreamHub Sub';
            if (vid && vid.textTracks && vid.textTracks[0]) {
                vid.textTracks[0].mode = 'showing';
            }
            if (subSt) subSt.textContent = `Subtitles: ${activeSubLabel || 'Loaded'}`;
        } else {
            track.removeAttribute('src');
            if (subSt) subSt.textContent = 'Subtitles: None';
        }
    }

    function skipInlineIntro(delta) {
        const vid = document.getElementById('inlineVideoEl');
        if (!vid || isNaN(vid.currentTime)) return;
        const nxt = Math.max(0, vid.currentTime + (delta || 85));
        vid.currentTime = (vid.duration && !isNaN(vid.duration)) ? Math.min(vid.duration - 1, nxt) : nxt;
        showToast('Intro Skipped (+85s)');
    }

    function cancelInlineNext() {
        inlineNextCancelled = true;
        clearInterval(inlineNextTimer);
        inlineNextTimer = null;
        const b = document.getElementById('inlineNextBanner');
        if (b) b.classList.remove('open');
    }

    function triggerInlineNextNow() {
        cancelInlineNext();
        closeInlinePlayer();
        playNextEpisode();
    }

    function openInlinePlayer() {
        if (!activeStreamUrl) return;
        const ov = document.getElementById('inlineWebPlayerModal');
        const vid = document.getElementById('inlineVideoEl');
        const title = document.getElementById('inlinePlayerTitle');
        const note = document.getElementById('inlinePlayerNote');
        const nextBtn = document.getElementById('btnInlineNextEp');
        if (!ov || !vid) return;
        title.textContent = activeTitle || 'StreamHub Inline Player';
        const src = (activeCompressMethod === -1)
            ? (activeSid ? (`/api/hls/playlist.m3u8?sid=${encodeURIComponent(activeSid)}&t_ms=0`) : activeStreamUrl)
            : (activeSid ? ('/s/' + activeSid + '.mkv') : activeStreamUrl);
        note.textContent = (activeCompressMethod === -1)
            ? 'HLS Adaptive Stream — Full Audio + Parallel Prefetch Seek active'
            : 'MKV Browser Preview — if DDP/Atmos audio is muted, click "Play in StreamHub In-Built Player"';

        inlineNextCancelled = false;
        clearInterval(inlineNextTimer);
        const nxtBanner = document.getElementById('inlineNextBanner');
        if (nxtBanner) nxtBanner.classList.remove('open');
        const hasNext = !!getNextEpisodePayload();
        if (nextBtn) nextBtn.style.display = hasNext ? 'inline-flex' : 'none';
        applySubtitleToInlineVideo();

        const resumeTarget = getActiveResumeSec();
        let resumeApplied = false;
        lastInlineSaveTs = 0;

        vid.onerror = () => {
            if (activeCompressMethod !== -1) {
                note.textContent = 'Browser cannot play HEVC/Dolby MKV — please use "Play in StreamHub In-Built Player".';
                showToast('Play in StreamHub In-Built Player (4K HEVC/Dolby support)', 'err');
            }
        };
        vid.onloadedmetadata = () => {
            if (!resumeApplied && resumeTarget >= 10 && (!vid.duration || resumeTarget < vid.duration - 20)) {
                resumeApplied = true;
                try { vid.currentTime = resumeTarget; showToast(`Resumed at ${fmtResumeShort(resumeTarget)}`); } catch (e) {}
            }
            applySubtitleToInlineVideo();
        };
        vid.ontimeupdate = () => {
            const cur = Math.floor(vid.currentTime || 0);
            const dur = isFinite(vid.duration) ? Math.floor(vid.duration || 0) : 0;
            const nowMs = Date.now();
            if (cur >= 5 && nowMs - lastInlineSaveTs > 4500 && activeMediaKey) {
                lastInlineSaveTs = nowMs;
                saveProgressClient({
                    key: activeMediaKey,
                    parent_key: activeParentKey,
                    ep_label: activeEpLabel,
                    season_idx: activeSeriesSeasonIdx,
                    ep_idx: activeSeriesEpIdx,
                    zip_index: activeZipIndex,
                    sid: activeSid,
                    position_sec: cur,
                    duration_sec: dur,
                    title: activeTitle,
                });
            }
            if (hasNext && !inlineNextCancelled && dur > 90 && (dur - cur <= 15) && !inlineNextTimer) {
                startInlineNextCountdown();
            }
        };
        vid.onended = () => {
            if (hasNext && !inlineNextCancelled) startInlineNextCountdown();
        };

        if (window._inlineHls) { try { window._inlineHls.destroy(); } catch (e) {} window._inlineHls = null; }
        ov.classList.add('open');
        if (src.includes('.m3u8') && typeof Hls !== 'undefined' && Hls.isSupported()) {
            const hls = new Hls({
                enableWorker: true,
                lowLatencyMode: false,
                backBufferLength: 60,
                maxBufferLength: 24,
                fragLoadingTimeOut: 35000,
                manifestLoadingTimeOut: 20000,
            });
            hls.loadSource(src);
            hls.attachMedia(vid);
            hls.on(Hls.Events.MANIFEST_PARSED, () => vid.play().catch(() => {}));
            hls.on(Hls.Events.ERROR, (_ev, data) => {
                if (data && data.fatal) {
                    if (data.type === Hls.ErrorTypes.NETWORK_ERROR) hls.startLoad();
                    else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
                }
            });
            window._inlineHls = hls;
        } else {
            vid.src = src;
            vid.play().catch(() => {});
        }
    }

    function startInlineNextCountdown() {
        const b = document.getElementById('inlineNextBanner');
        const txt = document.getElementById('inlineNextText');
        if (!b || !txt) return;
        inlineNextSec = 5;
        txt.textContent = `Next Episode in ${inlineNextSec}s...`;
        b.classList.add('open');
        clearInterval(inlineNextTimer);
        inlineNextTimer = setInterval(() => {
            inlineNextSec -= 1;
            if (inlineNextSec <= 0) {
                triggerInlineNextNow();
            } else {
                txt.textContent = `Next Episode in ${inlineNextSec}s...`;
            }
        }, 1000);
    }

    function closeInlinePlayer() {
        const ov = document.getElementById('inlineWebPlayerModal');
        const vid = document.getElementById('inlineVideoEl');
        clearInterval(inlineNextTimer);
        inlineNextTimer = null;
        if (vid && activeMediaKey && (vid.currentTime || 0) >= 5) {
            saveProgressClient({
                key: activeMediaKey,
                parent_key: activeParentKey,
                ep_label: activeEpLabel,
                season_idx: activeSeriesSeasonIdx,
                ep_idx: activeSeriesEpIdx,
                zip_index: activeZipIndex,
                sid: activeSid,
                position_sec: Math.floor(vid.currentTime || 0),
                duration_sec: isFinite(vid.duration) ? Math.floor(vid.duration || 0) : 0,
                title: activeTitle,
            });
        }
        if (window._inlineHls) { try { window._inlineHls.destroy(); } catch (e) {} window._inlineHls = null; }
        if (vid) {
            vid.onerror = null; vid.ontimeupdate = null; vid.onloadedmetadata = null; vid.onended = null;
            try { vid.pause(); } catch (e) {}
            vid.removeAttribute('src'); vid.load();
        }
        if (ov) ov.classList.remove('open');
        const b = document.getElementById('inlineNextBanner');
        if (b) b.classList.remove('open');
    }

    window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeInlinePlayer();
        const ov = document.getElementById('inlineWebPlayerModal');
        if (ov && ov.classList.contains('open') && (e.key === 's' || e.key === 'S')) {
            const tag = (document.activeElement && document.activeElement.tagName) || '';
            if (tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'SELECT') {
                e.preventDefault();
                skipInlineIntro(85);
            }
        }
    });

    async function launchAndroidPlayerWithPayload(payload, forceExternal) {
        showToast(forceExternal ? 'Opening External VLC...' : 'Starting StreamHub Cinema Player...');
        try {
            const resp = await fetch('/api/prepare_play', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload || {})
            });
            const data = await resp.json();
            if (!data || !data.success || !data.target) {
                if (data && data.vip_required) {
                    refreshVipStatus(false);
                    openVipUnlockModal(false, Boolean(data.upgrade_4k_required));
                }
                showToast('Player Error: ' + ((data && (data.message || data.error)) || 'Stream is not ready'), 'err');
                return;
            }
            if (forceExternal && typeof window.StreamHubAndroid.openExternalVlc === 'function') {
                window.StreamHubAndroid.openExternalVlc(String(data.target), String(data.title || 'StreamHub'));
            } else {
                window.StreamHubAndroid.playMedia(JSON.stringify(data));
            }
        } catch (e) {
            showToast('Player Launch Error: ' + e.message, 'err');
        }
    }

    async function launchVlcCurrent(forceExternal, startZero) {
        if (!activeStreamUrl) { showToast('Please resolve a movie link first', 'err'); return; }
        if (activeSid) markWatched(activeSid);
        if (!isAndroidApp() && isMobileDevice()) {
            if (activeSid) {
                openInPhoneVlc(activeSid, activeTitle);
                return;
            }
            const sid = await ensureSid(activeStreamUrl, activeSeekUrl, activeBaseOff, activeSubSize, activeCompressMethod, activeTitle);
            if (sid) {
                window.location.href = '/phone/' + sid;
                return;
            }
        }
        const track = document.getElementById('audioTrackSelect').value || 1;
        const startTime = startZero ? 0 : getActiveResumeSec();
        playInVlcCustom(
            encodeURIComponent(activeStreamUrl),
            track,
            encodeURIComponent(activeSeekUrl),
            activeBaseOff,
            activeSubSize,
            activeCompressMethod,
            activeSid,
            !!forceExternal,
            startTime
        );
    }

    function launchVlcFromStart() {
        launchVlcCurrent(false, true);
    }

    function launchExternalVlcCurrent() {
        launchVlcCurrent(true);
    }

    async function ensureSid(streamUrl, seekUrl, baseOff, subSize, cm, title) {
        try {
            const resp = await fetch('/api/register_stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    stream_url: streamUrl,
                    seek_url: seekUrl || '',
                    base_offset: baseOff || 0,
                    sub_size: (subSize !== undefined && subSize !== null) ? subSize : null,
                    compress_method: cm || 0,
                    title: title || activeTitle || '4K Stream'
                })
            });
            const data = await resp.json();
            return data.sid || '';
        } catch (e) {
            return '';
        }
    }

    async function playInVlcCustom(encodedUrl, track, encodedSeekUrl, baseOff, subSize, cm, sid, forceExternal, startTimeOverride) {
        if (!requireVipAccess()) return;
        const url = decodeURIComponent(encodedUrl);
        const seekUrl = encodedSeekUrl ? decodeURIComponent(encodedSeekUrl) : "";
        if (!isAndroidApp() && isMobileDevice()) {
            if (sid) {
                openInPhoneVlc(sid, activeTitle);
                return;
            }
            const newSid = await ensureSid(url, seekUrl, baseOff, subSize, cm, activeTitle);
            if (newSid) {
                window.location.href = '/phone/' + newSid;
                return;
            }
        }
        const trk = track || document.getElementById('audioTrackSelect').value || 1;
        const subEl = document.getElementById('subTrackSelect');
        const subTrk = subEl ? subEl.value : 'auto';
        const startTime = (startTimeOverride !== undefined) ? Number(startTimeOverride || 0) : getActiveResumeSec();
        const reqPayload = {
            stream_url: url,
            seek_url: seekUrl,
            audio_track: parseInt(trk),
            sub_track: subTrk,
            base_offset: baseOff || 0,
            sub_size: (subSize !== undefined && subSize !== null) ? subSize : null,
            compress_method: cm || 0,
            sid: sid || activeSid || '',
            title: activeTitle || 'StreamHub Cinema',
            start_time: startTime,
            media_key: activeMediaKey || sid || activeTitle,
            parent_key: activeParentKey || '',
            ep_label: activeEpLabel || '',
            subtitle_file: activeSubFilePath || '',
            subtitle_key: activeSubKey || '',
            next_episode: getNextEpisodePayload()
        };
        if (isAndroidApp()) {
            return launchAndroidPlayerWithPayload(reqPayload, !!forceExternal);
        }
        const endpoint = forceExternal ? '/api/play_vlc' : '/api/play_inbuilt';
        try {
            const resp = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(reqPayload)
            });
            const data = await resp.json();
            if (data.success) {
                showToast(forceExternal ? 'External VLC launched — Seekbar ready' : (startTime >= 10 ? `Resuming at ${fmtResumeShort(startTime)} in In-Built Player!` : 'StreamHub In-Built Cinema Player opened!'));
            } else {
                showToast('Player Error: ' + (data.message || data.error || 'Could not open player'), 'err');
            }
        } catch (e) {
            showToast('Error: ' + e.message, 'err');
        }
    }

    async function findOnlineSubtitles() {
        const subInEl = document.getElementById('subQueryInput');
        const q = ((subInEl && subInEl.value) || activeTitle || '').trim();
        const box = document.getElementById('subResultsList');
        const btn = document.getElementById('btnFindSubs');
        if (!box) return;
        box.style.display = 'flex';
        box.innerHTML = '<div style="font-size:0.78rem;color:var(--text-muted);padding:4px 0;">Searching OpenSubtitles .srt tracks...</div>';
        if (btn) btn.disabled = true;
        try {
            const epMatch = (activeEpLabel || q).match(/E(?:P)?\s*(\d+)/i);
            const sMatch = q.match(/S\s*(\d+)/i);
            const params = new URLSearchParams({
                q: q,
                imdb_id: activeImdbId || '',
                season: sMatch ? sMatch[1] : (activeSeriesSeasonIdx >= 0 && activeSeriesData ? String(activeSeriesSeasonIdx + 1) : ''),
                episode: epMatch ? epMatch[1] : ''
            });
            const resp = await fetch(`/api/subtitles/search?${params.toString()}`);
            const data = await resp.json();
            box.innerHTML = '';
            const list = (data && data.subtitles) || [];
            if (!list.length) {
                box.innerHTML = `<div style="font-size:0.76rem;color:#fca5a5;padding:4px 0;">${esc((data && data.error) || 'No online .srt found — use "Local .srt" to upload your own file')}</div>`;
            } else {
                list.slice(0, 12).forEach(s => {
                    const row = document.createElement('div');
                    row.className = 'sub-item';
                    row.innerHTML = `
                        <span class="sub-item-lbl">${esc(s.label)}</span>
                        <button type="button" class="btn-sm btn-vlc-sm" onclick="loadOnlineSubtitle('${encodeURIComponent(s.url)}','${esc(s.label)}','${esc(s.lang)}')">Load .srt</button>
                    `;
                    box.appendChild(row);
                });
            }
        } catch (e) {
            box.innerHTML = '<div style="font-size:0.76rem;color:#fca5a5;">Subtitle search error</div>';
        }
        if (btn) btn.disabled = false;
    }

    async function loadOnlineSubtitle(encodedUrl, label, lang) {
        const subUrl = decodeURIComponent(encodedUrl);
        showToast('Downloading .srt subtitle...');
        try {
            const resp = await fetch('/api/subtitles/load', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: subUrl, label: label || 'Subtitle', lang: lang || 'English', apply_inbuilt: true })
            });
            const data = await resp.json();
            if (data && data.success) {
                activeSubFilePath = data.srt_path || '';
                activeSubKey = data.sub_key || '';
                activeSubVttUrl = data.vtt_url || '';
                activeSubLabel = data.label || label || 'Subtitle';
                const badge = document.getElementById('activeSubBadge');
                if (badge) badge.textContent = `Loaded: ${activeSubLabel}`;
                applySubtitleToInlineVideo();
                showToast(data.inbuilt_applied ? 'Subtitle attached live!' : 'Subtitle ready — will auto-load in player');
            } else {
                showToast('Subtitle load failed: ' + ((data && data.error) || ''), 'err');
            }
        } catch (e) {
            showToast('Subtitle error: ' + e.message, 'err');
        }
    }

    async function handleLocalSrtFile(inp) {
        const f = inp && inp.files && inp.files[0];
        if (!f) return;
        try {
            const text = await f.text();
            const resp = await fetch('/api/subtitles/load_local', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ srt_content: text, label: f.name, lang: 'Local', apply_inbuilt: true })
            });
            const data = await resp.json();
            if (data && data.success) {
                activeSubFilePath = data.srt_path || '';
                activeSubKey = data.sub_key || '';
                activeSubVttUrl = data.vtt_url || '';
                activeSubLabel = f.name;
                const badge = document.getElementById('activeSubBadge');
                if (badge) badge.textContent = `Loaded: ${f.name}`;
                applySubtitleToInlineVideo();
                showToast(`Local subtitle "${f.name}" loaded!`);
            }
        } catch (e) {
            showToast('Error reading local .srt file: ' + e.message, 'err');
        }
        inp.value = '';
    }

    const THEME_META = {
        netflix: { name: 'Netflix Crimson', color: '#05060b' },
        amoled: { name: 'AMOLED Pure Black', color: '#000000' },
        prime: { name: 'Prime Sapphire', color: '#040914' },
        gold: { name: 'Royal Theater Gold', color: '#08070d' }
    };

    function fmtBytes(bytes) {
        const b = Number(bytes || 0);
        if (b <= 0) return '0 MB';
        if (b >= 1024 * 1024 * 1024) return (b / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
        return (b / (1024 * 1024)).toFixed(b >= 100 * 1024 * 1024 ? 0 : 1) + ' MB';
    }

    function autoSelectPreferredAudioOption() {
        const sel = document.getElementById('audioTrackSelect');
        const pref = String((userSettingsState.profile && userSettingsState.profile.preferred_language) || 'Auto').toLowerCase();
        if (!sel || !pref || pref === 'auto') return;
        for (let i = 0; i < sel.options.length; i++) {
            const txt = String(sel.options[i].textContent || '').toLowerCase();
            if (txt.includes(pref)) {
                sel.selectedIndex = i;
                return;
            }
        }
    }

    function applyThemeUi(themeId) {
        const th = THEME_META[themeId] ? themeId : 'netflix';
        if (th === 'netflix') document.documentElement.removeAttribute('data-theme');
        else document.documentElement.setAttribute('data-theme', th);
        const mc = document.querySelector('meta[name="theme-color"]');
        if (mc) mc.setAttribute('content', THEME_META[th].color);
        try { localStorage.setItem('sh_theme', th); } catch (e) {}
        document.querySelectorAll('#themePickerGrid .theme-card').forEach(btn => {
            btn.classList.toggle('active', btn.getAttribute('data-theme-id') === th);
        });
        const badge = document.getElementById('activeThemeBadge');
        if (badge) badge.textContent = THEME_META[th].name;
        const mSub = document.getElementById('menuSubTheme');
        if (mSub) mSub.textContent = THEME_META[th].name;
    }

    function applyProfileUi(profile) {
        const p = profile || {};
        const rawAv = p.avatar || 'film';
        const av = _LEGACY_AV_MAP[rawAv] || (_SH_ICONS[rawAv] ? rawAv : 'film');
        const nm = p.name || 'StreamHub User';
        const lg = p.preferred_language || 'Auto';
        const hdAv = document.getElementById('settingsHeadAvatar');
        if (hdAv) hdAv.innerHTML = _shAvatarSvg(av);
        const hdSub = document.getElementById('settingsHeadSub');
        if (hdSub) hdSub.textContent = `${nm} • Audio: ${lg} • v${userSettingsState.version || '2.5.4'}`;
        const mProf = document.getElementById('menuSubProfile');
        if (mProf) mProf.textContent = `${nm} • Audio: ${lg}`;
        const nmInp = document.getElementById('setProfileName');
        if (nmInp && document.activeElement !== nmInp) nmInp.value = nm;
        const lgSel = document.getElementById('setProfileLang');
        if (lgSel) lgSel.value = lg;
        document.querySelectorAll('#avatarPickerGrid .avatar-chip').forEach(btn => {
            btn.classList.toggle('active', btn.getAttribute('data-av') === av);
        });
    }

    function renderStorageSummaryUi(st, prefs) {
        const s = st || userSettingsState.storage || {};
        const p = prefs || userSettingsState.download_prefs || {};
        const used = Number(s.used_bytes || 0);
        const limGb = Number(p.storage_limit_gb !== undefined ? p.storage_limit_gb : (s.limit_gb || 4));
        const limBytes = limGb > 0 ? limGb * 1024 * 1024 * 1024 : 0;
        const pct = limBytes > 0 ? Math.min(100, Math.round((used * 100) / limBytes)) : Math.min(100, Math.round(used / (100 * 1024 * 1024)));
        const limLabel = limGb > 0 ? `${limGb} GB` : 'Unlimited';
        const txt = `Used: ${fmtBytes(used)} / ${limLabel}`;

        const stEl = document.getElementById('setStorageText');
        if (stEl) stEl.textContent = txt;
        const bf = document.getElementById('setStorageBarFill');
        if (bf) bf.style.width = pct + '%';
        const cntEl = document.getElementById('setDownloadCountText');
        if (cntEl) cntEl.textContent = `${s.completed_count || 0} Ready • ${s.active_count || 0} Active`;

        const dlTxt = document.getElementById('dlModalStorageText');
        if (dlTxt) dlTxt.textContent = txt;
        const dlFill = document.getElementById('dlModalStorageFill');
        if (dlFill) dlFill.style.width = pct + '%';
        const dlPol = document.getElementById('dlModalPolicyText');
        if (dlPol) dlPol.textContent = `Auto-Delete Watched: ${p.auto_delete_watched ? 'ON' : 'OFF'} • Expire: ${Number(p.auto_expire_days) > 0 ? (p.auto_expire_days + 'd') : 'Never'}`;

        const capSel = document.getElementById('setStorageCap');
        if (capSel) capSel.value = String(limGb);
        const autoChk = document.getElementById('setAutoDelWatched');
        if (autoChk) autoChk.checked = Boolean(p.auto_delete_watched);
        const expSel = document.getElementById('setExpireDays');
        if (expSel) expSel.value = String(p.auto_expire_days !== undefined ? p.auto_expire_days : 7);
        const dqSel = document.getElementById('setDefaultDlQuality');
        if (dqSel) dqSel.value = String(p.default_quality || '720p');
        const sqSel = document.getElementById('setDefaultStreamQuality');
        if (sqSel) sqSel.value = String(p.stream_quality || 'auto');

        const badge = document.getElementById('navDownloadsBadge');
        const dockBadge = document.getElementById('dockDownloadsBadge');
        const totCnt = Number(s.total_count || 0);
        const activeCnt = Number(s.active_count || 0);
        let pctLabel = String(totCnt);
        const actItem = offlineDownloadsList.find(x => x.status === 'downloading' || x.status === 'queued');
        if (actItem && activeCnt > 0) {
            pctLabel = Math.round(Number(actItem.pct || 0)) + '%';
        }
        if (badge) {
            badge.style.display = totCnt > 0 ? 'inline-flex' : 'none';
            badge.textContent = pctLabel;
        }
        if (dockBadge) {
            dockBadge.style.display = totCnt > 0 ? 'inline-flex' : 'none';
            dockBadge.textContent = pctLabel;
        }
        const libCnt = document.getElementById('libDlCount');
        if (libCnt) libCnt.textContent = `(${totCnt})`;
        const homeCnt = document.getElementById('homeDlCountPill');
        if (homeCnt) homeCnt.textContent = (s.active_count > 0) ? `(${Math.round(actItem ? actItem.pct : 0)}% active)` : `(${totCnt})`;
        const mDlPrefs = document.getElementById('menuSubDlPrefs');
        if (mDlPrefs) {
            const expTxt = Number(p.auto_expire_days) > 0 ? `${p.auto_expire_days}d` : 'Never';
            mDlPrefs.textContent = `Used: ${fmtBytes(used)} / ${limLabel} • ${String(p.default_quality || '720p').toUpperCase()} • Expire ${expTxt}`;
        }
        const mMyDl = document.getElementById('menuSubMyDownloads');
        if (mMyDl) {
            mMyDl.textContent = (activeCnt > 0 && actItem)
                ? `Downloading ${Math.round(actItem.pct || 0)}% (${fmtBytes(actItem.downloaded_bytes)})`
                : `${s.completed_count || 0} Ready • ${s.paused_count || 0} Paused`;
        }

        const needsWake = activeCnt > 0;
        if (lastWakeLockActive !== needsWake) {
            lastWakeLockActive = needsWake;
            if (window.StreamHubAndroid && typeof window.StreamHubAndroid.setDownloadWakeLock === 'function') {
                try { window.StreamHubAndroid.setDownloadWakeLock(needsWake); } catch (e) {}
            }
        }
    }

    async function initSettingsAndDownloads() {
        try {
            const resp = await fetch('/api/settings');
            const data = await resp.json();
            if (data && data.success) {
                userSettingsState = data;
                applyThemeUi(data.theme || 'netflix');
                applyProfileUi(data.profile);
                renderStorageSummaryUi(data.storage, data.download_prefs);
            }
        } catch (e) {}
        await refreshDownloadsState(false);
    }

    const SH_FULL_PAGES = [
        'vipUnlockModal', 'startupUpdateModal', 'settingsModalOverlay', 'profilePageModal', 'themePageModal',
        'dlSettingsPageModal', 'toolsPageModal', 'updateModalOverlay', 'downloadsModalOverlay', 'hub4kPageModal'
    ];

    let vipState = {
        active: false, remaining_sec: 0, unlockUntilPerfMs: 0, code: '', label: '',
        max_devices: 1, allow_4k: false, allow_download: false, price_inr: null, device_id: '', reason: '',
        plans: [
            { id: '15d', name: '15 Days', validity: '15 Days', sub: 'HD / 1080p Streaming', duration_seconds: 1296000, price_inr: 29, allow_4k: false, allow_download: false, badge: 'Streaming Only' },
            { id: '30d', name: '1 Month', validity: '30 Days', sub: 'HD / 1080p Streaming', duration_seconds: 2592000, price_inr: 49, allow_4k: false, allow_download: false, badge: 'Streaming Only' },
            { id: '3m', name: '3 Months', validity: '90 Days (3 Months)', sub: 'HD / 1080p + Downloads', duration_seconds: 7776000, price_inr: 119, allow_4k: false, allow_download: true, badge: 'Offline Downloads' },
            { id: '6m', name: '6 Months', validity: '180 Days (6 Months)', sub: 'Downloads + 4K HDR', duration_seconds: 15552000, price_inr: 199, allow_4k: true, allow_download: true, badge: '4K + Downloads' },
            { id: '1y', name: '1 Year', validity: '365 Days (1 Year)', sub: 'All 4K + Downloads', duration_seconds: 31536000, price_inr: 349, allow_4k: true, allow_download: true, badge: 'Best Value • 4K' }
        ]
    };
    const VIP_PURCHASE_EMAIL = 'khushalsharma4543@gmail.com';
    let pendingVipAction = null;
    let vipTickerTimer = null;
    let selectedPlanIdUi = '6m';
    let currentVipUpgradeMode = false;

    function _vipNormalizeUpgradeMode(mode) {
        if (mode === '4k' || mode === true) return '4k';
        if (mode === 'download') return 'download';
        return false;
    }

    function _vipPlanMailtoUrl(p) {
        const plan = p || (vipState.plans || []).find(x => x.id === (selectedPlanIdUi || '6m')) || (vipState.plans || [])[0] || {
            id: '6m', name: '6 Months', validity: '180 Days (6 Months)', price_inr: 199, allow_4k: true, allow_download: true
        };
        const devId = vipState.device_id || 'Pending Device ID';
        const validity = plan.validity || plan.name;
        const subject = `StreamHub VIP Plan Purchase - ${plan.name} (Rs ${plan.price_inr})`;
        const bodyLines = [
            'Hello StreamHub Support,',
            '',
            'I would like to purchase the following StreamHub VIP Access Key:',
            `Plan: ${plan.name}`,
            `Validity: ${validity}`,
            `Price: Rs ${plan.price_inr}`,
            `HD / 1080p Streaming: Included`,
            `Offline Movie Downloads: ${plan.allow_download ? 'Included (Unlocked)' : 'Locked (Requires 3M+ Plan)'}`,
            `4K UHD & 4K Cinema Vault: ${plan.allow_4k ? 'Included (Unlocked)' : 'Locked (Requires 6M+ Plan)'}`,
            `Device Limit: 1 Device`,
            `Device ID: ${devId}`,
            '',
            'Please share payment instructions and send my VIP Access Key.',
            'Thank you!'
        ];
        return `mailto:${VIP_PURCHASE_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(bodyLines.join('\r\n'))}`;
    }

    function buyVipPlanViaEmail(planId, ev) {
        if (ev) { ev.stopPropagation(); ev.preventDefault(); }
        const plans = vipState.plans || [];
        const plan = plans.find(x => x.id === planId) || plans.find(x => x.id === selectedPlanIdUi) || plans[0];
        if (!plan) return false;
        selectedPlanIdUi = plan.id;
        _updateSelectedPlanNote(plan);
        _renderVipPlansGrid(currentVipUpgradeMode);
        const mailtoUrl = _vipPlanMailtoUrl(plan);
        showToast(`Opening email to ${VIP_PURCHASE_EMAIL} for ${plan.name} (Rs ${plan.price_inr})...`, 'ok');
        if (window.StreamHubAndroid) {
            try {
                if (typeof window.StreamHubAndroid.openEmailComposer === 'function') {
                    window.StreamHubAndroid.openEmailComposer(mailtoUrl);
                    return false;
                }
                if (typeof window.StreamHubAndroid.openExternalUrl === 'function') {
                    window.StreamHubAndroid.openExternalUrl(mailtoUrl);
                    return false;
                }
            } catch (e) {}
        }
        window.location.href = mailtoUrl;
        return false;
    }

    function _updateSelectedPlanNote(p) {
        const note = document.getElementById('vipSelectedPlanNote');
        if (!note || !p) return;
        const feats = p.allow_4k
            ? 'HD/1080p Streaming + Offline Downloads + 4K UHD & 4K Cinema Vault Unlocked'
            : (p.allow_download
                ? 'HD/1080p Streaming + Offline Downloads Unlocked (4K Locked — needs 6M+)'
                : 'HD/1080p Streaming Only (Offline Downloads need 3M+, 4K needs 6M+)');
        note.innerHTML = `<strong>Selected: ${esc(p.name)} (&#8377;${p.price_inr})</strong> &bull; ${esc(p.validity || p.name)} &mdash; ${esc(feats)}. Tap <strong>Buy Plan via Email</strong> to request your key from ${VIP_PURCHASE_EMAIL}, then paste it above.`;
        note.style.display = 'block';
    }

    function _is4kText(txt) {
        return /(?:2160p|ds4k|uhd|(?:^|[^a-z0-9])4k(?:uhd|hdr|sdr|remux|bluray)?(?=$|[^a-z0-9]))/i.test(String(txt || ''));
    }
    function _hasHdNon4kText(txt) {
        return /\b(?:1080p|720p|480p|360p)\b/i.test(String(txt || ''));
    }
    function _is4kVaultItem(m) {
        if (!m) return false;
        const src = String(m.source || '').toLowerCase();
        const pl = String(m.permalink || m.url || '').toLowerCase();
        return src === '4khdhub' || /4khdhub\./.test(pl);
    }
    function _is4kExclusiveItem(m) {
        if (!m) return false;
        if (_is4kVaultItem(m)) return true;
        const combined = `${m.title || ''} ${m.card_tag || ''} ${m.badges || ''}`;
        return _is4kText(combined) && !_hasHdNon4kText(combined);
    }
    function _isMultiQuality4kItem(m) {
        if (!m || _is4kExclusiveItem(m)) return false;
        const combined = `${m.title || ''} ${m.card_tag || ''} ${m.badges || ''}`;
        return _is4kText(combined) && _hasHdNon4kText(combined);
    }
    function _syncHeroVipState() {
        const heroLbl = document.getElementById('btnHeroPlayLabel');
        const qBadge = document.getElementById('heroQualityBadge');
        const m = (typeof heroItems !== 'undefined' && heroItems.length) ? heroItems[(heroIdx || 0) % heroItems.length] : null;
        const isActive = Boolean(vipState.active && _vipRemainingNow() > 0);
        const is4kLocked = isActive && !vipState.allow_4k;
        if (qBadge && m) {
            if (is4kLocked && _is4kExclusiveItem(m)) qBadge.textContent = '4K LOCKED (6M+)';
            else if (is4kLocked && _isMultiQuality4kItem(m)) qBadge.textContent = 'HD • 4K Locked';
            else qBadge.textContent = m.card_tag || (_is4kText(m.title) ? '4K UHD' : 'FULL HD');
        }
        if (heroLbl) {
            if (!isActive) heroLbl.textContent = 'Unlock to Watch';
            else if (is4kLocked && m && _is4kExclusiveItem(m)) heroLbl.textContent = 'Upgrade 6M+ (4K)';
            else heroLbl.textContent = 'Watch Now';
        }
    }

    function _renderVipPlansGrid(upgradeMode) {
        const grid = document.getElementById('vipPlansGrid');
        if (!grid) return;
        const mode = _vipNormalizeUpgradeMode(upgradeMode);
        const plans = vipState.plans || [];
        grid.innerHTML = '';
        plans.forEach(p => {
            if (mode === '4k' && vipState.active && !vipState.allow_4k && !p.allow_4k) return;
            if (mode === 'download' && vipState.active && !vipState.allow_download && !p.allow_download) return;
            const isDl = Boolean(p.allow_download);
            const is4k = Boolean(p.allow_4k);
            const card = document.createElement('div');
            card.className = 'vip-plan-item' + (is4k ? ' ultra' : '') + (selectedPlanIdUi === p.id ? ' selected' : '');
            card.setAttribute('role', 'button');
            card.tabIndex = 0;
            const badgeCls = is4k ? 'ultra' : (isDl ? 'dl' : '');
            const badgeTxt = p.badge || (is4k ? '4K + Downloads' : (isDl ? 'Offline Downloads' : 'Streaming Only'));
            const validityTxt = p.validity || p.name;
            const featHd = '<li class="vip-feat-yes">&#10003; Full HD / 1080p Streaming</li>';
            const featDl = isDl
                ? '<li class="vip-feat-yes">&#10003; Offline Movie &amp; Series Downloads</li>'
                : '<li class="vip-feat-no">&#10005; Offline Downloads (Requires 3M+ Plan)</li>';
            const feat4k = is4k
                ? '<li class="vip-feat-yes">&#10003; 4K UHD, HDR &amp; 4K Cinema Vault</li>'
                : '<li class="vip-feat-no">&#10005; 4K UHD &amp; Cinema Vault (Requires 6M+ Plan)</li>';
            const featDev = '<li class="vip-feat-yes">&#10003; 1 Key = 1 Device Lock</li>';
            const mailtoHref = _vipPlanMailtoUrl(p);

            card.innerHTML = `
                <div>
                    <div class="vip-plan-head">
                        <div class="vip-plan-name">${esc(p.name)}</div>
                        <span class="vip-plan-badge ${badgeCls}">${esc(badgeTxt)}</span>
                    </div>
                    <div class="vip-plan-price-row" style="margin-top:6px;">
                        <div class="vip-plan-price">&#8377;${p.price_inr}</div>
                        <div class="vip-plan-validity">Validity: ${esc(validityTxt)}</div>
                    </div>
                    <ul class="vip-plan-feats" style="margin-top:10px;">
                        ${featHd}${featDl}${feat4k}${featDev}
                    </ul>
                </div>
                <a href="${esc(mailtoHref)}" class="vip-plan-buy-btn" onclick="return buyVipPlanViaEmail('${esc(p.id)}', event)">Buy ${esc(p.name)} via Email (&#8377;${p.price_inr})</a>
            `;
            const selectPlan = () => {
                selectedPlanIdUi = p.id;
                _updateSelectedPlanNote(p);
                _renderVipPlansGrid(mode);
            };
            card.onclick = selectPlan;
            card.onkeydown = (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    selectPlan();
                }
            };
            grid.appendChild(card);
        });
    }

    function _fmtVipRem(sec, compact) {
        const s = Math.max(0, Math.ceil(Number(sec || 0)));
        if (s < 60) return `${s}s`;
        if (s < 3600) {
            const m = Math.floor(s / 60);
            const r = s % 60;
            return (compact || !r) ? `${m}m` : `${m}m ${r}s`;
        }
        if (s < 86400) {
            const h = Math.floor(s / 3600);
            const m = Math.floor((s % 3600) / 60);
            return (compact || !m) ? `${h}h` : `${h}h ${m}m`;
        }
        const d = Math.floor(s / 86400);
        const h = Math.floor((s % 86400) / 3600);
        return (compact || !h) ? `${d}d` : `${d}d ${h}h`;
    }

    function _vipRemainingNow() {
        if (!vipState.active || !vipState.unlockUntilPerfMs) return 0;
        return Math.max(0, (vipState.unlockUntilPerfMs - performance.now()) / 1000);
    }

    function applyVipUiState(upgradeModeArg) {
        const upgMode = _vipNormalizeUpgradeMode(upgradeModeArg);
        currentVipUpgradeMode = upgMode;
        const rem = _vipRemainingNow();
        const isActive = Boolean(vipState.active && rem > 0);
        if (!isActive && vipState.active) {
            vipState.active = false;
            vipState.allow_4k = false;
            vipState.allow_download = false;
            vipState.remaining_sec = 0;
            vipState.reason = vipState.reason || 'VIP Key has expired — please enter a new key';
        }
        const is4kLocked = isActive && !vipState.allow_4k;
        const isDlLocked = isActive && !vipState.allow_download;
        document.body.classList.toggle('sh-vip-locked', !isActive);
        document.body.classList.toggle('sh-vip-4k-locked', is4kLocked);
        document.body.classList.toggle('sh-vip-dl-locked', isDlLocked);

        const navPill = document.getElementById('btnVipNavPill');
        const navLbl = document.getElementById('btnVipNavLabel');
        if (navPill) {
            navPill.classList.toggle('unlocked', isActive && vipState.allow_4k);
            navPill.classList.toggle('hd-only', is4kLocked);
        }
        if (navLbl) {
            const compactNav = window.innerWidth <= 480;
            const remTxt = _fmtVipRem(rem, compactNav);
            if (!isActive) navLbl.textContent = 'VIP Locked';
            else if (vipState.allow_4k) navLbl.textContent = compactNav ? `4K ${remTxt}` : `4K VIP ${remTxt}`;
            else if (vipState.allow_download) navLbl.textContent = compactNav ? `DL ${remTxt}` : `VIP+DL ${remTxt}`;
            else navLbl.textContent = compactNav ? `HD ${remTxt}` : `HD VIP ${remTxt}`;
        }

        _syncHeroVipState();

        const dlBan = document.getElementById('downloadsVipBanner');
        if (dlBan) dlBan.style.display = (!isActive || isDlLocked) ? 'block' : 'none';
        const dlBtnDetails = document.getElementById('btnDownloadQuality');
        if (dlBtnDetails) dlBtnDetails.title = (!isActive || isDlLocked) ? 'Offline Downloads require a 3M+ VIP Plan' : 'Download for offline viewing';
        const dlBtnActive = document.getElementById('btnOfflineDownloadActive');
        if (dlBtnActive) dlBtnActive.title = (!isActive || isDlLocked) ? 'Offline Downloads require a 3M+ VIP Plan' : 'Save for offline viewing';

        const menuSub = document.getElementById('menuSubVip');
        if (menuSub) {
            if (!isActive) menuSub.textContent = 'Locked • Plans from Rs 29 (15 Days)';
            else if (vipState.allow_4k) menuSub.textContent = `4K Ultra + Downloads • ${_fmtVipRem(rem)} left (${vipState.code})`;
            else if (vipState.allow_download) menuSub.textContent = `HD + Downloads (4K Locked) • ${_fmtVipRem(rem)} left (${vipState.code})`;
            else menuSub.textContent = `HD Streaming Only (DL & 4K Locked) • ${_fmtVipRem(rem)} left (${vipState.code})`;
        }

        const stPill = document.getElementById('vipModalStatePill');
        const devPill = document.getElementById('vipModalDevicePill');
        const entryWrap = document.getElementById('vipKeyEntryWrap');
        const activeWrap = document.getElementById('vipActiveWrap');
        const activeBox = document.getElementById('vipActiveBox');
        const desc = document.getElementById('vipModalDesc');
        const secSub = document.getElementById('vipPlansSectionSub');

        if (stPill) {
            if (!isActive) stPill.textContent = 'Status: Locked';
            else if (vipState.allow_4k) stPill.textContent = `4K VIP • ${_fmtVipRem(rem)} left`;
            else if (vipState.allow_download) stPill.textContent = `DL VIP • ${_fmtVipRem(rem)} (4K Locked)`;
            else stPill.textContent = `HD VIP • ${_fmtVipRem(rem)} (DL & 4K Locked)`;
            stPill.classList.toggle('new', isActive);
        }
        if (devPill) {
            const did = vipState.device_id ? ` • ${String(vipState.device_id).slice(0, 8)}` : '';
            devPill.textContent = `1 Key = ${vipState.max_devices || 1} Device${did}`;
        }
        const showEntry = (!isActive) || (is4kLocked && upgMode === '4k') || (isDlLocked && upgMode === 'download');
        if (entryWrap) entryWrap.style.display = showEntry ? 'block' : 'none';
        if (activeWrap) activeWrap.style.display = isActive ? 'block' : 'none';
        if (desc) {
            if (is4kLocked && upgMode === '4k') {
                desc.textContent = '4K UHD & Cinema Vault are locked on your current plan. Upgrade with a 6 Months (Rs 199) or 1 Year (Rs 349) Access Key below:';
            } else if (isDlLocked && upgMode === 'download') {
                desc.textContent = 'Offline Downloads are locked on 15 Days and 1 Month plans. Upgrade with a 3 Months (Rs 119), 6 Months (Rs 199), or 1 Year (Rs 349) Access Key below:';
            } else if (isActive) {
                if (vipState.allow_4k) desc.textContent = '4K Ultra VIP is active — all HD/1080p streams, offline downloads, and 4K HDR Cinema Vault titles are unlocked!';
                else if (vipState.allow_download) desc.textContent = 'Offline Downloads VIP is active — HD/1080p streaming & offline downloads are unlocked (6M/1Y plan required for 4K).';
                else desc.textContent = 'HD Streaming VIP is active — all HD/1080p streaming is unlocked (3M+ plan required for offline downloads, 6M+ for 4K).';
            } else {
                desc.textContent = vipState.reason || '15 Days & 1 Month unlock HD/1080p streaming only. 3 Months unlocks Offline Downloads. 6 Months & 1 Year unlock 4K HDR.';
            }
        }
        if (secSub) {
            if (is4kLocked && upgMode === '4k') secSub.textContent = 'Showing 4K-eligible plans (6M+)';
            else if (isDlLocked && upgMode === 'download') secSub.textContent = 'Showing Offline Download plans (3M+)';
            else secSub.textContent = '1 Key = 1 Device • Instant Email Request';
        }
        if (activeBox && isActive) {
            const tierSummary = vipState.allow_4k
                ? 'ALL FEATURES + 4K ULTRA + DOWNLOADS UNLOCKED'
                : (vipState.allow_download
                    ? 'HD/1080p STREAMING + OFFLINE DOWNLOADS (4K Locked)'
                    : 'HD/1080p STREAMING ONLY (Downloads Need 3M+ • 4K Needs 6M+)');
            activeBox.textContent = `Key: ${vipState.code}\nPlan: ${vipState.label || 'VIP Pass'}\nAccess Tier: ${tierSummary}\nTime Left: ${_fmtVipRem(rem)} | Device 1/${vipState.max_devices || 1}`;
        }
        const supMail = document.getElementById('vipSupportMailLink');
        if (supMail) supMail.href = _vipPlanMailtoUrl();
        _renderVipPlansGrid(upgMode);
    }

    function _restartVipTicker() {
        if (vipTickerTimer) { clearInterval(vipTickerTimer); vipTickerTimer = null; }
        if (!vipState.active) return;
        const rem0 = _vipRemainingNow();
        const stepMs = rem0 <= 120 ? 250 : 2000;
        vipTickerTimer = setInterval(() => {
            const rem = _vipRemainingNow();
            if (rem <= 0) {
                clearInterval(vipTickerTimer);
                vipTickerTimer = null;
                vipState.active = false;
                vipState.allow_4k = false;
                vipState.allow_download = false;
                vipState.remaining_sec = 0;
                vipState.reason = 'VIP Key time expired — please enter a new key';
                applyVipUiState(false);
                try {
                    const iw = document.getElementById('inlineWebPlayerModal');
                    if (iw && iw.classList.contains('open')) closeInlinePlayer();
                } catch (e) {}
                showToast('VIP Key expired! Please enter a new key', 'err');
                fetch('/api/vip/status').catch(() => {});
                return;
            }
            applyVipUiState(currentVipUpgradeMode);
            if (stepMs > 500 && rem <= 120) _restartVipTicker();
        }, stepMs);
    }

    function _syncVipPayload(d) {
        if (!d || typeof d !== 'object') return;
        const wasActive = vipState.active;
        const rem = Math.max(0, Number(d.remaining_sec || 0));
        vipState.active = Boolean(d.active && rem > 0);
        vipState.allow_4k = Boolean(vipState.active && d.allow_4k);
        vipState.allow_download = Boolean(vipState.active && d.allow_download);
        vipState.price_inr = d.price_inr !== undefined ? d.price_inr : vipState.price_inr;
        if (Array.isArray(d.plans) && d.plans.length) vipState.plans = d.plans;
        vipState.remaining_sec = rem;
        vipState.unlockUntilPerfMs = vipState.active ? (performance.now() + rem * 1000) : 0;
        vipState.code = String(d.code || '');
        vipState.label = String(d.label || '');
        vipState.max_devices = Number(d.max_devices || 1);
        vipState.device_id = String(d.device_id || '');
        vipState.reason = String(d.reason || '');
        applyVipUiState(currentVipUpgradeMode);
        _restartVipTicker();
        if (wasActive && !vipState.active) {
            showToast(vipState.reason || 'VIP Key has expired', 'err');
        }
    }

    async function refreshVipStatus(forceRemote) {
        try {
            const resp = await fetch('/api/vip/status' + (forceRemote ? '?refresh=1' : ''));
            const d = await resp.json();
            _syncVipPayload(d);
            return d;
        } catch (e) {
            applyVipUiState(false);
            return null;
        }
    }

    window.openVipUnlockModal = function(fromPlayer, upgradeModeArg) {
        dismissIntro();
        const errBox = document.getElementById('vipModalErr');
        const noteBox = document.getElementById('vipSelectedPlanNote');
        if (errBox) { errBox.style.display = 'none'; errBox.textContent = ''; }
        if (noteBox) { noteBox.style.display = 'none'; }
        let upgMode = _vipNormalizeUpgradeMode(upgradeModeArg);
        if (!upgMode && vipState.active && !vipState.allow_4k && Boolean(fromPlayer)) {
            upgMode = '4k';
        }
        if (upgMode === 'download' && (!selectedPlanIdUi || selectedPlanIdUi === '15d' || selectedPlanIdUi === '30d')) {
            selectedPlanIdUi = '3m';
        } else if (upgMode === '4k' && (!selectedPlanIdUi || (selectedPlanIdUi !== '6m' && selectedPlanIdUi !== '1y'))) {
            selectedPlanIdUi = '6m';
        }
        applyVipUiState(upgMode);
        const ov = document.getElementById('vipUnlockModal');
        if (ov) ov.classList.add('open');
        _syncFullPageView();
        if (!upgMode) refreshVipStatus(Boolean(fromPlayer));
        if (!vipState.active || upgMode) {
            setTimeout(() => {
                const inp = document.getElementById('vipKeyInput');
                if (inp) inp.focus();
            }, 90);
        }
    };

    function closeVipUnlockModal() {
        pendingVipAction = null;
        currentVipUpgradeMode = false;
        const ov = document.getElementById('vipUnlockModal');
        if (ov) ov.classList.remove('open');
        _syncFullPageView();
    }

    async function submitVipKey() {
        const inp = document.getElementById('vipKeyInput');
        const errBox = document.getElementById('vipModalErr');
        const btn = document.getElementById('btnVipActivate');
        const raw = (inp ? inp.value : '').trim();
        if (!raw) {
            if (errBox) { errBox.textContent = 'Please enter your VIP Key code first'; errBox.style.display = 'block'; }
            return;
        }
        if (errBox) errBox.style.display = 'none';
        if (btn) { btn.disabled = true; btn.textContent = 'Checking Key...'; }
        try {
            const resp = await fetch('/api/vip/activate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key: raw })
            });
            const d = await resp.json();
            if (btn) { btn.disabled = false; btn.textContent = 'Unlock with Key'; }
            if (!d || !d.success || !d.active) {
                if (errBox) {
                    errBox.textContent = (d && (d.error || d.message)) || 'Invalid or expired VIP Key';
                    errBox.style.display = 'block';
                }
                return;
            }
            if (inp) inp.value = '';
            currentVipUpgradeMode = false;
            _syncVipPayload(d);
            const cb = pendingVipAction;
            pendingVipAction = null;
            const ov = document.getElementById('vipUnlockModal');
            if (ov) ov.classList.remove('open');
            _syncFullPageView();
            showToast(d.message || 'VIP Unlocked!', 'ok');
            if (typeof cb === 'function') setTimeout(() => cb(), 120);
        } catch (e) {
            if (btn) { btn.disabled = false; btn.textContent = 'Unlock with Key'; }
            if (errBox) { errBox.textContent = 'Network error: ' + e.message; errBox.style.display = 'block'; }
        }
    }

    async function lockVipForTest() {
        try {
            const resp = await fetch('/api/vip/deactivate', { method: 'POST' });
            const d = await resp.json();
            currentVipUpgradeMode = false;
            _syncVipPayload(d);
            showToast('App locked again (Test Mode)');
        } catch (e) {}
    }

    function requireVipAccess(onUnlocked) {
        if (vipState.active && _vipRemainingNow() > 0) return true;
        vipState.active = false;
        applyVipUiState();
        pendingVipAction = (typeof onUnlocked === 'function') ? onUnlocked : null;
        window.openVipUnlockModal(false);
        return false;
    }

    function requireDownloadAccess(onUnlocked) {
        if (!requireVipAccess(onUnlocked)) return false;
        if (vipState.allow_download) return true;
        pendingVipAction = (typeof onUnlocked === 'function') ? onUnlocked : null;
        window.openVipUnlockModal(false, 'download');
        return false;
    }

    function require4kAccess(onUnlocked) {
        if (!requireVipAccess(onUnlocked)) return false;
        if (vipState.allow_4k) return true;
        pendingVipAction = (typeof onUnlocked === 'function') ? onUnlocked : null;
        window.openVipUnlockModal(false, '4k');
        return false;
    }
    let hub4kItems = [];
    let hub4kPage = 1;
    let hub4kCategory = '';
    let hub4kSearchQuery = '';
    let hub4kFilter = 'all';

    async function openHub4kModal() {
        dismissIntro();
        const ov = document.getElementById('hub4kPageModal');
        if (ov) ov.classList.add('open');
        _syncFullPageView();
        if (!hub4kItems.length) {
            await loadHub4kLatest(1, hub4kCategory, false);
        }
    }

    function closeHub4kModal() {
        const ov = document.getElementById('hub4kPageModal');
        if (ov) ov.classList.remove('open');
        _syncFullPageView();
    }

    function openHub4kMovie(movie) {
        if (!require4kAccess(() => openHub4kMovie(movie))) return;
        _savedMainScrollY = 0;
        closeHub4kModal();
        openTitleNetflixStyle(movie);
    }

    function renderHub4kGrid() {
        const grid = document.getElementById('hub4kGrid');
        if (!grid) return;
        grid.innerHTML = '';
        const list = hub4kItems.filter(m => {
            if (hub4kFilter === 'movies' && m.is_series) return false;
            if (hub4kFilter === 'series' && !m.is_series) return false;
            return true;
        });
        if (!list.length) {
            grid.innerHTML = '<div style="grid-column:1/-1;color:var(--text-muted);padding:28px 0;text-align:center;font-size:0.86rem;">No 4K title found — please try a different category or search query.</div>';
            return;
        }
        list.forEach(m => {
            const card = createPosterCard(m, 'movie-card');
            card.onclick = () => openHub4kMovie(m);
            card.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openHub4kMovie(m); } };
            grid.appendChild(card);
        });
    }

    function setHub4kFilter(f) {
        hub4kFilter = f;
        document.getElementById('hub4kFilterAll').classList.toggle('active', f === 'all');
        document.getElementById('hub4kFilterMovies').classList.toggle('active', f === 'movies');
        document.getElementById('hub4kFilterSeries').classList.toggle('active', f === 'series');
        renderHub4kGrid();
    }

    async function loadHub4kLatest(page, cat, append) {
        const p = page || 1;
        const c = cat || '';
        const st = document.getElementById('hub4kStatus');
        const btnMore = document.getElementById('btnLoadMore4k');
        if (!append && st) st.textContent = 'Loading 4K Cinema feed...';
        if (append && btnMore) { btnMore.disabled = true; btnMore.textContent = 'Loading...'; }
        try {
            const resp = await fetch(`/api/4khub/latest?page=${p}&cat=${encodeURIComponent(c)}`);
            const items = await resp.json();
            hub4kPage = p;
            hub4kCategory = c;
            hub4kSearchQuery = '';
            if (append && Array.isArray(items)) {
                const seen = new Set(hub4kItems.map(x => x.permalink));
                items.forEach(x => { if (!seen.has(x.permalink)) hub4kItems.push(x); });
            } else {
                hub4kItems = Array.isArray(items) ? items : [];
            }
            renderHub4kGrid();
            if (st) st.textContent = `${hub4kItems.length} 4K HDR & Series titles loaded`;
            if (btnMore) {
                btnMore.disabled = false;
                if (Array.isArray(items) && items.length >= 10) {
                    btnMore.style.display = 'inline-flex';
                    btnMore.textContent = `Load More (Page ${p + 1})`;
                } else {
                    btnMore.style.display = 'none';
                }
            }
        } catch (e) {
            if (st) st.textContent = '4K feed load error: ' + e.message;
            if (btnMore) { btnMore.disabled = false; }
        }
    }

    async function searchHub4k(query, page, append) {
        const inp = document.getElementById('hub4kSearchInput');
        const q = (query !== undefined ? query : (inp ? inp.value : '')).trim();
        if (!q) { await loadHub4kLatest(1, hub4kCategory, false); return; }
        const p = page || 1;
        const st = document.getElementById('hub4kStatus');
        const btnMore = document.getElementById('btnLoadMore4k');
        if (!append && st) st.textContent = `Searching "${q}" in 4K Cinema...`;
        try {
            const resp = await fetch(`/api/4khub/search?q=${encodeURIComponent(q)}&page=${p}`);
            const items = await resp.json();
            hub4kPage = p;
            hub4kSearchQuery = q;
            if (append && Array.isArray(items)) {
                const seen = new Set(hub4kItems.map(x => x.permalink));
                items.forEach(x => { if (!seen.has(x.permalink)) hub4kItems.push(x); });
            } else {
                hub4kItems = Array.isArray(items) ? items : [];
            }
            renderHub4kGrid();
            if (st) st.textContent = `Found ${hub4kItems.length} 4K titles for "${q}"`;
            if (btnMore) {
                btnMore.disabled = false;
                if (Array.isArray(items) && items.length >= 10) {
                    btnMore.style.display = 'inline-flex';
                    btnMore.textContent = `Load More Results (Page ${p + 1})`;
                } else {
                    btnMore.style.display = 'none';
                }
            }
        } catch (e) {
            if (st) st.textContent = 'Search error: ' + e.message;
        }
    }

    function selectHub4kCategory(btnEl, cat) {
        document.querySelectorAll('#hub4kCategoryStrip .cat-chip').forEach(b => b.classList.remove('active'));
        if (btnEl) btnEl.classList.add('active');
        const inp = document.getElementById('hub4kSearchInput');
        if (inp) inp.value = '';
        hub4kSearchQuery = '';
        loadHub4kLatest(1, cat, false);
    }

    function resetHub4kBrowse() {
        const inp = document.getElementById('hub4kSearchInput');
        if (inp) inp.value = '';
        hub4kSearchQuery = '';
        const firstChip = document.querySelector('#hub4kCategoryStrip .cat-chip[data-cat4k=""]');
        selectHub4kCategory(firstChip, '');
    }

    function loadMoreHub4k() {
        const nextP = hub4kPage + 1;
        if (hub4kSearchQuery) searchHub4k(hub4kSearchQuery, nextP, true);
        else loadHub4kLatest(nextP, hub4kCategory, true);
    }
    let _savedMainScrollY = 0;

    function _syncFullPageView() {
        let topOpen = null;
        SH_FULL_PAGES.forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                el.classList.remove('sh-page-visible');
                if (el.classList.contains('open')) topOpen = el;
            }
        });
        const mainParts = [
            document.getElementById('topNavbar'),
            document.getElementById('heroSpotlight'),
            document.querySelector('main.app-shell'),
            document.querySelector('nav.mobile-dock'),
            document.getElementById('introSplash')
        ];
        if (topOpen) {
            if (!document.body.getAttribute('data-sh-subview')) {
                _savedMainScrollY = window.scrollY || 0;
                document.body.setAttribute('data-sh-subview', '1');
            }
            mainParts.forEach(el => { if (el) el.classList.add('sh-view-hidden'); });
            topOpen.classList.add('sh-page-visible');
            window.scrollTo(0, 0);
        } else {
            const wasSub = document.body.getAttribute('data-sh-subview') === '1';
            document.body.removeAttribute('data-sh-subview');
            mainParts.forEach(el => { if (el) el.classList.remove('sh-view-hidden'); });
            if (wasSub && _savedMainScrollY > 0) {
                setTimeout(() => window.scrollTo(0, _savedMainScrollY), 10);
            }
        }
    }

    async function openSettingsModal() {
        dismissIntro();
        const ov = document.getElementById('settingsModalOverlay');
        if (ov) ov.classList.add('open');
        _syncFullPageView();
        try {
            const resp = await fetch('/api/settings');
            const data = await resp.json();
            if (data && data.success) {
                userSettingsState = data;
                applyThemeUi(data.theme || 'netflix');
                applyProfileUi(data.profile);
                renderStorageSummaryUi(data.storage, data.download_prefs);
            }
        } catch (e) {}
    }

    function closeSettingsModal() {
        const ov = document.getElementById('settingsModalOverlay');
        if (ov) ov.classList.remove('open');
        _syncFullPageView();
    }

    function openSettingsSubPage(id) {
        dismissIntro();
        const ov = document.getElementById(id);
        if (ov) ov.classList.add('open');
        _syncFullPageView();
    }

    function closeSubPage(id) {
        const ov = document.getElementById(id);
        if (ov) ov.classList.remove('open');
        _syncFullPageView();
    }

    function closeAllSettingsPages() {
        SH_FULL_PAGES.forEach(id => {
            const ov = document.getElementById(id);
            if (ov) ov.classList.remove('open');
        });
        clearInterval(updatePollTimer);
        updatePollTimer = null;
        if (offlinePollTimer) {
            const activeCnt = Number((userSettingsState.storage && userSettingsState.storage.active_count) || 0);
            if (activeCnt === 0) { clearInterval(offlinePollTimer); offlinePollTimer = null; }
        }
        _syncFullPageView();
        renderLiveDownloadBanner();
    }

    function pickProfileAvatar(btn) {
        document.querySelectorAll('#avatarPickerGrid .avatar-chip').forEach(b => b.classList.remove('active'));
        if (btn) btn.classList.add('active');
    }

    async function saveProfileSettings() {
        const nmInp = document.getElementById('setProfileName');
        const lgSel = document.getElementById('setProfileLang');
        const activeAv = document.querySelector('#avatarPickerGrid .avatar-chip.active');
        const profPayload = {
            name: nmInp ? nmInp.value.trim() : 'StreamHub User',
            avatar: activeAv ? activeAv.getAttribute('data-av') : 'film',
            preferred_language: lgSel ? lgSel.value : 'Auto'
        };
        try {
            const resp = await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ profile: profPayload })
            });
            const data = await resp.json();
            if (data && data.success) {
                userSettingsState = data;
                applyProfileUi(data.profile);
                autoSelectPreferredAudioOption();
                showToast('Profile saved (' + data.profile.name + ')', 'ok');
            }
        } catch (e) {
            showToast('Profile save failed', 'err');
        }
    }

    async function applyAndSaveTheme(themeId) {
        applyThemeUi(themeId);
        try {
            const resp = await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ theme: themeId })
            });
            const data = await resp.json();
            if (data && data.success) {
                userSettingsState = data;
                showToast('Theme: ' + (THEME_META[themeId] ? THEME_META[themeId].name : themeId), 'ok');
            }
        } catch (e) {}
    }

    async function saveDownloadPrefs() {
        const cap = document.getElementById('setStorageCap');
        const autoDel = document.getElementById('setAutoDelWatched');
        const exp = document.getElementById('setExpireDays');
        const dq = document.getElementById('setDefaultDlQuality');
        const sq = document.getElementById('setDefaultStreamQuality');
        const prefs = {
            storage_limit_gb: cap ? parseInt(cap.value, 10) : 4,
            auto_delete_watched: autoDel ? Boolean(autoDel.checked) : true,
            auto_expire_days: exp ? parseInt(exp.value, 10) : 7,
            default_quality: dq ? dq.value : '720p',
            stream_quality: sq ? sq.value : 'auto'
        };
        try {
            const resp = await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ download_prefs: prefs })
            });
            const data = await resp.json();
            if (data && data.success) {
                userSettingsState = data;
                renderStorageSummaryUi(data.storage, data.download_prefs);
                showToast('Download storage settings updated', 'ok');
            }
        } catch (e) {
            showToast('Settings update failed', 'err');
        }
    }

    async function clearAllOfflineDownloads() {
        if (!confirm('Delete all offline downloaded movies?')) return;
        try {
            const resp = await fetch('/api/downloads/action', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'clear_all' })
            });
            const data = await resp.json();
            if (data && data.success) {
                offlineDownloadsList = data.items || [];
                renderStorageSummaryUi(data.storage, data.prefs);
                renderDownloadsList();
                showToast('All offline files cleared', 'ok');
            }
        } catch (e) {
            showToast('Clear failed', 'err');
        }
    }

    function _inferQualityTag(text) {
        const t = String(text || '');
        let res = 'HD';
        if (/2160p|4k|uhd/i.test(t)) res = '4K 2160p';
        else if (/1080p/i.test(t)) res = '1080p';
        else if (/720p/i.test(t)) res = '720p';
        else if (/480p/i.test(t)) res = '480p';
        const mSz = t.match(/(\d+(?:\.\d+)?\s*(?:GB|MB))/i);
        return { res, size: mSz ? mSz[1].toUpperCase() : '' };
    }

    function _detectLangsInText(text) {
        const t = String(text || '').toLowerCase();
        const found = [];
        if (/dual\s*audio/i.test(t)) found.push('Dual Audio');
        [['Hindi', /\bhindi\b|\bhin\b/i], ['English', /\benglish\b|\beng\b/i], ['Tamil', /\btamil\b/i], ['Telugu', /\btelugu\b/i], ['Punjabi', /\bpunjabi\b/i], ['Bengali', /\bbengali\b/i], ['Malayalam', /\bmalayalam\b/i], ['Kannada', /\bkannada\b/i]].forEach(([lbl, rg]) => {
            if (rg.test(t) && !found.includes(lbl)) found.push(lbl);
        });
        return found;
    }

    function openDownloadPicker(cfg) {
        if (!requireDownloadAccess(() => openDownloadPicker(cfg))) return;
        const options = (cfg && cfg.options && cfg.options.length) ? cfg.options : [];
        if (!options.length) {
            showToast('No direct download link found', 'err');
            return;
        }
        const isVault4kDl = Boolean(
            (cfg && (_is4kVaultItem(cfg) || /4khdhub\./i.test(String(cfg.parent_key || cfg.media_key || ''))))
            || options.every(o => _is4kText(o.label) && !_hasHdNon4kText(o.label))
        );
        if (vipState.active && !vipState.allow_4k && isVault4kDl) {
            require4kAccess(() => openDownloadPicker(cfg));
            return;
        }
        const prefQ = String((userSettingsState.download_prefs && userSettingsState.download_prefs.default_quality) || '720p').toLowerCase();
        const prefLang = String((userSettingsState.profile && userSettingsState.profile.preferred_language) || 'Auto');

        let bestIdx = 0;
        let bestScore = -99999;
        options.forEach((o, i) => {
            const lbl = String(o.label || '').toLowerCase();
            let sc = 0;
            if (vipState.active && !vipState.allow_4k && _is4kText(o.label) && !_hasHdNon4kText(o.label)) sc -= 1000;
            if (prefQ !== 'ask' && lbl.includes(prefQ)) sc += 4;
            else if (lbl.includes('720p')) sc += 2;
            else if (lbl.includes('480p')) sc += 2;
            if (prefLang !== 'Auto' && lbl.includes(prefLang.toLowerCase())) sc += 5;
            if (sc > bestScore) { bestScore = sc; bestIdx = i; }
        });

        dlPickerState = {
            title: cleanDisplayTitle((cfg && cfg.title) || activeTitle || 'Movie'),
            poster: (cfg && cfg.poster) || (currentMovieMeta ? currentMovieMeta.thumbnail : '') || '',
            ep_label: (cfg && cfg.ep_label) || '',
            media_key: (cfg && cfg.media_key) || '',
            parent_key: (cfg && cfg.parent_key) || '',
            options,
            selectedIdx: bestIdx,
            selectedLang: prefLang || 'Auto'
        };

        const titleEl = document.getElementById('dlPickerTitle');
        if (titleEl) titleEl.textContent = dlPickerState.ep_label ? `${dlPickerState.title} • ${dlPickerState.ep_label}` : dlPickerState.title;
        const pImg = document.getElementById('dlPickerPoster');
        if (pImg) {
            if (dlPickerState.poster) { pImg.src = dlPickerState.poster; pImg.style.display = 'block'; }
            else pImg.style.display = 'none';
        }
        renderDownloadPickerChoices();

        const st = userSettingsState.storage || {};
        const dp = userSettingsState.download_prefs || {};
        const limLabel = Number(dp.storage_limit_gb) > 0 ? `${dp.storage_limit_gb} GB` : 'Unlimited';
        const hintEl = document.getElementById('dlPickerStorageHint');
        if (hintEl) {
            hintEl.textContent = `Private App Storage: ${fmtBytes(st.used_bytes || 0)} / ${limLabel} used • Hidden from Gallery/Files${dp.auto_delete_watched ? ' • Auto-delete after watching' : ''}`;
        }
        const ov = document.getElementById('downloadPickerModal');
        if (ov) ov.classList.add('open');
    }

    function closeDownloadPicker() {
        const ov = document.getElementById('downloadPickerModal');
        if (ov) ov.classList.remove('open');
    }

    function renderDownloadPickerChoices() {
        const qWrap = document.getElementById('dlPickerQualities');
        const lWrap = document.getElementById('dlPickerLangs');
        if (qWrap) {
            qWrap.innerHTML = '';
            dlPickerState.options.forEach((opt, i) => {
                const tag = _inferQualityTag(opt.label);
                const is4kOpt = _is4kText(opt.label) && !_hasHdNon4kText(opt.label);
                const locked4kOpt = Boolean(vipState.active && !vipState.allow_4k && is4kOpt);
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'dl-q-pill' + (i === dlPickerState.selectedIdx ? ' active' : '');
                btn.style.cssText = 'text-align:left;display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;';
                const hintTag = /480p/i.test(tag.res) ? 'Low Size' : (/720p/i.test(tag.res) ? 'Balanced HD' : '');
                const szText = locked4kOpt ? 'LOCKED (6M+ Plan)' : (opt.size_label || tag.size || hintTag || 'Direct');
                btn.innerHTML = `<span style="font-weight:800;overflow:hidden;text-overflow:ellipsis;">${esc(opt.label)}</span><span style="font-size:0.72rem;opacity:0.9;white-space:nowrap;${locked4kOpt ? 'color:#fde047;font-weight:800;' : ''}">${esc(szText)}</span>`;
                btn.onclick = () => {
                    if (locked4kOpt) { closeDownloadPicker(); window.openVipUnlockModal(false, '4k'); return; }
                    dlPickerState.selectedIdx = i; renderDownloadPickerChoices();
                };
                qWrap.appendChild(btn);
            });
        }
        if (lWrap) {
            const selOpt = dlPickerState.options[dlPickerState.selectedIdx] || {};
            const detected = _detectLangsInText(selOpt.label || dlPickerState.title);
            const langs = ['Auto'];
            detected.forEach(l => { if (!langs.includes(l)) langs.push(l); });
            ['Hindi', 'English', 'Dual Audio', 'Tamil', 'Telugu', 'Punjabi', 'Bengali'].forEach(l => {
                if (!langs.includes(l)) langs.push(l);
            });
            lWrap.innerHTML = '';
            langs.forEach(lg => {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'dl-lang-pill' + (dlPickerState.selectedLang === lg ? ' active' : '');
                btn.textContent = lg === 'Auto' ? 'Auto (Default)' : lg;
                btn.onclick = () => {
                    dlPickerState.selectedLang = lg;
                    if (lg !== 'Auto' && dlPickerState.options.length > 1) {
                        const matchIdx = dlPickerState.options.findIndex(o => String(o.label || '').toLowerCase().includes(lg.toLowerCase()));
                        if (matchIdx >= 0) dlPickerState.selectedIdx = matchIdx;
                    }
                    renderDownloadPickerChoices();
                };
                lWrap.appendChild(btn);
            });
        }
    }

    function openDownloadPickerFromDetails() {
        const list = (currentDetailsQualities && currentDetailsQualities.length)
            ? currentDetailsQualities.map(q => ({
                label: q.title,
                hdhub_link: q.link,
                size_label: _inferQualityTag(q.title).size,
                quality_label: _inferQualityTag(q.title).res
            }))
            : [];
        if (!list.length) {
            const sel = document.getElementById('qualitySelect');
            if (sel && sel.value) {
                const selOpt = sel.options[sel.selectedIndex];
                list.push({ label: (selOpt && selOpt.textContent) || 'Selected Quality', hdhub_link: sel.value, size_label: '', quality_label: 'HD' });
            }
        }
        const cardTitleEl = document.getElementById('movieCardTitle');
        openDownloadPicker({
            title: currentMovieMeta ? currentMovieMeta.title : ((cardTitleEl && cardTitleEl.textContent) || 'Movie'),
            poster: currentMovieMeta ? currentMovieMeta.thumbnail : '',
            media_key: currentMovieMeta ? (currentMovieMeta.permalink || currentMovieMeta.title) : '',
            options: list
        });
    }

    function openDownloadPickerForEpisode(sIdx, epIdx) {
        const season = (activeSeriesData && activeSeriesData.seasons || [])[sIdx];
        const ep = season && (season.episodes || [])[epIdx];
        if (!ep) return;
        openDownloadPicker({
            title: (currentMovieMeta ? currentMovieMeta.title : activeTitle) || 'Web Series',
            ep_label: ep.ep_label,
            poster: currentMovieMeta ? currentMovieMeta.thumbnail : '',
            media_key: ep.link,
            parent_key: currentMovieMeta ? (currentMovieMeta.permalink || '') : '',
            options: [{
                label: `${ep.ep_label} • ${ep.title}`,
                hdhub_link: ep.link,
                size_label: ep.size || '',
                quality_label: _inferQualityTag(ep.title).res
            }]
        });
    }

    function openDownloadPickerForZipEp(zipIdx) {
        const ep = activeZipEntries[zipIdx];
        if (!ep) return;
        openDownloadPicker({
            title: cleanDisplayTitle(activeZipTitle || activeTitle || 'Episode'),
            ep_label: ep.name,
            poster: currentMovieMeta ? currentMovieMeta.thumbnail : '',
            media_key: ep.sid || ep.name,
            parent_key: currentMovieMeta ? (currentMovieMeta.permalink || '') : '',
            options: [{
                label: ep.name,
                stream_url: ep.stream_url,
                seek_url: ep.seek_url || '',
                base_offset: ep.base_offset || 0,
                sub_size: ep.sub_size !== undefined ? ep.sub_size : null,
                compress_method: ep.compress_method || 0,
                zip_index: zipIdx,
                size_label: ep.size || '',
                quality_label: _inferQualityTag(ep.name).res
            }]
        });
    }

    function _getActiveResSizeStr() {
        const rs = document.getElementById('resSize');
        return (rs && rs.textContent) ? rs.textContent.replace(/^Size:\s*/i, '') : '';
    }

    function openDownloadPickerFromActive() {
        if (activeZipEntries && activeZipEntries.length && activeZipIndex >= 0) {
            openDownloadPickerForZipEp(activeZipIndex);
            return;
        }
        const opts = [];
        const directMirrors = (activeResolvedMirrors || []).filter(m => m && m.alive && m.url && !String(m.url).includes('.m3u8'));
        if (activeStreamUrl && !String(activeStreamUrl).includes('.m3u8')) {
            opts.push({
                label: `${activeTitle || 'Current Stream'} (Best Server)`,
                stream_url: activeStreamUrl,
                seek_url: activeSeekUrl || '',
                base_offset: activeBaseOff || 0,
                sub_size: activeSubSize !== undefined ? activeSubSize : null,
                compress_method: activeCompressMethod || 0,
                backup_urls: directMirrors.map(m => m.url),
                hdhub_link: activeSourceLink || '',
                size_label: _getActiveResSizeStr(),
                quality_label: inferQualityLabel(activeTitle, activeCompressMethod)
            });
        }
        directMirrors.forEach(m => {
            if (m.url !== activeStreamUrl) {
                opts.push({
                    label: `${cleanDisplayTitle(m.name)} • ${inferQualityLabel(activeTitle, 0)}`,
                    stream_url: m.url,
                    seek_url: m.seek_url || activeSeekUrl || '',
                    hdhub_link: activeSourceLink || '',
                    size_label: _getActiveResSizeStr(),
                    quality_label: inferQualityLabel(activeTitle, 0)
                });
            }
        });
        if (!opts.length && activeSourceLink) {
            opts.push({
                label: activeTitle || 'Resolve & Download',
                hdhub_link: activeSourceLink,
                size_label: '',
                quality_label: inferQualityLabel(activeTitle, activeCompressMethod)
            });
        }
        openDownloadPicker({
            title: activeTitle || 'StreamHub Movie',
            ep_label: activeEpLabel || '',
            poster: currentMovieMeta ? currentMovieMeta.thumbnail : '',
            media_key: activeMediaKey || activeSid || activeTitle,
            parent_key: activeParentKey || '',
            options: opts
        });
    }

    let liveBannerHiddenKey = '';
    function hideLiveDownloadBanner() {
        const cur = offlineDownloadsList.find(x => x.status === 'downloading' || x.status === 'queued') || offlineDownloadsList[0];
        liveBannerHiddenKey = cur ? `${cur.id}:${cur.status}` : 'hidden';
        const b = document.getElementById('liveDownloadBanner');
        if (b) b.classList.remove('show');
    }

    function toggleLiveDownloadPause() {
        const cur = offlineDownloadsList.find(x => x.status === 'downloading' || x.status === 'queued' || x.status === 'paused' || x.status === 'error');
        if (!cur) return;
        if (cur.status === 'downloading' || cur.status === 'queued') {
            downloadAction(cur.id, 'pause');
        } else {
            downloadAction(cur.id, 'resume');
        }
    }

    function renderLiveDownloadBanner() {
        const b = document.getElementById('liveDownloadBanner');
        if (!b) return;
        const modalOpen = Boolean(document.getElementById('downloadsModalOverlay') && document.getElementById('downloadsModalOverlay').classList.contains('open'));
        const cur = offlineDownloadsList.find(x => x.status === 'downloading' || x.status === 'queued')
            || offlineDownloadsList.find(x => x.status === 'paused' || x.status === 'error');
        if (!cur || modalOpen) {
            b.classList.remove('show');
            return;
        }
        const curKey = `${cur.id}:${cur.status}`;
        if (liveBannerHiddenKey === curKey && cur.status !== 'downloading') {
            b.classList.remove('show');
            return;
        }
        const pct = Number(cur.pct || 0);
        const dlStr = fmtBytes(cur.downloaded_bytes);
        const totStr = cur.total_bytes > 0 ? fmtBytes(cur.total_bytes) : (cur.size_label || '...');
        const spd = Number(cur.speed_bps || 0);
        let etaStr = '';
        if (cur.status === 'downloading' && spd > 0 && cur.total_bytes > cur.downloaded_bytes) {
            const remSec = Math.round((cur.total_bytes - cur.downloaded_bytes) / spd);
            etaStr = remSec >= 3600 ? ` • ${Math.floor(remSec/3600)}h ${Math.floor((remSec%3600)/60)}m` : (remSec >= 60 ? ` • ${Math.floor(remSec/60)}m left` : ` • ${remSec}s`);
        }
        const ttlEl = document.getElementById('liveDlTitle');
        const fillEl = document.getElementById('liveDlProgFill');
        const metaEl = document.getElementById('liveDlMeta');
        const togBtn = document.getElementById('liveDlToggleBtn');
        if (ttlEl) ttlEl.textContent = (cur.status === 'downloading' ? 'Downloading: ' : (cur.status === 'queued' ? 'Connecting: ' : 'Paused: ')) + cur.title;
        if (fillEl) fillEl.style.width = Math.min(100, Math.max(0, pct)) + '%';
        if (metaEl) metaEl.textContent = `${pct.toFixed(1)}% • ${dlStr} / ${totStr}${spd > 0 ? ' • ' + fmtBytes(spd) + '/s' : ''}${etaStr}`;
        if (togBtn) togBtn.textContent = (cur.status === 'downloading' || cur.status === 'queued') ? 'Pause' : 'Resume';
        b.classList.add('show');
    }

    async function confirmStartDownload() {
        const opt = dlPickerState.options[dlPickerState.selectedIdx];
        if (!opt) return;
        const btn = document.getElementById('btnStartDownloadConfirm');
        if (btn) { btn.disabled = true; btn.textContent = 'Starting download...'; }
        try {
            const payload = {
                title: dlPickerState.title,
                ep_label: dlPickerState.ep_label,
                poster: dlPickerState.poster,
                media_key: dlPickerState.media_key || opt.hdhub_link || dlPickerState.title,
                parent_key: dlPickerState.parent_key,
                quality_label: opt.quality_label || _inferQualityTag(opt.label).res,
                size_label: opt.size_label || '',
                language: dlPickerState.selectedLang || 'Auto',
                hdhub_link: opt.hdhub_link || '',
                stream_url: opt.stream_url || '',
                seek_url: opt.seek_url || '',
                base_offset: opt.base_offset || 0,
                sub_size: opt.sub_size !== undefined ? opt.sub_size : null,
                compress_method: opt.compress_method || 0,
                backup_urls: opt.backup_urls || [],
                zip_index: opt.zip_index !== undefined ? opt.zip_index : -1
            };
            const resp = await fetch('/api/downloads/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await resp.json();
            if (!data || !data.success) {
                if (data && data.vip_required) {
                    closeDownloadPicker();
                    refreshVipStatus(false);
                    window.openVipUnlockModal(false, data.upgrade_4k_required ? '4k' : (data.upgrade_download_required ? 'download' : false));
                }
                showToast((data && data.error) || 'Could not start download', 'err');
                return;
            }
            liveBannerHiddenKey = '';
            closeDownloadPicker();
            if (window.StreamHubAndroid && typeof window.StreamHubAndroid.notifyDownloadMonitor === 'function') {
                try { window.StreamHubAndroid.notifyDownloadMonitor(); } catch (e) {}
            }
            await refreshDownloadsState(true);
            showToast(data.message || 'Download started! View live progress in the bar below', 'ok', () => openDownloadsModal());
        } catch (e) {
            showToast('Download request error: ' + e.message, 'err');
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = 'Start Private Download'; }
        }
    }

    async function openDownloadsModal() {
        dismissIntro();
        const ov = document.getElementById('downloadsModalOverlay');
        if (ov) ov.classList.add('open');
        _syncFullPageView();
        await refreshDownloadsState(true);
    }

    function closeDownloadsModal() {
        const ov = document.getElementById('downloadsModalOverlay');
        if (ov) ov.classList.remove('open');
        const activeCnt = Number((userSettingsState.storage && userSettingsState.storage.active_count) || 0);
        if (activeCnt === 0 && offlinePollTimer) {
            clearInterval(offlinePollTimer);
            offlinePollTimer = null;
        }
        _syncFullPageView();
        renderLiveDownloadBanner();
    }

    function _syncDownloadPoller(activeCount) {
        const dlOv = document.getElementById('downloadsModalOverlay');
        const modalOpen = Boolean(dlOv && dlOv.classList.contains('open'));
        if (activeCount > 0 || modalOpen) {
            if (!offlinePollTimer) {
                offlinePollTimer = setInterval(() => refreshDownloadsState(false), modalOpen ? 1000 : 1800);
            }
        } else if (offlinePollTimer) {
            clearInterval(offlinePollTimer);
            offlinePollTimer = null;
        }
    }

    async function refreshDownloadsState() {
        try {
            const resp = await fetch('/api/downloads');
            const data = await resp.json();
            if (data && data.success) {
                offlineDownloadsList = data.items || [];
                userSettingsState.storage = data.storage || userSettingsState.storage;
                if (data.prefs) userSettingsState.download_prefs = data.prefs;
                renderStorageSummaryUi(data.storage, data.prefs);
                renderDownloadsList();
                renderLiveDownloadBanner();
                _syncDownloadPoller(Number((data.storage && data.storage.active_count) || 0));
            }
        } catch (e) {}
    }

    function renderDownloadsList() {
        const wrap = document.getElementById('downloadsListWrap');
        if (!wrap) return;
        if (!offlineDownloadsList.length) {
            wrap.innerHTML = `<div class="settings-card" style="text-align:center;padding:32px 16px;">
                <div style="width:48px;height:48px;margin:0 auto 12px;border-radius:12px;background:var(--surface-2);border:1px solid var(--border);display:grid;place-items:center;color:var(--text-muted);">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                </div>
                <div style="font-size:0.95rem;font-weight:700;color:#fff;margin-bottom:4px;">No Offline Downloads</div>
                <div style="font-size:0.78rem;color:var(--text-muted);line-height:1.45;">Open any movie or web series and tap <b>Download</b> to watch offline without internet (requires 3 Months+ VIP Plan).</div>
            </div>`;
            return;
        }
        wrap.innerHTML = '';
        offlineDownloadsList.forEach(item => {
            const card = document.createElement('div');
            card.className = 'dl-item-card';
            const pct = Number(item.pct || 0);
            const spd = Number(item.speed_bps || 0);
            const spdTxt = spd > 0 ? ` • ${fmtBytes(spd)}/s` : '';
            const st = item.status || 'paused';
            let statusLine = '';
            let actionsHtml = '';
            if (st === 'completed') {
                const expTxt = (item.expires_in_days !== null && item.expires_in_days !== undefined) ? ` • ${item.expires_in_days}d left` : '';
                const watchTxt = Number(item.last_watched_pct || 0) >= 5 ? ` • Watched ${Math.round(item.last_watched_pct)}%` : '';
                statusLine = `<span style="color:#86efac;font-weight:600;">Ready Offline (${fmtBytes(item.total_bytes || item.downloaded_bytes)})</span>${watchTxt}${expTxt}`;
                actionsHtml = `
                    <button type="button" class="btn-sm btn-vlc-sm" onclick="playOfflineDownload('${esc(item.id)}')">${_SH_ICONS.play} Play Offline</button>
                    <button type="button" class="btn-sm btn-copy-sm" onclick="downloadAction('${esc(item.id)}','delete')" aria-label="Delete">${_SH_ICONS.trash}</button>
                `;
            } else if (st === 'downloading' || st === 'queued') {
                statusLine = `<span style="color:#93c5fd;font-weight:600;">${st === 'queued' ? 'Connecting...' : `Downloading ${pct.toFixed(1)}%`}</span> • ${fmtBytes(item.downloaded_bytes)} / ${item.total_bytes > 0 ? fmtBytes(item.total_bytes) : '...'}${spdTxt}`;
                actionsHtml = `
                    <button type="button" class="btn-sm btn-indigo-sm" onclick="downloadAction('${esc(item.id)}','pause')">${_SH_ICONS.pause} Pause</button>
                    <button type="button" class="btn-sm btn-copy-sm" onclick="downloadAction('${esc(item.id)}','delete')" aria-label="Cancel">${_SH_ICONS.close}</button>
                `;
            } else {
                const isErr = st === 'error';
                statusLine = `<span style="color:${isErr ? '#fca5a5' : '#fde68a'};font-weight:600;">${isErr ? 'Error' : 'Paused'} (${pct.toFixed(1)}%)</span> • ${fmtBytes(item.downloaded_bytes)}${item.error ? ' — ' + esc(item.error) : ''}`;
                actionsHtml = `
                    <button type="button" class="btn-sm btn-vlc-sm" onclick="downloadAction('${esc(item.id)}','resume')">${_SH_ICONS.play} Resume</button>
                    <button type="button" class="btn-sm btn-copy-sm" onclick="downloadAction('${esc(item.id)}','delete')" aria-label="Delete">${_SH_ICONS.trash}</button>
                `;
            }
            const imgHtml = item.poster
                ? `<img class="dl-poster" src="${esc(item.poster)}" alt="">`
                : `<div class="dl-poster" style="display:grid;place-items:center;color:var(--text-muted);">${_SH_ICONS.film}</div>`;
            const progBar = (st !== 'completed')
                ? `<div class="storage-bar-track" style="height:6px;margin:7px 0;"><div class="storage-bar-fill" style="width:${pct}%"></div></div>`
                : '';

            card.innerHTML = `
                ${imgHtml}
                <div style="flex:1;min-width:0;">
                    <div style="font-size:0.88rem;font-weight:800;color:#fff;line-height:1.25;">${esc(item.title)}${item.ep_label ? ' &bull; ' + esc(item.ep_label) : ''}</div>
                    <div style="display:flex;gap:6px;flex-wrap:wrap;margin:5px 0;">
                        <span class="badge" style="font-size:0.68rem;padding:2px 7px;">${esc(item.quality_label || 'HD')}</span>
                        <span class="badge badge-gold" style="font-size:0.68rem;padding:2px 7px;">Audio: ${esc(item.language || 'Auto')}</span>
                    </div>
                    <div style="font-size:0.74rem;color:var(--text-muted);line-height:1.35;">${statusLine}</div>
                    ${progBar}
                    <div style="display:flex;gap:7px;flex-wrap:wrap;margin-top:8px;">${actionsHtml}</div>
                </div>
            `;
            wrap.appendChild(card);
        });
    }

    async function downloadAction(dlId, action) {
        try {
            const resp = await fetch('/api/downloads/action', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: dlId, action })
            });
            const data = await resp.json();
            if (data && data.success) {
                offlineDownloadsList = data.items || [];
                userSettingsState.storage = data.storage || userSettingsState.storage;
                if (data.prefs) userSettingsState.download_prefs = data.prefs;
                if (action === 'resume') {
                    liveBannerHiddenKey = '';
                    if (window.StreamHubAndroid && typeof window.StreamHubAndroid.notifyDownloadMonitor === 'function') {
                        try { window.StreamHubAndroid.notifyDownloadMonitor(); } catch (e) {}
                    }
                }
                renderStorageSummaryUi(data.storage, data.prefs);
                renderDownloadsList();
                renderLiveDownloadBanner();
                _syncDownloadPoller(Number((data.storage && data.storage.active_count) || 0));
            } else if (data && data.vip_required) {
                refreshVipStatus(false);
                window.openVipUnlockModal(false, data.upgrade_4k_required ? '4k' : (data.upgrade_download_required ? 'download' : false));
                if (data.error) showToast(data.error, 'err');
            } else if (data && data.error) {
                showToast(data.error, 'err');
            }
        } catch (e) {
            showToast('Action error: ' + e.message, 'err');
        }
    }

    async function playOfflineDownload(dlId) {
        if (!requireVipAccess(() => playOfflineDownload(dlId))) return;
        const item = offlineDownloadsList.find(x => x.id === dlId);
        if (!item) return;
        const p = (item.media_key && progressMap[item.media_key]) ? progressMap[item.media_key] : null;
        const startSec = (p && p.position_sec >= 10 && p.pct < 95) ? Math.floor(p.position_sec) : 0;
        const fullTitle = item.ep_label ? `${item.title} • ${item.ep_label}` : item.title;
        const payload = {
            offline_id: item.id,
            title: fullTitle,
            audio_track: item.preferred_audio_track || 1,
            sub_track: 'auto',
            start_time: startSec,
            media_key: item.media_key || item.id,
            parent_key: item.parent_key || '',
            ep_label: item.ep_label || ''
        };
        if (isAndroidApp()) {
            launchAndroidPlayerWithPayload(payload, false);
            return;
        }
        try {
            const resp = await fetch('/api/play_inbuilt', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const d = await resp.json();
            if (d && d.success) {
                showToast('Offline playback started!', 'ok');
            } else {
                const localUrl = window.location.origin + '/api/offline_stream?id=' + encodeURIComponent(item.id);
                window.open(localUrl, '_blank');
            }
        } catch (e) {
            showToast('Offline player error: ' + e.message, 'err');
        }
    }

    let pendingUpdateInfo = null;
    let updatePollTimer = null;

    function openStartupUpdatePopup(data) {
        if (!data || !data.update_available) return;
        const lv = String(data.latest_version || '');
        if (lv && startupPopupShownForVer === lv) return;
        const anySubOpen = SH_FULL_PAGES.some(id => {
            if (id === 'startupUpdateModal') return false;
            const el = document.getElementById(id);
            return el && el.classList.contains('open');
        });
        if (anySubOpen) return;
        pendingUpdateInfo = data;
        startupPopupShownForVer = lv;
        dismissIntro();

        const ov = document.getElementById('startupUpdateModal');
        const oldV = document.getElementById('startupUpdOldVer');
        const newV = document.getElementById('startupUpdNewVer');
        const notes = document.getElementById('startupUpdNotes');
        const btn = document.getElementById('btnStartupUpdateNow');
        const pw = document.getElementById('startupUpdProgWrap');
        const pf = document.getElementById('startupUpdProgFill');
        const stEl = document.getElementById('startupUpdStatus');

        if (oldV) oldV.textContent = 'v' + (data.version || (userSettingsState && userSettingsState.version) || '2.5.6');
        if (newV) newV.textContent = 'v' + (lv || 'Latest');
        if (notes) notes.textContent = data.notes || `StreamHub v${lv} update available.`;
        if (pw) pw.style.display = 'none';
        if (pf) pf.style.width = '0%';
        if (stEl) { stEl.style.display = 'none'; stEl.textContent = ''; }
        if (btn) {
            btn.disabled = false;
            const useOta = isAndroidApp() && data.ota_supported && data.py_download_url;
            btn.textContent = useOta ? `Instant Update v${lv}` : `Update to v${lv}`;
        }
        if (ov) ov.classList.add('open');
        _syncFullPageView();
    }

    function closeStartupUpdateModal(remember) {
        if (remember && pendingUpdateInfo && pendingUpdateInfo.latest_version) {
            startupPopupShownForVer = String(pendingUpdateInfo.latest_version);
        }
        const ov = document.getElementById('startupUpdateModal');
        if (ov) ov.classList.remove('open');
        _syncFullPageView();
    }

    function triggerStartupUpdateNow() {
        if (!pendingUpdateInfo) return;
        const onAndroid = isAndroidApp();
        if (onAndroid && pendingUpdateInfo.ota_supported && pendingUpdateInfo.py_download_url) {
            applyOtaPyUpdateNow(true);
            return;
        }
        if (onAndroid && pendingUpdateInfo.apk_download_url) {
            applyAndroidApkUpdateNow();
            return;
        }
        closeStartupUpdateModal(false);
        openUpdateModal(false);
        if (!onAndroid && pendingUpdateInfo.download_url) {
            setTimeout(() => applyAppUpdateNow(), 180);
        }
    }

    function openUpdateModal(fromSettings) {
        dismissIntro();
        const ov = document.getElementById('updateModalOverlay');
        if (ov) ov.classList.add('open');
        _syncFullPageView();
        const bk = document.getElementById('btnUpdateBackToSettings');
        if (bk) bk.style.display = (fromSettings === false) ? 'inline-flex' : 'inline-flex';
        checkAppUpdate(true, true);
    }

    function closeUpdateModal() {
        const ov = document.getElementById('updateModalOverlay');
        if (ov) ov.classList.remove('open');
        clearInterval(updatePollTimer);
        updatePollTimer = null;
        _syncFullPageView();
    }

    function backFromUpdateToSettings() {
        const uov = document.getElementById('updateModalOverlay');
        if (uov) uov.classList.remove('open');
        clearInterval(updatePollTimer);
        updatePollTimer = null;
        const sov = document.getElementById('settingsModalOverlay');
        if (sov) sov.classList.add('open');
        _syncFullPageView();
    }

    function renderUpdateModalState(data) {
        if (!data) return;
        const meta = document.getElementById('updateMetaLine');
        const inp = document.getElementById('updateFeedInput');
        const box = document.getElementById('updateStatusBox');
        const dlBox = document.getElementById('updateDownloadBox');
        const notesEl = document.getElementById('updateNotesText');
        const applyBtn = document.getElementById('btnApplyUpdate');
        const otaBtn = document.getElementById('btnOtaPyUpdate');
        const apkBtn = document.getElementById('btnApkUpdate');
        const extLink = document.getElementById('linkApkExternal');
        const onAndroid = isAndroidApp();
        if (meta) {
            const envLabel = onAndroid
                ? ('Android APK' + (data.ota_active ? ' (Instant OTA)' : ''))
                : (data.frozen ? 'Desktop App (.exe)' : 'Python Mode');
            meta.textContent = `v${data.version || '2.5.0'} • ${envLabel}${onAndroid ? '' : (' • ' + (data.libvlc_ready ? '4K libVLC Ready' : 'Web Only'))}`;
        }
        if (inp && !inp.value && data.update_url) inp.value = data.update_url;
        if (box) {
            box.style.color = data.update_available ? '#6ee7b7' : (data.error ? '#fca5a5' : '#cbd5e1');
            box.textContent = data.error ? ('Check error: ' + data.error) : (data.message || 'Status ready');
        }
        pendingUpdateInfo = data.update_available ? data : null;
        const hasAndroidAction = onAndroid && (data.ota_supported || data.apk_download_url);
        const hasPcAction = !onAndroid && data.download_url;
        const showDl = data.update_available && (hasAndroidAction || hasPcAction);
        if (dlBox) dlBox.style.display = showDl ? 'block' : 'none';
        if (notesEl) notesEl.textContent = data.notes || `New release v${data.latest_version} ready to install.`;
        if (otaBtn) {
            if (onAndroid && data.update_available && data.ota_supported && data.py_download_url) {
                otaBtn.disabled = false;
                otaBtn.textContent = `Instant Update v${data.latest_version || ''} (~0.7 MB)`;
                otaBtn.style.display = 'block';
            } else {
                otaBtn.style.display = 'none';
            }
        }
        if (apkBtn) {
            if (onAndroid && data.update_available && data.apk_download_url) {
                apkBtn.disabled = false;
                apkBtn.textContent = data.ota_supported
                    ? `Download Full APK v${data.latest_version || ''}`
                    : `Download & Install APK v${data.latest_version || ''}`;
                apkBtn.style.display = 'block';
                if (data.ota_supported) {
                    apkBtn.className = 'btn-sm btn-copy-sm';
                } else {
                    apkBtn.className = 'btn-sm btn-vlc-sm';
                }
            } else {
                apkBtn.style.display = 'none';
            }
        }
        if (extLink) {
            if (onAndroid && data.update_available && data.apk_download_url) {
                extLink.href = data.apk_download_url;
                extLink.style.display = 'block';
            } else {
                extLink.style.display = 'none';
            }
        }
        if (applyBtn) {
            if (!onAndroid && data.update_available && data.download_url) {
                applyBtn.disabled = false;
                applyBtn.textContent = `Download & Install v${data.latest_version || ''}`;
                applyBtn.style.display = 'block';
            } else {
                applyBtn.style.display = 'none';
            }
        }
    }

    window.__shApkUpdateProgress = function(pct, state, msg) {
        const wrap = document.getElementById('updateProgWrap');
        const fill = document.getElementById('updateProgFill');
        const box = document.getElementById('updateStatusBox');
        const apkBtn = document.getElementById('btnApkUpdate');
        const spWrap = document.getElementById('startupUpdProgWrap');
        const spFill = document.getElementById('startupUpdProgFill');
        const spSt = document.getElementById('startupUpdStatus');
        const spBtn = document.getElementById('btnStartupUpdateNow');
        if (wrap) wrap.style.display = 'block';
        if (fill && typeof pct === 'number') fill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
        if (spWrap) spWrap.style.display = 'block';
        if (spFill && typeof pct === 'number') spFill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
        if (box && msg) {
            box.style.color = state === 'error' ? '#fca5a5' : '#86efac';
            box.textContent = msg;
        }
        if (spSt && msg) {
            spSt.style.display = 'block';
            spSt.style.color = state === 'error' ? '#fca5a5' : '#86efac';
            spSt.textContent = msg;
        }
        const ver = (pendingUpdateInfo && pendingUpdateInfo.latest_version) || '';
        if (apkBtn) {
            if (state === 'downloading') {
                apkBtn.disabled = true;
                apkBtn.textContent = `Downloading APK ${pct || 0}%...`;
            } else if (state === 'error' || state === 'need_permission' || state === 'installer_opened') {
                apkBtn.disabled = false;
                apkBtn.textContent = state === 'need_permission'
                    ? `Tap after granting permission (v${ver})`
                    : `Download & Install APK v${ver}`;
            }
        }
        if (spBtn) {
            if (state === 'downloading') {
                spBtn.disabled = true;
                spBtn.textContent = `Downloading ${pct || 0}%...`;
            } else if (state === 'error' || state === 'need_permission' || state === 'installer_opened') {
                spBtn.disabled = false;
                spBtn.textContent = state === 'need_permission'
                    ? `Allow Permission & Install v${ver}`
                    : `Install v${ver}`;
            }
        }
        if (state === 'error' && msg) showToast(msg, 'err');
        if (state === 'need_permission') showToast('Allow "Install unknown apps", then tap Install', 'ok');
    };

    async function applyOtaPyUpdateNow(fromPopup) {
        if (!pendingUpdateInfo || !pendingUpdateInfo.py_download_url) return;
        const btn = document.getElementById(fromPopup ? 'btnStartupUpdateNow' : 'btnOtaPyUpdate');
        const wrap = document.getElementById(fromPopup ? 'startupUpdProgWrap' : 'updateProgWrap');
        const fill = document.getElementById(fromPopup ? 'startupUpdProgFill' : 'updateProgFill');
        const box = document.getElementById(fromPopup ? 'startupUpdStatus' : 'updateStatusBox');
        if (btn) { btn.disabled = true; btn.textContent = 'Installing update...'; }
        if (wrap) wrap.style.display = 'block';
        if (box && fromPopup) box.style.display = 'block';
        if (fill) fill.style.width = '45%';
        try {
            const resp = await fetch('/api/apply_ota_py', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    py_url: pendingUpdateInfo.py_download_url,
                    sha256: pendingUpdateInfo.py_sha256 || '',
                    version: pendingUpdateInfo.latest_version || ''
                })
            });
            const st = await resp.json();
            if (!st.success) {
                if (fill) fill.style.width = '0%';
                if (box) { box.style.color = '#fca5a5'; box.textContent = st.error || 'Instant update failed'; }
                if (btn) { btn.disabled = false; btn.textContent = `Instant Update v${pendingUpdateInfo.latest_version || ''}`; }
                showToast(st.error || 'Instant update failed', 'err');
                return;
            }
            if (fill) fill.style.width = '100%';
            if (box) { box.style.color = '#86efac'; box.textContent = st.message || 'Restarting app...'; }
            if (btn) btn.textContent = 'Restarting app...';
            showToast('Update ready! Restarting app...', 'ok');
            setTimeout(() => {
                if (window.StreamHubAndroid && typeof window.StreamHubAndroid.restartApp === 'function') {
                    window.StreamHubAndroid.restartApp();
                } else {
                    window.location.reload();
                }
            }, 900);
        } catch (e) {
            if (btn) btn.disabled = false;
            showToast('Instant update error: ' + e.message, 'err');
        }
    }

    function applyAndroidApkUpdateNow() {
        if (!pendingUpdateInfo || !pendingUpdateInfo.apk_download_url) return;
        const url = pendingUpdateInfo.apk_download_url;
        const sha = pendingUpdateInfo.apk_sha256 || '';
        if (window.StreamHubAndroid && typeof window.StreamHubAndroid.installApkUpdate === 'function') {
            window.StreamHubAndroid.installApkUpdate(url, sha);
        } else if (window.StreamHubAndroid && typeof window.StreamHubAndroid.openExternalUrl === 'function') {
            window.StreamHubAndroid.openExternalUrl(url);
        } else {
            window.open(url, '_blank', 'noopener');
        }
    }

    async function clearCacheAndRefresh() {
        try { localStorage.removeItem('sh_home_cache_v2'); } catch (e) {}
        closeUpdateModal();
        showToast('Refreshing latest movies from HDHub4u...');
        await loadHomeHub(true);
    }

    async function checkAppUpdate(manual, inModal, silentBg) {
        const chip = document.getElementById('btnAppVersionChip');
        const navPill = document.getElementById('btnNavUpdatePill');
        const navDot = document.getElementById('navSettingsDot');
        const dockDot = document.getElementById('dockSettingsDot');
        const modeTxt = document.getElementById('deviceModeText');
        const box = document.getElementById('updateStatusBox');
        const chkBtn = document.getElementById('btnCheckUpdateBig');
        if (inModal && box) box.textContent = 'Checking GitHub for updates...';
        if (inModal && chkBtn) { chkBtn.disabled = true; chkBtn.textContent = 'Checking...'; }
        try {
            const resp = await fetch((manual ? '/api/check_update?_t=' : '/api/app_info?_t=') + Date.now(), { cache: 'no-store' });
            const data = await resp.json();
            if (data && data.version) {
                const hasUpd = !!data.update_available;
                if (navDot) navDot.style.display = hasUpd ? 'block' : 'none';
                if (dockDot) dockDot.style.display = hasUpd ? 'block' : 'none';
                if (chip) {
                    if (hasUpd) {
                        chip.textContent = `Update v${data.latest_version}`;
                        chip.classList.add('active');
                    } else {
                        chip.textContent = isAndroidApp()
                            ? `v${data.version} • Settings`
                            : `v${data.version} • ${data.libvlc_ready ? 'libVLC Ready' : 'Web Mode'}`;
                        chip.classList.remove('active');
                    }
                }
                const setUpdBadge = document.getElementById('settingsUpdateBadge');
                const setUpdSub = document.getElementById('settingsUpdateSub');
                if (setUpdBadge) {
                    if (hasUpd) {
                        setUpdBadge.textContent = `New v${data.latest_version}`;
                        setUpdBadge.style.background = 'rgba(34,197,94,0.18)';
                        setUpdBadge.style.color = '#86efac';
                    } else {
                        setUpdBadge.textContent = `Up to Date • v${data.version}`;
                        setUpdBadge.style.background = 'var(--surface-2)';
                        setUpdBadge.style.color = 'var(--text-muted)';
                    }
                }
                if (setUpdSub) {
                    setUpdSub.textContent = hasUpd
                        ? `New update v${data.latest_version} available — tap to install`
                        : `Installed: v${data.version} • GitHub auto-update active`;
                }
                if (navPill) {
                    const navLbl = document.getElementById('btnNavUpdateLabel');
                    if (hasUpd) {
                        if (navLbl) navLbl.textContent = `v${data.latest_version}`;
                        navPill.style.display = 'inline-flex';
                    } else {
                        navPill.style.display = 'none';
                    }
                }
                if (hasUpd && isAndroidApp() && modeTxt) {
                    modeTxt.textContent = `Update v${data.latest_version}`;
                }
            }
            if (inModal) renderUpdateModalState(data);
            else if (silentBg) {
                if (data && data.update_available) {
                    pendingUpdateInfo = data;
                    openStartupUpdatePopup(data);
                } else if ((!data || data.success === false) && !startupUpdRetried) {
                    startupUpdRetried = true;
                    setTimeout(() => checkAppUpdate(true, false, true), 3500);
                }
            } else if (manual) {
                showToast(data.message || `StreamHub v${data.version || '2.5.6'} active`);
            }
        } catch (e) {
            if (inModal && box) box.textContent = 'Update check error';
            else if (silentBg && !startupUpdRetried) {
                startupUpdRetried = true;
                setTimeout(() => checkAppUpdate(true, false, true), 3500);
            } else if (manual && !silentBg) showToast('Update check failed', 'err');
        } finally {
            if (inModal && chkBtn) { chkBtn.disabled = false; chkBtn.textContent = 'Check for Updates'; }
        }
    }

    async function saveUpdateFeedAndCheck() {
        const inp = document.getElementById('updateFeedInput');
        const btn = document.getElementById('btnCheckNow');
        const box = document.getElementById('updateStatusBox');
        if (btn) btn.disabled = true;
        if (box) box.textContent = 'Saving feed & checking latest release...';
        try {
            const resp = await fetch('/api/update_config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ update_url: (inp ? inp.value : '').trim() })
            });
            const data = await resp.json();
            renderUpdateModalState(data);
        } catch (e) {
            if (box) box.textContent = 'Save/check error: ' + e.message;
        }
        if (btn) btn.disabled = false;
    }

    async function applyAppUpdateNow() {
        if (!pendingUpdateInfo || !pendingUpdateInfo.download_url) return;
        const btn = document.getElementById('btnApplyUpdate');
        const wrap = document.getElementById('updateProgWrap');
        const fill = document.getElementById('updateProgFill');
        const box = document.getElementById('updateStatusBox');
        if (btn) { btn.disabled = true; btn.textContent = 'Starting download...'; }
        if (wrap) wrap.style.display = 'block';
        try {
            await fetch('/api/apply_update', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    download_url: pendingUpdateInfo.download_url,
                    sha256: pendingUpdateInfo.sha256 || '',
                    version: pendingUpdateInfo.latest_version || ''
                })
            });
            clearInterval(updatePollTimer);
            updatePollTimer = setInterval(async () => {
                try {
                    const r = await fetch('/api/update_status');
                    const st = await r.json();
                    if (fill) fill.style.width = `${st.pct || 0}%`;
                    if (btn) btn.textContent = st.state === 'downloading' ? `Downloading ${st.pct || 0}%...` : (st.message || st.state);
                    if (box && st.message) box.textContent = st.message;
                    if (st.state === 'error' || st.state === 'staged' || st.state === 'ready_restart') {
                        clearInterval(updatePollTimer);
                        updatePollTimer = null;
                        if (st.state === 'error' && btn) btn.disabled = false;
                        if (st.state === 'ready_restart') showToast('App updating & restarting...', 'ok');
                    }
                } catch (e) {
                    clearInterval(updatePollTimer);
                    updatePollTimer = null;
                    if (box) box.textContent = 'Restarting into new StreamHub version — reload this page in 3 seconds...';
                }
            }, 600);
        } catch (e) {
            if (btn) btn.disabled = false;
            showToast('Update start failed: ' + e.message, 'err');
        }
    }

    async function runSetupAction(action) {
        try {
            const resp = await fetch('/api/setup_action', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action })
            });
            const data = await resp.json();
            showToast(data.message || data.error || 'Done', data.success ? 'ok' : 'err');
        } catch (e) {
            showToast('Setup error: ' + e.message, 'err');
        }
    }

    function fmtHms(sec) {
        sec = Math.max(0, Math.floor(sec || 0));
        const h = Math.floor(sec / 3600);
        const m = Math.floor((sec % 3600) / 60);
        const s = sec % 60;
        return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
    }

    function openRemoteModal() {
        document.getElementById('remoteModalOverlay').classList.add('open');
        pollVlcRemote();
        clearInterval(remoteTimer);
        remoteTimer = setInterval(pollVlcRemote, 1500);
    }

    function closeRemoteModal() {
        document.getElementById('remoteModalOverlay').classList.remove('open');
        clearInterval(remoteTimer);
        remoteTimer = null;
    }

    async function pollVlcRemote() {
        try {
            const resp = await fetch('/api/vlc_remote');
            const st = await resp.json();
            updateRemoteUi(st);
        } catch (e) {}
    }

    async function sendVlcCmd(cmd, val) {
        try {
            const resp = await fetch('/api/vlc_remote', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: cmd, val: val !== undefined ? val : '' })
            });
            const st = await resp.json();
            updateRemoteUi(st);
            if (!st.connected) showToast('PC VLC is not running right now', 'err');
        } catch (e) {}
    }

    function remoteSeekTo(sec) {
        sendVlcCmd('seek', Math.floor(Number(sec) || 0));
    }

    function remoteSetVolume(pct) {
        const v = Math.round((Number(pct) || 0) * 256 / 100);
        document.getElementById('remoteVolText').textContent = pct + '%';
        sendVlcCmd('volume', v);
    }

    function updateRemoteUi(st) {
        const chip = document.getElementById('remoteStatusChip');
        const now = document.getElementById('remoteNowPlaying');
        const playBtn = document.getElementById('btnRemotePlayPause');
        const seekBar = document.getElementById('remoteSeekBar');
        const volBar = document.getElementById('remoteVolBar');
        const volTxt = document.getElementById('remoteVolText');

        if (!st || !st.connected) {
            chip.textContent = 'PC VLC Offline — start a movie in VLC on your PC';
            chip.style.color = '#fca5a5';
            playBtn.textContent = 'Play / Pause';
            return;
        }
        chip.textContent = `Connected • ${String(st.state || 'playing').toUpperCase()} (${st.rate || 1}x)`;
        chip.style.color = '#6ee7b7';
        now.textContent = st.title || activeTitle || 'Playing in PC VLC';
        playBtn.textContent = st.state === 'playing' ? 'Pause' : 'Play';

        remoteLastLen = st.length || 0;
        seekBar.max = Math.max(100, remoteLastLen);
        seekBar.value = Math.min(seekBar.max, st.time || 0);
        document.getElementById('remoteTimeCur').textContent = fmtHms(st.time);
        document.getElementById('remoteTimeDur').textContent = fmtHms(remoteLastLen);

        volBar.value = st.volume_pct !== undefined ? st.volume_pct : 100;
        volTxt.textContent = (st.volume_pct !== undefined ? st.volume_pct : 100) + '%';
        const aDel = document.getElementById('remoteAudDelay');
        if (aDel) aDel.textContent = `${st.audiodelay_ms || 0}ms`;
        const sDel = document.getElementById('remoteSubDelay');
        if (sDel) sDel.textContent = `${st.subdelay_ms || 0}ms`;
        const rNight = document.getElementById('btnRemoteNight');
        if (rNight) rNight.style.background = st.night_boost ? 'rgba(109,40,217,0.45)' : '';
        const rTop = document.getElementById('btnRemoteTop');
        if (rTop) rTop.style.background = st.topmost ? 'rgba(2,132,199,0.45)' : '';

        const aSel = document.getElementById('remoteAudioSelect');
        const aSig = JSON.stringify(st.audio_streams || []);
        if (aSel.dataset.sig !== aSig) {
            aSel.dataset.sig = aSig;
            aSel.innerHTML = '';
            (st.audio_streams || []).forEach(s => {
                const o = document.createElement('option');
                o.value = s.id;
                o.textContent = s.label;
                aSel.appendChild(o);
            });
        }
        const sSel = document.getElementById('remoteSubSelect');
        const sSig = JSON.stringify(st.subtitle_streams || []);
        if (sSel.dataset.sig !== sSig) {
            sSel.dataset.sig = sSig;
            sSel.innerHTML = '<option value="">Select Subtitle...</option><option value="-1">Subtitles Off</option>';
            (st.subtitle_streams || []).forEach(s => {
                const o = document.createElement('option');
                o.value = s.id;
                o.textContent = s.label;
                sSel.appendChild(o);
            });
        }
    }

    function copyTextFallback(text, okMsg) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(() => showToast(okMsg)).catch(() => prompt('Copy link:', text));
        } else {
            prompt('Copy link:', text);
        }
    }

    function copyLanUrl() {
        const url = lanBaseUrl || window.location.origin;
        copyTextFallback(url, 'Phone Web URL copied: ' + url);
    }

    function copyPhoneLink() {
        if (!activeSid) return;
        const url = (lanBaseUrl || window.location.origin) + '/phone/' + activeSid;
        copyTextFallback(url, 'Phone VLC Launcher link copied');
    }

    function copyActiveStreamUrl() {
        if (!activeStreamUrl) { showToast('Please resolve a movie link first', 'err'); return; }
        let target = activeStreamUrl;
        if (activeCompressMethod === -1) {
            target = activeStreamUrl;
        } else if (activeSid) {
            const base = isMobileDevice() ? window.location.origin : (lanBaseUrl || window.location.origin);
            target = `${base}/s/${activeSid}.mkv`;
        } else if (activeBaseOff || activeSubSize !== null || activeCompressMethod) {
            const seekQ = activeSeekUrl ? `&seek_url=${encodeURIComponent(activeSeekUrl)}` : '';
            target = `${window.location.origin}/api/stream?url=${encodeURIComponent(activeStreamUrl)}${seekQ}&off=${activeBaseOff}&sz=${activeSubSize || 0}&cm=${activeCompressMethod}`;
        }
        copyTextFallback(target, 'Stream URL copied to clipboard');
    }

    function copyCustomUrl(encodedUrl, sid) {
        const url = decodeURIComponent(encodedUrl);
        if (url.includes('.m3u8')) {
            copyTextFallback(url, 'HLS Stream URL copied');
            return;
        }
        if (sid) {
            const base = isMobileDevice() ? window.location.origin : (lanBaseUrl || window.location.origin);
            copyTextFallback(`${base}/s/${sid}.mkv`, 'Proxy Stream URL copied');
            return;
        }
        copyTextFallback(url, 'Stream URL copied');
    }
</script>
</body>
</html>"""

# ── HTTP Server Request Handler ────────────────────────────────────────────────

class RequestHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if args and str(args[1]) not in ('200', '304'):
            super().log_message(fmt, *args)

    def _request_origin(self):
        host = (self.headers.get('Host') or '').strip()
        if host:
            return f"http://{host}"
        return f"http://{get_lan_ip()}:{PORT}"

    def _parse_zip_qp(self, qp):
        base_off = int(qp.get('off', ['0'])[0] or 0)
        sz_raw = qp.get('sz', [''])[0]
        sub_sz = int(sz_raw) if sz_raw and int(sz_raw) > 0 else None
        cm = int(qp.get('cm', ['0'])[0] or 0)
        return base_off, sub_sz, cm

    def _resolve_stream_request(self, spath, qp):
        m_short = re.match(r'^/s/([a-fA-F0-9]+)(?:\.(?:mkv|mp4))?$', spath)
        if m_short:
            info = get_short_stream(m_short.group(1))
            if not info:
                return ('not_found', None)
            return ('ok', (info['stream_url'], info['seek_url'], info['base_offset'], info['sub_size'], info['compress_method'], info.get('backup_urls') or []))
        if spath == '/api/stream':
            raw_target  = qp.get('url', [''])[0]
            seek_target = qp.get('seek_url', [''])[0]
            sid_hint    = qp.get('sid', [''])[0].strip()
            base_off, sub_sz, cm = self._parse_zip_qp(qp)
            if not raw_target:
                return ('missing_url', None)
            backups = []
            if sid_hint:
                sinfo = get_short_stream(sid_hint)
                if sinfo:
                    backups = sinfo.get('backup_urls') or []
                    if not seek_target:
                        seek_target = sinfo.get('seek_url', '') or ''
            return ('ok', (raw_target, seek_target, base_off, sub_sz, cm, backups))
        return ('other', None)

    def do_HEAD(self):
        parsed = urllib.parse.urlparse(self.path)
        qp     = urllib.parse.parse_qs(parsed.query)
        spath  = parsed.path
        kind, params = self._resolve_stream_request(spath, qp)
        if kind == 'not_found':
            self.send_response(404); self.end_headers(); return
        if kind == 'ok':
            raw_target, seek_target, base_off, sub_sz, cm, backups = params
            sess = get_stream_session(raw_target, seek_target, base_off, sub_sz, cm, backup_urls=backups)
            sess.ensure_init()
            can_seek = (cm != 8 and bool(sess.seek_candidates(seek_target or '')))
            self.send_response(200)
            self.send_header('Content-Type', sess.content_type)
            self.send_header('Accept-Ranges', 'bytes' if can_seek else 'none')
            if sess.total_size:
                self.send_header('Content-Length', str(sess.total_size))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()

    def _send_stream_headers(self, status, ctype, start_b, end_b, total_sz, has_range, accept_ranges='bytes'):
        self.send_response(status)
        self.send_header('Content-Type', ctype or 'video/x-matroska')
        self.send_header('Accept-Ranges', accept_ranges)
        self.send_header('Access-Control-Allow-Origin', '*')
        if status == 206 and has_range and total_sz and end_b is not None:
            self.send_header('Content-Range', f'bytes {start_b}-{end_b}/{total_sz}')
        if end_b is not None and end_b >= start_b:
            self.send_header('Content-Length', str(end_b - start_b + 1))
        self.end_headers()

    def _pipe_and_cache(self, sess, upstream_url, resp, cur_pos, end_byte):
        """Streams response bytes non-blockingly via read1(), caches 2MB LRU blocks, and auto-resumes across mirrors."""
        can_resume = bool(is_range_capable(upstream_url) or sess.seek_candidates(upstream_url))
        blk_idx = cur_pos // LRU_BLOCK_SIZE
        blk_off = cur_pos % LRU_BLOCK_SIZE
        blk_buf = bytearray() if blk_off == 0 else None
        resumes = 0
        chunks_written = 0

        try:
            bw_bytes = 0
            bw_t0 = time.time()
            while True:
                try:
                    read_fn = getattr(resp, 'read1', resp.read)
                    while True:
                        want = 65536 if chunks_written < 4 else 131072
                        if end_byte is not None:
                            rem = end_byte - cur_pos + 1
                            if rem <= 0:
                                _record_stream_bw(bw_bytes, time.time() - bw_t0)
                                return
                            want = min(want, rem)
                        chunk = read_fn(want)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        chunks_written += 1
                        bw_bytes += len(chunk)
                        if bw_bytes >= 524288:
                            _record_stream_bw(bw_bytes, time.time() - bw_t0)
                            bw_bytes = 0
                            bw_t0 = time.time()
                        if chunks_written <= 4:
                            try:
                                self.wfile.flush()
                            except Exception:
                                return
                        # Accumulate aligned 2MB blocks for instant recent seeks
                        pos = 0
                        while pos < len(chunk):
                            c_idx = cur_pos // LRU_BLOCK_SIZE
                            if blk_buf is None:
                                if cur_pos % LRU_BLOCK_SIZE == 0:
                                    blk_idx = c_idx
                                    blk_buf = bytearray()
                                else:
                                    # Jump to next block boundary
                                    skip = min(len(chunk) - pos, LRU_BLOCK_SIZE - (cur_pos % LRU_BLOCK_SIZE))
                                    cur_pos += skip
                                    pos += skip
                                    continue
                            take = min(len(chunk) - pos, LRU_BLOCK_SIZE - len(blk_buf))
                            blk_buf.extend(chunk[pos:pos + take])
                            cur_pos += take
                            pos += take
                            if len(blk_buf) == LRU_BLOCK_SIZE:
                                sess.put_lru_bytes(blk_idx * LRU_BLOCK_SIZE, bytes(blk_buf))
                                blk_buf = None
                finally:
                    try:
                        resp.close()
                    except Exception:
                        pass

                # Check if stream finished naturally or dropped early
                if end_byte is None or cur_pos > end_byte or not can_resume or resumes >= 5:
                    break
                resumes += 1
                rng = sess.upstream_range(cur_pos, end_byte)
                try:
                    resp, st, upstream_url = sess.open_seek_range(upstream_url, rng, cur_pos=cur_pos, timeout=12)
                    if st not in (200, 206):
                        resp.close()
                        break
                except Exception:
                    break
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception:
            pass

    def _stream_deflated_zip_entry(self, raw_target, base_off, sub_sz):
        """Sequential inflate stream for DEFLATE (method=8) remote ZIP entries (no random seek)."""
        try:
            resp, st = open_upstream_range(raw_target, range_hdr=f'bytes={base_off}-', hedges=(3, 6, 8), timeout=12)
        except Exception as e:
            self.send_response(502); self.end_headers()
            self.wfile.write(str(e).encode()); return
        with resp:
            if st not in (200, 206):
                self.send_response(502); self.end_headers(); return
            self.send_response(200)
            self.send_header('Content-Type', 'video/x-matroska')
            self.send_header('Accept-Ranges', 'none')
            self.send_header('Access-Control-Allow-Origin', '*')
            if sub_sz:
                self.send_header('Content-Length', str(sub_sz))
            self.end_headers()
            decomp = zlib.decompressobj(-15)
            sent = 0
            try:
                while not decomp.eof:
                    raw = resp.read(256 * 1024)
                    if not raw:
                        break
                    out = decomp.decompress(raw)
                    if out:
                        if sub_sz and sent + len(out) > sub_sz:
                            out = out[:sub_sz - sent]
                        self.wfile.write(out)
                        sent += len(out)
                        if sub_sz and sent >= sub_sz:
                            break
                tail = decomp.flush()
                if tail and (not sub_sz or sent < sub_sz):
                    self.wfile.write(tail)
            except Exception:
                pass

    def _stream_offline_file(self, qp):
        ok_vip, vip_err = _require_vip_or_error()
        if not ok_vip:
            self._json(vip_err, status=403); return
        dl_id = (qp.get('id') or [''])[0].strip()
        if not dl_id:
            self.send_response(400); self.end_headers(); return
        entry = _get_offline_entry(dl_id)
        if not entry or entry.get('status') != 'completed':
            self.send_response(404); self.end_headers()
            self.wfile.write(b'Offline movie not found'); return
        fpath = _offline_item_path(entry)
        if not fpath or not os.path.isfile(fpath):
            self.send_response(404); self.end_headers()
            self.wfile.write(b'Offline file missing on disk'); return

        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
        except Exception:
            pass

        total_sz = os.path.getsize(fpath)
        range_hdr = self.headers.get('Range', '')
        start_byte = 0
        end_byte = max(0, total_sz - 1) if total_sz > 0 else 0
        is_partial = False
        if range_hdr and total_sz > 0:
            m_sfx = re.search(r'bytes=-(\d+)$', range_hdr)
            m_rng = re.search(r'bytes=(\d+)-(\d*)', range_hdr)
            if m_sfx:
                sfx = int(m_sfx.group(1))
                start_byte = max(0, total_sz - sfx)
                end_byte = total_sz - 1
                is_partial = True
            elif m_rng:
                start_byte = int(m_rng.group(1))
                if m_rng.group(2):
                    end_byte = min(int(m_rng.group(2)), total_sz - 1)
                is_partial = True

        if total_sz > 0 and start_byte >= total_sz:
            self.send_response(416)
            self.send_header('Content-Range', f'bytes */{total_sz}')
            self.end_headers()
            return

        content_len = (end_byte - start_byte + 1) if total_sz > 0 else 0
        ctype = 'video/mp4' if fpath.lower().endswith('.mp4') else 'video/x-matroska'
        self.send_response(206 if is_partial else 200)
        self.send_header('Content-Type', ctype)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(content_len))
        if is_partial:
            self.send_header('Content-Range', f'bytes {start_byte}-{end_byte}/{total_sz}')
        self.end_headers()

        _offline_stream_enter(dl_id)
        try:
            with open(fpath, 'rb') as fp:
                fp.seek(start_byte)
                remaining = content_len
                while remaining > 0:
                    chunk = fp.read(min(262144, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            _offline_stream_exit(dl_id)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qp     = urllib.parse.parse_qs(parsed.query)
        path   = parsed.path

        # High-Speed Streaming Proxy (/api/stream or compact /s/<sid>.mkv)
        stream_kind, stream_params = self._resolve_stream_request(path, qp)
        if stream_kind == 'not_found':
            self.send_response(404); self.end_headers()
            self.wfile.write(b'Stream not found or expired'); return
        if stream_kind == 'missing_url':
            self.send_response(400); self.end_headers()
            self.wfile.write(b'Missing url param'); return
        if stream_kind == 'ok':
            raw_target, seek_target, base_off, sub_sz, cm, backups = stream_params

            try:
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
            except Exception:
                pass

            if cm == 8:
                self._stream_deflated_zip_entry(raw_target, base_off, sub_sz)
                return

            sess = get_stream_session(raw_target, seek_target, base_off, sub_sz, cm, backup_urls=backups)
            sess.ensure_init()
            total_sz = sess.total_size

            range_hdr  = self.headers.get('Range', '')
            start_byte = 0
            end_byte   = (total_sz - 1) if total_sz else None

            if range_hdr:
                m_sfx = re.search(r'bytes=-(\d+)$', range_hdr)
                m_rng = re.search(r'bytes=(\d+)-(\d*)', range_hdr)
                if m_sfx:
                    sfx = int(m_sfx.group(1))
                    if total_sz:
                        start_byte = max(0, total_sz - sfx)
                        end_byte   = total_sz - 1
                elif m_rng:
                    start_byte = int(m_rng.group(1))
                    if m_rng.group(2):
                        eb = int(m_rng.group(2))
                        end_byte = min(eb, total_sz - 1) if total_sz else eb

            if total_sz and start_byte >= total_sz:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{total_sz}')
                self.end_headers()
                return

            seek_cands = sess.seek_candidates(seek_target or '')
            can_seek = (cm != 8 and bool(seek_cands))
            accept_rng = 'bytes' if can_seek else 'none'
            out_status = 206 if (range_hdr and can_seek) else 200

            # 1. Instant 0ms Tail Cache Hit (MKV Cues)
            if sess.tail_buf and sess.tail_start is not None and start_byte >= sess.tail_start:
                off = start_byte - sess.tail_start
                want = (end_byte - start_byte + 1) if end_byte is not None else len(sess.tail_buf)
                data = sess.tail_buf[off:off + want]
                self._send_stream_headers(out_status, sess.content_type, start_byte, start_byte + len(data) - 1, total_sz, bool(range_hdr), accept_ranges=accept_rng)
                try:
                    self.wfile.write(data)
                except Exception:
                    pass
                return

            # Fast-fail duplicate MKV tail probes if worker quota is exhausted on tail
            if total_sz and start_byte >= max(0, total_sz - 2 * 1024 * 1024) and not sess.tail_buf and (time.time() - sess.tail_failed_at) < 20.0:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{total_sz}')
                self.end_headers()
                return

            # 2. Instant Header Cache Hit (0..3MB) + Seamless Upstream Continuation (Google CDN 30MB/s or R2)
            if sess.head_buf and start_byte < len(sess.head_buf):
                eff_end = end_byte if end_byte is not None else (total_sz - 1 if total_sz else len(sess.head_buf) - 1)
                self._send_stream_headers(out_status, sess.content_type, start_byte, eff_end, total_sz, bool(range_hdr), accept_ranges=accept_rng)
                want_total = eff_end - start_byte + 1
                part = sess.head_buf[start_byte:start_byte + want_total]
                try:
                    for i in range(0, len(part), 65536):
                        self.wfile.write(part[i:i + 65536])
                except Exception:
                    return

                cur_pos = start_byte + len(part)
                if cur_pos > eff_end:
                    return

                # Continue streaming past 3MB from fast raw_target (Google CDN or R2)
                try:
                    if is_range_capable(raw_target):
                        resp, st = open_upstream_range(raw_target, range_hdr=sess.upstream_range(cur_pos, eff_end), hedges=(3, 6, 8), timeout=12)
                    else:
                        # Google CDN ignores Range: open at 0 and skip <= 3MB (takes <0.1s at 30MB/s!)
                        resp, st = open_upstream_range(raw_target, timeout=15)
                        rem_skip = cur_pos
                        while rem_skip > 0:
                            d = resp.read(min(262144, rem_skip))
                            if not d:
                                break
                            rem_skip -= len(d)
                except Exception:
                    return
                self._pipe_and_cache(sess, raw_target, resp, cur_pos, eff_end)
                return

            # 3. Mid-file / Seek Requests (>= 3MB): Check 2MB LRU RAM Cache first, else Seek Upstream across mirrors
            lru_hit = sess.get_lru_slice(start_byte, (end_byte - start_byte + 1) if end_byte is not None else LRU_BLOCK_SIZE)
            seek_upstream = seek_cands[0] if seek_cands else ''

            if not lru_hit and not seek_upstream:
                self.send_response(416)
                if total_sz:
                    self.send_header('Content-Range', f'bytes */{total_sz}')
                self.end_headers()
                return

            cur_pos = start_byte
            headers_sent = False
            if lru_hit:
                eff_end = end_byte if end_byte is not None else (total_sz - 1 if total_sz else start_byte + len(lru_hit) - 1)
                self._send_stream_headers(out_status, sess.content_type, start_byte, eff_end, total_sz, bool(range_hdr), accept_ranges=accept_rng)
                headers_sent = True
                try:
                    self.wfile.write(lru_hit)
                    self.wfile.flush()
                except Exception:
                    return
                cur_pos += len(lru_hit)
                if end_byte is not None and cur_pos > eff_end:
                    return

            if not seek_upstream:
                return

            rng_str = sess.upstream_range(cur_pos, end_byte)
            try:
                resp, st, seek_upstream = sess.open_seek_range(seek_target or '', rng_str, cur_pos=cur_pos, timeout=12)
            except Exception:
                if total_sz and start_byte >= max(0, total_sz - 2 * 1024 * 1024):
                    sess.tail_failed_at = time.time()
                    if not headers_sent:
                        self.send_response(416)
                        self.send_header('Content-Range', f'bytes */{total_sz}')
                        self.end_headers()
                elif not headers_sent:
                    self.send_response(503); self.end_headers()
                return

            if st == 416:
                resp.close()
                if not headers_sent:
                    self.send_response(416)
                    if total_sz:
                        self.send_header('Content-Range', f'bytes */{total_sz}')
                    self.end_headers()
                return

            # Parse upstream Content-Range ONLY for non-ZIP files so archive total doesn't overwrite episode size
            if not sess.is_sub:
                up_cr = resp.headers.get('Content-Range', '')
                m_up = re.search(r'bytes\s+\d+-\d+/(\d+)', up_cr)
                if m_up:
                    actual_tot = int(m_up.group(1))
                    if not total_sz or actual_tot < total_sz:
                        sess.total_size = total_sz = actual_tot
                    if end_byte is not None:
                        end_byte = min(end_byte, total_sz - 1)
                elif not total_sz:
                    cl = resp.headers.get('Content-Length', '')
                    if cl:
                        end_byte = cur_pos + int(cl) - 1

            # Cache tail automatically if this was a tail request that succeeded
            if total_sz and start_byte >= max(0, total_sz - 2 * 1024 * 1024) and not sess.tail_buf and end_byte is not None and (end_byte - cur_pos + 1) <= 2 * 1024 * 1024:
                tail_data = resp.read()
                resp.close()
                if cur_pos == start_byte and tail_data:
                    sess.tail_start = start_byte
                    sess.tail_buf = tail_data
                if not headers_sent:
                    self._send_stream_headers(out_status, sess.content_type, start_byte, cur_pos + len(tail_data) - 1, total_sz, bool(range_hdr))
                try:
                    self.wfile.write(tail_data)
                except Exception:
                    pass
                return

            if not headers_sent:
                eff_end = end_byte if end_byte is not None else (total_sz - 1 if total_sz else None)
                self._send_stream_headers(out_status, sess.content_type, start_byte, eff_end, total_sz, bool(range_hdr))

            self._pipe_and_cache(sess, seek_upstream, resp, cur_pos, end_byte)
            return

        # Combined OTT Rails + Full Home Catalog API (cached 2m, parallel upstream)
        if path == '/api/home_hub':
            force_ref = qp.get('refresh', ['0'])[0] == '1'
            self._json(get_home_hub(force_refresh=force_ref))
            return

        # Latest HDHub4u Movies & Web Series API (with pagination & category support)
        if path == '/api/latest':
            try:
                page = int(qp.get('page', ['1'])[0] or 1)
            except Exception:
                page = 1
            cat = qp.get('cat', [''])[0]
            self._json(get_latest_hdhub4u_movies(page=page, category=cat))
            return

        # HDHub4u Search API (+ 4KHDHub fallback supplement when few results match)
        if path == '/api/search':
            q = qp.get('q', [''])[0]
            try:
                page = int(qp.get('page', ['1'])[0] or 1)
            except Exception:
                page = 1
            try:
                limit = int(qp.get('limit', ['24'])[0] or 24)
            except Exception:
                limit = 24
            src = qp.get('source', ['all'])[0].strip().lower()
            if src == '4khdhub':
                self._json(search_4khdhub(q, page=page, limit=limit))
                return
            hits = search_hdhub4u(q, page=page, limit=limit)
            if q.strip() and page == 1 and limit >= 10 and len(hits) < 4 and src != 'hdhub4u':
                try:
                    extra = search_4khdhub(q, page=1, limit=max(1, limit - len(hits)))
                    seen_p = {h.get('permalink') for h in hits}
                    for ex in extra:
                        if ex.get('permalink') not in seen_p:
                            hits.append(ex)
                            seen_p.add(ex.get('permalink'))
                except Exception:
                    pass
            self._json(hits[:limit])
            return

        # 4KHDHub Dedicated 4K Cinema APIs
        if path == '/api/4khub/latest':
            try:
                page = int(qp.get('page', ['1'])[0] or 1)
            except Exception:
                page = 1
            cat = qp.get('cat', [''])[0]
            self._json(get_latest_4k_movies(page=page, category=cat))
            return

        if path == '/api/4khub/search':
            q = qp.get('q', [''])[0]
            try:
                page = int(qp.get('page', ['1'])[0] or 1)
            except Exception:
                page = 1
            try:
                limit = int(qp.get('limit', ['24'])[0] or 24)
            except Exception:
                limit = 24
            self._json(search_4khdhub(q, page=page, limit=limit))
            return

        # Saved Playback Progress Store API
        if path == '/api/progress':
            key = qp.get('key', [''])[0].strip()
            items = _read_progress_disk()
            if key:
                self._json({"success": True, "item": items.get(key)})
            else:
                self._json({"success": True, "items": items})
            return

        # Online Subtitle Search API (OpenSubtitles-V3 + Cinemeta IMDb resolver)
        if path == '/api/subtitles/search':
            q = qp.get('q', [''])[0]
            imdb_id = qp.get('imdb_id', [''])[0]
            s_num = qp.get('season', [''])[0]
            e_num = qp.get('episode', [''])[0]
            self._json(search_online_subtitles(
                query=q,
                imdb_id=imdb_id,
                season=int(s_num) if s_num.isdigit() else None,
                episode=int(e_num) if e_num.isdigit() else None,
            ))
            return

        # Browser WebVTT Subtitle Track Endpoint
        if path == '/api/sub.vtt':
            sub_id = qp.get('id', [''])[0].strip()
            vtt = get_cached_sub_vtt(sub_id)
            if vtt is None:
                self.send_response(404); self.end_headers(); return
            raw_v = vtt.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/vtt; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(raw_v)))
            self.end_headers()
            self.wfile.write(raw_v)
            return

        # Real IMDb Ratings Batch API (Cinemeta)
        if path == '/api/ratings':
            ids_raw = qp.get('ids', [''])[0]
            items = [{"key": x.strip(), "imdb_id": x.strip()} for x in ids_raw.split(',') if x.strip()]
            self._json({"success": True, "ratings": fetch_imdb_ratings_batch(items) if items else {}})
            return

        # App Info & Auto-Update Check APIs (.exe / Updater Readiness)
        if path == '/api/app_info':
            self._json(get_app_info_payload())
            return

        if path == '/api/network_speed':
            self._json(get_network_speed_payload())
            return

        if path == '/api/vip/status':
            force_chk = qp.get('refresh', ['0'])[0] in ('1', 'true', 'yes')
            self._json(get_vip_status(verify_remote=force_chk))
            return

        if path == '/api/check_update':
            self._json(check_app_update())
            return

        if path == '/api/update_status':
            self._json(get_update_status())
            return

        # Real MKV Embedded Audio & Subtitle Detector API
        if path == '/api/media_info':
            sid = qp.get('sid', [''])[0].strip()
            info = get_short_stream(sid) if sid else None
            if not info:
                self._json({"success": False, "error": "Stream not found"}, status=404); return
            if info.get('compress_method', 0) != 0:
                self._json({"success": True, "audio_tracks": [], "subtitle_tracks": [], "video": {}, "duration_sec": 0}); return
            sess = get_stream_session(info['stream_url'], info['seek_url'], info['base_offset'], info['sub_size'], info['compress_method'])
            sess.ensure_init()
            mi = sess.media_info or parse_mkv_media_info(sess.head_buf, sess.tail_buf)
            self._json({"success": True, **mi})
            return

        # PC VLC HTTP Remote Status API (usable from phone or PC)
        if path == '/api/vlc_remote':
            self._json(vlc_remote_call())
            return

        # Parallel-Prefetch Sliced HLS Proxy (Zero-Crash libVLC Seek + Fast Browser hls.js)
        if path.startswith('/api/hls/'):
            sid_q = qp.get('sid', [''])[0].strip()
            raw_h_u = qp.get('url', [''])[0].strip()
            if sid_q and not raw_h_u:
                sinfo = get_short_stream(sid_q)
                if sinfo:
                    raw_h_u = sinfo.get('stream_url', '')
            if not raw_h_u:
                self.send_response(404); self.end_headers(); return
            hsess = get_hls_session(raw_h_u, sid=sid_q)

            if path == '/api/hls/info':
                hsess.ensure_init()
                self._json({
                    "success": bool(hsess.segments),
                    "sid": hsess.sid or sid_q,
                    "total_ms": hsess.total_ms,
                    "total_sec": round(hsess.total_sec, 2),
                    "segment_count": len(hsess.segments),
                })
                return

            if path == '/api/hls/slice_target':
                try:
                    t_ms = int(float(qp.get('t_ms', ['0'])[0] or 0))
                except Exception:
                    t_ms = 0
                origin = self._request_origin()
                self._json(hsess.slice_for_ms(t_ms, sid_override=sid_q, origin=origin))
                return

            if path == '/api/hls/playlist.m3u8':
                try:
                    t_ms = int(float(qp.get('t_ms', ['0'])[0] or 0))
                except Exception:
                    t_ms = 0
                if t_ms > 0:
                    s_info = hsess.slice_for_ms(t_ms, sid_override=sid_q, origin=self._request_origin())
                    m3u_txt, start_ms = hsess.render_playlist(s_info['seg_idx'], sid_override=sid_q)
                else:
                    m3u_txt, start_ms = hsess.render_playlist(0, sid_override=sid_q)
                if not m3u_txt:
                    self.send_response(502); self.end_headers(); return
                body = m3u_txt.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/vnd.apple.mpegurl; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-StreamHub-Start-Ms', str(start_ms))
                self.send_header('X-StreamHub-Total-Ms', str(hsess.total_ms))
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except Exception:
                    pass
                return

            if path == '/api/hls/seg':
                try:
                    seg_i = int(qp.get('i', ['0'])[0] or 0)
                except Exception:
                    seg_i = -1
                def _send_seg_head(ctype, clen):
                    self.send_response(200)
                    self.send_header('Content-Type', ctype or 'video/MP2T')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.send_header('Cache-Control', 'private, max-age=300')
                    if clen is not None:
                        self.send_header('Content-Length', str(clen))
                    self.end_headers()
                try:
                    ok = hsess.stream_segment_to(seg_i, _send_seg_head, self.wfile)
                    if not ok:
                        self.send_response(502); self.end_headers()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                except Exception:
                    pass
                return

            if path == '/api/hls/asset':
                asset_u = qp.get('u', [''])[0].strip()
                if not asset_u:
                    self.send_response(400); self.end_headers(); return
                try:
                    req_a = urllib.request.Request(make_safe_url(asset_u), headers={'User-Agent': USER_AGENT, 'Referer': 'https://hdstream4u.com/'})
                    with urllib.request.urlopen(req_a, timeout=12) as resp_a:
                        raw_a = resp_a.read()
                        ctype_a = resp_a.headers.get('Content-Type') or 'application/octet-stream'
                    self.send_response(200)
                    self.send_header('Content-Type', ctype_a)
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.send_header('Content-Length', str(len(raw_a)))
                    self.end_headers()
                    self.wfile.write(raw_a)
                except Exception:
                    try:
                        self.send_response(502); self.end_headers()
                    except Exception:
                        pass
                return

            self.send_response(404); self.end_headers()
            return

        # PWA Manifest, Service Worker, and Icons
        if path == '/manifest.webmanifest':
            manifest = json.dumps({
                "name": "StreamHub",
                "short_name": "StreamHub",
                "description": "Ultra HD 4K & HLS Movie & Web Series Streamer",
                "start_url": "/",
                "scope": "/",
                "display": "standalone",
                "background_color": "#07080d",
                "theme_color": "#07080d",
                "icons": [
                    {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
                    {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
                    {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}
                ]
            }).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/manifest+json; charset=utf-8')
            self.send_header('Content-Length', str(len(manifest)))
            self.end_headers()
            self.wfile.write(manifest)
            return

        if path == '/sw.js':
            sw_js = (
                b"self.addEventListener('install', e => self.skipWaiting());\n"
                b"self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));\n"
                b"self.addEventListener('fetch', e => { /* live LAN stream passthrough */ });\n"
            )
            self.send_response(200)
            self.send_header('Content-Type', 'application/javascript; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Content-Length', str(len(sw_js)))
            self.end_headers()
            self.wfile.write(sw_js)
            return

        if path in ('/icon-192.png', '/icon-512.png'):
            sz = 512 if '512' in path else 192
            png = make_app_icon_png(sz)
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Cache-Control', 'public, max-age=86400')
            self.send_header('Content-Length', str(len(png)))
            self.end_headers()
            self.wfile.write(png)
            return

        if path == '/icon.svg':
            svg = APP_ICON_SVG.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'image/svg+xml; charset=utf-8')
            self.send_header('Content-Length', str(len(svg)))
            self.end_headers()
            self.wfile.write(svg)
            return

        # 4KHDHub + HDHub4u Movie / Series Details API
        if path == '/api/movie':
            url = qp.get('url', [''])[0]
            self._json(parse_movie_details(url))
            return

        # Active LAN IP Info for QR & Phone Sharing
        if path == '/api/lan_info':
            lan_ip = get_lan_ip()
            srv_port = getattr(self.server, 'server_address', (None, _ACTIVE_PORT))[1] or _ACTIVE_PORT
            self._json({"ip": lan_ip, "port": srv_port, "url": f"http://{lan_ip}:{srv_port}"})
            return

        # Dynamic QR SVG Generator (always points to PC LAN IP so phones can scan)
        if path == '/api/qr.svg':
            srv_port = getattr(self.server, 'server_address', (None, _ACTIVE_PORT))[1] or _ACTIVE_PORT
            lan_origin = f"http://{get_lan_ip()}:{srv_port}"
            text = qp.get('text', [''])[0].strip()
            if not text or text == '/':
                text = lan_origin
            elif text.startswith('/'):
                text = lan_origin + text
            svg_bytes = make_qr_svg(text).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'image/svg+xml; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(svg_bytes)))
            self.end_headers()
            self.wfile.write(svg_bytes)
            return

        # Phone VLC 1-Tap Launcher Page (Target of per-movie/episode QR code)
        if path.startswith('/phone/'):
            sid = path[len('/phone/'):].strip('/')
            info = get_short_stream(sid)
            if not info:
                msg = (
                    '<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<title>Stream Expired</title></head><body style="background:#050811;color:#f8fafc;font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px;text-align:center;">'
                    '<div style="max-width:420px;background:#0b1120;border:1px solid #1e293b;border-radius:16px;padding:24px;">'
                    '<h2 style="color:#fbbf24;margin-bottom:10px;">Stream Expired</h2>'
                    '<p style="color:#94a3b8;font-size:0.9rem;margin-bottom:18px;">The server may have restarted. Open StreamHub and resolve the movie again.</p>'
                    '<a href="/" style="display:inline-block;background:#f59e0b;color:#000;padding:12px 20px;border-radius:10px;font-weight:800;text-decoration:none;">Open StreamHub</a>'
                    '</div></body></html>'
                ).encode('utf-8')
                self.send_response(404)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
                return

            title = info.get('title') or 'StreamHub Stream'
            size = info.get('size') or '4K UHD'
            cm = info.get('compress_method', 0)
            is_hls = (cm == -1 or '.m3u8' in (info.get('stream_url') or ''))
            if cm == 0 and not is_hls:
                get_stream_session(info['stream_url'], info['seek_url'], info['base_offset'], info['sub_size'], cm, backup_urls=info.get('backup_urls')).warm_async()
            elif is_hls:
                get_hls_session(info['stream_url'], sid=sid).warm_async()
            seek_badge = '⚡ HLS Instant Seek' if is_hls else ('⚡ Full Seekbar Scrubbing Active' if cm != 8 else '▶ Sequential Stream (No Seek)')
            watch_target = f'/api/hls/playlist.m3u8?sid={sid}&t_ms=0' if is_hls else ('/s/' + sid + '.mkv')
            watch_href = f"/watch?url={urllib.parse.quote(watch_target, safe='')}&t={urllib.parse.quote(title)}"
            pl_href = f"/api/playlist.m3u8?sid={urllib.parse.quote(sid, safe='')}&t={urllib.parse.quote(title)}"
            direct_url_json = json.dumps(info['stream_url']) if is_hls else '""'
            stream_preview = info['stream_url'] if is_hls else f'/s/{sid}.mkv'
            page = (
                PHONE_PLAY_PAGE
                .replace('__MOVIE_TITLE_HTML__', _html.escape(title))
                .replace('__MOVIE_SIZE_HTML__', _html.escape(size))
                .replace('__SEEK_BADGE_HTML__', seek_badge)
                .replace('__SID_JSON__', json.dumps(sid))
                .replace('__DIRECT_URL_JSON__', direct_url_json)
                .replace('__WATCH_HREF__', _html.escape(watch_href))
                .replace('__PLAYLIST_HREF__', _html.escape(pl_href))
                .replace('__STREAM_PATH_HTML__', _html.escape(stream_preview))
            ).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return

        # In-App HTML5 Web Player
        if path == '/watch':
            stream_url = qp.get('url', [''])[0]
            title = qp.get('t', ['4K Movie Stream'])[0]
            html = WATCH_PAGE.replace('__MOVIE_TITLE__', _html.escape(title)).replace('__STREAM_URL_JSON__', json.dumps(stream_url))
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html.encode('utf-8'))
            return

        # M3U8 Playlist File Generator
        if path == '/api/playlist.m3u8':
            sid        = qp.get('sid', [''])[0]
            stream_url = qp.get('url', [''])[0]
            seek_url   = qp.get('seek_url', [''])[0]
            title      = qp.get('t', ['4K Movie Stream'])[0]
            base_off, sub_sz, cm = self._parse_zip_qp(qp)
            info = get_short_stream(sid) if sid else None
            if info:
                stream_url, seek_url, base_off, sub_sz, cm = (
                    info['stream_url'], info['seek_url'], info['base_offset'], info['sub_size'], info['compress_method']
                )
            elif stream_url:
                sid = register_short_stream(stream_url, seek_url, base_off, sub_sz, cm, title)

            is_hls = (cm == -1 or '.m3u8' in stream_url or 'hdstream4u' in stream_url)
            if is_hls:
                vlc_opts = "#EXTVLCOPT:http-referrer=https://hdstream4u.com/\n#EXTVLCOPT:network-caching=400\n"
                target_pl_url = stream_url
            else:
                vlc_opts = (
                    "#EXTVLCOPT:network-caching=2000\n"
                    "#EXTVLCOPT:http-reconnect=true\n"
                    "#EXTVLCOPT:avcodec-fast=true\n"
                    "#EXTVLCOPT:avcodec-hurry-up=true\n"
                )
                origin = self._request_origin()
                if sid:
                    target_pl_url = f"{origin}/s/{sid}.mkv"
                else:
                    seek_param = f"&seek_url={urllib.parse.quote(seek_url, safe='')}" if seek_url else ""
                    zip_param = f"&off={base_off}&sz={sub_sz or 0}&cm={cm}" if (base_off or sub_sz or cm) else ""
                    target_pl_url = f"{origin}/api/stream?url={urllib.parse.quote(stream_url, safe='')}{seek_param}{zip_param}"
                if cm == 0 and stream_url:
                    get_stream_session(stream_url, seek_url, base_off, sub_sz, cm).warm_async()

            m3u = f"""#EXTM3U
#EXTINF:-1,{title}
{vlc_opts}{target_pl_url}
"""
            content = m3u.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/x-mpegurl')
            self.send_header('Content-Disposition', 'attachment; filename="streamhub.m3u8"')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        # User Settings & Private Offline Downloads APIs
        if path == '/api/settings':
            self._json(get_user_settings())
            return

        if path == '/api/downloads':
            self._json(list_offline_downloads())
            return

        if path == '/api/offline_stream':
            self._stream_offline_file(qp)
            return

        # Frontend App
        if path in ('/', '/index.html'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
            self.send_header('Pragma', 'no-cache')
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode('utf-8'))
            return

        self.send_response(404); self.end_headers()

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        raw_body = self.rfile.read(length)

        # Compact Short Stream Registry API (for mobile deep links & QR)
        if self.path == '/api/register_stream':
            try:
                body = json.loads(raw_body.decode('utf-8'))
                su = body.get('stream_url', '')
                if not su:
                    self._json({"success": False, "error": "stream_url missing"}, status=400); return
                sub_raw = body.get('sub_size', None)
                sid = register_short_stream(
                    su,
                    body.get('seek_url', ''),
                    int(body.get('base_offset', 0) or 0),
                    int(sub_raw) if sub_raw is not None else None,
                    int(body.get('compress_method', 0) or 0),
                    body.get('title', '4K Stream'),
                    body.get('size', '4K UHD'),
                )
                self._json({"success": True, "sid": sid})
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        # Full Season M3U8 Playlist Generator (for ZIP packs)
        if self.path == '/api/season_playlist.m3u8':
            try:
                form = urllib.parse.parse_qs(raw_body.decode('utf-8'))
                payload = json.loads(form.get('payload', ['{}'])[0])
                entries = payload.get('entries', [])
                origin = self._request_origin()
                lines = ["#EXTM3U"]
                for ep in entries:
                    su = ep.get('stream_url', '')
                    sk = ep.get('seek_url', '')
                    off = int(ep.get('base_offset', 0) or 0)
                    sz = int(ep.get('sub_size', 0) or 0)
                    cm = int(ep.get('compress_method', 0) or 0)
                    ep_name = ep.get('name', 'Episode')
                    ep_sid = ep.get('sid') or register_short_stream(su, sk, off, sz if sz > 0 else None, cm, ep_name, ep.get('size', '4K UHD'))
                    url_p = f"{origin}/s/{ep_sid}.mkv"
                    lines.append(f"#EXTINF:-1,{ep_name}")
                    lines.append("#EXTVLCOPT:network-caching=2000")
                    lines.append("#EXTVLCOPT:http-reconnect=true")
                    lines.append(url_p)
                content = ("\n".join(lines) + "\n").encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-mpegurl')
                self.send_header('Content-Disposition', 'attachment; filename="season_pack.m3u8"')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        # PC VLC HTTP Remote Control Command API
        if self.path == '/api/vlc_remote':
            try:
                body = json.loads(raw_body.decode('utf-8'))
                cmd = (body.get('command') or '').strip()
                val = body.get('val', '')
                self._json(vlc_remote_call(cmd=cmd or None, val=val))
            except Exception as e:
                self._json({"connected": False, "error": str(e)}, status=500)
            return

        # Resolve API
        if self.path == '/api/resolve':
            ok_vip, vip_err = _require_vip_or_error()
            if not ok_vip:
                self._json(vip_err, status=403); return
            try:
                body = json.loads(raw_body.decode('utf-8'))
                target_url = body.get('url', '')
                res = resolve_hubcloud(target_url)
                if res.get('success') and not is_4k_unlocked() and not res.get('is_zip') and not res.get('is_series'):
                    if _is_4k_marker(f"{res.get('title', '')} {body.get('quality_label', '')}"):
                        msg4k = "4K Ultra playback requires a 6 Months (Rs 199) or 1 Year (Rs 349) VIP Plan!"
                        self._json({"success": False, "vip_required": True, "upgrade_4k_required": True, "error": msg4k, "message": msg4k}, status=403)
                        return
                self._json(res)
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        if self.path == '/api/vip/activate':
            try:
                body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                res = activate_vip_key(body.get('key', ''))
                self._json(res, status=200 if res.get('success') else 400)
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        if self.path == '/api/vip/deactivate':
            try:
                self._json(deactivate_vip_session())
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        # Save / Delete Auto-Resume Playback Progress
        if self.path == '/api/progress':
            try:
                body = json.loads(raw_body.decode('utf-8'))
                if body.get('clear_all'):
                    delete_playback_progress(None)
                    self._json({"success": True})
                elif body.get('delete') and body.get('key'):
                    delete_playback_progress(str(body.get('key')))
                    self._json({"success": True})
                else:
                    rec = save_playback_progress(body)
                    note_offline_watch_progress(body, rec)
                    self._json({"success": True, "item": rec})
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        # Download & Attach Online .srt Subtitle (for Browser WebVTT + Live In-Built Player)
        if self.path in ('/api/subtitles/load', '/api/subtitles/load_local'):
            try:
                body = json.loads(raw_body.decode('utf-8'))
                res = save_subtitle_asset(
                    sub_url=body.get('url', '') if self.path == '/api/subtitles/load' else '',
                    srt_text=body.get('srt_content', ''),
                    label=str(body.get('label', 'Subtitle') or 'Subtitle'),
                    lang=str(body.get('lang', 'Subtitle') or 'Subtitle'),
                )
                if res.get('success') and body.get('apply_inbuilt', True):
                    rem = vlc_remote_call(cmd='load_sub', val=res['srt_path'])
                    res['inbuilt_applied'] = bool(rem.get('connected'))
                self._json(res)
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        # Batch IMDb Ratings Lookup
        if self.path == '/api/ratings':
            try:
                body = json.loads(raw_body.decode('utf-8'))
                items = body.get('items', []) if isinstance(body, dict) else []
                self._json({"success": True, "ratings": fetch_imdb_ratings_batch(items)})
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        # Auto-Next Episode Trigger from In-Built Player
        if self.path == '/api/play_next_episode':
            ok_vip, vip_err = _require_vip_or_error()
            if not ok_vip:
                self._json(vip_err, status=403); return
            try:
                body = json.loads(raw_body.decode('utf-8'))
                srv_port = getattr(self.server, 'server_address', (None, PORT))[1] or PORT
                if not body.get('stream_url') and body.get('hdhub_link'):
                    resolved = resolve_hubcloud(body['hdhub_link'])
                    if not resolved.get('success') or not resolved.get('stream_url'):
                        self._json({"success": False, "error": resolved.get('error', 'Next episode resolve failed')}, status=502)
                        return
                    body = {
                        **body,
                        "stream_url": resolved['stream_url'],
                        "seek_url": resolved.get('seek_url', ''),
                        "base_offset": resolved.get('base_offset', 0),
                        "sub_size": resolved.get('sub_size', None),
                        "compress_method": resolved.get('compress_method', 0),
                        "sid": resolved.get('sid', ''),
                        "title": body.get('title') or resolved.get('title') or 'Next Episode',
                    }
                res = launch_inbuilt_player(body, srv_port=srv_port)
                self._json(res)
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        # User Settings & Private Offline Download Controls
        if self.path == '/api/settings':
            try:
                body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                self._json(save_user_settings(body))
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        if self.path == '/api/downloads/start':
            try:
                body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                res = start_offline_download(body)
                self._json(res, status=403 if (isinstance(res, dict) and res.get("vip_required")) else 200)
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        if self.path == '/api/downloads/action':
            try:
                body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                res = handle_offline_download_action(body)
                self._json(res, status=403 if (isinstance(res, dict) and res.get("vip_required")) else 200)
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        # App Auto-Update Config, Self-Updater Trigger & Easy Setup Helpers
        if self.path == '/api/update_config':
            try:
                body = json.loads(raw_body.decode('utf-8'))
                raw_url = str(body.get('update_url', '') or '').strip()
                _save_app_config({"update_url": raw_url})
                self._json(check_app_update())
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        if self.path == '/api/apply_update':
            try:
                body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                self._json(start_app_update(
                    download_url=body.get('download_url', ''),
                    expected_sha256=body.get('sha256', ''),
                    target_version=body.get('version', ''),
                ))
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        if self.path == '/api/apply_ota_py':
            try:
                body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                self._json(apply_ota_py_update(
                    py_url=body.get('py_url', ''),
                    expected_sha256=body.get('sha256', ''),
                    target_version=body.get('version', ''),
                ))
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        if self.path == '/api/setup_action':
            try:
                body = json.loads(raw_body.decode('utf-8'))
                self._json(run_setup_action(str(body.get('action', '') or '')))
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        # Desktop App Window Controls & Single-Instance Focus
        if self.path == '/api/focus_app':
            _focus_desktop_window()
            self._json({
                "success": True,
                "window_mode": "desktop" if _DESKTOP_MODE else "server",
                "window_hwnd": int(_WINDOW_HWND or 0),
            })
            return

        if self.path == '/api/window_action':
            try:
                body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                act = str(body.get('action', '') or '').strip().lower()
                self._json(_apply_window_action(act))
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        # Shared stream launch prep (Android Native Player / External VLC / In-Built Player)
        if self.path == '/api/prepare_play':
            try:
                body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                srv_port = getattr(self.server, 'server_address', (None, PORT))[1] or PORT
                cfg, err = _prepare_play_target(body, srv_port=srv_port)
                if err:
                    self._json(err, status=403 if err.get('vip_required') else 400)
                else:
                    self._json({"success": True, **cfg})
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        if self.path == '/api/prewarm':
            try:
                body = json.loads(raw_body.decode('utf-8')) if raw_body else {}
                lnk = str(body.get('url', '') or '').strip()
                if lnk:
                    _prewarm_episode_async(lnk)
                self._json({"success": True})
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        if self.path in ('/api/play_vlc', '/api/play_inbuilt'):
            try:
                body = json.loads(raw_body.decode('utf-8'))
                srv_port = getattr(self.server, 'server_address', (None, PORT))[1] or PORT
                if self.path == '/api/play_inbuilt':
                    res = launch_inbuilt_player(body, srv_port=srv_port)
                else:
                    res = launch_external_vlc(body, srv_port=srv_port)
                self._json(res)
            except Exception as e:
                self._json({"success": False, "error": str(e)}, status=500)
            return

        self.send_response(404); self.end_headers()

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

# ── Shared Playback Preparation + Embedded StreamHub Cinema Player ───────────

_INBUILT_PROC_LOCK = threading.Lock()
_INBUILT_PLAYER_PROC = None
_EXTERNAL_VLC_USED = False

def _find_libvlc_dir():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(_APP_DATA_DIR, "vlc"),
        os.path.join(base_dir, "vlc"),
    ]
    if IS_FROZEN:
        exe_dir = os.path.dirname(sys.executable)
        meipass = getattr(sys, '_MEIPASS', '')
        candidates.insert(1, os.path.join(exe_dir, "vlc"))
        if meipass:
            candidates.insert(1, os.path.join(meipass, "vlc"))
    candidates.extend([
        os.path.dirname(VLC_PATH) if VLC_PATH else "",
        r"C:\Program Files\VideoLAN\VLC",
        r"C:\Program Files (x86)\VideoLAN\VLC",
    ])
    for cand in candidates:
        if cand and os.path.exists(os.path.join(cand, "libvlc.dll")):
            return cand
    return None

def _ensure_embedded_vlc_extracted(show_splash=True):
    """Unpacks bundled portable vlc_runtime.zip ONCE into %LocalAppData%\\StreamHub\\vlc for instant launches."""
    target_vlc = os.path.join(_APP_DATA_DIR, "vlc")
    if os.path.exists(os.path.join(target_vlc, "libvlc.dll")):
        return target_vlc
    meipass = getattr(sys, '_MEIPASS', '')
    zip_cand = None
    for base in (meipass, os.path.dirname(sys.executable) if IS_FROZEN else os.path.dirname(os.path.abspath(__file__))):
        if base:
            zc = os.path.join(base, "vlc_runtime.zip")
            if os.path.exists(zc):
                zip_cand = zc
                break
    if not zip_cand:
        return _find_libvlc_dir()

    import zipfile
    splash_root = None
    if show_splash:
        try:
            import tkinter as tk
            splash_root = tk.Tk()
            splash_root.overrideredirect(True)
            splash_root.configure(bg="#0b0e17")
            sw, sh = splash_root.winfo_screenwidth(), splash_root.winfo_screenheight()
            splash_root.geometry(f"380x110+{(sw-380)//2}+{(sh-110)//2}")
            tk.Label(splash_root, text="StreamHub", bg="#0b0e17", fg="#e50914", font=("Segoe UI", 14, "bold")).pack(pady=(18, 4))
            tk.Label(splash_root, text="First-time 4K Cinema Engine setup (1-2s)...", bg="#0b0e17", fg="#cbd5e1", font=("Segoe UI", 9)).pack()
            splash_root.update()
        except Exception:
            splash_root = None
    try:
        stage_dir = os.path.join(_APP_DATA_DIR, "vlc_staging")
        if os.path.exists(stage_dir):
            shutil.rmtree(stage_dir, ignore_errors=True)
        os.makedirs(stage_dir, exist_ok=True)
        with zipfile.ZipFile(zip_cand, 'r') as zf:
            zf.extractall(stage_dir)
        if os.path.exists(target_vlc):
            shutil.rmtree(target_vlc, ignore_errors=True)
        os.replace(stage_dir, target_vlc)
    except Exception:
        pass
    finally:
        if splash_root:
            try:
                splash_root.destroy()
            except Exception:
                pass
    return _find_libvlc_dir()

_EPISODE_CACHE_LOCK = threading.Lock()
_EPISODE_RESOLVE_CACHE = OrderedDict()  # {link: (ts, resolved_dict)}
_EPISODE_PREWARM_EVENTS = {}            # {link: threading.Event}
_HBLINKS_OPTS_CACHE = OrderedDict()     # {hblinks_url: (ts, hb_opts)}
_MOVIE_DETAILS_CACHE_LOCK = threading.Lock()
_MOVIE_DETAILS_CACHE = OrderedDict()    # {permalink: (ts, details_dict)}

def _get_movie_details_cached(post_url):
    url = str(post_url or '').strip()
    if not url:
        return None
    with _MOVIE_DETAILS_CACHE_LOCK:
        hit = _MOVIE_DETAILS_CACHE.get(url)
        if hit and (time.time() - hit[0]) < 1200:
            _MOVIE_DETAILS_CACHE.move_to_end(url)
            return hit[1]
    details = parse_movie_details(url)
    if details and (details.get('qualities') or details.get('seasons') or details.get('packs')):
        with _MOVIE_DETAILS_CACHE_LOCK:
            _MOVIE_DETAILS_CACHE[url] = (time.time(), details)
            while len(_MOVIE_DETAILS_CACHE) > 60:
                _MOVIE_DETAILS_CACHE.popitem(last=False)
    return details

def _parse_quality_option_meta(title_str):
    t = str(title_str or '').strip()
    tl = t.lower()
    if '2160p' in tl:
        short_lbl = '4K UHD'
    elif '1080p' in tl:
        short_lbl = '1080p'
    elif '720p' in tl:
        short_lbl = '720p'
    elif '480p' in tl:
        short_lbl = '480p'
    elif '360p' in tl:
        short_lbl = '360p'
    elif re.search(r'\b(4k|uhd|ds4k)\b', tl):
        short_lbl = '4K UHD'
    else:
        short_lbl = 'HD'

    langs = []
    if re.search(r'dual[\s\-]*audio|hindi\s*\+\s*eng|hindi\s*\+\s*english', tl):
        langs.append('Dual Audio')
    else:
        for nm, pat in (
            ('Hindi', r'\b(hindi|hin)\b'),
            ('English', r'\b(english|eng)\b'),
            ('Tamil', r'\b(tamil|tam)\b'),
            ('Telugu', r'\b(telugu|tel)\b'),
            ('Malayalam', r'\b(malayalam|mal)\b'),
            ('Kannada', r'\b(kannada|kan)\b'),
            ('Punjabi', r'\b(punjabi)\b'),
            ('Korean', r'\b(korean|kor)\b'),
        ):
            if re.search(pat, tl):
                langs.append(nm)
    if 'multi' in tl and 'Dual Audio' not in langs:
        langs.append('Multi Audio')
    lang_hint = ' + '.join(langs[:2]) if langs else ''

    m_sz = re.search(r'(\d+(?:\.\d+)?)\s*(GB|MB|TB)', t, re.I)
    size_hint = ''
    size_gb = 0.0
    if m_sz:
        num = float(m_sz.group(1))
        unit = m_sz.group(2).upper()
        size_hint = f"{m_sz.group(1)} {unit}"
        size_gb = num * 1024.0 if unit == 'TB' else (num / 1024.0 if unit == 'MB' else num)

    parts = [short_lbl]
    if lang_hint:
        parts.append(lang_hint)
    if size_hint:
        parts.append(size_hint)
    if len(parts) == 1 and t:
        clean_t = re.sub(r'\s+', ' ', t).strip()
        label = f"{short_lbl} • {clean_t[:42]}"
    else:
        label = ' • '.join(parts)
    return {
        'label': label,
        'short_label': short_lbl,
        'language_hint': lang_hint,
        'size_hint': size_hint,
        'size_gb': round(size_gb, 2),
    }

def _score_quality_candidate(title_str, pref_quality='auto', pref_lang='Auto', link='', size_gb=0.0):
    tl = str(title_str or '').lower()
    lnk_l = str(link or '').lower()
    pq = str(pref_quality or 'auto').lower().strip()
    pl = str(pref_lang or 'Auto').lower().strip()
    score = 0

    if not size_gb:
        size_gb = _parse_quality_option_meta(title_str).get('size_gb', 0.0)

    is_hdstream = 'hdstream4u.com' in lnk_l
    tier_map = [
        ('480p', r'480p'),
        ('720p', r'720p'),
        ('1080p', r'1080p'),
        ('4k', r'2160p|4k|uhd|ds4k'),
    ]
    matched_tier = 'hls_alt' if is_hdstream else '720p'
    if not is_hdstream:
        for tk, pat in tier_map:
            if re.search(pat, tl):
                matched_tier = tk
                break

    if pq in ('auto', 'ask', ''):
        # Fast-start Adaptive mode: 480p -> 720p -> lightweight 1080p; avoid 50-90GB remuxes on start
        auto_weights = {'480p': 145, '720p': 115, '1080p': 75, '4k': 25, 'hls_alt': 12}
        score += auto_weights.get(matched_tier, 50)
    else:
        norm_pq = '4k' if any(x in pq for x in ('4k', '2160', 'uhd')) else ('1080p' if '1080' in pq else ('480p' if '480' in pq else '720p'))
        if matched_tier == norm_pq:
            score += 130
        elif (norm_pq == '720p' and matched_tier == '1080p') or (norm_pq == '1080p' and matched_tier == '720p'):
            score += 65
        elif matched_tier == '480p':
            score += 40
        elif matched_tier in ('720p', '1080p'):
            score += 35
        elif matched_tier == 'hls_alt':
            score += 5

    # Prefer lightweight, fast-loading files over massive 40-90GB raw BluRay Remuxes
    if size_gb >= 42.0:
        score -= 115 if pq != '4k' else 35
    elif size_gb >= 22.0:
        score -= 60 if pq != '4k' else 15
    elif size_gb >= 10.0 and pq in ('auto', 'ask', '480p', '720p', ''):
        score -= 28
    elif 0.1 < size_gb <= 3.5:
        score += 18

    if is_hdstream:
        score -= 40
    if 'hubcloud' in lnk_l or 'hubcdn' in lnk_l:
        score += 12
    elif 'hubdrive' in lnk_l:
        score += 6

    if pl and pl != 'auto':
        if pl in tl:
            score += 95
        elif pl in ('hindi', 'dual audio') and ('dual' in tl or 'hindi' in tl or 'multi' in tl):
            score += 75
        elif pl == 'english' and ('dual' in tl or 'english' in tl or 'eng' in tl):
            score += 65

    if re.search(r'\b(hdts|cam|camrip|predvd|telesync)\b', tl):
        score -= 100
    if 'zip' in tl or 'pack' in tl:
        score -= 20
    return score

def _build_quality_options_from_list(qualities_raw, active_idx=-1, active_link=''):
    out = []
    seen = set()
    act_l = str(active_link or '').strip()
    for q in (qualities_raw or []):
        if isinstance(q, dict):
            lnk = str(q.get('hdhub_link') or q.get('link') or q.get('stream_url') or '').strip()
            raw_t = str(q.get('raw_title') or q.get('title') or q.get('label') or '').strip()
        else:
            lnk = ''
            raw_t = ''
        if not lnk or lnk in seen:
            continue
        seen.add(lnk)
        meta = _parse_quality_option_meta(raw_t)
        idx = len(out)
        is_act = (idx == active_idx) or (bool(act_l) and lnk == act_l)
        out.append({
            'id': idx,
            'label': meta['label'],
            'short_label': meta['short_label'],
            'language_hint': meta['language_hint'],
            'size_hint': meta['size_hint'],
            'size_gb': meta['size_gb'],
            'raw_title': raw_t,
            'hdhub_link': lnk,
            'stream_url': str((q or {}).get('stream_url', '') or ''),
            'sid': str((q or {}).get('sid', '') or ''),
            'compress_method': int((q or {}).get('compress_method', 0) or 0) if isinstance(q, dict) else 0,
            'is_active': is_act,
        })
    if out and not any(o['is_active'] for o in out):
        out[0]['is_active'] = True
    return out

def _find_completed_offline_match(keys):
    key_set = {str(k or '').strip() for k in (keys or []) if str(k or '').strip()}
    if not key_set:
        return None
    with _OFFLINE_LOCK:
        items = list(_load_offline_db_unlocked())
    for item in items:
        if item.get('status') != 'completed':
            continue
        mk = str(item.get('media_key', '') or '').strip()
        hl = str(item.get('hdhub_link', '') or '').strip()
        pk = str(item.get('parent_key', '') or '').strip()
        if (mk and mk in key_set) or (hl and hl in key_set) or (pk and pk in key_set):
            fpath = _offline_item_path(item)
            if fpath and os.path.isfile(fpath):
                return item
    return None

def _rank_quality_option_indices(quality_options, pref_quality='auto', pref_lang='Auto', selected_q_id=None):
    """Rank normalized quality_options indices best-first for playback resolution."""
    if not quality_options:
        return []
    ranked = sorted(
        range(len(quality_options)),
        key=lambda i: _score_quality_candidate(
            quality_options[i].get('raw_title') or quality_options[i].get('label'),
            pref_quality,
            pref_lang,
            link=quality_options[i].get('hdhub_link', ''),
            size_gb=float(quality_options[i].get('size_gb', 0) or 0)
        ),
        reverse=True
    )
    if selected_q_id is not None and 0 <= selected_q_id < len(quality_options):
        ranked = [selected_q_id] + [i for i in ranked if i != selected_q_id]
    return ranked

def _get_hblinks_options_cached(hdhub_link):
    """Fetch and cache parsed quality mirror options from an HBLInks page."""
    lnk = str(hdhub_link or '').strip()
    if not lnk or 'hblinks.' not in lnk.lower():
        return []
    now = time.time()
    with _EPISODE_CACHE_LOCK:
        hit = _HBLINKS_OPTS_CACHE.get(lnk)
        if hit and (now - hit[0]) < 1800:
            _HBLINKS_OPTS_CACHE.move_to_end(lnk)
            return list(hit[1])
    try:
        hb_html, _ = fetch_html(lnk, timeout=8)
        hb_opts = _extract_hblinks_options(hb_html)
    except Exception:
        hb_opts = []
    if hb_opts:
        with _EPISODE_CACHE_LOCK:
            _HBLINKS_OPTS_CACHE[lnk] = (time.time(), hb_opts)
            while len(_HBLINKS_OPTS_CACHE) > 80:
                _HBLINKS_OPTS_CACHE.popitem(last=False)
    return list(hb_opts)

def _resolve_episode_cached(hdhub_link):
    lnk = str(hdhub_link or '').strip()
    if not lnk:
        return None
    wait_ev = None
    with _EPISODE_CACHE_LOCK:
        hit = _EPISODE_RESOLVE_CACHE.get(lnk)
        if hit and (time.time() - hit[0]) < 1800:
            _EPISODE_RESOLVE_CACHE.move_to_end(lnk)
            return hit[1]
        wait_ev = _EPISODE_PREWARM_EVENTS.get(lnk)
    if wait_ev is not None:
        wait_ev.wait(timeout=25)
        with _EPISODE_CACHE_LOCK:
            hit = _EPISODE_RESOLVE_CACHE.get(lnk)
            if hit and (time.time() - hit[0]) < 1800:
                _EPISODE_RESOLVE_CACHE.move_to_end(lnk)
                return hit[1]
    res = resolve_hubcloud(lnk)
    if res and res.get('success') and res.get('stream_url'):
        with _EPISODE_CACHE_LOCK:
            _EPISODE_RESOLVE_CACHE[lnk] = (time.time(), res)
            while len(_EPISODE_RESOLVE_CACHE) > 80:
                _EPISODE_RESOLVE_CACHE.popitem(last=False)
    return res

def _prewarm_episode_async(hdhub_link):
    lnk = str(hdhub_link or '').strip()
    if not lnk:
        return
    now = time.time()
    with _EPISODE_CACHE_LOCK:
        hit = _EPISODE_RESOLVE_CACHE.get(lnk)
        if hit and (now - hit[0]) < 1800:
            return
        if lnk in _EPISODE_PREWARM_EVENTS:
            return
        ev = threading.Event()
        _EPISODE_PREWARM_EVENTS[lnk] = ev

    def _bg():
        target_lnk = lnk
        try:
            if 'hblinks.' in lnk.lower():
                hb_opts = _get_hblinks_options_cached(lnk)
                if hb_opts:
                    user_cfg = get_user_settings()
                    pref_q = str((user_cfg.get('download_prefs') or {}).get('stream_quality') or 'auto').strip()
                    pref_l = str((user_cfg.get('profile') or {}).get('preferred_language') or 'Auto').strip()
                    q_built = _build_quality_options_from_list(hb_opts)
                    ranked = _rank_quality_option_indices(q_built, pref_q, pref_l)
                    if ranked:
                        target_lnk = q_built[ranked[0]]['hdhub_link']
                        with _EPISODE_CACHE_LOCK:
                            if target_lnk not in _EPISODE_PREWARM_EVENTS:
                                _EPISODE_PREWARM_EVENTS[target_lnk] = ev
            res = resolve_hubcloud(target_lnk)
            if res and res.get('success') and res.get('stream_url'):
                with _EPISODE_CACHE_LOCK:
                    now_ok = time.time()
                    for cache_k in {lnk, target_lnk}:
                        if cache_k:
                            _EPISODE_RESOLVE_CACHE[cache_k] = (now_ok, res)
                    while len(_EPISODE_RESOLVE_CACHE) > 80:
                        _EPISODE_RESOLVE_CACHE.popitem(last=False)
                cm_r = int(res.get('compress_method', 0) or 0)
                if cm_r == 0:
                    get_stream_session(res['stream_url'], res.get('seek_url', ''), int(res.get('base_offset', 0) or 0), res.get('sub_size', None), cm_r).warm_async()
                elif cm_r == -1 and res.get('sid'):
                    get_hls_session(res['stream_url'], sid=res['sid']).warm_async()
        except Exception:
            pass
        finally:
            with _EPISODE_CACHE_LOCK:
                if _EPISODE_PREWARM_EVENTS.get(lnk) is ev:
                    _EPISODE_PREWARM_EVENTS.pop(lnk, None)
                if target_lnk != lnk and _EPISODE_PREWARM_EVENTS.get(target_lnk) is ev:
                    _EPISODE_PREWARM_EVENTS.pop(target_lnk, None)
            ev.set()

    threading.Thread(target=_bg, daemon=True).start()

def _prepare_play_target(body, srv_port=None):
    ok_vip, vip_err = _require_vip_or_error()
    if not ok_vip:
        return None, vip_err
    offline_id = str(body.get('offline_id', '') or '').strip()
    is_offline_local = False
    offline_entry = None
    stream_url = str(body.get('stream_url', '') or '').strip()
    seek_url = str(body.get('seek_url', '') or '').strip()
    base_off = int(body.get('base_offset', 0) or 0)
    sub_raw = body.get('sub_size', None)
    sub_sz = int(sub_raw) if sub_raw is not None else None
    cm = int(body.get('compress_method', 0) or 0)
    sid_val = str(body.get('sid', '') or '').strip()
    backups = body.get('backup_urls') or []

    user_cfg = get_user_settings()
    pref_quality = str(body.get('preferred_quality') or (user_cfg.get('download_prefs') or {}).get('stream_quality') or 'auto').strip()
    is_auto_mode = pref_quality.lower() in ('auto', 'ask', '')
    pref_lang = str(body.get('preferred_language') or (user_cfg.get('profile') or {}).get('preferred_language') or 'Auto').strip()
    movie_permalink = str(body.get('movie_permalink', '') or '').strip()
    hdhub_link = str(body.get('hdhub_link', '') or '').strip()
    force_online = bool(body.get('force_online', False))
    sel_q_raw = body.get('selected_quality_id', None)
    selected_q_id = int(sel_q_raw) if sel_q_raw is not None and str(sel_q_raw).strip() != '' else None
    incoming_q_opts = body.get('quality_options') if isinstance(body.get('quality_options'), list) else []

    title_val = str(body.get('title', 'StreamHub Cinema') or 'StreamHub Cinema')
    ep_label_val = str(body.get('ep_label', '') or '')
    media_key_in = str(body.get('media_key', '') or '').strip()
    parent_key_in = str(body.get('parent_key', '') or '').strip()
    next_ep_obj = body.get('next_episode') if isinstance(body.get('next_episode'), dict) else None
    start_sec = max(0, int(body.get('start_time', 0) or 0))
    quality_options = []
    active_quality_label = ''

    if offline_id:
        offline_entry = _get_offline_entry(offline_id)
        if not offline_entry or offline_entry.get('status') != 'completed':
            return None, {"success": False, "message": "Offline movie download is not complete yet"}
        fpath = _offline_item_path(offline_entry)
        if not fpath or not os.path.isfile(fpath):
            return None, {"success": False, "message": "Offline video file not found in device storage"}
        is_offline_local = True

    # Instant offline check when opening movie directly without forcing online quality switch
    if not is_offline_local and not stream_url and not force_online and selected_q_id is None:
        matched_off = _find_completed_offline_match([media_key_in, movie_permalink, hdhub_link])
        if matched_off:
            offline_id = matched_off.get('id', '')
            offline_entry = matched_off
            is_offline_local = True

    if not is_offline_local and (not stream_url or not backups) and sid_val:
        sinfo = get_short_stream(sid_val)
        if sinfo:
            if not stream_url:
                stream_url = sinfo.get('stream_url', '')
                seek_url = sinfo.get('seek_url', '')
                base_off = int(sinfo.get('base_offset', 0) or 0)
                sub_sz = sinfo.get('sub_size', None)
                cm = int(sinfo.get('compress_method', 0) or 0)
            if not backups:
                backups = sinfo.get('backup_urls') or []

    # Direct Netflix-style movie_permalink background resolution & best-quality selection
    details_obj = None
    raw_q_source = list(incoming_q_opts)
    if not is_offline_local and not stream_url and movie_permalink and (not raw_q_source or not hdhub_link):
        details_obj = _get_movie_details_cached(movie_permalink)
        if details_obj:
            if details_obj.get('title') and (not title_val or title_val == 'StreamHub Cinema'):
                title_val = details_obj['title']
            if details_obj.get('qualities') and not raw_q_source:
                raw_q_source = list(details_obj['qualities'])
            elif details_obj.get('is_series') and not hdhub_link:
                seasons = details_obj.get('seasons') or []
                if seasons:
                    chosen_s_idx, chosen_e_idx = 0, 0
                    prog_map = _read_progress_disk()
                    best_ts = -1
                    for si, sea in enumerate(seasons):
                        for ei, ep_item in enumerate(sea.get('episodes') or []):
                            pk = f"{movie_permalink}::ep::{ep_item.get('ep_label', '')}"
                            pr = prog_map.get(pk) or prog_map.get(ep_item.get('link', ''))
                            if pr and float(pr.get('updated_at', 0) or 0) > best_ts:
                                best_ts = float(pr.get('updated_at', 0) or 0)
                                chosen_s_idx, chosen_e_idx = si, ei
                    ep_list = seasons[chosen_s_idx].get('episodes') or []
                    if ep_list:
                        ep_cur = ep_list[chosen_e_idx]
                        hdhub_link = str(ep_cur.get('link', '') or '').strip()
                        ep_label_val = str(ep_cur.get('ep_label', '') or f"Ep {chosen_e_idx + 1}")
                        parent_key_in = parent_key_in or movie_permalink
                        media_key_in = f"{movie_permalink}::ep::{ep_label_val}"
                        if chosen_e_idx + 1 < len(ep_list) and not next_ep_obj:
                            ep_nxt = ep_list[chosen_e_idx + 1]
                            nx_lbl = str(ep_nxt.get('ep_label', '') or f"Ep {chosen_e_idx + 2}")
                            next_ep_obj = {
                                'title': title_val,
                                'hdhub_link': ep_nxt.get('link', ''),
                                'ep_label': nx_lbl,
                                'media_key': f"{movie_permalink}::ep::{nx_lbl}",
                                'parent_key': movie_permalink,
                                'movie_permalink': movie_permalink,
                            }

    if not is_offline_local and not stream_url and not raw_q_source and hdhub_link and 'hblinks.' in hdhub_link.lower():
        hb_opts = _get_hblinks_options_cached(hdhub_link)
        if hb_opts:
            raw_q_source = hb_opts
            if selected_q_id is None:
                hdhub_link = ''

    allow_4k_cur = is_4k_unlocked()
    if raw_q_source:
        quality_options = _build_quality_options_from_list(raw_q_source, active_link=hdhub_link)
        for q_opt in quality_options:
            q_opt['locked_4k'] = (not allow_4k_cur) and _is_4k_marker(
                f"{q_opt.get('short_label', '')} {q_opt.get('label', '')} {q_opt.get('raw_title', '')}"
            )

    # Resolve best candidate if no direct stream_url yet
    if not is_offline_local and not stream_url:
        if not allow_4k_cur and _is_4khdhub_url(movie_permalink):
            msg4k = "4K Cinema Vault requires a 6 Months (Rs 199) or 1 Year (Rs 349) VIP Plan!"
            return None, {"success": False, "vip_required": True, "upgrade_4k_required": True, "error": msg4k, "message": msg4k}

        if not allow_4k_cur and quality_options and selected_q_id is not None and 0 <= selected_q_id < len(quality_options):
            if quality_options[selected_q_id].get('locked_4k'):
                msg4k = "4K Ultra playback requires a 6 Months (Rs 199) or 1 Year (Rs 349) VIP Plan!"
                return None, {"success": False, "vip_required": True, "upgrade_4k_required": True, "error": msg4k, "message": msg4k}

        if not allow_4k_cur and not quality_options and _is_4k_marker(title_val) and not re.search(r'(?:1080p|720p|480p|360p)', title_val, re.I):
            msg4k = "This is a 4K Ultra title — unlock with a 6 Months (Rs 199) or 1 Year (Rs 349) VIP Plan!"
            return None, {"success": False, "vip_required": True, "upgrade_4k_required": True, "error": msg4k, "message": msg4k}

        if hdhub_link and (not quality_options or selected_q_id is not None or not movie_permalink):
            cand_indices = []
            if quality_options:
                if selected_q_id is not None and 0 <= selected_q_id < len(quality_options):
                    cand_indices.append(selected_q_id)
                for idx_q, q_opt in enumerate(quality_options):
                    if q_opt.get('hdhub_link') == hdhub_link and idx_q not in cand_indices:
                        cand_indices.insert(0, idx_q)
            cand_indices = [i for i in cand_indices if not quality_options[i].get('locked_4k')]
            if not cand_indices and quality_options and any(q.get('locked_4k') for q in quality_options):
                playable_non4k = [q['id'] for q in quality_options if not q.get('locked_4k')]
                if not playable_non4k:
                    msg4k = "4K Ultra playback requires a 6 Months (Rs 199) or 1 Year (Rs 349) VIP Plan!"
                    return None, {"success": False, "vip_required": True, "upgrade_4k_required": True, "error": msg4k, "message": msg4k}
                cand_indices = playable_non4k[:4]
            if not cand_indices:
                cand_links = [(0, hdhub_link, '')]
            else:
                cand_links = [(idx_q, quality_options[idx_q]['hdhub_link'], quality_options[idx_q]['short_label']) for idx_q in cand_indices]
        elif quality_options:
            eff_pq = '1080p' if (not allow_4k_cur and pref_quality.lower() in ('4k', '2160p', 'uhd')) else pref_quality
            ranked = _rank_quality_option_indices(quality_options, eff_pq, pref_lang, selected_q_id)
            if not allow_4k_cur:
                ranked = [i for i in ranked if not quality_options[i].get('locked_4k')]
                if not ranked:
                    msg4k = "Only 4K stream available — unlock with a 6 Months (Rs 199) or 1 Year (Rs 349) VIP Plan!"
                    return None, {"success": False, "vip_required": True, "upgrade_4k_required": True, "error": msg4k, "message": msg4k}
            cand_links = [(i, quality_options[i]['hdhub_link'], quality_options[i]['short_label']) for i in ranked[:6]]
        elif hdhub_link:
            cand_links = [(0, hdhub_link, '')]
        else:
            err_detail = (details_obj or {}).get('error') or 'No active stream link found for this movie'
            return None, {"success": False, "message": err_detail}

        resolved = None
        last_err = ''
        winning_idx = -1
        fallback_first = None
        for idx_q, c_link, _ in cand_links:
            r_try = _resolve_episode_cached(c_link)
            if not (r_try and r_try.get('success') and r_try.get('stream_url')):
                if r_try and r_try.get('error'):
                    last_err = str(r_try.get('error'))
                continue
            cand_cm = int(r_try.get('compress_method', 0) or 0)
            cand_url = r_try.get('stream_url', '')
            cand_seek = r_try.get('seek_url', '') or ''
            cand_sid = str(r_try.get('sid', '') or '')
            cand_backups = []
            if cand_sid:
                s_tmp = get_short_stream(cand_sid)
                if s_tmp:
                    cand_backups = s_tmp.get('backup_urls') or []
            if cand_cm == 0 and '.m3u8' not in cand_url and len(cand_links) > 1:
                sess_chk = get_stream_session(
                    cand_url, cand_seek,
                    int(r_try.get('base_offset', 0) or 0),
                    r_try.get('sub_size', None),
                    cand_cm,
                    backup_urls=cand_backups
                )
                sess_chk.ensure_init()
                if not sess_chk.head_buf and not sess_chk.total_size:
                    if fallback_first is None:
                        fallback_first = (r_try, idx_q, c_link)
                    last_err = 'Server returned 403 / timeout — trying alternate mirror'
                    continue
            resolved = r_try
            winning_idx = idx_q
            hdhub_link = c_link
            break
        if not resolved and fallback_first is not None:
            resolved, winning_idx, hdhub_link = fallback_first
        if not resolved:
            return None, {"success": False, "message": last_err or 'Stream link could not be resolved — please try another title'}

        stream_url = resolved.get('stream_url', '')
        seek_url = resolved.get('seek_url', '') or ''
        base_off = int(resolved.get('base_offset', 0) or 0)
        sub_sz = resolved.get('sub_size', None)
        cm = int(resolved.get('compress_method', 0) or 0)
        sid_val = str(resolved.get('sid', '') or '')
        if not title_val or title_val == 'StreamHub Cinema':
            title_val = str(resolved.get('title') or 'StreamHub Cinema')
        if sid_val and not backups:
            sinfo2 = get_short_stream(sid_val)
            if sinfo2:
                backups = sinfo2.get('backup_urls') or []
        if quality_options and 0 <= winning_idx < len(quality_options):
            for qi, qo in enumerate(quality_options):
                qo['is_active'] = (qi == winning_idx)
            selected_q_id = winning_idx
            active_quality_label = quality_options[winning_idx].get('short_label', '')

    if not is_offline_local and not stream_url:
        return None, {"success": False, "message": "Stream URL missing"}

    use_port = int(srv_port or PORT)
    duration_ms_hint = 0
    if is_offline_local:
        is_hls = False
        final_target = f"http://127.0.0.1:{use_port}/api/offline_stream?id={urllib.parse.quote(offline_id, safe='')}"
        title_val = str(body.get('title') or (offline_entry or {}).get('title') or 'Offline Movie')
        hls_base_ms = 0
        hls_intra_sec = 0.0
        hls_total_ms = 0
        hls_sid = ""
        active_quality_label = str((offline_entry or {}).get('quality_label') or 'Offline HD')
    else:
        is_hls = (cm == -1 or '.m3u8' in stream_url or 'hdstream4u' in stream_url)
        hls_base_ms = 0
        hls_intra_sec = 0.0
        hls_total_ms = 0
        hls_sid = sid_val

        if is_hls:
            if not hls_sid:
                hls_sid = register_short_stream(stream_url, '', 0, None, -1, title_val, 'Adaptive HLS')
            try:
                sl = get_hls_session(stream_url, sid=hls_sid).slice_for_ms(start_sec * 1000, sid_override=hls_sid, origin=f"http://127.0.0.1:{use_port}")
                if sl.get("success"):
                    final_target = sl["playlist_url"]
                    hls_base_ms = int(sl.get("seg_start_ms", 0) or 0)
                    hls_intra_sec = float(sl.get("intra_sec", 0.0) or 0.0)
                    hls_total_ms = int(sl.get("total_ms", 0) or 0)
                else:
                    final_target = stream_url
            except Exception:
                final_target = stream_url
        else:
            if cm == 0:
                sess_obj = get_stream_session(stream_url, seek_url, base_off, sub_sz, cm, backup_urls=backups)
                sess_obj.warm_async()
                if getattr(sess_obj, 'media_info', None) and sess_obj.media_info.get('duration_sec'):
                    duration_ms_hint = int(float(sess_obj.media_info['duration_sec']) * 1000)
            proxy_url = f"http://127.0.0.1:{use_port}/api/stream?url={urllib.parse.quote(stream_url, safe='')}"
            if seek_url:
                proxy_url += f"&seek_url={urllib.parse.quote(seek_url, safe='')}"
            if sid_val:
                proxy_url += f"&sid={urllib.parse.quote(sid_val, safe='')}"
            if base_off or sub_sz is not None or cm:
                proxy_url += f"&off={base_off}&sz={sub_sz or 0}&cm={cm}"
            final_target = proxy_url

    if not active_quality_label:
        active_quality_label = 'Auto HLS' if is_hls else _parse_quality_option_meta(title_val).get('short_label', 'HD')
    if not quality_options:
        # Expose mirror fallback options if multiple direct CDNs exist (e.g. for series episodes)
        alt_opts = [{
            'id': 0,
            'label': f"{active_quality_label} • Primary CDN",
            'short_label': active_quality_label,
            'language_hint': '',
            'size_hint': '',
            'raw_title': title_val,
            'hdhub_link': hdhub_link,
            'stream_url': stream_url if not is_offline_local else '',
            'sid': sid_val,
            'compress_method': cm,
            'is_active': True,
        }]
        for bi, b_url in enumerate(backups or []):
            bu = str(b_url or '').strip()
            if not bu or bu == stream_url or len(alt_opts) >= 4:
                continue
            b_cm = -1 if '.m3u8' in bu else cm
            b_lbl = 'HLS Adaptive' if b_cm == -1 else f"Backup Server {len(alt_opts)}"
            alt_opts.append({
                'id': len(alt_opts),
                'label': f"{active_quality_label} • {b_lbl}",
                'short_label': active_quality_label,
                'language_hint': '',
                'size_hint': '',
                'raw_title': title_val,
                'hdhub_link': '',
                'stream_url': bu,
                'sid': '',
                'compress_method': b_cm,
                'is_active': False,
            })
        quality_options = alt_opts
        if selected_q_id is None:
            selected_q_id = 0

    sub_file = str(body.get('subtitle_file', '') or '')
    sub_key = str(body.get('subtitle_key', '') or '')
    if not sub_file and sub_key:
        with _SUB_LOCK:
            s_entry = _SUB_CACHE.get(sub_key)
            if s_entry:
                sub_file = s_entry.get('srt_path', '')

    can_seek = True if (is_hls or is_offline_local) else (cm != 8 and bool(seek_url or is_range_capable(stream_url) or any(is_range_capable(u) for u in (backups or []))))

    if next_ep_obj and not is_offline_local:
        if next_ep_obj.get('hdhub_link'):
            _prewarm_episode_async(next_ep_obj['hdhub_link'])
        elif next_ep_obj.get('stream_url') and int(next_ep_obj.get('compress_method', 0) or 0) == 0:
            get_stream_session(
                next_ep_obj['stream_url'],
                next_ep_obj.get('seek_url', ''),
                int(next_ep_obj.get('base_offset', 0) or 0),
                next_ep_obj.get('sub_size', None),
                0
            ).warm_async()

    pref_track = int(body.get('audio_track') or (offline_entry or {}).get('preferred_audio_track') or 1)
    media_key_val = str(media_key_in or (offline_entry or {}).get('media_key') or offline_id or movie_permalink or sid_val or title_val or '')

    return {
        "target": final_target,
        "is_hls": is_hls,
        "seekable": can_seek,
        "hls_sid": hls_sid,
        "hls_raw_url": stream_url if is_hls else "",
        "hls_base_ms": hls_base_ms,
        "hls_intra_sec": hls_intra_sec,
        "hls_total_ms": hls_total_ms,
        "duration_ms": duration_ms_hint,
        "audio_track": pref_track,
        "sub_track": str(body.get('sub_track', 'auto') or 'auto'),
        "preferred_language": pref_lang,
        "preferred_quality": pref_quality,
        "title": title_val,
        "start_time": start_sec,
        "server_origin": f"http://127.0.0.1:{use_port}",
        "media_key": media_key_val,
        "parent_key": str(parent_key_in or (offline_entry or {}).get('parent_key') or ''),
        "ep_label": str(ep_label_val or (offline_entry or {}).get('ep_label') or ''),
        "movie_permalink": movie_permalink,
        "hdhub_link": hdhub_link,
        "offline_id": offline_id,
        "subtitle_file": sub_file,
        "next_episode": next_ep_obj,
        "auto_mode": is_auto_mode,
        "allow_4k": allow_4k_cur,
        "active_quality_label": active_quality_label,
        "selected_quality_id": int(selected_q_id or 0),
        "quality_options": quality_options,
    }, None

# ── User Settings (Profile, Theme, Download Prefs) & Private Offline Downloads ──

_DEFAULT_PROFILE = {
    "name": "StreamHub User",
    "avatar": "film",
    "preferred_language": "Auto",
}
_DEFAULT_DOWNLOAD_PREFS = {
    "storage_limit_gb": 4,       # 2, 4, 8, 16, or 0 for Unlimited
    "auto_delete_watched": True, # Clean up automatically once watched (>=95%)
    "auto_expire_days": 7,       # 7, 14, 30, or 0 for Never
    "default_quality": "720p",   # 480p, 720p, 1080p, ask (Offline downloads)
    "stream_quality": "auto",    # auto (480p start -> ABR), 480p, 720p, 1080p, 4k
}
_VALID_THEMES = ("netflix", "amoled", "prime", "gold")
_OFFLINE_RECONCILED = False

def _ensure_offline_dir():
    os.makedirs(_OFFLINE_DIR, exist_ok=True)
    nomedia = os.path.join(_OFFLINE_DIR, ".nomedia")
    if not os.path.exists(nomedia):
        try:
            with open(nomedia, 'wb') as f:
                f.write(b'')
        except Exception:
            pass

def _offline_item_path(entry, part=False):
    fname = os.path.basename(str((entry or {}).get("file_name") or ""))
    if not fname:
        return ""
    if part:
        fname += ".part"
    path = os.path.realpath(os.path.join(_OFFLINE_DIR, fname))
    base = os.path.realpath(_OFFLINE_DIR)
    if not path.startswith(base):
        return ""
    return path

def _offline_stream_enter(dl_id):
    with _OFFLINE_LOCK:
        _OFFLINE_ACTIVE_STREAMS[dl_id] = _OFFLINE_ACTIVE_STREAMS.get(dl_id, 0) + 1

def _offline_stream_exit(dl_id):
    with _OFFLINE_LOCK:
        cur = _OFFLINE_ACTIVE_STREAMS.get(dl_id, 0) - 1
        if cur <= 0:
            _OFFLINE_ACTIVE_STREAMS.pop(dl_id, None)
        else:
            _OFFLINE_ACTIVE_STREAMS[dl_id] = cur

def _load_offline_db_unlocked():
    global _OFFLINE_RECONCILED
    _ensure_offline_dir()
    items = []
    try:
        if os.path.exists(_OFFLINE_META_FILE):
            with open(_OFFLINE_META_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            if isinstance(raw, list):
                items = [x for x in raw if isinstance(x, dict) and x.get("id")]
    except Exception:
        items = []

    if not _OFFLINE_RECONCILED:
        _OFFLINE_RECONCILED = True
        changed = False
        for it in items:
            if it.get("status") in ("downloading", "queued") and it["id"] not in _OFFLINE_ACTIVE_WORKERS:
                it["status"] = "paused"
                it["speed_bps"] = 0
                seg_file = os.path.join(_OFFLINE_DIR, f"{it['id']}.segs.json")
                if os.path.isfile(seg_file):
                    try:
                        with open(seg_file, 'r', encoding='utf-8') as sf:
                            sd = json.load(sf)
                        done_cnt = len(sd.get("done") or [])
                        csz = int(sd.get("chunk_size") or 0)
                        tot = int(sd.get("total_bytes") or it.get("total_bytes") or 0)
                        it["downloaded_bytes"] = min(tot, done_cnt * csz) if tot > 0 else (done_cnt * csz)
                    except Exception:
                        pass
                else:
                    ppath = _offline_item_path(it, part=True)
                    if ppath and os.path.isfile(ppath):
                        it["downloaded_bytes"] = os.path.getsize(ppath)
                changed = True
        if changed:
            _save_offline_db_unlocked(items)
    return items

def _save_offline_db_unlocked(items):
    _ensure_offline_dir()
    try:
        tmp = _OFFLINE_META_FILE + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _OFFLINE_META_FILE)
    except Exception:
        pass

def _get_offline_entry(dl_id):
    with _OFFLINE_LOCK:
        for it in _load_offline_db_unlocked():
            if it.get("id") == dl_id:
                return dict(it)
    return None

def _update_offline_entry(dl_id, patch):
    with _OFFLINE_LOCK:
        items = _load_offline_db_unlocked()
        for it in items:
            if it.get("id") == dl_id:
                it.update(patch)
                _save_offline_db_unlocked(items)
                return dict(it)
    return None

def _parse_size_to_bytes(size_str):
    s = str(size_str or "").strip()
    m = re.search(r'(\d+(?:\.\d+)?)\s*(TB|GB|MB|KB)\b', s, re.I)
    if not m:
        return 0
    val = float(m.group(1))
    unit = m.group(2).upper()
    mult = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}.get(unit, 1)
    return int(val * mult)

# ── Firebase VIP Redeem Key Engine (streamhub-vip Firestore + Anonymous Auth) ────
FIREBASE_PROJECT_ID = "streamhub-vip"
FIREBASE_API_KEY = "AIzaSyCy53BV5obtOcXykp5d5tFv9A8vvAalaaM"
FIREBASE_FIRESTORE_BASE = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents"
VIP_4K_CUTOFF_SEC = 7776000  # Strictly > 90 days (3 months) unlocks 4K Ultra
VIP_DOWNLOAD_CUTOFF_SEC = 7776000  # >= 90 days (3 months) unlocks Offline Downloads
VIP_PLANS_META = [
    {"id": "15d", "name": "15 Days", "validity": "15 Days", "sub": "HD / 1080p Streaming", "duration_seconds": 1296000, "price_inr": 29, "allow_4k": False, "allow_download": False, "badge": "Streaming Only"},
    {"id": "30d", "name": "1 Month", "validity": "30 Days", "sub": "HD / 1080p Streaming", "duration_seconds": 2592000, "price_inr": 49, "allow_4k": False, "allow_download": False, "badge": "Streaming Only"},
    {"id": "3m", "name": "3 Months", "validity": "90 Days (3 Months)", "sub": "HD / 1080p + Downloads", "duration_seconds": 7776000, "price_inr": 119, "allow_4k": False, "allow_download": True, "badge": "Offline Downloads"},
    {"id": "6m", "name": "6 Months", "validity": "180 Days (6 Months)", "sub": "Downloads + 4K HDR", "duration_seconds": 15552000, "price_inr": 199, "allow_4k": True, "allow_download": True, "badge": "4K + Downloads"},
    {"id": "1y", "name": "1 Year", "validity": "365 Days (1 Year)", "sub": "All 4K + Downloads", "duration_seconds": 31536000, "price_inr": 349, "allow_4k": True, "allow_download": True, "badge": "Best Value • 4K"},
]

def _is_4k_marker(txt):
    return bool(re.search(r'(?:2160p|ds4k|uhd|(?<![a-z0-9])4k(?:uhd|hdr|sdr|remux|bluray)?(?![a-z0-9]))', str(txt or ''), re.I))

_VIP_LOCK = threading.Lock()
_VIP_RUNTIME = {
    "loaded": False,
    "active": False,
    "code": "",
    "label": "",
    "duration_seconds": 0,
    "max_devices": 1,
    "allow_4k": False,
    "allow_download": False,
    "price_inr": None,
    "device_id": "",
    "activated_epoch": 0.0,
    "expires_epoch": 0.0,
    "unlock_until_mono": 0.0,
    "last_remote_check_mono": 0.0,
    "last_reason": "Enter a VIP Access Key to unlock StreamHub",
}

def _parse_rfc3339_utc(ts_str):
    from datetime import datetime, timezone
    s = str(ts_str or "").strip()
    if not s:
        return 0.0
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    m = re.match(r'^(.+\.\d{6})\d+([+-].+)$', s)
    if m:
        s = m.group(1) + m.group(2)
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0

def _parse_http_date_utc(headers_obj):
    import email.utils
    from datetime import timezone
    try:
        d = headers_obj.get("Date") if headers_obj else ""
        if d:
            dt = email.utils.parsedate_to_datetime(d)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
    except Exception:
        pass
    return time.time()

def _get_vip_device_id():
    env_id = str(os.environ.get("STREAMHUB_DEVICE_ID", "") or "").strip()
    if env_id:
        return env_id[:32]
    cfg = _load_app_config()
    saved = str(cfg.get("vip_device_id", "") or "").strip()
    if saved:
        return saved[:32]
    gen = "dev_" + hashlib.sha256(f"{uuid.getnode()}:{uuid.uuid4().hex}".encode("utf-8")).hexdigest()[:20]
    _save_app_config({"vip_device_id": gen})
    return gen

def _firebase_ensure_token(force_refresh=False):
    cfg = _load_app_config()
    auth = cfg.get("vip_auth") if isinstance(cfg.get("vip_auth"), dict) else {}
    now = time.time()
    id_tok = str(auth.get("id_token", "") or "").strip()
    ref_tok = str(auth.get("refresh_token", "") or "").strip()
    uid = str(auth.get("uid", "") or "").strip()
    exp_at = float(auth.get("expires_at", 0) or 0)

    if not force_refresh and id_tok and (exp_at - now) > 90:
        return id_tok, uid, ""

    if ref_tok:
        try:
            body_bytes = urllib.parse.urlencode({
                "grant_type": "refresh_token",
                "refresh_token": ref_tok,
            }).encode("utf-8")
            req = urllib.request.Request(
                f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_API_KEY}",
                data=body_bytes,
                headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "StreamHub/2.6"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                d = json.loads(r.read().decode("utf-8", "ignore"))
            new_tok = str(d.get("id_token", "") or "").strip()
            new_ref = str(d.get("refresh_token", ref_tok) or ref_tok).strip()
            new_uid = str(d.get("user_id", uid) or uid).strip()
            ttl = max(300, int(d.get("expires_in", 3600) or 3600))
            if new_tok:
                _save_app_config({"vip_auth": {"id_token": new_tok, "refresh_token": new_ref, "uid": new_uid, "expires_at": time.time() + ttl}})
                return new_tok, new_uid, ""
        except Exception:
            pass

    try:
        payload = json.dumps({"returnSecureToken": True}).encode("utf-8")
        req = urllib.request.Request(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_API_KEY}",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "StreamHub/2.6"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
        new_tok = str(d.get("idToken", "") or "").strip()
        new_ref = str(d.get("refreshToken", "") or "").strip()
        new_uid = str(d.get("localId", "") or "").strip()
        ttl = max(300, int(d.get("expiresIn", 3600) or 3600))
        if not new_tok:
            return "", "", "Failed to obtain Firebase Anonymous Auth token"
        _save_app_config({"vip_auth": {"id_token": new_tok, "refresh_token": new_ref, "uid": new_uid, "expires_at": time.time() + ttl}})
        return new_tok, new_uid, ""
    except Exception as e:
        return "", "", f"Firebase connection failed: {e}"

def _firestore_request(method, url, payload=None, id_token=None):
    hdrs = {"Accept": "application/json", "User-Agent": "StreamHub/2.6"}
    if id_tok := str(id_token or "").strip():
        hdrs["Authorization"] = f"Bearer {id_tok}"
    data = None
    if payload is not None:
        hdrs["Content-Type"] = "application/json; charset=utf-8"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            srv_now = _parse_http_date_utc(r.headers)
            raw = r.read().decode("utf-8", "ignore")
            return r.status, (json.loads(raw) if raw.strip() else {}), srv_now
    except urllib.error.HTTPError as he:
        srv_now = _parse_http_date_utc(he.headers)
        raw = ""
        try:
            raw = he.read().decode("utf-8", "ignore")
        except Exception:
            pass
        try:
            err_json = json.loads(raw) if raw.strip() else {}
        except Exception:
            err_json = {"error": {"message": raw or str(he)}}
        return he.code, err_json, srv_now

def _fs_decode_vip_doc(doc):
    flds = (doc or {}).get("fields") or {}
    def _val(v):
        if not isinstance(v, dict):
            return None
        if "stringValue" in v:
            return str(v["stringValue"])
        if "integerValue" in v:
            return int(v["integerValue"])
        if "doubleValue" in v:
            return float(v["doubleValue"])
        if "booleanValue" in v:
            return bool(v["booleanValue"])
        if "timestampValue" in v:
            return str(v["timestampValue"])
        if "nullValue" in v:
            return None
        if "arrayValue" in v:
            vals = (v.get("arrayValue") or {}).get("values") or []
            return [_val(x) for x in vals]
        return None
    dev_ids = _val(flds.get("device_ids")) or []
    act_raw = _val(flds.get("activated_at"))
    dur_val = int(_val(flds.get("duration_seconds")) or 0)
    explicit_4k = _val(flds.get("allow_4k"))
    allow_4k = bool(explicit_4k) if explicit_4k is not None else (dur_val > VIP_4K_CUTOFF_SEC)
    allow_download = bool((dur_val >= VIP_DOWNLOAD_CUTOFF_SEC) or allow_4k)
    pr_raw = _val(flds.get("price_inr"))
    return {
        "name": str((doc or {}).get("name") or ""),
        "update_time": str((doc or {}).get("updateTime") or ""),
        "code": str(_val(flds.get("code")) or ""),
        "duration_seconds": dur_val,
        "max_devices": int(_val(flds.get("max_devices")) or 1),
        "allow_4k": allow_4k,
        "allow_download": allow_download,
        "price_inr": int(pr_raw) if pr_raw is not None else None,
        "revoked": bool(_val(flds.get("revoked"))),
        "device_ids": [str(x) for x in dev_ids if isinstance(x, str) and x],
        "activated_at_iso": str(act_raw or ""),
        "activated_epoch": _parse_rfc3339_utc(act_raw) if act_raw else 0.0,
        "label": str(_val(flds.get("label")) or ""),
    }

def _vip_persist_session(code="", label="", duration_seconds=0, max_devices=1, allow_4k=None, allow_download=None, price_inr=None, device_id="", activated_epoch=0.0, expires_epoch=0.0, remaining_sec=0.0, reason=""):
    mono_now = time.monotonic()
    wall_now = time.time()
    dur_i = int(duration_seconds or 0)
    eff_4k = (dur_i > VIP_4K_CUTOFF_SEC) if allow_4k is None else bool(allow_4k)
    eff_dl = bool((dur_i >= VIP_DOWNLOAD_CUTOFF_SEC) or eff_4k) if allow_download is None else bool(allow_download or eff_4k)
    active = bool(code and remaining_sec > 0.05)
    with _VIP_LOCK:
        _VIP_RUNTIME.update({
            "loaded": True,
            "active": active,
            "code": code if active else "",
            "label": label if active else "",
            "duration_seconds": dur_i,
            "max_devices": int(max_devices or 1),
            "allow_4k": eff_4k if active else False,
            "allow_download": eff_dl if active else False,
            "price_inr": int(price_inr) if (active and price_inr is not None) else None,
            "device_id": device_id or _get_vip_device_id(),
            "activated_epoch": float(activated_epoch or 0.0),
            "expires_epoch": float(expires_epoch or 0.0),
            "unlock_until_mono": (mono_now + remaining_sec) if active else 0.0,
            "last_remote_check_mono": mono_now,
            "last_reason": reason or ("VIP Active" if active else "Enter a VIP Access Key to unlock StreamHub"),
        })
    if active:
        _save_app_config({
            "vip_session": {
                "code": code,
                "label": label,
                "duration_seconds": dur_i,
                "max_devices": int(max_devices or 1),
                "allow_4k": eff_4k,
                "allow_download": eff_dl,
                "price_inr": int(price_inr) if price_inr is not None else None,
                "device_id": device_id,
                "activated_epoch": float(activated_epoch or 0.0),
                "expires_epoch": float(expires_epoch or 0.0),
                "expires_wall_est": wall_now + remaining_sec,
            }
        })
    else:
        _save_app_config({"vip_session": None})

def _vip_load_persisted_once():
    with _VIP_LOCK:
        if _VIP_RUNTIME["loaded"]:
            return
        _VIP_RUNTIME["loaded"] = True
        _VIP_RUNTIME["device_id"] = _get_vip_device_id()
    cfg = _load_app_config()
    sess = cfg.get("vip_session")
    if not isinstance(sess, dict) or not sess.get("code"):
        return
    rem = float(sess.get("expires_wall_est", 0) or 0) - time.time()
    if rem > 0.2:
        dur_i = int(sess.get("duration_seconds") or 0)
        eff_4k = bool(sess["allow_4k"]) if "allow_4k" in sess else (dur_i > VIP_4K_CUTOFF_SEC)
        eff_dl = bool(sess["allow_download"]) if "allow_download" in sess else bool(dur_i >= VIP_DOWNLOAD_CUTOFF_SEC or eff_4k)
        with _VIP_LOCK:
            _VIP_RUNTIME.update({
                "active": True,
                "code": str(sess.get("code") or ""),
                "label": str(sess.get("label") or ""),
                "duration_seconds": dur_i,
                "max_devices": int(sess.get("max_devices") or 1),
                "allow_4k": eff_4k,
                "allow_download": eff_dl,
                "price_inr": sess.get("price_inr"),
                "device_id": str(sess.get("device_id") or _get_vip_device_id()),
                "activated_epoch": float(sess.get("activated_epoch") or 0.0),
                "expires_epoch": float(sess.get("expires_epoch") or 0.0),
                "unlock_until_mono": time.monotonic() + rem,
                "last_remote_check_mono": 0.0,
                "last_reason": "VIP Active",
            })
        threading.Thread(target=_vip_verify_remote_sync, daemon=True).start()
    else:
        _vip_persist_session(reason="VIP Key has expired — please enter a new key")

def _vip_verify_remote_sync():
    with _VIP_LOCK:
        code = _VIP_RUNTIME.get("code", "")
        if not code:
            return
        dev_id = _VIP_RUNTIME.get("device_id") or _get_vip_device_id()
    id_tok, _, auth_err = _firebase_ensure_token()
    if not id_tok:
        return
    url = f"{FIREBASE_FIRESTORE_BASE}/vip_keys/{urllib.parse.quote(code, safe='')}"
    status, data, srv_now = _firestore_request("GET", url, id_token=id_tok)
    if status == 401:
        id_tok, _, _ = _firebase_ensure_token(force_refresh=True)
        if id_tok:
            status, data, srv_now = _firestore_request("GET", url, id_token=id_tok)
    if status == 404:
        _vip_persist_session(reason="This VIP Key has been removed")
        return
    if status != 200 or not isinstance(data, dict) or not data.get("fields"):
        return
    parsed = _fs_decode_vip_doc(data)
    if parsed["revoked"]:
        _vip_persist_session(reason="This VIP Key has been blocked by the administrator")
        return
    if dev_id not in parsed["device_ids"]:
        _vip_persist_session(reason="Device lock was reset or changed — please re-enter your key")
        return
    if parsed["activated_epoch"] > 0 and parsed["duration_seconds"] > 0:
        exp_ep = parsed["activated_epoch"] + parsed["duration_seconds"]
        rem = exp_ep - srv_now
        if rem <= 0:
            _vip_persist_session(reason="VIP Key has expired — please enter a new key")
        else:
            _vip_persist_session(
                code=code, label=parsed["label"],
                duration_seconds=parsed["duration_seconds"], max_devices=parsed["max_devices"],
                allow_4k=parsed["allow_4k"], allow_download=parsed["allow_download"], price_inr=parsed["price_inr"],
                device_id=dev_id, activated_epoch=parsed["activated_epoch"],
                expires_epoch=exp_ep, remaining_sec=rem, reason="VIP Active",
            )

def activate_vip_key(raw_key):
    code = re.sub(r'[^A-Z0-9_-]', '', str(raw_key or '').strip().upper())
    if len(code) < 6 or len(code) > 40:
        return {"success": False, "error": "VIP Key must be between 6 and 40 characters"}
    dev_id = _get_vip_device_id()
    id_tok, _, auth_err = _firebase_ensure_token()
    if not id_tok:
        return {"success": False, "error": auth_err or "Firebase login error — please check your internet connection"}

    doc_path = f"projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents/vip_keys/{urllib.parse.quote(code, safe='')}"
    get_url = f"{FIREBASE_FIRESTORE_BASE}/vip_keys/{urllib.parse.quote(code, safe='')}"
    status, data, srv_now = _firestore_request("GET", get_url, id_token=id_tok)
    if status == 401:
        id_tok, _, _ = _firebase_ensure_token(force_refresh=True)
        if id_tok:
            status, data, srv_now = _firestore_request("GET", get_url, id_token=id_tok)

    if status == 404:
        return {"success": False, "error": "Invalid VIP Key — code not found in database"}
    if status != 200 or not isinstance(data, dict) or not data.get("fields"):
        err_msg = ((data or {}).get("error") or {}).get("message") or f"Firestore HTTP {status}"
        return {"success": False, "error": f"Key verification failed: {err_msg}"}

    parsed = _fs_decode_vip_doc(data)
    if parsed["revoked"]:
        _vip_persist_session(reason="This VIP Key has been blocked")
        return {"success": False, "error": "This VIP Key has been blocked or revoked by the administrator"}
    dur = parsed["duration_seconds"]
    if dur <= 0:
        return {"success": False, "error": "Invalid VIP key duration"}
    max_dev = max(1, parsed["max_devices"])
    devs = list(parsed["device_ids"])

    if parsed["activated_epoch"] > 0:
        exp_ep = parsed["activated_epoch"] + dur
        rem = exp_ep - srv_now
        if rem <= 0:
            _vip_persist_session(reason="This VIP Key has expired")
            return {"success": False, "expired": True, "error": "This VIP Key has expired — please enter a new key"}
        if dev_id not in devs:
            if len(devs) >= max_dev:
                return {"success": False, "error": f"Device Limit Reached ({len(devs)}/{max_dev})! This key is already locked to another device."}
            devs.append(dev_id)
            commit_body = {
                "writes": [{
                    "update": {
                        "name": doc_path,
                        "fields": {
                            "activated_at": {"timestampValue": parsed["activated_at_iso"]},
                            "device_ids": {"arrayValue": {"values": [{"stringValue": d} for d in devs]}},
                        },
                    },
                    "updateMask": {"fieldPaths": ["activated_at", "device_ids"]},
                    "currentDocument": {"updateTime": parsed["update_time"]},
                }]
            }
            c_st, c_res, c_srv_now = _firestore_request("POST", f"{FIREBASE_FIRESTORE_BASE}:commit", payload=commit_body, id_token=id_tok)
            if c_st != 200:
                c_msg = ((c_res or {}).get("error") or {}).get("message") or f"HTTP {c_st}"
                return {"success": False, "error": f"Could not bind device lock: {c_msg}"}
            srv_now = c_srv_now or srv_now
            rem = max(0.0, exp_ep - srv_now)
        act_ep = parsed["activated_epoch"]
    else:
        if len(devs) >= max_dev and dev_id not in devs:
            return {"success": False, "error": "Device Limit Reached! This key is already active on another device."}
        if dev_id not in devs:
            devs.append(dev_id)
        commit_body = {
            "writes": [{
                "update": {
                    "name": doc_path,
                    "fields": {
                        "activated_at": {"timestampValue": "1970-01-01T00:00:00Z"},
                        "device_ids": {"arrayValue": {"values": [{"stringValue": d} for d in devs]}},
                    },
                },
                "updateMask": {"fieldPaths": ["activated_at", "device_ids"]},
                "currentDocument": {"updateTime": parsed["update_time"]},
                "updateTransforms": [
                    {"fieldPath": "activated_at", "setToServerValue": "REQUEST_TIME"}
                ],
            }]
        }
        c_st, c_res, c_srv_now = _firestore_request("POST", f"{FIREBASE_FIRESTORE_BASE}:commit", payload=commit_body, id_token=id_tok)
        if c_st != 200:
            c_msg = ((c_res or {}).get("error") or {}).get("message") or f"HTTP {c_st}"
            return {"success": False, "error": f"Key activation failed: {c_msg}"}
        wr = ((c_res or {}).get("writeResults") or [{}])[0]
        commit_ts_iso = wr.get("updateTime") or c_res.get("commitTime") or ""
        act_ep = _parse_rfc3339_utc(commit_ts_iso) or c_srv_now
        srv_now = max(act_ep, c_srv_now or act_ep)
        exp_ep = act_ep + dur
        rem = max(0.0, dur - max(0.0, srv_now - act_ep))

    if rem <= 0.05:
        _vip_persist_session(reason="VIP Key expired immediately upon activation")
        return {"success": False, "expired": True, "error": "This VIP Key has already expired"}

    _vip_persist_session(
        code=code, label=parsed["label"] or (f"{dur}s Test" if dur < 120 else "VIP Pass"),
        duration_seconds=dur, max_devices=max_dev,
        allow_4k=parsed["allow_4k"], allow_download=parsed["allow_download"], price_inr=parsed["price_inr"],
        device_id=dev_id, activated_epoch=act_ep, expires_epoch=exp_ep, remaining_sec=rem,
        reason="VIP Unlocked!",
    )
    st = get_vip_status(verify_remote=False)
    st["message"] = f"VIP Unlocked! ({st.get('label') or code})"
    return st

def deactivate_vip_session():
    _vip_persist_session(reason="VIP Key has been locked")
    return get_vip_status(verify_remote=False)

def get_vip_status(verify_remote=False):
    _vip_load_persisted_once()
    if verify_remote:
        _vip_verify_remote_sync()
    mono_now = time.monotonic()
    need_bg_sync = False
    with _VIP_LOCK:
        act = bool(_VIP_RUNTIME["active"])
        rem = max(0.0, _VIP_RUNTIME["unlock_until_mono"] - mono_now) if act else 0.0
        if act and rem <= 0.0:
            _VIP_RUNTIME["active"] = False
            _VIP_RUNTIME["code"] = ""
            _VIP_RUNTIME["allow_4k"] = False
            _VIP_RUNTIME["allow_download"] = False
            _VIP_RUNTIME["price_inr"] = None
            _VIP_RUNTIME["unlock_until_mono"] = 0.0
            _VIP_RUNTIME["last_reason"] = "VIP Key time expired — please enter a new key"
            act = False
            need_bg_sync = False
            should_clear_disk = True
        else:
            should_clear_disk = False
            if act and (mono_now - _VIP_RUNTIME["last_remote_check_mono"]) > 20.0:
                _VIP_RUNTIME["last_remote_check_mono"] = mono_now
                need_bg_sync = True
        res = {
            "success": True,
            "active": act,
            "remaining_sec": round(rem, 2),
            "code": _VIP_RUNTIME["code"] if act else "",
            "label": _VIP_RUNTIME["label"] if act else "",
            "duration_seconds": _VIP_RUNTIME["duration_seconds"],
            "max_devices": _VIP_RUNTIME["max_devices"],
            "allow_4k": bool(_VIP_RUNTIME["allow_4k"]) if act else False,
            "allow_download": bool(_VIP_RUNTIME["allow_download"]) if act else False,
            "price_inr": _VIP_RUNTIME["price_inr"] if act else None,
            "device_id": _VIP_RUNTIME["device_id"] or _get_vip_device_id(),
            "reason": _VIP_RUNTIME["last_reason"],
            "plans": VIP_PLANS_META,
        }
    if should_clear_disk:
        _save_app_config({"vip_session": None})
    if need_bg_sync:
        threading.Thread(target=_vip_verify_remote_sync, daemon=True).start()
    return res

def is_vip_active():
    return bool(get_vip_status(verify_remote=False).get("active"))

def is_4k_unlocked():
    st = get_vip_status(verify_remote=False)
    return bool(st.get("active") and st.get("allow_4k"))

def is_download_unlocked():
    st = get_vip_status(verify_remote=False)
    return bool(st.get("active") and st.get("allow_download"))

def _require_vip_or_error():
    st = get_vip_status(verify_remote=False)
    if st.get("active"):
        return True, None
    msg = st.get("reason") or "VIP Key required — please enter an access key to continue"
    return False, {"success": False, "vip_required": True, "error": msg, "message": msg}

def get_user_settings():
    cfg = _load_app_config()
    prof = dict(_DEFAULT_PROFILE)
    if isinstance(cfg.get("profile"), dict):
        for k in _DEFAULT_PROFILE:
            if cfg["profile"].get(k) is not None and str(cfg["profile"].get(k)).strip() != "":
                prof[k] = str(cfg["profile"][k]).strip()[:48]
    theme = str(cfg.get("theme", "netflix") or "netflix").strip().lower()
    if theme not in _VALID_THEMES:
        theme = "netflix"
    prefs = dict(_DEFAULT_DOWNLOAD_PREFS)
    if isinstance(cfg.get("download_prefs"), dict):
        dp = cfg["download_prefs"]
        try:
            lim = int(dp.get("storage_limit_gb", prefs["storage_limit_gb"]))
            if lim in (0, 2, 4, 8, 16):
                prefs["storage_limit_gb"] = lim
        except Exception:
            pass
        if "auto_delete_watched" in dp:
            prefs["auto_delete_watched"] = bool(dp["auto_delete_watched"])
        try:
            exp = int(dp.get("auto_expire_days", prefs["auto_expire_days"]))
            if exp in (0, 7, 14, 30):
                prefs["auto_expire_days"] = exp
        except Exception:
            pass
        dq = str(dp.get("default_quality", prefs["default_quality"]) or "").lower()
        if dq in ("480p", "720p", "1080p", "ask"):
            prefs["default_quality"] = dq
        sq = str(dp.get("stream_quality", prefs["stream_quality"]) or "").lower()
        if sq in ("auto", "480p", "720p", "1080p", "4k"):
            prefs["stream_quality"] = sq

    return {
        "success": True,
        "version": APP_VERSION,
        "update_url": _get_effective_update_url()[1],
        "profile": prof,
        "theme": theme,
        "download_prefs": prefs,
        "storage": get_offline_storage_summary(prefs),
    }

def save_user_settings(body):
    if not isinstance(body, dict):
        return get_user_settings()
    cur = get_user_settings()
    updates = {}
    if isinstance(body.get("profile"), dict):
        prof = dict(cur["profile"])
        p_in = body["profile"]
        if "name" in p_in:
            nm = str(p_in["name"] or "").strip()[:32]
            prof["name"] = nm or _DEFAULT_PROFILE["name"]
        if "avatar" in p_in:
            av = str(p_in["avatar"] or "").strip()[:8]
            if av:
                prof["avatar"] = av
        if "preferred_language" in p_in:
            prof["preferred_language"] = str(p_in["preferred_language"] or "Auto").strip()[:24] or "Auto"
        updates["profile"] = prof
    if "theme" in body:
        th = str(body["theme"] or "").strip().lower()
        if th in _VALID_THEMES:
            updates["theme"] = th
    if isinstance(body.get("download_prefs"), dict):
        dp = dict(cur["download_prefs"])
        d_in = body["download_prefs"]
        if "storage_limit_gb" in d_in:
            try:
                lim = int(d_in["storage_limit_gb"])
                if lim in (0, 2, 4, 8, 16):
                    dp["storage_limit_gb"] = lim
            except Exception:
                pass
        if "auto_delete_watched" in d_in:
            dp["auto_delete_watched"] = bool(d_in["auto_delete_watched"])
        if "auto_expire_days" in d_in:
            try:
                exp = int(d_in["auto_expire_days"])
                if exp in (0, 7, 14, 30):
                    dp["auto_expire_days"] = exp
            except Exception:
                pass
        if "default_quality" in d_in:
            dq = str(d_in["default_quality"] or "").lower()
            if dq in ("480p", "720p", "1080p", "ask"):
                dp["default_quality"] = dq
        if "stream_quality" in d_in:
            sq = str(d_in["stream_quality"] or "").lower()
            if sq in ("auto", "480p", "720p", "1080p", "4k"):
                dp["stream_quality"] = sq
        updates["download_prefs"] = dp

    if updates:
        _save_app_config(updates)
        if "download_prefs" in updates:
            # Refresh expiry timestamps on completed downloads if auto_expire_days updated
            exp_days = int(updates["download_prefs"].get("auto_expire_days", 7) or 0)
            with _OFFLINE_LOCK:
                items = _load_offline_db_unlocked()
                changed = False
                for it in items:
                    if it.get("status") == "completed":
                        comp = float(it.get("completed_at") or it.get("created_at") or time.time())
                        it["expires_at"] = (comp + exp_days * 86400.0) if exp_days > 0 else 0
                        changed = True
                if changed:
                    _save_offline_db_unlocked(items)
            _cleanup_offline_downloads()
    return get_user_settings()

def get_offline_storage_summary(prefs_override=None):
    _ensure_offline_dir()
    if prefs_override is None:
        cfg = _load_app_config()
        dp = cfg.get("download_prefs") if isinstance(cfg.get("download_prefs"), dict) else {}
        try:
            lim_gb = int(dp.get("storage_limit_gb", _DEFAULT_DOWNLOAD_PREFS["storage_limit_gb"]))
        except Exception:
            lim_gb = _DEFAULT_DOWNLOAD_PREFS["storage_limit_gb"]
    else:
        lim_gb = int(prefs_override.get("storage_limit_gb", 4))
    limit_bytes = lim_gb * (1024 ** 3) if lim_gb > 0 else 0
    used = 0
    active_cnt = 0
    completed_cnt = 0
    paused_cnt = 0
    with _OFFLINE_LOCK:
        for it in _load_offline_db_unlocked():
            st = it.get("status")
            if st in ("downloading", "queued"):
                active_cnt += 1
                used += int(it.get("downloaded_bytes") or 0)
            elif st == "completed":
                completed_cnt += 1
                fp = _offline_item_path(it, part=False)
                if fp and os.path.isfile(fp):
                    used += os.path.getsize(fp)
                else:
                    used += int(it.get("total_bytes") or it.get("downloaded_bytes") or 0)
            elif st == "paused":
                paused_cnt += 1
                used += int(it.get("downloaded_bytes") or 0)
            else:
                used += int(it.get("downloaded_bytes") or 0)
    return {
        "used_bytes": used,
        "limit_gb": lim_gb,
        "limit_bytes": limit_bytes,
        "active_count": active_cnt,
        "completed_count": completed_cnt,
        "paused_count": paused_cnt,
        "total_count": active_cnt + completed_cnt + paused_cnt,
    }

def _delete_offline_files_only(entry):
    for part_flag in (False, True):
        fp = _offline_item_path(entry, part=part_flag)
        if fp and os.path.isfile(fp):
            try:
                os.remove(fp)
            except Exception:
                pass
    seg_fp = os.path.join(_OFFLINE_DIR, f"{(entry or {}).get('id', '')}.segs.json")
    if seg_fp and os.path.isfile(seg_fp):
        try:
            os.remove(seg_fp)
        except Exception:
            pass

def _cleanup_offline_downloads():
    cfg = _load_app_config()
    dp = cfg.get("download_prefs") if isinstance(cfg.get("download_prefs"), dict) else {}
    auto_del_watched = bool(dp.get("auto_delete_watched", _DEFAULT_DOWNLOAD_PREFS["auto_delete_watched"]))
    try:
        exp_days = int(dp.get("auto_expire_days", _DEFAULT_DOWNLOAD_PREFS["auto_expire_days"]))
    except Exception:
        exp_days = 7
    now = time.time()
    removed = 0
    with _OFFLINE_LOCK:
        items = _load_offline_db_unlocked()
        kept = []
        for it in items:
            dl_id = it.get("id")
            if dl_id in _OFFLINE_ACTIVE_WORKERS or _OFFLINE_ACTIVE_STREAMS.get(dl_id, 0) > 0:
                kept.append(it)
                continue
            should_del = False
            if it.get("status") == "completed":
                exp_at = float(it.get("expires_at") or 0)
                if exp_days > 0 and exp_at > 0 and now > exp_at:
                    should_del = True
                pct = float(it.get("last_watched_pct") or 0)
                wd_at = float(it.get("watched_done_at") or 0)
                if auto_del_watched and pct >= 95.0 and (now - wd_at) >= 10.0:
                    should_del = True
            if should_del:
                _delete_offline_files_only(it)
                removed += 1
            else:
                kept.append(it)
        if removed:
            _save_offline_db_unlocked(kept)
    return removed

def _evict_for_storage(needed_bytes, limit_bytes):
    if not limit_bytes or limit_bytes <= 0:
        return True, 0, ""
    _cleanup_offline_downloads()
    freed_cnt = 0
    for _pass in range(2):
        summary = get_offline_storage_summary()
        used = int(summary["used_bytes"])
        if needed_bytes <= 0 and used < limit_bytes:
            return True, freed_cnt, ""
        if needed_bytes > 0 and (used + needed_bytes) <= limit_bytes:
            return True, freed_cnt, ""
        with _OFFLINE_LOCK:
            items = _load_offline_db_unlocked()
            # Prefer deleting watched items (>=85%), oldest completed first
            candidates = [
                it for it in items
                if it.get("status") == "completed"
                and it.get("id") not in _OFFLINE_ACTIVE_WORKERS
                and _OFFLINE_ACTIVE_STREAMS.get(it.get("id"), 0) == 0
                and float(it.get("last_watched_pct") or 0) >= 85.0
            ]
            candidates.sort(key=lambda x: float(x.get("completed_at") or x.get("created_at") or 0))
            if not candidates:
                break
            victim = candidates[0]
            _delete_offline_files_only(victim)
            items = [it for it in items if it.get("id") != victim.get("id")]
            _save_offline_db_unlocked(items)
            freed_cnt += 1
    lim_gb = round(limit_bytes / (1024 ** 3), 1)
    return False, freed_cnt, f"Storage Limit ({lim_gb} GB) reached! Delete older downloads from My Downloads or increase the limit in Settings."

def _offline_match_audio_track(audio_tracks, desired_lang):
    dl = str(desired_lang or "").strip().lower()
    if not dl or dl in ("auto", "dual audio", "dual"):
        return 1
    aliases = {
        "hindi": ("hindi", "hin"),
        "english": ("english", "eng"),
        "tamil": ("tamil", "tam"),
        "telugu": ("telugu", "tel"),
        "malayalam": ("malayalam", "mal"),
        "kannada": ("kannada", "kan"),
        "bengali": ("bengali", "ben", "bangla"),
        "punjabi": ("punjabi", "pan"),
        "marathi": ("marathi", "mar"),
    }
    keys = aliases.get(dl, (dl,))
    for trk in (audio_tracks or []):
        name = str(trk.get("name") or "").lower()
        if any(k in name for k in keys):
            try:
                return int(trk.get("id") or 1)
            except Exception:
                return 1
    return 1

def note_offline_watch_progress(body, rec=None):
    off_id = str((body or {}).get("offline_id") or "").strip()
    m_key = str((body or {}).get("key") or "").strip()
    pct = 0.0
    if isinstance(rec, dict) and rec.get("pct") is not None:
        pct = float(rec.get("pct") or 0)
    elif (body or {}).get("duration_sec"):
        pos = float(body.get("position_sec") or 0)
        dur = float(body.get("duration_sec") or 0)
        if dur > 0:
            pct = round((pos / dur) * 100.0, 1)
    if not off_id and not m_key:
        return
    matched_done = False
    with _OFFLINE_LOCK:
        items = _load_offline_db_unlocked()
        changed = False
        for it in items:
            if (off_id and it.get("id") == off_id) or (m_key and it.get("media_key") == m_key):
                if pct > float(it.get("last_watched_pct") or 0):
                    it["last_watched_pct"] = pct
                    changed = True
                if pct >= 95.0 and not it.get("watched_done_at"):
                    it["watched_done_at"] = time.time()
                    changed = True
                    matched_done = True
        if changed:
            _save_offline_db_unlocked(items)
    if matched_done:
        cfg = get_user_settings()
        if cfg["download_prefs"].get("auto_delete_watched"):
            threading.Timer(12.0, _cleanup_offline_downloads).start()

def _pick_downloadable_url(resolved):
    """Select best direct MP4/MKV mirror URL from resolve_hubcloud output."""
    if not isinstance(resolved, dict):
        return "", "", []
    su = str(resolved.get("stream_url") or "")
    sk = str(resolved.get("seek_url") or "")
    cm = int(resolved.get("compress_method", 0) or 0)
    mirrors = resolved.get("mirrors") or []
    backups = []
    for m in mirrors:
        if isinstance(m, dict) and m.get("alive") and m.get("url"):
            u = str(m["url"])
            if '.m3u8' not in u and 'hdstream4u' not in u:
                backups.append(u)
    if su and '.m3u8' not in su and 'hdstream4u' not in su and cm == 0:
        return su, sk, backups
    if cm == 0 and backups:
        alt = backups[0]
        return alt, (sk or alt), backups[1:]
    return su, sk, backups

def _probe_mkv_tracks_from_file(fpath):
    try:
        sz = os.path.getsize(fpath)
        if sz < 1024:
            return []
        with open(fpath, 'rb') as f:
            head = f.read(min(2 * 1024 * 1024, sz))
            tail = b''
            if sz > len(head):
                f.seek(max(len(head), sz - 512 * 1024))
                tail = f.read(512 * 1024)
        minfo = parse_mkv_media_info(head, tail)
        return (minfo or {}).get("audio_tracks") or []
    except Exception:
        return []

_OFFLINE_STOP_EVENTS = {}

def _save_offline_segs(seg_path, chunk_size, total_bytes, done_set):
    try:
        tmp = seg_path + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({"chunk_size": chunk_size, "total_bytes": total_bytes, "done": sorted(done_set)}, f)
        os.replace(tmp, seg_path)
    except Exception:
        pass

def _run_offline_download_worker(dl_id):
    stop_flag = threading.Event()
    with _OFFLINE_LOCK:
        _OFFLINE_ACTIVE_WORKERS.add(dl_id)
        _OFFLINE_PAUSED_IDS.discard(dl_id)
        _OFFLINE_STOP_EVENTS[dl_id] = stop_flag
    try:
        entry = _get_offline_entry(dl_id)
        if not entry or dl_id in _OFFLINE_DELETED_IDS:
            return

        _update_offline_entry(dl_id, {"status": "downloading", "error": "", "speed_bps": 0})
        stream_url = str(entry.get("stream_url") or "")
        seek_url = str(entry.get("seek_url") or "")
        base_off = int(entry.get("base_off", 0) or 0)
        sub_sz = entry.get("sub_size", None)
        cm = int(entry.get("compress_method", 0) or 0)
        backups = list(entry.get("backup_urls") or [])
        hdhub_link = str(entry.get("hdhub_link") or "")

        if not stream_url and hdhub_link:
            res = _resolve_episode_cached(hdhub_link) or resolve_hubcloud(hdhub_link)
            if not res or not res.get("success"):
                _update_offline_entry(dl_id, {
                    "status": "error",
                    "error": (res or {}).get("error") or "Download link could not be resolved"
                })
                return
            cm = int(res.get("compress_method", 0) or 0)
            if res.get("is_zip") and res.get("zip_entries") and not entry.get("sub_size"):
                z_idx = int(entry.get("zip_index", 0) or 0)
                z_eps = res.get("zip_entries") or []
                if 0 <= z_idx < len(z_eps):
                    zep = z_eps[z_idx]
                    stream_url = zep.get("stream_url", "")
                    seek_url = zep.get("seek_url", "")
                    base_off = int(zep.get("base_offset", 0) or 0)
                    sub_sz = zep.get("sub_size", None)
                    cm = int(zep.get("compress_method", 0) or 0)
            else:
                stream_url, seek_url, backups = _pick_downloadable_url(res)
                base_off = int(res.get("base_offset", 0) or 0)
                sub_sz = res.get("sub_size", None)
            _update_offline_entry(dl_id, {
                "stream_url": stream_url,
                "seek_url": seek_url,
                "base_off": base_off,
                "sub_size": sub_sz,
                "compress_method": cm,
                "backup_urls": backups,
            })

        if not stream_url or '.m3u8' in stream_url or 'hdstream4u' in stream_url:
            _update_offline_entry(dl_id, {
                "status": "error",
                "error": "This mirror is a live HLS stream; choose another quality or CDN mirror for direct download"
            })
            return

        if cm == 8:
            _update_offline_entry(dl_id, {
                "status": "error",
                "error": "Compressed Deflate ZIP entries do not support resumable direct download yet"
            })
            return

        # Re-normalize filename extension once true stream_url is resolved
        ext = ".mp4" if ".mp4" in stream_url.lower() else ".mkv"
        if not str(entry.get("file_name") or "").endswith(ext):
            entry = _update_offline_entry(dl_id, {"file_name": f"{dl_id}{ext}"}) or entry

        part_path = _offline_item_path(entry, part=True)
        final_path = _offline_item_path(entry, part=False)
        seg_path = os.path.join(_OFFLINE_DIR, f"{dl_id}.segs.json")
        _ensure_offline_dir()

        sub_sz_int = int(sub_sz) if sub_sz is not None and int(sub_sz) > 0 else 0
        total_bytes = sub_sz_int if sub_sz_int > 0 else int(entry.get("total_bytes") or 0)

        urls_pool = []
        for u in ([seek_url] + [stream_url] + backups):
            if u and '.m3u8' not in u and u not in urls_pool:
                urls_pool.append(u)
        range_first = [u for u in urls_pool if is_range_capable(u)]
        other_urls = [u for u in urls_pool if not is_range_capable(u)]
        ordered_urls = range_first + other_urls
        if not ordered_urls:
            _update_offline_entry(dl_id, {"status": "error", "error": "Download URL is not available"})
            return

        # Probe Range capability + true content size for fast parallel downloading
        probe_url = ordered_urls[0]
        range_supported = False
        verified_range_urls = []
        for cand_u in (range_first or ordered_urls):
            if dl_id in _OFFLINE_DELETED_IDS:
                _delete_offline_files_only(entry); return
            if dl_id in _OFFLINE_PAUSED_IDS:
                _update_offline_entry(dl_id, {"status": "paused", "speed_bps": 0}); return
            try:
                p_resp, p_st = open_upstream_range(cand_u, range_hdr=f"bytes={base_off}-{base_off + 1}", hedges=(2, 5, 8), timeout=15)
                with p_resp:
                    if p_st == 206:
                        range_supported = True
                        verified_range_urls.append(cand_u)
                        if total_bytes <= 0:
                            cr = p_resp.headers.get('Content-Range', '')
                            m_cr = re.search(r'/(\d+)$', cr)
                            if m_cr:
                                total_bytes = int(m_cr.group(1))
                        break
                    elif p_st == 200 and total_bytes <= 0:
                        cl = p_resp.headers.get('Content-Length', '')
                        if cl:
                            total_bytes = int(cl)
            except Exception:
                continue

        if verified_range_urls:
            for u in range_first:
                if u not in verified_range_urls:
                    verified_range_urls.append(u)

        cfg = get_user_settings()
        limit_bytes = int(cfg["download_prefs"].get("storage_limit_gb", 4) or 0) * (1024 ** 3)
        downloaded = int(entry.get("downloaded_bytes") or 0)
        remaining_est = max(0, total_bytes - downloaded) if total_bytes > 0 else _parse_size_to_bytes(entry.get("size_label"))
        ok_cap, _, cap_err = _evict_for_storage(remaining_est, limit_bytes)
        if not ok_cap:
            _update_offline_entry(dl_id, {"status": "error", "error": cap_err, "speed_bps": 0})
            return
        if total_bytes > 0:
            _update_offline_entry(dl_id, {"total_bytes": total_bytes})

        # ── Fast Multi-Connection Segmented Parallel Range Mode (up to 10 parallel streams) ──
        if range_supported and total_bytes >= 2 * 1024 * 1024 and verified_range_urls:
            if total_bytes >= 256 * 1024 * 1024:
                chunk_size = 4 * 1024 * 1024
                max_conns = 10
            elif total_bytes >= 16 * 1024 * 1024:
                chunk_size = 2 * 1024 * 1024
                max_conns = 8
            else:
                chunk_size = 512 * 1024
                max_conns = 6
            done_set = set()
            if os.path.isfile(seg_path):
                try:
                    with open(seg_path, 'r', encoding='utf-8') as sf:
                        sd = json.load(sf)
                    if int(sd.get("chunk_size") or 0) > 0 and int(sd.get("total_bytes") or 0) == total_bytes:
                        chunk_size = int(sd["chunk_size"])
                        done_set = set(int(x) for x in (sd.get("done") or []))
                except Exception:
                    done_set = set()
            elif os.path.isfile(part_path):
                # Migrate any partial sequential download without losing completed chunks
                old_sz = os.path.getsize(part_path)
                if 0 < old_sz < total_bytes:
                    done_set = set(range(old_sz // chunk_size))

            total_segs = (total_bytes + chunk_size - 1) // chunk_size
            pending_segs = [i for i in range(total_segs) if i not in done_set]

            # Pre-size sparse .part file safely for concurrent random-offset segment writes
            with open(part_path, 'r+b' if os.path.isfile(part_path) else 'wb') as pf:
                if os.path.getsize(part_path) < total_bytes:
                    pf.truncate(total_bytes)
            _save_offline_segs(seg_path, chunk_size, total_bytes, done_set)

            def _seg_len(sidx):
                s0 = sidx * chunk_size
                return min(chunk_size, total_bytes - s0)

            completed_bytes_base = sum(_seg_len(i) for i in done_set if 0 <= i < total_segs)
            seg_lock = threading.Lock()
            cursor = [0]
            inflight_bytes = {}  # {worker_id: int}
            worker_errors = []
            n_workers = min(max_conns, max(1, len(pending_segs)))

            def _chunk_worker(wid):
                while not stop_flag.is_set():
                    if dl_id in _OFFLINE_DELETED_IDS or dl_id in _OFFLINE_PAUSED_IDS:
                        stop_flag.set()
                        return
                    with seg_lock:
                        if cursor[0] >= len(pending_segs):
                            inflight_bytes.pop(wid, None)
                            return
                        seg_idx = pending_segs[cursor[0]]
                        cursor[0] += 1
                        inflight_bytes[wid] = 0

                    seg_off = seg_idx * chunk_size
                    seg_len = min(chunk_size, total_bytes - seg_off)
                    written = 0
                    seg_ok = False
                    for attempt in range(7):
                        if stop_flag.is_set() or dl_id in _OFFLINE_DELETED_IDS or dl_id in _OFFLINE_PAUSED_IDS:
                            stop_flag.set()
                            return
                        u = verified_range_urls[(seg_idx + attempt) % len(verified_range_urls)]
                        u_start = base_off + seg_off + written
                        u_end = base_off + seg_off + seg_len - 1
                        try:
                            resp, st = open_upstream_range(u, range_hdr=f"bytes={u_start}-{u_end}", hedges=(1, 2, 4), timeout=20)
                            with resp:
                                if st != 206 and not (st == 200 and u_start == 0):
                                    raise RuntimeError(f"HTTP {st}")
                                with open(part_path, 'r+b', buffering=1048576) as out_f:
                                    out_f.seek(seg_off + written)
                                    while written < seg_len:
                                        if stop_flag.is_set() or dl_id in _OFFLINE_DELETED_IDS or dl_id in _OFFLINE_PAUSED_IDS:
                                            stop_flag.set()
                                            return
                                        buf = resp.read(min(524288, seg_len - written))
                                        if not buf:
                                            break
                                        out_f.write(buf)
                                        written += len(buf)
                                        with seg_lock:
                                            inflight_bytes[wid] = written
                                    out_f.flush()
                            if written >= seg_len:
                                seg_ok = True
                                break
                        except Exception as wex:
                            if attempt == 6:
                                worker_errors.append(str(wex))
                            stop_flag.wait(min(2.0, 0.35 * (attempt + 1)))

                    with seg_lock:
                        inflight_bytes.pop(wid, None)
                        if seg_ok:
                            done_set.add(seg_idx)
                        else:
                            stop_flag.set()
                            return

            threads = [threading.Thread(target=_chunk_worker, args=(wi,), daemon=True) for wi in range(n_workers)]
            for t in threads:
                t.start()

            prev_tot = completed_bytes_base
            last_t = time.time()
            last_seg_save = last_t
            last_done_cnt = len(done_set)
            speed_ema = 0.0
            while any(t.is_alive() for t in threads):
                if dl_id in _OFFLINE_DELETED_IDS or dl_id in _OFFLINE_PAUSED_IDS:
                    stop_flag.set()
                with seg_lock:
                    cur_done_cnt = len(done_set)
                    cur_done_bytes = sum(_seg_len(i) for i in done_set if 0 <= i < total_segs) + sum(inflight_bytes.values())
                now = time.time()
                dt = max(0.05, now - last_t)
                delta = max(0, cur_done_bytes - prev_tot)
                inst_bps = delta / dt
                speed_ema = inst_bps if speed_ema <= 0 else (speed_ema * 0.55 + inst_bps * 0.45)
                prev_tot = cur_done_bytes
                last_t = now
                downloaded = min(total_bytes, cur_done_bytes)
                _update_offline_entry(dl_id, {
                    "downloaded_bytes": downloaded,
                    "total_bytes": total_bytes,
                    "speed_bps": int(speed_ema),
                })
                if cur_done_cnt > last_done_cnt or (now - last_seg_save >= 1.2):
                    with seg_lock:
                        _save_offline_segs(seg_path, chunk_size, total_bytes, done_set)
                        last_done_cnt = len(done_set)
                    last_seg_save = now
                stop_flag.wait(0.35)

            for t in threads:
                t.join(timeout=2.0)

            _save_offline_segs(seg_path, chunk_size, total_bytes, done_set)
            downloaded = min(total_bytes, sum(_seg_len(i) for i in done_set if 0 <= i < total_segs))

            if dl_id in _OFFLINE_DELETED_IDS:
                _delete_offline_files_only(entry)
                return
            if dl_id in _OFFLINE_PAUSED_IDS:
                _update_offline_entry(dl_id, {"status": "paused", "downloaded_bytes": downloaded, "total_bytes": total_bytes, "speed_bps": 0})
                return
            if len(done_set) < total_segs:
                err_msg = worker_errors[-1] if worker_errors else "Connection interrupted"
                _update_offline_entry(dl_id, {
                    "status": "error",
                    "downloaded_bytes": downloaded,
                    "total_bytes": total_bytes,
                    "speed_bps": 0,
                    "error": f"Download paused ({err_msg}) — tap Resume to continue",
                })
                return
        else:
            # ── Sequential Fast Buffered Mode (Google CDN / servers without Range) ──
            downloaded = os.path.getsize(part_path) if os.path.isfile(part_path) else 0
            url_idx = 0
            retries = 0
            last_ui_ts = time.time()
            speed_ema = 0.0
            chunk_window_bytes = 0
            chunk_window_t0 = time.time()

            while True:
                if dl_id in _OFFLINE_DELETED_IDS:
                    _delete_offline_files_only(entry)
                    return
                if dl_id in _OFFLINE_PAUSED_IDS:
                    _update_offline_entry(dl_id, {"status": "paused", "downloaded_bytes": downloaded, "speed_bps": 0})
                    return
                if total_bytes > 0 and downloaded >= total_bytes:
                    break

                cur_url = ordered_urls[url_idx % len(ordered_urls)]
                req_start = base_off + downloaded
                if sub_sz_int > 0:
                    rng_hdr = f"bytes={req_start}-{base_off + sub_sz_int - 1}"
                else:
                    rng_hdr = f"bytes={req_start}-"

                try:
                    resp, st = open_upstream_range(cur_url, range_hdr=rng_hdr, hedges=(2, 5, 8), timeout=18)
                    with resp:
                        if st not in (200, 206):
                            raise RuntimeError(f"HTTP {st}")
                        if st == 200 and downloaded > 0:
                            if base_off > 0:
                                raise RuntimeError("ZIP server rejected byte range")
                            downloaded = 0
                            with open(part_path, 'wb'):
                                pass

                        if total_bytes <= 0:
                            cr = resp.headers.get('Content-Range', '')
                            m_cr = re.search(r'/(\d+)$', cr)
                            if m_cr:
                                total_bytes = int(m_cr.group(1))
                            elif resp.headers.get('Content-Length'):
                                cl = int(resp.headers['Content-Length'])
                                total_bytes = cl if (st == 200 or downloaded == 0) else (downloaded + cl)
                            if total_bytes > 0:
                                ok_cap2, _, cap_err2 = _evict_for_storage(max(0, total_bytes - downloaded), limit_bytes)
                                if not ok_cap2:
                                    _update_offline_entry(dl_id, {"status": "error", "error": cap_err2, "total_bytes": total_bytes})
                                    return
                                _update_offline_entry(dl_id, {"total_bytes": total_bytes})

                        mode = 'ab' if (downloaded > 0 and os.path.isfile(part_path)) else 'wb'
                        with open(part_path, mode) as out_f:
                            retries = 0
                            while True:
                                if dl_id in _OFFLINE_DELETED_IDS:
                                    out_f.close()
                                    _delete_offline_files_only(entry)
                                    return
                                if dl_id in _OFFLINE_PAUSED_IDS:
                                    out_f.flush()
                                    _update_offline_entry(dl_id, {
                                        "status": "paused",
                                        "downloaded_bytes": downloaded,
                                        "total_bytes": total_bytes,
                                        "speed_bps": 0,
                                    })
                                    return

                                to_read = 1048576
                                if total_bytes > 0:
                                    left = total_bytes - downloaded
                                    if left <= 0:
                                        break
                                    to_read = min(to_read, left)

                                buf = resp.read(to_read)
                                if not buf:
                                    break
                                out_f.write(buf)
                                n = len(buf)
                                downloaded += n
                                chunk_window_bytes += n

                                now = time.time()
                                dt_win = now - chunk_window_t0
                                if dt_win >= 0.45:
                                    inst_bps = chunk_window_bytes / max(0.05, dt_win)
                                    speed_ema = inst_bps if speed_ema <= 0 else (speed_ema * 0.55 + inst_bps * 0.45)
                                    chunk_window_bytes = 0
                                    chunk_window_t0 = now
                                if now - last_ui_ts >= 0.5:
                                    out_f.flush()
                                    _update_offline_entry(dl_id, {
                                        "downloaded_bytes": downloaded,
                                        "total_bytes": total_bytes,
                                        "speed_bps": int(speed_ema),
                                    })
                                    last_ui_ts = now

                        if total_bytes <= 0 or downloaded >= total_bytes:
                            break
                        raise RuntimeError("Premature stream end")
                except Exception as ex:
                    retries += 1
                    url_idx += 1
                    if retries > 6:
                        _update_offline_entry(dl_id, {
                            "status": "error",
                            "downloaded_bytes": downloaded,
                            "total_bytes": total_bytes,
                            "speed_bps": 0,
                            "error": f"Download paused ({ex}) — tap Resume to continue",
                        })
                        return
                    time.sleep(min(2.5, 0.4 * retries))

        if os.path.isfile(seg_path):
            try:
                os.remove(seg_path)
            except Exception:
                pass
        if os.path.isfile(part_path):
            if os.path.isfile(final_path):
                os.remove(final_path)
            os.replace(part_path, final_path)

        final_sz = os.path.getsize(final_path) if os.path.isfile(final_path) else downloaded
        tracks = _probe_mkv_tracks_from_file(final_path)
        pref_trk = _offline_match_audio_track(tracks, entry.get("language"))
        cfg_now = get_user_settings()
        exp_days = int(cfg_now["download_prefs"].get("auto_expire_days", 7) or 0)
        done_ts = time.time()
        _update_offline_entry(dl_id, {
            "status": "completed",
            "downloaded_bytes": final_sz,
            "total_bytes": final_sz,
            "speed_bps": 0,
            "error": "",
            "audio_tracks": tracks,
            "preferred_audio_track": pref_trk,
            "completed_at": done_ts,
            "expires_at": (done_ts + exp_days * 86400.0) if exp_days > 0 else 0,
        })
    except Exception as exc:
        _update_offline_entry(dl_id, {"status": "error", "error": str(exc), "speed_bps": 0})
    finally:
        with _OFFLINE_LOCK:
            _OFFLINE_ACTIVE_WORKERS.discard(dl_id)
            _OFFLINE_STOP_EVENTS.pop(dl_id, None)
            _OFFLINE_PAUSED_IDS.discard(dl_id)
            _OFFLINE_DELETED_IDS.discard(dl_id)

def list_offline_downloads():
    _cleanup_offline_downloads()
    cfg = get_user_settings()
    with _OFFLINE_LOCK:
        items = _load_offline_db_unlocked()
        out = []
        now = time.time()
        for it in reversed(items):
            d = dict(it)
            tot = int(d.get("total_bytes") or 0)
            dl = int(d.get("downloaded_bytes") or 0)
            d["pct"] = 100.0 if d.get("status") == "completed" else (round((dl * 100.0) / tot, 1) if tot > 0 else 0.0)
            exp_at = float(d.get("expires_at") or 0)
            d["expires_in_days"] = max(0, int((exp_at - now + 86399) // 86400)) if exp_at > now else None
            out.append(d)
    return {
        "success": True,
        "items": out,
        "storage": cfg["storage"],
        "prefs": cfg["download_prefs"],
    }

def start_offline_download(body):
    ok_vip, vip_err = _require_vip_or_error()
    if not ok_vip:
        return vip_err
    if not is_download_unlocked():
        msg_dl = "Offline Downloads require a 3 Months (Rs 119), 6 Months (Rs 199), or 1 Year (Rs 349) VIP Plan."
        return {"success": False, "vip_required": True, "upgrade_download_required": True, "error": msg_dl, "message": msg_dl}
    if not isinstance(body, dict):
        return {"success": False, "error": "Invalid download request"}
    _ensure_offline_dir()
    hdhub_link = str(body.get("hdhub_link") or "").strip()
    stream_url = str(body.get("stream_url") or "").strip()
    if not hdhub_link and not stream_url:
        return {"success": False, "error": "Download link is missing"}

    title = str(body.get("title") or "StreamHub Movie").strip()
    quality_label = str(body.get("quality_label") or "720p HD").strip()
    if not is_4k_unlocked() and _is_4k_marker(f"{quality_label} {body.get('size_label', '')}"):
        msg4k = "4K Offline Downloads require a 6 Months (Rs 199) or 1 Year (Rs 349) VIP Plan."
        return {"success": False, "vip_required": True, "upgrade_4k_required": True, "error": msg4k, "message": msg4k}
    language = str(body.get("language") or get_user_settings()["profile"].get("preferred_language") or "Auto").strip()
    size_label = str(body.get("size_label") or "").strip()
    media_key = str(body.get("media_key") or body.get("sid") or hdhub_link or title).strip()

    with _OFFLINE_LOCK:
        items = _load_offline_db_unlocked()
        for it in items:
            same_link = (hdhub_link and it.get("hdhub_link") == hdhub_link) or (stream_url and it.get("stream_url") == stream_url and int(it.get("zip_index", -1)) == int(body.get("zip_index", -1)))
            if same_link:
                if it.get("status") == "completed" and os.path.isfile(_offline_item_path(it)):
                    return {"success": True, "already_downloaded": True, "item": dict(it), "message": "This movie/episode is already saved offline"}
                if it.get("status") in ("downloading", "queued"):
                    return {"success": True, "item": dict(it), "message": "Download is already in progress"}
                # Resume existing paused/errored download with updated language if requested
                it["language"] = language
                it["status"] = "queued"
                it["error"] = ""
                _save_offline_db_unlocked(items)
                threading.Thread(target=_run_offline_download_worker, args=(it["id"],), daemon=True).start()
                return {"success": True, "resumed": True, "item": dict(it), "message": "Download resumed"}

    cfg = get_user_settings()
    limit_bytes = int(cfg["download_prefs"].get("storage_limit_gb", 4) or 0) * (1024 ** 3)
    est_bytes = int(body.get("sub_size") or 0) or _parse_size_to_bytes(size_label)
    ok_cap, _, cap_err = _evict_for_storage(est_bytes, limit_bytes)
    if not ok_cap:
        return {"success": False, "error": cap_err}

    dl_id = uuid.uuid4().hex[:12]
    ext = ".mp4" if ".mp4" in stream_url.lower() else ".mkv"
    new_item = {
        "id": dl_id,
        "title": title,
        "ep_label": str(body.get("ep_label") or "").strip(),
        "poster": str(body.get("poster") or "").strip(),
        "media_key": media_key,
        "parent_key": str(body.get("parent_key") or "").strip(),
        "quality_label": quality_label,
        "size_label": size_label,
        "language": language,
        "hdhub_link": hdhub_link,
        "stream_url": stream_url,
        "seek_url": str(body.get("seek_url") or "").strip(),
        "base_off": int(body.get("base_offset", 0) or 0),
        "sub_size": int(body["sub_size"]) if body.get("sub_size") is not None else None,
        "compress_method": int(body.get("compress_method", 0) or 0),
        "backup_urls": list(body.get("backup_urls") or []),
        "zip_index": int(body.get("zip_index", -1) if body.get("zip_index") is not None else -1),
        "file_name": f"{dl_id}{ext}",
        "total_bytes": int(body.get("sub_size") or 0),
        "downloaded_bytes": 0,
        "status": "queued",
        "error": "",
        "speed_bps": 0,
        "audio_tracks": [],
        "preferred_audio_track": 1,
        "last_watched_pct": 0.0,
        "watched_done_at": 0,
        "created_at": time.time(),
        "completed_at": 0,
        "expires_at": 0,
    }
    with _OFFLINE_LOCK:
        items = _load_offline_db_unlocked()
        items.append(new_item)
        _save_offline_db_unlocked(items)
    threading.Thread(target=_run_offline_download_worker, args=(dl_id,), daemon=True).start()
    return {"success": True, "item": new_item, "message": f"Downloading started ({quality_label} • {language})"}

def handle_offline_download_action(body):
    action = str((body or {}).get("action") or "").strip().lower()
    dl_id = str((body or {}).get("id") or "").strip()
    if action == "clear_all":
        with _OFFLINE_LOCK:
            items = _load_offline_db_unlocked()
            kept = []
            for it in items:
                iid = it.get("id")
                if iid in _OFFLINE_ACTIVE_WORKERS:
                    _OFFLINE_DELETED_IDS.add(iid)
                if _OFFLINE_ACTIVE_STREAMS.get(iid, 0) > 0:
                    kept.append(it)
                else:
                    _delete_offline_files_only(it)
            _save_offline_db_unlocked(kept)
        return {"success": True, **list_offline_downloads()}

    if not dl_id:
        return {"success": False, "error": "Download id missing"}

    if action == "pause":
        ev = None
        with _OFFLINE_LOCK:
            _OFFLINE_PAUSED_IDS.add(dl_id)
            ev = _OFFLINE_STOP_EVENTS.get(dl_id)
        if ev:
            ev.set()
        for _ in range(40):
            if dl_id not in _OFFLINE_ACTIVE_WORKERS:
                break
            time.sleep(0.025)
        with _OFFLINE_LOCK:
            items = _load_offline_db_unlocked()
            for it in items:
                if it.get("id") == dl_id and it.get("status") in ("downloading", "queued"):
                    it["status"] = "paused"
                    it["speed_bps"] = 0
            _save_offline_db_unlocked(items)
        return {"success": True, **list_offline_downloads()}

    if action == "resume":
        ok_vip, vip_err = _require_vip_or_error()
        if not ok_vip:
            return vip_err
        if not is_download_unlocked():
            msg_dl = "Resuming offline downloads requires a 3 Months (Rs 119), 6 Months (Rs 199), or 1 Year (Rs 349) VIP Plan."
            return {"success": False, "vip_required": True, "upgrade_download_required": True, "error": msg_dl, "message": msg_dl}
        entry = _get_offline_entry(dl_id)
        if not entry:
            return {"success": False, "error": "Download entry not found"}
        if not is_4k_unlocked() and _is_4k_marker(f"{entry.get('quality_label', '')} {entry.get('size_label', '')}"):
            msg4k = "4K Offline Downloads require a 6 Months (Rs 199) or 1 Year (Rs 349) VIP Plan."
            return {"success": False, "vip_required": True, "upgrade_4k_required": True, "error": msg4k, "message": msg4k}
        if dl_id in _OFFLINE_ACTIVE_WORKERS and dl_id in _OFFLINE_PAUSED_IDS:
            for _ in range(80):
                if dl_id not in _OFFLINE_ACTIVE_WORKERS:
                    break
                time.sleep(0.025)
        if dl_id not in _OFFLINE_ACTIVE_WORKERS:
            with _OFFLINE_LOCK:
                _OFFLINE_PAUSED_IDS.discard(dl_id)
            _update_offline_entry(dl_id, {"status": "queued", "error": ""})
            threading.Thread(target=_run_offline_download_worker, args=(dl_id,), daemon=True).start()
        return {"success": True, **list_offline_downloads()}

    if action == "delete":
        with _OFFLINE_LOCK:
            if _OFFLINE_ACTIVE_STREAMS.get(dl_id, 0) > 0:
                return {"success": False, "error": "This video is currently playing; close the player before deleting"}
            if dl_id in _OFFLINE_ACTIVE_WORKERS:
                _OFFLINE_DELETED_IDS.add(dl_id)
                ev_del = _OFFLINE_STOP_EVENTS.get(dl_id)
                if ev_del:
                    ev_del.set()
            items = _load_offline_db_unlocked()
            rem = []
            for it in items:
                if it.get("id") == dl_id:
                    _delete_offline_files_only(it)
                else:
                    rem.append(it)
            _save_offline_db_unlocked(rem)
        return {"success": True, **list_offline_downloads()}

    return {"success": False, "error": f"Unknown action: {action}"}

def _stop_previous_players():
    global _INBUILT_PLAYER_PROC, _EXTERNAL_VLC_USED
    with _INBUILT_PROC_LOCK:
        if _INBUILT_PLAYER_PROC is not None:
            try:
                if _INBUILT_PLAYER_PROC.poll() is None:
                    _INBUILT_PLAYER_PROC.terminate()
                    _INBUILT_PLAYER_PROC.wait(timeout=1.5)
            except Exception:
                try:
                    _INBUILT_PLAYER_PROC.kill()
                except Exception:
                    pass
            _INBUILT_PLAYER_PROC = None
    if _EXTERNAL_VLC_USED:
        _EXTERNAL_VLC_USED = False
        try:
            subprocess.run(["taskkill", "/F", "/IM", "vlc.exe"], capture_output=True, timeout=2)
        except Exception:
            pass

def launch_external_vlc(body, srv_port=None):
    cfg, err = _prepare_play_target(body, srv_port=srv_port)
    if err:
        return err
    if not os.path.exists(VLC_PATH):
        return {"success": False, "message": f"VLC player not found at {VLC_PATH}"}
    _stop_previous_players()

    extra_opts = [
        '--network-caching=2000',
        '--file-caching=1500',
        '--http-reconnect',
        '--avcodec-hw=any',
        '--avcodec-threads=0',
        '--high-priority',
        '--no-video-title-show',
        '--extraintf=http',
        '--http-host=127.0.0.1',
        f'--http-port={VLC_HTTP_PORT}',
        f'--http-password={VLC_HTTP_PASS}',
    ]
    if cfg["is_hls"]:
        extra_opts.append('--http-referrer=https://hdstream4u.com/')
    if cfg["audio_track"] > 1:
        extra_opts.append(f'--audio-track={cfg["audio_track"] - 1}')
    if cfg["sub_track"] == 'off':
        extra_opts.append('--sub-track=-1')
    elif cfg["sub_track"].isdigit():
        extra_opts.append(f'--sub-track={int(cfg["sub_track"])}')

    global _EXTERNAL_VLC_USED
    cmd = [VLC_PATH] + extra_opts + [cfg["target"]]
    _EXTERNAL_VLC_USED = True
    if sys.platform == 'win32':
        try:
            subprocess.Popen(cmd, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
            return {"success": True, "message": "External VLC launched cleanly."}
        except Exception:
            pass
    subprocess.Popen(cmd)
    return {"success": True, "message": "External VLC launched cleanly."}

def launch_inbuilt_player(body, srv_port=None):
    """Launches StreamHub's custom 0%-loss In-Built Cinema Player window powered by embedded libVLC."""
    global _INBUILT_PLAYER_PROC
    cfg, err = _prepare_play_target(body, srv_port=srv_port)
    if err:
        return err
    if not _find_libvlc_dir():
        return {"success": False, "message": "Embedded libvlc.dll not found — please check your VLC installation."}

    _stop_previous_players()
    env = os.environ.copy()
    env["STREAMHUB_PLAYER_CFG"] = json.dumps(cfg)
    if getattr(sys, 'frozen', False):
        cmd = [sys.executable, "--player-ui"]
    else:
        cmd = [sys.executable, os.path.abspath(__file__), "--player-ui"]
    kwargs = {"env": env}
    if sys.platform == 'win32':
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 1
        kwargs["startupinfo"] = si
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(cmd, **kwargs)
    with _INBUILT_PROC_LOCK:
        _INBUILT_PLAYER_PROC = proc
    return {"success": True, "pid": proc.pid, "message": "StreamHub In-Built Cinema Player opened!"}

def _run_inbuilt_player(cfg_override=None, headless_test=False):
    """StreamHub's self-contained 0% quality loss 4K/HDR/Dolby Cinema Player window."""
    import ctypes
    import tkinter as tk
    from tkinter import ttk

    cfg = cfg_override if cfg_override is not None else json.loads(os.environ.get("STREAMHUB_PLAYER_CFG", "{}"))
    target_url = cfg.get("target", "")
    if not target_url:
        return

    vlc_dir = _find_libvlc_dir()
    if not vlc_dir:
        raise RuntimeError("libvlc.dll not found")

    os.environ.setdefault("VLC_PLUGIN_PATH", os.path.join(vlc_dir, "plugins"))
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(vlc_dir)
    lib = ctypes.CDLL(os.path.join(vlc_dir, "libvlc.dll"))

    class _TrackDesc(ctypes.Structure):
        pass
    _TrackDesc._fields_ = [("i_id", ctypes.c_int), ("psz_name", ctypes.c_char_p), ("p_next", ctypes.POINTER(_TrackDesc))]

    # Configure strict 64-bit ctypes signatures
    lib.libvlc_new.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)]
    lib.libvlc_new.restype = ctypes.c_void_p
    lib.libvlc_release.argtypes = [ctypes.c_void_p]
    lib.libvlc_media_new_location.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.libvlc_media_new_location.restype = ctypes.c_void_p
    lib.libvlc_media_add_option.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.libvlc_media_release.argtypes = [ctypes.c_void_p]
    lib.libvlc_media_player_new_from_media.argtypes = [ctypes.c_void_p]
    lib.libvlc_media_player_new_from_media.restype = ctypes.c_void_p
    lib.libvlc_media_player_set_media.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.libvlc_media_player_set_hwnd.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.libvlc_media_player_play.argtypes = [ctypes.c_void_p]
    lib.libvlc_media_player_play.restype = ctypes.c_int
    lib.libvlc_media_player_set_pause.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.libvlc_media_player_stop.argtypes = [ctypes.c_void_p]
    lib.libvlc_media_player_release.argtypes = [ctypes.c_void_p]
    lib.libvlc_media_player_get_state.argtypes = [ctypes.c_void_p]
    lib.libvlc_media_player_get_state.restype = ctypes.c_int
    lib.libvlc_media_player_get_length.argtypes = [ctypes.c_void_p]
    lib.libvlc_media_player_get_length.restype = ctypes.c_int64
    lib.libvlc_media_player_get_time.argtypes = [ctypes.c_void_p]
    lib.libvlc_media_player_get_time.restype = ctypes.c_int64
    lib.libvlc_media_player_set_time.argtypes = [ctypes.c_void_p, ctypes.c_int64]
    lib.libvlc_media_player_is_seekable.argtypes = [ctypes.c_void_p]
    lib.libvlc_media_player_is_seekable.restype = ctypes.c_int
    lib.libvlc_media_player_set_rate.argtypes = [ctypes.c_void_p, ctypes.c_float]
    lib.libvlc_media_player_get_rate.argtypes = [ctypes.c_void_p]
    lib.libvlc_media_player_get_rate.restype = ctypes.c_float
    lib.libvlc_audio_get_volume.argtypes = [ctypes.c_void_p]
    lib.libvlc_audio_get_volume.restype = ctypes.c_int
    lib.libvlc_audio_set_volume.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.libvlc_video_set_mouse_input.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    lib.libvlc_video_set_key_input.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    lib.libvlc_audio_get_track_description.argtypes = [ctypes.c_void_p]
    lib.libvlc_audio_get_track_description.restype = ctypes.POINTER(_TrackDesc)
    lib.libvlc_audio_get_track.argtypes = [ctypes.c_void_p]
    lib.libvlc_audio_get_track.restype = ctypes.c_int
    lib.libvlc_audio_set_track.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.libvlc_video_get_spu_description.argtypes = [ctypes.c_void_p]
    lib.libvlc_video_get_spu_description.restype = ctypes.POINTER(_TrackDesc)
    lib.libvlc_video_get_spu.argtypes = [ctypes.c_void_p]
    lib.libvlc_video_get_spu.restype = ctypes.c_int
    lib.libvlc_video_set_spu.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.libvlc_video_set_crop_geometry.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.libvlc_video_get_crop_geometry.argtypes = [ctypes.c_void_p]
    lib.libvlc_video_get_crop_geometry.restype = ctypes.c_char_p
    lib.libvlc_video_set_aspect_ratio.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.libvlc_video_get_aspect_ratio.argtypes = [ctypes.c_void_p]
    lib.libvlc_video_get_aspect_ratio.restype = ctypes.c_char_p
    lib.libvlc_track_description_list_release.argtypes = [ctypes.POINTER(_TrackDesc)]
    lib.libvlc_audio_get_delay.argtypes = [ctypes.c_void_p]
    lib.libvlc_audio_get_delay.restype = ctypes.c_int64
    lib.libvlc_audio_set_delay.argtypes = [ctypes.c_void_p, ctypes.c_int64]
    lib.libvlc_audio_set_delay.restype = ctypes.c_int
    lib.libvlc_video_get_spu_delay.argtypes = [ctypes.c_void_p]
    lib.libvlc_video_get_spu_delay.restype = ctypes.c_int64
    lib.libvlc_video_set_spu_delay.argtypes = [ctypes.c_void_p, ctypes.c_int64]
    lib.libvlc_video_set_spu_delay.restype = ctypes.c_int
    lib.libvlc_media_player_add_slave.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    lib.libvlc_media_player_add_slave.restype = ctypes.c_int
    lib.libvlc_video_take_snapshot.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint]
    lib.libvlc_video_take_snapshot.restype = ctypes.c_int
    lib.libvlc_audio_equalizer_new.argtypes = []
    lib.libvlc_audio_equalizer_new.restype = ctypes.c_void_p
    lib.libvlc_audio_equalizer_set_preamp.argtypes = [ctypes.c_void_p, ctypes.c_float]
    lib.libvlc_audio_equalizer_set_preamp.restype = ctypes.c_int
    lib.libvlc_audio_equalizer_set_amp_at_index.argtypes = [ctypes.c_void_p, ctypes.c_float, ctypes.c_uint]
    lib.libvlc_audio_equalizer_set_amp_at_index.restype = ctypes.c_int
    lib.libvlc_media_player_set_equalizer.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.libvlc_media_player_set_equalizer.restype = ctypes.c_int
    lib.libvlc_audio_equalizer_release.argtypes = [ctypes.c_void_p]

    root = tk.Tk()
    if headless_test:
        root.withdraw()
    title_str = cfg.get("title") or "StreamHub Cinema"
    root.title(f"StreamHub Player — {title_str}")
    root.configure(bg="#05060b")
    scr_w = max(800, root.winfo_screenwidth())
    scr_h = max(600, root.winfo_screenheight())
    w = min(1240, scr_w - 60)
    h = min(740, scr_h - 80)
    x = max(0, (scr_w - w) // 2)
    y = max(0, (scr_h - h) // 2)
    root.geometry(f"{w}x{h}+{x}+{y}")
    root.minsize(720, 440)

    try:
        png_data = base64.b64encode(make_app_icon_png(64)).decode('ascii')
        icon_img = tk.PhotoImage(data=png_data)
        root.iconphoto(True, icon_img)
        root._icon_ref = icon_img
    except Exception:
        pass

    # Main layout: top title bar, center black video canvas, bottom Netflix-style control deck
    top_bar = tk.Frame(root, bg="#0b0e17", height=42)
    top_bar.pack(fill="x", side="top")
    tk.Label(top_bar, text=" S ", bg="#e50914", fg="#ffffff", font=("Segoe UI", 10, "bold"), padx=4, pady=1).pack(side="left", padx=(12, 8), pady=8)
    tk.Label(top_bar, text=title_str, bg="#0b0e17", fg="#f8fafc", font=("Segoe UI", 10, "bold"), anchor="w").pack(side="left", fill="x", expand=True)
    badge_txt = "HLS STREAM" if cfg.get("is_hls") else "0% LOSS • 4K HDR / DOLBY READY"
    tk.Label(top_bar, text=badge_txt, bg="#161b2c", fg="#38bdf8", font=("Segoe UI", 8, "bold"), padx=8, pady=3).pack(side="right", padx=(6, 12))
    osd_lbl = tk.Label(top_bar, text="", bg="#0b0e17", fg="#fde68a", font=("Segoe UI", 9, "bold"), padx=9, pady=2)
    osd_lbl.pack(side="right", padx=(4, 4))

    video_frame = tk.Frame(root, bg="#000000")
    video_frame.pack(fill="both", expand=True)

    hud_bar = tk.Frame(root, bg="#0b0e17", padx=14, pady=8)
    hud_bar.pack(fill="x", side="bottom")

    next_bar = tk.Frame(hud_bar, bg="#14192a", padx=10, pady=6)
    next_lbl = tk.Label(next_bar, text="Next Episode in 5s", bg="#14192a", fg="#fde68a", font=("Segoe UI", 9, "bold"))
    next_lbl.pack(side="left")
    next_cancel_btn = tk.Button(next_bar, text="Cancel", bg="#23293f", fg="#e2e8f0", relief="flat", bd=0, font=("Segoe UI", 8, "bold"), padx=10, pady=3, cursor="hand2")
    next_now_btn = tk.Button(next_bar, text="▶ Play Next Now", bg="#e50914", fg="#ffffff", relief="flat", bd=0, font=("Segoe UI", 8, "bold"), padx=10, pady=3, cursor="hand2")
    next_cancel_btn.pack(side="right", padx=(6, 0))
    next_now_btn.pack(side="right")

    state_lbl = tk.Label(hud_bar, text="Connecting & Buffering...", bg="#0b0e17", fg="#fbbf24", font=("Segoe UI", 8, "bold"))
    state_lbl.pack(fill="x", anchor="w", pady=(0, 3))

    # Timeline row
    tl_row = tk.Frame(hud_bar, bg="#0b0e17")
    tl_row.pack(fill="x", pady=(0, 6))
    cur_lbl = tk.Label(tl_row, text="00:00:00", bg="#0b0e17", fg="#e2e8f0", font=("Consolas", 9, "bold"), width=9)
    cur_lbl.pack(side="left")
    seek_canvas = tk.Canvas(tl_row, height=16, bg="#0b0e17", highlightthickness=0, cursor="hand2")
    seek_canvas.pack(side="left", fill="x", expand=True, padx=10)
    dur_lbl = tk.Label(tl_row, text="00:00:00", bg="#0b0e17", fg="#94a3b8", font=("Consolas", 9), width=9)
    dur_lbl.pack(side="right")

    # Primary Controls Row
    ctrl_row = tk.Frame(hud_bar, bg="#0b0e17")
    ctrl_row.pack(fill="x")

    btn_style = {"bg": "#181d2e", "fg": "#f8fafc", "activebackground": "#252c45", "activeforeground": "#ffffff", "relief": "flat", "bd": 0, "font": ("Segoe UI", 9, "bold"), "padx": 10, "pady": 5, "cursor": "hand2"}
    compact_btn_style = dict(btn_style, padx=8, pady=4, font=("Segoe UI", 8, "bold"))

    play_btn = tk.Button(ctrl_row, text="⏸ Pause", bg="#e50914", fg="#ffffff", activebackground="#b20710", activeforeground="#ffffff", relief="flat", bd=0, font=("Segoe UI", 9, "bold"), padx=14, pady=5, cursor="hand2")
    rw_btn = tk.Button(ctrl_row, text="⏪ -10s", **btn_style)
    ff_btn = tk.Button(ctrl_row, text="+10s ⏩", **btn_style)
    skip_btn = tk.Button(ctrl_row, text="Skip Intro +85s (S)", **compact_btn_style)
    rw_btn.pack(side="left", padx=(0, 5))
    play_btn.pack(side="left", padx=(0, 5))
    ff_btn.pack(side="left", padx=(0, 5))
    skip_btn.pack(side="left", padx=(0, 10))

    tk.Label(ctrl_row, text="Audio:", bg="#0b0e17", fg="#94a3b8", font=("Segoe UI", 8, "bold")).pack(side="left")
    audio_var = tk.StringVar(value="Default")
    audio_box = ttk.Combobox(ctrl_row, textvariable=audio_var, state="readonly", width=15)
    audio_box.pack(side="left", padx=(4, 8))

    tk.Label(ctrl_row, text="Sub:", bg="#0b0e17", fg="#94a3b8", font=("Segoe UI", 8, "bold")).pack(side="left")
    sub_var = tk.StringVar(value="Default")
    sub_box = ttk.Combobox(ctrl_row, textvariable=sub_var, state="readonly", width=12)
    sub_box.pack(side="left", padx=(4, 8))

    fs_btn = tk.Button(ctrl_row, text="⛶ Fullscreen", **btn_style)
    fs_btn.pack(side="right")

    vol_var = tk.IntVar(value=100)
    vol_scale = tk.Scale(ctrl_row, from_=0, to=150, orient="horizontal", variable=vol_var, showvalue=False, length=82, bg="#0b0e17", fg="#fff", troughcolor="#21273d", highlightthickness=0, bd=0, sliderrelief="flat")
    vol_lbl = tk.Label(ctrl_row, text="🔊 100%", bg="#0b0e17", fg="#cbd5e1", font=("Segoe UI", 8, "bold"), width=7)
    vol_lbl.pack(side="right", padx=(4, 8))
    vol_scale.pack(side="right", padx=(4, 2))

    # Pro Mode & Sync Controls Row (Video Crop/Ratio, Always-on-Top Mini, Night Dialogue EQ, Snapshot, Sync)
    pro_row = tk.Frame(hud_bar, bg="#0b0e17")
    pro_row.pack(fill="x", pady=(6, 0))

    crop_btn = tk.Button(pro_row, text="⊞ Crop: Default (C)", **compact_btn_style)
    crop_btn.pack(side="left", padx=(0, 5))
    ar_btn = tk.Button(pro_row, text="↔ Ratio: Auto (A)", **compact_btn_style)
    ar_btn.pack(side="left", padx=(0, 5))
    top_btn = tk.Button(pro_row, text="📌 Mini Top (T)", **compact_btn_style)
    top_btn.pack(side="left", padx=(0, 5))
    night_btn = tk.Button(pro_row, text="🌙 Night Boost (N)", **compact_btn_style)
    night_btn.pack(side="left", padx=(0, 5))
    snap_btn = tk.Button(pro_row, text="📷 Snap", **compact_btn_style)
    snap_btn.pack(side="left", padx=(0, 8))

    aud_dn_btn = tk.Button(pro_row, text="Aud -50ms (J)", **compact_btn_style)
    aud_up_btn = tk.Button(pro_row, text="Aud +50ms (K)", **compact_btn_style)
    sub_dn_btn = tk.Button(pro_row, text="Sub -50ms (G)", **compact_btn_style)
    sub_up_btn = tk.Button(pro_row, text="Sub +50ms (H)", **compact_btn_style)
    aud_dn_btn.pack(side="left", padx=(0, 3))
    aud_up_btn.pack(side="left", padx=(0, 7))
    sub_dn_btn.pack(side="left", padx=(0, 3))
    sub_up_btn.pack(side="left", padx=(0, 7))

    rate_var = tk.StringVar(value="1.0x")
    rate_box = ttk.Combobox(pro_row, textvariable=rate_var, values=("1.0x", "1.25x", "1.5x", "2.0x"), state="readonly", width=5)
    rate_box.pack(side="right")
    tk.Label(pro_row, text="Speed:", bg="#0b0e17", fg="#94a3b8", font=("Segoe UI", 8, "bold")).pack(side="right", padx=(0, 4))

    root.update_idletasks()

    vlc_args = [b"--no-video-title-show", b"--quiet", b"--http-reconnect", b"--avcodec-hw=any", b"--file-caching=1500", b"--network-caching=2000"]
    if headless_test:
        vlc_args.extend([b"--vout=dummy", b"--aout=dummy"])
    c_args = (ctypes.c_char_p * len(vlc_args))(*vlc_args)
    inst = lib.libvlc_new(len(vlc_args), c_args)
    med = lib.libvlc_media_new_location(inst, target_url.encode('utf-8'))
    if cfg.get("is_hls"):
        lib.libvlc_media_add_option(med, b":http-referrer=https://hdstream4u.com/")
        intra_s = float(cfg.get("hls_intra_sec", 0) or 0)
        if intra_s >= 0.5:
            lib.libvlc_media_add_option(med, f":start-time={intra_s:.2f}".encode('ascii'))
    mp = lib.libvlc_media_player_new_from_media(med)
    lib.libvlc_media_release(med)
    lib.libvlc_media_player_set_hwnd(mp, ctypes.c_void_p(video_frame.winfo_id()))
    lib.libvlc_video_set_mouse_input(mp, 0)
    lib.libvlc_video_set_key_input(mp, 0)

    # Night Dialogue Boost 10-band Equalizer (reduces bass rumble, lifts speech 500Hz-4kHz)
    night_eq = lib.libvlc_audio_equalizer_new()
    if night_eq:
        lib.libvlc_audio_equalizer_set_preamp(night_eq, 3.5)
        for _bi, _bv in enumerate((-5.0, -4.0, -2.0, 1.5, 4.0, 6.5, 7.0, 5.0, 1.5, -1.0)):
            lib.libvlc_audio_equalizer_set_amp_at_index(night_eq, _bv, _bi)

    import math
    _scr_w = max(640, root.winfo_screenwidth())
    _scr_h = max(360, root.winfo_screenheight())
    _g = math.gcd(_scr_w, _scr_h)
    _fit_geo = f"{_scr_w // _g}:{_scr_h // _g}".encode('ascii')
    crop_presets = [("Default", None), (f"Fit ({_fit_geo.decode()})", _fit_geo)]
    for _lbl, _val in [
        ("16:9", b"16:9"), ("16:10", b"16:10"), ("4:3", b"4:3"),
        ("1.85:1", b"185:100"), ("2.21:1", b"221:100"),
        ("2.35:1", b"235:100"), ("2.39:1", b"239:100"),
        ("5:3", b"5:3"), ("1:1", b"1:1")
    ]:
        if _val != _fit_geo:
            crop_presets.append((_lbl, _val))

    ar_presets = [
        ("Auto", None), ("16:9", b"16:9"), ("16:10", b"16:10"), ("4:3", b"4:3"),
        ("2.35:1", b"235:100"), ("2.21:1", b"221:100"), ("1.85:1", b"185:100"),
        ("5:4", b"5:4"), ("1:1", b"1:1")
    ]

    player_state = {
        "fullscreen": False,
        "hud_visible": True,
        "last_move": time.time(),
        "audio_map": {},
        "sub_map": {},
        "tracks_applied": False,
        "start_applied": bool(cfg.get("is_hls") and int(cfg.get("hls_base_ms", 0) or 0) > 0),
        "initial_sub_loaded": False,
        "closed": False,
        "drag_pct": None,
        "muted": False,
        "pre_mute_vol": 100,
        "crop_idx": 0,
        "crop_label": "Default",
        "ar_idx": 0,
        "ar_label": "Auto",
        "osd_until": 0.0,
        "audiodelay_ms": 0,
        "subdelay_ms": 0,
        "night_boost": False,
        "topmost": False,
        "saved_geo": None,
        "snapshot_path": "",
        "last_prog_ts": 0.0,
        "last_prog_pos": -1,
        "next_countdown_started": None,
        "next_cancelled": False,
        "next_triggered": False,
        "is_hls": bool(cfg.get("is_hls")),
        "cfg_seekable": bool(cfg.get("seekable", True)),
        "hls_sid": str(cfg.get("hls_sid", "") or ""),
        "hls_raw_url": str(cfg.get("hls_raw_url", "") or ""),
        "hls_srv_origin": str(cfg.get("server_origin", "") or ""),
        "hls_base_ms": int(cfg.get("hls_base_ms", 0) or 0),
        "hls_total_ms": int(cfg.get("hls_total_ms", 0) or 0),
        "hls_seeking": False,
        "hls_seek_gen": 0,
        "pending_seek_ms": None,
        "seek_debounce_id": None,
        "last_seek_ts": 0.0,
        "last_seek_tgt_ms": None,
        "prev_cur_ms": -1,
        "stall_since": 0.0,
        "stall_retries": 0,
    }

    def _abs_tot_ms():
        if player_state["is_hls"] and player_state["hls_total_ms"] > 0:
            return player_state["hls_total_ms"]
        return max(0, lib.libvlc_media_player_get_length(mp))

    def _abs_cur_ms():
        if player_state["is_hls"]:
            if player_state["hls_seeking"] and player_state["pending_seek_ms"] is not None:
                return max(0, int(player_state["pending_seek_ms"]))
            raw_t = lib.libvlc_media_player_get_time(mp)
            return max(0, player_state["hls_base_ms"] + max(0, raw_t))
        return max(0, lib.libvlc_media_player_get_time(mp))

    def _fmt_ms(ms):
        sec = max(0, int((ms or 0) // 1000))
        return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"

    def _read_desc(fn):
        ptr = fn(mp)
        out = []
        cur = ptr
        while cur:
            name = (cur.contents.psz_name or b'Track').decode('utf-8', 'replace')
            out.append((int(cur.contents.i_id), name))
            cur = cur.contents.p_next
        if ptr:
            lib.libvlc_track_description_list_release(ptr)
        return out

    def _report_progress(sync=False, force=False):
        srv_origin = cfg.get("server_origin") or ""
        m_key = cfg.get("media_key") or ""
        if not srv_origin or not m_key:
            return
        cur_s = max(0, _abs_cur_ms() // 1000)
        tot_s = max(0, _abs_tot_ms() // 1000)
        if not force and cur_s < 5:
            return
        if not force and abs(cur_s - player_state["last_prog_pos"]) < 3:
            return
        player_state["last_prog_pos"] = cur_s
        player_state["last_prog_ts"] = time.time()
        body = json.dumps({
            "key": m_key,
            "parent_key": cfg.get("parent_key") or "",
            "ep_label": cfg.get("ep_label") or "",
            "title": title_str,
            "position_sec": cur_s,
            "duration_sec": tot_s,
        }).encode('utf-8')
        def _send():
            try:
                req = urllib.request.Request(
                    f"{srv_origin}/api/progress",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=1.0):
                    pass
            except Exception:
                pass
        if sync:
            _send()
        else:
            threading.Thread(target=_send, daemon=True).start()

    def toggle_pause(_=None):
        st = lib.libvlc_media_player_get_state(mp)
        lib.libvlc_media_player_set_pause(mp, 0 if st == 4 else 1)

    def _apply_hls_slice(gen, sl):
        if player_state["closed"] or gen != player_state["hls_seek_gen"]:
            return
        pl_url = (sl or {}).get("playlist_url") or ""
        if not pl_url:
            player_state["hls_seeking"] = False
            player_state["pending_seek_ms"] = None
            return
        if sl.get("total_ms"):
            player_state["hls_total_ms"] = int(sl["total_ms"])
        player_state["hls_base_ms"] = int(sl.get("seg_start_ms", 0) or 0)
        intra_s = float(sl.get("intra_sec", 0.0) or 0.0)
        lib.libvlc_media_player_stop(mp)
        med2 = lib.libvlc_media_new_location(inst, pl_url.encode('utf-8'))
        lib.libvlc_media_add_option(med2, b":http-referrer=https://hdstream4u.com/")
        if intra_s >= 0.5:
            lib.libvlc_media_add_option(med2, f":start-time={intra_s:.2f}".encode('ascii'))
        lib.libvlc_media_player_set_media(mp, med2)
        lib.libvlc_media_release(med2)
        player_state["audio_map"] = {}
        player_state["sub_map"] = {}
        player_state["tracks_applied"] = False
        player_state["initial_sub_loaded"] = False
        player_state["hls_needs_reapply"] = True
        lib.libvlc_media_player_play(mp)

    def _is_nonseekable_active():
        return (not player_state["is_hls"]) and (not player_state["cfg_seekable"])

    def _execute_seek_ms(tgt_ms):
        if _is_nonseekable_active():
            player_state["drag_pct"] = None
            player_state["pending_seek_ms"] = None
            _flash_osd("Sequential CDN only — select ⚡ HLS/R2 to seek")
            return
        tot = _abs_tot_ms()
        tgt_ms = max(0, int(tgt_ms))
        if tot > 0:
            tgt_ms = min(max(0, tot - 500), tgt_ms)
        player_state["drag_pct"] = None
        player_state["last_seek_ts"] = time.time()
        player_state["last_seek_tgt_ms"] = tgt_ms
        player_state["stall_since"] = 0.0
        player_state["stall_retries"] = 0

        if player_state["is_hls"] and player_state["hls_srv_origin"] and (player_state["hls_sid"] or player_state["hls_raw_url"]):
            player_state["hls_seek_gen"] += 1
            gen = player_state["hls_seek_gen"]
            player_state["hls_seeking"] = True
            player_state["pending_seek_ms"] = tgt_ms
            _flash_osd(f"Seeking -> {_fmt_ms(tgt_ms)}")

            def _fetch_slice():
                try:
                    q_ident = (
                        f"sid={urllib.parse.quote(player_state['hls_sid'], safe='')}"
                        if player_state["hls_sid"]
                        else f"url={urllib.parse.quote(player_state['hls_raw_url'], safe='')}"
                    )
                    u = f"{player_state['hls_srv_origin']}/api/hls/slice_target?{q_ident}&t_ms={tgt_ms}"
                    req = urllib.request.Request(u)
                    with urllib.request.urlopen(req, timeout=8) as r:
                        sl = json.loads(r.read().decode('utf-8'))
                    if not player_state["closed"] and gen == player_state["hls_seek_gen"]:
                        root.after(0, lambda s=sl, g=gen: _apply_hls_slice(g, s))
                except Exception:
                    if not player_state["closed"] and gen == player_state["hls_seek_gen"]:
                        player_state["hls_seeking"] = False
                        player_state["pending_seek_ms"] = None
            threading.Thread(target=_fetch_slice, daemon=True).start()
            return

        player_state["pending_seek_ms"] = None
        lib.libvlc_media_player_set_time(mp, int(tgt_ms))

    def _flush_pending_seek():
        player_state["seek_debounce_id"] = None
        tgt = player_state.get("pending_seek_ms")
        if tgt is not None and not player_state["closed"]:
            _execute_seek_ms(tgt)

    def _schedule_seek_ms(tgt_ms, immediate=False):
        if _is_nonseekable_active():
            player_state["drag_pct"] = None
            player_state["pending_seek_ms"] = None
            _flash_osd("Sequential CDN only — select ⚡ HLS/R2 to seek")
            return
        tot = _abs_tot_ms()
        tgt_ms = max(0, int(tgt_ms))
        if tot > 0:
            tgt_ms = min(max(0, tot - 500), tgt_ms)
        prev_id = player_state.get("seek_debounce_id")
        if prev_id:
            try:
                root.after_cancel(prev_id)
            except Exception:
                pass
            player_state["seek_debounce_id"] = None
        player_state["pending_seek_ms"] = tgt_ms
        if tot > 0:
            player_state["drag_pct"] = max(0.0, min(1.0, tgt_ms / float(tot)))
        _flash_osd(f"Seek -> {_fmt_ms(tgt_ms)}")
        if immediate:
            _execute_seek_ms(tgt_ms)
        else:
            player_state["seek_debounce_id"] = root.after(140, _flush_pending_seek)

    def seek_rel(delta_sec, immediate=False):
        if player_state.get("seek_debounce_id") and player_state.get("pending_seek_ms") is not None:
            base = int(player_state["pending_seek_ms"])
        else:
            base = _abs_cur_ms()
        nxt = max(0, base + int(delta_sec) * 1000)
        _schedule_seek_ms(nxt, immediate=immediate)

    def skip_intro(_=None):
        seek_rel(85, immediate=True)
        _flash_osd("Intro Skipped (+85s)")

    def toggle_fullscreen(_=None):
        if player_state["topmost"]:
            toggle_topmost()
        player_state["fullscreen"] = not player_state["fullscreen"]
        root.attributes("-fullscreen", player_state["fullscreen"])
        fs_btn.config(text="⮌ Exit Full" if player_state["fullscreen"] else "⛶ Fullscreen")
        _show_hud()

    def exit_fullscreen(_=None):
        if player_state["fullscreen"]:
            toggle_fullscreen()

    def _on_audio_pick(_=None):
        tid = player_state["audio_map"].get(audio_var.get())
        if tid is not None:
            lib.libvlc_audio_set_track(mp, tid)

    def _on_sub_pick(_=None):
        sid_num = player_state["sub_map"].get(sub_var.get())
        if sid_num is not None:
            lib.libvlc_video_set_spu(mp, sid_num)

    def _on_rate_pick(_=None):
        try:
            r = float(rate_var.get().replace("x", ""))
            lib.libvlc_media_player_set_rate(mp, r)
        except Exception:
            pass

    def _on_vol_change(_=None):
        v = int(vol_var.get())
        player_state["muted"] = (v == 0)
        vol_lbl.config(text=f"{'🔇' if v == 0 else '🔊'} {v}%")
        lib.libvlc_audio_set_volume(mp, v)

    def _toggle_mute(_=None):
        if player_state["muted"]:
            vol_var.set(player_state["pre_mute_vol"] or 100)
        else:
            player_state["pre_mute_vol"] = max(20, vol_var.get())
            vol_var.set(0)
        _on_vol_change()

    def _seek_from_x(ev_x):
        tot = _abs_tot_ms()
        w_canv = max(1, seek_canvas.winfo_width())
        pct = max(0.0, min(1.0, ev_x / float(w_canv)))
        if tot > 0:
            _schedule_seek_ms(int(pct * tot), immediate=True)
        else:
            player_state["drag_pct"] = None

    def _show_hud(_=None):
        player_state["last_move"] = time.time()
        if not player_state["hud_visible"]:
            top_bar.pack(fill="x", side="top", before=video_frame)
            hud_bar.pack(fill="x", side="bottom", after=video_frame)
            player_state["hud_visible"] = True

    def _flash_osd(msg):
        osd_lbl.config(text=f"  {msg}  ", bg="#1e293b", fg="#fde68a")
        player_state["osd_until"] = time.time() + 2.6
        _show_hud()

    def cycle_crop(_=None):
        player_state["crop_idx"] = (player_state["crop_idx"] + 1) % len(crop_presets)
        lbl, geo = crop_presets[player_state["crop_idx"]]
        player_state["crop_label"] = lbl
        lib.libvlc_video_set_crop_geometry(mp, geo)
        crop_btn.config(text=f"⊞ Crop: {lbl} (C)")
        _flash_osd(f"Crop / Fit: {lbl}")

    def cycle_aspect(_=None):
        player_state["ar_idx"] = (player_state["ar_idx"] + 1) % len(ar_presets)
        lbl, ar = ar_presets[player_state["ar_idx"]]
        player_state["ar_label"] = lbl
        lib.libvlc_video_set_aspect_ratio(mp, ar)
        ar_btn.config(text=f"↔ Ratio: {lbl} (A)")
        _flash_osd(f"Aspect Ratio: {lbl}")

    def nudge_audio_delay(delta_ms=0, abs_ms=None):
        cur_ms = int(lib.libvlc_audio_get_delay(mp) // 1000)
        target_ms = int(abs_ms) if abs_ms is not None else (cur_ms + int(delta_ms))
        target_ms = max(-10000, min(10000, target_ms))
        lib.libvlc_audio_set_delay(mp, target_ms * 1000)
        player_state["audiodelay_ms"] = target_ms
        _flash_osd(f"Audio Sync: {target_ms:+d} ms")

    def nudge_sub_delay(delta_ms=0, abs_ms=None):
        cur_ms = int(lib.libvlc_video_get_spu_delay(mp) // 1000)
        target_ms = int(abs_ms) if abs_ms is not None else (cur_ms + int(delta_ms))
        target_ms = max(-10000, min(10000, target_ms))
        lib.libvlc_video_set_spu_delay(mp, target_ms * 1000)
        player_state["subdelay_ms"] = target_ms
        _flash_osd(f"Sub Sync: {target_ms:+d} ms")

    def toggle_night_boost(_=None):
        if not night_eq:
            return
        player_state["night_boost"] = not player_state["night_boost"]
        on = player_state["night_boost"]
        lib.libvlc_media_player_set_equalizer(mp, night_eq if on else None)
        night_btn.config(
            text="🌙 Night EQ ON (N)" if on else "🌙 Night Boost (N)",
            bg="#6d28d9" if on else "#181d2e",
        )
        _flash_osd("Night Dialogue Boost: ON" if on else "Night Dialogue Boost: OFF")

    def toggle_topmost(_=None):
        if player_state["fullscreen"]:
            player_state["fullscreen"] = False
            root.attributes("-fullscreen", False)
            fs_btn.config(text="⛶ Fullscreen")
        player_state["topmost"] = not player_state["topmost"]
        on = player_state["topmost"]
        if on:
            player_state["saved_geo"] = root.geometry()
            root.minsize(380, 220)
            mw, mh = 460, 285
            mx = max(12, scr_w - mw - 24)
            my = max(12, scr_h - mh - 56)
            root.attributes("-topmost", True)
            root.geometry(f"{mw}x{mh}+{mx}+{my}")
            pro_row.pack_forget()
            top_btn.config(text="📌 Exit Mini (T)", bg="#0284c7")
            _flash_osd("Mini Always-on-Top ON")
        else:
            root.attributes("-topmost", False)
            root.minsize(720, 440)
            pro_row.pack(fill="x", pady=(6, 0))
            if player_state["saved_geo"]:
                root.geometry(player_state["saved_geo"])
            top_btn.config(text="📌 Mini Top (T)", bg="#181d2e")
            _flash_osd("Normal Window Restored")

    def take_snapshot(_=None):
        snap_dir = _SNAPSHOT_DIR
        try:
            os.makedirs(snap_dir, exist_ok=True)
        except Exception:
            snap_dir = tempfile.gettempdir()
        safe_name = re.sub(r'[^a-zA-Z0-9_-]+', '_', title_str).strip('_')[:36] or "StreamHub"
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(snap_dir, f"{safe_name}_{stamp}.png")
        rc = lib.libvlc_video_take_snapshot(mp, 0, out_path.encode('utf-8'), 0, 0)
        if rc == 0 or os.path.exists(out_path):
            player_state["snapshot_path"] = out_path
            _flash_osd(f"Snapshot saved: {os.path.basename(out_path)}")
            return out_path
        _flash_osd("Snapshot capture is not ready yet — wait for playback to start")
        return ""

    def _load_sub_file(path):
        try:
            p = str(path or '').strip()
            if not p or not os.path.exists(p):
                return False
            uri = pathlib.Path(p).resolve().as_uri().encode('utf-8')
            rc = lib.libvlc_media_player_add_slave(mp, 0, uri, 1)
            if rc == 0:
                _flash_osd("External .srt subtitle loaded!")
                def _refresh_sub():
                    if player_state["closed"]:
                        return
                    s_list = _read_desc(lib.libvlc_video_get_spu_description)
                    if s_list:
                        player_state["sub_map"] = {nm: sid_n for sid_n, nm in s_list}
                        sub_box["values"] = [nm for _, nm in s_list]
                        cur_s = lib.libvlc_video_get_spu(mp)
                        sub_var.set(next((nm for sid_n, nm in s_list if sid_n == cur_s), s_list[-1][1]))
                root.after(450, _refresh_sub)
                return True
        except Exception:
            pass
        return False

    def _cancel_next(_=None):
        player_state["next_cancelled"] = True
        next_bar.pack_forget()

    def _trigger_next_episode(_=None):
        nxt_ep = cfg.get("next_episode")
        srv_origin = cfg.get("server_origin") or ""
        if not nxt_ep or not srv_origin or player_state["next_triggered"]:
            return
        player_state["next_triggered"] = True
        next_lbl.config(text="Loading Next Episode...")
        _report_progress(sync=True, force=True)
        payload = json.dumps(nxt_ep).encode('utf-8')
        def _go():
            try:
                req = urllib.request.Request(
                    f"{srv_origin}/api/play_next_episode",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=14):
                    pass
            except Exception:
                pass
            if not player_state["closed"]:
                root.after(150, _shutdown)
        threading.Thread(target=_go, daemon=True).start()

    play_btn.config(command=toggle_pause)
    rw_btn.config(command=lambda: seek_rel(-10))
    ff_btn.config(command=lambda: seek_rel(10))
    skip_btn.config(command=skip_intro)
    fs_btn.config(command=toggle_fullscreen)
    crop_btn.config(command=cycle_crop)
    ar_btn.config(command=cycle_aspect)
    top_btn.config(command=toggle_topmost)
    night_btn.config(command=toggle_night_boost)
    snap_btn.config(command=take_snapshot)
    aud_dn_btn.config(command=lambda: nudge_audio_delay(-50))
    aud_up_btn.config(command=lambda: nudge_audio_delay(50))
    sub_dn_btn.config(command=lambda: nudge_sub_delay(-50))
    sub_up_btn.config(command=lambda: nudge_sub_delay(50))
    next_cancel_btn.config(command=_cancel_next)
    next_now_btn.config(command=_trigger_next_episode)

    audio_box.bind("<<ComboboxSelected>>", _on_audio_pick)
    sub_box.bind("<<ComboboxSelected>>", _on_sub_pick)
    rate_box.bind("<<ComboboxSelected>>", _on_rate_pick)
    vol_scale.config(command=_on_vol_change)
    seek_canvas.bind("<Button-1>", lambda e: player_state.__setitem__("drag_pct", max(0.0, min(1.0, e.x / max(1.0, seek_canvas.winfo_width())))))
    seek_canvas.bind("<B1-Motion>", lambda e: player_state.__setitem__("drag_pct", max(0.0, min(1.0, e.x / max(1.0, seek_canvas.winfo_width())))))
    seek_canvas.bind("<ButtonRelease-1>", lambda e: _seek_from_x(e.x))

    video_frame.bind("<Double-Button-1>", toggle_fullscreen)
    video_frame.bind("<Motion>", _show_hud)
    root.bind("<Motion>", _show_hud)
    root.bind("<Left>", lambda e: seek_rel(-10))
    root.bind("<Right>", lambda e: seek_rel(10))
    root.bind("<Escape>", exit_fullscreen)

    def _on_keypress(ev):
        ks = (ev.keysym or '').lower()
        shift = bool(ev.state & 0x0001)
        if ks == 'space':
            toggle_pause()
        elif ks == 's':
            if shift:
                take_snapshot()
            else:
                skip_intro()
        elif ks == 'p':
            take_snapshot()
        elif ks == 't':
            toggle_topmost()
        elif ks == 'n':
            toggle_night_boost()
        elif ks == 'f':
            toggle_fullscreen()
        elif ks == 'm':
            _toggle_mute()
        elif ks == 'c':
            cycle_crop()
        elif ks == 'a':
            cycle_aspect()
        elif ks == 'j':
            nudge_audio_delay(-50)
        elif ks == 'k':
            nudge_audio_delay(50)
        elif ks == 'g':
            nudge_sub_delay(-50)
        elif ks == 'h':
            nudge_sub_delay(50)
    root.bind("<KeyPress>", _on_keypress)

    # Embedded HTTP status/command bridge so Phone Remote works with In-Built Player too!
    remote_queue = []
    remote_lock = threading.Lock()

    class _PlayerRemoteHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            p = urllib.parse.urlparse(self.path)
            if p.path != '/requests/status.json':
                self.send_response(404); self.end_headers(); return
            qs = urllib.parse.parse_qs(p.query)
            cmd = qs.get('command', [''])[0]
            val = qs.get('val', [''])[0]
            if cmd:
                done_ev = threading.Event()
                with remote_lock:
                    remote_queue.append((cmd, val, done_ev))
                if cmd == 'pl_stop' or player_state["closed"]:
                    payload = json.dumps({"state": "stopped", "time": 0, "length": 0, "volume": 256, "fullscreen": False, "rate": 1.0, "information": {"category": {"meta": {"filename": title_str}}}}).encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                done_ev.wait(timeout=1.8 if cmd in ('snapshot', 'load_sub') else 0.55)
                time.sleep(0.05)
            if player_state["closed"]:
                return
            st_code = lib.libvlc_media_player_get_state(mp)
            st_str = {3: 'playing', 4: 'paused', 5: 'stopped', 6: 'ended'}.get(st_code, 'opening')
            t_sec = max(0, _abs_cur_ms() // 1000)
            l_sec = max(0, _abs_tot_ms() // 1000)
            vol_vlc = int(round(max(0, lib.libvlc_audio_get_volume(mp)) * 256 / 100))
            cats = {"meta": {"filename": title_str}}
            cur_a = lib.libvlc_audio_get_track(mp)
            cur_s = lib.libvlc_video_get_spu(mp)
            for nm, tid in player_state["audio_map"].items():
                if tid != -1:
                    cats[f"Stream {tid}"] = {"Type": "Audio", "Language": nm + (" *" if tid == cur_a else "")}
            for nm, sid_n in player_state["sub_map"].items():
                key_s = f"Stream {sid_n}"
                if key_s not in cats:
                    cats[key_s] = {"Type": "Subtitle", "Language": nm + (" *" if sid_n == cur_s else "")}
            payload = json.dumps({
                "state": st_str, "time": t_sec, "length": l_sec,
                "seekable": bool(player_state["is_hls"] or player_state["cfg_seekable"]),
                "volume": vol_vlc, "fullscreen": player_state["fullscreen"],
                "rate": round(lib.libvlc_media_player_get_rate(mp), 2),
                "crop": player_state["crop_label"],
                "aspect_ratio": player_state["ar_label"],
                "audiodelay_ms": player_state["audiodelay_ms"],
                "subdelay_ms": player_state["subdelay_ms"],
                "night_boost": player_state["night_boost"],
                "topmost": player_state["topmost"],
                "snapshot_path": player_state["snapshot_path"],
                "information": {"category": cats}
            }).encode('utf-8')
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    remote_srv = None
    try:
        remote_srv = ThreadedTCPServer(('127.0.0.1', VLC_HTTP_PORT), _PlayerRemoteHandler)
        threading.Thread(target=remote_srv.serve_forever, daemon=True).start()
    except Exception:
        remote_srv = None

    def _shutdown():
        if player_state["closed"]:
            return
        try:
            cur_on_exit = max(0, _abs_cur_ms() // 1000)
            if cur_on_exit >= 5:
                _report_progress(sync=True, force=True)
        except Exception:
            pass
        player_state["closed"] = True
        try:
            if remote_srv:
                remote_srv.shutdown()
                remote_srv.server_close()
        except Exception:
            pass
        try:
            if night_eq:
                lib.libvlc_media_player_set_equalizer(mp, None)
                lib.libvlc_audio_equalizer_release(night_eq)
        except Exception:
            pass
        try:
            lib.libvlc_media_player_stop(mp)
            lib.libvlc_media_player_release(mp)
            lib.libvlc_release(inst)
        except Exception:
            pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _shutdown)
    lib.libvlc_media_player_play(mp)

    def _process_remote_cmds():
        if player_state["closed"]:
            return
        with remote_lock:
            cmds = list(remote_queue)
            remote_queue.clear()
        for cmd, val, done_ev in cmds:
            try:
                if cmd == 'pl_pause':
                    toggle_pause()
                elif cmd == 'pl_play':
                    lib.libvlc_media_player_play(mp)
                elif cmd == 'pl_stop':
                    done_ev.set()
                    _shutdown()
                    return
                elif cmd == 'fullscreen':
                    toggle_fullscreen()
                elif cmd == 'crop_cycle':
                    cycle_crop()
                elif cmd == 'aspect_cycle':
                    cycle_aspect()
                elif cmd == 'night_boost':
                    toggle_night_boost()
                elif cmd == 'topmost':
                    toggle_topmost()
                elif cmd == 'snapshot':
                    take_snapshot()
                elif cmd == 'skip_intro':
                    skip_intro()
                elif cmd == 'next_ep':
                    _trigger_next_episode()
                elif cmd == 'load_sub':
                    _load_sub_file(val)
                elif cmd == 'audiodelay':
                    try:
                        s_v = str(val or '0').strip()
                        if s_v.startswith(('+', '-')):
                            nudge_audio_delay(int(float(s_v)))
                        else:
                            nudge_audio_delay(abs_ms=int(float(s_v)))
                    except Exception:
                        pass
                elif cmd == 'subdelay':
                    try:
                        s_v = str(val or '0').strip()
                        if s_v.startswith(('+', '-')):
                            nudge_sub_delay(int(float(s_v)))
                        else:
                            nudge_sub_delay(abs_ms=int(float(s_v)))
                    except Exception:
                        pass
                elif cmd == 'rate':
                    try:
                        rf = float(val)
                        lib.libvlc_media_player_set_rate(mp, rf)
                        rate_var.set(f"{rf:.2g}x")
                    except Exception:
                        pass
                elif cmd == 'audio_track':
                    try:
                        tid = int(val)
                        lib.libvlc_audio_set_track(mp, tid)
                        for nm, k_id in player_state["audio_map"].items():
                            if k_id == tid:
                                audio_var.set(nm)
                                break
                    except Exception:
                        pass
                elif cmd == 'subtitle_track':
                    try:
                        sid_n = int(val)
                        lib.libvlc_video_set_spu(mp, sid_n)
                        for nm, k_id in player_state["sub_map"].items():
                            if k_id == sid_n:
                                sub_var.set(nm)
                                break
                    except Exception:
                        pass
                elif cmd == 'volume':
                    try:
                        pct = max(0, min(150, round(int(val) * 100 / 256)))
                        vol_var.set(pct); _on_vol_change()
                    except Exception:
                        pass
                elif cmd == 'seek':
                    if str(val).startswith(('+', '-')):
                        seek_rel(int(val), immediate=True)
                    elif str(val).isdigit():
                        _execute_seek_ms(int(val) * 1000)
            finally:
                done_ev.set()
        if not player_state["closed"]:
            root.after(45, _process_remote_cmds)

    def _tick():
        if player_state["closed"]:
            return

        st = lib.libvlc_media_player_get_state(mp)
        lib_cur = lib.libvlc_media_player_get_time(mp)
        tot = _abs_tot_ms()
        cur = _abs_cur_ms()
        play_btn.config(text="▶ Play" if st == 4 else "⏸ Pause")

        if st == 3:
            if player_state["hls_seeking"] and lib_cur >= 200:
                player_state["hls_seeking"] = False
                player_state["pending_seek_ms"] = None
                if player_state.get("hls_needs_reapply"):
                    player_state["hls_needs_reapply"] = False
                    _on_rate_pick()
                    _on_vol_change()
                    if player_state["crop_idx"] > 0:
                        lib.libvlc_video_set_crop_geometry(mp, crop_presets[player_state["crop_idx"]][1])
                    if player_state["ar_idx"] > 0:
                        lib.libvlc_video_set_aspect_ratio(mp, ar_presets[player_state["ar_idx"]][1])
                    if player_state["audiodelay_ms"]:
                        lib.libvlc_audio_set_delay(mp, int(player_state["audiodelay_ms"]) * 1000)
                    if player_state["subdelay_ms"]:
                        lib.libvlc_video_set_spu_delay(mp, int(player_state["subdelay_ms"]) * 1000)
                    if night_eq and player_state["night_boost"]:
                        lib.libvlc_media_player_set_equalizer(mp, night_eq)

            if cur > 0 and not player_state["hls_seeking"]:
                if not player_state["is_hls"] and not player_state["cfg_seekable"]:
                    state_lbl.config(text="Playing Sequential (Google CDN — Select ⚡ HLS/R2 for Seek)", fg="#fbbf24")
                else:
                    state_lbl.config(text="Playing (0% Quality Loss • Seekbar Active)", fg="#6ee7b7")
            else:
                state_lbl.config(text="Buffering stream...", fg="#fbbf24")
            if not player_state["audio_map"]:
                a_list = _read_desc(lib.libvlc_audio_get_track_description)
                s_list = _read_desc(lib.libvlc_video_get_spu_description)
                if a_list:
                    player_state["audio_map"] = {nm: tid for tid, nm in a_list}
                    audio_box["values"] = [nm for _, nm in a_list]
                    want = int(cfg.get("audio_track", 1) or 1)
                    non_dis = [(tid, nm) for tid, nm in a_list if tid != -1]
                    if want > 1 and len(non_dis) >= want and not player_state["tracks_applied"]:
                        tid, nm = non_dis[want - 1]
                        lib.libvlc_audio_set_track(mp, tid)
                        audio_var.set(nm)
                    else:
                        cur_a = lib.libvlc_audio_get_track(mp)
                        audio_var.set(next((nm for tid, nm in a_list if tid == cur_a), a_list[0][1]))
                if s_list:
                    player_state["sub_map"] = {nm: sid_n for sid_n, nm in s_list}
                    sub_box["values"] = [nm for _, nm in s_list]
                    want_s = str(cfg.get("sub_track", "auto") or "auto")
                    if want_s == 'off':
                        lib.libvlc_video_set_spu(mp, -1)
                        sub_var.set(next((nm for sid_n, nm in s_list if sid_n == -1), s_list[0][1]))
                    elif want_s.isdigit():
                        non_dis_s = [(sid_n, nm) for sid_n, nm in s_list if sid_n != -1]
                        idx_s = int(want_s)
                        if idx_s < len(non_dis_s) and not player_state["tracks_applied"]:
                            lib.libvlc_video_set_spu(mp, non_dis_s[idx_s][0])
                            sub_var.set(non_dis_s[idx_s][1])
                    else:
                        cur_s = lib.libvlc_video_get_spu(mp)
                        sub_var.set(next((nm for sid_n, nm in s_list if sid_n == cur_s), s_list[0][1]))
                if cfg.get("subtitle_file") and not player_state["initial_sub_loaded"]:
                    player_state["initial_sub_loaded"] = True
                    _load_sub_file(cfg.get("subtitle_file"))
                player_state["tracks_applied"] = True

            st_start = int(cfg.get("start_time", 0) or 0)
            if st_start > 0 and not player_state["start_applied"] and tot > 0 and lib_cur >= 300:
                player_state["start_applied"] = True
                _execute_seek_ms(st_start * 1000)

            # Auto-recovery watchdog if a non-HLS HTTP seek stalls with unchanged timestamp
            if not player_state["is_hls"] and lib_cur > 0:
                if player_state["last_seek_ts"] > 0 and (time.time() - player_state["last_seek_ts"]) < 16.0:
                    if lib_cur == player_state["prev_cur_ms"]:
                        if not player_state["stall_since"]:
                            player_state["stall_since"] = time.time()
                        elif (time.time() - player_state["stall_since"]) >= 5.5 and player_state["stall_retries"] < 2:
                            player_state["stall_retries"] += 1
                            player_state["stall_since"] = time.time()
                            lib.libvlc_media_player_set_time(mp, int(player_state["last_seek_tgt_ms"] or lib_cur))
                            lib.libvlc_media_player_set_pause(mp, 0)
                            _flash_osd("Re-syncing stream after seek...")
                    else:
                        player_state["stall_since"] = 0.0
                else:
                    player_state["stall_since"] = 0.0
                player_state["prev_cur_ms"] = lib_cur

            if time.time() - player_state["last_prog_ts"] >= 5.0:
                _report_progress()
        elif st == 4:
            state_lbl.config(text="Paused", fg="#fde68a")
        elif st == 7:
            state_lbl.config(text="Stream error — try switching mirror or External VLC", fg="#fca5a5")
        else:
            state_lbl.config(text="Connecting & Buffering stream...", fg="#fbbf24")

        # Binge Auto-Next Episode Countdown near end of episode
        nxt_ep = cfg.get("next_episode")
        near_end = bool(
            nxt_ep and not player_state["next_cancelled"] and not player_state["hls_seeking"]
            and ((st == 6) or (tot > 90000 and tot - cur <= 15000))
        )
        if near_end:
            if player_state["next_countdown_started"] is None:
                player_state["next_countdown_started"] = time.time()
                next_bar.pack(fill="x", before=state_lbl, pady=(0, 6))
            rem = max(0, 5 - int(time.time() - player_state["next_countdown_started"]))
            nxt_name = str(nxt_ep.get("title") or nxt_ep.get("ep_label") or "Next Episode")[:42]
            next_lbl.config(text=f"Up Next: {nxt_name} in {rem}s...")
            if rem <= 0 and not player_state["next_triggered"]:
                _trigger_next_episode()
                return
        elif player_state["next_countdown_started"] is not None and not player_state["next_cancelled"]:
            player_state["next_countdown_started"] = None
            next_bar.pack_forget()

        cur_lbl.config(text=_fmt_ms(cur))
        dur_lbl.config(text=_fmt_ms(tot))
        seek_canvas.delete("all")
        cw = max(10, seek_canvas.winfo_width())
        pct = player_state["drag_pct"] if player_state["drag_pct"] is not None else ((cur / tot) if tot > 0 else 0.0)
        pct = max(0.0, min(1.0, pct))
        seek_canvas.create_rectangle(0, 6, cw, 10, fill="#23293f", outline="")
        fill_w = int(cw * pct)
        seek_canvas.create_rectangle(0, 6, fill_w, 10, fill="#e50914", outline="")
        seek_canvas.create_oval(max(0, fill_w - 6), 2, min(cw, fill_w + 6), 14, fill="#ffffff", outline="#e50914", width=2)

        if player_state["osd_until"] > 0 and time.time() > player_state["osd_until"]:
            osd_lbl.config(text="", bg="#0b0e17")
            player_state["osd_until"] = 0.0

        if player_state["fullscreen"] and st == 3 and player_state["hud_visible"] and (time.time() - player_state["last_move"] > 3.2):
            top_bar.pack_forget()
            hud_bar.pack_forget()
            player_state["hud_visible"] = False

        root.after(200, _tick)

    root.after(45, _process_remote_cmds)
    root.after(200, _tick)
    root.mainloop()

class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads      = True

def _probe_existing_instance():
    cfg = _load_app_config()
    candidates = []
    lp = cfg.get("last_port")
    if isinstance(lp, int) and lp > 0:
        candidates.append(lp)
    for p in (PORT, 5051, 5052):
        if p not in candidates:
            candidates.append(p)
    for p in candidates:
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{p}/api/app_info")
            with urllib.request.urlopen(req, timeout=0.45) as resp:
                data = json.loads(resp.read().decode('utf-8', 'replace'))
            if isinstance(data, dict) and data.get("version") and data.get("lan_url"):
                return p
        except Exception:
            continue
    return None

def _start_tray_icon(port, on_quit):
    """Native Win32 System Tray icon (zero console clutter, zero external pip dependency)."""
    if sys.platform != 'win32' or os.environ.get("STREAMHUB_NO_TRAY") == "1":
        return None
    def _tray_thread():
        try:
            import ctypes
            from ctypes import wintypes

            os.makedirs(_APP_DATA_DIR, exist_ok=True)
            ico_path = os.path.join(_APP_DATA_DIR, "streamhub_tray.ico")
            if not os.path.exists(ico_path):
                try:
                    from PIL import Image
                    imgs = [Image.open(io.BytesIO(make_app_icon_png(sz))) for sz in (64, 32, 16)]
                    imgs[0].save(ico_path, format="ICO", sizes=[(im.width, im.height) for im in imgs])
                except Exception:
                    ico_path = ""

            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
            kernel32 = ctypes.windll.kernel32
            LRESULT = ctypes.c_ssize_t
            LPARAM = ctypes.c_ssize_t

            WM_USER = 0x0400
            WM_TRAY = WM_USER + 20
            WM_DESTROY = 0x0002
            WM_COMMAND = 0x0111
            WM_RBUTTONUP = 0x0205
            WM_LBUTTONDBLCLK = 0x0203
            NIM_ADD = 0x00000000
            NIM_DELETE = 0x00000002
            NIF_MESSAGE = 0x00000001
            NIF_ICON = 0x00000002
            NIF_TIP = 0x00000004
            TPM_LEFTALIGN = 0x0000
            TPM_BOTTOMALIGN = 0x0020
            TPM_RIGHTBUTTON = 0x0002
            MF_STRING = 0x00000000
            MF_SEPARATOR = 0x00000800

            WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, LPARAM)

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
                ]

            class NOTIFYICONDATAW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                    ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                    ("szTip", ctypes.c_wchar * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                    ("szInfo", ctypes.c_wchar * 256), ("uVersion", wintypes.UINT),
                    ("szInfoTitle", ctypes.c_wchar * 64), ("dwInfoFlags", wintypes.DWORD),
                    ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON),
                ]

            user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, LPARAM]
            user32.DefWindowProcW.restype = LRESULT
            kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE
            user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
            user32.CreateWindowExW.restype = wintypes.HWND
            user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.LoadImageW.restype = wintypes.HANDLE
            user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
            user32.LoadIconW.restype = wintypes.HICON
            shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]

            nid_holder = [None]

            def _show_menu(hwnd):
                menu = user32.CreatePopupMenu()
                user32.AppendMenuW(menu, MF_STRING, 1001, f"Open StreamHub (Port {port})")
                user32.AppendMenuW(menu, MF_STRING, 1002, f"Open Phone QR / LAN Share")
                user32.AppendMenuW(menu, MF_STRING, 1003, f"Check Updates (v{APP_VERSION})")
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                user32.AppendMenuW(menu, MF_STRING, 1004, "Exit StreamHub")
                pt = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                user32.SetForegroundWindow(hwnd)
                user32.TrackPopupMenu(menu, TPM_LEFTALIGN | TPM_BOTTOMALIGN | TPM_RIGHTBUTTON, pt.x, pt.y, 0, hwnd, None)
                user32.DestroyMenu(menu)

            def _wnd_proc(hwnd, msg, wp, lp):
                if msg == WM_TRAY:
                    ev = lp & 0xFFFF
                    if ev == WM_LBUTTONDBLCLK:
                        webbrowser.open(f"http://127.0.0.1:{port}")
                    elif ev == WM_RBUTTONUP:
                        _show_menu(hwnd)
                    return 0
                if msg == WM_COMMAND:
                    cmd_id = wp & 0xFFFF
                    if cmd_id == 1001:
                        webbrowser.open(f"http://127.0.0.1:{port}")
                    elif cmd_id == 1002:
                        webbrowser.open(f"http://127.0.0.1:{port}/?open=qr")
                    elif cmd_id == 1003:
                        webbrowser.open(f"http://127.0.0.1:{port}/?show_updates=1")
                    elif cmd_id == 1004:
                        if nid_holder[0] is not None:
                            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid_holder[0]))
                        user32.PostQuitMessage(0)
                        on_quit()
                    return 0
                if msg == WM_DESTROY:
                    if nid_holder[0] is not None:
                        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid_holder[0]))
                    user32.PostQuitMessage(0)
                    return 0
                return user32.DefWindowProcW(hwnd, msg, wp, lp)

            wndproc_cb = WNDPROC(_wnd_proc)
            hinst = kernel32.GetModuleHandleW(None)
            cls_name = f"StreamHubTray_{os.getpid()}"
            wc = WNDCLASSW()
            wc.lpfnWndProc = wndproc_cb
            wc.hInstance = hinst
            wc.lpszClassName = cls_name
            user32.RegisterClassW(ctypes.byref(wc))
            hwnd = user32.CreateWindowExW(0, cls_name, "StreamHubTray", 0, 0, 0, 0, 0, None, None, hinst, None)
            hicon = user32.LoadImageW(None, ico_path, 1, 32, 32, 0x0010) if ico_path and os.path.exists(ico_path) else None
            if not hicon:
                hicon = user32.LoadIconW(None, ctypes.cast(32512, wintypes.LPCWSTR))

            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = hwnd
            nid.uID = 1
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = WM_TRAY
            nid.hIcon = hicon
            nid.szTip = f"StreamHub v{APP_VERSION} (Port {port})"[:127]
            nid_holder[0] = nid
            shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            pass

    t = threading.Thread(target=_tray_thread, daemon=True)
    t.start()
    return t

def _ensure_desktop_ico():
    meipass = getattr(sys, '_MEIPASS', '')
    here = os.path.dirname(os.path.abspath(__file__))
    exe_dir = os.path.dirname(sys.executable) if IS_FROZEN else here
    for cand in (
        os.path.join(meipass, "streamhub.ico") if meipass else "",
        os.path.join(exe_dir, "streamhub.ico"),
        os.path.join(here, "build", "streamhub.ico"),
        os.path.join(_APP_DATA_DIR, "streamhub.ico"),
    ):
        if cand and os.path.exists(cand):
            return cand
    try:
        from PIL import Image, ImageDraw, ImageFont
        os.makedirs(_APP_DATA_DIR, exist_ok=True)
        out_ico = os.path.join(_APP_DATA_DIR, "streamhub.ico")
        sz = 256
        img = Image.new("RGBA", (sz, sz), (10, 12, 20, 255))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([12, 12, sz - 12, sz - 12], radius=52, fill=(14, 16, 28, 255), outline=(229, 9, 20, 255), width=10)
        draw.rounded_rectangle([42, 42, sz - 42, sz - 42], radius=34, fill=(229, 9, 20, 255))
        try:
            fnt = ImageFont.truetype("C:\\Windows\\Fonts\\segoeuib.ttf", 142)
        except Exception:
            fnt = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), "S", font=fnt)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((sz - tw) // 2 - bbox[0], (sz - th) // 2 - bbox[1] - 4), "S", fill=(255, 255, 255, 255), font=fnt)
        img.save(out_ico, format="ICO", sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
        return out_ico
    except Exception:
        return ""

def _extract_hwnd(win):
    if win is None:
        return 0
    try:
        native = getattr(win, 'native', None)
        h = getattr(native, 'Handle', None) if native is not None else None
        if h is None:
            return 0
        if hasattr(h, 'ToInt64'):
            return int(h.ToInt64())
        return int(str(h))
    except Exception:
        return 0

def _style_desktop_hwnd(hwnd, ico_path=""):
    """Applies dark Windows title bar, app icon, and guarantees Windows Taskbar visibility."""
    if sys.platform != 'win32' or not hwnd:
        return
    try:
        import ctypes
        u32 = ctypes.windll.user32
        dwm = getattr(ctypes.windll, "dwmapi", None)
        if dwm is not None:
            for attr in (20, 19):
                val = ctypes.c_int(1)
                try:
                    dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attr, ctypes.byref(val), ctypes.sizeof(val))
                except Exception:
                    pass
        if ico_path and os.path.exists(ico_path):
            h_big = u32.LoadImageW(None, str(ico_path), 1, 48, 48, 0x00000010)
            h_small = u32.LoadImageW(None, str(ico_path), 1, 16, 16, 0x00000010)
            if h_small:
                u32.SendMessageW(ctypes.c_void_p(hwnd), 0x0080, 0, h_small)
            if h_big:
                u32.SendMessageW(ctypes.c_void_p(hwnd), 0x0080, 1, h_big)
        ex = u32.GetWindowLongW(ctypes.c_void_p(hwnd), -20)
        new_ex = (ex & ~0x00000080) | 0x00040000  # ~WS_EX_TOOLWINDOW | WS_EX_APPWINDOW
        if new_ex != ex:
            u32.SetWindowLongW(ctypes.c_void_p(hwnd), -20, new_ex)
        style = u32.GetWindowLongW(ctypes.c_void_p(hwnd), -16)
        u32.SetWindowLongW(ctypes.c_void_p(hwnd), -16, style | 0x00020000 | 0x00010000 | 0x00080000)  # MINIMIZEBOX | MAXIMIZEBOX | SYSMENU
    except Exception:
        pass

def _focus_desktop_window():
    win = _WINDOW_REF
    if win is not None:
        try:
            win.restore()
        except Exception:
            pass
        try:
            win.show()
        except Exception:
            pass
    if sys.platform == 'win32' and _WINDOW_HWND:
        try:
            import ctypes
            u32 = ctypes.windll.user32
            if u32.IsIconic(ctypes.c_void_p(_WINDOW_HWND)):
                u32.ShowWindow(ctypes.c_void_p(_WINDOW_HWND), 9)  # SW_RESTORE
            else:
                u32.ShowWindow(ctypes.c_void_p(_WINDOW_HWND), 5)  # SW_SHOW
            u32.SetForegroundWindow(ctypes.c_void_p(_WINDOW_HWND))
        except Exception:
            pass

def _apply_window_action(action):
    win = _WINDOW_REF
    hwnd = int(_WINDOW_HWND or 0)
    if win is None and not hwnd:
        return {"success": False, "error": "Desktop window inactive"}
    if action == 'minimize':
        try:
            if win is not None:
                win.minimize()
            elif sys.platform == 'win32' and hwnd:
                import ctypes
                ctypes.windll.user32.ShowWindow(ctypes.c_void_p(hwnd), 6)
            return {"success": True, "action": "minimize", "hwnd": hwnd}
        except Exception as e:
            return {"success": False, "error": str(e)}
    if action == 'maximize':
        try:
            if win is not None:
                win.maximize()
            elif sys.platform == 'win32' and hwnd:
                import ctypes
                ctypes.windll.user32.ShowWindow(ctypes.c_void_p(hwnd), 3)
            return {"success": True, "action": "maximize", "hwnd": hwnd}
        except Exception as e:
            return {"success": False, "error": str(e)}
    if action in ('restore', 'focus'):
        _focus_desktop_window()
        return {"success": True, "action": action, "hwnd": hwnd}
    if action == 'close':
        def _close_async():
            time.sleep(0.08)
            try:
                if _WINDOW_REF is not None:
                    _WINDOW_REF.destroy()
                    return
            except Exception:
                pass
            if sys.platform == 'win32' and hwnd:
                try:
                    import ctypes
                    ctypes.windll.user32.PostMessageW(ctypes.c_void_p(hwnd), 0x0010, 0, 0)  # WM_CLOSE
                except Exception:
                    pass
        threading.Thread(target=_close_async, daemon=True).start()
        return {"success": True, "action": "close", "hwnd": hwnd}
    return {"success": False, "error": f"Unknown window action: {action}"}

def _launch_edge_app_fallback(url):
    """Fallback standalone window via Edge/Chrome --app mode if WebView2 fails."""
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    exe = next((p for p in candidates if os.path.exists(p)), None)
    if not exe:
        exe = shutil.which("msedge") or shutil.which("chrome")
    if not exe:
        return None
    profile_dir = os.path.join(_APP_DATA_DIR, "AppWindowProfile")
    os.makedirs(profile_dir, exist_ok=True)
    return subprocess.Popen([
        exe,
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        "--window-size=1320,840",
        "--no-first-run",
        "--no-default-browser-check",
    ])

def start_android_server(data_dir=None, port=5050):
    """Starts StreamHub's pure-Python HTTP resolver + proxy server inside Android APK."""
    global _APP_DATA_DIR, _PROGRESS_FILE, _CONFIG_FILE, _ACTIVE_PORT, _SERVER_REF, _DESKTOP_MODE
    if data_dir:
        _APP_DATA_DIR = str(data_dir)
        os.makedirs(_APP_DATA_DIR, exist_ok=True)
        _PROGRESS_FILE = os.path.join(_APP_DATA_DIR, "streamhub_progress.json")
        _CONFIG_FILE = os.path.join(_APP_DATA_DIR, "streamhub_config.json")
    _DESKTOP_MODE = False
    if _SERVER_REF is not None and _ACTIVE_PORT:
        return int(_ACTIVE_PORT)
    for cand in (int(port or 5050), 5050, 5051, 5052, 5053, 0):
        try:
            httpd = ThreadedTCPServer(("127.0.0.1", cand), RequestHandler)
            bound = int(httpd.server_address[1])
            _ACTIVE_PORT = bound
            _SERVER_REF = httpd
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            return bound
        except OSError:
            continue
    raise RuntimeError("Could not bind local StreamHub Android server")

def _bind_available_server():
    for cand in (PORT, 5051, 5052, 5053, 5054, 5055, 0):
        try:
            srv = ThreadedTCPServer(("", cand), RequestHandler)
            return srv, srv.server_address[1]
        except OSError:
            continue
    srv = ThreadedTCPServer(("", 0), RequestHandler)
    return srv, srv.server_address[1]

def run():
    global _ACTIVE_PORT, _SERVER_REF, _WINDOW_REF, _WINDOW_HWND, _APP_QUITTING, _DESKTOP_MODE
    _ensure_embedded_vlc_extracted(show_splash=IS_FROZEN)
    existing_port = _probe_existing_instance()
    if existing_port:
        focused_hwnd = 0
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{existing_port}/api/focus_app",
                data=b"{}",
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                info = json.loads(resp.read().decode('utf-8', 'replace'))
                focused_hwnd = int(info.get("window_hwnd", 0) or 0)
        except Exception:
            pass
        if not focused_hwnd:
            try:
                webbrowser.open(f"http://127.0.0.1:{existing_port}")
            except Exception:
                pass
        return

    httpd, bound_port = _bind_available_server()
    _ACTIVE_PORT = bound_port
    _SERVER_REF = httpd
    _save_app_config({"last_port": bound_port})
    lan_ip = get_lan_ip()
    url_local = f"http://127.0.0.1:{bound_port}/"

    print("=" * 60)
    print(f"   [SH] StreamHub v{APP_VERSION} - 4K + HDHub4u + In-Built Cinema")
    print("=" * 60)
    print(f"  Engine     : {'In-Built libVLC Ready' if _find_libvlc_dir() else 'Browser / External Only'}")
    print(f"  Server URL : http://localhost:{bound_port}")
    print(f"  Mobile URL : http://{lan_ip}:{bound_port}")
    print("=" * 60)

    def _quit_app(skip_destroy=False):
        global _APP_QUITTING
        if _APP_QUITTING:
            return
        _APP_QUITTING = True
        _stop_previous_players()
        if not skip_destroy:
            try:
                if _WINDOW_REF is not None:
                    _WINDOW_REF.destroy()
            except Exception:
                pass
        def _stop_srv():
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        t_srv = threading.Thread(target=_stop_srv, daemon=True)
        t_srv.start()
        t_srv.join(timeout=0.35)
        os._exit(0)

    headless = ('--headless' in sys.argv) or (os.environ.get('STREAMHUB_HEADLESS', '').strip() == '1')
    if headless:
        def _open_browser_headless():
            if os.environ.get('STREAMHUB_NO_BROWSER', '').strip() == '1':
                return
            time.sleep(0.6)
            try:
                webbrowser.open(url_local)
            except Exception:
                pass
        threading.Thread(target=_open_browser_headless, daemon=True).start()
        with httpd:
            print(f"\n  StreamHub Server running at http://localhost:{bound_port} (Mobile QR: http://{lan_ip}:{bound_port})\n")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\nServer stopped.")
        return

    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("StreamHub.Desktop.App")
        except Exception:
            pass

    ico_path = _ensure_desktop_ico()
    wv_data_dir = os.path.join(_APP_DATA_DIR, "WebView2")
    os.makedirs(wv_data_dir, exist_ok=True)
    os.environ.setdefault("WEBVIEW2_USER_DATA_FOLDER", wv_data_dir)

    print(f"\n  StreamHub Desktop App running at http://localhost:{bound_port} (Mobile QR: http://{lan_ip}:{bound_port})\n")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    try:
        import webview
        _DESKTOP_MODE = True
        webview.settings['ALLOW_DOWNLOADS'] = True
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True

        win = webview.create_window(
            "StreamHub",
            url_local,
            width=1320,
            height=840,
            min_size=(380, 600),
            resizable=True,
            background_color="#05060b",
            text_select=True,
        )
        _WINDOW_REF = win

        def _on_closed(*_args):
            threading.Thread(target=_quit_app, args=(True,), daemon=True).start()

        try:
            win.events.closed += _on_closed
        except Exception:
            pass

        def _on_win_ready(w, icon_file):
            global _WINDOW_HWND
            for _ in range(25):
                hwnd = _extract_hwnd(w)
                if hwnd:
                    _WINDOW_HWND = hwnd
                    _style_desktop_hwnd(hwnd, icon_file)
                    break
                time.sleep(0.08)

        webview.start(
            func=_on_win_ready,
            args=(win, ico_path),
            gui='edgechromium',
            private_mode=False,
            storage_path=wv_data_dir,
            icon=ico_path or None,
        )
        _quit_app()
        return
    except Exception as wv_err:
        _DESKTOP_MODE = False
        _WINDOW_REF = None
        print(f"Native WebView2 fallback ({wv_err}); trying Edge/Chrome standalone app window...")

    app_proc = _launch_edge_app_fallback(url_local)
    if app_proc is not None:
        try:
            app_proc.wait()
        except Exception:
            pass
        _quit_app()
        return

    if IS_FROZEN:
        _start_tray_icon(bound_port, _quit_app)
    threading.Thread(target=lambda: (time.sleep(0.6), webbrowser.open(url_local)), daemon=True).start()
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        _quit_app()

if __name__ == '__main__':
    if '--player-ui' in sys.argv:
        _ensure_embedded_vlc_extracted(show_splash=False)
        _run_inbuilt_player()
    else:
        run()
