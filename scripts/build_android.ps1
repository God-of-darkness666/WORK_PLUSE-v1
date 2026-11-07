# Builds HRMonitor Mobile APK without Android Studio (idempotent, resumed).
$ErrorActionPreference = "Continue"
$root = "C:\work\android-build"
$proj = "C:\work\hr-monitor\mobile"
New-Item -ItemType Directory -Force -Path $root | Out-Null

# network fix + diagnostics for Java-based tools (sdkmanager / gradle)
$env:JAVA_TOOL_OPTIONS = "-Djava.net.preferIPv4Stack=true -Dhttp.timeout=30000 -Dsun.net.client.defaultConnectTimeout=30000"

function Log($m) { Write-Host ("[build-apk] " + $m) }

# ---------- 1. JDK 17 (reuse if present) ----------
$jdkDir = (Get-ChildItem $root -Directory -ErrorAction SilentlyContinue | Where-Object Name -like "jdk-17*" | Select-Object -First 1).FullName
if (-not $jdkDir) {
    Log "downloading JDK 17 (Adoptium)..."
    $assets = Invoke-RestMethod "https://api.adoptium.net/v3/assets/latest/17/hotspot?architecture=x64&image_type=jdk&os=windows"
    $link = $assets[0].binary.package.link
    Invoke-WebRequest -Uri $link -OutFile "$root\jdk.zip" -UseBasicParsing
    Expand-Archive "$root\jdk.zip" -DestinationPath "$root" -Force
    $jdkDir = (Get-ChildItem $root -Directory | Where-Object Name -like "jdk-17*" | Select-Object -First 1).FullName
}
Log "JDK dir: $jdkDir"
$env:JAVA_HOME = $jdkDir

# ---------- 2. Android SDK cmdline tools (reuse if present) ----------
$sdkm = "$root\sdk\cmdline-tools\latest\bin\sdkmanager.bat"
if (-not (Test-Path $sdkm)) {
    if (-not (Test-Path "$root\cmdtools.zip")) {
        Log "downloading Android cmdline tools..."
        Invoke-WebRequest -Uri "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip" -OutFile "$root\cmdtools.zip" -UseBasicParsing
    }
    Expand-Archive "$root\cmdtools.zip" -DestinationPath "$root" -Force
    New-Item -ItemType Directory -Force -Path "$root\sdk\cmdline-tools" | Out-Null
    Move-Item "$root\cmdline-tools" "$root\sdk\cmdline-tools\latest" -Force
}
$sdk = "$root\sdk"

Log "accepting SDK licenses (with IPv4 fix)..."
$yesFile = "$root\yes.txt"
if (-not (Test-Path $yesFile)) { Set-Content $yesFile ("y`r`n" * 40) }
cmd /c "`"$yesFile`" | `"$sdkm`" --licenses --sdk_root=$sdk" 2>&1 | Tee-Object -FilePath "$root\licenses.log" | Out-Null
if ($LASTEXITCODE -ne 0) { Log "LICENSE step exit code: $LASTEXITCODE - see $root\licenses.log" }
Log "licenses done (exit $LASTEXITCODE)"

Log "installing SDK packages: platform-tools, android-34, build-tools 34..."
cmd /c "echo y | `"$sdkm`" --sdk_root=$sdk `"platform-tools`" `"platforms;android-34`" `"build-tools;34.0.0`"" 2>&1 | Tee-Object -FilePath "$root\packages.log" | Out-Null
Log "packages step exit code: $LASTEXITCODE"
if (Test-Path "$sdk\platforms\android-34") { Log "platform android-34 INSTALLED" } else { Log "platform android-34 MISSING" }

# ---------- 3. Gradle 8.7 (reuse if present) ----------
if (-not (Test-Path "$root\gradle-8.7\bin\gradle.bat")) {
    Log "downloading Gradle 8.7..."
    Invoke-WebRequest -Uri "https://services.gradle.org/distributions/gradle-8.7-bin.zip" -OutFile "$root\gradle.zip" -UseBasicParsing
    Expand-Archive "$root\gradle.zip" -DestinationPath "$root" -Force
}

# ---------- 4. project sdk.dir ----------
$sdkProp = $sdk -replace "\\", "/"
Set-Content "$proj\local.properties" ("sdk.dir=" + $sdkProp)

# ---------- 5. build ----------
Log "building APK (this step downloads ~400 MB of Gradle/Kotlin/AGP deps)..."
$env:JAVA_HOME = $jdkDir
$env:JAVA_OPTS = "-Djava.net.preferIPv4Stack=true"
& "$root\gradle-8.7\bin\gradle.bat" -p $proj assembleDebug --no-daemon 2>&1 | Tee-Object -FilePath "$root\gradle.log" | Select-Object -Last 25

Log "==== RESULT ===="
if (Test-Path "$proj\app\build\outputs\apk\debug\app-debug.apk") {
    $a = Get-Item "$proj\app\build\outputs\apk\debug\app-debug.apk"
    Log ("SUCCESS: " + $a.FullName + "  (" + [math]::Round($a.Length/1MB,1) + " MB)")
} else {
    Log "FAILED - no APK; inspect $root\gradle.log and $root\packages.log"
}
