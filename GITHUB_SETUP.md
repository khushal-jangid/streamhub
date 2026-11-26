# 🚀 StreamHub — GitHub Auto-Update System

Ab jab bhi aap **StreamHub** mein koi naya feature add karo ya bug fix karo, bas **1 command / double-click** chalao — aur sabhi PC + Android users ko update automatically app ke andar mil jayega!

---

## 🎯 Ye Kaise Kaam Karta Hai

### 1. Android Users Ke Liye (2 Tarike):
- **⚡ Instant Update (~0.5 MB, 2 seconds)**: Jab aap sirf `app.py` (UI, movie sites, link resolvers, bug fixes) mein feature add karte ho, toh user ke phone mein app sirf `streamhub_app.py` download karke SHA-256 verify karta hai aur 2 second mein restart hokar naya feature dikha deta hai — **75 MB APK dubara install karne ki zarurat bhi nahi!**
- **📱 Full APK Update**: Jab Android/Kotlin player ya native features badalte ho, toh app ke andar hi `StreamHub.apk` download hokar seedha Android Installer open ho jata hai.

### 2. Windows PC Users Ke Liye:
- App automatically `StreamHub.exe` download karta hai, SHA-256 verify karta hai, aur khud restart hokar update ho jata hai.

---

## 🛠️ Naya Feature Push Karne Ka Tarika (1-Click!)

1. Apne `app.py` (ya `StreamHubAndroid` ke code) mein naya feature add karo.
2. **`publish_release.bat`** par double-click karo (ya PowerShell mein chalao):
   ```powershell
   cd "C:\Users\choya\Downloads\StreamHub\StreamHub"
   .\publish_release.ps1 -Version "2.5.0" -Notes "Naya feature add kiya!"
   ```
3. Ye script automatically:
   - `app.py` ko `StreamHubAndroid\app\src\main\python\streamhub_app.py` mein sync karta hai
   - Naya `StreamHub.apk` build karta hai
   - Naya `StreamHub.exe` build karta hai
   - `dist\version.json` mein SHA-256 checksums update karta hai
   - Code GitHub par push karta hai aur `StreamHub.exe`, `StreamHub.apk`, `streamhub_app.py`, `version.json` ke saath nayi GitHub Release publish karta hai!

---

## ⚠️ Pehli Baar Users Ko Kya Dena Hoga

- **v2.4.0** (nayi `StreamHub.apk`) ek baar users ko bhej do (ya `https://github.com/khushal-jangid/streamhub/releases/latest` se download karwao).
- Iske baad **kabhi manually APK share karne ki zarurat nahi** — app khulne par khud GitHub check karega aur update dikha dega!

---

## 🔗 GitHub Links

- **Repo**: https://github.com/khushal-jangid/streamhub
- **Releases**: https://github.com/khushal-jangid/streamhub/releases
- **Latest Download**: https://github.com/khushal-jangid/streamhub/releases/latest
