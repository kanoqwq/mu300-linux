<#
.SYNOPSIS
    Remove MU300 Linux and return the device to stock Android (Windows version of uninstall.sh).

.DESCRIPTION
    Run with the device booted in rooted Android and connected over USB (adb). It makes the slot Android runs from
    the boot slot in misc, copies Android's boot image over the Linux slot's (boot_a over boot_b; boot_b over boot_a
    when Android runs from slot b), erases the Linux filesystem in the unpartitioned eMMC region (or inside the GPT
    partition named mu300root another installer made: its contents, never the partition) and, when you say
    so, the one on the SD card (ext4 labelled mu300sd; a card with any other filesystem is never touched), and
    removes the installer leftovers. Android's boot partition, the GPT, userdata and every other partition stay
    untouched.
    Needs: adb and Python 3.

      .\uninstall.ps1 -AnswersFile f.txt   ask nothing: every question is answered by its variable, from that file
                               (NAME=value lines) or the environment (-NonInteractive); a question without an answer
                               is an error that names the variable. $env:MU300_PROGRESS = '1' adds the progress
                               stream (tools\answers.ps1, docs\INSTALLER-AUTOMATION.md). The variables:
        ANDROID_SERIAL=<serial>            the adb device, with more than one attached (or MU300_ANS_DEVICE=<number>)
        MU300_ANS_REBOOT_ANDROID=yes|no    the device runs Linux: reboot it into Android over SSH
        MU300_ANS_WIPE=secure|quick|keep   the Linux filesystem on the eMMC
        MU300_ANS_SD_WIPE=erase|keep       the Linux filesystem on the SD card
        MU300_ANS_CONFIRM=UNINSTALL        the summary's question; @stdin waits for it on standard input
#>
[CmdletBinding()]
param(
    [string]$AnswersFile = '',  # file of NAME=value lines: every question answered by its variable
    [switch]$NonInteractive     # the same, with the variables from the environment
)

