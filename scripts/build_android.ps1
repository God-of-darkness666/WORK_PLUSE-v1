# Builds HRMonitor Mobile APK without Android Studio (fully automated).
# Installs JDK17 + Android SDK cmdline tools + Gradle 8.7 under C:\work\android-build
$ErrorActionPreference = "Continue"
$root = "C:\work\android-build"
$proj = "C:\work\hr-monitor\mobile"
New-Item -ItemType Directory -Force -Path $root | Out-Null

function Log($m) { Write-Host ("[build-apk] " + $m) }

# ---------- 1. JDK 17 ----------
Log "downloading JDK 17 (Adoptium)..."
$assets = Invoke-RestMethod "https://api.adoptium.net/v3/assets/latest/17/hotspot?architecture=x64&image_type=jdk&os=windows"
$link = $assets[0].binary.package.link
Log "JDK url: $link"
Invoke-WebRequest -Uri $link -OutFile "$root\jdk.zip" -UseBasicParsing
Expand-Archive "$root\jdk.zip" -DestinationPath "$root" -Force
$jdkDir = (Get-ChildItem $root -Directory | Where-Object Name -like "jdk-17*" | Select-Object -First 1).FullName
Log "JDK dir: $jdkDir"
$env:JAVA_HOME = $jdkDir

# ---------- 2. Android SDK cmdline tools ----------
Log "downloading Android cmdline tools..."
Invoke-WebRequest -Uri "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip" -OutFile "$root\cmdtools.zip" -UseBasicParsing
Expand-Archive "$root\cmdtools.zip" -DestinationPath "$root" -Force
$sdk = "$root\sdk"
New-Item -ItemType Directory -Force -Path "$sdk\cmdline-tools" | Out-Null
Move-Item "$root\cmdline-tools" "$sdk\cmdline-tools\latest" -Force
$sdkm = "$sdk\cmdline-tools\latest\bin\sdkmanager.bat"

Log "accepting SDK licenses..."
$yesFile = "$root\yes.txt"
Set-Content $yesFile ("y`r`n" * 40)
Get-Content $yesFile | & $sdkm --licenses --sdk_root=$sdk | Out-Null

Log "installing SDK packages (platform-tools, android-34, build-tools 34)..."
& $sdkm --sdk_root=$sdk "platform-tools" "platforms;android-34" "build-tools;34.0.0" | Out-Null

# ---------- 3. Gradle 8.7 ----------
Log "downloading Gradle 8.7..."
Invoke-WebRequest -Uri "https://services.gradle.org/distributions/gradle-8.7-bin.zip" -OutFile "$root\gradle.zip" -UseBasicParsing
Expand-Archive "$root\gradle.zip" -DestinationPath "$root" -Force

# ---------- 4. project sdk.dir ----------
$sdkProp = $sdk -replace "\\", "/"
Set-Content "$proj\local.properties" ("sdk.dir=" + $sdkProp)
Log "local.properties -> $sdkProp"

# ---------- 5. build ----------
Log "building APK..."
$env:JAVA_HOME = $jdkDir
& "$root\gradle-8.7\bin\gradle.bat" -p $proj assembleDebug --no-daemon

Log "==== RESULT ===="
if (Test-Path "$proj\app\build\outputs\apk\debug\app-debug.apk") {
    $a = Get-Item "$proj\app\build\outputs\apk\debug\app-debug.apk"
    Log ("SUCCESS: " + $a.FullName + "  (" + [math]::Round($a.Length/1MB,1) + " MB)")
} else {
    Log "FAILED - no APK produced; check gradle output above."
}
