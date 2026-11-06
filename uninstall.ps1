<#
.SYNOPSIS
    Remove MU300 Linux and return the device to stock Android (Windows version of uninstall.sh).

.DESCRIPTION
    Run with the device booted in rooted Android and connected over USB (adb). It makes slot a (Android) the boot
    slot in misc, copies boot_a over boot_b, erases the Linux filesystem in the unpartitioned eMMC region and removes
    the installer leftovers. boot_a, the GPT, userdata and every other partition stay untouched.
    Needs: adb and Python 3.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$T = '/data/local/tmp'
# the USB network of each kind of device: F50 192.168.77.1, U30 Air 192.168.78.1; $MU300_IP is set to the one
# that answers
$MU300_IP = '192.168.77.1'
function LinuxRunning {
    foreach ($ip in @('192.168.77.1', '192.168.78.1')) {
        if (Test-NetConnection -ComputerName $ip -Port 22 -InformationLevel Quiet -WarningAction SilentlyContinue) { $script:MU300_IP = $ip; return $true }
    }
    return $false
}
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

function Say($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Die($m) { Write-Host "`nERROR: $m" -ForegroundColor Red; exit 1 }
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
    # -Quiet (waiting for the device to come back): only the one F50/U30 Air. Otherwise always ask: a phone or
    # tablet next to it is what this must never touch.
    if ($Quiet) {
        if ($f50.Count -eq 1) { $env:ANDROID_SERIAL = ($f50[0] -split '\s+')[0] }
        return
    }
    Write-Host ('  ' + 'which adb device is the F50 or U30 Air?')
    $def = 1
    for ($i = 0; $i -lt $all.Count; $i++) {
        $model = if ($all[$i] -match 'model:(\S+)') { $Matches[1] } else { '' }
        Write-Host ('    {0}) {1} {2}' -f ($i + 1), ($all[$i] -split '\s+')[0], $model)
        if ($f50.Count -eq 1 -and $all[$i] -eq $f50[0]) { $def = $i + 1 }
    }
    $n = 0
    if (-not [int]::TryParse((Ask 'Device' "$def"), [ref]$n) -or $n -lt 1 -or $n -gt $all.Count) { Die 'invalid choice' }
    $env:ANDROID_SERIAL = ($all[$n - 1] -split '\s+')[0]
}
# [string]: with no device adb prints nothing, and `-notmatch` on that empty result is falsy, not true
function AdbState { SelectDevice -Quiet; [string](Quiet { adb get-state }) }
function Ask($question, $default) {
    $a = Read-Host "$question [$default]"
    if ([string]::IsNullOrWhiteSpace($a)) { return $default } else { return $a.Trim() }
}
function SuDo($cmd) { (Quiet { adb shell "su -c '$cmd'" }) -join "`n" -replace "`r", '' }
# see install.ps1: `su -c` may run on a pty that rewrites LF as CRLF, so binaries are written on the device
# and pulled rather than streamed (issue #2)
function SuDoToFile($cmd, $path) {
    $dev = '/data/local/tmp/mu300-pull.bin'
    & adb shell "su -c '$cmd > $dev'" | Out-Null
    Quiet { adb pull $dev "$path" } | Out-Null
    & adb shell "su -c 'rm -f $dev'" | Out-Null
}
$script:PyExe = $null
function Python { param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PyArgs)
    if (-not $script:PyExe) {
        foreach ($n in 'python', 'python3', 'py') {
            $c = Get-Command $n -ErrorAction SilentlyContinue
            if ($c) { $script:PyExe = $c.Source; break }
        }
        if (-not $script:PyExe) { Die 'Python 3 not found' }
    }
    & $script:PyExe @PyArgs
}
function Hex32 { (SuDo 'dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null | od -An -tx1') -replace '\s', '' }