$ErrorActionPreference = 'Stop'
$T = '/data/local/tmp'
# the answers mode and the progress stream (tools\answers.ps1: Progress, Info, Step, AnswerNamed). This script is
# English only: T only fills in the {1} of the messages that file shares with install.ps1.
$Top = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
function T($key) {
    $s = [string]$key
    for ($i = 0; $i -lt $args.Count; $i++) { $s = $s.Replace("{$($i + 1)}", [string]$args[$i]) }
    $s
}
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
Progress 'hello' 'script' 'uninstall.ps1' 'version' '1' 'mode' 'uninstall'
# the USB network of each kind of device: F50 192.168.77.1, U30 Air 192.168.78.1, V50 192.168.79.1; $MU300_IP is set
# to the one that answers
$MU300_IP = '192.168.77.1'
function LinuxRunning {
    foreach ($ip in @('192.168.77.1', '192.168.78.1', '192.168.79.1')) {
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

function Say($m) { ProgressSay $m; Write-Host "`n==> $m" -ForegroundColor Cyan }
function Die($m) { Progress 'error' 'message' $m; Write-Host "`nERROR: $m" -ForegroundColor Red; exit 1 }
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
    if (-not [int]::TryParse((Ask 'Device' "$def" 'MU300_ANS_DEVICE'), [ref]$n) -or $n -lt 1 -or $n -gt $all.Count) { Die 'invalid choice' }
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
    $a = if ($script:MuAnswersMode) { AnswerNamed $question $default $Id } else { Read-Host "$question [$default]" }
    $r = if ([string]::IsNullOrWhiteSpace($a)) { $default } else { $a.Trim() }
    if (AnswerSecret ([string]$Id)) { Progress 'question' 'id' $Id 'text' $question 'default' $default 'secret' '1' }
    else { Progress 'question' 'id' $Id 'text' $question 'default' $default 'answer' $r }
    return $r
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
            # -CommandType Application: command lookup ignores case and functions win, so a bare `Get-Command python`
            # finds this very function, whose .Source is empty - and the uninstaller stopped with "Python 3 not
            # found" on every Windows machine (install.ps1 had the same, issue #3)
            $c = @(Get-Command $n -CommandType Application -ErrorAction SilentlyContinue)[0]
            if ($c) { $script:PyExe = $(if ($c.Source) { $c.Source } else { $c.Path }); break }
        }
        if (-not $script:PyExe) { Die 'Python 3 not found' }
    }
    & $script:PyExe @PyArgs
}
# What the card holds, from the ext4 magic and label of its superblock (install.ps1's SdState): yes for a mu300sd
# filesystem, foreign for any other ext4, labelled or not, no for anything else. Only yes is ever erased.
function SdState([string]$Magic, [string]$Label) {
    if ($Magic -ne '53ef') { return 'no' }
    if ($Label.Trim() -eq 'mu300sd') { return 'yes' }
    return 'foreign'
}
function SdExisting {
    $m = (SuDo "dd if=$SD_DEV bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1") -replace '\s', ''
    $l = (SuDo "dd if=$SD_DEV bs=1 skip=1144 count=16 2>/dev/null") -replace '\0', ''
    SdState $m $l
}
# The device command that erases the mu300sd filesystem on the card: the same text as tools/storage.sh's
# sd_erase_cmd (tests/installer.Tests.ps1 compares them). The device checks again right before the write: never
# mmcblk0, an SD card in sysfs, still ext4 labelled mu300sd; Android lets go of the card (vold mounts it as
# /dev/block/vold/public:179,N), anything mounted from its own nodes is unmounted, and a card mount of either kind
# that is still there stops it. Prints ERASED, else nothing is written. Single quotes only around the parts with $ for the device (no double quotes reach it intact).
function SdEraseCommand([string]$Dev) {
    if ($Dev -match '^/dev/block/mmcblk0') { throw "refusing ${Dev}: that is the internal eMMC" }
    if ($Dev -notmatch '^/dev/block/mmcblk[1-9](p[0-9]{1,2})?$') { throw "refusing ${Dev}: not an SD card device" }
    $d = ($Dev -replace '^/dev/block/', '') -replace 'p[0-9]+$', ''
    "case $Dev in */mmcblk0|*/mmcblk0p*) echo REFUSED $Dev is the eMMC; exit 1 ;; esac; " +
    '[ x$(cat /sys/block/' + $d + '/device/type 2>/dev/null) = xSD ] || { echo REFUSED ' + $d + ' is not an SD card; exit 1; }; ' +
    'set -- $(dd if=' + $Dev + ' bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1); [ x$1$2 = x53ef ] || { echo REFUSED no ext4 on ' + $Dev + '; exit 1; }; ' +
    '[ x$(dd if=' + $Dev + ' bs=1 skip=1144 count=16 2>/dev/null | tr -d \\000) = xmu300sd ] || { echo REFUSED no mu300sd on ' + $Dev + '; exit 1; }; ' +
    'for v in $(sm list-volumes 2>/dev/null | grep -o ^public:179,[0-9]*); do sm unmount $v >/dev/null 2>&1; done; ' +
    'while read d m r; do case $d in /dev/block/' + $d + '|/dev/block/' + $d + 'p*) umount $m 2>/dev/null ;; esac; done < /proc/mounts; ' +
    'grep -q -e ^/dev/block/' + $d + ' -e public:179, /proc/mounts && { echo BUSY; exit 1; }; ' +
    'dd if=/dev/zero of=' + $Dev + ' bs=1048576 count=64 conv=notrunc 2>/dev/null; sync; echo ERASED'
}
function Hex32 { (SuDo 'dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null | od -An -tx1') -replace '\s', '' }

