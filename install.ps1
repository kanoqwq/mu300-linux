<#
.SYNOPSIS
    MU300 / ZTE F50 Linux installer for Windows (same as install.sh on macOS/Linux).

.DESCRIPTION
    Run with the device booted in rooted Android and connected over USB (adb).
      .\install.ps1 -Check     only inspect the device; writes nothing
      .\install.ps1            install Ubuntu, OpenWrt or both from the prebuilt release images
      .\install.ps1 -Answers f.txt   take one answer per question from that file (run without a console; the file
                               holds the password in plain text, delete it afterwards)

    Needs: adb. Python 3 and its lz4 module are installed for this user when missing (winget or python.org, then
    pip); Windows 10/11 provide tar and curl.
    The published images contain no proprietary files: the Wi-Fi/Bluetooth firmware and the Android modem/GPU
    userspace are pulled from *your* device into work\ and added during installation. A Windows file system
    cannot hold every name the Android subset contains (the property area is a set of files called
    u:object_r:<context>:s0), so the archive from the device is kept beside it as work\android-subset\
    windows-source.tar.gz, and the tools that build the images take the subset from that file.
    Writes the Linux region, boot_b, slot b's boot chain (backed up first) and 32 bytes of misc;
    Android's slot a, the GPT and userdata stay untouched. -NoBootchainSync skips chain synchronization.
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
    [switch]$NoSelfUpdate,   # do not bring this copy up to date with GitHub first
    [switch]$NoBootchainSync # leave the other slot's boot chain unchanged
)

