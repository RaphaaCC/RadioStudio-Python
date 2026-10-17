$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $root ".venv\Scripts\python.exe"
$deploy = Join-Path $root ".venv\Scripts\pyside6-deploy.exe"
$iscc = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

if (-not (Test-Path -LiteralPath $python)) { throw "Ambiente do projeto não encontrado: $python" }
if (-not (Test-Path -LiteralPath $deploy)) { throw "pyside6-deploy não encontrado: $deploy" }
if (-not $iscc) { throw "Inno Setup 6 não encontrado. Instale o Inno Setup antes de gerar o Setup.exe." }

$releaseDir = Join-Path $root "release"
$buildDir = Join-Path $root "build"
$configDir = Join-Path $buildDir "release-config"
$distDir = Join-Path $buildDir "dist"
$portableName = "RadioStudio-Python (1.0.0 Sirius Beta) - Portable.exe"
$setupName = "RadioStudio-Python (1.0.0 Sirius Beta) - Setup.exe"
$assetsDir = Join-Path $root "radiostudio\assets"
$iconFile = Join-Path $assetsDir "icon.ico"
$portableEntry = Join-Path $root "packaging\portable_entry.py"
$installerEntry = Join-Path $root "packaging\installer_entry.py"

New-Item -ItemType Directory -Force -Path $releaseDir, $buildDir, $configDir, $distDir | Out-Null

