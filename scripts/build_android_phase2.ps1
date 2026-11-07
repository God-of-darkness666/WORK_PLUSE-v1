# Builds HRMonitor Mobile APK — phase 2: extract manually-downloaded packages + gradle build.
# Assumes the 4 zips are already in C:\work\android-build (downloaded with curl).
$ErrorActionPreference = "Stop"
$root = "C:\work\android-build"
$proj = "C:\work\hr-monitor\mobile"

$env:JAVA_TOOL_OPTIONS = "-Djava.net.preferIPv4Stack=true"

function Log($m) { Write-Host ("[build-apk] " + $m) }

# ---------- 1. JDK 17 (already present) ----------
$jdkDir = (Get-ChildItem $root -Directory | Where-Object Name -like "jdk-17*" | Select-Object -First 1).FullName
$env:JAVA_HOME = $jdkDir
Log "JDK: $jdkDir"

# ---------- 2. extract platform-34 ----------
$sdk = "$root\sdk"
if (-not (Test-Path "$sdk\platforms\android-34\android.jar")) {
    $platZip = "$root\platform-34-ext7_r02.zip"
    if (-not (Test-Path $platZip)) { throw "platform zip missing!" }
    Log "extracting platform-34..."
    Expand-Archive $platZip -DestinationPath "$root\tmp_plat" -Force
    $inner = (Get-ChildItem "$root\tmp_plat" -Directory -Recurse | Where-Object Name -eq "android-34" | Where-Object { Test-Path "$($_.FullName)\android.jar" } | Select-Object -First 1).FullName
    if (-not $inner) { throw "could not locate android-34 folder inside the platform zip" }
    New-Item -ItemType Directory -Force -Path "$sdk\platforms" | Out-Null
    if (Test-Path "$sdk\platforms\android-34") { Remove-Item "$sdk\platforms\android-34" -Recurse -Force }
    Move-Item $inner "$sdk\platforms\android-34" -Force
    Remove-Item "$root\tmp_plat" -Recurse -Force
}
Log "platform: $(Test-Path "$sdk\platforms\android-34\android.jar")"

# ---------- 3. extract build-tools 34.0.0 ----------
if (-not (Test-Path "$sdk\build-tools\34.0.0\aapt2.exe")) {
    Log "extracting build-tools..."
    Expand-Archive "$root\build-tools_r34-windows.zip" -DestinationPath "$root\tmp_bt" -Force
    $btSrc = (Get-ChildItem "$root\tmp_bt" -Directory | Where-Object { Test-Path "$($_.FullName)\aapt2.exe" -ErrorAction SilentlyContinue } | Select-Object -First 1).FullName
    if (-not $btSrc) { $btSrc = (Get-ChildItem "$root\tmp_bt" -Directory | Select-Object -First 1).FullName }
    New-Item -ItemType Directory -Force -Path "$sdk\build-tools" | Out-Null
    New-Item -ItemType Directory -Force -Path "$sdk\build-tools\34.0.0" | Out-Null
    Get-ChildItem "$btSrc" | ForEach-Object { Move-Item $_.FullName "$sdk\build-tools\34.0.0" -Force }
    Remove-Item "$root\tmp_bt" -Recurse -Force
}
Log "build-tools/aapt2: $(Test-Path "$sdk\build-tools\34.0.0\aapt2.exe")"

# ---------- 4. extract platform-tools (adb etc., AGP sanity) ----------
if (-not (Test-Path "$sdk\platform-tools\adb.exe")) {
    Log "extracting platform-tools..."
    Expand-Archive "$root\platform-tools-latest-windows.zip" -DestinationPath "$sdk" -Force
}

# ---------- 5. gradle ----------
if (-not (Test-Path "$root\gradle-8.7\bin\gradle.bat")) {
    Log "extracting Gradle 8.7..."
    Expand-Archive "$root\gradle-8.7-bin.zip" -DestinationPath "$root" -Force
}
if (-not (Test-Path "$root\gradle-8.7\bin\gradle.bat")) { throw "gradle.bat missing - zip extraction failed!" }

# ---------- 6. project local.properties ----------
Set-Content "$proj\local.properties" ("sdk.dir=" + ($sdk -replace "\\", "/"))

# ---------- 7. build ----------
Log "building APK..."
Get-ChildItem env: | Where-Object Name -in @("JAVA_HOME","JAVA_OPTS","JAVA_TOOL_OPTIONS") | Format-Table -AutoSize
& "$root\gradle-8.7\bin\gradle.bat" -p $proj assembleDebug --no-daemon --console=plain 2>&1 | Tee-Object -FilePath "$root\gradle.log" | Select-Object -Last 20

Log "==== RESULT ===="
if (Test-Path "$proj\app\build\outputs\apk\debug\app-debug.apk") {
    $a = Get-Item "$proj\app\build\outputs\apk\debug\app-debug.apk"
    Log ("SUCCESS: " + $a.FullName + "  (" + [math]::Round($a.Length/1MB,2) + " MB)")
} else {
    Log "FAILED - inspect $root\gradle.log"
    exit 1
}