$ErrorActionPreference = 'Stop'
$T = '/data/local/tmp'
# $PSScriptRoot can be empty in Windows PowerShell 5.1 (param defaults, `powershell -File` from cmd.exe)
$Top = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $Work) { $Work = Join-Path $Top 'work' }
# the USB network of each kind of device: F50 192.168.77.1, U30 Air 192.168.78.1; $MU300_IP is set to the one
# that answers
$MU300_IP = '192.168.77.1'
function LinuxRunning {
    foreach ($ip in @('192.168.77.1', '192.168.78.1')) {
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
function Say($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Die($m) { Write-Host ("`n" + (T 'ERROR:') + " $m") -ForegroundColor Red; exit 1 }
# Windows PowerShell 5.1 turns every stderr line of a native command into an ErrorRecord once stderr is
# redirected, and with ErrorActionPreference Stop that aborts the script (adb's "daemon not running",
# "no devices", push progress). Run such commands with Continue and drop their stderr.
# The parameter must not share a name with any variable the blocks use: a scriptblock looks its variables up where
# it runs, names are case-insensitive, and with it called $Cmd, SuDo's { adb shell "su -c '$cmd'" } ran
# `su -c '<the text of the block>'` - so "su does not work on the device" on every Windows machine (issue #4).
function Quiet([scriptblock]$QuietBlock_) { $ErrorActionPreference = 'Continue'; & $QuietBlock_ 2>$null }
# With more than one adb device attached (a phone, an emulator, a device over the network) every plain adb command
# fails with "more than one device/emulator", which read as "no adb device". Pick the F50 and point adb at it with
# ANDROID_SERIAL: the only device, else the only one that says it is an F50/MU300 or a U30 Air, else ask (-Quiet
# never asks).
function SelectDevice([switch]$Quiet) {
    if ($env:ANDROID_SERIAL) { return }
    $all = @((Quiet { adb devices -l }) | Where-Object { $_ -match '^\S+\s+device\b' })
    if ($all.Count -eq 0) { return }
    $f50 = @($all | Where-Object { $_ -match 'model:F50|product:MU300|device:MU300|device:U30Air|product:MU5352|device:MU5352' })
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
    if (-not [int]::TryParse((Ask (T 'Device') "$def"), [ref]$n) -or $n -lt 1 -or $n -gt $all.Count) { Die (T 'invalid choice') }
    $env:ANDROID_SERIAL = ($all[$n - 1] -split '\s+')[0]
}
# [string]: with no device adb prints nothing, and `-notmatch` on that empty result is falsy, not true
function AdbState { SelectDevice -Quiet; [string](Quiet { adb get-state }) }
function Ask($question, $default) {
    $a = if ($script:AnswerQueue) { NextAnswer $question } else { Read-Host "$question [$(T $default)]" }
    if ([string]::IsNullOrWhiteSpace($a)) { return $default } else { return (NormalizeAnswer $a.Trim()) }
}
# adb shell with root; stdin is never forwarded so prompts of this script are not eaten
function SuDo($cmd) {
    (Quiet { adb shell "su -c '$cmd'" }) -join "`n" -replace "`r", ''
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
# GitHub's release CDN throttles single connections hard in some regions: pull large files as parallel ranges
function Fetch($url, $out) {
    $jobs = 8
    try { $len = [int64](Invoke-WebRequest $url -Method Head -UseBasicParsing).Headers['Content-Length'][0] } catch { $len = 0 }
    if ($len -lt 8MB) { Invoke-WebRequest $url -OutFile $out -UseBasicParsing; return }
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
    foreach ($j in $running) { Wait-Job $j | Out-Null; if ($j.State -ne 'Completed') { $failed = $true }; Receive-Job $j -ErrorAction SilentlyContinue | Out-Null; Remove-Job $j }
    if (-not $failed) {
        $fs = [IO.File]::Create($out)
        for ($i = 0; $i -lt $jobs; $i++) { $b = [IO.File]::OpenRead("$out.part$i"); $b.CopyTo($fs); $b.Close(); Remove-Item "$out.part$i" }
        $fs.Close()
        if ((Get-Item $out).Length -eq $len) { return }
    }
    Get-ChildItem "$out.part*" -ErrorAction SilentlyContinue | Remove-Item -Force
    Write-Host ('  ' + (T 'parallel download failed, retrying as a single stream'))
    Invoke-WebRequest $url -OutFile $out -UseBasicParsing
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

# first of all, make this the newest installer (it runs the updated one and ends when it changed)
if (SelfUpdate) {
    $env:MU300_SELF_UPDATED = '1'
    $PSBoundParameters['Lang'] = $Lang
    & $PSCommandPath @PSBoundParameters
    exit $LASTEXITCODE
}

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
$target = @((Quiet { adb devices -l }) | Where-Object { $_ -match '^\S+\s+device\b' -and $_ -match 'model:F50|product:MU300|device:MU300|device:U30Air|product:MU5352|device:MU5352' })
$linuxFirst = (-not $env:ANDROID_SERIAL) -and $target.Count -eq 0 -and (LinuxRunning)
if (-not $linuxFirst) { SelectDevice }
if ($linuxFirst -or (AdbState) -notmatch 'device') {
    # the device may be running MU300 Linux right now: then only SSH on the USB network answers
    $linux = $linuxFirst -or (LinuxRunning)
    if (-not $linux) { Die (T 'no adb device (boot Android, enable USB debugging)') }
    Say (T 'The device is running MU300 Linux, not Android')
    Write-Host ('  ' + (T 'Installing and uninstalling happen from Android (slot a), so the device has to reboot first.'))
    Write-Host ('  ' + (T 'I can ask it over SSH; you will be prompted for its password.'))
    if ((Ask (T 'Reboot the device into Android now? (yes/no)') 'yes') -ne 'yes') { Die (T 'boot Android yourself (on the device: sudo mu300-next-boot android && sudo reboot)') }
    # -t: sudo needs a terminal to ask for the device password; reboot cuts the connection, so watch the port
    foreach ($u in 'ubuntu', 'root') {
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
    Write-Host ('  ' + (T 'waiting for Android'))
    for ($i = 0; $i -lt 60; $i++) {
        if ((AdbState) -match 'device') { break }
        Start-Sleep 5
    }
    if ((AdbState) -notmatch 'device') { Die (T 'the device did not come back as Android; boot it yourself (mu300-next-boot android)') }
    Write-Host ('  ' + (T 'Android is up'))
}
# an Android that is still starting answers adb before su and storage are ready: the pulls then failed
for ($i = 0; $i -lt 60 -and (SuDo 'getprop sys.boot_completed') -ne '1'; $i++) {
    if ($i -eq 0) { Write-Host ('  ' + (T 'waiting for Android to finish starting')) }
    Start-Sleep 2
}
if ((SuDo 'id -u') -ne '0') { Die (T 'su does not work on the device') }
$model = "$(SuDo 'getprop ro.product.model') / $(SuDo 'getprop ro.product.device')"
Write-Host (T 'device: {1}' $model)
# The U30 Air is the F50's board with a battery: the same kernel and images, a few modules of its own (init
# loads them; the boot image says which device it is for)
$DEVICE = 'f50'
if ($model -match 'MU5352') {
    if ($model.Trim() -ne 'MU5352 / MU5352' -or (SuDo 'getprop ro.board.platform') -ne 'ums9620') { Die 'M3 requires MU5352 / MU5352 on ums9620' }
    $DEVICE = 'u30air'
} elseif ($model -match 'U30Air|U30_Air|U30 Air') {
    $DEVICE = 'u30air'
} elseif ($model -notmatch 'MU300|F50|mu300') {
    if ((Ask (T 'This does not look like a ZTE F50/MU300 or U30 Air. Continue anyway? (yes/no)') 'no') -ne 'yes') { exit 1 }
}
if ((SuDo 'getprop ro.boot.slot_suffix') -ne '_a') { Die (T 'Android must be running from slot a') }
# Existing desktop path targets b. Check the OTA chain before any installation.
$SYNC_BOOTCHAIN = if ($NoBootchainSync -or $env:MU300_SYNC_BOOTCHAIN -eq '0') { 0 } else { 1 }
$BC_LINE = 'boot chain: not synced (-NoBootchainSync)'
if ($SYNC_BOOTCHAIN -eq 1) {
    New-Item -ItemType Directory -Force -Path $Work | Out-Null
    if (-not (PushUnix "$Top\tools\android-bootchain.sh" "$T/android-bootchain.sh")) { Die 'Could not send boot chain checker' }
    $BC_LINE = SuDo "sh $T/android-bootchain.sh check"
    if ($BC_LINE -notmatch '(?m)^BOOTCHAIN-PLAN a b') { Die "Boot chain check refused; nothing installed: $BC_LINE" }
}
Write-Host $BC_LINE

Say (T 'Locating free eMMC space after the last partition')
$parts = (SuDo 'e=0; for p in /sys/block/mmcblk0/mmcblk0p*; do x=$(( $(cat $p/start) + $(cat $p/size) )); [ $x -gt $e ] && e=$x; done; echo $e $(cat /sys/block/mmcblk0/size)').Split(' ')
if ($parts.Count -ne 2) { Die (T 'could not read the partition table from the device (is su granted? try again)') }
[int64]$lastEnd = $parts[0]; [int64]$disk = $parts[1]
[int64]$start = [math]::Floor($lastEnd / 4096 + 1) * 4096
[int64]$end = [math]::Floor(($disk - 34) / 4096 - 1) * 4096
if ($end -le $start) { $end = $start }
[int64]$OFF = $start * 512
[int64]$SIZE = ($end - $start) * 512
function Gib([int64]$b) { '{0:N1} GiB' -f ($b / 1GB) }
# What each choice needs: the installed systems measure ~320 MiB (OpenWrt) and ~580 MiB (Ubuntu), and an update
# keeps the previous one as <os>.old while the new one is unpacked, so allow for two of each plus working room.
[int64]$NEED_OPENWRT = 800MB; [int64]$NEED_UBUNTU = 1600MB; [int64]$NEED_BOTH = 2400MB
Write-Host (T 'eMMC: {1} ({2} sectors), partitions end at {3} (sector {4}), free after them: {5}' (Gib ($disk * 512)) $disk (Gib ($lastEnd * 512)) $lastEnd (Gib $SIZE))
# Smaller eMMC variants leave less room behind userdata, and how much is needed depends on the choice further
# down - OpenWrt alone fits in a few hundred megabytes. So refuse only what cannot hold anything at all, and
# check the real requirement once the systems are known. There is nowhere else to put this region on these
# devices: userdata is metadata-encrypted (dm-default-key), so an image file inside it cannot be read from
# Linux, and the spare-looking blackbox and fulldumpdb partitions are written by the firmware itself.
if ($SIZE -lt 700MB) {
    $mib = [int64]($SIZE / 1MB)
    Die ((T 'only {1} MiB of free space after the last partition: this device has a different layout, nothing is changed.' $mib) + "`n" + (T 'Please report the numbers above (eMMC size and where the partitions end); they identify the variant.'))
}

$existing = 'no'
function RegionOnDisk([int64]$bytes) { $bytes -ge 0 -and ($bytes + 2048) -le ($disk * 512) }
foreach ($cand in @($OFF, 27762098176)) {
    if (-not (RegionOnDisk $cand)) { continue }
    $m = (SuDo "dd if=/dev/block/mmcblk0 bs=1 skip=$($cand + 1080) count=2 2>/dev/null | od -An -tx1") -replace '\s', ''
    $l = (SuDo "dd if=/dev/block/mmcblk0 bs=1 skip=$($cand + 1144) count=16 2>/dev/null") -replace '\0', ''
    if ($m -eq '53ef' -and $l.Trim() -eq 'mu300root') {
        $blocks = [int64]((SuDo "dd if=/dev/block/mmcblk0 bs=1 skip=$($cand + 1028) count=4 2>/dev/null | od -An -tu4").Trim())
        $OFF = $cand; $SIZE = $blocks * 4096; $existing = 'yes'; break
    }
}
Write-Host (T 'Linux region: offset {1}, {2}, existing mu300root filesystem: {3}' $OFF (Gib $SIZE) (T $existing))

$dirty = 0
if ($existing -eq 'no') {
    $step = [int64]($SIZE / 1MB / 16)
    $probe = (0..15 | ForEach-Object { [int64]($OFF / 1MB) + $_ * $step }) -join ' '
    # Empty is 0x00 or 0xFF (what an eMMC reads back after an erase). No double quotes in the command: Windows
    # PowerShell 5.1 drops them on the way to adb, tr got an unquoted \000 - "delete the character 0" - and every
    # empty MiB counted as data, so on Windows an empty region was always "16 of 16 not empty". \\ reaches the
    # device's shell as one backslash, which tr then reads as the start of an octal escape.
    $dirty = [int](SuDo "n=0; for s in $probe; do c=`$(dd if=/dev/block/mmcblk0 bs=1048576 skip=`$s count=1 2>/dev/null | tr -d \\000\\377 | wc -c); [ `$c -gt 0 ] && n=`$((n + 1)); done; echo `$n").Trim()
    Write-Host (T 'data check: {1} of 16 samples contain data' $dirty)
    if ($dirty -gt 0) {
        $head = (SuDo "dd if=/dev/block/mmcblk0 bs=1048576 skip=$([int64]($OFF / 1MB)) count=1 2>/dev/null | od -An -tx1 -N32") -replace '\s+', ' '
        Write-Host ('  ' + (T 'start of the region: {1}' $head.Trim()))
    }
}
if ($existing -eq 'yes') {
    $verdict = T 'OK: a MU300 Linux installation is already present (it can be kept or replaced)'
} elseif ($dirty -gt 0) {
    $verdict = T 'WARNING: the unpartitioned space is not empty; it may be used by this firmware. Installing overwrites it'
} elseif ($SIZE -ge 20GB) {
    $verdict = T 'OK: free and empty, same layout as the tested device (~32 GiB after userdata on the 64 GB eMMC)'
} elseif ($SIZE -ge $NEED_BOTH) {
    $verdict = T 'OK: free and empty, smaller than on the tested device but enough for both systems'
} elseif ($SIZE -ge $NEED_UBUNTU) {
    $verdict = T 'OK: free and empty, but room for one system only (Ubuntu or OpenWrt, not both)'
} else {
    $verdict = T 'OK: free and empty, but small: OpenWrt fits, Ubuntu does not'
}
Write-Host (T 'result: {1}' $verdict)
if ($Check) {
    Write-Host ("`n" + (T 'Nothing was written. Android version: {1}' (SuDo 'getprop ro.build.display.id')))
    exit 0
}
if ($dirty -gt 0 -and (Ask (T 'Type overwrite to use this region anyway') 'no') -ne 'overwrite') { Die (T 'cancelled') }

Say (T 'What should be installed?')
Write-Host ('  ' + (T '1) Ubuntu LTS: 24.04 or 26.04, asked next (full distribution, apt, ~500 MiB RAM in use)'))
Write-Host ('  ' + (T '2) OpenWrt {1} (router, LuCI web UI, ~140 MiB RAM in use)' '25.12.5'))
Write-Host ('  ' + (T '3) both (switch later with: mu300-os ubuntu|openwrt)'))
if ($SIZE -lt $NEED_BOTH) {
    $fits = if ($SIZE -ge $NEED_UBUNTU) { T 'one system fits, not both' } else { T 'only OpenWrt fits' }
    Write-Host ('  ' + (T '(this device has {1}: {2})' (Gib $SIZE) $fits))
}
switch (Ask (T 'Choice') '3') {
    '1' { $OSES = @('ubuntu') }
    '2' { $OSES = @('openwrt') }
    '3' { $OSES = @('ubuntu', 'openwrt') }
    default { Die (T 'invalid choice') }
}
$need = if ($OSES.Count -eq 2) { $NEED_BOTH } elseif ($OSES[0] -eq 'ubuntu') { $NEED_UBUNTU } else { $NEED_OPENWRT }
if ($SIZE -lt $need) {
    $needMib = [int64]($need / 1MB); $haveMib = [int64]($SIZE / 1MB)
    Die (T 'that choice needs about {1} MiB and this device has {2} MiB of free space' $needMib $haveMib)
}
$BOOT_OS = $OSES[0]
if ($OSES.Count -eq 2) {
    $BOOT_OS = Ask (T 'Which one should boot (ubuntu/openwrt)') 'ubuntu'
    if ($BOOT_OS -notin @('ubuntu', 'openwrt')) { Die (T 'invalid system') }
}
$UBUNTU = '24.04'
if ($OSES -contains 'ubuntu') {
    Say (T 'Which Ubuntu?')
    Write-Host ('  ' + (T '1) 24.04 LTS  the longest tested, supported until 2029'))
    Write-Host ('  ' + (T '2) 26.04 LTS  BETA: the newest (systemd 259, newer packages), supported until 2031; tested less'))
    switch (Ask (T 'Ubuntu') '1') {
        { $_ -in '1', '24.04' } { $UBUNTU = '24.04' }
        { $_ -in '2', '26.04' } { $UBUNTU = '26.04' }
        default { Die (T 'invalid choice') }
    }
}
# the release file of a system: Ubuntu 26.04 has its own, 24.04 keeps the name it always had
function RootfsFile($os) { if ($os -eq 'ubuntu' -and $UBUNTU -eq '26.04') { 'mu300-ubuntu-26.04-rootfs.tar.gz' } else { "mu300-$os-rootfs.tar.gz" } }
$DEFAULT_LINUX = if ((Ask (T 'Boot Linux by default instead of Android (falls back to Android if Linux fails)? (yes/no)') 'yes') -eq 'yes') { 1 } else { 0 }
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
    $BOOT_ATTEMPTS = Ask (T 'Failed boots before Android (1-6)') '5'
    if ($BOOT_ATTEMPTS -notmatch '^[1-6]$') { Die (T 'enter a number from 1 to 6') }
}
$IMPORT_HOTSPOT = if ((Ask (T "Copy Android's hotspot name and password to Linux? (yes/no)") 'yes') -eq 'yes') { 1 } else { 0 }
$gpu = Ask (T 'Include the Mali GPU (OpenCL) userspace (~90 MiB)? (yes/no)') 'yes'
$KERNEL = $null
Say (T 'Which kernel?')
Write-Host ('  ' + (T "1) 5.4   Unisoc's vendor kernel (Android 12 base): the longest tested, everything this project supports"))
Write-Host ('  ' + (T '2) 6.18  mainline Linux, current long-term (LTS) release: newer drivers and security fixes, the same'))
Write-Host ('  ' + (T '         functions (hotspot, mobile data, SMS, Bluetooth, VPN, GPU); no USB-C video output yet'))
Write-Host ('  ' + (T '3) 7.2   the newest stable mainline Linux (7.2 for now): the newest drivers, the same functions'))
Write-Host ('  ' + (T '         as 6.18; tested less than 6.18'))
Write-Host ('  ' + (T 'This can be changed later on the device: sudo mu300-update kernel 5.4|6.18|7.2'))
while (-not $KERNEL) {
    switch (Ask (T 'Kernel') '1') {
        { $_ -in '1', '5.4' } {
            if ($UBUNTU -eq '26.04') {
                Write-Host ('  ' + (T 'Ubuntu 26.04 needs a mainline kernel (6.18 or 7.2): its programs use system calls that 5.4 does not have (tar, for one, cannot unpack folders there).'))
            } else { $KERNEL = '5.4' }
        }
        { $_ -in '2', '6.18' } { $KERNEL = '6.18' }
        { $_ -in '3', '7.2' } { $KERNEL = '7.2' }
        default { Write-Host ('  ' + (T 'enter 1, 2 or 3')) }
    }
}
$FORMAT = 0; $WIPE_LEGACY = 0; $UPDATE = 0
if ($existing -eq 'no') {
    $FORMAT = 1
} else {
    Write-Host ''
    Write-Host ('  ' + (T 'A MU300 Linux installation is already on this device.'))
    Write-Host ('    ' + (T 'update  reinstall the systems and keep settings and data (/etc/mu300, users and home directories,'))
    Write-Host ('    ' + (T '        SSH host keys, OpenWrt UCI config; the hotspot settings are kept too)'))
    Write-Host ('    ' + (T 'wipe    erase the Linux filesystem and install from scratch'))
    switch (Ask (T 'update or wipe') 'update') {
        'update' { $UPDATE = 1 }
        'wipe' { $FORMAT = 1 }
        default { Die (T 'invalid choice') }
    }
    if ($FORMAT -eq 0 -and $OSES -contains 'ubuntu') { $WIPE_LEGACY = 1 }
}
if ($script:AnswerQueue) {
    $p1 = NextAnswer (T 'Password for the "ubuntu" user (Ubuntu) and "root" (OpenWrt)') -Secret
    $p2 = NextAnswer (T 'Repeat') -Secret
} else {
    $pw1 = Read-Host -AsSecureString (T 'Password for the "ubuntu" user (Ubuntu) and "root" (OpenWrt)')
    $pw2 = Read-Host -AsSecureString (T 'Repeat')
    $p1 = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($pw1))
    $p2 = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($pw2))
}
if ($p1 -ne $p2 -or $p1.Length -lt 6) { Die (T 'passwords differ or are shorter than 6 characters') }

New-Item -ItemType Directory -Force -Path "$Work\dumps", "$Work\firmware" | Out-Null
Say (T 'Pulling device data into {1} (stays on this computer)' $Work)
SuDoToFile 'cat /dev/block/by-name/boot_a' "$Work\dumps\boot_a.img"
SuDoToFile 'dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null' "$Work\dumps\misc-head.bin"
if ((Get-Item "$Work\dumps\boot_a.img").Length -lt 1MB) { Die (T 'pulling boot_a failed') }
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

if (-not $Release) {
    if ($ReleaseUrl) { $Release = 'custom' }
    else {
        try { $Release = (Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest" -UseBasicParsing).tag_name }
        catch { Die (T 'cannot find the newest release of {1} (use -Release <tag> to pick one)' $Repo) }
    }
}
Say (T 'Downloading release {1}' $Release)
$REL = "$Work\release\$Release"
New-Item -ItemType Directory -Force -Path $REL | Out-Null
$base = if ($ReleaseUrl) { $ReleaseUrl } else { "https://github.com/$Repo/releases/download/$Release" }
Invoke-WebRequest "$base/SHA256SUMS" -OutFile "$REL\SHA256SUMS" -UseBasicParsing
$sums = @{}
foreach ($line in Get-Content "$REL\SHA256SUMS") {
    $p = $line -split '\s+', 2
    if ($p.Count -eq 2) { $sums[$p[1].TrimStart('*')] = $p[0] }
}
$files = @('mu300-kernel.tar.gz') + ($OSES | ForEach-Object { RootfsFile $_ })
if ($KERNEL -ne '5.4') { $files += "mu300-kernel-$KERNEL.tar.gz" }
foreach ($f in $files) {
    if (-not $sums.ContainsKey($f)) {
        $why = if ($KERNEL -ne '5.4' -and $f -eq "mu300-kernel-$KERNEL.tar.gz") { ' ' + (T '(choose kernel 5.4, or a newer release)') } else { '' }
        Die ((T '{1} is not part of release {2}' $f $Release) + $why)
    }
    $have = if (Test-Path "$REL\$f") { (Get-FileHash "$REL\$f" -Algorithm SHA256).Hash.ToLower() } else { '' }
    if ($have -ne $sums[$f]) {
        Write-Host "  $f"
        Fetch "$base/$f" "$REL\$f.part"
        if ((Get-FileHash "$REL\$f.part" -Algorithm SHA256).Hash.ToLower() -ne $sums[$f]) { Die (T 'checksum mismatch for {1}' $f) }
        Move-Item -Force "$REL\$f.part" "$REL\$f"
    }
}
Remove-Item -Recurse -Force "$REL\kernel" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path "$REL\kernel" | Out-Null
& tar -xzf "$REL\mu300-kernel.tar.gz" -C "$REL\kernel"
if ($DEVICE -ne 'f50' -and -not (Test-Path "$REL\kernel\modules-$DEVICE")) { Die (T 'release {1} does not support this device yet; use a newer one' $Release) }
# a mainline kernel (6.18, 7.2): its bundle, unpacked
$KMAIN = $null
if ($KERNEL -ne '5.4') {
    $KMAIN = "$REL\kernel-$KERNEL"
    Remove-Item -Recurse -Force $KMAIN -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $KMAIN | Out-Null
    & tar -xzf "$REL\mu300-kernel-$KERNEL.tar.gz" -C $KMAIN
    foreach ($k in 'Image', 'ramdisk-generic.lz4', 'kernel.release') {
        if (-not (Test-Path "$KMAIN\$k")) { Die (T '{1} is incomplete' "mu300-kernel-$KERNEL.tar.gz") }
    }
    # a bundle names the devices it runs on; older mainline kernels do not bring up the U30 Air's USB (FINDINGS 33c)
    if ($DEVICE -ne 'f50' -and -not ((Test-Path "$KMAIN\devices") -and ((Get-Content "$KMAIN\devices") -match "\b$DEVICE\b"))) {
        Die (T 'release {1} does not support this device yet; use a newer one' $Release)
    }
}

Say (T 'Adding the vendor files from your device to the images')
foreach ($os in $OSES) {
    $argv = @("$Top\tools\vendor-overlay.py", '--os', $os, '--firmware', "$Work\firmware",
        '--android-subset', "$Work\android-subset", '--out', "$Work\mu300-vendor-$os.tar.gz")
    if ($gpu -eq 'yes' -and (Test-Path "$Work\android-gpu-subset")) { $argv += @('--gpu-subset', "$Work\android-gpu-subset") }
    if ($KMAIN) { $argv += @('--kernel-bundle', $KMAIN) }
    Python @argv
    if ($LASTEXITCODE -ne 0) { Die (T '{1} failed' 'vendor-overlay.py') }
}
# not `| Python ...`: that helper is an advanced function with no pipeline-bound parameter, so the
# binding fails, and its body would not forward $input to the child's stdin even if it bound
$PWHASH = ($p1 | & $script:PyExe "$Top\tools\sha512crypt.py").Trim()

Say (T 'Building the boot image')
WriteUnix "$Work\init" (((Get-Content -Raw "$Top\boot\init") -replace '(?m)^ROOT_OFFSET=[0-9]*', "ROOT_OFFSET=$OFF"))
# a mainline kernel: its kernel, and its generic ramdisk segment behind this one (its init and modules win) - the
# image that "mu300-update kernel 6.18" (or 7.2) writes on the device
$bootArgs = @("$Top\boot\build-boot-image.py", '--stock-boot', "$Work\dumps\boot_a.img", '--misc-head', "$Work\dumps\misc-head.bin",
    '--kernel', $(if ($KMAIN) { "$KMAIN\Image" } else { "$REL\kernel\Image" }),
    '--modules', "$REL\kernel\modules", '--init', "$Work\init", '--busybox', "$REL\kernel\busybox",
    '--logdw', "$REL\kernel\logdw", '--ueventd-perms', "$Top\android-vendor\ueventd-perms.sh",
    '--android-subset', "$Work\android-subset", '--out', "$Work\boot-linux-slotb.img", '--device', $DEVICE)
if (Test-Path "$REL\kernel\modules-u30air") { $bootArgs += @('--device-modules', "u30air=$REL\kernel\modules-u30air") }
if ($KMAIN) { $bootArgs += @('--append-ramdisk', "$KMAIN\ramdisk-generic.lz4") }
# a failed build must stop here: otherwise an earlier image (or none) would be written to the device
Remove-Item "$Work\boot-linux-slotb.*" -ErrorAction SilentlyContinue
Python @bootArgs | Out-Null
if ($LASTEXITCODE -ne 0 -or -not (Test-Path "$Work\boot-linux-slotb.img")) { Die (T '{1} failed' 'build-boot-image.py') }

Say (T 'Ready to install')
Write-Host ('  ' + (T 'source:         {1}' (T 'prebuilt release {1} + vendor files from this device' $Release)))
$sysText = ($OSES -join ' ') + $(if ($OSES -contains 'ubuntu') { " (Ubuntu $UBUNTU)" } else { '' })
Write-Host ('  ' + (T 'systems:        {1} (boots: {2})' $sysText $BOOT_OS))
Write-Host ('  ' + (T 'kernel:         {1}' "$KERNEL$(if ($KMAIN) { " (mainline, $((Get-Content "$KMAIN\kernel.release").Trim()))" })"))
Write-Host ('  ' + (T 'default boot:   {1}' $(if ($DEFAULT_LINUX -eq 1) { T 'Linux (Android after {1} failed boots in a row)' $BOOT_ATTEMPTS } else { T 'Android, Linux on demand' })))
Write-Host ('  ' + (T 'filesystem:     {1}' $(if ($FORMAT -eq 1) { T 'CREATE new ext4 (erases the Linux region)' } else { T 'keep existing' })))
if ($UPDATE -eq 1) { Write-Host ('  ' + (T 'update:         settings and user data of the chosen systems are kept, everything else is replaced')) }
Write-Host ('  ' + (T 'writes:         Linux region at offset {1}, boot_b, 32 bytes of misc (boot_a, GPT and userdata are not touched)' $OFF))
Write-Host $BC_LINE
if ((Ask (T 'Type INSTALL to continue') 'no') -ne 'INSTALL') { Die (T 'cancelled') }

Say (T 'Copying to the device')
foreach ($f in 'android-mount-mu300root.sh', 'android-install.sh', 'android-bootchain.sh', 'tf-storage.sh') { PushUnix "$Top\tools\$f" "$T/$f" | Out-Null }
foreach ($os in $OSES) {
    & adb push "$REL\$(RootfsFile $os)" "$T/mu300-$os.tar.gz" | Out-Null
    & adb push "$Work\mu300-vendor-$os.tar.gz" "$T/mu300-vendor-$os.tar.gz" | Out-Null
}
$envFile = "$Work\mu300-install.env"
$lines = @("OFF=$OFF", "SIZE=$SIZE", "OFF_S=$($OFF / 512)", "SIZE_S=$($SIZE / 512)", "FORMAT=$FORMAT",
    "OSES=`"$($OSES -join ' ')`"", "WIPE_LEGACY=$WIPE_LEGACY", "UPDATE=$UPDATE", "BOOT_OS=$BOOT_OS", "DEFAULT_LINUX=$DEFAULT_LINUX", "BOOT_ATTEMPTS=$BOOT_ATTEMPTS",
    "IMPORT_HOTSPOT=$IMPORT_HOTSPOT", "KERNEL=$KERNEL", "SYNC_BOOTCHAIN=$SYNC_BOOTCHAIN", "PWHASH='$PWHASH'")
WriteUnix $envFile (($lines -join "`n") + "`n")
& adb push $envFile "$T/mu300-install.env" | Out-Null
Remove-Item $envFile
$log = SuDo "sh $T/android-install.sh"
Write-Host $log
if ($log -notmatch 'MU300-INSTALL-OK') { Die (T 'installation on the device failed; boot_b and misc were not changed') }

if ($SYNC_BOOTCHAIN -eq 1 -and (SuDo "[ -d $T/mu300-bootchain-backup ] && echo y") -eq 'y') {
    $bcDir = Join-Path $Work ('bootchain\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Force -Path (Split-Path $bcDir) | Out-Null
    Quiet { adb pull "$T/mu300-bootchain-backup" "$bcDir" } | Out-Null
    # Keep the device backup too, even if adb pull fails.
}
Say (T 'Writing boot_b and arming slot b')
$EXP = (Get-Content "$Work\boot-linux-slotb.json" | ConvertFrom-Json).sha256
& adb push "$Work\boot-linux-slotb.img" "$T/mu300-boot.img" | Out-Null
& adb push "$Work\boot-linux-slotb.misc-slot-b-trial.bin" "$T/mu300-bc-b.bin" | Out-Null
if ((SuDo "sha256sum $T/mu300-boot.img").Split(' ')[0] -ne $EXP) { Die (T 'pushed boot image hash mismatch') }
SuDo "dd if=$T/mu300-boot.img of=/dev/block/by-name/boot_b bs=4M && sync" | Out-Null
if ((SuDo 'sha256sum /dev/block/by-name/boot_b').Split(' ')[0] -ne $EXP) { Die (T 'boot_b verify failed (slot a still active, Android keeps booting)') }
SuDo "dd if=$T/mu300-bc-b.bin of=/dev/block/by-name/misc bs=1 seek=2048 conv=notrunc && sync && rm $T/mu300-boot.img $T/mu300-bc-b.bin" | Out-Null

# on-device switch for later: one command in Android instead of plugging into a computer (needs Magisk)
Say (T 'Installing the on-device switch (Magisk module)')
$ModSrc = Join-Path $Top 'android\magisk\mu300-linux-switch'
$Mod = '/data/adb/modules/mu300_linux_switch'
$MTmp = "$T/mu300-magisk"
if ((SuDo 'magisk -v')) {
    Quiet { adb shell "rm -rf $MTmp" } | Out-Null
    Quiet { adb shell "mkdir -p $MTmp/system/bin" } | Out-Null
    foreach ($f in 'module.prop', 'switch.sh', 'action.sh', 'bootchain.sh') {
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

Say (T 'Done. Rebooting into {1}' $BOOT_OS)
$ip = if ($DEVICE -eq 'u30air') { '192.168.78.1' } else { '192.168.77.1' }
Write-Host ('  ' + (T 'USB network: {1}   SSH: {2}' $ip $(if ($BOOT_OS -eq 'ubuntu') { "ubuntu@$ip" } else { "root@$ip, LuCI http://$ip" })))
Write-Host ('  ' + (T 'switch systems: mu300-os ubuntu|openwrt   back to Android: mu300-next-boot android'))
Write-Host ('  ' + (T 'back to Linux from Android (with Magisk): su -c mu300-linux'))
& adb reboot | Out-Null
