param(
  [string]$ServerUrl = "http://localhost:8000"
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$writer = Join-Path $here "write_usb.ps1"

function Test-Admin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $p = New-Object Security.Principal.WindowsPrincipal($id)
  return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
  $script = $MyInvocation.MyCommand.Path
  Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
  exit
}

function Get-UsbDisks {
  try {
    return @(Get-Disk | Where-Object { $_.BusType -eq "USB" } | Select-Object Number, FriendlyName, @{N = "SizeGB"; E = { [int]($_.Size / 1GB) } })
  } catch {
    return @()
  }
}

$form = New-Object Windows.Forms.Form
$form.Text = "AI PC Agent — флешка"
$form.Size = New-Object Drawing.Size(540, 480)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false

$script:y = 16
function Add-Label($text) {
  $l = New-Object Windows.Forms.Label
  $l.Text = $text
  $l.Location = New-Object Drawing.Point(16, $script:y)
  $l.AutoSize = $true
  $form.Controls.Add($l)
  $script:y += 22
  return $l
}
function Add-Box($default) {
  $t = New-Object Windows.Forms.TextBox
  $t.Location = New-Object Drawing.Point(16, $script:y)
  $t.Width = 490
  if ($default) { $t.Text = $default }
  $form.Controls.Add($t)
  $script:y += 28
  return $t
}

Add-Label "Сделать загрузочную флешку"
Add-Label "Windows ISO не нужен. Его потом скачает Grok Bot на пустой ПК."
Add-Label "Адрес сервера"
$urlBox = Add-Box $ServerUrl
Add-Label "Токен с сайта"
$tokenBox = Add-Box ""
Add-Label "Флешка (будет стёрта!)"

$combo = New-Object Windows.Forms.ComboBox
$combo.Location = New-Object Drawing.Point(16, $script:y)
$combo.Width = 490
$combo.DropDownStyle = "DropDownList"
$form.Controls.Add($combo)
$script:y += 36

$refresh = New-Object Windows.Forms.Button
$refresh.Text = "Обновить список флешек"
$refresh.Location = New-Object Drawing.Point(16, $script:y)
$refresh.Width = 220
$form.Controls.Add($refresh)
$script:y += 40

$log = New-Object Windows.Forms.TextBox
$log.Multiline = $true
$log.ScrollBars = "Vertical"
$log.ReadOnly = $true
$log.Location = New-Object Drawing.Point(16, $script:y)
$log.Size = New-Object Drawing.Size(490, 140)
$form.Controls.Add($log)
$script:y += 150

$write = New-Object Windows.Forms.Button
$write.Text = "Записать флешку"
$write.Location = New-Object Drawing.Point(16, $script:y)
$write.Width = 220
$write.Height = 32
$form.Controls.Add($write)

$script:disks = @()
function Write-Log($text) {
  $log.AppendText($text + [Environment]::NewLine)
  $log.SelectionStart = $log.Text.Length
  $log.ScrollToCaret()
  [Windows.Forms.Application]::DoEvents()
}

function Refresh-Disks {
  $script:disks = @(Get-UsbDisks)
  $combo.Items.Clear()
  foreach ($d in $script:disks) {
    [void]$combo.Items.Add(("Диск {0} — {1} ({2} ГБ)" -f $d.Number, $d.FriendlyName, $d.SizeGB))
  }
  if ($combo.Items.Count -gt 0) { $combo.SelectedIndex = 0 }
  else { [void]$combo.Items.Add("USB не найдена"); $combo.SelectedIndex = 0 }
  Write-Log ("Найдено USB: {0}" -f $script:disks.Count)
}

$refresh.Add_Click({ Refresh-Disks })
$write.Add_Click({
    if (-not $script:disks -or $script:disks.Count -eq 0) {
      [Windows.Forms.MessageBox]::Show("Вставь USB и нажми Обновить.")
      return
    }
    $token = $tokenBox.Text.Trim()
    $url = $urlBox.Text.Trim()
    if (-not $token) {
      [Windows.Forms.MessageBox]::Show("Вставь токен с сайта.")
      return
    }
    $num = [int]$script:disks[$combo.SelectedIndex].Number
    $ok = [Windows.Forms.MessageBox]::Show("Диск $num будет полностью стёрт. Продолжить?", "Стереть флешку?", "YesNo")
    if ($ok -ne "Yes") { return }
    Write-Log "Пишу... не вынимай флешку. ISO не нужен."
    if (-not (Test-Path $writer)) {
      $dl = $url.TrimEnd("/") + "/usb-maker/write_usb.ps1"
      Write-Log "Скачиваю write_usb.ps1 с $dl"
      Invoke-WebRequest -Uri $dl -OutFile $writer -UseBasicParsing
    }
    $root = Split-Path -Parent $here
    $args = @(
      "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $writer,
      "-DiskNumber", "$num",
      "-Token", $token,
      "-ServerUrl", $url,
      "-ProjectRoot", $root
    )
    $p = Start-Process -FilePath "powershell.exe" -ArgumentList $args -Wait -PassThru -NoNewWindow -RedirectStandardOutput (Join-Path $env:TEMP "ai-usb-out.txt") -RedirectStandardError (Join-Path $env:TEMP "ai-usb-err.txt")
    $outFile = Join-Path $env:TEMP "ai-usb-out.txt"
    $errFile = Join-Path $env:TEMP "ai-usb-err.txt"
    if (Test-Path $outFile) { Write-Log (Get-Content $outFile -Raw) }
    if (Test-Path $errFile) { $e = Get-Content $errFile -Raw; if ($e) { Write-Log $e } }
    if ($p.ExitCode -ne 0) {
      [Windows.Forms.MessageBox]::Show("Ошибка записи. Смотри лог. Нужны права администратора и WinRE на этом ПК.")
      return
    }
    [Windows.Forms.MessageBox]::Show("Флешка готова.`n1. Вставь в пустой ПК`n2. Boot from USB`n3. Интернет`n4. На сайте появится компьютер`n5. Grok скачает Windows сам")
  })

try {
  Refresh-Disks
  [Windows.Forms.Application]::Run($form)
} catch {
  [Windows.Forms.MessageBox]::Show("Ошибка: $($_.Exception.Message)")
}
