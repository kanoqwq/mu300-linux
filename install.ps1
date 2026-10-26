<#
.SYNOPSIS
    MU300 / ZTE F50 Linux installer for Windows (same as install.sh on macOS/Linux).

.DESCRIPTION
    Run with the device booted in rooted Android and connected over USB (adb).
      .\install.ps1 -Check     only inspect the device; writes nothing
      .\install.ps1            install Ubuntu, OpenWrt or both from the prebuilt release images
      .\install.ps1 -Answers f.txt   take one answer per question from that file (run without a console; the file
                               holds the password in plain text, delete it afterwards)
      .\install.ps1 -Backup    only copy what an installation reads first to this computer: Android's boot image
                               and the first block of misc, checked against the device (writes nothing to it)
      .\install.ps1 -AdbPath   print the adb this installer would use, and stop
      .\install.ps1 -AnswersFile f.txt   ask nothing: every question is answered by its variable, from that file
                               (NAME=value lines) or the environment (-NonInteractive); a question without an answer
                               is an error that names the variable. $env:MU300_PROGRESS = '1' adds a machine-readable
                               progress stream. For programs that drive the installer: tools\answers.ps1 and
                               docs\INSTALLER-AUTOMATION.md. The variables are those of install.sh (its header
                               lists them), with MU300_LANG, MU300_RELEASE, MU300_RELEASE_URL and MU300_WORK for
                               -Lang, -Release, -ReleaseUrl and -Work.
      .\install.ps1 -NoBootchainSync   leave the Linux slot's boot chain as it is (for experts; the default copies
                               uboot, sml, teecfg, trustos and vbmeta of Android's slot there, backed up first)
      .\install.ps1 -Package DIR   install the system and the kernel of a release's Magisk zip unpacked at DIR, from
                               its files and without a download ($env:MU300_PACKAGE; the zip's own computer\install.ps1,
                               which its start.cmd runs, finds them by itself: START-HERE.md in the zip)

    Needs: adb. Python 3 and its lz4 module are installed for this user when missing (winget or python.org, then
    pip); Windows 10/11 provide tar and curl.
    The published images contain no proprietary files: the Wi-Fi/Bluetooth firmware and the Android modem/GPU
    userspace are pulled from *your* device into work\ and added during installation. A Windows file system
    cannot hold every name the Android subset contains (the property area is a set of files called
    u:object_r:<context>:s0), so the archive from the device is kept beside it as work\android-subset\
    windows-source.tar.gz, and the tools that build the images take the subset from that file.
    Started by a person at a console for a plain installation, it first offers the graphical installer of gui\, a
    window that drives this script ($env:MU300_GUI = '0': never ask, '1': start it without asking). The window needs
    a Python 3.8 or newer with tkinter; tools\gui-launch.ps1 looks for one and, with your yes, installs it with
    winget.
    Only the Linux region (or the SD card), the boot partition of the slot Android does not run from (boot_b, or
    boot_a when Android runs from b) and 32 bytes of misc are written; Android's boot partition, the GPT and
    userdata stay untouched. The one exception is asked for first and needs ERASE typed: where userdata fills the
    disk (the 32 GB variant), the installer offers to shrink it, which rewrites the GPT and erases Android's data -
    EXPERIMENTAL: Android's own recovery resets Android, and the installation resumes on the next run once Android is
    set up again with USB debugging and root (a transaction in ~\mu300-backups; -Resume, -NoResume, -Transaction FILE;
    tools\resume.py). With an SD card in the slot it asks where the Linux filesystem goes ($env:MU300_STORAGE =
    'internal' or 'sd' answers without asking).
    The VPN, when wanted, is the module of github.com/dikeckaan/mu300-linux-vpn: its latest release
    ($env:MU300_VPN_URL: another place with its files and SHA256SUMS; $env:MU300_VPN_MODULE: a copy on this computer).
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [string]$Release = '',   # empty: the newest published release
    [string]$ReleaseUrl,
    [string]$Repo = 'dikeckaan/mu300-linux',
    [string]$Work = '',      # empty: .\work next to this script
    [string]$Lang = '',      # en, tr or zh; empty: ask (English is the default)
    [string]$Answers = '',   # file with one answer per line: run without a console (see Ask; the password too)
    [string]$AnswersFile = '',  # file of NAME=value lines: every question answered by its variable (tools\answers.ps1)
    [switch]$NonInteractive, # the same, with the variables from the environment
    [switch]$Backup,         # only copy Android's boot image and the first block of misc to this computer
    [switch]$AdbPath,        # only print the adb that would be used
    [switch]$NoSelfUpdate,   # do not bring this copy up to date with GitHub first
    [switch]$NoBootchainSync, # leave the Linux slot's boot chain as it is ($env:MU300_SYNC_BOOTCHAIN = '0' too)
    [switch]$Resume,         # continue the installation prepared by making room, or stop when there is none
    [switch]$NoResume,       # install as if nothing had been prepared ($env:MU300_RESUME = '0' too)
    [string]$Transaction = '', # that transaction, not the newest one for this device ($env:MU300_TRANSACTION)
    [string]$Package = ''    # an unpacked Magisk zip of a release: its system and kernel, nothing downloaded
)

$ErrorActionPreference = 'Stop'
$T = '/data/local/tmp'
# $PSScriptRoot can be empty in Windows PowerShell 5.1 (param defaults, `powershell -File` from cmd.exe)
$Top = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
# the answers mode and the progress stream (Progress, Info, Step, AnswerNamed); an answers file sets MU300_* variables,
# so it is read before anything looks at one
. (Join-Path (Join-Path $Top 'tools') 'answers.ps1')
AnswersSetup $AnswersFile ([bool]$NonInteractive)
# An error nobody caught still ends the progress stream with an event - whoever reads it must not wait for more - and
# the script with status 1, saying where it was. (A trap that only passes the error on leaves the status of the last
# program that ran, which is 0 more often than not.)
trap {
    $where = "$_ ($(Split-Path -Leaf $_.InvocationInfo.ScriptName) line $($_.InvocationInfo.ScriptLineNumber))"
    Progress 'error' 'message' $where
    Write-Host "`nERROR: $where" -ForegroundColor Red
    exit 1
}
if (-not $Lang -and $env:MU300_LANG) { $Lang = $env:MU300_LANG }
if (-not $Release -and $env:MU300_RELEASE) { $Release = $env:MU300_RELEASE }
if (-not $ReleaseUrl -and $env:MU300_RELEASE_URL) { $ReleaseUrl = $env:MU300_RELEASE_URL }
if (-not $Work -and $env:MU300_WORK) { $Work = $env:MU300_WORK }
if (-not $Work) { $Work = Join-Path $Top 'work' }
# the USB network of each kind of device: F50 192.168.77.1, U30 Air 192.168.78.1, V50 192.168.79.1; $MU300_IP is set
# to the one that answers
$MU300_IP = '192.168.77.1'
function LinuxRunning {
    foreach ($ip in @('192.168.77.1', '192.168.78.1', '192.168.79.1')) {
        if (Test-NetConnection -ComputerName $ip -Port 22 -InformationLevel Quiet -WarningAction SilentlyContinue) { $script:MU300_IP = $ip; return $true }
    }
    return $false
}

# A console is not always available: a script driving the installer, a CI run, or a terminal that cannot answer
# the password prompt (Read-Host -AsSecureString refuses a redirected stdin and blocks instead). -Answers <file>
# then takes one answer per question, in the order they are asked, an empty line meaning "the default". The file
# holds the password in plain text, so delete it afterwards; secret answers are never echoed.
$script:AnswerQueue = $null
if ($Answers) {
    $q = New-Object 'System.Collections.Generic.Queue[string]'
    foreach ($line in [IO.File]::ReadAllLines($Answers, [Text.Encoding]::UTF8)) { $q.Enqueue($line) }
    $script:AnswerQueue = $q
}
function NextAnswer($question, [switch]$Secret) {
    if (-not $script:AnswerQueue -or $script:AnswerQueue.Count -eq 0) { Die (T 'no answer left for the question: {1}' $question) }
    $a = [string]$script:AnswerQueue.Dequeue()
    Write-Host ('  ' + $(if ($Secret) { '*' * $a.Length } else { $a }))
    $a
}