Say 'Checking host tools and device'
FindAdb
if (-not (Get-Command adb -ErrorAction SilentlyContinue)) { Die 'adb not found' }
# the device in Linux, and only a phone or tablet in Android: that is not the one to touch - reboot the device first
$target = @((Quiet { adb devices -l }) | Where-Object { $_ -match '^\S+\s+device\b' -and $_ -match 'model:F50|product:MU300|device:MU300|device:U30Air|product:MU5352|device:MU5352' })
$linuxFirst = (-not $env:ANDROID_SERIAL) -and $target.Count -eq 0 -and (LinuxRunning)
if (-not $linuxFirst) { SelectDevice }
if ($linuxFirst -or (AdbState) -notmatch 'device') {
    $linux = $linuxFirst -or (LinuxRunning)
    if (-not $linux) { Die 'no adb device (boot Android, enable USB debugging)' }
    Say 'The device is running MU300 Linux, not Android'
    Write-Host '  Uninstalling happens from Android (slot a), so the device has to reboot first.'
    if ((Ask 'Reboot the device into Android now? (yes/no)' 'yes') -ne 'yes') { Die 'boot Android yourself (in Linux: sudo mu300-next-boot android && sudo reboot)' }
    # -t: sudo needs a terminal to ask for the device password; reboot cuts the connection, so watch the port
    foreach ($u in 'ubuntu', 'root') {
        Write-Host "  $u@$MU300_IP - enter the device password when asked (Ctrl-C to skip)"
        & ssh -t -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL -o LogLevel=ERROR -o ConnectTimeout=8 "$u@$MU300_IP" `
            'if [ "$(id -u)" = 0 ]; then S=; else S=sudo; fi; $S sh -c "/opt/mu300/bin/mu300-next-boot android && sync && reboot"'
        $gone = $false
        for ($i = 0; $i -lt 8; $i++) {
            if (-not (Test-NetConnection -ComputerName $MU300_IP -Port 22 -InformationLevel Quiet -WarningAction SilentlyContinue)) { $gone = $true; break }
            Start-Sleep 5
        }
        if ($gone) { Write-Host '  rebooting'; break }
    }
    Write-Host '  waiting for Android'
    for ($i = 0; $i -lt 60; $i++) {
        if ((AdbState) -match 'device') { break }
        Start-Sleep 5
    }
    if ((AdbState) -notmatch 'device') { Die 'the device did not come back as Android' }
}
if ((SuDo 'id -u') -ne '0') { Die 'su does not work on the device' }
if ((SuDo 'for u in /sys/class/block/mmcblk*p*/uevent; do grep -qx PARTNAME=mu300root "$u" && { echo internal-partition; break; }; done; true') -eq 'internal-partition') {
    Die 'Use OpenWrt-Installer.exe: Advanced options -> Uninstall and return space, with the original installation records. This legacy script cannot safely remove a GPT mu300root partition.'
}
$model = "$(SuDo 'getprop ro.product.model') / $(SuDo 'getprop ro.product.device')"
Write-Host "device: $model"
if ($model -match 'MU5352') {
    if ($model.Trim() -ne 'MU5352 / MU5352' -or (SuDo 'getprop ro.board.platform') -ne 'ums9620') { Die 'M3 requires MU5352 / MU5352 on ums9620' }
} elseif ($model -notmatch 'MU300|F50|mu300|U30Air|U30_Air') { Die 'this does not look like a ZTE F50/MU300 or U30 Air' }
if ((SuDo 'getprop ro.boot.slot_suffix') -ne '_a') { Die 'Android must be running from slot a (boot Android first: mu300-next-boot android)' }

Say 'Looking for the Linux installation'
$parts = (SuDo 'e=0; for p in /sys/block/mmcblk0/mmcblk0p*; do x=$(( $(cat $p/start) + $(cat $p/size) )); [ $x -gt $e ] && e=$x; done; echo $e $(cat /sys/block/mmcblk0/size)').Split(' ')
if ($parts.Count -ne 2) { Die 'could not read the partition table from the device (is su granted? try again)' }
[int64]$lastEnd = $parts[0]; [int64]$disk = $parts[1]
[int64]$OFF = 0; [int64]$SIZE = 0
[int64]$start = [math]::Floor($lastEnd / 4096 + 1) * 4096 * 512
foreach ($cand in @($start, 27762098176)) {
    # Upstream 486856c: do not read a legacy/dynamic superblock past the eMMC.
    if ($cand -lt 0 -or ($cand + 2048) -gt ($disk * 512)) { continue }
    $m = (SuDo "dd if=/dev/block/mmcblk0 bs=1 skip=$($cand + 1080) count=2 2>/dev/null | od -An -tx1") -replace '\s', ''
    $l = (SuDo "dd if=/dev/block/mmcblk0 bs=1 skip=$($cand + 1144) count=16 2>/dev/null") -replace '\0', ''
    if ($m -eq '53ef' -and $l.Trim() -eq 'mu300root') {
        $blocks = [int64]((SuDo "dd if=/dev/block/mmcblk0 bs=1 skip=$($cand + 1028) count=4 2>/dev/null | od -An -tu4").Trim())
        $OFF = $cand; $SIZE = $blocks * 4096; break
    }
}
if ($OFF -gt 0) {
    if (($OFF / 512) -lt $lastEnd -or (($OFF + $SIZE) / 512) -gt ($disk - 34)) { Die 'the mu300root filesystem overlaps a partition, refusing to touch it' }
    if ($OFF % 1MB -ne 0) { Die "unexpected filesystem offset $OFF" }
    Write-Host "Linux filesystem: offset $OFF, $([int64]($SIZE / 1MB)) MiB"
} else {
    Write-Host 'no mu300root filesystem found (already erased?)'
}
$BC = Hex32

Say 'What should be removed?'
$wipe = 'keep'
if ($OFF -gt 0) {
    Write-Host "  secure  overwrite the whole $([int64]($SIZE / 1GB)) GiB region and verify (recommended, takes a few"
    Write-Host '          minutes; your files are really gone afterwards)'
    Write-Host '  quick   only erase the filesystem headers (fast, but the files stay readable on the flash)'
    Write-Host '  keep    leave the Linux filesystem in place (it just never boots again)'
    $wipe = Ask 'Erase the Linux filesystem: secure / quick / keep' 'secure'
    if ($wipe -eq 'full') { $wipe = 'secure' }
    if ($wipe -notin @('secure', 'quick', 'keep')) { Die 'invalid choice' }
}
Write-Host ''
Write-Host '  misc:     boot slot a (Android), Linux boot disabled'
Write-Host '  boot_b:   replaced with a copy of boot_a (stock Android boot image)'
Write-Host "  Linux:    $(if ($wipe -eq 'keep') { 'kept on the eMMC (not bootable)' } else { "$wipe erase of $([int64]($SIZE / 1MB)) MiB at offset $OFF" })"
Write-Host '  untouched: boot_a, GPT, userdata and all other partitions'
if ((Ask 'Type UNINSTALL to continue' 'no') -ne 'UNINSTALL') { Die 'cancelled' }

Say 'Making slot a the boot slot'
$miscTmp = [IO.Path]::GetTempFileName()
SuDoToFile 'dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null' $miscTmp
$py = @'
import struct, sys, zlib
head = open(sys.argv[1], "rb").read()
bc = bytearray(head[0x800:0x820])
if len(bc) != 32 or bc[4:8] != b"BCAB" or zlib.crc32(bytes(bc[:28])) != struct.unpack("<I", bc[28:])[0]:
    sys.exit("misc has no valid bootloader_control block")
bc[0:4] = b"_a\0\0"; bc[12] = 0x9f; bc[14] = 0x1e
bc[28:32] = struct.pack("<I", zlib.crc32(bytes(bc[:28])))
print(bc.hex())
'@
$pyFile = [IO.Path]::GetTempFileName() + '.py'
[IO.File]::WriteAllText($pyFile, $py)
$NEW = (Python $pyFile $miscTmp | Select-Object -Last 1).Trim()
Remove-Item $miscTmp, $pyFile -ErrorAction SilentlyContinue
if ($NEW.Length -ne 64) { Die 'cannot build the slot a boot control block' }
if ($BC -ne $NEW) {
    $bin = [IO.Path]::GetTempFileName()
    [IO.File]::WriteAllBytes($bin, ([byte[]] -split ($NEW -replace '..', '0x$& ')))
    & adb push $bin "$T/mu300-bc-a.bin" | Out-Null
    Remove-Item $bin
    SuDo "dd if=$T/mu300-bc-a.bin of=/dev/block/by-name/misc bs=1 seek=2048 conv=notrunc 2>/dev/null && sync && rm $T/mu300-bc-a.bin" | Out-Null
    if ((Hex32) -ne $NEW) { Die 'misc verify failed' }
    Write-Host 'slot a set'
} else {
    Write-Host 'already on slot a'
}

Say 'Restoring boot_b from boot_a'
$A = (SuDo 'sha256sum /dev/block/by-name/boot_a').Split(' ')[0]
SuDo 'dd if=/dev/block/by-name/boot_a of=/dev/block/by-name/boot_b bs=4M 2>/dev/null && sync' | Out-Null
if ((SuDo 'sha256sum /dev/block/by-name/boot_b').Split(' ')[0] -ne $A) { Die 'boot_b verify failed (misc already points to slot a, Android keeps booting)' }
Write-Host 'boot_b = boot_a'

if ($wipe -ne 'keep') {
    Say "Erasing the Linux filesystem ($wipe)"
    $busy = SuDo "for o in /sys/block/loop*/loop/offset; do [ x`$(cat `$o 2>/dev/null) = x$OFF ] && echo `${o%/loop/offset}; done"
    if ($busy) { Die "the Linux region is still attached ($busy); reboot Android and run again" }
    [int64]$skip = $OFF / 1MB; [int64]$mib = $SIZE / 1MB
    if ($wipe -eq 'quick') {
        SuDo "dd if=/dev/zero of=/dev/block/mmcblk0 bs=1048576 seek=$skip count=64 conv=notrunc 2>/dev/null; sync" | Out-Null
    } else {
        Write-Host "overwriting $([int64]($mib / 1024)) GiB, this takes a few minutes"
        SuDo "command -v blkdiscard >/dev/null && blkdiscard -o $OFF -l $SIZE /dev/block/mmcblk0 2>/dev/null; dd if=/dev/zero of=/dev/block/mmcblk0 bs=1048576 seek=$skip count=$mib conv=notrunc 2>/dev/null; sync" | Out-Null
    }
    $m = (SuDo "dd if=/dev/block/mmcblk0 bs=1 skip=$($OFF + 1080) count=2 2>/dev/null | od -An -tx1") -replace '\s', ''
    if ($m -eq '53ef') { Die 'the filesystem signature is still there' }
    if ($wipe -eq 'secure') {
        $step = [int64]($mib / 32) + 1
        $left = [int](SuDo "n=0; s=$skip; e=$($skip + $mib); while [ `$s -lt `$e ]; do c=`$(dd if=/dev/block/mmcblk0 bs=1048576 skip=`$s count=1 2>/dev/null | tr -d \\000 | wc -c); [ `$c -gt 0 ] && n=`$((n + 1)); s=`$((s + $step)); done; echo `$n").Trim()
        if ($left -ne 0) { Die "$left of 32 samples still contain data; run the secure erase again" }
        Write-Host 'erased and verified (32 samples across the region are empty)'
    } else {
        Write-Host 'erased (headers only)'
    }
}

# (no double quotes in a command for the device: Windows PowerShell 5.1 drops them on the way to adb)
SuDo "grep -qw $T/mu300root /proc/mounts || rm -rf $T/mu300root; rm -f $T/mu300-* $T/android-install.sh $T/android-mount-mu300root.sh" | Out-Null
# the on-device switch would point at a boot_b that is Android again
Say 'Removing the on-device switch (Magisk module)'
if ((SuDo 'magisk -v')) { SuDo '[ -d /data/adb/modules/mu300_linux_switch ] && touch /data/adb/modules/mu300_linux_switch/remove' | Out-Null }

Say 'Done. The device boots stock Android; reboot it once to check.'