Step 'preflight' 5
Say 'Checking host tools and device'
FindAdb
if (-not (Get-Command adb -ErrorAction SilentlyContinue)) { Die 'adb not found' }
# the device in Linux, and only a phone or tablet in Android: that is not the one to touch - reboot the device first
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
    $linux = $linuxFirst -or $linuxAdb.Count -gt 0 -or (LinuxRunning)
    if (-not $linux) { Die 'no adb device (boot Android, enable USB debugging)' }
    Say 'The device is running MU300 Linux, not Android'
    Write-Host '  Uninstalling happens from Android, so the device has to reboot first.'
    if ((Ask 'Reboot the device into Android now? (yes/no)' 'yes' 'MU300_ANS_REBOOT_ANDROID') -ne 'yes') { Die 'boot Android yourself (in Linux: sudo mu300-next-boot android && sudo reboot)' }
    # Over adb where the Linux lets this computer in (every computer, by default): a root shell, no password to
    # type, and it works where the USB network does not. The marker file first: only our Linux is told to reboot.
    $gone = $false; $sshTried = $false
    $open = @(LinuxAdbSerials (Quiet { adb devices -l }) -Open)
    if ($LinuxWanted) { $open = @($open | Where-Object { $_ -eq $LinuxWanted }) }
    $s = $(if ($open.Count -eq 1) { $open[0] } else { $LinuxWanted })
    if ($open.Count -eq 1) {
        if (("" + (Quiet { adb -s $s shell 'test -x /opt/mu300/bin/mu300-next-boot && echo mu300' })).Trim() -eq 'mu300') {
            Write-Host ('  ' + "asking it over adb ($s)")
            Quiet { adb -s $s shell '/opt/mu300/bin/mu300-next-boot android && sync && reboot' } | Out-Null
            for ($i = 0; $i -lt 8; $i++) {
                if (@(LinuxAdbSerials (Quiet { adb devices -l })) -notcontains $s) { $gone = $true; break }
                Start-Sleep 5
            }
            if ($gone) { Write-Host ('  ' + 'rebooting') }
        }
    }
    # Only where the address can be no other device: with two boards in Linux on adb (or the one named not on adb
    # while another one in Linux is), 192.168.77.1 or .78.1 may well be the other one (measured: the F50 named by
    # ANDROID_SERIAL next to a U30 Air in Linux at .78.1).
    $inLinux = @(LinuxAdbSerials (Quiet { adb devices -l }))
    if (-not $gone -and ($inLinux.Count -gt 1 -or ($LinuxWanted -and $inLinux.Count -gt 0 -and $inLinux -notcontains $LinuxWanted))) {
        Die 'more than one device runs MU300 Linux and adb could not reboot this one: boot Android yourself (on the device: sudo mu300-next-boot android && sudo reboot), or give its address with MU300_IP'
    }
    # -t: sudo needs a terminal to ask for the device password; reboot cuts the connection, so watch the port
    foreach ($u in 'ubuntu', 'root') {
        if ($gone -or -not (LinuxRunning)) { break }
        $sshTried = $true
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
    # no way in at all: no SSH on this cable, and an adb that takes authorised keys only or would not reboot
    if (-not $gone -and -not $sshTried -and @(LinuxAdbSerials (Quiet { adb devices -l })).Count -gt 0) {
        Die 'boot Android yourself (in Linux: sudo mu300-next-boot android && sudo reboot)'
    }
    Write-Host '  waiting for Android'
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
    if (-not $lmUp) { Die 'the device did not come back as Android' }
}
# whatever its serial number says: a root shell of our Linux must never be taken for a rooted Android
if (("" + (Quiet { adb shell 'ls -d /opt/mu300/bin 2>/dev/null' })).Trim()) {
    Die 'The device is running MU300 Linux, not Android: boot Android yourself (in Linux: sudo mu300-next-boot android && sudo reboot)'
}
if ((SuDo 'id -u') -ne '0') { Die 'su does not work on the device' }
$model = "$(SuDo 'getprop ro.product.model') / $(SuDo 'getprop ro.product.device')"
Write-Host "device: $model"
# (the ZTE V50, experimental: FINDINGS 95, as install.ps1 takes it)
if ($model -notmatch 'MU300|F50|mu300|U30Air|U30_Air' -and $model -cne 'V50 / MU3351') { Die 'this does not look like a ZTE F50/MU300, U30 Air or V50' }
# Linux is on the slot Android is not on (install.ps1): b next to an Android on a, a next to an Android on b
switch ((SuDo 'getprop ro.boot.slot_suffix').Trim()) {
    '_a' { $ANDROID_SLOT = 'a'; $LINUX_SLOT = 'b' }
    '_b' { $ANDROID_SLOT = 'b'; $LINUX_SLOT = 'a' }
    default { Die 'cannot tell which slot Android runs from (boot Android first: mu300-next-boot android)' }
}
if ((SuDo 'readlink -f /dev/block/by-name/boot_a') -eq (SuDo 'readlink -f /dev/block/by-name/boot_b')) { Die 'boot_a and boot_b are the same partition' }
Write-Host "Android runs from slot $ANDROID_SLOT; Linux is on slot $LINUX_SLOT (boot_$LINUX_SLOT)"
Info 'device.model' $model; Info 'slot.android' $ANDROID_SLOT; Info 'slot.linux' $LINUX_SLOT