# Messages in other languages: i18n\<lang>.tsv, shared with install.sh (tools/i18n.sh) - one "English<TAB>
# translation" line per message, {1}.. for the arguments; a message without a line there stays in English. The
# dictionary is ordinal: PowerShell's own hashtables ignore case, and two messages may differ only in case.
$script:Msg = New-Object 'System.Collections.Generic.Dictionary[string,string]'
function LoadLanguage($l) {
    $script:Msg.Clear()
    $f = Join-Path $Top "i18n\$l.tsv"
    if ($l -eq 'en' -or -not (Test-Path $f)) { return }
    # ReadAllLines with UTF-8: Get-Content in Windows PowerShell 5.1 reads files as ANSI
    foreach ($line in [IO.File]::ReadAllLines($f, [Text.Encoding]::UTF8)) {
        $i = $line.IndexOf("`t")
        if ($i -gt 0 -and -not $line.StartsWith('#')) { $script:Msg[$line.Substring(0, $i)] = $line.Substring($i + 1) }
    }
    # Turkish and Chinese letters need a UTF-8 console (the default code page shows them as '?')
    try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
}
function T($key) {
    $s = if ($script:Msg.ContainsKey($key)) { $script:Msg[$key] } else { $key }
    for ($i = 0; $i -lt $args.Count; $i++) { $s = $s.Replace("{$($i + 1)}", [string]$args[$i]) }
    $s
}
# answers typed in the chosen language count as the English keywords the script compares with
function NormalizeAnswer([string]$a) {
    $yes = @('evet', 'e', 'y', "$([char]0x662F)", "$([char]0x662F)$([char]0x7684)", "$([char]0x597D)")
    $no = @(('hay' + [char]0x131 + 'r'), 'hayir', 'h', 'n', "$([char]0x5426)", "$([char]0x4E0D)", "$([char]0x4E0D)$([char]0x662F)")
    $upd = @(('g' + [char]0xFC + 'ncelle'), 'guncelle', "$([char]0x66F4)$([char]0x65B0)")
    $wipe = @('sil', "$([char]0x6E05)$([char]0x9664)", "$([char]0x64E6)$([char]0x9664)")
    if ($yes -contains $a) { return 'yes' }
    if ($no -contains $a) { return 'no' }
    if ($upd -contains $a) { return 'update' }
    if ($wipe -contains $a) { return 'wipe' }
    $a
}
if ($Lang -notin 'en', 'tr', 'zh' -and $AdbPath) { $Lang = 'en' }
if ($Lang -notin 'en', 'tr', 'zh' -and $script:MuAnswersMode) { AnswersFail 'no answer to the language question: set MU300_LANG to en, tr or zh' }
if ($Lang -notin 'en', 'tr', 'zh') {
    # the names in their own scripts, written as code points: this file stays ASCII for Windows PowerShell 5.1.
    # (Concatenations inside @(...) need parentheses: the comma binds tighter than +.)
    $tr = 'T' + [char]0xFC + 'rk' + [char]0xE7 + 'e'
    $zh = "$([char]0x4E2D)$([char]0x6587)"
    try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
    Write-Host "`n  1) English   2) $tr   3) $zh"
    $l = Read-Host "  Language / Dil / $([char]0x8BED)$([char]0x8A00) [1]"
    $Lang = switch ($l.Trim()) { { $_ -in '2', 'tr' } { 'tr' } { $_ -in '3', 'zh' } { 'zh' } default { 'en' } }
}
LoadLanguage $Lang
# cmd.exe runs a program from the current directory, PowerShell does not: with adb.exe next to the project (or
# in the directory the installer is started from) but not on PATH, `adb devices` worked in cmd and the installer
# said "adb not found". Look where people usually put platform-tools, and put the one found on PATH.
function FindAdb {
    if (Get-Command adb -CommandType Application -ErrorAction SilentlyContinue) { return }
    $base = if ($Top) { $Top } elseif ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $dirs = @($base, (Join-Path $base 'platform-tools'), (Get-Location).Path, (Join-Path (Get-Location).Path 'platform-tools'))
    if ($env:LOCALAPPDATA) { $dirs += Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools' }
    if ($env:ANDROID_HOME) { $dirs += Join-Path $env:ANDROID_HOME 'platform-tools' }
    if ($env:USERPROFILE) { $dirs += @((Join-Path $env:USERPROFILE 'platform-tools'), (Join-Path $env:USERPROFILE 'Downloads\platform-tools'), (Join-Path $env:USERPROFILE 'Desktop\platform-tools')) }
    $dirs += @('C:\platform-tools', 'C:\adb', 'C:\Android\platform-tools')
    foreach ($d in $dirs) {
        if ($d -and (Test-Path (Join-Path $d 'adb.exe'))) { $env:PATH = "$d;$env:PATH"; return }
    }
}

# Bring this copy of the project up to date with GitHub before doing anything (tools/self-update.sh does the same
# for install.sh): a git clone is fast-forwarded to GitHub's main, a downloaded zip gets the files that differ,
# with the commit checked kept in .mu300-source. Without GitHub the local copy runs; nothing here stops an install.
function SelfUpdate {
    if ($NoSelfUpdate -or $env:MU300_NO_SELF_UPDATE -or $env:MU300_SELF_UPDATED) { return $false }
    $ProgressPreference = 'SilentlyContinue'   # Windows PowerShell 5.1 downloads many times slower with the bar
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }
    try { $remote = (Invoke-RestMethod "https://api.github.com/repos/$Repo/commits/main" -UseBasicParsing -TimeoutSec 15).sha } catch { $remote = $null }
    if ($remote -notmatch '^[0-9a-f]{40}$') {
        Write-Host ('  ' + (T 'could not check GitHub for a newer installer; continuing with this copy'))
        return $false
    }
    $git = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ((Test-Path (Join-Path $Top '.git')) -and $git) {
        $ErrorActionPreference = 'Continue'
        $head = [string](& $git.Source -C $Top rev-parse HEAD 2>$null)
        if ($head.Trim() -eq $remote) { return $false }
        # a copy that already has GitHub's commit is ahead of it (someone working on the project): leave it alone
        & $git.Source -C $Top merge-base --is-ancestor $remote HEAD 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return $false }
        if ([string](& $git.Source -C $Top status --porcelain --untracked-files=no 2>$null)) {
            Write-Host ('  ' + (T 'a newer installer is on GitHub, but this copy has local changes; not updating it'))
            return $false
        }
        Say (T 'Updating the installer to the newest version from GitHub')
        $env:GIT_TERMINAL_PROMPT = '0'; $env:GIT_HTTP_LOW_SPEED_LIMIT = '1000'; $env:GIT_HTTP_LOW_SPEED_TIME = '20'
        & $git.Source -C $Top fetch -q "https://github.com/$Repo.git" main 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { & $git.Source -C $Top merge -q --ff-only FETCH_HEAD 2>$null | Out-Null }
        if ($LASTEXITCODE -ne 0) {
            Write-Host ('  ' + (T 'could not update this copy (it has commits of its own?); continuing with it'))
            return $false
        }
    } else {
        $stamp = Join-Path $Top '.mu300-source'
        if ((Test-Path $stamp) -and ((Get-Content $stamp -Raw).Trim() -eq $remote)) { return $false }
        Say (T 'Checking the installer against the newest version on GitHub')
        $tmp = Join-Path ([IO.Path]::GetTempPath()) ("mu300-src-" + [Guid]::NewGuid().ToString('N'))
        try {
            New-Item -ItemType Directory -Force -Path $tmp | Out-Null
            Invoke-WebRequest "https://codeload.github.com/$Repo/zip/$remote" -OutFile "$tmp\src.zip" -UseBasicParsing -TimeoutSec 600
            Expand-Archive -Path "$tmp\src.zip" -DestinationPath "$tmp\x" -Force
        } catch {
            Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
            Write-Host ('  ' + (T 'could not download it; continuing with this copy'))
            return $false
        }
        $src = (Get-ChildItem "$tmp\x" -Directory | Select-Object -First 1).FullName
        # Files listed in .mu300-keep stay as they are: without this the published copy would replace the
        # fixed scripts of this copy on the very next run, and the install would fail in the same place
        # again. The git branch above does the same by refusing to touch a copy with local changes.
        $keep = @()
        $keepFile = Join-Path $Top '.mu300-keep'
        if (Test-Path $keepFile) {
            $keep = @([IO.File]::ReadAllLines($keepFile, [Text.Encoding]::UTF8) |
                ForEach-Object { $_.Trim().Replace('\', '/') } | Where-Object { $_ -and -not $_.StartsWith('#') })
        }
        $n = 0; $kept = 0
        foreach ($f in Get-ChildItem $src -Recurse -File) {
            $rel = $f.FullName.Substring($src.Length + 1)
            if ($keep -contains $rel.Replace('\', '/')) { $kept++; continue }
            $dst = Join-Path $Top $rel
            if ((Test-Path $dst) -and (Get-FileHash $dst).Hash -eq (Get-FileHash $f.FullName).Hash) { continue }
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
            Copy-Item -Force $f.FullName $dst
            $n++
        }
        Set-Content -Path $stamp -Value $remote -Encoding Ascii
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        if ($kept -gt 0) { Write-Host ('  ' + (T 'this copy has changes of its own, so {1} files were left as they are' $kept)) }
        if ($n -eq 0) { return $false }
        Write-Host ('  ' + (T '{1} files updated' $n))
    }
    Write-Host ('  ' + (T 'restarting the updated installer'))
    return $true
}
function Say($m) { ProgressSay $m; Write-Host "`n==> $m" -ForegroundColor Cyan }
function Die($m) { Progress 'error' 'message' $m; Write-Host ("`n" + (T 'ERROR:') + " $m") -ForegroundColor Red; exit 1 }
# Windows PowerShell 5.1 turns every stderr line of a native command into an ErrorRecord once stderr is
# redirected, and with ErrorActionPreference Stop that aborts the script (adb's "daemon not running",
# "no devices", push progress). Run such commands with Continue and drop their stderr.
# The parameter must not share a name with any variable the blocks use: a scriptblock looks its variables up where
# it runs, names are case-insensitive, and with it called $Cmd, SuDo's { adb shell "su -c '$cmd'" } ran
# `su -c '<the text of the block>'` - so "su does not work on the device" on every Windows machine (issue #4).
function Quiet([scriptblock]$QuietBlock_) { $ErrorActionPreference = 'Continue'; & $QuietBlock_ 2>$null }
# A device running MU300 Linux is an adb device too: adb is on by default there (FINDINGS 70), and its USB serial
# number says which system answers: MU300LINUX-... in Linux (boot/init), the bare number in Android. It is never a
# device to install to, and nothing else would stop it: "su -c id -u" says 0 there as well (the adb shell is root).
# AndroidLines: the "adb devices -l" lines of the devices in Android that answer.
function AndroidLines($lines) { @($lines | Where-Object { $_ -match '^\S+\s+device\b' -and $_ -notmatch '^MU300LINUX' }) }
# LinuxAdbSerials: the serial numbers of the devices in Linux among them; -Open: only those that let this computer
# in (state "device"; "unauthorized" is an adb that takes authorised keys only)
function LinuxAdbSerials($lines, [switch]$Open) {
    @($lines | Where-Object { $_ -match '^MU300LINUX\S*\s+\S+' -and (-not $Open -or $_ -match '^\S+\s+device\b') } |
        ForEach-Object { ($_ -split '\s+')[0] })
}
# With more than one adb device attached (a phone, an emulator, a device over the network) every plain adb command
# fails with "more than one device/emulator", which read as "no adb device". Pick the F50 and point adb at it with
# ANDROID_SERIAL: the only device, else the only one that says it is an F50/MU300 or a U30 Air, else ask (-Quiet
# never asks).
function SelectDevice([switch]$Quiet) {
    if ($env:ANDROID_SERIAL) { return }
    $all = @(AndroidLines (Quiet { adb devices -l }))
    if ($all.Count -eq 0) { return }
    $f50 = @($all | Where-Object { $_ -match 'model:F50|product:MU300|device:MU300|device:U30Air|device:MU3351' })
    # the only adb device, and an F50/U30 Air: nothing to ask
    if ($all.Count -eq 1 -and $f50.Count -eq 1) { $env:ANDROID_SERIAL = ($all[0] -split '\s+')[0]; return }
    # -Quiet (waiting for the device to come back as Android): only the one F50/U30 Air, never a question.
    # Otherwise always ask: a phone or tablet next to it is what the installer must never write to.
    if ($Quiet) {
        if ($f50.Count -eq 1) { $env:ANDROID_SERIAL = ($f50[0] -split '\s+')[0] }
        return
    }
    Write-Host ('  ' + (T 'which adb device is the F50 or U30 Air?'))
    $def = 1
    for ($i = 0; $i -lt $all.Count; $i++) {
        $model = if ($all[$i] -match 'model:(\S+)') { $Matches[1] } else { '' }
        Write-Host ('    {0}) {1} {2}' -f ($i + 1), ($all[$i] -split '\s+')[0], $model)
        if ($f50.Count -eq 1 -and $all[$i] -eq $f50[0]) { $def = $i + 1 }
    }
    $n = 0
    if (-not [int]::TryParse((Ask (T 'Device') "$def" 'MU300_ANS_DEVICE'), [ref]$n) -or $n -lt 1 -or $n -gt $all.Count) { Die (T 'invalid choice') }
    $env:ANDROID_SERIAL = ($all[$n - 1] -split '\s+')[0]
}
# [string]: with no device adb prints nothing, and `-notmatch` on that empty result is falsy, not true
# nothing either when the only adb devices are in Linux; "linux" when ANDROID_SERIAL names one of those
function AdbState {
    SelectDevice -Quiet
    if ($env:ANDROID_SERIAL -like 'MU300LINUX*') { return 'linux' }
    if (-not $env:ANDROID_SERIAL -and @(AndroidLines (Quiet { adb devices -l })).Count -eq 0) { return '' }
    [string](Quiet { adb get-state })
}
# Ask QUESTION DEFAULT [VARIABLE]: VARIABLE answers the question in answers mode (tools\answers.ps1)
function Ask($question, $default, $Id) {
    $a = if ($script:MuAnswersMode) { AnswerNamed $question $default $Id }
        elseif ($script:AnswerQueue) { NextAnswer $question } else { Read-Host "$question [$(T $default)]" }
    $r = if ([string]::IsNullOrWhiteSpace($a)) { $default } else { NormalizeAnswer $a.Trim() }
    if (AnswerSecret ([string]$Id)) { Progress 'question' 'id' $Id 'text' $question 'default' $default 'secret' '1' }
    else { Progress 'question' 'id' $Id 'text' $question 'default' $default 'answer' $r }
    return $r
}
# adb shell with root; stdin is never forwarded so prompts of this script are not eaten
function SuDo($cmd) {
    (Quiet { adb shell "su -c '$cmd'" }) -join "`n" -replace "`r", ''
}
# a property of the device that needs no root (the first line of adb shell getprop)
function AdbProp($name) {
    $o = @(Quiet { adb shell getprop $name })
    if ($o.Count -eq 0) { return '' }
    return ([string]$o[0]).Trim()
}
# Write the file on the device and pull it. cmd.exe redirection does keep the byte stream intact on this
# side, but the damage happens on the other one: some devices give `su -c` a pty, whose ONLCR rewrites every
# LF as CRLF, so the bytes are already corrupt before they reach the host (issue #2).
function SuDoToFile($cmd, $path) {
    $dev = '/data/local/tmp/mu300-pull.bin'
    & adb shell "su -c '$cmd > $dev'" | Out-Null
    Quiet { adb pull $dev "$path" } | Out-Null
    & adb shell "su -c 'rm -f $dev'" | Out-Null
}
$script:PyExe = $null
# A Python that really runs, 3.8 or newer. Being on PATH says little on Windows: without Python installed, python.exe
# is still there as the Microsoft Store's app-execution alias, which only opens the Store (and exits 9009).
function FindPython {
    $cands = @()
    foreach ($n in 'python', 'python3', 'py') {
        # -CommandType Application: command lookup is case-insensitive and functions win, so a bare
        # `Get-Command python` resolves to the function below and never reaches python.exe (issue #3)
        foreach ($c in @(Get-Command $n -CommandType Application -ErrorAction SilentlyContinue)) {
            # the Store's python.exe has an empty .Source: fall back to .Path
            $cands += $(if ($c.Source) { $c.Source } else { $c.Path })
        }
    }
    # installed a moment ago by InstallPython: not on this process's PATH yet
    $cands += @(Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | ForEach-Object { $_.FullName })
    foreach ($exe in $cands) {
        Quiet { & $exe -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' } | Out-Null
        if ($LASTEXITCODE -eq 0) { return $exe }
    }
    return $null
}
# Python 3.12 for this user only (no administrator rights needed): winget where there is one, else the installer
# from python.org
function InstallPython {
    Say (T 'Python 3 is not installed; installing it for this user')
    if (Get-Command winget -CommandType Application -ErrorAction SilentlyContinue) {
        Quiet { winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements --disable-interactivity } | Out-Null
        if (FindPython) { return }
    }
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
    $exe = Join-Path $env:TEMP "python-3.12.10-$arch.exe"
    try {
        Invoke-WebRequest "https://www.python.org/ftp/python/3.12.10/python-3.12.10-$arch.exe" -OutFile $exe -UseBasicParsing
    } catch { return }
    Start-Process -Wait -FilePath $exe -ArgumentList '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_test=0', 'Include_launcher=0'
    Remove-Item $exe -ErrorAction SilentlyContinue
}
function Python { param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PyArgs)
    if (-not $script:PyExe) {
        $script:PyExe = FindPython
        if (-not $script:PyExe) { InstallPython; $script:PyExe = FindPython }
        if (-not $script:PyExe) { Die (T 'Python 3 not found (install it from python.org or the Microsoft Store)') }
    }
    & $script:PyExe @PyArgs
}
# the VPN module's latest release (or $env:MU300_VPN_MODULE, a copy on this computer), checked against that release's
# SHA256SUMS (fetched every time: under releases/latest the same name is a new file with every release), as $EXTRA_VPN
function FetchVpnModule {
    $vd = Join-Path $Work 'vpn-module'
    New-Item -ItemType Directory -Force -Path $vd | Out-Null
    if ($env:MU300_VPN_MODULE) {
        if (-not (Test-Path $env:MU300_VPN_MODULE)) { Die (T '{1} not found' $env:MU300_VPN_MODULE) }
        $sf = Join-Path (Split-Path $env:MU300_VPN_MODULE) 'SHA256SUMS'
        if (Test-Path $sf) {
            $leaf = Split-Path $env:MU300_VPN_MODULE -Leaf
            foreach ($line in Get-Content $sf) {
                $p = $line -split '\s+', 2
                if ($p.Count -eq 2 -and $p[1].TrimStart('*') -eq $leaf -and
                    (Get-FileHash $env:MU300_VPN_MODULE -Algorithm SHA256).Hash.ToLower() -ne $p[0]) {
                    Die (T 'checksum mismatch for {1}' $env:MU300_VPN_MODULE)
                }
            }
        }
        $script:EXTRA_VPN = $env:MU300_VPN_MODULE
        return
    }
    $vb = if ($env:MU300_VPN_URL) { $env:MU300_VPN_URL } else { 'https://github.com/dikeckaan/mu300-linux-vpn/releases/latest/download' }
    $f = 'mu300-linux-vpn.tar.gz'
    Say (T 'Downloading the VPN module')
    try { Invoke-WebRequest "$vb/SHA256SUMS" -OutFile "$vd\SHA256SUMS" -UseBasicParsing } catch { Die (T 'cannot download {1}' "$vb/SHA256SUMS") }
    $want = $null
    foreach ($line in Get-Content "$vd\SHA256SUMS") {
        $p = $line -split '\s+', 2
        if ($p.Count -eq 2 -and $p[1].TrimStart('*') -eq $f) { $want = $p[0] }
    }
    if (-not $want) { Die (T '{1} is not part of release {2}' $f $vb) }
    $have = if (Test-Path "$vd\$f") { (Get-FileHash "$vd\$f" -Algorithm SHA256).Hash.ToLower() } else { '' }
    if ($have -ne $want) {
        Write-Host "  $f"
        Fetch "$vb/$f" "$vd\$f.part"
        if ((Get-FileHash "$vd\$f.part" -Algorithm SHA256).Hash.ToLower() -ne $want) { Die (T 'checksum mismatch for {1}' $f) }
        Move-Item -Force "$vd\$f.part" "$vd\$f"
    }
    $script:EXTRA_VPN = "$vd\$f"
}
# GitHub's release CDN throttles single connections hard in some regions: pull large files as parallel ranges
function Fetch($url, $out) {
    $jobs = 8
    try { $len = [int64](Invoke-WebRequest $url -Method Head -UseBasicParsing).Headers['Content-Length'][0] } catch { $len = 0 }
    # for the progress stream: the file, and while a parallel download runs how many of its bytes are there, once a
    # second (FetchWatch); whoever reads the stream works out the speed and the time left
    $name = (Split-Path $out -Leaf) -replace '\.part$', ''
    Progress 'download' 'file' $name 'bytes' 0 'total' $len 'state' 'start'
    if ($len -lt 8MB) {
        Invoke-WebRequest $url -OutFile $out -UseBasicParsing
        Progress 'download' 'file' $name 'bytes' (Get-Item $out).Length 'total' $len 'state' 'done'
        return
    }
    $part = [int64]($len / $jobs) + 1
    $running = @()
    for ($i = 0; $i -lt $jobs; $i++) {
        $s = $i * $part; $e = [math]::Min($s + $part - 1, $len - 1)
        $running += Start-Job -ScriptBlock {
            param($u, $f, $a, $b)
            $r = [System.Net.HttpWebRequest]::Create($u); $r.AddRange($a, $b)
            $resp = $r.GetResponse(); $fs = [IO.File]::Create($f)
            $resp.GetResponseStream().CopyTo($fs); $fs.Close(); $resp.Close()
        } -ArgumentList $url, "$out.part$i", $s, $e
    }
    $failed = $false
    while ($script:MuProgress -and @($running | Where-Object { $_.State -eq 'Running' }).Count -gt 0) {
        Start-Sleep -Seconds 1
        Progress 'download' 'file' $name 'bytes' (FetchWatch $out) 'total' $len 'state' 'running'
    }
    foreach ($j in $running) { Wait-Job $j | Out-Null; if ($j.State -ne 'Completed') { $failed = $true }; Receive-Job $j -ErrorAction SilentlyContinue | Out-Null; Remove-Job $j }
    if (-not $failed) {
        $fs = [IO.File]::Create($out)
        for ($i = 0; $i -lt $jobs; $i++) { $b = [IO.File]::OpenRead("$out.part$i"); $b.CopyTo($fs); $b.Close(); Remove-Item "$out.part$i" }
        $fs.Close()
        if ((Get-Item $out).Length -eq $len) { Progress 'download' 'file' $name 'bytes' $len 'total' $len 'state' 'done'; return }
    }
    Get-ChildItem "$out.part*" -ErrorAction SilentlyContinue | Remove-Item -Force
    Write-Host ('  ' + (T 'parallel download failed, retrying as a single stream'))
    Invoke-WebRequest $url -OutFile $out -UseBasicParsing
    Progress 'download' 'file' $name 'bytes' (Get-Item $out).Length 'total' $len 'state' 'done'
}
# the bytes of a parallel download that are there so far. Each part is opened to ask its length: the directory's
# figure for a file another process is still writing lags behind.
function FetchWatch($out) {
    [int64]$got = 0
    foreach ($f in @(Get-ChildItem "$out.part*" -ErrorAction SilentlyContinue)) {
        try { $s = [IO.File]::Open($f.FullName, 'Open', 'Read', 'ReadWrite'); $got += $s.Length; $s.Close() } catch { }
    }
    return $got
}
# shell scripts and config files for the device must keep Unix line endings
function WriteUnix($path, $text) { [IO.File]::WriteAllText($path, ($text -replace "`r`n", "`n")) }
# adb push of a script or other text file for the device, with LF line ends: a clone with core.autocrlf=true has
# CRLF files, and Android's sh stops at "do\r" before it runs a single command (issue #7)
function PushUnix($src, $dst) {
    $tmp = Join-Path $Work ('lf-' + (Split-Path $src -Leaf))
    WriteUnix $tmp ([IO.File]::ReadAllText($src))
    & adb push $tmp $dst | Out-Null
    $ok = $LASTEXITCODE
    Remove-Item $tmp
    $ok -eq 0
}
# BootchainLine: the plan's line from the device's BOOTCHAIN-PLAN <android> <linux> [names] (android-bootchain.sh
# check), $null without one; BootchainRefused: the reason of its BOOTCHAIN-REFUSED line (install.sh's bootchain_line)
function BootchainLine([string]$Out) {
    $plan = @(($Out -replace "`r", '') -split "`n" | Where-Object { $_ -like 'BOOTCHAIN-PLAN *' })
    if ($plan.Count -eq 0) { return $null }
    $f = @($plan[0].Trim().Split(' ') | Where-Object { $_ })
    if ($f.Count -lt 3) { return $null }
    $diff = @($f | Select-Object -Skip 3)
    if ($diff.Count -eq 0) { return (T 'boot chain:     identical on both slots') }
    return (T "boot chain:     {1} differ between the slots; Android's slot {2} is copied to slot {3} (backed up first)" ($diff -join ', ') $f[1] $f[2])
}
function BootchainRefused([string]$Out) {
    $r = @(($Out -replace "`r", '') -split "`n" | Where-Object { $_ -like 'BOOTCHAIN-REFUSED: *' })
    if ($r.Count -eq 0) { return (T 'no answer from the device') }
    return $r[0].Substring('BOOTCHAIN-REFUSED: '.Length)
}

if ($AdbPath) {
    FindAdb
    $adbCmd = Get-Command adb -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $adbCmd) { exit 1 }
    Write-Host $(if ($adbCmd.Source) { $adbCmd.Source } else { $adbCmd.Path })
    exit 0
}
Progress 'hello' 'script' 'install.ps1' 'version' '1' 'mode' $(if ($Check) { 'check' } elseif ($Backup) { 'backup' } else { 'prebuilt' })

# ---------------------------------------------------------------- a package (tools/package.sh for install.sh)
# A release's Magisk zip, unpacked on a computer, is an installer for that computer too: its computer\ directory is
# this project's installers, with a file .mu300-package in it, and the system and the kernel of the zip lie next to
# it - payload\<the two assets>, mu300\manifest naming them and mu300\SHA256SUMS, the release's own checksums. Then
# exactly that system and that kernel are installed from those files, nothing is downloaded and the installer does not
# update itself (it is the one that came with the files). -Package DIR or $env:MU300_PACKAGE names another one.
# PackageDir: the directory of the unpacked zip, or '' (a copy of the project)
function PackageDir([string]$Given, [string]$TopDir) {
    if ($Given) { return $Given }
    if ($TopDir -and (Test-Path -LiteralPath (Join-Path $TopDir '.mu300-package'))) { return (Split-Path -Parent $TopDir) }
    return ''
}
# PackageManifest: the manifest of an unpacked zip, read as the zip's own installer reads it (only its keys, every
# value exactly one a release has: they become file names), as a hashtable; $null when it is not valid
function PackageManifest([string]$Dir) {
    $f = Join-Path (Join-Path $Dir 'mu300') 'manifest'
    if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { return $null }
    $m = @{ TAG = ''; SYSTEM = ''; OS = ''; UBUNTU = ''; KERNEL = ''; KERNEL_ASSET = ''; ROOTFS_ASSET = ''; SHA256_KERNEL = ''; SHA256_ROOTFS = '' }
    foreach ($line in [IO.File]::ReadAllLines($f)) {
        $i = $line.IndexOf('=')
        if ($i -le 0) { continue }
        $k = $line.Substring(0, $i)
        if ($m.ContainsKey($k) -and $k -ceq $k.ToUpperInvariant()) { $m[$k] = $line.Substring($i + 1).TrimEnd("`r") }
    }
    if ($m.TAG -cnotmatch '^v[0-9][0-9A-Za-z._-]*$' -or $m.TAG.Contains('..')) { return $null }
    $sys = @('openwrt:openwrt::mu300-openwrt-rootfs.tar.gz', 'openwrt-luci:openwrt-luci::mu300-openwrt-luci-rootfs.tar.gz',
        'ubuntu-24.04:ubuntu:24.04:mu300-ubuntu-rootfs.tar.gz', 'ubuntu-26.04:ubuntu:26.04:mu300-ubuntu-26.04-rootfs.tar.gz')
    if ($sys -cnotcontains "$($m.SYSTEM):$($m.OS):$($m.UBUNTU):$($m.ROOTFS_ASSET)") { return $null }
    if (@('5.4:mu300-kernel.tar.gz', '6.18:mu300-kernel-6.18.tar.gz', '7.2:mu300-kernel-7.2.tar.gz', '7.3:mu300-kernel-7.3.tar.gz') -cnotcontains "$($m.KERNEL):$($m.KERNEL_ASSET)") { return $null }
    if ($m.UBUNTU -eq '26.04' -and $m.KERNEL -eq '5.4') { return $null }
    foreach ($h in $m.SHA256_KERNEL, $m.SHA256_ROOTFS) { if ($h -cnotmatch '^[0-9a-f]{64}$') { return $null } }
    return $m
}
# PackageCheck: why a payload file of an unpacked zip cannot be used ('' when it can): it must be there, with the
# checksum the manifest names ($Want) and the one the release's SHA256SUMS names
function PackageCheck([string]$Dir, [string]$Name, [string]$Want) {
    $sums = Join-Path (Join-Path $Dir 'mu300') 'SHA256SUMS'
    $listed = ''
    if (Test-Path -LiteralPath $sums) {
        foreach ($line in [IO.File]::ReadAllLines($sums)) {
            $p = $line.TrimEnd("`r") -split '\s+', 2
            if ($p.Count -eq 2 -and $p[1].TrimStart('*') -ceq $Name) { $listed = $p[0]; break }
        }
    }
    $f = Join-Path (Join-Path $Dir 'payload') $Name
    if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { return 'missing' }
    $have = (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLowerInvariant()
    if (-not $listed -or $have -cne $Want -or $have -cne $listed) { return 'mismatch' }
    return ''
}
$PKG = PackageDir $(if ($Package) { $Package } else { [string]$env:MU300_PACKAGE }) $Top
$PM = $null
if ($PKG) {
    if (-not (Test-Path -LiteralPath $PKG -PathType Container)) { Die (T 'no unpacked Magisk zip at {1}' $PKG) }
    $PKG = (Resolve-Path -LiteralPath $PKG).Path
    if (-not (Test-Path -LiteralPath (Join-Path $PKG 'mu300\manifest')) -or -not (Test-Path -LiteralPath (Join-Path $PKG 'mu300\SHA256SUMS'))) {
        Die (T '{1} is not an unpacked Magisk zip of a release (it has no mu300/manifest and mu300/SHA256SUMS)' $PKG)
    }
    $PM = PackageManifest $PKG
    if (-not $PM) { Die (T 'the package is incomplete or damaged (its manifest is not valid)') }
    $Release = $PM.TAG; $ReleaseUrl = ''
    Info 'package' $PKG; Info 'package.system' $PM.SYSTEM; Info 'package.kernel' $PM.KERNEL; Info 'package.release' $PM.TAG
    Write-Host (T 'Package: {1} with kernel {2}, release {3} (unpacked Magisk zip at {4})' $PM.SYSTEM $PM.KERNEL $PM.TAG $PKG)
}
# Making room by a reset, and the installation that resumes afterwards (EXPERIMENTAL; tools/resume.sh is the same for
# install.sh, tools/resume.py keeps the transaction): where the transactions and backups are kept, and whether one waits.
$BackupDir = if ($env:MU300_BACKUP_DIR) { $env:MU300_BACKUP_DIR } else { Join-Path $HOME 'mu300-backups' }
$ResumeWanted = if ($Resume) { '1' } elseif ($NoResume) { '0' } else { [string]$env:MU300_RESUME }
if (-not $Transaction -and $env:MU300_TRANSACTION) { $Transaction = $env:MU300_TRANSACTION }
# ResumePending: a transaction in $Dir still waits (for any device) - read here without Python, before the self-update
function ResumePending([string]$Dir) {
    if (-not (Test-Path -LiteralPath $Dir -PathType Container)) { return $false }
    foreach ($f in @(Get-ChildItem -LiteralPath $Dir -Directory -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName 'transaction.json' })) {
        if (-not (Test-Path -LiteralPath $f)) { continue }
        try { $j = [IO.File]::ReadAllText($f) | ConvertFrom-Json } catch { continue }
        if ($j.format -eq 1 -and @('prepared-request', 'prepared') -contains [string]$j.state) { return $true }
    }
    return $false
}
$resumeWaits = $ResumeWanted -ne '0' -and ($Transaction -or (ResumePending $BackupDir))
if ($resumeWaits) { Write-Host ('  ' + (T 'a prepared installation is waiting (tools/resume.sh): this installer does not update itself until it is done')) }

# first of all, make this the newest installer (it runs the updated one and ends when it changed) - not from a
# package, and not while a prepared installation waits: its transaction is bound to the tools that prepared it
if (-not $PKG -and -not $resumeWaits -and (SelfUpdate)) {
    $env:MU300_SELF_UPDATED = '1'
    AnswersReexport
    $PSBoundParameters['Lang'] = $Lang
    & $PSCommandPath @PSBoundParameters
    exit $LASTEXITCODE
}

# the graphical installer, offered once to a person who starts a plain installation
. (Join-Path (Join-Path $Top 'tools') 'gui-launch.ps1')
GuiOffer ([bool]($script:MuAnswersMode -or ($null -ne $script:AnswerQueue) -or $Check -or $Backup))

Step 'preflight' 2
Say (T 'Checking host tools and device')
FindAdb
foreach ($c in 'adb', 'tar') { if (-not (Get-Command $c -ErrorAction SilentlyContinue)) { Die (T '{1} not found' $c) } }
Python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' | Out-Null
if ($LASTEXITCODE -ne 0) { Die (T 'Python 3.8 or newer is required') }
if (-not $Check) {
    Quiet { Python -c 'import lz4.block' } | Out-Null
    if ($LASTEXITCODE -ne 0) {
        # a fresh Python has no lz4, and "pip install lz4" often went to another Python than the one found here
        # (the Store alias, py.exe): install it into this very interpreter, for this user only
        Write-Host ('  ' + (T 'installing the lz4 Python module (pip install --user lz4)'))
        Quiet { Python -m pip install --user --disable-pip-version-check -q lz4 } | Out-Null
        Quiet { Python -c 'import lz4.block' } | Out-Null
    }
    if ($LASTEXITCODE -ne 0) { Die (T 'the lz4 Python module is required to build the boot image: pip install lz4') }
}
Quiet { adb start-server } | Out-Null
# The device in Linux, and only a phone or tablet in Android: that is not the one to install to - offer to send the
# device back to Android first
# (ANDROID_SERIAL naming the device in Linux: that is the one to send back to Android, not one to work on)
# (kept in $LinuxWanted: with two boards in Linux it is the one that is rebooted, never the other)
$LinuxWanted = ''
if ($env:ANDROID_SERIAL -like 'MU300LINUX*') { $LinuxWanted = $env:ANDROID_SERIAL; Remove-Item Env:ANDROID_SERIAL }
$devs = @(Quiet { adb devices -l })
$target = @(AndroidLines $devs | Where-Object { $_ -match 'model:F50|product:MU300|device:MU300|device:U30Air|device:MU3351' })
$linuxAdb = @(LinuxAdbSerials $devs)
$linuxFirst = (-not $env:ANDROID_SERIAL) -and $target.Count -eq 0 -and ($linuxAdb.Count -gt 0 -or (LinuxRunning))
if (-not $linuxFirst) { SelectDevice }
if ($linuxFirst -or (AdbState) -notmatch 'device') {
    # the device may be running MU300 Linux right now: then it is on the USB network (SSH) and, with adb on
    # there, among the adb devices as MU300LINUX-...
    $linux = $linuxFirst -or $linuxAdb.Count -gt 0 -or (LinuxRunning)
    if (-not $linux) { Die (T 'no adb device (boot Android, enable USB debugging)') }
    Say (T 'The device is running MU300 Linux, not Android')
    Write-Host ('  ' + (T 'Installing and uninstalling happen from Android, so the device has to reboot first.'))
    Write-Host ('  ' + (T 'I can ask it over SSH; you will be prompted for its password.'))
    if ((Ask (T 'Reboot the device into Android now? (yes/no)') 'yes' 'MU300_ANS_REBOOT_ANDROID') -ne 'yes') { Die (T 'boot Android yourself (on the device: sudo mu300-next-boot android && sudo reboot)') }
    # Over adb where the Linux lets this computer in (every computer, by default): a root shell, no password to
    # type, and it works where the USB network does not. The marker file first: only our Linux is told to reboot.
    $gone = $false; $sshTried = $false
    $open = @(LinuxAdbSerials (Quiet { adb devices -l }) -Open)
    if ($LinuxWanted) { $open = @($open | Where-Object { $_ -eq $LinuxWanted }) }
    $s = $(if ($open.Count -eq 1) { $open[0] } else { $LinuxWanted })
    if ($open.Count -eq 1) {
        if (("" + (Quiet { adb -s $s shell 'test -x /opt/mu300/bin/mu300-next-boot && echo mu300' })).Trim() -eq 'mu300') {
            Write-Host ('  ' + (T 'asking it over adb ({1})' $s))
            Quiet { adb -s $s shell '/opt/mu300/bin/mu300-next-boot android && sync && reboot' } | Out-Null
            for ($i = 0; $i -lt 8; $i++) {
                if (@(LinuxAdbSerials (Quiet { adb devices -l })) -notcontains $s) { $gone = $true; break }
                Start-Sleep 5
            }
            if ($gone) { Write-Host ('  ' + (T 'rebooting')) }
        }
    }
    # Only where the address can be no other device: with two boards in Linux on adb (or the one named not on adb
    # while another one in Linux is), 192.168.77.1 or .78.1 may well be the other one (measured: the F50 named by
    # ANDROID_SERIAL next to a U30 Air in Linux at .78.1).
    $inLinux = @(LinuxAdbSerials (Quiet { adb devices -l }))
    if (-not $gone -and ($inLinux.Count -gt 1 -or ($LinuxWanted -and $inLinux.Count -gt 0 -and $inLinux -notcontains $LinuxWanted))) {
        Die (T 'more than one device runs MU300 Linux and adb could not reboot this one: boot Android yourself (on the device: sudo mu300-next-boot android && sudo reboot), or give its address with MU300_IP')
    }
    # -t: sudo needs a terminal to ask for the device password; reboot cuts the connection, so watch the port
    foreach ($u in 'ubuntu', 'root') {
        if ($gone -or -not (LinuxRunning)) { break }
        $sshTried = $true
        Write-Host ('  ' + (T '{1} - enter the device password when asked (Ctrl-C to skip)' "$u@$MU300_IP"))
        & ssh -t -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL -o LogLevel=ERROR -o ConnectTimeout=8 "$u@$MU300_IP" `
            'if [ "$(id -u)" = 0 ]; then S=; else S=sudo; fi; $S sh -c "/opt/mu300/bin/mu300-next-boot android && sync && reboot"'
        $gone = $false
        for ($i = 0; $i -lt 8; $i++) {
            if (-not (Test-NetConnection -ComputerName $MU300_IP -Port 22 -InformationLevel Quiet -WarningAction SilentlyContinue)) { $gone = $true; break }
            Start-Sleep 5
        }
        if ($gone) { Write-Host ('  ' + (T 'rebooting')); break }
    }
    # no way in at all: no SSH on this cable, and an adb that takes authorised keys only or would not reboot
    if (-not $gone -and -not $sshTried -and @(LinuxAdbSerials (Quiet { adb devices -l })).Count -gt 0) {
        Die (T 'boot Android yourself (on the device: sudo mu300-next-boot android && sudo reboot)')
    }
    Write-Host ('  ' + (T 'waiting for Android'))
    # Which device comes back: the serial number of our Linux is MU300LINUX-<Android's serial>-<eMMC id> (boot/init).
    # Never "whatever single device adb lists": while the F50 rebooted, a Pixel next to it was the only one, and the
    # check went on with the Pixel (measured with install.sh).
    $lmBack = (($s -replace '^MU300LINUX-?', '') -split '-')[0]
    $lmUp = $false
    for ($i = 0; $i -lt 60; $i++) {
        if ($lmBack -and @(AndroidLines (Quiet { adb devices -l }) | Where-Object { ($_ -split '\s+')[0] -eq $lmBack }).Count -gt 0) { $env:ANDROID_SERIAL = $lmBack }
        $lmState = AdbState
        if ($env:ANDROID_SERIAL -and $lmState -match 'device') { $lmUp = $true; break }
        Start-Sleep 5
    }
    if (-not $lmUp) { Die (T 'the device did not come back as Android; boot it yourself (mu300-next-boot android)') }
    Write-Host ('  ' + (T 'Android is up'))
}
# whatever its serial number says: a root shell of our Linux must never be taken for a rooted Android
if (("" + (Quiet { adb shell 'ls -d /opt/mu300/bin 2>/dev/null' })).Trim()) {
    Die ((T 'The device is running MU300 Linux, not Android') + ': ' + (T 'boot Android yourself (on the device: sudo mu300-next-boot android && sudo reboot)'))
}
# an Android that is still starting answers adb before su and storage are ready: the pulls then failed
for ($i = 0; $i -lt 60 -and (AdbProp 'sys.boot_completed') -ne '1'; $i++) {
    if ($i -eq 0) { Write-Host ('  ' + (T 'waiting for Android to finish starting')) }
    Start-Sleep 2
}
# The bootloader's lock is a warning and never a refusal (the owner's decision of 2026-10-13, FINDINGS 71): Linux
# needs a device that boots a modified boot image, and that is an unlocked bootloader or a locked one with a patched
# TrustOS - which no property tells. Without either the device goes back to Android by itself after the install.
# BootloaderState: locked (ro.boot.flash.locked=1 or ro.boot.vbmeta.device_state=locked), unlocked, or unknown.
function BootloaderState([string]$FlashLocked, [string]$DeviceState) {
    if ($FlashLocked -eq '1' -or $DeviceState -eq 'locked') { return 'locked' }
    if ($FlashLocked -eq '0' -or $DeviceState -eq 'unlocked') { return 'unlocked' }
    return 'unknown'
}
$BL_WARN = T 'The bootloader is locked. Did you patch TrustOS? With a patched TrustOS Linux boots on a locked bootloader; without it the device goes back to Android by itself after the install.'
$BL_LOCKED = AdbProp 'ro.boot.flash.locked'; $BL_DEVICE_STATE = AdbProp 'ro.boot.vbmeta.device_state'
$BL_STATE = BootloaderState $BL_LOCKED $BL_DEVICE_STATE
# For a program that reads the progress stream: what the device says about itself before anything needs root (the
# model, Android's build, the bootloader's lock and verified-boot state), then whether su works and whose it is.
# Read only, and only for the stream.
if ($script:MuProgress) {
    Info 'device.model' "$(AdbProp 'ro.product.model') / $(AdbProp 'ro.product.device')"
    Info 'android.build' (AdbProp 'ro.build.display.id')
    Info 'bootloader.locked' $BL_LOCKED
    Info 'bootloader.device_state' $BL_DEVICE_STATE
    Info 'bootloader.state' $BL_STATE
    Info 'bootloader.verified' (AdbProp 'ro.boot.verifiedbootstate')
    if ((SuDo 'id -u') -eq '0') {
        Info 'root' 'yes'
        Info 'root.su' (@(([string]((Quiet { adb shell su -v }) -join "`n")) -split "`n")[0]).Trim()
    } else { Info 'root' 'no' }
}
if ((SuDo 'id -u') -ne '0') { Die (T 'su does not work on the device') }
$model = "$(SuDo 'getprop ro.product.model') / $(SuDo 'getprop ro.product.device')"
Write-Host (T 'device: {1}' $model)
if ($BL_STATE -eq 'locked') { Write-Host (T 'WARNING: {1}' $BL_WARN) }
# The U30 Air is the F50's board with a battery: the same kernel and images, a few modules of its own (init
# loads them; the boot image says which device it is for)
# The ZTE V50 (model V50, device MU3351, platform ums9620) is experimental (FINDINGS 95): only exactly that, never
# taken for an F50 (a V50 that is not all three is refused, not asked about), and only with a mainline kernel.
$DEVICE = 'f50'
$MODEL_KNOWN = $true
if ($model -ceq 'V50 / MU3351' -or $model -cmatch '^V50 / ' -or $model -cmatch ' / MU3351$') {
    if ($model -cne 'V50 / MU3351' -or (SuDo 'getprop ro.board.platform') -ne 'ums9620') {
        Die (T 'This looks like a ZTE V50 but is not one this installer knows (model V50, device MU3351, platform ums9620): {1}' $model)
    }
    $DEVICE = 'v50'
    Write-Host (T 'ZTE V50: EXPERIMENTAL, tested by nobody in this project (kano ported it); mainline kernel only; no second SIM, no NFC; USB network 192.168.79.1')
} elseif ($model -match 'U30Air|U30_Air|U30 Air') {
    $DEVICE = 'u30air'
} elseif ($model -notmatch 'MU300|F50|mu300') {
    Info 'device.kind' 'unknown'
    if ((Ask (T 'This does not look like a ZTE F50/MU300 or U30 Air. Continue anyway? (yes/no)') 'no' 'MU300_ANS_UNKNOWN_MODEL') -ne 'yes') { Progress 'error' 'message' (T 'cancelled'); exit 1 }
    $MODEL_KNOWN = $false
}
if ($MODEL_KNOWN) { Info 'device.kind' $DEVICE }
# Linux goes on the slot Android is not on: b next to an Android on a, a next to an Android on b (after an OTA the
# device often runs from b, issue #86) - install.sh's slot_setup. Android's own boot partition is never written.
switch ((SuDo 'getprop ro.boot.slot_suffix').Trim()) {
    '_a' { $ANDROID_SLOT = 'a'; $LINUX_SLOT = 'b' }
    '_b' { $ANDROID_SLOT = 'b'; $LINUX_SLOT = 'a' }
    default { Die (T 'cannot tell which slot Android runs from') }
}
if ((SuDo 'readlink -f /dev/block/by-name/boot_a') -eq (SuDo 'readlink -f /dev/block/by-name/boot_b')) { Die (T 'boot_a and boot_b are the same partition') }
Write-Host (T 'Android runs from slot {1}; Linux goes to slot {2} (boot_{2})' $ANDROID_SLOT $LINUX_SLOT)
Info 'slot.android' $ANDROID_SLOT; Info 'slot.linux' $LINUX_SLOT; Info 'work' $Work
# -Backup (install.sh --backup): what an installation reads from the device before it writes anything - Android's own
# boot image and the first block of misc - and nothing else, the boot image compared with the device's. The partitions
# that hold the unit's own identity and calibration (prodnv, the NV partitions) are not read here or anywhere in
# this script.
if ($Backup) {
    New-Item -ItemType Directory -Force -Path "$Work\dumps" | Out-Null
    Step 'backup' 30
    Say (T 'Copying the boot image of Android (boot_{1}) and the first block of misc to this computer' $ANDROID_SLOT)
    $bImg = "$Work\dumps\boot_$ANDROID_SLOT.img"; $bMisc = "$Work\dumps\misc-head.bin"
    SuDoToFile "cat /dev/block/by-name/boot_$ANDROID_SLOT" $bImg
    SuDoToFile 'dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null' $bMisc
    if (-not (Test-Path $bImg) -or (Get-Item $bImg).Length -eq 0) { Die (T 'could not read {1} from the device' "/dev/block/by-name/boot_$ANDROID_SLOT") }
    if (-not (Test-Path $bMisc) -or (Get-Item $bMisc).Length -eq 0) { Die (T 'could not read the misc header from the device') }
    $bWant = (([string](SuDo "sha256sum /dev/block/by-name/boot_$ANDROID_SLOT")).Trim() -split '\s+')[0]
    $bHave = (Get-FileHash $bImg -Algorithm SHA256).Hash.ToLower()
    if (-not $bWant -or $bWant -ne $bHave) { Die (T 'the copy of boot_{1} is not what the device holds; do not rely on it' $ANDROID_SLOT) }
    Write-Host "  $bImg  sha256 $bHave"
    Write-Host "  $bMisc"
    Write-Host ('  ' + (T 'Not copied, here or during an installation: the NV and calibration partitions (IMEI, radio calibration). The installer never reads or writes them.'))
    Write-Host ('  ' + (T 'Nothing was written to the device.'))
    Info 'backup.boot' $bImg; Info 'backup.misc' $bMisc; Info 'backup.sha256' $bHave
    Progress 'done' 'result' 'ok'
    exit 0
}
# The Linux slot gets the boot chain of Android's slot - uboot, sml, teecfg, trustos and vbmeta, one firmware build on
# both slots (issue #86): tools/android-bootchain.sh decides and checks, android-install.sh copies it before it
# installs anything, with a backup (install.sh's bootchain_plan). -NoBootchainSync skips that, for experts.
$SYNC_BOOTCHAIN = 1
if ($NoBootchainSync -or $env:MU300_SYNC_BOOTCHAIN -eq '0') {
    $SYNC_BOOTCHAIN = 0
    $BC_LINE = T 'boot chain:     not synced (-NoBootchainSync)'
} elseif (-not $MODEL_KNOWN) {
    # android-bootchain.sh writes only on a model it knows: a device taken as an F50 anyway keeps its chain as it is
    $SYNC_BOOTCHAIN = 0
    $BC_LINE = T 'boot chain:     not synced (the model is not recognised)'
} else {
    $bcOut = ''
    New-Item -ItemType Directory -Force -Path $Work | Out-Null
    if (PushUnix "$Top\tools\android-bootchain.sh" "$T/android-bootchain.sh") { $bcOut = SuDo "sh $T/android-bootchain.sh check" }
    $BC_LINE = BootchainLine $bcOut
    if (-not $BC_LINE) {
        $why = BootchainRefused $bcOut
        if ($Check) { $BC_LINE = T 'boot chain:     cannot be synced: {1}' $why }
        else { Die ((T 'the boot chain cannot be copied to the Linux slot: {1}' $why) + "`n" + (T 'Nothing was changed. Experts can skip this step with -NoBootchainSync.')) }
    }
}
Write-Host $BC_LINE
Info 'bootchain' $BC_LINE

Step 'storage' 6
Say (T 'Locating free eMMC space after the last partition')
# (tools/storage.sh's region_probe: the same command, so both installers see the same) the end of the last partition,
# the eMMC's size (the last line), each GPT partition named mu300root as part:START:SECTORS, and "image" for another
# installer's image file in Android's data (FINDINGS 179)
# (no double quotes in a device command: Windows PowerShell 5.1 drops them on the way to adb)
$parts = @((SuDo 'e=0; for p in /sys/block/mmcblk0/mmcblk0p*; do x=$(( $(cat $p/start) + $(cat $p/size) )); [ $x -gt $e ] && e=$x; grep -qx PARTNAME=mu300root $p/uevent 2>/dev/null && echo part:$(cat $p/start):$(cat $p/size); done; [ -f /data/mu300linux/openwrt.ext4 ] && echo image; echo $e $(cat /sys/block/mmcblk0/size)').Trim() -split '\s+')
$nums = @($parts | Where-Object { $_ -match '^[0-9]+$' })
$partList = @($parts | Where-Object { $_ -match '^part:[0-9]+:[0-9]+$' })
$FOREIGN_IMAGE = @($parts | Where-Object { $_ -eq 'image' }).Count -gt 0
if ($nums.Count -ne 2 -or ($nums.Count + $partList.Count + [int]$FOREIGN_IMAGE) -ne $parts.Count) { Die (T 'could not read the partition table from the device (is su granted? try again)') }
[int64]$lastEnd = $nums[0]; [int64]$disk = $nums[1]
# The free region behind the last partition, in sectors (tools/storage.sh's region_probe): from the first 4096-sector
# (2 MiB) boundary behind the last partition - the next one even when the partition ends on one - to the last
# boundary but one before the backup GPT. Each end gives up to one block away, so a partition table that ends at the
# end of the eMMC (issues #32, #65) makes them cross: with userdata up to the last usable sector of the 64 GB eMMC
# the start is the end of the disk and the end 4 MiB before it, the "-4 MiB" of issue #32. A negative size is not a
# region but an empty one (the card is offered, or room is made), whose start is never read (RegionOnDisk).
function FreeRegion([int64]$LastEnd, [int64]$Disk) {
    [int64]$start = [math]::Floor($LastEnd / 4096 + 1) * 4096
    [int64]$end = [math]::Floor(($Disk - 34) / 4096 - 1) * 4096
    if ($end -le $start) { $end = $start }
    return @{ Start = $start; End = $end }
}
$region = FreeRegion $lastEnd $disk
[int64]$REG_OFF = $region.Start * 512
[int64]$REG_SIZE = ($region.End - $region.Start) * 512
[int64]$OFF = $REG_OFF; [int64]$SIZE = $REG_SIZE
# A GPT partition named mu300root (another installer's, kanoqwq's desktop installer): Linux goes there, boot/init boots
# it. Two of them: which one is the system cannot be told, and neither is used.
$IN_PART = 0; [int64]$PART_OFF = -1; [int64]$PART_SIZE = 0
if ($partList.Count -eq 1) {
    $pp = $partList[0].Split(':')
    $PART_OFF = [int64]$pp[1] * 512; $PART_SIZE = [int64]$pp[2] * 512
    $OFF = $PART_OFF; $SIZE = $PART_SIZE; $IN_PART = 1
}
Info 'emmc.sectors' $disk; Info 'emmc.last_end' $lastEnd; Info 'region.offset' $OFF; Info 'region.size' $SIZE
# resume.py with these arguments: its lines, and its exit code in $script:ResumeRc
function ResumePy {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$RArgs)
    $ErrorActionPreference = 'Continue'
    $out = @(Python "$Top\tools\resume.py" @RArgs 2>$null | ForEach-Object { "$_" })
    $script:ResumeRc = $LASTEXITCODE
    return $out
}
# what identifies the device and its userdata, and how Android keeps its data: the same command as install.sh's
# (sysfs, properties, /proc/mounts; read only, and no double quotes - see the data check)
$script:FactsCmd = 'd=$(readlink -f /dev/block/by-name/userdata); n=${d##*/}; b=${n%p*}; echo NODE=$n; echo CID=$(cat /sys/class/block/$b/device/cid); echo START=$(cat /sys/class/block/$n/start); echo SIZE=$(cat /sys/class/block/$n/size); echo STATE=$(getprop ro.crypto.state); echo TYPE=$(getprop ro.crypto.type); while read s m r; do case $m in /data) echo SOURCE=$s; break ;; esac; done < /proc/mounts; echo MAGIC=$(dd if=$d bs=1 skip=1024 count=4 2>/dev/null | od -An -tx1 | tr -d \\040\\012); echo EXT=$(dd if=$d bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1 | tr -d \\040\\012)'
function ParseFacts([string]$Text) {
    $f = @{}
    foreach ($l in ($Text -split "`n")) { if ($l -match '^([A-Z]+)=(.*)$') { if (-not $f.ContainsKey($Matches[1])) { $f[$Matches[1]] = $Matches[2].Trim() } } }
    return $f
}
# FactsOk: the identity is one kano's checks take (an eMMC partition node, a 128-bit CID, numbers, a serial)
function FactsOk($F) {
    return ([string]$F.NODE -cmatch '^mmcblk[0-9]p[0-9]+$' -and [string]$F.CID -cmatch '^[0-9a-f]{32}$' -and
        [string]$F.START -match '^[0-9]+$' -and [string]$F.SIZE -match '^[0-9]+$' -and [string]$F.SERIAL)
}
function ResumeFacts {
    $f = ParseFacts (SuDo $script:FactsCmd)
    $f.SERIAL = AdbProp 'ro.serialno'
    if (-not (FactsOk $f)) { return $null }
    $magic = if ($f.EXT -eq '53ef') { '53ef' } else { [string]$f.MAGIC }
    $f.CRYPTO = [string]((ResumePy encrypted "state=$($f.STATE)" "type=$($f.TYPE)" "source=$($f.SOURCE)" "magic=$magic") | Select-Object -First 1)
    return $f
}
# the backup of kano's: the table (as read for the plan), the first block of misc, both slots' boot and boot chain,
# each checked against the device. Never the NV or calibration partitions.
function ResumeBackup([string]$Dir, [string]$G) {
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    Copy-Item "$G\gpt.head" (Join-Path $Dir 'gpt.head'); Copy-Item "$G\gpt.tail" (Join-Path $Dir 'gpt.tail')
    SuDoToFile 'dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null' (Join-Path $Dir 'misc-head.bin')
    if (-not (Test-Path (Join-Path $Dir 'misc-head.bin'))) { Die (T 'could not read the misc header from the device') }
    foreach ($bp in 'boot', 'uboot', 'sml', 'teecfg', 'trustos', 'vbmeta') {
        foreach ($bs in 'a', 'b') {
            $n = "${bp}_$bs"
            Say (T 'Backing up {1}' $n)
            $f = Join-Path $Dir "$n.img"
            SuDoToFile "cat /dev/block/by-name/$n" $f
            $want = (([string](SuDo "sha256sum /dev/block/by-name/$n")).Trim() -split '\s+')[0]
            if (-not (Test-Path $f) -or -not $want -or (Get-FileHash $f -Algorithm SHA256).Hash.ToLower() -ne $want) {
                Die (T 'the copy of {1} is not what the device holds; nothing was changed' $n)
            }
        }
    }
    $lines = foreach ($n in @('gpt.head', 'gpt.tail', 'misc-head.bin') + @(Get-ChildItem -LiteralPath $Dir -Filter '*.img' | Sort-Object Name | ForEach-Object { $_.Name })) {
        "$((Get-FileHash (Join-Path $Dir $n) -Algorithm SHA256).Hash.ToLower())  $n"
    }
    WriteUnix (Join-Path $Dir 'SHA256SUMS') ((@($lines) -join "`n") + "`n")
}
# a file to the device, and there with the checksum it has here
function ResumePush([string]$Local, [string]$Remote) {
    Quiet { adb push $Local $Remote } | Out-Null
    $sum = (([string](SuDo "sha256sum $Remote")).Trim() -split '\s+')[0]
    return ($sum -eq (Get-FileHash $Local -Algorithm SHA256).Hash.ToLower())
}
function ResumeSha([string]$Cmd) { return (([string](SuDo $Cmd)).Trim() -split '\s+')[0] }
# GptShow: the userdata entry from the lines of resize-last-partition.py --show, $null when one is missing.
function GptShow([string[]]$Lines) {
    $v = @{}
    foreach ($l in @($Lines)) { if ("$l" -match '^(LAST_FIRST|LAST_END|USABLE_END)=([0-9]+)\s*$') { $v[$Matches[1]] = [int64]$Matches[2] } }
    if ($v.Count -ne 3) { return $null }
    return @{ First = $v['LAST_FIRST']; End = $v['LAST_END']; UsableEnd = $v['USABLE_END'] }
}
# RepartitionPlan: what userdata and Linux have to share, in sectors, and whether it can be split at all.
function RepartitionPlan([int64]$First, [int64]$End, [int64]$UsableEnd) {
    [int64]$total = $UsableEnd - $First + 1
    [int64]$minAndroid = 4GB / 512      # below this Android has nowhere to put apps or updates
    [int64]$minLinux = 800MB / 512      # OpenWrt alone, with room for one update
    [int64]$missing = $minAndroid + $minLinux - $total
    if ($missing -lt 0) { $missing = 0 }
    return @{ Total = $total; MinAndroid = $minAndroid; MinLinux = $minLinux; MaxLinux = ($total - $minAndroid)
        Free = ($UsableEnd - $End); Ok = (($total - $minAndroid) -ge $minLinux); Missing = $missing }
}
# RepartitionSectors: the answer to "How many GiB for Linux?" as sectors, a multiple of 4096 of them (2 MiB). State
# is nan for anything but a number like 4 or 2.5 (written with a point in every language), small, large or ok.
function RepartitionSectors([string]$Gib, [int64]$MinLinux, [int64]$MaxLinux) {
    if ($Gib -notmatch '^([0-9]+\.?[0-9]*|\.[0-9]+)$') { return @{ State = 'nan'; Sectors = [int64]0 } }
    $g = [double]::Parse($Gib, [Globalization.CultureInfo]::InvariantCulture)
    if ($g -gt 1048576) { return @{ State = 'large'; Sectors = [int64]0 } }   # more than the numbers below hold
    [int64]$s = [math]::Truncate($g * 1073741824 / 512)
    $s = $s - ($s % 4096)
    $state = if ($s -lt $MinLinux) { 'small' } elseif ($s -gt $MaxLinux) { 'large' } else { 'ok' }
    return @{ State = $state; Sectors = $s }
}
# LayoutFigures: the numbers a refusal has to say (issue #32: "a different layout" alone identifies nothing) - the
# eMMC, where its partitions end, what is free and what the smallest installation lacks; with the userdata entry
# ($Info, GptShow) and its plan, userdata's own sectors and what a split lacks.
function LayoutFigures([int64]$Disk, [int64]$LastEnd, [int64]$Size, $Info, $Plan) {
    [int64]$need = 700MB
    [int64]$short = $need - $Size
    if ($short -lt 0) { $short = 0 }
    $mib = { param([int64]$b) [int64][math]::Floor($b / 1MB) }
    $up = { param([int64]$b) [int64][math]::Ceiling($b / 1MB) }
    $lines = @((T 'eMMC: {1} ({2} sectors); the partitions end at sector {3}; free after them: {4} MiB' (Gib ($Disk * 512)) $Disk $LastEnd (& $mib $Size)))
    $lines += (T 'needed behind the partitions: {1} MiB; missing: {2} MiB' (& $mib $need) (& $up $short))
    if ($Info -and $Plan) {
        $lines += (T 'userdata now:         {1} (sectors {2}..{3})' (Gib (($Info.End - $Info.First + 1) * 512)) $Info.First $Info.End)
        $lines += (T 'to share by shrinking it: {1} MiB up to sector {2}; needed: {3} MiB ({4} MiB Android + {5} MiB Linux); missing: {6} MiB' (& $mib ($Plan.Total * 512)) $Info.UsableEnd (& $mib (($Plan.MinAndroid + $Plan.MinLinux) * 512)) (& $mib ($Plan.MinAndroid * 512)) (& $mib ($Plan.MinLinux * 512)) (& $up ($Plan.Missing * 512)))
    }
    return @($lines | ForEach-Object { "  $_" })
}
# resize-last-partition.py in $Dir with these arguments: its lines, what it refuses with among them, and its exit
# code in $script:GptRc. It validates both GPT copies and every CRC before it says anything. Run from $Dir with file
# names only: it takes "head,tail" apart at the comma, and a Windows path may hold one.
function GptTool([string]$Dir, [string[]]$ToolArgs) {
    $ErrorActionPreference = 'Continue'
    $argv = @("$Top\tools\resize-last-partition.py") + $ToolArgs
    $out = @()
    Push-Location $Dir
    try { $out = @(Python @argv 2>&1 | ForEach-Object { "$_" }); $script:GptRc = $LASTEXITCODE }
    finally { Pop-Location }
    return $out
}
# The partition table as the tool wants it: the first 34 sectors of the eMMC and the last 33, as $Prefix.head and
# $Prefix.tail. A file from an earlier run never stands in for one that could not be read.
function GptDump([int64]$Disk, [string]$Prefix) {
    Remove-Item -Force "$Prefix.head", "$Prefix.tail" -ErrorAction SilentlyContinue
    SuDoToFile 'dd if=/dev/block/mmcblk0 bs=512 count=34 2>/dev/null' "$Prefix.head"
    SuDoToFile "dd if=/dev/block/mmcblk0 bs=512 skip=$($Disk - 33) count=33 2>/dev/null" "$Prefix.tail"
    if (-not (Test-Path "$Prefix.head") -or -not (Test-Path "$Prefix.tail")) { return $false }
    return ((Get-Item "$Prefix.head").Length -eq 34 * 512 -and (Get-Item "$Prefix.tail").Length -eq 33 * 512)
}
# a prepared installation waiting for this device (the make-room path below, on an earlier run): its checks first
$RESUMING = 0; $RESUME_TX = ''
if ($ResumeWanted -ne '0' -and ($ResumeWanted -eq '1' -or $resumeWaits)) {
    $rv = ResumeFacts
    if (-not $rv) {
        if ($ResumeWanted -eq '1') { Die (T 'could not read what identifies this device (serial, eMMC CID, userdata)') }
    } else {
        $RESUME_TX = if ($Transaction) { $Transaction } else { [string]((ResumePy find $BackupDir $rv.SERIAL $rv.CID) | Select-Object -First 1) }
        if (-not $RESUME_TX) {
            if ($ResumeWanted -eq '1') { Die (T 'no saved transaction for this device in {1} (--transaction FILE names one)' $BackupDir) }
        } elseif (-not (Test-Path -LiteralPath $RESUME_TX)) {
            Die (T '{1} not found' $RESUME_TX)
        } else {
            $RESUMING = 1
            Info 'resume.transaction' $RESUME_TX
            Say (T 'A prepared installation is waiting: {1}' $RESUME_TX)
            Write-Host ('  ' + (T 'EXPERIMENTAL: it continues the installation that made room by resetting Android. --no-resume ignores it.'))
            # every check of the transaction against the device, read only (tools\resume.py check)
            $g = Join-Path $Work 'gpt'
            New-Item -ItemType Directory -Force -Path $g | Out-Null
            $rs = $null
            if (GptDump $disk "$g\resume") { $rs = GptShow @(GptTool $g @('resume.head,resume.tail', '--disk-sectors', "$disk", '--name', 'userdata', '--show')) }
            if (-not $rs) { Die (T 'could not read the partition table') }
            $rr = [string]((ResumePy get $RESUME_TX region_start) | Select-Object -First 1) -replace '^region_start=', ''
            if ($rr -notmatch '^[0-9]+$') { Die (T 'could not read the transaction {1}' $RESUME_TX) }
            Remove-Item -Force "$g\resume-marker.bin", "$g\resume-bcb.bin" -ErrorAction SilentlyContinue
            SuDoToFile "dd if=/dev/block/mmcblk0 bs=512 skip=$rr count=8 2>/dev/null" "$g\resume-marker.bin"
            SuDoToFile 'dd if=/dev/block/by-name/misc bs=2048 count=1 2>/dev/null' "$g\resume-bcb.bin"
            $idArgs = @('identity', $Top)
            if ($PKG) { $idArgs += (Join-Path $PKG 'mu300\manifest') }
            $id = [string]((ResumePy @idArgs) | Select-Object -First 1)
            $out = [string]((ResumePy check $RESUME_TX "serial=$($rv.SERIAL)" "slot=$ANDROID_SLOT" "cid=$($rv.CID)" "node=$($rv.NODE)" `
                "start=$($rv.START)" "size=$($rv.SIZE)" "disk=$disk" "gpt_first=$($rs.First)" "gpt_end=$($rs.End)" "usable_end=$($rs.UsableEnd)" `
                "region_start=$([int64]($OFF / 512))" "region_end=$([int64](($OFF + $SIZE) / 512))" "marker=$g\resume-marker.bin" `
                "bcb=$g\resume-bcb.bin" "identity=$id") | Select-Object -First 1)
            Info 'resume.check' $out
            if ($script:ResumeRc -ne 0) {
                $why = $out -replace '^RESUME-REFUSED: ', ''
                if ($Check) {
                    Write-Host ('  ' + (T 'The prepared installation cannot be continued: {1}' $why))
                    $RESUMING = 0
                } else {
                    Die ((T 'The prepared installation cannot be continued: {1}' $why) + "`n" + (T 'Nothing was written. Keep the device as it is and keep {1}.' $RESUME_TX) + "`n" +
                        (T 'Another transaction: --transaction FILE. To install as if nothing had been prepared: --no-resume (the region is then treated as space that holds data).'))
                }
            } else {
                Write-Host ('  ' + (T 'the device, its slot, its partition table, the region, the token and this installer are the ones the transaction was made with'))
            }
        }
    }
}
# the partitions, for a picture of the eMMC: "name:first sector:sectors" of each (the stream only; sysfs, read only)
if ($script:MuProgress) { Info 'partitions' ((SuDo 'for p in /sys/block/mmcblk0/mmcblk0p*; do echo $(sed -n s/^PARTNAME=//p $p/uevent):$(cat $p/start):$(cat $p/size); done') -replace "`n", ' ') }
function Gib([int64]$b) { '{0:N1} GiB' -f ($b / 1GB) }
# Where the Linux filesystem goes (tools/storage.sh's choose_storage). The caller asks; an empty answer is the
# default (StorageDefault): internal storage; the card when there is too little room inside (the way that needs no
# repartitioning) or when it holds an installation already (that is what the device starts).
function StorageDefault([int64]$InternalBytes, [string]$SdState) {
    if ($InternalBytes -lt 700MB -or $SdState -eq 'yes') { return 'sd' } else { return 'internal' }
}
function ChooseStorage([string]$SdDev, [int64]$SdBytes, [int64]$InternalBytes, [string]$Forced, [string]$Answer, [string]$SdState) {
    if ($Forced -eq 'internal') { return 'internal' }
    if ($Forced -eq 'sd') { if (-not $SdDev) { throw 'MU300_STORAGE=sd, but there is no usable SD card in the device' }; return 'sd' }
    if ($Forced) { throw 'MU300_STORAGE must be internal or sd' }
    if (-not $SdDev) { return 'internal' }
    if (-not $Answer) { return (StorageDefault $InternalBytes $SdState) }
    if ($Answer -eq 'internal' -or $Answer -eq 'sd') { return $Answer }
    throw 'invalid choice'
}
# What Enter chooses (install.sh's systems_default): both when both fit, else Ubuntu when it fits, else OpenWrt. It was
# always 3, and on the 804 MiB a make-room run of 0.79 GiB leaves Enter stopped the reinstall (FINDINGS 153).
function SystemsDefault([int64]$Size, [int64]$NeedUbuntu, [int64]$NeedBoth) {
    if ($Size -ge $NeedBoth) { return '3' } elseif ($Size -ge $NeedUbuntu) { return '1' } else { return '2' }
}
# The line under "that choice needs ..." that names the choice that fits here (install.sh's too_small_for_choice);
# empty when nothing does
function ChoiceThatFits([int64]$Size, [int64]$NeedOpenWrt, [int64]$NeedUbuntu) {
    if ($Size -ge $NeedUbuntu) { return (T 'Choose 1 (Ubuntu) or 2 (OpenWrt) instead: one system fits here.') }
    if ($Size -ge $NeedOpenWrt) { return (T 'Choose 2 (OpenWrt) instead: it fits here.') }
    return ''
}
# The systems a choice stands for (1 Ubuntu, 2 OpenWrt, 3 both), the way install.sh's choose_systems has them. OpenWrt is
# plain (1) or with the MU300 control panel (2, "openwrt-luci" then stands in the list instead of "openwrt"); $Preset
# is MU300_OPENWRT (plain|luci) and answers without asking, an empty $Which is the default (plain).
function ChooseOpenWrt([string[]]$Oses, [string]$Preset, [string]$Which) {
    if ($Preset) {
        if ($Preset -eq 'plain') { $Which = '1' } elseif ($Preset -eq 'luci') { $Which = '2' } else { throw 'MU300_OPENWRT must be plain or luci' }
    } elseif (-not $Which) { $Which = '1' }
    if ($Which -ne '1' -and $Which -ne '2') { throw 'invalid choice' }
    if ($Which -eq '2') { return @($Oses | ForEach-Object { if ($_ -eq 'openwrt') { 'openwrt-luci' } else { $_ } }) }
    return @($Oses)
}
# boot/init starts a mu300sd card before anything on the eMMC: an internal installation made while such a card is
# in the slot never starts, so that choice needs the typed word (storage.sh's internal_over_card)
function InternalOverCard([string]$Where, [string]$SdState) { return ($Where -eq 'internal' -and $SdState -eq 'yes') }
# What the card holds, from the ext4 magic and label of its superblock: yes for a mu300sd filesystem, foreign for
# any other ext4, labelled or not (someone's data: android-install.sh refuses to format it), no for anything else
function SdState([string]$Magic, [string]$Label) {
    if ($Magic -ne '53ef') { return 'no' }
    if ($Label.Trim() -eq 'mu300sd') { return 'yes' }
    return 'foreign'
}
# A card installation needs a kernel that reads the card (tools/storage.sh's sd_kernel_ok): the mainline bundles
# that do say so in ./features; older ones gate the SD host off and the device would never find its card.
function SdKernelOk([int]$SdMode, [string]$Dir) {
    if ($SdMode -ne 1) { return $true }
    $f = Join-Path $Dir 'features'
    return [bool]((Test-Path $f) -and (@(Get-Content $f) -contains 'sdcard'))
}
# mu300-install.env, what android-install.sh is told (tools/storage.sh's write_install_env writes the same text).
# OFF/SIZE and their sectors are always the internal region: in SD mode SIZE is the card's by now and INT_SIZE keeps
# the region's, which the root-on-sd marker needs; the card's own size is read on the device.
function InstallEnvText($v) {
    [int64]$rs = if ([int]$v.SD_MODE -eq 1) { $v.INT_SIZE } else { $v.SIZE }
    [int64]$off = $v.OFF
    $lines = @("OFF=$off", "SIZE=$rs", "OFF_S=$([math]::Floor($off / 512))", "SIZE_S=$([math]::Floor($rs / 512))",
        "FORMAT=$($v.FORMAT)", "OSES=`"$(@($v.OSES) -join ' ')`"", "WIPE_LEGACY=$($v.WIPE_LEGACY)", "UPDATE=$($v.UPDATE)",
        "BOOT_OS=$($v.BOOT_OS)", "DEFAULT_LINUX=$($v.DEFAULT_LINUX)", "BOOT_ATTEMPTS=$($v.BOOT_ATTEMPTS)",
        "IMPORT_HOTSPOT=$($v.IMPORT_HOTSPOT)", "KERNEL=$($v.KERNEL)", "SD_MODE=$($v.SD_MODE)", "SD_DEV=$($v.SD_DEV)",
        "INTERNAL_EXISTS=$($v.INTERNAL_EXISTS)", "ADB=$(if ($v.ADB) { $v.ADB } else { 0 })", "ADB_KEY='$($v.ADB_KEY)'",
        "SYNC_BOOTCHAIN=$(if ($v.SYNC_BOOTCHAIN) { $v.SYNC_BOOTCHAIN } else { 0 })",
        "PWHASH='$($v.PWHASH)'", "KEEP_PW_OSES=`"$(@($v.KEEP_PW_OSES) -join ' ')`"")
    return (($lines -join "`n") + "`n")
}
# AdbPubKey: the first line of an adb public key file (adbkey.pub: "BASE64 user@host") as it goes into mu300-install.env
# inside single quotes - an Android RSA-2048 key and a name of [A-Za-z0-9@._:-] only - or $null (tools/storage.sh's
# adb_pubkey)
function AdbPubKey($path) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $line = @(Get-Content -LiteralPath $path -TotalCount 1)[0]
    if (-not $line) { return $null }
    $parts = @($line.Trim() -split ' ', 2)
    if ($parts[0] -cnotmatch '^QAAAA[A-Za-z0-9+/]{694}=$') { return $null }
    $name = if ($parts.Count -gt 1) { $parts[1] -replace '[^A-Za-z0-9@._:-]', '' } else { '' }
    if ($name.Length -gt 64) { $name = $name.Substring(0, 64) }
    if (-not $name) { $name = 'installer' }
    return "$($parts[0]) $name"
}
# VpnOnSystems: the systems among $oses of the Linux filesystem already on the device whose VPN is on (ENABLE=1 in
# etc/mu300/vpn.conf), as "os:ks" when its kill switch is on too - decided on the device by the images'
# lib/vpn-orphan.sh, pushed along - through a read-only mount taken down again at once (as install.sh's vpn_on_systems).
# The VPN is a module the new systems do not carry: it is then offered by default, and with the kill switch needed.
function VpnOnSystems($oses, $existing, $sdMode, $sdDev, $off, $size) {
    if ($existing -ne 'yes') { return @() }
    $envs = if ($sdMode -eq 1) { "MU300_SD_DEV=$sdDev" } else { "MU300_OFF=$off MU300_SIZE=$size" }
    if (-not (PushUnix "$Top\tools\android-mount-mu300root.sh" "$T/android-mount-mu300root.sh")) { return @() }
    if (-not (PushUnix "$Top\rootfs\overlay\opt\mu300\lib\vpn-orphan.sh" "$T/vpn-orphan.sh")) { return @() }
    $out = SuDo "$envs MU300_RO=1 sh $T/android-mount-mu300root.sh $T/mu300probe >/dev/null 2>&1 && { . $T/vpn-orphan.sh; for o in $(@($oses) -join ' '); do if vpn_killswitch_wanted $T/mu300probe/`$o; then echo `$o:ks; else case `$(vpn_conf_value $T/mu300probe/`$o/etc/mu300/vpn.conf ENABLE) in 1) echo `$o ;; esac; fi; done; sh $T/android-mount-mu300root.sh -u $T/mu300probe >/dev/null 2>&1; }; true"
    return @(([string]$out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { @($oses) -contains ($_ -replace ':ks$', '') })
}
# The passwords of the systems on the Linux filesystem already on the device, for an update that keeps them (install.sh's
# pw_states and pw_check, tools/storage.sh): android-pwcheck.sh, pushed along, looks through a read-only mount taken
# down again at once. Neither shows or hands anywhere a password or a hash: one word per system.
function PwMountEnv($sdMode, $sdDev, $off, $size) { if ($sdMode -eq 1) { "MU300_SD_DEV=$sdDev" } else { "MU300_OFF=$off MU300_SIZE=$size" } }
# PwStates: "os:SET", "os:EMPTY" (no password), "os:LOCKED" or "os:NOUSER" per system of $oses; nothing when the
# filesystem cannot be mounted
function PwStates($oses, $existing, $sdMode, $sdDev, $off, $size) {
    if ($existing -ne 'yes') { return @() }
    if (-not (PushUnix "$Top\tools\android-mount-mu300root.sh" "$T/android-mount-mu300root.sh")) { return @() }
    if (-not (PushUnix "$Top\tools\android-pwcheck.sh" "$T/android-pwcheck.sh")) { return @() }
    $envs = PwMountEnv $sdMode $sdDev $off $size
    $out = SuDo "$envs MU300_RO=1 sh $T/android-mount-mu300root.sh $T/mu300pw >/dev/null 2>&1 && { for o in $(@($oses) -join ' '); do case `$o in ubuntu) u=ubuntu ;; *) u=root ;; esac; echo `$o:`$(sh $T/android-pwcheck.sh -s $T/mu300pw/`$o `$u); done; sh $T/android-mount-mu300root.sh -u $T/mu300pw >/dev/null 2>&1 && rmdir $T/mu300pw 2>/dev/null; }; true"
    return @(([string]$out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^[a-z-]+:[A-Z]+$' })
}
# PwCheck OS PASSWORD: MATCH, NOMATCH, LOCKED, NOUSER or UNVERIFIABLE (nothing: the filesystem did not mount). The
# password goes to the device in a file (LF, UTF-8 without a BOM) removed from this computer at once, which
# android-pwcheck.sh reads on its standard input and the same command removes on the device; never on a command line.
function PwCheck($os, [string]$pw, $sdMode, $sdDev, $off, $size, [string]$pwEnv) {
    $u = if ($os -eq 'ubuntu') { 'ubuntu' } else { 'root' }
    $f = [IO.Path]::GetTempFileName()
    $pushed = $false
    try {
        [IO.File]::WriteAllText($f, "$pw`n", (New-Object Text.UTF8Encoding $false))
        Quiet { adb push $f "$T/mu300-pwcheck.in" } | Out-Null
        $pushed = $LASTEXITCODE -eq 0
    } finally { Remove-Item -LiteralPath $f -ErrorAction SilentlyContinue }
    if (-not $pushed) { return '' }
    $envs = PwMountEnv $sdMode $sdDev $off $size
    $out = SuDo "chmod 600 $T/mu300-pwcheck.in; $envs MU300_RO=recover sh $T/android-mount-mu300root.sh $T/mu300pw >/dev/null 2>&1 && { $pwEnv sh $T/android-pwcheck.sh $T/mu300pw/$os $u < $T/mu300-pwcheck.in; sh $T/android-mount-mu300root.sh -u $T/mu300pw >/dev/null 2>&1 && rmdir $T/mu300pw 2>/dev/null; }; rm -f $T/mu300-pwcheck.in; true"
    $last = @(([string]$out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($last.Count -eq 0) { return '' }
    return $last[-1]
}
# PwTools: the project's own tools that compute the hash on the device (install.sh's pw_tools, tools/storage.sh's
# pw_tools_push) - never a program of the Linux system being checked, which would run with Android's root (FINDINGS
# 126). From an unpacked zip its mu300\ (mu300-pwcheck when its release has one, and busybox); else the release's 5.4
# kernel bundle, fetched into the release's folder and checked against SHA256SUMS now (the download step finds it
# there and does not fetch it again). Both pushed, made executable once; returns the environment android-pwcheck.sh
# gets for them ('' without: then only an empty password can be confirmed, anything else is UNVERIFIABLE).
function PwTools {
    $bin = ''; $bb = ''
    try {
        if ($PKG) {
            foreach ($n in 'mu300-pwcheck', 'busybox') {
                $f = Join-Path $PKG "mu300\$n"
                if ((Test-Path -LiteralPath $f -PathType Leaf) -and (Get-Item -LiteralPath $f).Length -gt 0) {
                    if ($n -eq 'busybox') { $bb = $f } else { $bin = $f }
                }
            }
        } elseif ($Release) {
            $rel = "$Work\release\$Release"; $dir = "$Work\pwtools"
            New-Item -ItemType Directory -Force -Path $rel, $dir | Out-Null
            $base = if ($ReleaseUrl) { $ReleaseUrl } else { "https://github.com/$Repo/releases/download/$Release" }
            Invoke-WebRequest "$base/SHA256SUMS" -OutFile "$rel\SHA256SUMS" -UseBasicParsing
            $want = ''
            foreach ($line in Get-Content "$rel\SHA256SUMS") {
                $q = $line -split '\s+', 2
                if ($q.Count -eq 2 -and $q[1].TrimStart('*') -eq 'mu300-kernel.tar.gz') { $want = $q[0].ToLower() }
            }
            if (-not $want) { return '' }
            $k = "$rel\mu300-kernel.tar.gz"
            $have = if (Test-Path -LiteralPath $k) { (Get-FileHash -LiteralPath $k -Algorithm SHA256).Hash.ToLower() } else { '' }
            if ($have -ne $want) {
                Fetch "$base/mu300-kernel.tar.gz" "$k.part" | Out-Null
                if ((Get-FileHash -LiteralPath "$k.part" -Algorithm SHA256).Hash.ToLower() -ne $want) {
                    Remove-Item -LiteralPath "$k.part" -ErrorAction SilentlyContinue
                    return ''
                }
                Move-Item -Force -LiteralPath "$k.part" -Destination $k
            }
            foreach ($n in 'mu300-pwcheck', 'busybox') {
                Remove-Item -LiteralPath "$dir\$n" -ErrorAction SilentlyContinue
                Quiet { & tar -xzf $k -C $dir "./$n" } | Out-Null
                if ((Test-Path -LiteralPath "$dir\$n" -PathType Leaf) -and (Get-Item -LiteralPath "$dir\$n").Length -gt 0) {
                    if ($n -eq 'busybox') { $bb = "$dir\$n" } else { $bin = "$dir\$n" }
                }
            }
        }
    } catch { return '' }
    $envs = @()
    if ($bin) {
        Quiet { adb push $bin "$T/mu300-pwcheck" } | Out-Null
        if ($LASTEXITCODE -eq 0) { $envs += "MU300_PWCHECK_BIN=$T/mu300-pwcheck" }
    }
    if ($bb) {
        Quiet { adb push $bb "$T/mu300-pwbb" } | Out-Null
        if ($LASTEXITCODE -eq 0) { $envs += "MU300_PWCHECK_BB=$T/mu300-pwbb" }
    }
    if ($envs.Count -gt 0) { SuDo "chmod 755 $T/mu300-pwcheck $T/mu300-pwbb; true" | Out-Null }
    return ($envs -join ' ')
}
# ReadSecret QUESTION: one line typed without echo (the -Answers queue: its next line)
function ReadSecret($question) {
    if ($script:AnswerQueue) { return (NextAnswer $question -Secret) }
    $ss = Read-Host -AsSecureString $question
    return [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($ss))
}
function VpnDefault($on) { if (@($on).Count -gt 0) { 'yes' } else { 'no' } }
function SdExisting {
    $m = (SuDo "dd if=$SD_DEV bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1") -replace '\s', ''
    $l = (SuDo "dd if=$SD_DEV bs=1 skip=1144 count=16 2>/dev/null") -replace '\0', ''
    SdState $m $l
}
# ---------------------------------------------------------------- making room on a small eMMC (EXPERIMENTAL)
# install.sh's offer_repartition, the same questions and the same commands on the device. userdata fills the disk on
# the 32 GB variant, and on a 64 GB unit whose partition table a vendor firmware flash replaced (issue #32): there is
# no gap behind it and nothing to install into. Space can be made by shrinking userdata, and only userdata: it is
# the **last** partition, so its end moves and nothing else does - every other partition keeps its offset, and the
# AVB descriptors and the boot chain stay valid. That is the entire safety argument, and it is why this refuses to
# touch anything else: only the two copies of the partition table and the first 32 MiB of userdata are written, and
# only the partition table is read.
#
# It is still the most dangerous thing this installer can do. It rewrites the partition table and it erases
# everything in Android. Reported and first done by hand in issue #2; treat it as experimental.
#
# ---- reset and resume (EXPERIMENTAL; tools/resume.sh): ported from kano's desktop installer (kanoqwq/mu300-linux,
# clean-tf-7.2, desktop/installer.py and desktop/boot/part-prepare.sh; f6b0668d, 0e19fe32). userdata is shrunk as before,
# but Android's own recovery formats it (a bootloader message in misc, --wipe_data): with file-based encryption a
# filesystem cleared from outside is one Android cannot mount. A transaction and a backup are saved on this computer
# first, a token goes to the start of the new region (outside every partition, where the reset does not reach), and
# the next run continues when the device, its slot, the partition table, the region, the token and these tools still
# match (tools\resume.py check).
function OfferRepartition([int64]$Disk, [int64]$LastEnd, [int64]$Size) {
    Say (T 'Not enough free space - this looks like the 32 GB variant')
    $g = Join-Path $Work 'gpt'
    New-Item -ItemType Directory -Force -Path $g | Out-Null
    $report = T 'Please report the numbers above (eMMC size and where the partitions end); they identify the variant.'
    $refused = T 'only {1} MiB of free space after the last partition: this device has a different layout, nothing is changed.' ([int64][math]::Floor($Size / 1MB))
    if (-not (GptDump $Disk "$g\gpt")) {
        Die ((@((T 'could not read the partition table')) + @(LayoutFigures $Disk $LastEnd $Size $null $null) + @($report)) -join "`n")
    }

    $shown = @(GptTool $g @('gpt.head,gpt.tail', '--disk-sectors', "$Disk", '--name', 'userdata', '--show'))
    if ($script:GptRc -ne 0) {
        # not a table this path knows (userdata is not the last partition, a CRC does not match, ...): the tool's
        # reason, and the numbers
        Die ((@($refused) + @($shown | ForEach-Object { "  $_" }) + @(LayoutFigures $Disk $LastEnd $Size $null $null) + @($report)) -join "`n")
    }
    $info = GptShow $shown
    if (-not $info) {
        Die ((@((T 'could not read the userdata entry from the partition table')) + @(LayoutFigures $Disk $LastEnd $Size $null $null) + @($report)) -join "`n")
    }

    $plan = RepartitionPlan $info.First $info.End $info.UsableEnd   # sectors that userdata and Linux have to share
    Info 'makeroom.first' $info.First; Info 'makeroom.end' $info.End; Info 'makeroom.usable_end' $info.UsableEnd
    Info 'makeroom.min_android' $plan.MinAndroid; Info 'makeroom.min_linux' $plan.MinLinux; Info 'makeroom.max_linux' $plan.MaxLinux
    if (-not $plan.Ok) {
        Die ((@((T 'there is not enough room to split: {1} in total,' (Gib ($plan.Total * 512))), (T 'and Android needs at least {1} of it. Nothing is changed.' (Gib ($plan.MinAndroid * 512)))) + @(LayoutFigures $Disk $LastEnd $Size $info $plan)) -join "`n")
    }

    # the device's identity and how Android keeps its data (read only); kano's rule: encrypted, plain, or unknown
    $rf = ResumeFacts
    if (-not $rf) { Die (T 'could not read what identifies this device (serial, eMMC CID, userdata)') }
    if ([int64]$rf.START -ne $info.First -or [int64]$rf.START + [int64]$rf.SIZE - 1 -ne $info.End) {
        Die (T 'the partition table and the running kernel disagree about userdata; reboot Android and try again')
    }
    Info 'makeroom.crypto' $rf.CRYPTO; Info 'makeroom.crypto_state' $rf.STATE; Info 'makeroom.crypto_type' $rf.TYPE
    $cryptoText = switch ($rf.CRYPTO) { 'yes' { T 'encrypted' } 'no' { T 'not encrypted' } default { T 'unknown' } }
    Write-Host ''
    Write-Host "  eMMC:                 $(Gib ($Disk * 512))"
    Write-Host ('  ' + (T 'userdata now:         {1} (sectors {2}..{3})' (Gib (($info.End - $info.First + 1) * 512)) $info.First $info.End))
    Write-Host ('  ' + (T 'free behind it:       {1}' (Gib ($plan.Free * 512))))
    Write-Host ('  ' + (T 'to share:             {1}' (Gib ($plan.Total * 512))))
    Write-Host ('  ' + (T "Android's data:       {1} ({2})" $cryptoText ("$(if ($rf.STATE) { $rf.STATE } else { '?' })$(if ($rf.TYPE) { '/' + $rf.TYPE })")))
    Write-Host ''
    Write-Host ('  ' + (T 'Linux needs at least  {1} (OpenWrt) / 1.6 GiB (Ubuntu) / 2.4 GiB (both)' (Gib ($plan.MinLinux * 512))))
    Write-Host ('  ' + (T 'Android keeps at least {1}' (Gib ($plan.MinAndroid * 512))))
    Write-Host ''
    Write-Host ('  ' + (T 'THIS IS EXPERIMENTAL. It rewrites the partition table and ERASES everything in Android -'))
    Write-Host ('  ' + (T 'apps, photos, accounts, settings. Take a full backup first:  sh tools/backup-device.sh'))
    Write-Host ('  ' + (T 'Only userdata changes size; it is the last partition, so nothing else moves.'))
    Write-Host ('  ' + (T "Android's own recovery erases Android's data: the device restarts into it, resets Android (a factory"))
    Write-Host ('  ' + (T 'reset) and starts Android again, which takes a few minutes and more than one restart. Then set Android up'))
    Write-Host ('  ' + (T 'again, turn USB debugging back on and bring root back (Magisk: install the Magisk app again and let'))
    Write-Host ('  ' + (T 'Shell have root), and run this installer again: it finds what was prepared and continues.'))
    Write-Host ('  ' + (T 'What was prepared, and a backup of the partition table, misc, boot and the boot chain, are kept on this'))
    Write-Host ('  ' + (T 'computer in {1} - the reset erases everything of it on the device.' $BackupDir))
    Write-Host ''
    if ($Check) {
        Write-Host ('  ' + (T '-Check writes nothing. Run the installer without it to make room.'))
        return
    }
    # kano's rule: an encryption state that cannot be told is no ground to stand on
    if ($rf.CRYPTO -ne 'yes' -and $rf.CRYPTO -ne 'no') {
        Die (T "Android's encryption state is unknown ({1}); nothing is changed." "$(if ($rf.STATE) { $rf.STATE } else { '?' })/$(if ($rf.TYPE) { $rf.TYPE } else { '?' })")
    }
    if ((Ask (T 'Make room by shrinking userdata? (yes/no)') 'no' 'MU300_ANS_MAKE_ROOM') -ne 'yes') { Die (T 'nothing was changed.') }

    while ($true) {
        $lin = RepartitionSectors (Ask (T 'How many GiB for Linux? (the rest stays with Android)') '4' 'MU300_ANS_LINUX_GIB') $plan.MinLinux $plan.MaxLinux
        if ($lin.State -eq 'ok') {
            # userdata ends where the region the installers find behind it (FreeRegion) is the size asked for: it
            # starts one 2 MiB block after userdata and stops one block short of the disk's end, so a userdata that
            # ended that many sectors before the usable end left 4 MiB or more less (install.sh; FINDINGS 112)
            [int64]$newEnd = [math]::Floor(($Disk - 34) / 4096 - 1) * 4096 - $lin.Sectors - 4096 - 1
            if ($newEnd -gt $info.UsableEnd) { $newEnd = $info.UsableEnd - $lin.Sectors }
            $linRegion = FreeRegion ($newEnd + 1) $Disk
            if (($linRegion.End - $linRegion.Start) -lt $plan.MinLinux) { $lin.State = 'small' }
            elseif (($newEnd - $info.First + 1) -lt $plan.MinAndroid) { $lin.State = 'large' }
            else { break }
        }
        if ($lin.State -eq 'nan') { Write-Host ('  ' + (T 'give a number, like 4 or 2.5')) }
        elseif ($lin.State -eq 'small') { Write-Host ('  ' + (T 'too small: Linux needs at least {1}' (Gib ($plan.MinLinux * 512)))) }
        else { Write-Host ('  ' + (T 'too large: Android would be left with less than {1}' (Gib ($plan.MinAndroid * 512)))) }
    }
    Write-Host ''
    Write-Host ('  ' + (T 'userdata (Android):   {1}' (Gib (($newEnd - $info.First + 1) * 512))))
    Write-Host ('  ' + (T 'Linux region:         {1}' (Gib (($linRegion.End - $linRegion.Start) * 512))))
    Write-Host ''
    # the word itself, in capitals, in every language
    if ((Ask (T 'Type ERASE to rewrite the partition table and wipe Android''s data') '' 'MU300_ANS_ERASE_ANDROID') -cne 'ERASE') { Die (T 'nothing was changed.') }

    # a backup on this computer, before anything is written
    Step 'backup' 30
    $bd = Join-Path $BackupDir ((Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $rf.CID.Substring(24, 8))
    Say (T 'Backing up to {1}' $bd)
    ResumeBackup $bd $g
    Info 'resume.backup' $bd

    # the transaction, saved before the first write; the region by the installers' arithmetic behind the new end
    $rg = FreeRegion ($newEnd + 1) $Disk
    $tok = [string]((ResumePy token) | Select-Object -First 1)
    $idArgs = @('identity', $Top)
    if ($PKG) { $idArgs += (Join-Path $PKG 'mu300\manifest') }
    $id = [string]((ResumePy @idArgs) | Select-Object -First 1)
    if ($tok -notmatch '^[0-9a-f]{32}$' -or $id -notmatch '^[0-9a-f]{64}$') { Die (T 'could not compute the installer identity') }
    $tx = Join-Path $bd 'transaction.json'
    ResumePy new $tx "serial=$($rf.SERIAL)" "slot=$ANDROID_SLOT" "cid=$($rf.CID)" "node=$($rf.NODE)" "start=$($rf.START)" `
        "size=$($rf.SIZE)" "disk=$Disk" "target=$($newEnd - [int64]$rf.START + 1)" "need=$($rg.End - $rg.Start)" `
        "usable_end=$($info.UsableEnd)" "region_start=$($rg.Start)" "region_end=$($rg.End)" "token=$tok" "identity=$id" `
        "crypto=$($rf.CRYPTO)" "crypto_state=$($rf.STATE)" "crypto_type=$($rf.TYPE)" "backup=$bd" | Out-Null
    if ($script:ResumeRc -ne 0) { Die (T 'could not save the transaction; nothing was changed') }
    Info 'resume.transaction' $tx
    Write-Host ('  ' + (T 'transaction saved: {1}' $tx))
    ResumePy marker $tok "$g\marker.bin" | Out-Null
    ResumePy bcb "$g\bcb.bin" | Out-Null
    if (-not (Test-Path "$g\marker.bin") -or -not (Test-Path "$g\bcb.bin")) { Die (T 'could not prepare the files to write; nothing was changed') }

    Step 'repartition' 50
    Say (T 'Rewriting the partition table')
    Remove-Item -Force "$g\new.head", "$g\new.tail" -ErrorAction SilentlyContinue
    GptTool $g @('gpt.head,gpt.tail', '--disk-sectors', "$Disk", '--name', 'userdata', '--end-sector', "$newEnd", '--out', 'new') | ForEach-Object { Write-Host "  $_" }
    $made = $script:GptRc -eq 0 -and (Test-Path "$g\new.head") -and (Test-Path "$g\new.tail")
    if ($made) { $made = (Get-Item "$g\new.head").Length -eq 34 * 512 -and (Get-Item "$g\new.tail").Length -eq 33 * 512 }
    if (-not $made) { Die (T 'the resize was refused; nothing is changed') }
    # what goes to the device is what was made here, byte for byte (a push that stopped halfway leaves a short file)
    foreach ($n in 'new.head', 'new.tail', 'marker.bin', 'bcb.bin') {
        if (-not (ResumePush "$g\$n" "$T/mu300-$n")) {
            SuDo "rm -f $T/mu300-new.head $T/mu300-new.tail $T/mu300-marker.bin $T/mu300-bcb.bin" | Out-Null
            Die (T 'the new partition table did not arrive on the device as it was made; nothing is changed')
        }
    }
    $abBefore = (SuDo 'dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null | od -An -tx1 -v') -replace '\s', ''
    # the backup copy first: if power is lost between the two, the primary is still the old table and the device
    # boots, which is the recoverable order
    SuDo "dd if=$T/mu300-new.tail of=/dev/block/mmcblk0 bs=512 seek=$($Disk - 33) conv=fsync 2>/dev/null" | Out-Null
    SuDo "dd if=$T/mu300-new.head of=/dev/block/mmcblk0 bs=512 conv=fsync 2>/dev/null" | Out-Null

    Say (T 'Verifying')
    $null = GptDump $Disk "$g\after"
    $after = GptShow @(GptTool $g @('after.head,after.tail', '--disk-sectors', "$Disk", '--name', 'userdata', '--show'))
    if ($script:GptRc -ne 0 -or -not $after) {
        Die ((T 'the table on the device does not read back as valid GPT.') + "`n" + (T 'The backup copy at the end of the disk was written first, so Android''s own repair may still fix this. Do not power off.'))
    }
    if ($after.End -ne $newEnd) { Die (T 'the table did not take (userdata still ends at {1}). Nothing else was changed.' $after.End) }
    Write-Host ('  ' + (T 'userdata now ends at sector {1}' $after.End))

    # the token: the first 4 KiB of the region, outside every partition from now on
    SuDo "dd if=$T/mu300-marker.bin of=/dev/block/mmcblk0 bs=512 seek=$($rg.Start) count=8 conv=notrunc,fsync 2>/dev/null" | Out-Null
    if ((ResumeSha "dd if=/dev/block/mmcblk0 bs=512 skip=$($rg.Start) count=8 2>/dev/null | sha256sum") -ne (Get-FileHash "$g\marker.bin" -Algorithm SHA256).Hash.ToLower()) {
        Die (T 'the token did not read back from the device; misc was not changed, Android keeps its data. Run the installer again.')
    }
    # the reset, by Android's own recovery: the bootloader message in the first 2 KiB of misc
    Say (T 'Asking Android''s recovery to reset Android''s data on the next boot')
    SuDo "dd if=$T/mu300-bcb.bin of=/dev/block/by-name/misc bs=2048 count=1 conv=notrunc,fsync 2>/dev/null" | Out-Null
    $abAfter = (SuDo 'dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null | od -An -tx1 -v') -replace '\s', ''
    if ((ResumeSha 'dd if=/dev/block/by-name/misc bs=2048 count=1 2>/dev/null | sha256sum') -ne (Get-FileHash "$g\bcb.bin" -Algorithm SHA256).Hash.ToLower() -or $abAfter -ne $abBefore) {
        Die (T 'the request for the reset did not read back from misc. The partition table is changed already: run the installer again, it continues from here.')
    }
    SuDo "sync; rm -f $T/mu300-new.head $T/mu300-new.tail $T/mu300-marker.bin $T/mu300-bcb.bin" | Out-Null
    ResumePy set $tx 'state=prepared' | Out-Null
    if ($script:ResumeRc -ne 0) { Die (T 'could not save the transaction') }

    Say (T 'Done - the device restarts into the reset now')
    Write-Host ('  ' + (T "Android's recovery formats Android's data on the next start; then Android starts as new. Set it up,"))
    Write-Host ('  ' + (T 'turn USB debugging on, bring root back (the Magisk app, Shell allowed), and run this installer again.'))
    Write-Host ('  ' + (T 'transaction: {1}' $tx))
    $rb = Ask (T 'Reboot now? (yes/no)') 'yes' 'MU300_ANS_REBOOT'
    Progress 'done' 'result' 'ok' 'repartitioned' 'yes' 'resume' 'pending' 'transaction' $tx
    if ($rb -eq 'yes') { Quiet { adb reboot } | Out-Null }
}

# What each choice needs: the installed systems measure ~320 MiB (OpenWrt) and ~580 MiB (Ubuntu), and an update
# keeps the previous one as <os>.old while the new one is unpacked, so allow for two of each plus working room.
[int64]$NEED_OPENWRT = 800MB; [int64]$NEED_UBUNTU = 1600MB; [int64]$NEED_BOTH = 2400MB
Info 'need.openwrt' $NEED_OPENWRT; Info 'need.ubuntu' $NEED_UBUNTU; Info 'need.both' $NEED_BOTH
Write-Host (T 'eMMC: {1} ({2} sectors), partitions end at {3} (sector {4}), free after them: {5}' (Gib ($disk * 512)) $disk (Gib ($lastEnd * 512)) $lastEnd (Gib $REG_SIZE))
Info 'region.partition' $IN_PART
if ($IN_PART -eq 1) { Write-Host (T 'Linux partition: the GPT partition named mu300root, made by another installer, {1} at offset {2}; Linux goes there, the partition table is not changed' (Gib $SIZE) $OFF) }
if ($FOREIGN_IMAGE) {
    Write-Host (T 'Note: the Android data partition holds a Linux image file from another installer (/data/mu300linux/openwrt.ext4). This installer cannot update that layout, and after this installation the device starts the new system, not that one. The file is left as it is: delete it in Android to free its space.')
    Info 'region.foreign_image' 1
}
# The SD card as the other place for it (boot/init looks there first): "<block device> <512-byte sectors> <type>"
# of the first mmc disk that is not the eMMC - its first partition when it has one, the whole card otherwise. The
# type keeps a second eMMC or an SDIO function out. Single quotes only (see the data check below).
$SD_DEV = ''; [int64]$SD_BYTES = 0
$sd = @((SuDo 'for b in /sys/block/mmcblk[1-9]; do [ -e $b/device/type ] || continue; n=${b##*/}; if [ -e $b/${n}p1 ]; then echo /dev/block/${n}p1 $(cat $b/${n}p1/size) $(cat $b/device/type); else echo /dev/block/$n $(cat $b/size) $(cat $b/device/type); fi; break; done').Trim() -split '\s+')
if ($sd.Count -eq 3 -and $sd[2] -eq 'SD' -and $sd[1] -match '^[0-9]+$') {
    if ([int64]$sd[1] * 512 -ge 700MB) { $SD_DEV = $sd[0]; $SD_BYTES = [int64]$sd[1] * 512 }
    else { Write-Host (T 'SD card present but smaller than 700 MiB; not used'); Info 'sd.small' 1 }
}
Info 'sd.device' $SD_DEV; Info 'sd.bytes' $SD_BYTES
$SD_MODE = 0; $sdEx = 'no'
if ($RESUMING -eq 1 -and -not $Check) {
    Info 'storage' 'internal'            # the region that was prepared for it
} elseif ($Check) {
    if ($SD_DEV) {
        $sdEx = SdExisting
        Info 'sd.state' $sdEx
        Write-Host (T 'SD card: {1}, {2}, existing mu300sd filesystem: {3}' $SD_DEV (Gib $SD_BYTES) (T $(if ($sdEx -eq 'yes') { 'yes' } else { 'no' })))
        if ($sdEx -eq 'foreign') { Write-Host ('  ' + (T 'the SD card holds another Linux (ext4) filesystem; the installer will not format it')) }
    }
} else {
    $ans = ''
    if ($SD_DEV) { $sdEx = SdExisting }
    if ($SD_DEV -and -not $env:MU300_STORAGE) {
        $ans = Ask (T 'Where should the Linux filesystem go: internal storage or the SD card ({1}, {2})? (internal/sd)' $SD_DEV (Gib $SD_BYTES)) (StorageDefault $SIZE $sdEx) 'MU300_STORAGE'
    }
    try { $where = ChooseStorage $SD_DEV $SD_BYTES $SIZE ([string]$env:MU300_STORAGE) $ans $sdEx }
    catch { Die (T $_.Exception.Message) }
    if (InternalOverCard $where $sdEx) {
        Write-Host (T 'The SD card ({1}) holds a Linux installation (mu300sd), and the device always starts that one first: an installation to internal storage does not start while this card is in the slot.' $SD_DEV)
        Write-Host (T 'Take the card out before the device restarts, or erase its installation first with the uninstaller.')
        if ((Ask (T 'Type internal to install to internal storage anyway') 'no' 'MU300_ANS_INTERNAL_OVER_CARD') -ne 'internal') { Die (T 'cancelled') }
    }
    Info 'storage' $where
    if ($where -eq 'sd') {
        $SD_MODE = 1
        # Another Linux filesystem on the card may be someone's data, and the device refuses to format it. Say so
        # now, not after the password, the download and the build.
        if ($sdEx -eq 'foreign') {
            Die ((T 'the SD card ({1}) holds another Linux (ext4) filesystem, and the installer never formats one that is not its own (mu300sd).' $SD_DEV) + "`n" + (T 'Copy off what you need and format the card elsewhere, use another card, or install to internal storage (MU300_STORAGE=internal).'))
        }
    }
}
# Smaller eMMC variants leave less room behind userdata, and how much is needed depends on the choice further
# down - OpenWrt alone fits in a few hundred megabytes. So refuse only what cannot hold anything at all, and
# check the real requirement once the systems are known. There is nowhere else to put this region on these
# devices: userdata is metadata-encrypted (dm-default-key), so an image file inside it cannot be read from
# Linux, and the spare-looking blackbox and fulldumpdb partitions are written by the firmware itself.
if ($SD_MODE -eq 0 -and $partList.Count -gt 1) {
    Die (T 'The eMMC has {1} partitions named mu300root, and which one holds Linux cannot be told. Nothing was changed: remove the one that is not used (with the installer that made it) and try again.' $partList.Count)
}
if ($SD_MODE -eq 0 -and $SIZE -lt 700MB) {
    # -Check with a card: a real run offers the card (as the default) and needs no repartitioning
    if ($Check -and $SD_DEV -and $sdEx -ne 'foreign') {
        Write-Host (T 'result: {1}' (T 'too little free eMMC space for Linux; the installer will offer the SD card instead (no repartitioning needed)'))
        Write-Host ("`n" + (T 'Nothing was written. Android version: {1}' (SuDo 'getprop ro.build.display.id')))
        Info 'verdict.code' 'sd-only'; Progress 'done' 'result' 'ok'
        exit 0
    }
    OfferRepartition $disk $lastEnd $SIZE   # installs nothing: it refuses, or makes room for a second pass
    Progress 'done' 'result' 'ok'
    exit 0
}

# Whether the ext4 superblock of a region at BYTES (its second KiB) lies on the eMMC. A read past the end of the disk
# never returns on this device: the dd spins on one core, survives kill -9 and heats the SoC until a reboot (issues
# #43, #52, #65: a 32 GB eMMC whose table ends at its end puts the region's start at the disk's end, and the fixed
# offset of the first releases lies beyond a 32 GB eMMC altogether).
function RegionOnDisk([int64]$bytes) { [math]::Floor(($bytes + 2048) / 512) -le $disk }
$existing = 'no'; $PART_FS = 'none'
# the places in the order boot/init looks: the free region, the partition named mu300root, the old fixed offset (not
# when it lies inside that partition); a filesystem in the partition is never larger than the partition
$cands = @($REG_OFF); if ($IN_PART -eq 1) { $cands += $PART_OFF }; $cands += [int64]27762098176
foreach ($cand in $cands) {
    if (-not (RegionOnDisk $cand)) { continue }
    if ($cand -eq 27762098176 -and $IN_PART -eq 1 -and $cand -ge $PART_OFF -and $cand -lt ($PART_OFF + $PART_SIZE)) { continue }
    $m = (SuDo "dd if=/dev/block/mmcblk0 bs=1 skip=$($cand + 1080) count=2 2>/dev/null | od -An -tx1") -replace '\s', ''
    # two bytes on the disk always read as four hex digits: anything else is no answer, never "no installation"
    if ($m -notmatch '^[0-9a-f]{4}$') { Die (T 'no answer from the device') }
    $l = (SuDo "dd if=/dev/block/mmcblk0 bs=1 skip=$($cand + 1144) count=16 2>/dev/null") -replace '\0', ''
    if ($IN_PART -eq 1 -and $cand -eq $PART_OFF -and $m -eq '53ef') { $PART_FS = $(if ($l.Trim() -eq 'mu300root') { 'mu300root' } else { 'ext4' }) }
    if ($m -eq '53ef' -and $l.Trim() -eq 'mu300root') {
        $blocks = [int64]((SuDo "dd if=/dev/block/mmcblk0 bs=1 skip=$($cand + 1028) count=4 2>/dev/null | od -An -tu4").Trim())
        $OFF = $cand; $SIZE = $blocks * 4096; $existing = 'yes'
        if ($IN_PART -eq 1 -and $cand -eq $PART_OFF) { if ($SIZE -gt $PART_SIZE) { $SIZE = $PART_SIZE } } else { $IN_PART = 0 }
        break
    }
}
# another ext4 in that partition is not this installer's to format (the device refuses it too)
if ($SD_MODE -eq 0 -and $IN_PART -eq 1 -and $existing -ne 'yes' -and $PART_FS -eq 'ext4') {
    Die (T 'The partition named mu300root holds another Linux (ext4) filesystem, not labelled mu300root, and the installer never formats one that is not its own. Nothing was changed.')
}
Write-Host (T 'Linux region: offset {1}, {2}, existing mu300root filesystem: {3}' $OFF (Gib $SIZE) (T $existing))
$INTERNAL_EXISTS = $(if ($existing -eq 'yes') { 1 } else { 0 }); [int64]$INT_SIZE = 0
Info 'region.existing' $existing; Info 'region.offset' $OFF; Info 'region.size' $SIZE
if ($SD_MODE -eq 1) {
    # from here on SIZE and existing describe the card; OFF and INT_SIZE keep the internal region for the marker
    $INT_SIZE = $SIZE; $SIZE = $SD_BYTES; $existing = SdExisting
    Write-Host (T 'Linux filesystem: SD card {1}, {2}, existing mu300sd filesystem: {3}' $SD_DEV (Gib $SIZE) (T $existing))
}

$dirty = 0
if ($SD_MODE -eq 0 -and $existing -eq 'no' -and $RESUMING -eq 0) {
    $step = [int64]($SIZE / 1MB / 16)
    $probe = (0..15 | ForEach-Object { [int64]($OFF / 1MB) + $_ * $step }) -join ' '
    # Empty is 0x00 or 0xFF (what an eMMC reads back after an erase). No double quotes in the command: Windows
    # PowerShell 5.1 drops them on the way to adb, tr got an unquoted \000 - "delete the character 0" - and every
    # empty MiB counted as data, so on Windows an empty region was always "16 of 16 not empty". \\ reaches the
    # device's shell as one backslash, which tr then reads as the start of an octal escape.
    $dirtyOut = (SuDo "n=0; for s in $probe; do c=`$(dd if=/dev/block/mmcblk0 bs=1048576 skip=`$s count=1 2>/dev/null | tr -d \\000\\377 | wc -c); [ `$c -gt 0 ] && n=`$((n + 1)); done; echo `$n").Trim()
    # [int]'' is 0: no answer passed as an empty region
    if ($dirtyOut -notmatch '^[0-9]+$') { Die (T 'no answer from the device') }
    $dirty = [int]$dirtyOut
    Write-Host (T 'data check: {1} of 16 samples contain data' $dirty)
    Info 'region.dirty' $dirty
    if ($dirty -gt 0) {
        $head = (SuDo "dd if=/dev/block/mmcblk0 bs=1048576 skip=$([int64]($OFF / 1MB)) count=1 2>/dev/null | od -An -tx1 -N32") -replace '\s+', ' '
        Write-Host ('  ' + (T 'start of the region: {1}' $head.Trim()))
    }
}
if ($RESUMING -eq 1) {
    # (what the region holds is what was userdata, erased with the word ERASE when it was prepared, and the token)
    $vcode = 'resume'; $verdict = T 'OK: the region prepared by making room, checked against its transaction'
} elseif ($existing -eq 'yes') {
    $vcode = 'existing'; $verdict = T 'OK: a MU300 Linux installation is already present (it can be kept or replaced)'
} elseif ($SD_MODE -eq 1) {
    $vcode = 'sd'; $verdict = T 'OK: the SD card will be formatted; everything on it is erased'
} elseif ($dirty -gt 0 -and $IN_PART -eq 1) {
    $vcode = 'dirty'; $verdict = T 'WARNING: the partition named mu300root is not empty, and it holds no Linux filesystem of this project. Installing overwrites it'
} elseif ($dirty -gt 0) {
    $vcode = 'dirty'; $verdict = T 'WARNING: the unpartitioned space is not empty; it may be used by this firmware. Installing overwrites it'
} elseif ($SIZE -ge 20GB) {
    $vcode = 'tested'; $verdict = T 'OK: free and empty, same layout as the tested device (~32 GiB after userdata on the 64 GB eMMC)'
} elseif ($SIZE -ge $NEED_BOTH) {
    $vcode = 'both'; $verdict = T 'OK: free and empty, smaller than on the tested device but enough for both systems'
} elseif ($SIZE -ge $NEED_UBUNTU) {
    $vcode = 'one'; $verdict = T 'OK: free and empty, but room for one system only (Ubuntu or OpenWrt, not both)'
} else {
    $vcode = 'openwrt'; $verdict = T 'OK: free and empty, but small: OpenWrt fits, Ubuntu does not'
}
Write-Host (T 'result: {1}' $verdict)
Info 'verdict' $verdict; Info 'verdict.code' $vcode
if ($Check) {
    Write-Host ("`n" + (T 'Nothing was written. Android version: {1}' (SuDo 'getprop ro.build.display.id')))
    Progress 'done' 'result' 'ok'
    exit 0
}
if ($dirty -gt 0 -and (Ask (T 'Type overwrite to use this region anyway') 'no' 'MU300_ANS_OVERWRITE') -ne 'overwrite') { Die (T 'cancelled') }

# ---------------------------------------------------------------- the release (tools/releases.sh's release_resolve)
# -Release <tag> or -ReleaseUrl: that one, nothing asked. Otherwise the newest full release; when GitHub also has a
# prerelease published after it, the question is whether to install that beta instead (default: no). It is looked up
# here, before the other questions: a GitHub that does not answer stops the run before the password and the pulls.
# ReleasePick: what tools/release_pick.py says about GitHub's list of releases in $JsonFile - Latest and Beta, ''
# for what there is none of. install.sh and the graphical installer decide with the same file.
function ReleasePick([string]$JsonFile) {
    $ErrorActionPreference = 'Continue'
    $r = @{ Latest = ''; Beta = '' }
    foreach ($l in @(Python "$Top\tools\release_pick.py" $JsonFile 2>$null)) {
        if ("$l" -match '^(LATEST|BETA)=([A-Za-z0-9._+-]+)\s*$') {
            if ($Matches[1] -eq 'LATEST') { $r.Latest = $Matches[2] } else { $r.Beta = $Matches[2] }
        }
    }
    if (-not $r.Latest) { $r.Beta = '' }
    return $r
}
# ReleaseList: GitHub's list, picked. No token is needed. Both are '' when the list does not come (no network, the
# API's rate limit, a proxy's page): the caller then looks up the newest release alone, as it always did.
function ReleaseList {
    $none = @{ Latest = ''; Beta = '' }
    $f = Join-Path ([IO.Path]::GetTempPath()) "mu300-releases-$PID.json"
    $ProgressPreference = 'SilentlyContinue'
    try {
        Invoke-WebRequest "https://api.github.com/repos/$Repo/releases?per_page=15" -OutFile $f -UseBasicParsing -TimeoutSec 30
        if (-not (Test-Path $f)) { return $none }
        return (ReleasePick $f)
    } catch { return $none }
    finally { Remove-Item $f -ErrorAction SilentlyContinue }
}
# The release tags (docs/FINDINGS.md 125): vYYYY.MM.DD up to v2026.10.27, where the "day" was a counter, and from then
# on vYYYY.MM.DD.N, the real (UTC) day the release was built and N = 1..99 for the releases of that day. Every
# four-field tag is newer than every three-field one; four-field tags compare by the date, then by N, as numbers. The
# rule of tools/releases.sh and tools/release_pick.py, held to the same table (tests/fixtures/release-tags.tsv).
# TagKey: a number in the order of the releases (1YYYYMMDD00, 2YYYYMMDDNN), or $null for what is no release tag
function TagKey([string]$Tag) {
    # ([0-9], not \d: .NET's \d takes every script's digits; \z, not $: $ lets a newline at the end through)
    if ($Tag -cmatch '^v([0-9]{4})\.([0-9]{2})\.([0-9]{2})\z') { return [long]('1' + $Matches[1] + $Matches[2] + $Matches[3] + '00') }
    if ($Tag -cmatch '^v([0-9]{4})\.(0[1-9]|1[0-2])\.(0[1-9]|[12][0-9]|3[01])\.([1-9][0-9]?)\z') {
        return [long]('2' + $Matches[1] + $Matches[2] + $Matches[3] + $Matches[4].PadLeft(2, '0'))
    }
    return $null
}
# TagNewer A B: release A is newer than release B; false when either is no release tag
function TagNewer([string]$A, [string]$B) {
    $ka = TagKey $A; $kb = TagKey $B
    return [bool]($null -ne $ka -and $null -ne $kb -and $ka -gt $kb)
}
# BetaAsked: whether the beta question is put. Not with -Answers: that file holds one answer per question in a fixed
# order, and a question that comes and goes with GitHub's releases would shift every answer behind it.
function BetaAsked([string]$Latest, [string]$Beta, [bool]$Positional) { return [bool]($Latest -and $Beta -and $Beta -ne $Latest -and -not $Positional) }
if (-not $Release) {
    if ($ReleaseUrl) { $Release = 'custom' }
    else {
        $rl = ReleaseList
        $Release = $rl.Latest
        if (-not $Release) {
            try { $Release = (Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest" -UseBasicParsing).tag_name }
            catch { Die (T 'cannot find the newest release of {1} (use -Release <tag> to pick one)' $Repo) }
        } elseif (BetaAsked $Release $rl.Beta ([bool]$script:AnswerQueue)) {
            Write-Host ('  ' + (T 'A beta is out: {1}, a prerelease for testing, newer than the newest full release {2}.' $rl.Beta $Release))
            if ((Ask (T 'Install the beta {1} instead of {2}? (yes/no)' $rl.Beta $Release) 'no' 'MU300_ANS_BETA') -eq 'yes') { $Release = $rl.Beta }
        }
    }
}
Info 'release' $Release

Step 'choices' 10
Say (T 'What should be installed?')
if ($PKG) {
    # a package's system, Ubuntu and kernel are its own: nothing to ask, only whether they fit here
    Write-Host ('  ' + (T 'This package installs {1} with kernel {2} (release {3}); nothing is downloaded.' $PM.SYSTEM $PM.KERNEL $PM.TAG))
    Write-Host ('  ' + (T 'Another system or kernel: the zip of that combination, or ./install.sh of the project.'))
    $OSES = @($PM.OS); $BOOT_OS = $PM.OS; $UBUNTU = $(if ($PM.UBUNTU) { $PM.UBUNTU } else { '24.04' })
    if ($DEVICE -eq 'v50' -and $PM.KERNEL -eq '5.4') { Die (T 'The ZTE V50 runs a mainline kernel only: take the zip for kernel 6.18, 7.2 or 7.3 of this release') }
    $need = if ($PM.OS -eq 'ubuntu') { $NEED_UBUNTU } else { $NEED_OPENWRT }
    if ($SIZE -lt $need) {
        $needMib = [int64]($need / 1MB); $haveMib = [int64]($SIZE / 1MB)
        $fit = if ($SIZE -ge $NEED_OPENWRT) { "`n" + (T 'An OpenWrt zip of this release fits here.') } else { '' }
        Die ((T 'that choice needs about {1} MiB and this device has {2} MiB of free space' $needMib $haveMib) + $fit)
    }
} else {
Write-Host ('  ' + (T '1) Ubuntu LTS: 24.04 or 26.04, asked next (full distribution, apt, ~500 MiB RAM in use)'))
Write-Host ('  ' + (T '2) OpenWrt {1} (router, LuCI web UI, ~140 MiB RAM in use)' '25.12.6'))
Write-Host ('  ' + (T '3) both (switch later with: mu300-os ubuntu|openwrt)'))
if ($SIZE -lt $NEED_BOTH) {
    $fits = if ($SIZE -ge $NEED_UBUNTU) { T 'one system fits, not both' } else { T 'only OpenWrt fits' }
    Write-Host ('  ' + (T '(this device has {1}: {2})' (Gib $SIZE) $fits))
}
switch (Ask (T 'Choice') (SystemsDefault $SIZE $NEED_UBUNTU $NEED_BOTH) 'MU300_ANS_SYSTEMS') {
    '1' { $OSES = @('ubuntu') }
    '2' { $OSES = @('openwrt') }
    '3' { $OSES = @('ubuntu', 'openwrt') }
    default { Die (T 'invalid choice') }
}
# a choice that does not fit stops here, before the question of which OpenWrt, and says which one fits
$need = if ($OSES.Count -eq 2) { $NEED_BOTH } elseif ($OSES[0] -eq 'ubuntu') { $NEED_UBUNTU } else { $NEED_OPENWRT }
if ($SIZE -lt $need) {
    $needMib = [int64]($need / 1MB); $haveMib = [int64]($SIZE / 1MB)
    $fit = ChoiceThatFits $SIZE $NEED_OPENWRT $NEED_UBUNTU
    Die ((T 'that choice needs about {1} MiB and this device has {2} MiB of free space' $needMib $haveMib) + $(if ($fit) { "`n" + $fit } else { '' }))
}
if ($OSES -contains 'openwrt') {
    $owPreset = [string]$env:MU300_OPENWRT
    if ($owPreset -and $owPreset -notin 'plain', 'luci') { Die (T 'MU300_OPENWRT must be plain or luci') }
    $owAnswer = ''
    if (-not $owPreset) {
        Say (T 'Which OpenWrt?')
        Write-Host ('  ' + (T '1) OpenWrt {1}: the standard LuCI web interface' '25.12.6'))
        Write-Host ('  ' + (T '2) OpenWrt {1} with the MU300 control panel: dashboard, cellular locks, SMS, AT terminal, USB modes (by kanoqwq)' '25.12.6'))
        $owAnswer = Ask (T 'Choice') '1' 'MU300_OPENWRT'
    }
    try { $OSES = @(ChooseOpenWrt $OSES $owPreset $owAnswer) } catch { Die (T 'invalid choice') }
}
$BOOT_OS = $OSES[0]
if ($OSES.Count -eq 2) {
    $BOOT_OS = Ask (T 'Which one should boot ({1})' ($OSES -join '/')) $BOOT_OS 'MU300_ANS_BOOT_OS'
    if ($BOOT_OS -notin $OSES) { Die (T 'invalid system') }
}
$UBUNTU = '24.04'
if ($OSES -contains 'ubuntu') {
    Say (T 'Which Ubuntu?')
    Write-Host ('  ' + (T '1) 24.04 LTS  the longest tested, supported until 2029'))
    Write-Host ('  ' + (T '2) 26.04 LTS  the newest (systemd 259, newer packages), supported until 2031; kernel 6.18, 7.2 or 7.3'))
    switch (Ask (T 'Ubuntu') '1' 'MU300_ANS_UBUNTU') {
        { $_ -in '1', '24.04' } { $UBUNTU = '24.04' }
        { $_ -in '2', '26.04' } { $UBUNTU = '26.04' }
        default { Die (T 'invalid choice') }
    }
}
}
# the release file of a system: Ubuntu 26.04 has its own, 24.04 keeps the name it always had
function RootfsFile($os) { if ($os -eq 'ubuntu' -and $UBUNTU -eq '26.04') { 'mu300-ubuntu-26.04-rootfs.tar.gz' } else { "mu300-$os-rootfs.tar.gz" } }
$DEFAULT_LINUX = if ((Ask (T 'Boot Linux by default instead of Android (falls back to Android if Linux fails)? (yes/no)') 'yes' 'MU300_ANS_DEFAULT_LINUX') -eq 'yes') { 1 } else { 0 }
$BOOT_ATTEMPTS = 5
if ($DEFAULT_LINUX -eq 1) {
    Write-Host ''
    Write-Host ('  ' + (T 'How many failed Linux boots in a row before the device goes back to Android by itself?'))
    Write-Host ''
    Write-Host ('  ' + (T 'A boot counts as failed when it never finishes starting up - the power goes, the battery runs out, or'))
    Write-Host ('  ' + (T 'Linux hangs - before about a minute after power-on. One boot that does finish resets the count.'))
    Write-Host ''
    Write-Host ('  ' + (T '* Higher is more forgiving: a flaky cable or a couple of power cuts while it is starting will not throw you'))
    Write-Host ('  ' + (T '  back into Android.'))
    Write-Host ('  ' + (T '* Lower gets you to Android sooner if Linux is really broken.'))
    Write-Host ('  ' + (T '* It is also your way back to Android with no computer at hand: cut the power while it is starting this'))
    Write-Host ('  ' + (T '  many times in a row.'))
    Write-Host ''
    Write-Host ('  ' + (T "1 is how this project used to behave (a single interrupted boot returns to Android). 1-6; the device's"))
    Write-Host ('  ' + (T 'boot counter has no room for more. It can be changed later with: mu300-next-boot attempts N'))
    $BOOT_ATTEMPTS = Ask (T 'Failed boots before Android (1-6)') '5' 'MU300_ANS_BOOT_ATTEMPTS'
    if ($BOOT_ATTEMPTS -notmatch '^[1-6]$') { Die (T 'enter a number from 1 to 6') }
}
$IMPORT_HOTSPOT = if ((Ask (T "Copy Android's hotspot name and password to Linux? (yes/no)") 'yes' 'MU300_ANS_HOTSPOT') -eq 'yes') { 1 } else { 0 }
$gpu = Ask (T 'Include the Mali GPU (OpenCL) userspace (~90 MiB)? (yes/no)') 'yes' 'MU300_ANS_GPU'
# the VPN is not part of the systems: a module of its own repository, which goes onto the Linux partition (as the vpn
# extra) only when wanted
Write-Host ('  ' + (T 'The VPN (mu300-vpn, with Xray and sing-box) is a module of its own: {1}. It can also be added later on the device:' 'https://github.com/dikeckaan/mu300-linux-vpn'))
Write-Host '    sudo mu300-extra install vpn'
# a system being replaced whose VPN is on keeps it only with the module: then it is the default
$vpnOn = @(VpnOnSystems $OSES $existing $SD_MODE $SD_DEV $OFF $SIZE)
$vpnKs = @($vpnOn | Where-Object { $_ -match ':ks$' } | ForEach-Object { $_ -replace ':ks$', '' })
if ($vpnOn.Count -gt 0) {
    Write-Host ('  ' + (T 'The VPN is on in the system being replaced ({1}): without the module it stays off after the installation.' (($vpnOn | ForEach-Object { $_ -replace ':ks$', '' }) -join ' ')))
}
$vx = Ask (T 'Install the VPN module (about 40 MB more to download, 120 MB on the device)? (yes/no)') (VpnDefault $vpnOn) 'MU300_ANS_VPN'
# its kill switch on too: without the module nothing would enforce it (android-install.sh refuses the same, later)
if ($vx -ne 'yes' -and $vpnKs.Count -gt 0) {
    Die (T 'The VPN is on with its kill switch in {1}: an update without the VPN module would leave it to nothing. Answer yes to install the module, or turn the VPN off there first (ENABLE=0 in /etc/mu300/vpn.conf).' ($vpnKs -join ' '))
}
# adb over USB to Linux is on in every system and lets every computer in, with a root shell and no password (the
# owner's decision of 2026-10-13, FINDINGS 70; on the device: mu300-usb adb auth open|keys, mu300-usb adb off). The
# one thing to choose here is to close it to all but this computer from the first boot on: its adb key, which the
# installer runs with anyway, is then the one authorised, and adb takes authorised keys only.
$ADB = 0; $ADB_KEY = ''
if ((Ask (T "adb to Linux over USB is on and lets every computer in: a root shell without a password for anyone who plugs in a cable. Let in only this computer's adb key (~/.android/adbkey.pub) instead? (yes/no)") 'no' 'MU300_ANS_ADB') -eq 'yes') {
    $adbHome = if ($env:ANDROID_USER_HOME) { $env:ANDROID_USER_HOME } else { Join-Path $HOME '.android' }
    $ADB_KEY = AdbPubKey (Join-Path $adbHome 'adbkey.pub')
    if (-not $ADB_KEY) { Die (T 'no adb key of this computer to authorise ({1})' (Join-Path $adbHome 'adbkey.pub')) }
    $ADB = 1
}
$EXTRA_VPN = $null
# the web panel for Ubuntu (Cockpit and the MU300 page): the panel extra, optional; offline from the release (Cockpit's
# packages in it), so the first boot needs no network for it
$px = 'no'
$EXTRA_PANEL = $null
if ($OSES -contains 'ubuntu') {
    Write-Host ('  ' + (T 'The web panel for Ubuntu (Cockpit with an MU300 page: signal, SMS, VPN, Wi-Fi, updates, a terminal; on the LAN only) is optional. It can also be added later on the device:'))
    Write-Host '    sudo mu300-extra install panel'
    # (not part of a package: it is a file of the release, not of the zip)
    if ($PKG) { Write-Host ('  ' + (T 'It is not part of this package.')) }
    else { $px = Ask (T 'Install the web panel for Ubuntu (about 24 MB more to download, 50 MB on the device)? (yes/no)') 'no' 'MU300_ANS_PANEL' }
}
$KERNEL = $null
if ($PKG) { $KERNEL = $PM.KERNEL } else {
Say (T 'Which kernel?')
Write-Host ('  ' + (T "1) 5.4   Unisoc's vendor kernel (Android 12 base): the longest tested, everything this project supports"))
Write-Host ('  ' + (T '2) 6.18  mainline Linux, current long-term (LTS) release: newer drivers and security fixes, the same'))
Write-Host ('  ' + (T '         functions (hotspot, mobile data, SMS, Bluetooth, VPN, GPU); no USB-C video output yet'))
Write-Host ('  ' + (T '3) 7.2   mainline Linux, stable release: newer drivers, the same functions'))
Write-Host ('  ' + (T '         as 6.18; tested less than 6.18'))
Write-Host ('  ' + (T '4) 7.3   the newest mainline Linux (7.3 for now): the newest drivers, the same functions'))
Write-Host ('  ' + (T '         as 6.18; tested less than 7.2'))
Write-Host ('  ' + (T 'This can be changed later on the device: sudo mu300-update kernel 5.4|6.18|7.2|7.3'))
while (-not $KERNEL) {
    switch (Ask (T 'Kernel') $(if ($DEVICE -eq 'v50') { '2' } else { '1' }) 'MU300_ANS_KERNEL') {
        { $_ -in '1', '5.4' } {
            if ($DEVICE -eq 'v50') {
                Write-Host ('  ' + (T 'The ZTE V50 runs a mainline kernel only (6.18, 7.2 or 7.3).'))
            } elseif ($UBUNTU -eq '26.04') {
                Write-Host ('  ' + (T 'Ubuntu 26.04 needs a mainline kernel (6.18, 7.2 or 7.3): its programs use system calls that 5.4 does not have (tar, for one, cannot unpack folders there).'))
            } else { $KERNEL = '5.4' }
        }
        { $_ -in '2', '6.18' } { $KERNEL = '6.18' }
        { $_ -in '3', '7.2' } { $KERNEL = '7.2' }
        { $_ -in '4', '7.3' } { $KERNEL = '7.3' }
        default { Write-Host ('  ' + (T 'enter 1, 2, 3 or 4')) }
    }
}
}
$FORMAT = 0; $WIPE_LEGACY = 0; $UPDATE = 0
if ($existing -eq 'no') {
    $FORMAT = 1
} else {
    Write-Host ''
    Write-Host ('  ' + (T 'A MU300 Linux installation is already on this device.'))
    Write-Host ('    ' + (T 'update  install the new release over the systems and keep everything you added or changed in them'))
    Write-Host ('    ' + (T '        (files, settings, accounts; packages you installed come back at the first boot)'))
    Write-Host ('    ' + (T 'wipe    erase the Linux filesystem and install from scratch'))
    switch (Ask (T 'update or wipe') 'update' 'MU300_ANS_EXISTING') {
        'update' { $UPDATE = 1 }
        'wipe' { $FORMAT = 1 }
        default { Die (T 'invalid choice') }
    }
    if ($FORMAT -eq 0 -and $OSES -contains 'ubuntu') { $WIPE_LEGACY = 1 }
}
if ($SD_MODE -eq 1 -and $FORMAT -eq 1) {
    if ((Ask (T 'Everything on the SD card ({1}, {2}) will be erased. Type ERASE to continue' $SD_DEV (Gib $SIZE)) 'no' 'MU300_ANS_ERASE_SD') -cne 'ERASE') { Die (T 'cancelled') }
}
# The password. An update keeps the accounts and passwords of the systems it replaces when the user says so and shows
# that they know the current one (checked on the device by android-pwcheck.sh, with the hash's own method; an empty
# one, OpenWrt's root as it ships, is confirmed by an empty entry). Three wrong entries stop the run before anything is
# written (install.sh's password block). The systems installed new, and an update whose user says no, get the new
# password asked for below.
$KEEP_PW_OSES = @(); $NEW_PW_OSES = @($OSES); $p1 = ''; $p2 = ''
if ($UPDATE -eq 1) {
    $pwHave = @()
    foreach ($l in @(PwStates $OSES $existing $SD_MODE $SD_DEV $OFF $SIZE)) {
        $st = $l.Substring($l.IndexOf(':') + 1)
        if ($st -in 'SET', 'EMPTY', 'LOCKED') { $pwHave += $l.Substring(0, $l.IndexOf(':')) }
    }
    if ($pwHave.Count -gt 0) {
        Write-Host ''
        Write-Host ('  ' + (T 'The installed {1} has a login password of its own.' ($pwHave -join ' ')))
        Write-Host ('    ' + (T 'yes  keep it: the current password is asked once and checked on the device'))
        Write-Host ('    ' + (T 'no   set a new password'))
        switch (Ask (T 'Keep the current password? (yes/no)') 'yes' 'MU300_ANS_KEEP_PASSWORD') {
            'yes' {
                $pwEnv = PwTools
                foreach ($os in $pwHave) {
                    $pwu = if ($os -eq 'ubuntu') { 'ubuntu' } else { 'root' }
                    $osv = $os.ToUpper().Replace('-', '_')
                    $q = T 'Current password of "{1}" in the installed {2} (only Enter if it has none)' $pwu $os
                    $tries = 0
                    while ($true) {
                        $tries++
                        if ($script:MuAnswersMode) {
                            $cid = "MU300_ANS_CURRENT_PASSWORD_$osv"
                            if (-not (AnswerLookup $cid).Set) { $cid = 'MU300_ANS_CURRENT_PASSWORD' }
                            if (-not (AnswerLookup $cid).Set) { Die (T 'no answer to the question "{1}": set {2} (nothing is asked and nothing is assumed in answers mode)' $q 'MU300_ANS_CURRENT_PASSWORD') }
                            # one MU300_ANS_CURRENT_PASSWORD may answer for every system (a wrong one stops below)
                            [void]$script:MuAsked.Remove($cid)
                            $cur = AnswerNamed $q '' $cid -Secret
                        } else {
                            $cid = 'MU300_ANS_CURRENT_PASSWORD'
                            $cur = ReadSecret $q
                        }
                        Progress 'question' 'id' $cid 'text' $q 'default' '' 'secret' '1'
                        $res = PwCheck $os ([string]$cur) $SD_MODE $SD_DEV $OFF $SIZE $pwEnv
                        $cur = $null
                        Info "password.$os" $res
                        if ($res -eq 'MATCH') {
                            Write-Host ('  ' + (T 'confirmed: the password of {1} stays as it is' $os))
                            $KEEP_PW_OSES += $os
                            break
                        } elseif ($res -eq 'NOMATCH') {
                            Write-Host ('  ' + (T 'that is not the current password of "{1}" in {2}' $pwu $os))
                        } elseif ($res -eq 'LOCKED') {
                            Die (T '"{1}" in the installed {2} has no password login, so there is none to confirm. Nothing was changed: run the installer again and answer no to set a new password.' $pwu $os)
                        } else {
                            Die (T 'the current password of "{1}" in the installed {2} could not be checked on the device ({3}). Nothing was changed: run the installer again and answer no to set a new password.' $pwu $os $(if ($res) { $res } else { 'no answer' }))
                        }
                        if ($script:MuAnswersMode -or $tries -ge 3) {
                            Die (T 'the current password of "{1}" in the installed {2} was not confirmed. Nothing was changed: run the installer again and answer no to set a new password.' $pwu $os)
                        }
                    }
                }
            }
            'no' { }
            default { Die (T 'invalid choice') }
        }
    }
    $NEW_PW_OSES = @($OSES | Where-Object { $KEEP_PW_OSES -notcontains $_ })
}
if ($NEW_PW_OSES.Count -gt 0) {
    if ($script:MuAnswersMode) {
        # MU300_ANS_PASSWORD: no event and no line of output ever shows it
        $p1 = AnswerNamed (T 'Password for the "ubuntu" user (Ubuntu) and "root" (OpenWrt)') '' 'MU300_ANS_PASSWORD' -Secret
        $p2 = $p1
        Progress 'question' 'id' 'MU300_ANS_PASSWORD' 'text' (T 'Password for the "ubuntu" user (Ubuntu) and "root" (OpenWrt)') 'default' '' 'secret' '1'
    } elseif ($script:AnswerQueue) {
        $p1 = NextAnswer (T 'Password for the "ubuntu" user (Ubuntu) and "root" (OpenWrt)') -Secret
        $p2 = NextAnswer (T 'Repeat') -Secret
    } else {
        $pw1 = Read-Host -AsSecureString (T 'Password for the "ubuntu" user (Ubuntu) and "root" (OpenWrt)')
        $pw2 = Read-Host -AsSecureString (T 'Repeat')
        $p1 = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($pw1))
        $p2 = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($pw2))
    }
    if ($p1 -ne $p2 -or $p1.Length -lt 6) { Die (T 'passwords differ or are shorter than 6 characters') }
}

New-Item -ItemType Directory -Force -Path "$Work\dumps", "$Work\firmware" | Out-Null
Step 'pull' 15
Say (T 'Pulling device data into {1} (stays on this computer)' $Work)
# Android's own boot image is the base of the Linux one (its header, its AVB footer)
SuDoToFile "cat /dev/block/by-name/boot_$ANDROID_SLOT" "$Work\dumps\boot_$ANDROID_SLOT.img"
SuDoToFile 'dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null' "$Work\dumps\misc-head.bin"
if (-not (Test-Path "$Work\dumps\boot_$ANDROID_SLOT.img") -or (Get-Item "$Work\dumps\boot_$ANDROID_SLOT.img").Length -lt 1MB) { Die (T 'pulling boot_{1} failed' $ANDROID_SLOT) }
# extract_subset.py builds the tree beside its place and renames it only when it is complete, so a
# directory that is there holds the whole subset - and on Windows it also holds windows-source.tar.gz,
# which is where the tools take the subset from there (see the header). An older run of the extractor
# died on the property area's names and left a partial directory that the old check accepted as it was,
# so both things it must have are tested instead of the directory alone.
$subset = "$Work\android-subset"
$haveSubset = (Test-Path "$subset\vendor\bin\modem_control") -and (Test-Path "$subset\linkerconfig\ld.config.txt")
if ($haveSubset -and $env:OS -eq 'Windows_NT') { $haveSubset = Test-Path "$subset\windows-source.tar.gz" }
if (-not $haveSubset) {
    Python "$Top\android-vendor\extract_subset.py" $subset
    if ($LASTEXITCODE -ne 0) { Die (T '{1} failed' 'extract_subset.py') }
}
foreach ($f in 'wcnmodem.bin', 'gnssmodem.bin', 'wifi_board_config.ini', 'wifi_board_config_ab.ini', 'bt_configure_pskey.ini', 'bt_configure_rf.ini') {
    foreach ($d in '/odm/firmware', '/vendor/firmware', '/vendor/etc') {
        if ((SuDo "[ -f $d/$f ] && echo y") -eq 'y') { SuDoToFile "cat $d/$f" "$Work\firmware\$f"; break }
    }
}
if ($gpu -eq 'yes') {
    # every run, not only the first: the script keeps what was already pulled and fills in the rest, and a
    # run that was interrupted used to leave a half-full directory behind that the old guard trusted
    $env:MU300_CLOSURE_ROOT = "$Work\android-gpu-subset"
    Python "$Top\android-vendor\pull_closure.py" /vendor/lib64/libOpenCL.so /vendor/lib64/egl/libGLES_mali.so /vendor/lib64/hw/vulkan.ums9620.so
}

# ($Release: looked up before the questions, see ReleaseList)
# Linux on a needs systems that know it (release v2026.10.10 on); a tag that is not a release tag is not judged
if ($LINUX_SLOT -eq 'a' -and (TagKey $Release) -and (TagNewer 'v2026.10.10' $Release)) {
    Die (T 'Android runs from slot b, and release {1} cannot boot Linux from slot a; use {2} or newer' $Release 'v2026.10.10')
}
Step 'download' 20
$REL = "$Work\release\$Release"
New-Item -ItemType Directory -Force -Path $REL | Out-Null
# $ASSETS: where the release's files are - downloaded into $REL, or a package's payload\ (the kernels are unpacked
# into $REL either way)
$ASSETS = $REL
$vpnFromModule = $false
if ($PKG) {
    Say (T 'Checking the files of this package')
    $ASSETS = Join-Path $PKG 'payload'
    foreach ($pf in @(@($PM.KERNEL_ASSET, $PM.SHA256_KERNEL), @($PM.ROOTFS_ASSET, $PM.SHA256_ROOTFS))) {
        Write-Host "  $($pf[0])"
        $why = PackageCheck $PKG $pf[0] $pf[1]
        if ($why -eq 'missing') { Die (T '{1} is missing from the package (payload/)' $pf[0]) }
        if ($why) { Progress 'verify' 'file' $pf[0] 'result' 'mismatch'; Die (T 'checksum mismatch for {1}' $pf[0]) }
        Progress 'verify' 'file' $pf[0] 'result' 'ok'
    }
    # the VPN is not in a zip: the module of its own repository, which is a download
    if ($vx -eq 'yes') { $vpnFromModule = $true }
} else {
Say (T 'Downloading release {1}' $Release)
$base = if ($ReleaseUrl) { $ReleaseUrl } else { "https://github.com/$Repo/releases/download/$Release" }
Invoke-WebRequest "$base/SHA256SUMS" -OutFile "$REL\SHA256SUMS" -UseBasicParsing
$sums = @{}
foreach ($line in Get-Content "$REL\SHA256SUMS") {
    $p = $line -split '\s+', 2
    if ($p.Count -eq 2) { $sums[$p[1].TrimStart('*')] = $p[0] }
}
$files = @('mu300-kernel.tar.gz') + ($OSES | ForEach-Object { RootfsFile $_ })
if ($KERNEL -ne '5.4') { $files += "mu300-kernel-$KERNEL.tar.gz" }
# the VPN: a release from before the module (its systems carry mu300-vpn) has the engines as its own vpn extra, from the
# same SHA256SUMS; a newer one has neither, and the module comes from its own repository (below)
if ($vx -eq 'yes') {
    if ($sums.ContainsKey('mu300-extra-vpn.tar.gz')) { $files += 'mu300-extra-vpn.tar.gz'; $EXTRA_VPN = "$REL\mu300-extra-vpn.tar.gz" }
    else { $vpnFromModule = $true }
}
if ($px -eq 'yes') {
    if ($sums.ContainsKey('mu300-extra-panel-offline.tar.gz')) {
        $files += 'mu300-extra-panel-offline.tar.gz'; $EXTRA_PANEL = "$REL\mu300-extra-panel-offline.tar.gz"
    } else {
        Write-Host ('  ' + (T 'release {1} has no web panel for Ubuntu; it can be added on the device later: sudo mu300-extra install panel' $Release))
    }
}
foreach ($f in $files) {
    if (-not $sums.ContainsKey($f)) {
        $why = if ($KERNEL -ne '5.4' -and $f -eq "mu300-kernel-$KERNEL.tar.gz") { ' ' + (T '(choose kernel 5.4, or a newer release)') } else { '' }
        Die ((T '{1} is not part of release {2}' $f $Release) + $why)
    }
    $have = if (Test-Path "$REL\$f") { (Get-FileHash "$REL\$f" -Algorithm SHA256).Hash.ToLower() } else { '' }
    if ($have -ne $sums[$f]) {
        Write-Host "  $f"
        Fetch "$base/$f" "$REL\$f.part"
        if ((Get-FileHash "$REL\$f.part" -Algorithm SHA256).Hash.ToLower() -ne $sums[$f]) { Progress 'verify' 'file' $f 'result' 'mismatch'; Die (T 'checksum mismatch for {1}' $f) }
        Move-Item -Force "$REL\$f.part" "$REL\$f"
        Progress 'verify' 'file' $f 'result' 'ok'
    } else { Progress 'verify' 'file' $f 'result' 'cached' }
}
}
if ($vpnFromModule) { FetchVpnModule }
# the 5.4 bundle: its kernel, and for every kernel the busybox, logdw and modules of the boot image's first segment.
# A package with a mainline kernel has no 5.4 bundle: its boot image is that kernel with its own generic segment,
# which brings init's busybox, logdw and modules (build-boot-image.py checks that it does).
$HAVE54 = (-not $PKG) -or $KERNEL -eq '5.4'
Remove-Item -Recurse -Force "$REL\kernel" -ErrorAction SilentlyContinue
if ($HAVE54) {
    New-Item -ItemType Directory -Force -Path "$REL\kernel" | Out-Null
    & tar -xzf "$ASSETS\mu300-kernel.tar.gz" -C "$REL\kernel"
    # the 5.4 modules of a device of its own (u30air); the V50 has none, it runs as the F50 does (its mainline
    # kernel is checked below)
    if (($DEVICE -notin @('f50', 'v50')) -and -not (Test-Path "$REL\kernel\modules-$DEVICE")) { Die (T 'release {1} does not support this device yet; use a newer one' $Release) }
}
# a mainline kernel (6.18, 7.2, 7.3): its bundle, unpacked
$KMAIN = $null
if ($KERNEL -ne '5.4') {
    $KMAIN = "$REL\kernel-$KERNEL"
    Remove-Item -Recurse -Force $KMAIN -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $KMAIN | Out-Null
    & tar -xzf "$ASSETS\mu300-kernel-$KERNEL.tar.gz" -C $KMAIN
    foreach ($k in 'Image', 'ramdisk-generic.lz4', 'kernel.release') {
        if (-not (Test-Path "$KMAIN\$k")) { Die (T '{1} is incomplete' "mu300-kernel-$KERNEL.tar.gz") }
    }
    # a bundle names the devices it runs on; older mainline kernels do not bring up the U30 Air's USB (FINDINGS 33c)
    if ($DEVICE -ne 'f50' -and -not ((Test-Path "$KMAIN\devices") -and ((Get-Content "$KMAIN\devices") -match "\b$DEVICE\b"))) {
        Die (T 'release {1} does not support this device yet; use a newer one' $Release)
    }
    if (-not (SdKernelOk $SD_MODE $KMAIN)) {
        Die (T 'the {1} kernel of release {2} cannot read the SD card: choose kernel 5.4, a newer release, or internal storage (MU300_STORAGE=internal)' $KERNEL $Release)
    }
}

Step 'vendor' 60
Say (T 'Adding the vendor files from your device to the images')
foreach ($os in $OSES) {
    $argv = @("$Top\tools\vendor-overlay.py", '--os', $os, '--firmware', "$Work\firmware",
        '--android-subset', "$Work\android-subset", '--out', "$Work\mu300-vendor-$os.tar.gz")
    if ($gpu -eq 'yes' -and (Test-Path "$Work\android-gpu-subset")) { $argv += @('--gpu-subset', "$Work\android-gpu-subset") }
    # the tarball, not $KMAIN: modules whose names differ only in case (xt_HL.ko, xt_hl.ko) collapse into one file
    # in a tree on NTFS (FINDINGS 212)
    if ($KMAIN) { $argv += @('--kernel-bundle', "$ASSETS\mu300-kernel-$KERNEL.tar.gz") }
    Python @argv
    if ($LASTEXITCODE -ne 0) { Die (T '{1} failed' 'vendor-overlay.py') }
}
# not `| Python ...`: that helper is an advanced function with no pipeline-bound parameter, so the
# binding fails, and its body would not forward $input to the child's stdin even if it bound
$PWHASH = if ($p1) { ($p1 | & $script:PyExe "$Top\tools\sha512crypt.py").Trim() } else { '' }

Step 'bootimage' 68
Say (T 'Building the boot image')
WriteUnix "$Work\init" (((Get-Content -Raw "$Top\boot\init") -replace '(?m)^ROOT_OFFSET=[0-9]*', "ROOT_OFFSET=$OFF"))
# a mainline kernel: its kernel, and its generic ramdisk segment behind this one (its init and modules win) - the
# image that "mu300-update kernel 6.18" (or 7.2, 7.3) writes on the device
$bootArgs = @("$Top\boot\build-boot-image.py", '--stock-boot', "$Work\dumps\boot_$ANDROID_SLOT.img", '--misc-head', "$Work\dumps\misc-head.bin",
    '--kernel', $(if ($KMAIN) { "$KMAIN\Image" } else { "$REL\kernel\Image" }),
    '--init', "$Work\init", '--ueventd-perms', "$Top\android-vendor\ueventd-perms.sh",
    '--android-subset', "$Work\android-subset", '--out', "$Work\boot-linux-slot$LINUX_SLOT.img", '--device', $DEVICE, '--linux-slot', $LINUX_SLOT)
if ($HAVE54) {
    $bootArgs += @('--modules', "$REL\kernel\modules", '--busybox', "$REL\kernel\busybox", '--logdw', "$REL\kernel\logdw")
    # init's e2fsck (FINDINGS 182): in the bundles of the releases since it came
    if (Test-Path "$REL\kernel\e2fsck") { $bootArgs += @('--e2fsck', "$REL\kernel\e2fsck") }
    if (Test-Path "$REL\kernel\modules-u30air") { $bootArgs += @('--device-modules', "u30air=$REL\kernel\modules-u30air") }
}
if ($KMAIN) { $bootArgs += @('--append-ramdisk', "$KMAIN\ramdisk-generic.lz4") }
# a failed build must stop here: otherwise an earlier image (or none) would be written to the device
Remove-Item "$Work\boot-linux-slot$LINUX_SLOT.*" -ErrorAction SilentlyContinue
Python @bootArgs | Out-Null
if ($LASTEXITCODE -ne 0 -or -not (Test-Path "$Work\boot-linux-slot$LINUX_SLOT.img")) { Die (T '{1} failed' 'build-boot-image.py') }

Step 'confirm' 72
Say (T 'Ready to install')
Write-Host ('  ' + (T 'source:         {1}' (T 'prebuilt release {1} + vendor files from this device' $Release)))
$sysText = ($OSES -join ' ') + $(if ($OSES -contains 'ubuntu') { " (Ubuntu $UBUNTU)" } else { '' })
Write-Host ('  ' + (T 'systems:        {1} (boots: {2})' $sysText $BOOT_OS))
Write-Host ('  ' + (T 'kernel:         {1}' "$KERNEL$(if ($KMAIN) { " (mainline, $((Get-Content "$KMAIN\kernel.release").Trim()))" })"))
$extras = @($(if ($EXTRA_VPN) { 'vpn' }), $(if ($EXTRA_PANEL) { 'panel' }) | Where-Object { $_ })
Write-Host ('  ' + (T 'extras:         {1}' $(if ($extras.Count -gt 0) { $extras -join ' ' } else { T 'none' })))
Write-Host ('  ' + (T 'adb to Linux:   {1}' $(if ($ADB -eq 1) { T 'on, only for the adb key of this computer' } else { T 'on, for every computer: a root shell without a password (on the device: mu300-usb adb auth keys, or mu300-usb adb off)' })))
Write-Host ('  ' + (T 'default boot:   {1}' $(if ($DEFAULT_LINUX -eq 1) { T 'Linux (Android after {1} failed boots in a row)' $BOOT_ATTEMPTS } else { T 'Android, Linux on demand' })))
$fsText = if ($FORMAT -eq 0) { T 'keep existing' } elseif ($SD_MODE -eq 1) { T 'CREATE new ext4 (erases the SD card)' } else { T 'CREATE new ext4 (erases the Linux region)' }
Write-Host ('  ' + (T 'filesystem:     {1}' $fsText))
if ($KEEP_PW_OSES.Count -gt 0) {
    $pwNew = if ($NEW_PW_OSES.Count -gt 0) { '; ' + (T 'the new one in {1}' ($NEW_PW_OSES -join ' ')) } else { '' }
    Write-Host ('  ' + (T 'password:       kept as it is in {1} (confirmed){2}' ($KEEP_PW_OSES -join ' ') $pwNew))
}
if ($UPDATE -eq 1) { Write-Host ('  ' + (T 'update:         everything you added to the chosen systems or changed in them is kept (/etc/mu300/update-report)')) }
if ($SD_MODE -eq 1) {
    Write-Host ('  ' + (T 'writes:         SD card {1}, boot_{2}, 32 bytes of misc (the eMMC region, boot_{3}, GPT and userdata are not touched)' $SD_DEV $LINUX_SLOT $ANDROID_SLOT))
} else {
    Write-Host ('  ' + (T 'writes:         Linux region at offset {1}, boot_{2}, 32 bytes of misc (boot_{3}, GPT and userdata are not touched)' $OFF $LINUX_SLOT $ANDROID_SLOT))
}
Write-Host ('  ' + $BC_LINE)
if ($BL_STATE -eq 'locked') { Write-Host ('  ' + (T 'WARNING: {1}' $BL_WARN)) }
# the word itself, in capitals (-cne: PowerShell's -ne does not tell install from INSTALL)
if ((Ask (T 'Type INSTALL to continue') 'no' 'MU300_ANS_CONFIRM') -cne 'INSTALL') { Die (T 'cancelled') }

Step 'copy' 75
Say (T 'Copying to the device')
foreach ($f in 'android-mount-mu300root.sh', 'android-install.sh', 'android-bootchain.sh') { PushUnix "$Top\tools\$f" "$T/$f" | Out-Null }
foreach ($os in $OSES) {
    & adb push "$ASSETS\$(RootfsFile $os)" "$T/mu300-$os.tar.gz" | Out-Null
    & adb push "$Work\mu300-vendor-$os.tar.gz" "$T/mu300-vendor-$os.tar.gz" | Out-Null
}
# android-install.sh puts every pushed mu300-extra-<name>.tar.gz onto the Linux partition (extra/<name>); the VPN
# module goes as the vpn extra, and each system links it into itself at its first boot (mu300-extra link)
if ($EXTRA_VPN) { & adb push $EXTRA_VPN "$T/mu300-extra-vpn.tar.gz" | Out-Null }
# the web panel goes as the panel extra; Ubuntu's first boot installs Cockpit from it in the background (mu300-extra link)
if ($EXTRA_PANEL) { & adb push $EXTRA_PANEL "$T/mu300-extra-panel.tar.gz" | Out-Null }
$envFile = "$Work\mu300-install.env"
$envText = (InstallEnvText @{ OFF = $OFF; SIZE = $SIZE; INT_SIZE = $INT_SIZE; FORMAT = $FORMAT; OSES = $OSES
    WIPE_LEGACY = $WIPE_LEGACY; UPDATE = $UPDATE; BOOT_OS = $BOOT_OS; DEFAULT_LINUX = $DEFAULT_LINUX
    BOOT_ATTEMPTS = $BOOT_ATTEMPTS; IMPORT_HOTSPOT = $IMPORT_HOTSPOT; KERNEL = $KERNEL; SD_MODE = $SD_MODE
    SD_DEV = $SD_DEV; INTERNAL_EXISTS = $INTERNAL_EXISTS; ADB = $ADB; ADB_KEY = $ADB_KEY
    SYNC_BOOTCHAIN = $SYNC_BOOTCHAIN; PWHASH = $PWHASH; KEEP_PW_OSES = $KEEP_PW_OSES })
# an update to an older release: the device refuses it unless told (tools/android-install.sh, the downgrade block)
if ($env:MU300_DOWNGRADE -eq 'yes') { $envText += "DOWNGRADE=1`n" }
WriteUnix $envFile $envText
& adb push $envFile "$T/mu300-install.env" | Out-Null
Remove-Item $envFile
Step 'device' 80
Say (T 'Installing on the device')
$log = SuDo "sh $T/android-install.sh"
Write-Host $log
# kept as install.sh keeps it: a timed-out SD-card stage (FINDINGS 50) names itself there, with the way out
$devLog = "$Work\device-install.log"
Set-Content -Path $devLog -Value $log
if ($log -notmatch 'MU300-INSTALL-OK') {
    Die ((T 'installation on the device failed; boot_{1} and misc were not changed' $LINUX_SLOT) + "`n" + (T 'the device log was kept at {1}' $devLog))
}
# the boot chain backup android-install.sh made, into the work directory (install.sh's bootchain_pull)
if ($SYNC_BOOTCHAIN -eq 1 -and (SuDo "[ -d $T/mu300-bootchain-backup ] && echo y") -eq 'y') {
    $bcDir = Join-Path $Work ('bootchain\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Force -Path (Split-Path $bcDir) | Out-Null
    Quiet { adb pull "$T/mu300-bootchain-backup" "$bcDir" } | Out-Null
    SuDo "rm -rf $T/mu300-bootchain-backup" | Out-Null
    $bcOk = Test-Path (Join-Path $bcDir 'SHA256SUMS')
    if ($bcOk) {
        foreach ($l in Get-Content (Join-Path $bcDir 'SHA256SUMS')) {
            $p = @($l.Split(' ') | Where-Object { $_ })
            if ($p.Count -ne 2 -or -not (Test-Path (Join-Path $bcDir $p[1])) -or
                (Get-FileHash (Join-Path $bcDir $p[1]) -Algorithm SHA256).Hash.ToLower() -ne $p[0]) { $bcOk = $false }
        }
    }
    if ($bcOk) { Write-Host ('  ' + (T 'boot chain backup: {1}' $bcDir)) }
    else { Write-Host ('  ' + (T 'the boot chain backup could not be copied to this computer; it is on the Linux filesystem (.mu300/bootchain)')) }
}

# Android still runs from the slot it ran from when the image was built, and the image is the one for the other slot
if ((SuDo 'getprop ro.boot.slot_suffix').Trim() -ne "_$ANDROID_SLOT") {
    Die (T 'Android no longer runs from slot {1}; boot_{2} and misc were not changed' $ANDROID_SLOT $LINUX_SLOT)
}
$bootJson = Get-Content "$Work\boot-linux-slot$LINUX_SLOT.json" | ConvertFrom-Json
$bcFile = "$Work\boot-linux-slot$LINUX_SLOT.misc-slot-$LINUX_SLOT-trial.bin"
if ($bootJson.linux_slot -ne $LINUX_SLOT -or -not (Test-Path $bcFile)) {
    Die (T 'the boot image was not built for slot {1}; boot_{1} and misc were not changed' $LINUX_SLOT)
}
Step 'boot' 92
Say (T 'Writing boot_{1} and arming slot {1}' $LINUX_SLOT)
$EXP = $bootJson.sha256
& adb push "$Work\boot-linux-slot$LINUX_SLOT.img" "$T/mu300-boot.img" | Out-Null
& adb push $bcFile "$T/mu300-bc.bin" | Out-Null
if ((SuDo "sha256sum $T/mu300-boot.img").Split(' ')[0] -ne $EXP) { Die (T 'pushed boot image hash mismatch') }
SuDo "dd if=$T/mu300-boot.img of=/dev/block/by-name/boot_$LINUX_SLOT bs=4M && sync" | Out-Null
# read back from the medium (the page cache dropped first): exactly the image's length in whole 4096-byte blocks,
# into a file, by one dd whose status counts (FINDINGS 50; install.sh's write_linux_boot does the same). A mismatch
# stops here: misc is not written, Android keeps booting.
$imgLen = (Get-Item "$Work\boot-linux-slot$LINUX_SLOT.img").Length
$imgBlocks = [int][math]::Ceiling($imgLen / 4096)
$readBack = SuDo "echo 3 > /proc/sys/vm/drop_caches; dd if=/dev/block/by-name/boot_$LINUX_SLOT of=$T/mu300-boot.rb bs=4096 count=$imgBlocks 2>/dev/null && head -c $imgLen $T/mu300-boot.rb | sha256sum"
if ("$readBack".Split(' ')[0] -ne $EXP) {
    Die (T 'boot_{1} verify failed (slot {2} still active, Android keeps booting)' $LINUX_SLOT $ANDROID_SLOT)
}
SuDo "dd if=$T/mu300-bc.bin of=/dev/block/by-name/misc bs=1 seek=2048 conv=notrunc && sync && rm $T/mu300-boot.img $T/mu300-bc.bin $T/mu300-boot.rb" | Out-Null
# the prepared installation is done: its transaction no longer waits (kano: state installed)
if ($RESUMING -eq 1) {
    ResumePy set $RESUME_TX 'state=installed' | Out-Null
    if ($script:ResumeRc -ne 0) { Write-Host ('  ' + (T 'could not mark {1} as done' $RESUME_TX)) }
}

# on-device switch for later: one command in Android instead of plugging into a computer (needs Magisk)
Step 'switch' 96
Say (T 'Installing the on-device switch (Magisk module)')
$ModSrc = Join-Path $Top 'android\magisk\mu300-linux-switch'
$Mod = '/data/adb/modules/mu300_linux_switch'
$MTmp = "$T/mu300-magisk"
# the module of this release, as its Magisk zip installs it (android/magisk/installer/module.prop.in, the version and
# versionCode of tools/make-magisk-zips.sh), not the generic "v1.0" one (docs/FINDINGS.md 131); a build of the
# checkout has no release tag and keeps the generic one
$PropSrc = Join-Path $ModSrc 'module.prop'
$PropKey = $(if ($Release) { TagKey $Release } else { $null })
if ($null -ne $PropKey) {
    $pk = [string]$PropKey
    $code = $(if ($pk.StartsWith('2')) { $pk.Substring(1) } else { $pk.Substring(1, $pk.Length - 3) })
    $sys = (@($OSES | ForEach-Object { if ($_ -eq 'ubuntu') { "ubuntu-$UBUNTU" } else { $_ } }) -join ' ')
    $prop = [IO.File]::ReadAllText((Join-Path $Top 'android\magisk\installer\module.prop.in'))
    $prop = $prop.Replace('@TAG@', $Release).Replace('@CODE@', $code).Replace('@SYSTEM@', $sys).Replace('@KERNEL@', $KERNEL)
    $PropSrc = Join-Path $Work 'module.prop'
    [IO.File]::WriteAllText($PropSrc, $prop)
}
if ((SuDo 'magisk -v')) {
    Quiet { adb shell "rm -rf $MTmp" } | Out-Null
    Quiet { adb shell "mkdir -p $MTmp/system/bin" } | Out-Null
    Quiet { PushUnix $PropSrc "$MTmp/module.prop" } | Out-Null
    foreach ($f in 'switch.sh', 'action.sh', 'bootchain.sh') {
        Quiet { PushUnix (Join-Path $ModSrc $f) "$MTmp/$f" } | Out-Null
    }
    Quiet { PushUnix (Join-Path $ModSrc 'system\bin\mu300-linux') "$MTmp/system/bin/mu300-linux" } | Out-Null
    SuDo "rm -rf $Mod && mkdir -p $Mod/system/bin && cp -a $MTmp/module.prop $MTmp/switch.sh $MTmp/action.sh $MTmp/bootchain.sh $Mod/ && cp -a $MTmp/system/bin/mu300-linux $Mod/system/bin/ && chown -R 0:0 $Mod && chmod 755 $Mod/switch.sh $Mod/action.sh $Mod/bootchain.sh $Mod/system/bin/mu300-linux && chmod 644 $Mod/module.prop && rm -rf $MTmp && sync" | Out-Null
    if ((SuDo "[ -x $Mod/switch.sh ] && echo yes") -eq 'yes') {
        Write-Host ('  ' + (T "installed: 'su -c mu300-linux' on the device starts Linux after the next Android boot"))
    } else {
        Write-Host ('  ' + (T '(skipped; the installer keeps working either way)'))
    }
} else {
    Write-Host ('  ' + (T 'no Magisk (or no root) on this device - skipped'))
}

Step 'done' 100
Say (T 'Done. Rebooting into {1}' $BOOT_OS)
$ip = if ($DEVICE -eq 'u30air') { '192.168.78.1' } elseif ($DEVICE -eq 'v50') { '192.168.79.1' } else { '192.168.77.1' }
Progress 'done' 'result' 'ok' 'boot_os' $BOOT_OS 'systems' ($OSES -join ' ') 'ip' $ip 'release' $Release 'kernel' $KERNEL
Write-Host ('  ' + (T 'USB network: {1}   SSH: {2}' $ip $(if ($BOOT_OS -eq 'ubuntu') { "ubuntu@$ip" } else { "root@$ip, LuCI http://$ip" })))
Write-Host ('  ' + (T 'switch systems: mu300-os {1}   back to Android: mu300-next-boot android' ($OSES -join '|')))
Write-Host ('  ' + (T 'back to Linux from Android (with Magisk): su -c mu300-linux'))
Write-Host ('  ' + (T 'News and new releases are on the Telegram channel of the project, you can follow it: {1}' 'https://t.me/mu300project'))
& adb reboot | Out-Null
