import subprocess
import time

vlc_path = r"C:\Program Files\VideoLAN\VLC\vlc.exe"
worker_url = "https://fbxdcn.jaboy16577.workers.dev/e3bddc1d81d2bcf7bfbbe5b1fc01575c8c61fc40496f7fb13f507d6fcd16227df7eec4eccd43cb9803ea34411b259eb7%3A%3A5cd23fe7d8b5d5dba54863747a32c544/1398015969/%5B4K%5D%5B60FPS%5D.The.Avengers.2012.2160p.4K.SDR.BluRay.x265.Dual.%5BEng%2BHin%5D.Esub.%F0%9F%92%80Homelander%F0%9F%92%80-4KHDHub.Com.mkv"

subprocess.run(["taskkill", "/F", "/IM", "vlc.exe"], capture_output=True)

opts = [
    vlc_path,
    "--network-caching=3000",
    "--http-referrer=https://gamerxyt.com/",
    "--avcodec-fast",
    worker_url
]

print("Launching VLC directly with Worker URL...")
p = subprocess.Popen(opts)
time.sleep(5)

res = subprocess.check_output(['powershell', '-Command', f'Get-Process -Id {p.pid} | Select-Object Id, ProcessName, WorkingSet64']).decode('utf-8', errors='ignore')
print("VLC info:\n", res)

time.sleep(2)
subprocess.run(["taskkill", "/F", "/IM", "vlc.exe"], capture_output=True)
print("Done.")