Step 'find' 15
Say 'Looking for the Linux installation'
# where the installers look and boot/init boots (tools/storage.sh's region_probe and region_find_existing, the same
# command as install.ps1's): the free region behind the last partition, a GPT partition named mu300root that another
# installer made (kanoqwq's desktop installer, FINDINGS 179), the old fixed offset
# (no double quotes in a device command: Windows PowerShell 5.1 drops them on the way to adb)
$parts = @((SuDo 'e=0; for p in /sys/block/mmcblk0/mmcblk0p*; do x=$(( $(cat $p/start) + $(cat $p/size) )); [ $x -gt $e ] && e=$x; grep -qx PARTNAME=mu300root $p/uevent 2>/dev/null && echo part:$(cat $p/start):$(cat $p/size); done; [ -f /data/mu300linux/openwrt.ext4 ] && echo image; echo $e $(cat /sys/block/mmcblk0/size)').Trim() -split '\s+')
$nums = @($parts | Where-Object { $_ -match '^[0-9]+$' })
$partList = @($parts | Where-Object { $_ -match '^part:[0-9]+:[0-9]+$' })
$image = @($parts | Where-Object { $_ -eq 'image' }).Count
if ($nums.Count -ne 2 -or ($nums.Count + $partList.Count + $image) -ne $parts.Count) { Die 'could not read the partition table from the device (is su granted? try again)' }
if ($partList.Count -gt 1) { Die "the eMMC has $($partList.Count) partitions named mu300root, and which one holds Linux cannot be told; nothing was changed" }
[int64]$lastEnd = $nums[0]; [int64]$disk = $nums[1]
[int64]$OFF = 0; [int64]$SIZE = 0
[int64]$start = [math]::Floor($lastEnd / 4096 + 1) * 4096 * 512
$IN_PART = 0; [int64]$PART_OFF = -1; [int64]$PART_SIZE = 0; $PART_FS = 'none'
if ($partList.Count -eq 1) {
    $pp = $partList[0].Split(':')
    $PART_OFF = [int64]$pp[1] * 512; $PART_SIZE = [int64]$pp[2] * 512
}
# a candidate past the end of the eMMC (a table that reaches the disk's end, issues #52/#65) is never read: that dd
# never returns on the device
function RegionOnDisk([int64]$bytes) { [math]::Floor(($bytes + 2048) / 512) -le $disk }
# the free space behind the last partition and before the backup GPT, or wholly inside the partition named mu300root
function RegionInBounds([int64]$o, [int64]$z) {
    (([math]::Floor($o / 512) -ge $lastEnd) -and ([math]::Floor(($o + $z) / 512) -le ($disk - 34))) -or
        (($PART_OFF -ge 0) -and ($o -ge $PART_OFF) -and (($o + $z) -le ($PART_OFF + $PART_SIZE)))
}
$cands = @($start); if ($PART_OFF -ge 0) { $cands += $PART_OFF }; $cands += [int64]27762098176
foreach ($cand in $cands) {
    if (-not (RegionOnDisk $cand)) { continue }
    if ($cand -eq 27762098176 -and $PART_OFF -ge 0 -and $cand -ge $PART_OFF -and $cand -lt ($PART_OFF + $PART_SIZE)) { continue }
    $m = (SuDo "dd if=/dev/block/mmcblk0 bs=1 skip=$($cand + 1080) count=2 2>/dev/null | od -An -tx1") -replace '\s', ''
    # two bytes on the disk always read as four hex digits: anything else is no answer, never "no installation"
    if ($m -notmatch '^[0-9a-f]{4}$') { Die 'no answer from the device while looking for the Linux filesystem; nothing was changed' }
    $l = (SuDo "dd if=/dev/block/mmcblk0 bs=1 skip=$($cand + 1144) count=16 2>/dev/null") -replace '\0', ''
    if ($cand -eq $PART_OFF -and $m -eq '53ef') { $PART_FS = $(if ($l.Trim() -eq 'mu300root') { 'mu300root' } else { 'ext4' }) }
    if ($m -eq '53ef' -and $l.Trim() -eq 'mu300root') {
        $blocks = [int64]((SuDo "dd if=/dev/block/mmcblk0 bs=1 skip=$($cand + 1028) count=4 2>/dev/null | od -An -tu4").Trim())
        $OFF = $cand; $SIZE = $blocks * 4096
        # a filesystem in the partition is never larger than the partition
        if ($cand -eq $PART_OFF) { $IN_PART = 1; if ($SIZE -gt $PART_SIZE) { $SIZE = $PART_SIZE } }
        break
    }
}
if ($OFF -gt 0) {
    if (-not (RegionInBounds $OFF $SIZE)) { Die 'the mu300root filesystem overlaps a partition, refusing to touch it' }
    if ($OFF % 1MB -ne 0) { Die "unexpected filesystem offset $OFF" }
    if ([math]::Floor($SIZE / 1MB) -le 0) { Die 'the mu300root filesystem has no size, refusing to touch it' }
    Write-Host "Linux filesystem: offset $OFF, $([math]::Floor($SIZE / 1MB)) MiB"
    if ($IN_PART -eq 1) { Write-Host '  in the GPT partition named mu300root that another installer made: an erase empties the partition, the partition itself stays (remove it with the installer that made it)' }
} else {
    Write-Host 'no mu300root filesystem found (already erased?)'
    if ($PART_FS -eq 'ext4') { Write-Host '  the GPT partition named mu300root holds another Linux filesystem (not labelled mu300root): not this project''s, left as it is' }
}
# The SD card (tools/storage.sh's sd_probe): "<block device> <512-byte sectors> <type>" of the first mmc disk that
# is not the eMMC - its first partition when it has one, the whole card otherwise; the type keeps a second eMMC or
# an SDIO function out. Its installation is a filesystem of its own: only ext4 labelled mu300sd counts.
$SD_DEV = ''; [int64]$SD_BYTES = 0; $SD_HAS = 'no'
$sd = @((SuDo 'for b in /sys/block/mmcblk[1-9]; do [ -e $b/device/type ] || continue; n=${b##*/}; if [ -e $b/${n}p1 ]; then echo /dev/block/${n}p1 $(cat $b/${n}p1/size) $(cat $b/device/type); else echo /dev/block/$n $(cat $b/size) $(cat $b/device/type); fi; break; done').Trim() -split '\s+')
if ($sd.Count -eq 3 -and $sd[2] -eq 'SD' -and $sd[1] -match '^[0-9]+$' -and [int64]$sd[1] * 512 -ge 700MB) {
    $SD_DEV = $sd[0]; $SD_BYTES = [int64]$sd[1] * 512
    $SD_HAS = SdExisting
}
if ($SD_HAS -eq 'yes') { Write-Host ('Linux filesystem on the SD card: {0}, {1:N1} GiB' -f $SD_DEV, ($SD_BYTES / 1GB)) }
$BC = Hex32
Info 'region.existing' $(if ($OFF -gt 0) { 'yes' } else { 'no' }); Info 'region.offset' $OFF; Info 'region.size' $SIZE
Info 'region.partition' $IN_PART
Info 'sd.device' $SD_DEV; Info 'sd.bytes' $SD_BYTES; Info 'sd.state' $SD_HAS

