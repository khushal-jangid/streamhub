import os, sys, io, json, hashlib, shutil, zipfile, subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import app  # reads APP_VERSION & make_app_icon_png cleanly

BUILD_DIR = os.path.join(BASE_DIR, "build")
DIST_DIR = os.path.join(BASE_DIR, "dist")
ICO_PATH = os.path.join(BUILD_DIR, "streamhub.ico")
VLC_ZIP = os.path.join(BUILD_DIR, "vlc_runtime.zip")

KEEP_PLUGIN_DIRS = {
    "access", "audio_filter", "audio_mixer", "audio_output",
    "codec", "control", "d3d11", "d3d9", "demux",
    "logger", "meta_engine", "misc", "packetizer",
    "spu", "stream_extractor", "stream_filter", "text_renderer",
    "video_chroma", "video_filter", "video_output",
}

def make_icon():
    from PIL import Image
    os.makedirs(BUILD_DIR, exist_ok=True)
    imgs = [Image.open(io.BytesIO(app.make_app_icon_png(sz))) for sz in (256, 128, 64, 48, 32, 16)]
    imgs[0].save(ICO_PATH, format="ICO", sizes=[(i.width, i.height) for i in imgs])
    print("Icon generated:", ICO_PATH)

def pack_vlc_runtime():
    vlc_src = app._find_libvlc_dir()
    if not vlc_src or not os.path.exists(os.path.join(vlc_src, "libvlc.dll")):
        raise RuntimeError("VLC installation not found for bundling!")
    print("Packing portable VLC from:", vlc_src)
    stage_dir = os.path.join(BUILD_DIR, "vlc_stage")
    if os.path.exists(stage_dir):
        shutil.rmtree(stage_dir, ignore_errors=True)
    os.makedirs(stage_dir, exist_ok=True)
    for dll in ("libvlc.dll", "libvlccore.dll"):
        p = os.path.join(vlc_src, dll)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(stage_dir, dll))
    plug_src = os.path.join(vlc_src, "plugins")
    plug_dst = os.path.join(stage_dir, "plugins")
    for dname in sorted(KEEP_PLUGIN_DIRS):
        src_d = os.path.join(plug_src, dname)
        if os.path.isdir(src_d):
            shutil.copytree(src_d, os.path.join(plug_dst, dname))
    cache_gen = os.path.join(vlc_src, "vlc-cache-gen.exe")
    if os.path.exists(cache_gen):
        r = subprocess.run([cache_gen, plug_dst], capture_output=True)
        print("vlc-cache-gen rc:", r.returncode)

    count = 0
    with zipfile.ZipFile(VLC_ZIP, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, _, files in os.walk(stage_dir):
            for fn in files:
                if not (fn.lower().endswith(".dll") or fn.lower() == "plugins.dat"):
                    continue
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, stage_dir)
                zf.write(full, arcname=rel)
                count += 1
    mb = os.path.getsize(VLC_ZIP) / (1024 * 1024)
    print(f"Packed {count} VLC runtime files into {VLC_ZIP} ({mb:.1f} MB compressed)")

def run_pyinstaller():
    os.makedirs(DIST_DIR, exist_ok=True)
    py_exe = sys.executable
    pi_exe = os.path.join(os.path.dirname(py_exe), "Scripts", "pyinstaller.exe")
    cmd = [
        pi_exe if os.path.exists(pi_exe) else "pyinstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noconsole",
        "--name", "StreamHub",
        "--icon", ICO_PATH,
        "--distpath", DIST_DIR,
        "--workpath", os.path.join(BUILD_DIR, "work"),
        "--specpath", BUILD_DIR,
        "--add-data", f"{VLC_ZIP};.",
        "--add-data", f"{ICO_PATH};.",
        "--collect-all", "webview",
        "--collect-all", "pythonnet",
        "--collect-all", "clr_loader",
        "--hidden-import", "clr",
        "--hidden-import", "webview",
        "--hidden-import", "webview.platforms.winforms",
        "--hidden-import", "webview.platforms.edgechromium",
        "--hidden-import", "bottle",
        "--hidden-import", "proxy_tools",
        "--hidden-import", "PIL.Image",
        "--hidden-import", "PIL.ImageDraw",
        "--hidden-import", "qrcode",
        os.path.join(BASE_DIR, "app.py"),
    ]
    print("Running PyInstaller:", " ".join(cmd))
    res = subprocess.run(cmd, cwd=BASE_DIR)
    if res.returncode != 0:
        raise RuntimeError(f"PyInstaller exited with code {res.returncode}")

def write_release_manifest():
    exe_dist = os.path.join(DIST_DIR, "StreamHub.exe")
    if not os.path.exists(exe_dist):
        raise RuntimeError("StreamHub.exe missing in dist/")
    exe_top = os.path.join(BASE_DIR, "StreamHub.exe")
    shutil.copy2(exe_dist, exe_top)

    h = hashlib.sha256()
    with open(exe_dist, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    digest = h.hexdigest()
    sz = os.path.getsize(exe_dist)
    manifest = {
        "version": app.APP_VERSION,
        "download_url": "https://github.com/YOUR_USERNAME/YOUR_REPO/releases/latest/download/StreamHub.exe",
        "sha256": digest,
        "size_bytes": sz,
        "notes": f"StreamHub v{app.APP_VERSION}: Standalone 4K Cinema App with built-in libVLC & 1-click Auto-Updater.",
    }
    mf_path = os.path.join(DIST_DIR, "version.json")
    with open(mf_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"SUCCESS: {exe_top} ({sz/(1024*1024):.1f} MB, sha256={digest[:16]}...)")
    print(f"Release manifest written: {mf_path}")

if __name__ == "__main__":
    make_icon()
    pack_vlc_runtime()
    run_pyinstaller()
    write_release_manifest()