function Remove-BuildDirectory([string]$path) {
    $resolvedBuild = [System.IO.Path]::GetFullPath($buildDir).TrimEnd('\') + '\'
    $resolvedTarget = [System.IO.Path]::GetFullPath($path)
    if (-not $resolvedTarget.StartsWith($resolvedBuild, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Recusa de remover pasta fora de build: $resolvedTarget"
    }
    if (Test-Path -LiteralPath $resolvedTarget) {
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }
}

function Write-DeployConfig(
    [string]$path,
    [string]$title,
    [string]$entry,
    [string]$outputDir,
    [string]$mode
) {
    $extraArgs = @(
        "--quiet",
        "--assume-yes-for-downloads",
        "--noinclude-qt-translations",
        "--windows-console-mode=disable",
        '--company-name="Raemi Labs"',
        '--product-name="RadioStudio Python 1.0.0 Sirius Beta"',
        "--file-version=1.0.0.0",
        "--product-version=1.0.0.0",
        '--file-description="RadioStudio Python"',
        '--copyright="Copyright (C) 2026 Raemi Labs"',
        "--include-data-dir=`"$assetsDir=radiostudio/assets`"",
        "--include-package-data=keyring",
        "--include-module=keyring.backends.Windows",
        "--include-distribution-metadata=keyring"
    ) -join " "

    $contents = @"
[app]
title = $title
project_dir = ..\..
input_file = $entry
exec_directory = $outputDir
project_file =
icon = $iconFile

[python]
packages = Nuitka==4.3.1
android_packages =

[qt]
qml_files =
excluded_qml_plugins =
modules =
plugins =

[android]
wheel_pyside =
wheel_shiboken =
plugins =

[ios]
wheel_pyside =
wheel_shiboken =
xcframework_path =
bundle_id =
team_id =
version = 1.0
deployment_target =
entitlements =
signing_style = Automatic
header_search_paths =

[nuitka]
macos.permissions =
mode = $mode
extra_args = $extraArgs

[buildozer]
mode = debug
recipe_dir =
jars_dir =
ndk_path =
sdk_path =
local_libs =
arch =
"@
    [System.IO.File]::WriteAllText($path, $contents, [System.Text.UTF8Encoding]::new($false))
}

$originalAppData = $env:APPDATA
$originalQtPlatform = $env:QT_QPA_PLATFORM
$originalPythonPath = $env:PYTHONPATH
Push-Location $root
try {
    if (-not (Test-Path -LiteralPath $iconFile)) { throw "Ícone do programa ausente: $iconFile" }

    Remove-Item -LiteralPath (Join-Path $releaseDir $portableName) -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $releaseDir $setupName) -Force -ErrorAction SilentlyContinue
    Remove-BuildDirectory (Join-Path $distDir "RadioStudio Python")
    Remove-BuildDirectory (Join-Path $distDir "RadioStudio Python.dist")
    Remove-BuildDirectory (Join-Path $buildDir "portable-smoke")
    Remove-BuildDirectory (Join-Path $buildDir "release-smoke-profile")

    $installerConfig = Join-Path $configDir "installer.pysidedeploy.spec"
    Write-DeployConfig $installerConfig "RadioStudio Python" $installerEntry $distDir "standalone"
    $env:PYTHONPATH = $root
    & $deploy -c $installerConfig --mode standalone --no-install --no-warn
    if ($LASTEXITCODE -ne 0) { throw "Falha ao compilar o pacote do instalador (pyside6-deploy: $LASTEXITCODE)." }

    $installerFolder = Join-Path $distDir "RadioStudio Python.dist"
    $compiledInstallerExe = Join-Path $installerFolder "installer_entry.exe"
    $installerExe = Join-Path $installerFolder "RadioStudio Python.exe"
    if (-not (Test-Path -LiteralPath $compiledInstallerExe)) {
        throw "Executável compilado para o instalador não encontrado: $compiledInstallerExe"
    }
    Move-Item -LiteralPath $compiledInstallerExe -Destination $installerExe

    $env:QT_QPA_PLATFORM = "offscreen"
    $testProfile = Join-Path $buildDir "release-smoke-profile"
    New-Item -ItemType Directory -Force -Path $testProfile | Out-Null
    $env:APPDATA = $testProfile
    $installerCheck = Start-Process -FilePath $installerExe -ArgumentList "--self-test" `
        -Wait -PassThru -WindowStyle Hidden
    if ($installerCheck.ExitCode -ne 0) {
        $log = Join-Path $testProfile "RadioStudioPython\app.log"
        if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 60 }
        throw "A verificação do executável instalado falhou (código $($installerCheck.ExitCode))."
    }
    Remove-BuildDirectory $testProfile

    $portableConfig = Join-Path $configDir "portable.pysidedeploy.spec"
    Write-DeployConfig $portableConfig "RadioStudio-Python (1.0.0 Sirius Beta) - Portable" `
        $portableEntry $releaseDir "onefile"
    & $deploy -c $portableConfig --mode onefile --no-install --no-warn
    if ($LASTEXITCODE -ne 0) { throw "Falha ao compilar o executável portátil (pyside6-deploy: $LASTEXITCODE)." }

    $portableExe = Join-Path $releaseDir $portableName
    if (-not (Test-Path -LiteralPath $portableExe)) { throw "Executável portátil não encontrado: $portableExe" }

    $smokeDir = Join-Path $buildDir "portable-smoke"
    New-Item -ItemType Directory -Force -Path $smokeDir | Out-Null
    Copy-Item -LiteralPath $portableExe -Destination (Join-Path $smokeDir $portableName)
    $portableProfile = Join-Path $buildDir "portable-profile"
    Remove-BuildDirectory $portableProfile
    New-Item -ItemType Directory -Force -Path $portableProfile | Out-Null
    $env:APPDATA = $portableProfile
    $portableCheck = Start-Process -FilePath (Join-Path $smokeDir $portableName) `
        -ArgumentList "--self-test" -Wait -PassThru -WindowStyle Hidden
    if ($portableCheck.ExitCode -ne 0) {
        $log = Join-Path $smokeDir "RadioStudioPythonData\app.log"
        if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 60 }
        throw "A verificação do executável portátil falhou (código $($portableCheck.ExitCode))."
    }
    Remove-BuildDirectory $portableProfile
    Remove-BuildDirectory $smokeDir

    & $iscc (Join-Path $root "packaging\installer.iss")
    if ($LASTEXITCODE -ne 0) { throw "Falha ao compilar o instalador (Inno Setup: $LASTEXITCODE)." }

    $setup = Join-Path $releaseDir $setupName
    if (-not (Test-Path -LiteralPath $setup)) { throw "Setup.exe não foi criado: $setup" }
    Get-Item -LiteralPath $portableExe, $setup | Select-Object FullName, Length
    $metadata = foreach ($item in (Get-Item -LiteralPath $portableExe, $installerExe, $setup)) {
        $versionInfo = $item.VersionInfo
        [pscustomobject]@{
            File = $item.Name
            Company = $versionInfo.CompanyName.Trim()
            Product = $versionInfo.ProductName.Trim()
            Description = $versionInfo.FileDescription.Trim()
            FileVersion = $versionInfo.FileVersion.Trim()
        }
    }
    $metadata | Format-Table -AutoSize
}
finally {
    Pop-Location
    if ($null -eq $originalQtPlatform) { Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue }
    else { $env:QT_QPA_PLATFORM = $originalQtPlatform }
    if ($null -eq $originalAppData) { Remove-Item Env:\APPDATA -ErrorAction SilentlyContinue }
    else { $env:APPDATA = $originalAppData }
    if ($null -eq $originalPythonPath) { Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue }
    else { $env:PYTHONPATH = $originalPythonPath }
}