Step 'choices' 25
Say 'What should be removed?'
$wipe = 'keep'
if ($OFF -gt 0) {
    Write-Host "  secure  overwrite the whole $([math]::Floor($SIZE / 1MB)) MiB region and verify (recommended, takes a few"
    Write-Host '          minutes; your files are really gone afterwards)'
    Write-Host '  quick   only erase the filesystem headers (fast, but the files stay readable on the flash; an'
    Write-Host '          installation into this space later finds it not empty and asks for the word overwrite)'
    Write-Host '  keep    leave the Linux filesystem in place (it just never boots again)'
    $wipe = Ask 'Erase the Linux filesystem: secure / quick / keep' 'secure' 'MU300_ANS_WIPE'
    if ($wipe -eq 'full') { $wipe = 'secure' }
    if ($wipe -notin @('secure', 'quick', 'keep')) { Die 'invalid choice' }
}
$sdwipe = 'keep'
if ($SD_HAS -eq 'yes') {
    Write-Host '  The SD card holds a Linux installation (ext4 labelled mu300sd):'
    Write-Host '  erase   remove the filesystem from the card (its first 64 MiB are overwritten: fast, but the files'
    Write-Host '          stay readable on the card until the space is reused)'
    Write-Host "  keep    leave the card as it is (it does not boot once boot_$LINUX_SLOT is restored)"
    $sdwipe = Ask 'Linux filesystem on the SD card: erase / keep' 'erase' 'MU300_ANS_SD_WIPE'
    if ($sdwipe -notin @('erase', 'keep')) { Die 'invalid choice' }
}
Progress 'step' 'id' 'confirm' 'percent' '30' 'title' 'What will be done'
Write-Host ''
Write-Host "  misc:     boot slot $ANDROID_SLOT (Android), Linux boot disabled"
Write-Host "  boot_$($LINUX_SLOT):   replaced with a copy of boot_$ANDROID_SLOT (stock Android boot image)"
if ($OFF -eq 0) { Write-Host '  Linux:    no installation on the eMMC' }
else {
    Write-Host "  Linux:    $(if ($wipe -eq 'keep') { 'kept on the eMMC (not bootable)' } else { "$wipe erase of $([math]::Floor($SIZE / 1MB)) MiB at offset $OFF" })"
    if ($IN_PART -eq 1) { Write-Host '            (inside the GPT partition named mu300root: the partition stays, only its contents are erased)' }
}
if ($sdwipe -eq 'erase') { Write-Host "  SD card:  mu300sd filesystem erased ($SD_DEV, first 64 MiB)" }
elseif ($SD_HAS -eq 'yes') { Write-Host "  SD card:  kept ($SD_DEV, not bootable)" }
else { Write-Host '  SD card:  no installation' }
Write-Host "  untouched: boot_$ANDROID_SLOT, GPT, userdata and all other partitions$(if ($IN_PART -eq 1) { ' (the partition named mu300root too: it stays)' })"
# the word itself, in capitals (-cne: PowerShell's -ne does not tell uninstall from UNINSTALL)
if ((Ask 'Type UNINSTALL to continue' 'no' 'MU300_ANS_CONFIRM') -cne 'UNINSTALL') { Die 'cancelled' }

