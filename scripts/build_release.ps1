param(
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not $SkipInstall) {
    python -m pip install '.[build]'
}

$separator = [IO.Path]::PathSeparator
$arguments = @(
    '--noconfirm', '--clean', '--onefile', '--windowed', '--name', 'QuickLookup',
    '--add-data', "offline_dictionary.json$separator.",
    '--add-data', "themes.json$separator.",
    '--add-data', "quick_lookup_config.json$separator.",
    '--collect-submodules', 'pynput',
    '--collect-submodules', 'pyttsx3',
    'quick_translate.py'
)

python -m PyInstaller @arguments

if (-not (Test-Path -LiteralPath '.\dist\QuickLookup.exe')) {
    throw '打包失败：未生成 dist\QuickLookup.exe'
}

Write-Host '打包完成：dist\QuickLookup.exe'
