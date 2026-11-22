<#
  Сборка MSI Word-Add-in-Copilot-Public (ТЗ, раздел 10).

    powershell -ExecutionPolicy Bypass -File installer\build.ps1 [-Version 0.1.0] [-Scope both|machine|user] -Pfx D:\keys\org.pfx
    powershell -ExecutionPolicy Bypass -File installer\build.ps1 -AllowUnsigned  # только локальный пилот
    powershell -ExecutionPolicy Bypass -File installer\build.ps1 -Pfx D:\keys\org.pfx -PfxPassword ***

  Результат в installer\out\:
    WordAddinCopilotPublic-<версия>.msi       для всех пользователей, нужны права администратора
    WordAddinCopilotPublic-<версия>-user.msi  для текущего пользователя, без прав администратора

  Нужно: .venv с зависимостями проекта и PyInstaller, Node.js, WiX Toolset 3.x
  (build\tools\wix314, переменная WIX или параметр -WixDir).
  Релизная сборка требует -Pfx; .pfx хранится вне репозитория. -AllowUnsigned разрешён только для локального пилота.
  Файл сохранён в UTF-8 с BOM: иначе Windows PowerShell 5.1 прочитает кириллицу как ANSI.
#>
param(
  [string]$Version = "0.1.0",
  [ValidateSet("both", "machine", "user")][string]$Scope = "both",
  [string]$WixDir = "",
  [string]$Pfx = "",
  [string]$PfxPassword = "",
  [string]$TimestampUrl = "http://timestamp.digicert.com",
  [switch]$AllowUnsigned
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$build = Join-Path $root "build"
$out = Join-Path $PSScriptRoot "out"
$app = Join-Path $build "pyi\WordAddinCopilotPublic"
$scopes = if ($Scope -eq "both") { @("machine", "user") } else { @($Scope) }

if (-not $Pfx -and -not $AllowUnsigned) {
  throw "Релизная сборка без подписи запрещена. Передайте -Pfx или явно используйте -AllowUnsigned для локального пилота."
}
if ($Pfx -and -not (Test-Path -LiteralPath $Pfx -PathType Leaf)) {
  throw "Файл сертификата подписи не найден: $Pfx"
}

if (-not $WixDir) {
  $WixDir = if ($env:WIX) { Join-Path $env:WIX "bin" } else { Join-Path $build "tools\wix314" }
}
foreach ($tool in "heat.exe", "candle.exe", "light.exe") {
  if (-not (Test-Path (Join-Path $WixDir $tool))) { throw "Не найден WiX: $(Join-Path $WixDir $tool)" }
}

function Invoke-Step([string]$Name, [scriptblock]$Block) {
  Write-Host "==> $Name"
  & $Block
  if ($LASTEXITCODE) { throw "${Name}: код возврата $LASTEXITCODE" }
}

$signtool = $null
if ($Pfx) {
  $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
    Where-Object FullName -match "\\x64\\" | Select-Object -Last 1 -ExpandProperty FullName
  if (-not $signtool) { throw "signtool не найден: установите Windows SDK (Signing Tools)" }
}
$pilotSuffix = if ($signtool) { "" } else { "-unsigned-pilot" }
$artifacts = [System.Collections.Generic.List[string]]::new()

Invoke-Step "Панель (tsc)" { Push-Location (Join-Path $root "addin"); try { npm run build } finally { Pop-Location } }
Invoke-Step "Иконки" { & $py (Join-Path $root "tools\make_icons.py") }

Invoke-Step "Приложение (PyInstaller)" {
  & $py -m PyInstaller --noconfirm --clean --log-level WARN --noconsole --onedir `
    --name WordAddinCopilotPublic `
    --icon (Join-Path $root "tray\assets\app.ico") `
    --distpath (Join-Path $build "pyi") --workpath (Join-Path $build "pyi-work") --specpath $build `
    --paths $root `
    --add-data "$(Join-Path $root 'actions.yaml');." `
    --add-data "$(Join-Path $root 'addin\dist');addin\dist" `
    --add-data "$(Join-Path $root 'tray\assets');tray\assets" `
    --collect-submodules uvicorn `
    --collect-all markitdown `
    --collect-submodules pymupdf `
    --collect-submodules fitz `
    --collect-submodules pytesseract `
    --hidden-import olefile `
    --hidden-import striprtf.striprtf `
    --hidden-import pystray._win32 `
    (Join-Path $PSScriptRoot "entry.py")
}

if ($signtool) {
  $exe = Join-Path $app "WordAddinCopilotPublic.exe"
  Invoke-Step "Подпись [EXE]" { & $signtool sign /fd SHA256 /td SHA256 /tr $TimestampUrl /f $Pfx /p $PfxPassword $exe }
  Invoke-Step "Проверка подписи [EXE]" { & $signtool verify /pa $exe }
}

Invoke-Step "Самопроверка exe" {
  $p = Start-Process -FilePath (Join-Path $app "WordAddinCopilotPublic.exe") -ArgumentList "--selftest" -Wait -PassThru -WindowStyle Hidden
  if ($p.ExitCode -ne 0) { throw "WordAddinCopilotPublic.exe --selftest: код $($p.ExitCode)" }
}

$wixObj = Join-Path $build "wix"
New-Item -ItemType Directory -Force $wixObj, $out | Out-Null
Invoke-Step "Сбор файлов (heat)" {
  & (Join-Path $WixDir "heat.exe") dir $app -nologo -cg AppFiles -dr AppDir -srd -sreg -scom -ag `
    -var var.AppSource -out (Join-Path $wixObj "AppFiles.wxs")
}

foreach ($s in $scopes) {
  $obj = Join-Path $wixObj $s
  New-Item -ItemType Directory -Force $obj | Out-Null
  $msi = Join-Path $out ($(if ($s -eq "user") { "WordAddinCopilotPublic-$Version-user$pilotSuffix.msi" } else { "WordAddinCopilotPublic-$Version$pilotSuffix.msi" }))
  # Установка в профиль пользователя: файлы — ключевые пути компонентов (ICE38/64/91 рассчитаны на реестр).
  $sice = if ($s -eq "user") { @("-sice:ICE38", "-sice:ICE64", "-sice:ICE91") } else { @() }

  Invoke-Step "Компиляция [$s] (candle)" {
    & (Join-Path $WixDir "candle.exe") -nologo -arch x64 -ext WixUtilExtension `
      "-dScope=$s" "-dVersion=$Version" "-dAppSource=$app" `
      "-dManifestFile=$(Join-Path $root 'addin\manifest.xml')" "-dIconFile=$(Join-Path $root 'tray\assets\app.ico')" `
      -out "$obj\" (Join-Path $PSScriptRoot "Product.wxs") (Join-Path $wixObj "AppFiles.wxs")
  }
  Invoke-Step "Сборка MSI [$s] (light)" {
    & (Join-Path $WixDir "light.exe") -nologo -ext WixUtilExtension -spdb @sice -out $msi `
      (Join-Path $obj "Product.wixobj") (Join-Path $obj "AppFiles.wixobj")
  }
  if ($signtool) {
    Invoke-Step "Подпись [$s]" { & $signtool sign /fd SHA256 /td SHA256 /tr $TimestampUrl /f $Pfx /p $PfxPassword $msi }
    Invoke-Step "Проверка подписи [$s]" { & $signtool verify /pa $msi }
  }
  $artifacts.Add($msi)
  Write-Host "Готово: $msi"
}
if (-not $signtool) {
  Write-Warning "ПИЛОТНАЯ СБОРКА: EXE и MSI не подписаны. Не распространяйте её как релиз."
}

$sbom = Join-Path $out "WordAddinCopilotPublic-$Version$pilotSuffix-sbom.cdx.json"
Invoke-Step "SBOM" { & $py (Join-Path $root "tools\generate_sbom.py") --version $Version --output $sbom }
$artifacts.Add($sbom)
$hashes = $artifacts | Sort-Object | ForEach-Object {
  $item = Get-Item -LiteralPath $_
  $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $item.FullName).Hash.ToLowerInvariant()
  "$hash  $($item.Name)"
}
Set-Content -LiteralPath (Join-Path $out "SHA256SUMS.txt") -Value $hashes -Encoding ascii
Write-Host "Готово: $(Join-Path $out 'SHA256SUMS.txt')"