# Android still runs from the slot it ran from at the start: its partition is never the one written below
if ((SuDo 'getprop ro.boot.slot_suffix').Trim() -ne "_$ANDROID_SLOT") { Die "Android no longer runs from slot $ANDROID_SLOT; nothing was changed" }
Step 'misc' 40
Say "Making slot $ANDROID_SLOT the boot slot"
$miscTmp = [IO.Path]::GetTempFileName()
SuDoToFile 'dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null' $miscTmp
$py = @'
import struct, sys, zlib
head = open(sys.argv[1], "rb").read()
bc = bytearray(head[0x800:0x820])
if len(bc) != 32 or bc[4:8] != b"BCAB" or zlib.crc32(bytes(bc[:28])) != struct.unpack("<I", bc[28:])[0]:
    sys.exit("misc has no valid bootloader_control block")
# Android's slot active (prio 15, successful), the other inactive (build-boot-image.py's misc-bc-slot-a/b.bin)
if sys.argv[2] == "a":
    bc[0:4] = b"_a\0\0"; bc[12] = 0x9f; bc[14] = 0x1e
else:
    bc[0:4] = b"_b\0\0"; bc[12] = 0x1e; bc[14] = 0x9f
bc[28:32] = struct.pack("<I", zlib.crc32(bytes(bc[:28])))
print(bc.hex())
'@
$pyFile = [IO.Path]::GetTempFileName() + '.py'
[IO.File]::WriteAllText($pyFile, $py)
$NEW = (Python $pyFile $miscTmp $ANDROID_SLOT | Select-Object -Last 1).Trim()
Remove-Item $miscTmp, $pyFile -ErrorAction SilentlyContinue
if ($NEW.Length -ne 64) { Die "cannot build the slot $ANDROID_SLOT boot control block" }
if ($BC -ne $NEW) {
    $bin = [IO.Path]::GetTempFileName()
    [IO.File]::WriteAllBytes($bin, ([byte[]] -split ($NEW -replace '..', '0x$& ')))
    & adb push $bin "$T/mu300-bc-a.bin" | Out-Null
    Remove-Item $bin
    SuDo "dd if=$T/mu300-bc-a.bin of=/dev/block/by-name/misc bs=1 seek=2048 conv=notrunc 2>/dev/null && sync && rm $T/mu300-bc-a.bin" | Out-Null
    if ((Hex32) -ne $NEW) { Die 'misc verify failed' }
    Write-Host "slot $ANDROID_SLOT set"
} else {
    Write-Host "already on slot $ANDROID_SLOT"
}

