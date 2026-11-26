import subprocess
import time

vlc_path = r"C:\Program Files\VideoLAN\VLC\vlc.exe"
google_url = "https://video-downloads.googleusercontent.com/ADGPM2mqVOGsjM1u_yX2liE6-FhGDg-nvpLPsu92-ocUOFuqGsjOJWEORg7lgjJAFwNpjAysE_CQOfDdrwv3PKzd3UjJcx_m7n6nsBCZ6ESMf_A4EsH-PcP6t4vpZxkPqQrGLAocqpMq31HyyOn_TfjriLyteggJq1YmaKZz3IQAoXoD5Gr2gC3E48MRyKJHxDzfUkDCEzIVjBMkvRruUlLgrWsDPiwwNXF5uKA2qg2MkBD6ptNJFa4H3ySeqMB8KcjGU666WPJON1FmjV-0j7qJ34AA3LNEZw"

subprocess.run(["taskkill", "/F", "/IM", "vlc.exe"], capture_output=True)

opts = [
    vlc_path,
    "--input-fast-seek",
    "--network-caching=1000",
    "--avcodec-fast",
    "--avcodec-threads=0",
    "--drop-late-frames",
    "--skip-frames",
    "--clock-synchro=0",
    "--clock-jitter=0",
    google_url
]

print("Launching VLC directly with Google CDN & fast-seek...")
p = subprocess.Popen(opts)
time.sleep(5)

res = subprocess.check_output(['powershell', '-Command', f'Get-Process -Id {p.pid} | Select-Object Id, ProcessName, WorkingSet64']).decode('utf-8', errors='ignore')
print("VLC info:\n", res)

time.sleep(2)
subprocess.run(["taskkill", "/F", "/IM", "vlc.exe"], capture_output=True)
print("Done.")
