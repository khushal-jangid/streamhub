param(
    [string]$Version = "",
    [string]$Notes = "",
    [int]$MinApkVersionCode = 0
)

$ErrorActionPreference = "Stop"
$StreamHubDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $StreamHubDir
$AndroidDir = Join-Path $RootDir "StreamHubAndroid"
$AppPy = Join-Path $StreamHubDir "app.py"
$AndroidPy = Join-Path $AndroidDir "app\src\main\python\streamhub_app.py"
$GradleFile = Join-Path $AndroidDir "app\build.gradle.kts"
$ManifestOut = Join-Path $StreamHubDir "dist\version.json"
$ApkOut = Join-Path $StreamHubDir "StreamHub.apk"
$ExeOut = Join-Path $StreamHubDir "dist\StreamHub.exe"
$Repo = "khushal-jangid/streamhub"

# 1. Read or update APP_VERSION in app.py
$appText = [System.IO.File]::ReadAllText($AppPy, [System.Text.Encoding]::UTF8)
if ($Version) {
    $Version = $Version.TrimStart('v', 'V')
    $appText = [regex]::Replace($appText, '(?m)^APP_VERSION\s*=\s*"[^"]+"', "APP_VERSION = `"$Version`"")
    [System.IO.File]::WriteAllText($AppPy, $appText, (New-Object System.Text.UTF8Encoding($false)))
} else {
    $m = [regex]::Match($appText, '(?m)^APP_VERSION\s*=\s*"([^"]+)"')
    if (-not $m.Success) { throw "APP_VERSION not found in app.py" }
    $Version = $m.Groups[1].Value
}

if (-not $Notes) {
    $Notes = "StreamHub v$Version — Latest updates, features & fixes for PC and Android."
}

$Tag = "v$Version"
Write-Host "=== StreamHub Release Builder: $Tag ===" -ForegroundColor Cyan

# 2. Sync app.py -> Android streamhub_app.py
Write-Host "[1/6] Syncing app.py -> StreamHubAndroid streamhub_app.py..."
[System.IO.File]::Copy($AppPy, $AndroidPy, $true)

# 3. Sync versionName + bump/read versionCode in build.gradle.kts
$gradleText = [System.IO.File]::ReadAllText($GradleFile, [System.Text.Encoding]::UTF8)
$vcMatch = [regex]::Match($gradleText, 'versionCode\s*=\s*(\d+)')
$vnMatch = [regex]::Match($gradleText, 'versionName\s*=\s*"([^"]+)"')
$apkVersionCode = if ($vcMatch.Success) { [int]$vcMatch.Groups[1].Value } else { 1 }
$prevVn = if ($vnMatch.Success) { $vnMatch.Groups[1].Value } else { "" }
if ($prevVn -ne $Version) {
    $apkVersionCode = $apkVersionCode + 1
    $gradleText = [regex]::Replace($gradleText, 'versionCode\s*=\s*\d+', "versionCode = $apkVersionCode")
    $gradleText = [regex]::Replace($gradleText, 'versionName\s*=\s*"[^"]+"', "versionName = `"$Version`"")
    [System.IO.File]::WriteAllText($GradleFile, $gradleText, (New-Object System.Text.UTF8Encoding($false)))
}
Write-Host "    Android versionName=$Version, versionCode=$apkVersionCode"

# 4. Build Android Release APK
Write-Host "[2/6] Building Android APK (Gradle assembleRelease)..."
Push-Location $AndroidDir
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    cmd /c "gradlew.bat assembleRelease"
    if ($LASTEXITCODE -ne 0) { throw "Gradle assembleRelease failed with exit code $LASTEXITCODE" }
} finally {
    $ErrorActionPreference = $prevEap
    Pop-Location
}
$builtApk = Join-Path $AndroidDir "app\build\outputs\apk\release\app-release.apk"
if (-not (Test-Path $builtApk)) { throw "Built APK not found at $builtApk" }
Copy-Item $builtApk $ApkOut -Force
Write-Host "    APK copied -> $ApkOut"

# 5. Build Windows EXE
Write-Host "[3/6] Building Windows StreamHub.exe (PyInstaller)..."
Push-Location $StreamHubDir
try {
    python build_exe.py
    if ($LASTEXITCODE -ne 0) { throw "build_exe.py failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

# 6. Compute hashes and write unified dist/version.json (EXE + APK + Instant OTA Python)
Write-Host "[4/6] Generating version.json with SHA-256 checksums..."
$exeSha = (Get-FileHash $ExeOut -Algorithm SHA256).Hash.ToLower()
$exeSize = (Get-Item $ExeOut).Length
$apkSha = (Get-FileHash $ApkOut -Algorithm SHA256).Hash.ToLower()
$apkSize = (Get-Item $ApkOut).Length
$pySha = (Get-FileHash $AndroidPy -Algorithm SHA256).Hash.ToLower()

$manifest = [ordered]@{
    version = $Version
    download_url = "https://github.com/$Repo/releases/download/$Tag/StreamHub.exe"
    sha256 = $exeSha
    size_bytes = $exeSize
    apk_download_url = "https://github.com/$Repo/releases/download/$Tag/StreamHub.apk"
    apk_sha256 = $apkSha
    apk_size_bytes = $apkSize
    py_download_url = "https://github.com/$Repo/releases/download/$Tag/streamhub_app.py"
    py_sha256 = $pySha
    min_apk_version_code = if ($MinApkVersionCode -gt 0) { $MinApkVersionCode } else { $apkVersionCode }
    notes = $Notes
}
$jsonOut = $manifest | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText($ManifestOut, $jsonOut + "`n", (New-Object System.Text.UTF8Encoding($false)))

# 7. Commit & push source changes
Write-Host "[5/6] Pushing code to GitHub..."
Push-Location $StreamHubDir
try {
    git add -A
    $pending = git status --porcelain
    if ($pending) {
        git commit -m "Release $Tag"
    }
    git push origin main

    # 8. Create or update GitHub Release
    Write-Host "[6/6] Publishing GitHub Release $Tag..."
    $exists = $false
    try {
        gh release view $Tag 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $exists = $true }
    } catch {}

    $releaseNotes = @"
## StreamHub $Tag

$Notes

### Downloads
- **Android App**: ``StreamHub.apk`` (Instant OTA updates enabled!)
- **Windows PC App**: ``StreamHub.exe`` (1-click auto-updater)
"@

    if ($exists) {
        gh release upload $Tag $ExeOut $ApkOut $AndroidPy $ManifestOut --clobber
        gh release edit $Tag --title "StreamHub $Tag" --notes $releaseNotes
    } else {
        gh release create $Tag $ExeOut $ApkOut $AndroidPy $ManifestOut --title "StreamHub $Tag" --notes $releaseNotes
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "SUCCESS! Release $Tag is live at: https://github.com/$Repo/releases/tag/$Tag" -ForegroundColor Green