Step 'boot' 55
Say "Restoring boot_$LINUX_SLOT from boot_$ANDROID_SLOT"
$A = (SuDo "sha256sum /dev/block/by-name/boot_$ANDROID_SLOT").Split(' ')[0]
SuDo "dd if=/dev/block/by-name/boot_$ANDROID_SLOT of=/dev/block/by-name/boot_$LINUX_SLOT bs=4M 2>/dev/null && sync" | Out-Null
if ((SuDo "sha256sum /dev/block/by-name/boot_$LINUX_SLOT").Split(' ')[0] -ne $A) { Die "boot_$LINUX_SLOT verify failed (misc already points to slot $ANDROID_SLOT, Android keeps booting)" }
Write-Host "boot_$LINUX_SLOT = boot_$ANDROID_SLOT"

if ($wipe -ne 'keep') {
    Step 'erase' 65
    Say "Erasing the Linux filesystem ($wipe)"
    $busy = SuDo "for o in /sys/block/loop*/loop/offset; do [ x`$(cat `$o 2>/dev/null) = x$OFF ] && echo `${o%/loop/offset}; done"
    if ($busy) { Die "the Linux region is still attached ($busy); reboot Android and run again" }
    # whole MiB, rounded down ([int64] of a division rounds to even, and up would write past a partition's end)
    [int64]$skip = [math]::Floor($OFF / 1MB); [int64]$mib = [math]::Floor($SIZE / 1MB)
    # once more, in the units the writes use: every megabyte written lies in the free space or in the partition
    if (-not (RegionInBounds ($skip * 1MB) ($mib * 1MB))) { Die 'the region does not lie in the free space; the filesystem was not erased' }
    if ($wipe -eq 'quick') {
        # (never more than the filesystem: in a partition named mu300root that is the partition's end)
        $n = [math]::Min(64, $mib)
        SuDo "dd if=/dev/zero of=/dev/block/mmcblk0 bs=1048576 seek=$skip count=$n conv=notrunc 2>/dev/null; sync" | Out-Null
    } else {
        Write-Host "overwriting $mib MiB, this takes a few minutes"
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

if ($sdwipe -eq 'erase') {
    Step 'sd' 80
    Say 'Erasing the Linux filesystem on the SD card'
    if ((SdExisting) -ne 'yes') { Die "the SD card ($SD_DEV) holds no mu300sd filesystem; nothing was erased" }
    try { $cmd = SdEraseCommand $SD_DEV } catch { Die $_.Exception.Message }
    $out = SuDo $cmd
    if ($out -notmatch 'ERASED') { Die "the SD card was not erased: $out" }
    if ((SdExisting) -ne 'no') { Die 'the SD card still shows a mu300sd filesystem' }
    Write-Host "erased ($SD_DEV)"
}
# A kept internal installation may hold the marker of a card installation next to it (boot/init then waits for
# the card). With the card's installation gone it would only make a later boot wait for nothing.
if ($OFF -gt 0 -and $wipe -eq 'keep' -and ($sdwipe -eq 'erase' -or $SD_HAS -ne 'yes')) {
    # LF line ends whatever the clone has (Android's sh stops at a CR)
    $lf = [IO.Path]::GetTempFileName()
    [IO.File]::WriteAllText($lf, ([IO.File]::ReadAllText((Join-Path $PSScriptRoot 'tools\android-mount-mu300root.sh')) -replace "`r`n", "`n"))
    $dst = "$T/android-mount-mu300root.sh"
    Quiet { adb push $lf $dst } | Out-Null
    Remove-Item $lf -ErrorAction SilentlyContinue
    $r = SuDo "MU300_OFF=$OFF MU300_SIZE=$SIZE sh $T/android-mount-mu300root.sh $T/mu300root >/dev/null && { rm -f $T/mu300root/.mu300/root-on-sd; sync; sh $T/android-mount-mu300root.sh -u $T/mu300root >/dev/null; echo CLEARED; }"
    if ($r -notmatch 'CLEARED') { Write-Host "note: could not open the kept Linux filesystem to clear its SD card marker (harmless: boot_$LINUX_SLOT is Android)" }
}

# (no double quotes in a command for the device: Windows PowerShell 5.1 drops them on the way to adb)
# (files only: /data/local/tmp/mu300-web is the web installer's directory)
SuDo "grep -qw $T/mu300root /proc/mounts || rm -rf $T/mu300root; for f in $T/mu300-*; do [ -d `$f ] || rm -f `$f; done; rm -f $T/android-install.sh $T/android-mount-mu300root.sh" | Out-Null
# the on-device switch would point at a Linux slot that holds Android again
Step 'switch' 95
Say 'Removing the on-device switch (Magisk module)'
if ((SuDo 'magisk -v')) { SuDo '[ -d /data/adb/modules/mu300_linux_switch ] && touch /data/adb/modules/mu300_linux_switch/remove' | Out-Null }

Step 'done' 100
Say 'Done. The device boots stock Android; reboot it once to check.'
Progress 'done' 'result' 'ok'
