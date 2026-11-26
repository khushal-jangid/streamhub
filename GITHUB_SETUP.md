# 🚀 StreamHub — GitHub Update Pipeline Setup Guide

Ye guide follow karo aur ek baar setup ke baad, **har nayi version automatically sab users tak pahunch jayegi.**

---

## ✅ Step 1: GitHub Par Repo Banao

1. [github.com/new](https://github.com/new) par jao
2. Repository banao:
   - **Repository name**: `streamhub`
   - **Visibility**: ✅ **Public** (zaruri hai — private mein APK download nahi hoga)
   - README: skip karo
3. **Create repository** click karo

---

## ✅ Step 2: Git Setup & Code Upload (Pehli Baar)

PowerShell open karo aur ye commands chalaao:

```powershell
cd "C:\Users\choya\Downloads\StreamHub\StreamHub"

# Git initialize karo
git init
git remote add origin https://github.com/khushal-jangid/streamhub.git
git branch -M main

# .gitignore banao
@"
__pycache__/
*.pyc
build/work/
dist/StreamHub.exe
StreamHub.exe
*.log
"@ | Out-File .gitignore -Encoding UTF8

# Pehli baar sab upload karo
git add .
git commit -m "Initial commit: StreamHub v2.3.0"
git push -u origin main
```

> Pehli baar GitHub login maangega — browser mein authenticate ho jayega.

---

## ✅ Step 3: Nayi Version Release Kaise Karo (Har Baar)

Jab bhi **new feature add karo ya bug fix karo**:

### a) Version bump karo in `app.py`:
```python
APP_VERSION = "2.4.0"   # line 55 — version badhao
```

### b) APK root folder mein rakhna hai:
```
C:\Users\choya\Downloads\StreamHub\StreamHub\StreamHub.apk  ← updated APK yahan
```

### c) Build + Release:
```powershell
cd "C:\Users\choya\Downloads\StreamHub\StreamHub"

# EXE build karo
python build_exe.py

# Commit karo
git add .
git commit -m "v2.4.0: Naya feature XYZ add kiya"

# Tag lagao — yahi GitHub Actions ko trigger karega!
git tag v2.4.0
git push origin main
git push origin v2.4.0
```

**Bas!** GitHub Actions automatically (5-10 min mein):
1. ✅ `StreamHub.exe` build karega
2. ✅ `version.json` update karega (real sha256 ke saath)
3. ✅ GitHub Release banayega
4. ✅ `StreamHub.exe` + `StreamHub.apk` + `version.json` upload karega

---

## ✅ Step 4: Users Ko Ek Baar Batana Hoga (Pehli Baar Only)

Pehli APK share karte waqt users ko ye URL app mein set karna hoga:

```
khushal-jangid/streamhub
```

**Settings (⚙) → Update Source field mein dalo → Save & Check**

Iske baad **hamesha ke liye automatic** — user ko kuch nahi karna padega!

---

## 📱 Android Users Ka Update Flow

```
Tu: git tag v2.4.0 → git push
           ↓ (5-10 min)
GitHub Actions: EXE + APK upload on Release page
           ↓ (automatic)
User app khole: "⬆ Update v2.4.0" notification dikhega
           ↓
"📱 Download New APK" button tap kare
           ↓
Browser mein APK download ho → Install → Done ✅
```

---

## 🔗 Teri Important Links

| Kya | URL |
|-----|-----|
| 📦 Releases page | https://github.com/khushal-jangid/streamhub/releases |
| 📄 version.json | https://github.com/khushal-jangid/streamhub/releases/latest/download/version.json |
| 📱 Latest APK | https://github.com/khushal-jangid/streamhub/releases/latest/download/StreamHub.apk |
| 🖥️ Latest EXE | https://github.com/khushal-jangid/streamhub/releases/latest/download/StreamHub.exe |
| ⚙️ Actions log | https://github.com/khushal-jangid/streamhub/actions |

---

## ⚠️ Important Notes

> **APK Manual Step**: `StreamHub.apk` repo root mein commit karna padega.
> GitHub Actions isko automatically release mein upload kar dega.

> **VLC Runtime**: Agar `build/vlc_runtime.zip` repo mein committed nahi hai toh
> EXE Actions par build fail hogi. Is case mein manually `dist/StreamHub.exe`
> build karke Actions artifacts se download karo ya pre-built EXE commit karo.
