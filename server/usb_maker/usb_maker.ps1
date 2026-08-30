param(
  [string]$ServerUrl = "http://localhost:8000",
  [string]$Token = ""
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
  $argList = "-STA -NoProfile -ExecutionPolicy Bypass -File `"$script`" -ServerUrl `"$ServerUrl`""
  if ($Token) { $argList += " -Token `"$Token`"" }
  Start-Process powershell.exe -Verb RunAs -ArgumentList $argList
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
$form.Text = "AI PC Agent - USB"
$form.Size = New-Object Drawing.Size(540, 420)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false

$script:y = 16
$script:serverUrl = $ServerUrl
$script:agentToken = $Token
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

Add-Label "Create bootable USB drive"
Add-Label "No Windows ISO needed. Grok Bot will download Windows on the target PC."

$urlBox = $null
$tokenBox = $null
if (-not $script:agentToken) {
  Add-Label "Server URL"
  $urlBox = Add-Box $ServerUrl
  Add-Label "Token from website"
  $tokenBox = Add-Box ""
}

Add-Label "USB drive (will be erased!)"

$combo = New-Object Windows.Forms.ComboBox
$combo.Location = New-Object Drawing.Point(16, $script:y)
$combo.Width = 490
$combo.DropDownStyle = "DropDownList"
$form.Controls.Add($combo)
$script:y += 36

$refresh = New-Object Windows.Forms.Button
$refresh.Text = "Refresh USB list"
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
$write.Text = "Write USB"
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
    [void]$combo.Items.Add(("Disk {0} - {1} ({2} GB)" -f $d.Number, $d.FriendlyName, $d.SizeGB))
  }
  if ($combo.Items.Count -gt 0) { $combo.SelectedIndex = 0 }
  else { [void]$combo.Items.Add("No USB drive found"); $combo.SelectedIndex = 0 }
  Write-Log ("USB drives found: {0}" -f $script:disks.Count)
}

$refresh.Add_Click({ Refresh-Disks })
$write.Add_Click({
    if (-not $script:disks -or $script:disks.Count -eq 0) {
      [Windows.Forms.MessageBox]::Show("Insert a USB drive and click Refresh.")
      return
    }
    $url = if ($urlBox) { $urlBox.Text.Trim() } else { $script:serverUrl }
    $token = if ($tokenBox) { $tokenBox.Text.Trim() } else { $script:agentToken }
    if (-not $token) {
      [Windows.Forms.MessageBox]::Show("Download usb-maker.bat from the website and run that file.")
      return
    }
    $num = [int]$script:disks[$combo.SelectedIndex].Number
    $ok = [Windows.Forms.MessageBox]::Show("Disk $num will be completely erased. Continue?", "Erase USB?", "YesNo")
    if ($ok -ne "Yes") { return }
    Write-Log "Writing... do not remove the USB drive. No ISO required."
    if (-not (Test-Path $writer)) {
      $dl = $url.TrimEnd("/") + "/usb-maker/write_usb.ps1"
      Write-Log "Downloading write_usb.ps1 from $dl"
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
      [Windows.Forms.MessageBox]::Show("Write failed. Check the log. Admin rights and WinRE are required on this PC.")
      return
    }
    [Windows.Forms.MessageBox]::Show("USB ready.`n1. Boot empty PC from USB`n2. Connect to the internet`n3. Device appears on the website`n4. Grok will download Windows")
  })

try {
  Refresh-Disks
  [Windows.Forms.Application]::Run($form)
} catch {
  [Windows.Forms.MessageBox]::Show("Error: $($_.Exception.Message)")
}
