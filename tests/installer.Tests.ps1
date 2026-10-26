# Tests for install.ps1's own functions, under Windows PowerShell 5.1 and PowerShell 7 (no Pester needed):
#   powershell -NoProfile -ExecutionPolicy Bypass -File tests\installer.Tests.ps1
# The functions are taken out of install.ps1 with the PowerShell parser and defined here, so the installer itself
# never runs. Exit code: the number of failed checks.
$ErrorActionPreference = 'Stop'
$Top = Split-Path -Parent $PSScriptRoot
$script:failed = 0
$script:passed = 0
# the answers mode and the progress stream, which the installers' functions call (silent unless a test turns it on)
. (Join-Path (Join-Path $Top 'tools') 'answers.ps1')
function Check($name, $got, $want) {
    if ([string]$got -ceq [string]$want) { $script:passed++ }
    else { $script:failed++; Write-Host "FAIL $name`n  got:  [$got]`n  want: [$want]" -ForegroundColor Red }
}

# the functions under test, straight from install.ps1
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $Top "install.ps1"), [ref]$null, [ref]$null)
$want = 'LoadLanguage', 'T', 'NormalizeAnswer', 'AndroidLines', 'LinuxAdbSerials', 'Gib', 'ChooseStorage', 'StorageDefault', 'InternalOverCard', 'SdState', 'SdKernelOk', 'InstallEnvText', 'ChooseOpenWrt', 'AdbPubKey', 'BootchainLine', 'BootchainRefused', 'PackageDir', 'PackageManifest', 'PackageCheck', 'SystemsDefault', 'ChoiceThatFits'
$defs = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $want -contains $n.Name }, $true)
foreach ($d in $defs) { . ([scriptblock]::Create($d.Extent.Text)) }
foreach ($w in 'LoadLanguage', 'T', 'NormalizeAnswer') {
    if (-not (Get-Command $w -CommandType Function -ErrorAction SilentlyContinue)) { Write-Host "FAIL: $w not found in install.ps1"; exit 99 }
}
$script:Msg = New-Object 'System.Collections.Generic.Dictionary[string,string]'

# ---- T: every translation, every placeholder --------------------------------------------------------------------
$argsT = 'A1', 'B2', 'C3', 'D4', 'E5', 'F6'
foreach ($lang in 'tr', 'zh') {
    LoadLanguage $lang
    $lines = [IO.File]::ReadAllLines((Join-Path (Join-Path $Top "i18n") "$lang.tsv"), [Text.Encoding]::UTF8)
    foreach ($line in $lines) {
        $i = $line.IndexOf("`t")
        if ($i -le 0 -or $line.StartsWith('#')) { continue }
        $key = $line.Substring(0, $i); $val = $line.Substring($i + 1)
        for ($k = 1; $k -le 6; $k++) { $val = $val.Replace("{$k}", $argsT[$k - 1]) }
        Check "T $lang $key" (T $key @argsT) $val
    }
}
LoadLanguage 'en'
Check 'T english' (T 'device: {1}' 'F50 / MU300') 'device: F50 / MU300'
Check 'T untranslated' (T 'nobody translated {1} {2}' 'x' 'y') 'nobody translated x y'
Check 'T no args' (T 'plain {1}') 'plain {1}'
# the dictionary is case-sensitive: two messages may differ only in case
LoadLanguage 'tr'
Check 'T case' (T 'DEVICE: {1}' 'x') 'DEVICE: x'

# ---- NormalizeAnswer --------------------------------------------------------------------------------------------
$cases = @{
    'evet' = 'yes'; 'e' = 'yes'; 'y' = 'yes'; "$([char]0x662F)" = 'yes'
    ('hay' + [char]0x131 + 'r') = 'no'; 'hayir' = 'no'; 'n' = 'no'; "$([char]0x5426)" = 'no'
    ('g' + [char]0xFC + 'ncelle') = 'update'; 'guncelle' = 'update'; "$([char]0x66F4)$([char]0x65B0)" = 'update'
    'sil' = 'wipe'; 'INSTALL' = 'INSTALL'; 'overwrite' = 'overwrite'; '6.18' = '6.18'
}
foreach ($k in $cases.Keys) { Check "NormalizeAnswer $k" (NormalizeAnswer $k) $cases[$k] }

# ---- Gib (the sizes the free-space check prints) ----------------------------------------------------------------
if (Get-Command Gib -ErrorAction SilentlyContinue) {
    Check 'Gib' ((Gib 34828075008) -replace ',', '.') '32.4 GiB'
}

# ---- ChooseStorage: internal region or SD card -------------------------------------------------------------------
Check 'no card'            (ChooseStorage '' 0 30GB '' '') 'internal'
Check 'card, default'      (ChooseStorage '/dev/block/mmcblk1p1' 62GB 30GB '' '') 'internal'
Check 'card, answer sd'    (ChooseStorage '/dev/block/mmcblk1p1' 62GB 30GB '' 'sd') 'sd'
Check 'small internal'     (ChooseStorage '/dev/block/mmcblk1p1' 62GB 100MB '' '') 'sd'
Check 'forced internal'    (ChooseStorage '/dev/block/mmcblk1p1' 62GB 100MB 'internal' '') 'internal'
Check 'forced sd'          (ChooseStorage '/dev/block/mmcblk1p1' 62GB 30GB 'sd' '') 'sd'
$threw = $false; try { ChooseStorage '' 0 30GB 'sd' '' | Out-Null } catch { $threw = $true }
Check 'forced sd, no card' $threw $true
$threw = $false; try { ChooseStorage '/dev/block/mmcblk1p1' 62GB 30GB '' 'usb' | Out-Null } catch { $threw = $true }
Check 'invalid answer'     $threw $true
$threw = $false; try { ChooseStorage '/dev/block/mmcblk1p1' 62GB 30GB 'usb' '' | Out-Null } catch { $threw = $true }
Check 'invalid forced'     $threw $true

# ---- ChooseOpenWrt: plain OpenWrt or with the MU300 control panel (install.sh's choose_systems) --------------------
Check 'owrt 3 + 2'         ((ChooseOpenWrt @('ubuntu', 'openwrt') '' '2') -join ' ') 'ubuntu openwrt-luci'
Check 'owrt 2 + 1'         ((ChooseOpenWrt @('openwrt') '' '1') -join ' ') 'openwrt'
Check 'owrt default'       ((ChooseOpenWrt @('openwrt') '' '') -join ' ') 'openwrt'
Check 'owrt preset luci'   ((ChooseOpenWrt @('openwrt') 'luci' '') -join ' ') 'openwrt-luci'
Check 'owrt preset plain'  ((ChooseOpenWrt @('ubuntu', 'openwrt') 'plain' '2') -join ' ') 'ubuntu openwrt'
Check 'owrt one element'   (@(ChooseOpenWrt @('openwrt') 'luci' '').Count) 1
$threw = $false; try { ChooseOpenWrt @('openwrt') 'x' '' | Out-Null } catch { $threw = $true }
Check 'owrt bad preset'    $threw $true
$threw = $false; try { ChooseOpenWrt @('openwrt') '' '7' | Out-Null } catch { $threw = $true }
Check 'owrt bad answer'    $threw $true
# a one-system result unrolls to a plain string, so the call site must wrap it in @() or $OSES[0] is one letter
$bare = ChooseOpenWrt @('openwrt') 'luci' ''
Check 'owrt bare result unrolls'  ($bare -is [string]) $true
$site = [IO.File]::ReadAllText((Join-Path $Top 'install.ps1'))
Check 'owrt call site wraps in @()'  ($site -match '\$OSES = @\(ChooseOpenWrt ') $true
# ---- SystemsDefault, ChoiceThatFits: Enter chooses what fits; a refusal names it (FINDINGS 153) -----------------------
LoadLanguage 'en'
Check 'systems default both'      (SystemsDefault 2400MB 1600MB 2400MB) '3'
Check 'systems default one'       (SystemsDefault 2399MB 1600MB 2400MB) '1'
Check 'systems default openwrt'   (SystemsDefault 804MB 1600MB 2400MB) '2'
Check 'fits openwrt'              (ChoiceThatFits 804MB 800MB 1600MB) 'Choose 2 (OpenWrt) instead: it fits here.'
Check 'fits one'                  (ChoiceThatFits 2000MB 800MB 1600MB) 'Choose 1 (Ubuntu) or 2 (OpenWrt) instead: one system fits here.'
Check 'fits nothing'              (ChoiceThatFits 700MB 800MB 1600MB) ''
Check 'systems default call site' ($site -match 'Ask \(T ''Choice''\) \(SystemsDefault \$SIZE ') $true
LoadLanguage 'tr'   # (as the checks below have always run)
foreach ($pair in @(@('', 'openwrt'), @('luci', 'openwrt-luci'))) {
    $OSES = @(ChooseOpenWrt @('openwrt') $pair[0] '')
    Check "owrt single [$($pair[1])] first"  $OSES[0] $pair[1]
    Check "owrt single [$($pair[1])] count"  $OSES.Count 1
}
# the boot question accepts the names that were chosen: 'openwrt' is not one of them after the second answer
$o = ChooseOpenWrt @('ubuntu', 'openwrt') '' '2'
Check 'boot openwrt-luci ok'  ('openwrt-luci' -in $o) $true
Check 'boot openwrt refused'  ('openwrt' -in $o) $false
# a card that holds an installation already is the default: init starts it before anything internal
Check 'installed card, default'    (ChooseStorage '/dev/block/mmcblk1p1' 62GB 30GB '' '' 'yes') 'sd'
Check 'blank card, default'        (ChooseStorage '/dev/block/mmcblk1p1' 62GB 30GB '' '' 'no') 'internal'
Check 'foreign card, default'      (ChooseStorage '/dev/block/mmcblk1p1' 62GB 30GB '' '' 'foreign') 'internal'
Check 'installed card, internal'   (ChooseStorage '/dev/block/mmcblk1p1' 62GB 30GB '' 'internal' 'yes') 'internal'
Check 'StorageDefault installed'   (StorageDefault 30GB 'yes') 'sd'
Check 'StorageDefault small'       (StorageDefault 100MB 'no') 'sd'
Check 'StorageDefault room'        (StorageDefault 30GB 'no') 'internal'
Check 'internal over card'         (InternalOverCard 'internal' 'yes') $true
Check 'internal, blank card'       (InternalOverCard 'internal' 'no') $false
Check 'internal, foreign card'     (InternalOverCard 'internal' 'foreign') $false
Check 'sd over card'               (InternalOverCard 'sd' 'yes') $false
$src = [IO.File]::ReadAllText((Join-Path $Top 'install.ps1'))
$iO = $src.IndexOf('if (InternalOverCard $where $sdEx) {')
$iA = $src.IndexOf("if ((Ask (T 'Type internal to install to internal storage anyway') 'no' 'MU300_ANS_INTERNAL_OVER_CARD') -ne 'internal') { Die (T 'cancelled') }")
Check 'internal over card asks'    ($iO -ge 0 -and $iA -gt $iO -and $iA - $iO -lt 600) $true

# ---- SdState: what is on the card already (ext4 magic at 1080, label at 1144) -------------------------------------
Check 'sd mu300sd'         (SdState '53ef' 'mu300sd') 'yes'
Check 'sd other label'     (SdState '53ef' 'data') 'foreign'
Check 'sd no label'        (SdState '53ef' '') 'foreign'
Check 'sd root label'      (SdState '53ef' 'mu300root') 'foreign'
Check 'sd not ext4'        (SdState '0000' 'mu300sd') 'no'
Check 'sd unreadable'      (SdState '' '') 'no'

# ---- SdKernelOk: a card installation needs a bundle that lists sdcard in ./features ------------------------------
$kd = Join-Path ([IO.Path]::GetTempPath()) ('mu300-k-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $kd | Out-Null
Check 'sd kernel, no features'      (SdKernelOk 1 $kd) $false
Check 'internal, no features'       (SdKernelOk 0 $kd) $true
[IO.File]::WriteAllText((Join-Path $kd 'features'), "other`n")
Check 'sd kernel, other features'   (SdKernelOk 1 $kd) $false
[IO.File]::WriteAllText((Join-Path $kd 'features'), "sdcard`n")
Check 'sd kernel, sdcard'           (SdKernelOk 1 $kd) $true
Remove-Item -Recurse -Force $kd
$src = [IO.File]::ReadAllText((Join-Path $Top 'install.ps1'))
$iUnpack = $src.IndexOf('& tar -xzf "$ASSETS\mu300-kernel-$KERNEL.tar.gz" -C $KMAIN')
$iCheck = $src.IndexOf('if (-not (SdKernelOk $SD_MODE $KMAIN))')
Check 'sd kernel check after unpack' ($iUnpack -ge 0 -and $iCheck -gt $iUnpack) $true

# ---- InstallEnvText: mu300-install.env, the same text tools/storage.sh's write_install_env writes ------------------
$common = @{ OFF = [int64]27762098176; FORMAT = 1; OSES = @('ubuntu', 'openwrt'); WIPE_LEGACY = 0; UPDATE = 0
    BOOT_OS = 'openwrt'; DEFAULT_LINUX = 1; BOOT_ATTEMPTS = 5; IMPORT_HOTSPOT = 1; KERNEL = '6.18'; SYNC_BOOTCHAIN = 1
    PWHASH = '$6$salt$hash/x.y' }
$sdEnv = $common.Clone(); $sdEnv.SIZE = [int64]31914967040; $sdEnv.INT_SIZE = [int64]34776023040; $sdEnv.SD_MODE = 1
$sdEnv.SD_DEV = '/dev/block/mmcblk1p1'; $sdEnv.INTERNAL_EXISTS = 1
$inEnv = $common.Clone(); $inEnv.SIZE = [int64]34776023040; $inEnv.INT_SIZE = [int64]0; $inEnv.SD_MODE = 0
$inEnv.SD_DEV = ''; $inEnv.INTERNAL_EXISTS = 0; $inEnv.FORMAT = 0; $inEnv.UPDATE = 1
$inEnv.ADB = 1; $inEnv.ADB_KEY = ('QAAAA' + ('A' * 694) + '= me@host'); $inEnv.KEEP_PW_OSES = @('openwrt')
$t = InstallEnvText $sdEnv
Check 'env sd: region'      ($t -match "(?m)^OFF=27762098176\nSIZE=34776023040\nOFF_S=54222848\nSIZE_S=67921920\n") $true
Check 'env sd: card'        ($t -match "(?m)^SD_MODE=1\nSD_DEV=/dev/block/mmcblk1p1\nINTERNAL_EXISTS=1\n") $true
Check 'env sd: format'      ($t -match "(?m)^FORMAT=1\n" -and $t -match "(?m)^UPDATE=0\n") $true
Check 'env sd: oses'        ($t -match '(?m)^OSES="ubuntu openwrt"\n') $true
Check 'env sd: hash'        ($t.EndsWith("SYNC_BOOTCHAIN=1`nPWHASH='`$6`$salt`$hash/x.y'`nKEEP_PW_OSES=`"`"`n")) $true
Check 'env sd: no CR'       ($t.Contains("`r")) $false
$t = InstallEnvText $inEnv
Check 'env internal'        ($t -match "(?m)^OFF=27762098176\nSIZE=34776023040\nOFF_S=54222848\nSIZE_S=67921920\nFORMAT=0\n") $true
Check 'env internal: sd'    ($t -match "(?m)^UPDATE=1\n" -and $t -match "(?m)^SD_MODE=0\nSD_DEV=\nINTERNAL_EXISTS=0\n") $true
Check 'env internal: adb'   ($t -match ("(?m)^ADB=1\nADB_KEY='QAAAA" + ('A' * 694) + "= me@host'\nSYNC_BOOTCHAIN=1\nPWHASH=")) $true
Check 'env sd: adb off'     ((InstallEnvText $sdEnv) -match "(?m)^ADB=0\nADB_KEY=''\n") $true
Check 'env internal: kept passwords' ($t.EndsWith("`nKEEP_PW_OSES=`"openwrt`"`n")) $true

# ---- AdbPubKey: the installing computer's adb key, safe inside single quotes --------------------------------------
$key = 'QAAAA' + ('A' * 694) + '='
$kf = Join-Path ([IO.Path]::GetTempPath()) "mu300-adbkey-$PID.pub"
[IO.File]::WriteAllText($kf, "$key me@my-pc")
Check 'adbkey: plain'       (AdbPubKey $kf) "$key me@my-pc"
[IO.File]::WriteAllText($kf, "$key evil'`$(reboot) `n")
Check 'adbkey: name cleaned' (AdbPubKey $kf) "$key evilreboot"
[IO.File]::WriteAllText($kf, $key)
Check 'adbkey: no name'     (AdbPubKey $kf) "$key installer"
[IO.File]::WriteAllText($kf, "ssh-rsa AAAAB3NzaC1yc2E me@my-pc")
Check 'adbkey: not adb'     (AdbPubKey $kf) ''
[IO.File]::WriteAllText($kf, ($key.Substring(1)) + ' x')
Check 'adbkey: short'       (AdbPubKey $kf) ''
Remove-Item $kf
Check 'adbkey: missing'     (AdbPubKey $kf) ''

# ---- a device running MU300 Linux among the adb devices (adb is on by default there): never one to install to ----
$f50Line = '324950664950           device usb:1 product:MU300 model:F50 device:MU300 transport_id:1'
$devs = @('List of devices attached', $f50Line, 'MU300LINUX-324950664950-203d8329 device usb:2 transport_id:2',
    'MU300LINUX-323960377386 unauthorized usb:3 transport_id:3', '192.168.31.1:55555     offline transport_id:4', '')
Check 'adb: android lines'     (@(AndroidLines $devs) -join '|') $f50Line
Check 'adb: linux serials'     (@(LinuxAdbSerials $devs) -join '|') 'MU300LINUX-324950664950-203d8329|MU300LINUX-323960377386'
Check 'adb: linux, let in'     (@(LinuxAdbSerials $devs -Open) -join '|') 'MU300LINUX-324950664950-203d8329'
$only = @('List of devices attached', 'MU300LINUX-324950664950-203d8329 device usb:2 transport_id:2', '')
Check 'adb: only linux'        (@(AndroidLines $only).Count) 0
Check 'adb: one linux'         (@(LinuxAdbSerials $only -Open).Count) 1
Check 'adb: nothing attached'  (@(LinuxAdbSerials @('List of devices attached', '')).Count) 0
Check 'adb: no output'         (@(AndroidLines $null).Count) 0

if (Get-Command sh -CommandType Application -ErrorAction SilentlyContinue) {
    foreach ($e in $sdEnv, $inEnv) {
        $vars = (($e.Keys | Sort-Object | ForEach-Object { "$_='$(@($e[$_]) -join ' ')'" }) -join '; ')
        if ($e.SD_MODE -eq 0) { $vars += "; INT_SIZE=''" }
        $shText = ((& sh -c ($vars + '; . ./tools/storage.sh; write_install_env')) -join "`n") + "`n"
        Check "env = storage.sh (SD_MODE=$($e.SD_MODE))" (InstallEnvText $e) $shText
    }
}

# ---- BootchainLine / BootchainRefused: the plan's line from android-bootchain.sh check (storage.sh's bootchain_line) --
LoadLanguage 'en'
Check 'bootchain identical' (BootchainLine "BOOTCHAIN-PLAN a b`r`nboot chain: identical on both slots") 'boot chain:     identical on both slots'
Check 'bootchain differ'    (BootchainLine "BOOTCHAIN-PLAN b a sml trustos`nx") "boot chain:     sml, trustos differ between the slots; Android's slot b is copied to slot a (backed up first)"
Check 'bootchain refused'   (BootchainLine 'BOOTCHAIN-REFUSED: uboot_a and uboot_b differ in size (1 and 2 bytes)') ''
Check 'bootchain reason'    (BootchainRefused "x`nBOOTCHAIN-REFUSED: uboot_a and uboot_b differ in size (1 and 2 bytes)") 'uboot_a and uboot_b differ in size (1 and 2 bytes)'
Check 'bootchain no answer' (BootchainRefused '') 'no answer from the device'

# ---- uninstall.ps1: what is on the card, and the command that erases it -----------------------------------------
$uast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $Top "uninstall.ps1"), [ref]$null, [ref]$null)
$udefs = $uast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and @('SdState', 'SdEraseCommand') -contains $n.Name }, $true)
foreach ($d in $udefs) { . ([scriptblock]::Create($d.Extent.Text)) }
Check 'uninstall sd mu300sd'    (SdState '53ef' 'mu300sd') 'yes'
Check 'uninstall sd other'      (SdState '53ef' 'mu300root') 'foreign'
Check 'uninstall sd not ext4'   (SdState '0000' 'mu300sd') 'no'
foreach ($dev in '/dev/block/mmcblk0p1', '/dev/block/mmcblk0', '/dev/block/sda1', '/dev/block/mmcblk1p1 x', '') {
    $threw = $false; try { SdEraseCommand $dev | Out-Null } catch { $threw = $true }
    Check "erase refuses [$dev]" $threw $true
}
$cmd = SdEraseCommand '/dev/block/mmcblk1p1'
Check 'erase command quotes'    ($cmd -match "[`"']") $false
Check 'erase command target'    ($cmd -match ' of=/dev/block/mmcblk1p1 bs=1048576 count=64 ') $true
# the same text tools/storage.sh's sd_erase_cmd builds (and tests/test_installer.py runs against stubs)
if (Get-Command sh -CommandType Application -ErrorAction SilentlyContinue) {
    foreach ($dev in '/dev/block/mmcblk1p1', '/dev/block/mmcblk1') {
        $shCmd = (& sh -c ('unset MU300_SYSFS MU300_MOUNTS; . ./tools/storage.sh; sd_erase_cmd ' + $dev)) -join "`n"
        Check "erase command = storage.sh ($dev)" (SdEraseCommand $dev) $shCmd
    }
}

# ---- the VPN question: yes by default over a system whose VPN is on ---------------------------------------------
$vdefs = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and @('VpnOnSystems', 'VpnDefault') -contains $n.Name }, $true)
foreach ($d in $vdefs) { . ([scriptblock]::Create($d.Extent.Text)) }
$T = '/data/local/tmp'
function PushUnix($src, $dst) { $true }
$script:suCmd = ''
$script:suOut = "ubuntu`r`n"
function SuDo($cmd) { $script:suCmd = $cmd; $script:suOut }
Check 'vpn on: ubuntu'          ((VpnOnSystems @('ubuntu', 'openwrt') 'yes' 0 '' 123 456) -join ' ') 'ubuntu'
Check 'vpn on: default yes'     (VpnDefault @(VpnOnSystems @('ubuntu', 'openwrt') 'yes' 0 '' 123 456)) 'yes'
Check 'vpn on: read only'       ($script:suCmd -match '^MU300_OFF=123 MU300_SIZE=456 MU300_RO=1 sh /data/local/tmp/android-mount-mu300root.sh ') $true
Check 'vpn on: no quotes'       ($script:suCmd -match "'") $false
Check 'vpn on: loop variable'   ($script:suCmd -match 'vpn_killswitch_wanted /data/local/tmp/mu300probe/\$o; then echo \$o:ks;') $true
Check 'vpn on: orphan lib'      ($script:suCmd -match '\. /data/local/tmp/vpn-orphan\.sh;') $true
$script:suOut = "ubuntu`nopenwrt:ks`n"
Check 'vpn on: kill switch'     ((VpnOnSystems @('ubuntu', 'openwrt') 'yes' 0 '' 123 456) -join ' ') 'ubuntu openwrt:ks'
Check 'vpn ks: default yes'     (VpnDefault @(VpnOnSystems @('openwrt') 'yes' 0 '' 123 456)) 'yes'
$script:suOut = "ubuntu`r`n"
$null = VpnOnSystems @('ubuntu') 'yes' 1 '/dev/block/mmcblk1p1' 0 0
Check 'vpn on: the card'        ($script:suCmd -match '^MU300_SD_DEV=/dev/block/mmcblk1p1 MU300_RO=1 ') $true
$script:suOut = ''
Check 'vpn off: default no'     (VpnDefault @(VpnOnSystems @('ubuntu', 'openwrt') 'yes' 0 '' 123 456)) 'no'
$script:suOut = "openwrt`n"
Check 'vpn on elsewhere: no'    (VpnDefault @(VpnOnSystems @('ubuntu') 'yes' 0 '' 123 456)) 'no'
$script:suCmd = ''
Check 'no install: default no'  (VpnDefault @(VpnOnSystems @('ubuntu') 'no' 0 '' 123 456)) 'no'
Check 'no install: no probe'    $script:suCmd ''

# ---- making room (issue #32): install.sh's offer_repartition, here OfferRepartition -------------------------------
$rnames = 'Quiet', 'FreeRegion', 'GptShow', 'RepartitionPlan', 'RepartitionSectors', 'LayoutFigures', 'GptTool', 'GptDump', 'OfferRepartition',
    'ResumePy', 'ParseFacts', 'FactsOk', 'ResumeFacts', 'ResumeBackup', 'ResumePush', 'ResumeSha', 'WriteUnix', 'ResumePending'
$rdefs = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $rnames -contains $n.Name }, $true)
foreach ($d in $rdefs) { . ([scriptblock]::Create($d.Extent.Text)) }
foreach ($w in $rnames) {
    if (-not (Get-Command $w -CommandType Function -ErrorAction SilentlyContinue)) { Write-Host "FAIL: $w not found in install.ps1"; exit 99 }
}
LoadLanguage 'en'
# what the make-room path reads to identify the device, as install.ps1 sends it (the same text as tools/resume.sh's)
$FactsCmd = [regex]::Match([IO.File]::ReadAllText((Join-Path $Top 'install.ps1')), "(?m)^\`$script:FactsCmd = '([^']*)'").Groups[1].Value
$script:FactsCmd = $FactsCmd
$shFacts = [regex]::Match([IO.File]::ReadAllText((Join-Path (Join-Path $Top 'tools') 'resume.sh')), "_rf=\`$\(su_do '([^']*)'\)").Groups[1].Value
Check 'facts: the same command as install.sh' ($FactsCmd -and $FactsCmd -ceq $shFacts) $true
Check 'facts: no double quotes (Windows PowerShell 5.1 drops them)' $FactsCmd.Contains('"') $false
$pf = ParseFacts "NODE=mmcblk0p18`r`nCID=15010053454d36344720c1a6b7d2e900`nSTART=12275712`nSIZE=109879296`nSTATE=encrypted`nTYPE=file`nSOURCE=/dev/block/dm-6`nMAGIC=`nEXT="
Check 'facts: read'             "$($pf.NODE) $($pf.START) $($pf.SIZE) $($pf.STATE) $($pf.TYPE) [$($pf.MAGIC)]" 'mmcblk0p18 12275712 109879296 encrypted file []'
$pf.SERIAL = '324950664950'
Check 'facts: ok'               (FactsOk $pf) $true
foreach ($bad in @(@{ NODE = 'sda1' }, @{ NODE = 'mmcblk0p18;x' }, @{ CID = 'XYZ' }, @{ CID = ('a' * 31) }, @{ START = '' }, @{ SIZE = '1e3' }, @{ SERIAL = '' })) {
    $b = $pf.Clone(); foreach ($k in $bad.Keys) { $b[$k] = $bad[$k] }
    Check "facts: refused $(@($bad.Keys) -join ',')" (FactsOk $b) $false
}
# The figures of the 64 GB eMMC (FINDINGS #9): 122159104 sectors, last usable LBA 122155007, userdata from sector
# 12275712, 20 GiB of it at the factory. Issue #32 gives no sectors: its layout is this one with userdata extended to
# the last usable LBA (tests/fakegpt.py's vendor64).
[int64]$D64 = 122159104; [int64]$U64 = 122155007; [int64]$F64 = 12275712
# the free region: the factory gap, and the table that ends at the end of the disk
$r = FreeRegion 54218752 $D64
Check 'region factory: start'   $r.Start 54222848
Check 'region factory: end'     $r.End 122150912
# userdata up to the last usable sector: the two boundaries cross by 4 MiB, the "-4 MiB" of the issue ...
Check 'region #32: the boundaries cross'  (([math]::Floor(($D64 - 34) / 4096 - 1) * 4096 - [math]::Floor(($U64 + 1) / 4096 + 1) * 4096) * 512 / 1MB) (-4)
# ... and that is an empty region at the end of the disk, not a negative one
$r = FreeRegion ($U64 + 1) $D64
Check 'region #32: start'       $r.Start $D64
Check 'region #32: size'        ($r.End - $r.Start) 0
$r = FreeRegion 33550336 33554432                       # issue #65: 2 MiB behind the last partition of a 16 GiB eMMC
Check 'region #65: size'        ($r.End - $r.Start) 0
$r = FreeRegion 113766400 $D64                          # #32 after 4 GiB were made: 4 MiB of them go to the alignment
Check 'region after the shrink' (($r.End - $r.Start) * 512 / 1MB) 4092

# what the tool says about the table
$g = GptShow @('disk            122159104 sectors (58.25 GiB)', 'LAST_NAME=userdata', "LAST_FIRST=$F64", "LAST_END=$U64`r", "USABLE_END=$U64")
Check 'show: first'             $g.First $F64
Check 'show: end'               $g.End $U64
Check 'show: usable end'        $g.UsableEnd $U64
Check 'show: one missing'       ($null -eq (GptShow @("LAST_FIRST=$F64", "LAST_END=$U64"))) $true
Check 'show: nothing'           ($null -eq (GptShow @())) $true
Check 'show: not a number'      ($null -eq (GptShow @("LAST_FIRST=$F64", 'LAST_END=x', "USABLE_END=$U64"))) $true

# the split: 4 GiB stay with Android, Linux needs 800 MiB
$p = RepartitionPlan $F64 $U64 $U64
Check 'plan #32: to share'      $p.Total 109879296
Check 'plan #32: free'          $p.Free 0
Check 'plan #32: most for Linux' $p.MaxLinux 101490688
Check 'plan #32: ok'            $p.Ok $true
Check 'plan #32: missing'       $p.Missing 0
Check 'plan: Android keeps'     $p.MinAndroid 8388608
Check 'plan: Linux needs'       $p.MinLinux 1638400
Check 'plan factory: free'      (RepartitionPlan $F64 54218751 $U64).Free 67936256
$p = RepartitionPlan 113766400 $U64 $U64                # 4 GiB of userdata in all
Check 'plan small: ok'          $p.Ok $false
Check 'plan small: missing'     $p.Missing 1638400
$p = RepartitionPlan ($U64 - 8388608 - 1638400 + 1) $U64 $U64      # exactly 4 GiB + 800 MiB
Check 'plan exact: ok'          $p.Ok $true
Check 'plan exact: most'        $p.MaxLinux 1638400

# the answer to "How many GiB for Linux?"
foreach ($c in @(@('4', 'ok', 8388608), @('2.5', 'ok', 5242880), @('2.', 'ok', 4194304), @('48.39', 'ok', 101478400),
        @('0.79', 'ok', 1654784), @('0.5', 'small', 1048576), @('.5', 'small', 1048576), @('0', 'small', 0),
        @('48.4', 'large', 101498880), @('100', 'large', 209715200), @('99999999999999999999', 'large', 0),
        @('', 'nan', 0), @('abc', 'nan', 0), @('4,5', 'nan', 0), @('1.2.3', 'nan', 0), @('.', 'nan', 0),
        @('-4', 'nan', 0), @('4 GiB', 'nan', 0), @('1e3', 'nan', 0))) {
    $s = RepartitionSectors $c[0] 1638400 101490688
    Check "sectors [$($c[0])] state"    $s.State $c[1]
    Check "sectors [$($c[0])] sectors"  $s.Sectors $c[2]
}
# a point is a point where the computer writes numbers with a comma
$culture = [Threading.Thread]::CurrentThread.CurrentCulture
[Threading.Thread]::CurrentThread.CurrentCulture = [Globalization.CultureInfo]::GetCultureInfo('tr-TR')
$s = RepartitionSectors '2.5' 1638400 101490688
[Threading.Thread]::CurrentThread.CurrentCulture = $culture
Check 'sectors 2.5 in Turkish'  $s.Sectors 5242880

# the numbers of a refusal
$f = @(LayoutFigures $D64 ($U64 + 1) 0 $null $null)
Check 'figures: two lines'      $f.Count 2
Check 'figures: the eMMC'       ($f[0] -match '^  eMMC: 58[.,]\d GiB \(122159104 sectors\); the partitions end at sector 122155008; free after them: 0 MiB$') $true
Check 'figures: missing'        $f[1] '  needed behind the partitions: 700 MiB; missing: 700 MiB'
Check 'figures: part missing'   (@(LayoutFigures $D64 ($U64 + 1) (100MB + 1) $null $null)[1]) '  needed behind the partitions: 700 MiB; missing: 600 MiB'
$i = @{ First = [int64]113766400; End = $U64; UsableEnd = $U64 }
$f = @(LayoutFigures $D64 ($U64 + 1) 0 $i (RepartitionPlan $i.First $i.End $i.UsableEnd))
Check 'figures: four lines'     $f.Count 4
Check 'figures: userdata'       ($f[2] -match '^  userdata now:         4[.,]0 GiB \(sectors 113766400\.\.122155007\)$') $true
Check 'figures: the split'      $f[3] '  to share by shrinking it: 4096 MiB up to sector 122155007; needed: 4896 MiB (4096 MiB Android + 800 MiB Linux); missing: 800 MiB'

# where there is no room, room is offered - the refusal with "a different layout" is what OfferRepartition says of a
# table it does not know
$site = [IO.File]::ReadAllText((Join-Path $Top 'install.ps1'))
$iGate = $site.IndexOf('if ($SD_MODE -eq 0 -and $SIZE -lt 700MB) {')
$iOffer = $site.IndexOf("    OfferRepartition `$disk `$lastEnd `$SIZE")
Check 'no room: room is offered' ($iGate -ge 0 -and $iOffer -gt $iGate -and $iOffer - $iGate -lt 900) $true
Check 'no room: then it ends'   ($site.Substring($iOffer, 200) -match '^[^\n]*\n    Progress ''done'' ''result'' ''ok''\r?\n    exit 0\r?\n\}') $true
Check 'ERASE is the word itself' ($site -match "wipe Android''s data'\) '' 'MU300_ANS_ERASE_ANDROID'\) -cne 'ERASE'\) \{ Die ") $true

# The whole path against a device that is three stubs: SuDoToFile reads the two pieces of the partition table, adb
# pushes files and reboots, SuDo runs what the installer sends as root - and counts everything else as a mistake.
# The table comes from tests/fakegpt.py and the real tools/resize-last-partition.py edits it, so Python is needed.
$py = $null
foreach ($n in 'python', 'python3', 'py') {
    foreach ($c in @(Get-Command $n -CommandType Application -ErrorAction SilentlyContinue)) {
        $exe = $(if ($c.Source) { $c.Source } else { $c.Path })
        $ok = $false
        try { & $exe -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>$null | Out-Null; $ok = ($LASTEXITCODE -eq 0) } catch { }
        if ($ok -and -not $py) { $py = $exe }
    }
}
if (-not $py) {
    if ($env:GITHUB_ACTIONS) { Check 'python for the make-room tests' 'none' 'found' }
    else { Write-Host 'no Python 3 here: the make-room path was not run against the fake device' }
} else {
    function Sha256Hex([byte[]]$b) {
        $h = [Security.Cryptography.SHA256]::Create()
        try { return (-join ($h.ComputeHash($b) | ForEach-Object { $_.ToString('x2') })) } finally { $h.Dispose() }
    }
    $fx = Join-Path ([IO.Path]::GetTempPath()) ('mu300-gpt-' + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $fx | Out-Null
    $T = '/data/local/tmp'
    function FakeDevice([string]$Layout, [switch]$SameWork) {
        & $py (Join-Path (Join-Path $Top 'tests') 'fakegpt.py') (Join-Path $fx 'dev') $Layout
        $script:dev = @{ disk = $D64; head = [IO.File]::ReadAllBytes((Join-Path $fx 'dev.head')); tail = [IO.File]::ReadAllBytes((Join-Path $fx 'dev.tail'))
            files = @{}; cmds = (New-Object System.Collections.ArrayList); bad = 0; rebooted = $false; wiped = $false
            shortPush = $false; deafDisk = $false; noRead = $false; marker = @{}; bcb = $null
            facts = "NODE=mmcblk0p18`nCID=15010053454d36344720c1a6b7d2e900`nSTART=$F64`nSIZE=$($U64 - $F64 + 1)`nSTATE=encrypted`nTYPE=file`nSOURCE=/dev/block/dm-6`nMAGIC=`nEXT=" }
        $script:dev.head0 = Sha256Hex $script:dev.head; $script:dev.tail0 = Sha256Hex $script:dev.tail
        if (-not $SameWork) { $script:Work = Join-Path $fx ('work-' + [guid]::NewGuid()) }
    }
    function Python { & $py @args }
    function AdbProp($name) { if ($name -eq 'ro.serialno') { return '324950664950' } else { $script:dev.bad++; return '' } }
    $ANDROID_SLOT = 'a'
    $BackupDir = Join-Path $fx 'backups'
    # the bytes of a partition of the fake device, the same every time
    function PartBytes([string]$Name) { return [Text.Encoding]::ASCII.GetBytes(("$Name " * 300)) }
    function SuDoToFile($cmd, $path) {
        [void]$script:dev.cmds.Add("pull: $cmd")
        if ($script:dev.noRead) { return }
        if ($cmd -ceq 'dd if=/dev/block/mmcblk0 bs=512 count=34 2>/dev/null') { [IO.File]::WriteAllBytes($path, $script:dev.head) }
        elseif ($cmd -ceq "dd if=/dev/block/mmcblk0 bs=512 skip=$($script:dev.disk - 33) count=33 2>/dev/null") { [IO.File]::WriteAllBytes($path, $script:dev.tail) }
        elseif ($cmd -ceq 'dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null') { [IO.File]::WriteAllBytes($path, (New-Object byte[] 4096)) }
        elseif ($cmd -cmatch '^cat /dev/block/by-name/((boot|uboot|sml|teecfg|trustos|vbmeta)_[ab])$') { [IO.File]::WriteAllBytes($path, (PartBytes $Matches[1])) }
        else { $script:dev.bad++ }
    }
    function adb {
        [void]$script:dev.cmds.Add("adb: $($args -join ' ')")
        if ($args[0] -eq 'push' -and $args.Count -eq 3) {
            [byte[]]$b = [IO.File]::ReadAllBytes([string]$args[1])
            if ($script:dev.shortPush) { $b = [byte[]]($b[0..511]) }
            $script:dev.files[[string]$args[2]] = $b
        } elseif ($args[0] -eq 'reboot' -and $args.Count -eq 1) { $script:dev.rebooted = $true }
        else { $script:dev.bad++ }
    }
    $tmpFiles = '/data/local/tmp/mu300-new.head /data/local/tmp/mu300-new.tail /data/local/tmp/mu300-marker.bin /data/local/tmp/mu300-bcb.bin'
    function SuDo($cmd) {
        [void]$script:dev.cmds.Add("su: $cmd")
        $seek = $script:dev.disk - 33
        if ($cmd -ceq $script:FactsCmd) { return $script:dev.facts }
        if ($cmd -cmatch '^sha256sum (/data/local/tmp/mu300-(new\.head|new\.tail|marker\.bin|bcb\.bin))$') {
            $f = $Matches[1]
            if ($script:dev.files.ContainsKey($f)) { return "$(Sha256Hex $script:dev.files[$f])  $f`r" } else { return '' }
        }
        if ($cmd -cmatch '^sha256sum /dev/block/by-name/((boot|uboot|sml|teecfg|trustos|vbmeta)_[ab])$') { return "$(Sha256Hex (PartBytes $Matches[1]))  /dev/block/by-name/$($Matches[1])`r" }
        if ($cmd -ceq 'dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null | od -An -tx1 -v') { return ' 5f 61 00 00 42 43 41 42 01 02 00 00 9f 00 1e 00 00 00 00 00 00 00 00 00 00 00 00 00 0b e1 71 46' }
        if ($cmd -cmatch '^dd if=/dev/block/mmcblk0 bs=512 skip=(\d+) count=8 2>/dev/null \| sha256sum$') {
            $b = if ($script:dev.marker.ContainsKey($Matches[1])) { $script:dev.marker[$Matches[1]] } else { New-Object byte[] 4096 }
            return "$(Sha256Hex $b)  -"
        }
        if ($cmd -ceq 'dd if=/dev/block/by-name/misc bs=2048 count=1 2>/dev/null | sha256sum') {
            $b = if ($script:dev.bcb) { $script:dev.bcb } else { New-Object byte[] 2048 }
            return "$(Sha256Hex $b)  -"
        }
        if ($cmd -ceq "dd if=/data/local/tmp/mu300-new.tail of=/dev/block/mmcblk0 bs=512 seek=$seek conv=fsync 2>/dev/null") {
            if (-not $script:dev.deafDisk) { $script:dev.tail = $script:dev.files['/data/local/tmp/mu300-new.tail'] }
        } elseif ($cmd -ceq 'dd if=/data/local/tmp/mu300-new.head of=/dev/block/mmcblk0 bs=512 conv=fsync 2>/dev/null') {
            if (-not $script:dev.deafDisk) { $script:dev.head = $script:dev.files['/data/local/tmp/mu300-new.head'] }
        } elseif ($cmd -cmatch '^dd if=/data/local/tmp/mu300-marker\.bin of=/dev/block/mmcblk0 bs=512 seek=(\d+) count=8 conv=notrunc,fsync 2>/dev/null$') {
            $script:dev.marker[$Matches[1]] = $script:dev.files['/data/local/tmp/mu300-marker.bin']
        } elseif ($cmd -ceq 'dd if=/data/local/tmp/mu300-bcb.bin of=/dev/block/by-name/misc bs=2048 count=1 conv=notrunc,fsync 2>/dev/null') {
            $script:dev.bcb = $script:dev.files['/data/local/tmp/mu300-bcb.bin']
        } elseif ($cmd -cmatch '^dd if=/dev/zero of=') { $script:dev.wiped = $true }
        elseif ($cmd -cne "sync; rm -f $tmpFiles" -and $cmd -cne "rm -f $tmpFiles") { $script:dev.bad++ }
        return ''
    }
    function Die($m) { throw "DIE: $m" }
    function Say($m) { [void]$script:out.Add("==> $m") }
    function Ask($question, $default) {
        [void]$script:asked.Add([string]$question)
        if ($script:answers.Count -eq 0) { throw "a question nobody answers: $question" }
        $a = [string]$script:answers.Dequeue()
        if ([string]::IsNullOrWhiteSpace($a)) { return $default } else { return (NormalizeAnswer $a.Trim()) }
    }
    # what the installer prints is kept, not shown; a failed check still is
    function Write-Host {
        if ("$($args[0])" -like 'FAIL*') { Microsoft.PowerShell.Utility\Write-Host @args } else { [void]$script:out.Add("$($args[0])") }
    }
    function RunOffer([string[]]$Answers) {
        $script:answers = New-Object 'System.Collections.Generic.Queue[string]'
        foreach ($a in @($Answers)) { $script:answers.Enqueue($a) }
        $script:out = New-Object System.Collections.ArrayList
        $script:asked = New-Object System.Collections.ArrayList
        $script:died = ''
        try { OfferRepartition $script:dev.disk ($U64 + 1) 0 } catch { $script:died = "$($_.Exception.Message)" }
    }
    function Writes { @($script:dev.cmds | Where-Object { $_ -match ' of=' }) }
    function Untouched { ((Sha256Hex $script:dev.head) -eq $script:dev.head0) -and ((Sha256Hex $script:dev.tail) -eq $script:dev.tail0) -and -not $script:dev.wiped -and -not $script:dev.rebooted -and (Writes).Count -eq 0 -and $null -eq $script:dev.bcb -and $script:dev.marker.Count -eq 0 }
    function Printed([string]$Pattern) { [bool](@($script:out | Where-Object { $_ -match $Pattern }).Count) }
    $tailDd = "su: dd if=/data/local/tmp/mu300-new.tail of=/dev/block/mmcblk0 bs=512 seek=$($D64 - 33) conv=fsync 2>/dev/null"
    $headDd = 'su: dd if=/data/local/tmp/mu300-new.head of=/dev/block/mmcblk0 bs=512 conv=fsync 2>/dev/null'
    # userdata ends where the region behind it is the 4 GiB asked for (FINDINGS 112); its first 4 KiB hold the token
    [int64]$newEnd4 = [math]::Floor(($D64 - 34) / 4096 - 1) * 4096 - 8388608 - 4096 - 1
    $rgStart = (FreeRegion ($newEnd4 + 1) $D64).Start
    $markerDd = "su: dd if=/data/local/tmp/mu300-marker.bin of=/dev/block/mmcblk0 bs=512 seek=$rgStart count=8 conv=notrunc,fsync 2>/dev/null"
    $bcbDd = 'su: dd if=/data/local/tmp/mu300-bcb.bin of=/dev/block/by-name/misc bs=2048 count=1 conv=notrunc,fsync 2>/dev/null'
    $bcbFile = Join-Path $fx 'bcb.bin'
    & $py (Join-Path (Join-Path $Top 'tools') 'resume.py') bcb $bcbFile

    # -Check: the figures, no question, nothing written
    $Check = $true
    FakeDevice 'vendor64'; RunOffer @()
    $Check = $false
    Check 'check: no error'         $script:died ''
    Check 'check: userdata'         (Printed '^  userdata now:         52[.,]4 GiB \(sectors 12275712\.\.122155007\)$') $true
    Check 'check: free behind it'   (Printed '^  free behind it:       0[.,]0 GiB$') $true
    Check 'check: to share'         (Printed '^  to share:             52[.,]4 GiB$') $true
    Check 'check: erases, it says'  (Printed 'ERASES everything in Android') $true
    Check 'check: says so'          ($script:out[$script:out.Count - 1]) '  -Check writes nothing. Run the installer without it to make room.'
    Check 'check: no question'      $script:asked.Count 0
    Check 'check: device untouched' (Untouched) $true
    Check 'check: reads the table and what identifies the device' (($script:dev.cmds -join '|')) "pull: dd if=/dev/block/mmcblk0 bs=512 count=34 2>/dev/null|pull: dd if=/dev/block/mmcblk0 bs=512 skip=$($D64 - 33) count=33 2>/dev/null|su: $FactsCmd"
    Check 'check: the reset, it says' (Printed "Android's own recovery erases Android's data") $true
    Check 'check: encrypted, it says' (Printed "^  Android's data:       encrypted \(encrypted/file\)$") $true
    Check 'check: nothing unexpected' $script:dev.bad 0

    # the default answer is no
    FakeDevice 'vendor64'; RunOffer @('')
    Check 'no: nothing changed'     $script:died 'DIE: nothing was changed.'
    Check 'no: one question'        ($script:asked -join '|') 'Make room by shrinking userdata? (yes/no)'
    Check 'no: device untouched'    (Untouched) $true
    Check 'no: nothing pushed'      (@($script:dev.cmds | Where-Object { $_ -like 'adb:*' }).Count) 0

    # yes, a size, and then anything but ERASE (erase, yes, nothing at all)
    foreach ($word in 'erase', 'yes', '', 'ERASE!') {
        FakeDevice 'vendor64'; RunOffer @('yes', '4', $word)
        Check "not ERASE [$word]: nothing changed"   $script:died 'DIE: nothing was changed.'
        Check "not ERASE [$word]: device untouched"  (Untouched) $true
        Check "not ERASE [$word]: nothing pushed"    (@($script:dev.cmds | Where-Object { $_ -like 'adb:*' }).Count) 0
        Check "not ERASE [$word]: no new table made" (Test-Path (Join-Path (Join-Path $script:Work 'gpt') 'new.head')) $false
    }

    # yes, sizes that are refused, 4 GiB, ERASE, reboot
    FakeDevice 'vendor64'; RunOffer @('evet', 'abc', '0.5', '100', '', 'ERASE', '')
    $gd = Join-Path $script:Work 'gpt'
    Check 'shrink: no error'        $script:died ''
    Check 'shrink: not a number'    (Printed '^  give a number, like 4 or 2\.5$') $true
    Check 'shrink: too small'       (Printed '^  too small: Linux needs at least 0[.,]8 GiB$') $true
    Check 'shrink: too large'       (Printed '^  too large: Android would be left with less than 4[.,]0 GiB$') $true
    Check 'shrink: what Android keeps' (Printed '^  userdata \(Android\):   48[.,]4 GiB$') $true
    Check 'shrink: what Linux gets' (Printed '^  Linux region:         4[.,]0 GiB$') $true
    Check 'shrink: questions'       ($script:asked -join '|') ('Make room by shrinking userdata? (yes/no)|' + ('How many GiB for Linux? (the rest stays with Android)|' * 4) + "Type ERASE to rewrite the partition table and wipe Android's data|Reboot now? (yes/no)")
    # the backup copy of the table first, then the primary, then the token, then the request to recovery - and nothing
    # else is written
    Check 'shrink: what is written' ((Writes) -join '|') "$tailDd|$headDd|$markerDd|$bcbDd"
    Check 'shrink: nothing unexpected' $script:dev.bad 0
    Check 'shrink: the table was kept'  ((Sha256Hex ([IO.File]::ReadAllBytes("$gd\gpt.head"))) + (Sha256Hex ([IO.File]::ReadAllBytes("$gd\gpt.tail")))) ($script:dev.head0 + $script:dev.tail0)
    Check 'shrink: the device has the new one' ((Sha256Hex $script:dev.head) + (Sha256Hex $script:dev.tail)) ((Sha256Hex ([IO.File]::ReadAllBytes("$gd\new.head"))) + (Sha256Hex ([IO.File]::ReadAllBytes("$gd\new.tail"))))
    Check 'shrink: it differs'      ((Sha256Hex $script:dev.head) -ne $script:dev.head0 -and (Sha256Hex $script:dev.tail) -ne $script:dev.tail0) $true
    $now = GptShow @(GptTool $gd @('after.head,after.tail', '--disk-sectors', "$D64", '--name', 'userdata', '--show'))
    Check 'shrink: userdata starts where it did' $now.First $F64
    Check 'shrink: userdata ends before a 4 GiB region'  $now.End $newEnd4
    Check 'shrink: the region is the size asked for' ((FreeRegion ($newEnd4 + 1) $D64).End - $rgStart) 8388608
    Check 'shrink: says where'      (Printed '^  userdata now ends at sector 113758207$') $true
    Check 'shrink: userdata not cleared from here' $script:dev.wiped $false
    Check 'shrink: the request to recovery' (Sha256Hex $script:dev.bcb) (Sha256Hex ([IO.File]::ReadAllBytes($bcbFile)))
    $tx = @(Get-ChildItem -LiteralPath $BackupDir -Recurse -Filter 'transaction.json')
    Check 'shrink: one transaction'  $tx.Count 1
    $txj = [IO.File]::ReadAllText($tx[0].FullName) | ConvertFrom-Json
    Check 'shrink: the transaction' "$($txj.state) $($txj.serial) $($txj.slot) $($txj.cid) $($txj.region_start) $($txj.target)" "prepared 324950664950 a 15010053454d36344720c1a6b7d2e900 $rgStart $($newEnd4 - $F64 + 1)"
    $mk = Join-Path $fx 'marker.bin'
    & $py (Join-Path (Join-Path $Top 'tools') 'resume.py') marker $txj.token $mk
    Check 'shrink: the token'       (Sha256Hex $script:dev.marker["$rgStart"]) (Sha256Hex ([IO.File]::ReadAllBytes($mk)))
    $bk = Split-Path -Parent $tx[0].FullName
    Check 'shrink: the backup'      (@(Get-Content (Join-Path $bk 'SHA256SUMS')).Count) 15
    Check 'shrink: the backup of boot_b' (Sha256Hex ([IO.File]::ReadAllBytes((Join-Path $bk 'boot_b.img')))) (Sha256Hex (PartBytes 'boot_b'))
    Check 'shrink: reboots'         $script:dev.rebooted $true
    Check 'shrink: the last thing'  ($script:dev.cmds[$script:dev.cmds.Count - 1]) 'adb: reboot'
    # no partition but userdata is named, and nothing but the partition table is read
    Check 'shrink: no other partition' (@($script:dev.cmds | Where-Object { $_ -match 'by-name/(?!(misc|userdata|boot_[ab]|uboot_[ab]|sml_[ab]|teecfg_[ab]|trustos_[ab]|vbmeta_[ab])\b)|nvram|fixnv|runtimenv|prodnv|persist|miscdata' }).Count) 0
    Check 'shrink: reads'           (@($script:dev.cmds | Where-Object { $_ -like 'pull:*' } | Sort-Object -Unique).Count) 15
    Remove-Item -Recurse -Force $BackupDir

    # "no" to the reboot
    FakeDevice 'vendor64'; RunOffer @('yes', '2.5', 'ERASE', 'no')
    Check 'no reboot: no error'     $script:died ''
    Check 'no reboot: written'      ((Writes) -join '|') ("$tailDd|$headDd|$markerDd|$bcbDd" -replace "seek=$rgStart", "seek=$((FreeRegion ($newEnd4 + 8388608 - 5242880 + 1) $D64).Start)")
    Check 'no reboot: stays up'     $script:dev.rebooted $false

    # a push that stops halfway: what is on the device is not what was made, and nothing is written
    FakeDevice 'vendor64'; $script:dev.shortPush = $true; RunOffer @('yes', '4', 'ERASE')
    Check 'short push: refused'     $script:died 'DIE: the new partition table did not arrive on the device as it was made; nothing is changed'
    Check 'short push: device untouched' (Untouched) $true
    Check 'short push: cleaned up'  ($script:dev.cmds[$script:dev.cmds.Count - 1]) "su: rm -f $tmpFiles"

    # a device whose table does not change: said, and userdata is not cleared
    FakeDevice 'vendor64'; $script:dev.deafDisk = $true; RunOffer @('yes', '4', 'ERASE')
    Check 'did not take: said'      $script:died 'DIE: the table did not take (userdata still ends at 122155007). Nothing else was changed.'
    Check 'did not take: no reset asked for' ($script:dev.wiped -or $script:dev.rebooted -or $null -ne $script:dev.bcb -or $script:dev.marker.Count -gt 0) $false

    # an encryption state that cannot be told: kano's rule, nothing is changed
    FakeDevice 'vendor64'; $script:dev.facts = $script:dev.facts -replace 'STATE=encrypted', 'STATE=' -replace 'TYPE=file', 'TYPE=' -replace 'SOURCE=/dev/block/dm-6', 'SOURCE=/dev/block/mmcblk0p18' -replace 'MAGIC=', 'MAGIC=1020f5f2'
    RunOffer @('yes', '4', 'ERASE')
    Check 'unknown crypto: refused' $script:died "DIE: Android's encryption state is unknown (?/?); nothing is changed."
    Check 'unknown crypto: no question' $script:asked.Count 0
    Check 'unknown crypto: device untouched' (Untouched) $true
    # the table and the running kernel disagree: reboot first
    FakeDevice 'vendor64'; $script:dev.facts = $script:dev.facts -replace "SIZE=\d+", 'SIZE=4096'
    RunOffer @('yes', '4', 'ERASE')
    Check 'kernel disagrees: refused' $script:died 'DIE: the partition table and the running kernel disagree about userdata; reboot Android and try again'
    Check 'kernel disagrees: device untouched' (Untouched) $true

    # a partition behind userdata: not a table to make room in. The tool's reason and the figures, no question
    FakeDevice 'nvlast64'; RunOffer @('yes', '4', 'ERASE')
    $lines = @($script:died -split "`n")
    Check 'other table: refused'    $lines[0] 'DIE: only 0 MiB of free space after the last partition: this device has a different layout, nothing is changed.'
    Check 'other table: why'        ($script:died -match "the last partition is 'l_fixnv2_a', not 'userdata': refusing") $true
    Check 'other table: the eMMC'   ($script:died -match '\n  eMMC: 58[.,]\d GiB \(122159104 sectors\); the partitions end at sector 122155008; free after them: 0 MiB\n') $true
    Check 'other table: missing'    ($script:died -match '\n  needed behind the partitions: 700 MiB; missing: 700 MiB\n') $true
    Check 'other table: report'     ($lines[$lines.Count - 1]) 'Please report the numbers above (eMMC size and where the partitions end); they identify the variant.'
    Check 'other table: no question' $script:asked.Count 0
    Check 'other table: device untouched' (Untouched) $true

    # a table that does not check (one bit of the backup entries): refused the same way
    FakeDevice 'vendor64'; $script:dev.tail[40] = $script:dev.tail[40] -bxor 1; $script:dev.tail0 = Sha256Hex $script:dev.tail
    RunOffer @('yes', '4', 'ERASE')
    Check 'bad CRC: refused'        ($script:died -match 'a different layout, nothing is changed\.\n  backup entry array CRC does not match; refusing to touch this table\n') $true
    Check 'bad CRC: device untouched' (Untouched) $true

    # userdata of 4 GiB in all: nothing to split, with what is missing
    FakeDevice 'small64'; RunOffer @('yes', '4', 'ERASE')
    Check 'small: refused'          (($script:died -split "`n")[0] -match '^DIE: there is not enough room to split: 4[.,]0 GiB in total,$') $true
    Check 'small: Android'          (($script:died -split "`n")[1] -match '^and Android needs at least 4[.,]0 GiB of it\. Nothing is changed\.$') $true
    Check 'small: userdata'         ($script:died -match '\(sectors 113766400\.\.122155007\)\n') $true
    Check 'small: missing'          ($script:died -match 'needed: 4896 MiB \(4096 MiB Android \+ 800 MiB Linux\); missing: 800 MiB$') $true
    Check 'small: no question'      $script:asked.Count 0
    Check 'small: device untouched' (Untouched) $true

    # a device that gives nothing, in a work directory that still holds the table of an earlier run: that one is
    # never taken for the device's
    FakeDevice 'vendor64'; RunOffer @('no')
    FakeDevice 'vendor64' -SameWork; $script:dev.noRead = $true; RunOffer @('yes', '4', 'ERASE')
    Check 'no table: refused'       (($script:died -split "`n")[0]) 'DIE: could not read the partition table'
    Check 'no table: the eMMC'      ($script:died -match 'the partitions end at sector 122155008; free after them: 0 MiB\n') $true
    Check 'no table: no question'   $script:asked.Count 0
    Check 'no table: device untouched' (Untouched) $true

    Remove-Item function:Write-Host
    Remove-Item -Recurse -Force $fx
}

# ---- the release and the beta question (tools/releases.sh's release_resolve, here ReleasePick and BetaAsked) -------
$bnames = 'ReleasePick', 'BetaAsked', 'TagKey', 'TagNewer'
$bdefs = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $bnames -contains $n.Name }, $true)
foreach ($d in $bdefs) { . ([scriptblock]::Create($d.Extent.Text)) }
foreach ($w in $bnames) {
    if (-not (Get-Command $w -CommandType Function -ErrorAction SilentlyContinue)) { Write-Host "FAIL: $w not found in install.ps1"; exit 99 }
}
# the release tags (docs/FINDINGS.md 125): the table every language is held to
foreach ($line in [IO.File]::ReadAllLines((Join-Path (Join-Path (Join-Path $Top 'tests') 'fixtures') 'release-tags.tsv'))) {
    if (-not $line -or $line.StartsWith('#')) { continue }
    $f = $line.Split("`t")
    switch ($f[0]) {
        'ok'  { Check "tag ok [$($f[1])]" ($null -ne (TagKey $f[1])) $true }
        'bad' { Check "tag bad [$($f[1])]" ($null -eq (TagKey $f[1])) $true }
        'cmp' {
            $a = $f[1]; $b = $f[3]
            $got = if (TagNewer $a $b) { if (TagNewer $b $a) { 'both' } else { '>' } } elseif (TagNewer $b $a) { '<' } elseif ($null -ne (TagKey $a) -and $null -ne (TagKey $b)) { '=' } else { '!' }
            Check "tag $a $($f[2]) $b" $got $f[2]
        }
    }
}
Check 'tag: a newline after it'  ($null -eq (TagKey "v2026.10.16.1`n")) $true
Check 'tag: other digits'        ($null -eq (TagKey "v2026.10.16.$([char]0x0661)")) $true
$site0 = [IO.File]::ReadAllText((Join-Path $Top 'install.ps1'))
Check 'slot a: by TagNewer'      ($site0 -match "\(TagNewer 'v2026\.10\.10' \`$Release\)") $true
Check 'beta asked'               (BetaAsked 'v2026.10.23' 'v2026.10.25' $false) $true
Check 'no beta: not asked'       (BetaAsked 'v2026.10.23' '' $false) $false
Check 'no release: not asked'    (BetaAsked '' 'v2026.10.25' $false) $false
Check 'the same tag: not asked'  (BetaAsked 'v2026.10.23' 'v2026.10.23' $false) $false
Check '-Answers: not asked'      (BetaAsked 'v2026.10.23' 'v2026.10.25' $true) $false
$site = [IO.File]::ReadAllText((Join-Path $Top 'install.ps1'))
Check 'release before the questions'  ($site.IndexOf('$rl = ReleaseList') -gt 0 -and $site.IndexOf('$rl = ReleaseList') -lt $site.IndexOf("Say (T 'What should be installed?')")) $true
Check 'the beta defaults to no'  ($site -match "\(Ask \(T 'Install the beta \{1\} instead of \{2\}\? \(yes/no\)' \`$rl\.Beta \`$Release\) 'no' 'MU300_ANS_BETA'\) -eq 'yes'") $true
Check 'a given release is not looked up'  ($site -match '(?s)if \(-not \$Release\) \{\s+if \(\$ReleaseUrl\) \{ \$Release = ''custom'' \}\s+else \{\s+\$rl = ReleaseList') $true
if (-not $py) {
    if ($env:GITHUB_ACTIONS) { Check 'python for the release tests' 'none' 'found' }
    else { Write-Host 'no Python 3 here: the release decision was not run against the fake API answers' }
} else {
    # fake answers of GitHub's API, decided by the real tools/release_pick.py
    function Python { & $py @args }
    $rx = Join-Path ([IO.Path]::GetTempPath()) ('mu300-rel-' + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $rx | Out-Null
    function Rel($tag, $when, $pre, $draft) { '{"tag_name": "' + $tag + '", "published_at": "' + $when + '", "prerelease": ' + $pre + ', "draft": ' + $draft + ', "assets": []}' }
    $full = Rel 'v2026.10.23' '2026-10-12T17:55:05Z' 'false' 'false'
    $oldBeta = Rel 'v2026.10.19' '2026-10-11T16:18:01Z' 'true' 'false'
    $newBeta = Rel 'v2026.10.25' '2026-10-14T08:00:00Z' 'true' 'false'
    $draft = Rel 'v2026.11.01' '2026-11-01T00:00:00Z' 'true' 'true'
    $evil = Rel 'v1; calc' '2026-10-20T00:00:00Z' 'true' 'false'
    $cases = @(
        @('no prerelease', "[$full]", 'v2026.10.23', ''),
        @('an overtaken prerelease', "[$full, $oldBeta]", 'v2026.10.23', ''),
        @('a newer prerelease', "[$oldBeta, $newBeta, $full]", 'v2026.10.23', 'v2026.10.25'),
        @('a draft', "[$draft, $full]", 'v2026.10.23', ''),
        @('only a prerelease', "[$newBeta]", '', ''),
        @('a tag that is not plain', "[$full, $evil]", 'v2026.10.23', ''),
        @('the rate limit', '{"message": "API rate limit exceeded for 203.0.113.7."}', '', ''),
        @('a page of a proxy', '<html>blocked</html>', '', ''),
        @('nothing', '', '', ''))
    foreach ($c in $cases) {
        $jf = Join-Path $rx 'releases.json'
        [IO.File]::WriteAllText($jf, $c[1])
        $r = ReleasePick $jf
        Check "release: $($c[0]): latest" $r.Latest $c[2]
        Check "release: $($c[0]): beta"   $r.Beta $c[3]
    }
    $r = ReleasePick (Join-Path $rx 'no-such-file.json')
    Check 'release: no file: latest'  $r.Latest ''
    Check 'release: no file: beta'    $r.Beta ''
    Remove-Item -Recurse -Force $rx
}

# ---- answers mode and the progress stream (tools\answers.ps1, dot-sourced near the top) -----------------------------
# the functions of the installer that go with it, and stubs for what they call
$anames = 'Ask', 'NextAnswer'
$adefs = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $anames -contains $n.Name }, $true)
foreach ($d in $adefs) { . ([scriptblock]::Create($d.Extent.Text)) }
LoadLanguage 'en'
$script:lines = New-Object System.Collections.ArrayList
# (a failed check still shows: Check prints through Write-Host too)
function Write-Host { if ("$($args[0])" -like 'FAIL *') { [Console]::Out.WriteLine([string]$args[0]) } else { [void]$script:lines.Add([string]$args[0]) } }
function Die($m) { Progress 'error' 'message' $m; throw "DIE: $m" }
function AnswersFail([string]$Message) { throw "FAIL: $Message" }
function ResetAnswers {
    $script:MuAnswers.Clear(); $script:MuAsked.Clear(); $script:lines.Clear()
    $script:MuAnswersMode = $true; $script:MuProgress = $true; $script:AnswerQueue = $null
}
function Events([string]$Name) { @($script:lines | Where-Object { "$_".StartsWith('@@mu300 {"event":"' + $Name + '"') }) }
function Dies([scriptblock]$Block) { try { & $Block | Out-Null; return '' } catch { return [string]$_.Exception.Message } }

Check 'json: plain'            (JsonEsc 'Kernel 6.18') 'Kernel 6.18'
Check 'json: quote, backslash' (JsonEsc 'a"b\c') 'a\"b\\c'
Check 'json: line end, tab'    (JsonEsc "a`r`nb`tc") 'a  b c'
Check 'json: control'          (JsonEsc ("a" + [char]1 + [char]27 + "b")) 'ab'
Check 'json: not ASCII'        (JsonEsc ('T' + [char]0xFC + 'rk' + [char]0x4E2D)) 'T\u00fcrk\u4e2d'
Check 'event line'             (ProgressLine 'info' 'key' 'region.size' 'value' 34828075008) '@@mu300 {"event":"info","key":"region.size","value":"34828075008"}'
Check 'event line: no values'  (ProgressLine 'hello') '@@mu300 {"event":"hello"}'

# the file: NAME=value lines, the value as it is
$pr = AnswersParse @('# a comment', '', 'MU300_ANS_KERNEL=6.18', "MU300_ANS_PASSWORD=p=ss w'rd `$x`r", 'ANDROID_SERIAL=324950664950', 'MU300_ANS_GPU=')
Check 'file: ok'               $pr.Ok $true
Check 'file: value'            $pr.Values['MU300_ANS_KERNEL'] '6.18'
Check 'file: value as it is'   $pr.Values['MU300_ANS_PASSWORD'] "p=ss w'rd `$x"
Check 'file: serial'           $pr.Values['ANDROID_SERIAL'] '324950664950'
Check 'file: empty value'      $pr.Values['MU300_ANS_GPU'] ''
Check 'file: not ours'         (AnswersParse @('PATH=C:\evil')).Error 'answers file: not a variable of the installer: PATH'
Check 'file: lower case'       (AnswersParse @('mu300_ans_kernel=1')).Ok $false
Check 'file: no ='             (AnswersParse @('MU300_ANS_GPU=yes', 'hunter2secret')).Error 'answers file: line 2 is not NAME=value'
Check 'file: no name'          (AnswersParse @('=x')).Error 'answers file: not a variable of the installer: '
# a secret is never shown, whichever way it is asked for, and never part of an event
ResetAnswers; $script:MuAnswers['MU300_ANS_PASSWORD'] = 'hunter2secret'
Check 'secret through Ask: taken'  (Ask 'Password' '' 'MU300_ANS_PASSWORD') 'hunter2secret'
Check 'secret through Ask: not shown' (@($script:lines | Where-Object { "$_" -match 'hunter2' }).Count) 0
Check 'secret through Ask: event' @(Events 'question')[0] '@@mu300 {"event":"question","id":"MU300_ANS_PASSWORD","text":"Password","default":"","secret":"1"}'
Check 'secret asked again: not shown' ((Dies { Ask 'Password' '' 'MU300_ANS_PASSWORD' }) -match 'hunter2') $false
# given in the environment, it leaves the environment: the programs the installer starts do not inherit it
ResetAnswers; $env:MU300_ANS_PASSWORD = 'hunter2secret'; $env:MU300_NONINTERACTIVE = '1'
AnswersSetup '' $false
Check 'secret from the environment: kept'  $script:MuAnswers['MU300_ANS_PASSWORD'] 'hunter2secret'
Check 'secret from the environment: gone from it' ($null -eq [Environment]::GetEnvironmentVariable('MU300_ANS_PASSWORD')) $true
AnswersReexport
Check 'secret back for the restart'   $env:MU300_ANS_PASSWORD 'hunter2secret'
Remove-Item env:MU300_ANS_PASSWORD; Remove-Item env:MU300_NONINTERACTIVE

# a question answered by its variable, announced on the stream
ResetAnswers; $script:MuAnswers['MU300_ANS_KERNEL'] = '2'
Check 'answer: taken'          (Ask 'Kernel' '1' 'MU300_ANS_KERNEL') '2'
Check 'answer: shown'          ($script:lines -contains 'Kernel [1]: 2') $true
Check 'answer: event'          @(Events 'question')[0] '@@mu300 {"event":"question","id":"MU300_ANS_KERNEL","text":"Kernel","default":"1","answer":"2"}'
# the words of the other languages count, as they do when typed
ResetAnswers; $script:MuAnswers['MU300_ANS_VPN'] = 'evet'
Check 'answer: normalised'     (Ask 'VPN? (yes/no)' 'no' 'MU300_ANS_VPN') 'yes'
# set to nothing: the default, which is never the destructive answer
ResetAnswers; $script:MuAnswers['MU300_ANS_CONFIRM'] = ''
Check 'answer: empty is the default'  (Ask 'Type INSTALL to continue' 'no' 'MU300_ANS_CONFIRM') 'no'
# from the environment when the file has none
ResetAnswers; $env:MU300_ANS_TESTONLY = '6.18'
Check 'answer: environment'    (Ask 'Kernel' '1' 'MU300_ANS_TESTONLY') '6.18'
Remove-Item env:MU300_ANS_TESTONLY
# no answer: an error that names the variable, never the default
ResetAnswers
$e = Dies { Ask 'Type INSTALL to continue' 'no' 'MU300_ANS_CONFIRM' }
Check 'unanswered: stops'      $e 'DIE: no answer to the question "Type INSTALL to continue": set MU300_ANS_CONFIRM (nothing is asked and nothing is assumed in answers mode)'
Check 'unanswered: error event' @(Events 'error').Count 1
Check 'unanswered: no question event' @(Events 'question').Count 0
# a question without a variable cannot be answered at all
ResetAnswers
Check 'no variable: stops'     ((Dies { Ask 'Something new' 'yes' }) -match '^DIE: this question has no variable') $true
# a question that comes back was not happy with its answer: stop instead of asking for ever
ResetAnswers; $script:MuAnswers['MU300_ANS_KERNEL'] = '9'
Ask 'Kernel' '1' 'MU300_ANS_KERNEL' | Out-Null
Check 'asked again: stops'     (Dies { Ask 'Kernel' '1' 'MU300_ANS_KERNEL' }) 'DIE: the answer in MU300_ANS_KERNEL was not accepted: 9'
# the password: never in an event, never on a line
ResetAnswers; $script:MuAnswers['MU300_ANS_PASSWORD'] = 'hunter2secret'
Check 'password: taken'        (AnswerNamed 'Password' '' 'MU300_ANS_PASSWORD' -Secret) 'hunter2secret'
Check 'password: not shown'    (@($script:lines | Where-Object { "$_" -match 'hunter2' }).Count) 0
Check 'password: stars'        ($script:lines -contains 'Password []: ******') $true
# without answers mode nothing here speaks
$script:MuAnswersMode = $false; $script:MuProgress = $false; $script:lines.Clear()
Progress 'step' 'id' 'x'; Info 'a' 'b'; Step 'x' 3
Check 'no stream: silent'      $script:lines.Count 0
$script:MuProgress = $true
Step 'download' 20; ProgressSay 'Downloading release v1'
Check 'step event'             $script:lines[0] '@@mu300 {"event":"step","id":"download","percent":"20","title":"Downloading release v1"}'
ProgressSay 'A heading without a step'
Check 'step: id used once'     $script:lines[1] '@@mu300 {"event":"step","id":"","percent":"","title":"A heading without a step"}'
$script:MuProgress = $false
Remove-Item function:Write-Host
Remove-Item function:Die
Remove-Item function:AnswersFail
# every question of both installers has its variable
foreach ($f in 'install.ps1', 'uninstall.ps1') {
    $code = [IO.File]::ReadAllText((Join-Path $Top $f))
    $bare = @([regex]::Matches($code, '(?m)^.*\bAsk \(?[T''"].*$') | ForEach-Object { $_.Value } | Where-Object { $_ -notmatch "'(MU300_[A-Z_]+)'\)?" })
    Check "$f - a question without a variable: $($bare -join ' | ')" $bare.Count 0
}

# ---- the graphical installer's question and the Python it needs (tools\gui-launch.ps1; tools/gui-launch.sh's tests
# are tests/test_gui_launch.py) ------------------------------------------------------------------------------------
. (Join-Path (Join-Path $Top 'tools') 'gui-launch.ps1')
LoadLanguage 'en'
# whether it is asked at all: setting, MU300_FROM_GUI, scripted (answers of either kind, -Check, -Backup), a console
Check 'gui: a person at a console'   (GuiAsked '' '' $false $true) $true
Check 'gui: no console'              (GuiAsked '' '' $false $false) $false
Check 'gui: MU300_GUI=0'             (GuiAsked '0' '' $false $true) $false
Check 'gui: MU300_GUI=1, no console' (GuiAsked '1' '' $false $false) $true
Check 'gui: started by the window'   (GuiAsked '1' '1' $false $true) $false
Check 'gui: scripted'                (GuiAsked '' '' $true $true) $false
Check 'gui: scripted, MU300_GUI=1'   (GuiAsked '1' '' $true $true) $false
Check 'gui: default with a screen'   (GuiDefault '' '') 'yes'
Check 'gui: default over SSH'        (GuiDefault '10.0.0.2 50000 10.0.0.1 22' '') 'no'
Check 'gui: default over SSH (client)' (GuiDefault '' '10.0.0.2 50000 22') 'no'
Check 'gui: the winget command'      (GuiWingetCommand) 'winget install -e --id Python.Python.3.12 --scope user'
$site = [IO.File]::ReadAllText((Join-Path $Top 'install.ps1'))
Check 'gui: offered after the self-update, before the device' ($site.IndexOf('if (SelfUpdate) {') -lt $site.IndexOf('GuiOffer (') -and $site.IndexOf('GuiOffer (') -lt $site.IndexOf("Say (T 'Checking host tools and device')")) $true
Check 'gui: not with answers, -Check or -Backup' ($site.Contains('GuiOffer ([bool]($script:MuAnswersMode -or ($null -ne $script:AnswerQueue) -or $Check -or $Backup))')) $true

if ($env:OS -ne 'Windows_NT') {
    Write-Host 'not Windows: the search for Python was not run against the fake directory trees'
} else {
    # Fake trees with stub interpreters: python.cmd files that answer the two probes (the version, import tkinter)
    # and record whatever else they are started with. $ver '' is the Microsoft Store's alias with no Python behind
    # it: it prints nothing and fails.
    $gx = Join-Path ([IO.Path]::GetTempPath()) ('mu300-gui-' + [guid]::NewGuid())
    $lad = Join-Path $gx 'LocalAppData'; $pf = Join-Path $gx 'Program Files'; $pf86 = Join-Path $gx 'Program Files (x86)'
    New-Item -ItemType Directory -Path $lad, $pf, $pf86 | Out-Null
    $ranLog = Join-Path $gx 'ran.txt'
    function StubPython([string]$Dir, [string]$Ver, [int]$TkRc, [int]$GuiRc = 0, [string]$Leaf = 'python.cmd') {
        New-Item -ItemType Directory -Force -Path $Dir | Out-Null
        $f = Join-Path $Dir $Leaf
        if (-not $Ver) { [IO.File]::WriteAllText($f, "@echo off`r`nexit /b 9009`r`n"); return $f }
        $p = $Ver.Split('.')
        $t = "@echo off`r`necho %* | findstr /c:`"tkinter`" >nul`r`nif not errorlevel 1 exit /b $TkRc`r`n" +
            "echo %* | findstr /c:`"version_info`" >nul`r`nif errorlevel 1 goto run`r`necho $($p[0])`r`necho $($p[1])`r`nexit /b 0`r`n" +
            ":run`r`necho ran %*>>`"$ranLog`"`r`nexit /b $GuiRc`r`n"
        [IO.File]::WriteAllText($f, $t)
        return $f
    }
    function Cands { return @(GuiPyCandidates $lad $pf $pf86 'python.cmd' -NoPath) }

    Check 'find: an empty machine'       (Cands).Count 0
    $r = GuiPyFind (Cands)
    Check 'find: nothing'                "$($null -eq $r.Python) $($null -eq $r.NoTk) $($null -eq $r.Old)" 'True True True'

    # python.org / winget for this user, newest first: 3.13 before 3.9 (by the number, not by the text)
    $p39 = StubPython (Join-Path $lad 'Programs\Python\Python39') '3.9' 0
    $p313 = StubPython (Join-Path $lad 'Programs\Python\Python313') '3.13' 0
    $c = Cands
    Check 'find: the user installs, newest first' (($c | ForEach-Object { $_.Exe }) -join '|') "$p313|$p39"
    $r = GuiPyFind $c
    Check 'find: the newest with tkinter'  "$($r.Python.Exe) $($r.Python.Version)" "$p313 3.13"
    $pr = GuiPyProbe @{ Exe = $p313; Pre = @() }
    Check 'probe: runs, version, tkinter'  "$($pr.Runs) $($pr.Version) $($pr.Old) $($pr.Tk)" 'True 3.13 False True'

    # the newest was set up without tcl/tk: the next one is taken
    StubPython (Join-Path $lad 'Programs\Python\Python313') '3.13' 1 | Out-Null
    $r = GuiPyFind (Cands)
    Check 'find: past one without tkinter' "$($r.Python.Exe) $($r.Python.Version)" "$p39 3.9"
    $pr = GuiPyProbe @{ Exe = $p313; Pre = @() }
    Check 'probe: no tkinter'              "$($pr.Runs) $($pr.Version) $($pr.Tk)" 'True 3.13 False'
    # none has it: the one without is named, so the message can say which is missing
    StubPython (Join-Path $lad 'Programs\Python\Python39') '3.9' 1 | Out-Null
    $r = GuiPyFind (Cands)
    Check 'find: only without tkinter'     "$($null -eq $r.Python) $($r.NoTk.Exe) $($r.NoTk.Version)" "True $p313 3.13"
    Remove-Item -Recurse -Force (Join-Path $lad 'Programs')

    # for all users, 64 and 32 bit; the user's own comes first
    $pAll = StubPython (Join-Path $pf 'Python312') '3.12' 0
    $p86 = StubPython (Join-Path $pf86 'Python311-32') '3.11' 0
    New-Item -ItemType Directory -Force -Path (Join-Path $pf 'PythonSoftwareFoundation'), (Join-Path $pf 'Python27') | Out-Null
    Check 'find: for all users'           ((Cands | ForEach-Object { $_.Exe }) -join '|') "$pAll|$p86"
    $pUser = StubPython (Join-Path $lad 'Programs\Python\Python310') '3.10' 0
    Check 'find: the user install first'  ((Cands | ForEach-Object { $_.Exe }) -join '|') "$pUser|$pAll|$p86"
    Remove-Item -Recurse -Force (Join-Path $lad 'Programs'), (Join-Path $pf 'Python312'), (Join-Path $pf86 'Python311-32')

    # too old, and what is no Python at all
    $pOld = StubPython (Join-Path $pf 'Python37') '3.7' 0
    $r = GuiPyFind (Cands)
    Check 'find: too old is named'        "$($null -eq $r.Python) $($r.Old.Exe) $($r.Old.Version)" "True $pOld 3.7"
    $pr = GuiPyProbe @{ Exe = $pOld; Pre = @() }
    Check 'probe: too old'                "$($pr.Runs) $($pr.Old) $($pr.Tk)" 'True True False'
    Remove-Item -Recurse -Force (Join-Path $pf 'Python37')
    # the Microsoft Store's alias with no Python behind it: found as a file, never taken for a Python
    $store = StubPython (Join-Path $lad 'Microsoft\WindowsApps') '' 0
    Check 'find: the Store alias is a candidate' ((Cands | ForEach-Object { $_.Exe }) -join '|') $store
    $pr = GuiPyProbe @{ Exe = $store; Pre = @() }
    Check 'probe: the Store stub does not run' "$($pr.Runs) [$($pr.Version)]" 'False []'
    $r = GuiPyFind (Cands)
    Check 'find: the Store stub is no Python' "$($null -eq $r.Python) $($null -eq $r.NoTk) $($null -eq $r.Old)" 'True True True'
    # ... and the Store's real Python, in the same place, is one
    StubPython (Join-Path $lad 'Microsoft\WindowsApps') '3.12' 0 | Out-Null
    Check 'find: the Store Python'        (GuiPyFind (Cands)).Python.Version '3.12'
    $pr = GuiPyProbe @{ Exe = (Join-Path $gx 'no-such-python.cmd'); Pre = @() }
    Check 'probe: a file that is not there' $pr.Runs $false
    # the launcher's -3 goes in front of the other arguments
    $py3 = StubPython (Join-Path $gx 'launcher') '3.11' 0 0 'py.cmd'
    $pr = GuiPyProbe @{ Exe = $py3; Pre = @('-3') }
    Check 'probe: the launcher'           "$($pr.Runs) $($pr.Version) $($pr.Tk)" 'True 3.11 True'
    Remove-Item -Recurse -Force (Join-Path $lad 'Microsoft')

    # a python.exe named by hand: the file, its folder, with quotes; nothing for what is not there
    $hand = StubPython (Join-Path $gx 'by hand') '3.12' 0 0 'python.exe.cmd'
    Check 'by hand: the file'             (GuiPyByHand $hand).Exe $hand
    Check 'by hand: quoted'               (GuiPyByHand "  `"$hand`" ").Exe $hand
    Check 'by hand: not there'            ($null -eq (GuiPyByHand (Join-Path $gx 'nope\python.exe'))) $true
    Check 'by hand: nothing'              ($null -eq (GuiPyByHand '   ')) $true
    [IO.File]::WriteAllText((Join-Path (Join-Path $gx 'by hand') 'python.exe'), '')
    Check 'by hand: its folder'           (GuiPyByHand (Join-Path $gx 'by hand')).Exe (Join-Path (Join-Path $gx 'by hand') 'python.exe')

    # the whole offer, with the search and the console replaced: what is asked, what is run, and that every way
    # that is not a started window comes back for the text installer
    $script:glines = New-Object System.Collections.ArrayList
    $script:gasked = New-Object System.Collections.ArrayList
    $script:ganswers = New-Object 'System.Collections.Generic.Queue[string]'
    function Write-Host { if ("$($args[0])" -like 'FAIL *') { [Console]::Out.WriteLine([string]$args[0]) } else { [void]$script:glines.Add([string]$args[0]) } }
    function Say($m) { [void]$script:glines.Add("==> $m") }
    function Ask($question, $default, $Id) {
        [void]$script:gasked.Add("$Id=$default")
        if ($script:ganswers.Count -eq 0) { return $default }
        $a = [string]$script:ganswers.Dequeue()
        if ([string]::IsNullOrWhiteSpace($a)) { return $default } else { return (NormalizeAnswer $a.Trim()) }
    }
    function GuiConsole { return $true }
    $script:gfound = @{ Python = $null; NoTk = $null; Old = $null }
    function GuiPySearch { return $script:gfound }
    function Offer([string[]]$Answers) {
        $script:glines.Clear(); $script:gasked.Clear(); $script:ganswers.Clear()
        foreach ($a in @($Answers)) { $script:ganswers.Enqueue($a) }
        Remove-Item $ranLog -ErrorAction SilentlyContinue
        GuiOffer $false
        return 'the text installer goes on'
    }
    $realTop = $Top; $Top = Join-Path $gx 'top'; $Lang = 'tr'
    New-Item -ItemType Directory -Force -Path (Join-Path $Top 'gui') | Out-Null
    [IO.File]::WriteAllText((Join-Path (Join-Path $Top 'gui') 'mu300-gui.py'), '# stands for the window')
    $saveGui = $env:MU300_GUI; $saveFrom = $env:MU300_FROM_GUI; $saveLang = $env:MU300_LANG
    $env:MU300_GUI = ''; $env:MU300_FROM_GUI = ''

    Check 'offer: no'                     (Offer @('no')) 'the text installer goes on'
    Check 'offer: no - one question'      ($script:gasked -join ' ') 'MU300_GUI=yes'
    Check 'offer: no - nothing said'      $script:glines.Count 0
    GuiOffer $true
    Check 'offer: scripted - not asked'   $script:gasked.Count 1

    # no Python: the default of the next question is the text installer
    Check 'offer: no Python'              (Offer @('yes', '')) 'the text installer goes on'
    Check 'offer: no Python - asked'      ($script:gasked -join ' ') 'MU300_GUI=yes MU300_GUI_PYTHON=no'
    Check 'offer: no Python - said'       ($script:glines -join '|') '  No Python 3 was found for the window.|  Continuing with the text installer.'
    # a path that is no Python
    Check 'offer: a wrong path'           (Offer @('yes', (Join-Path $gx 'nope\python.exe'))) 'the text installer goes on'
    Check 'offer: a wrong path - said'    ($script:glines -contains '  That is not a Python 3.8 or newer with tkinter.') $true
    # tkinter missing, too old: the message says which
    $script:gfound = @{ Python = $null; NoTk = @{ Exe = 'C:\Py\python.exe'; Pre = @(); Version = '3.12' }; Old = $null }
    Offer @('yes', 'no') | Out-Null
    Check 'offer: no tkinter - said'      $script:glines[0] '  Python 3.12 is there (C:\Py\python.exe), but without tkinter, which the window is made with.'
    $script:gfound = @{ Python = $null; NoTk = $null; Old = @{ Exe = 'C:\Py\python.exe'; Pre = @(); Version = '3.6' } }
    Offer @('yes', 'no') | Out-Null
    Check 'offer: too old - said'         $script:glines[0] '  Python 3.6 is there (C:\Py\python.exe), but the window needs Python 3.8 or newer.'

    # winget: the command is shown first and runs only with a yes; a stub winget.cmd first on PATH stands for it
    $wg = Join-Path $gx 'winget'; New-Item -ItemType Directory -Path $wg | Out-Null
    $wgLog = Join-Path $gx 'winget.txt'
    [IO.File]::WriteAllText((Join-Path $wg 'winget.cmd'), "@echo off`r`necho winget %*>>`"$wgLog`"`r`nexit /b 0`r`n")
    $savePath = $env:PATH; $env:PATH = "$wg;$env:PATH"
    $script:gfound = @{ Python = $null; NoTk = $null; Old = $null }
    Check 'offer: winget, then no'        (Offer @('yes', 'winget', '')) 'the text installer goes on'
    Check 'offer: winget - asked'         ($script:gasked -join ' ') 'MU300_GUI=yes MU300_GUI_PYTHON=no MU300_GUI_INSTALL_PYTHON=no'
    Check 'offer: winget - shown'         ($script:glines -contains '    winget install -e --id Python.Python.3.12 --scope user') $true
    Check 'offer: winget - not run'       (Test-Path $wgLog) $false
    Offer @('yes', 'winget', 'yes') | Out-Null
    Check 'offer: winget - run as shown'  ((Get-Content $wgLog) -join '|').Trim() 'winget install -e --id Python.Python.3.12 --scope user'
    $env:PATH = $savePath

    # a Python that can: the window is started with it, in the language chosen; one that ends with an error comes back
    $guiPy = StubPython (Join-Path $gx 'gui-python') '3.12' 0 3
    $script:gfound = @{ Python = @{ Exe = $guiPy; Pre = @(); Version = '3.12' }; NoTk = $null; Old = $null }
    Check 'offer: a window that fails'    (Offer @('yes')) 'the text installer goes on'
    Check 'offer: started'                $script:glines[0] '==> Starting the graphical installer (Python 3.12)'
    Check 'offer: with the window file'   (((Get-Content $ranLog) -join '').Trim()) "ran $(Join-Path (Join-Path $Top 'gui') 'mu300-gui.py')"
    Check 'offer: in the language chosen' $env:MU300_LANG 'tr'
    Check 'offer: says why it came back'  $script:glines[1] '  The graphical installer could not start or stopped with an error (3).'
    Check 'offer: one question'           ($script:gasked -join ' ') 'MU300_GUI=yes'
    # MU300_GUI=1: not asked, started
    $env:MU300_GUI = '1'
    Offer @() | Out-Null
    Check 'offer: MU300_GUI=1 asks nothing' $script:gasked.Count 0
    Check 'offer: MU300_GUI=1 starts it'  (Test-Path $ranLog) $true
    $env:MU300_GUI = '0'
    Offer @() | Out-Null
    Check 'offer: MU300_GUI=0 does nothing' "$($script:gasked.Count) $($script:glines.Count) $(Test-Path $ranLog)" '0 0 False'

    $env:MU300_GUI = $saveGui; $env:MU300_FROM_GUI = $saveFrom; $env:MU300_LANG = $saveLang
    $Top = $realTop
    Remove-Item function:Write-Host
    Remove-Item -Recurse -Force $gx -ErrorAction SilentlyContinue
}

# ---- a package: an unpacked Magisk zip (tools/package.sh is the same for install.sh) ------------------------------------
$px = Join-Path ([IO.Path]::GetTempPath()) ('mu300-package-' + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path (Join-Path $px 'computer'), (Join-Path $px 'mu300'), (Join-Path $px 'payload') | Out-Null
Check 'package: none'           (PackageDir '' (Join-Path $px 'computer')) ''
[IO.File]::WriteAllText((Join-Path $px 'computer\.mu300-package'), "x`n")
Check 'package: by the mark'    (PackageDir '' (Join-Path $px 'computer')) $px
Check 'package: named'          (PackageDir 'D:\other' (Join-Path $px 'computer')) 'D:\other'
[IO.File]::WriteAllText((Join-Path $px 'payload\mu300-kernel-6.18.tar.gz'), 'kernel bundle')
[IO.File]::WriteAllText((Join-Path $px 'payload\mu300-openwrt-rootfs.tar.gz'), 'openwrt system')
$hk = (Get-FileHash (Join-Path $px 'payload\mu300-kernel-6.18.tar.gz') -Algorithm SHA256).Hash.ToLower()
$hr = (Get-FileHash (Join-Path $px 'payload\mu300-openwrt-rootfs.tar.gz') -Algorithm SHA256).Hash.ToLower()
function PkgManifest([hashtable]$Over) {
    $m = [ordered]@{ TAG = 'v2026.10.27'; SYSTEM = 'openwrt'; OS = 'openwrt'; UBUNTU = ''; KERNEL = '6.18'; KERNEL_ASSET = 'mu300-kernel-6.18.tar.gz'
        ROOTFS_ASSET = 'mu300-openwrt-rootfs.tar.gz'; SHA256_KERNEL = $hk; SHA256_ROOTFS = $hr }
    if ($Over) { foreach ($k in $Over.Keys) { $m[$k] = $Over[$k] } }
    [IO.File]::WriteAllText((Join-Path $px 'mu300\manifest'), (($m.Keys | ForEach-Object { "$_=$($m[$_])" }) -join "`r`n") + "`r`n")
    return (PackageManifest $px)
}
$pm = PkgManifest
Check 'package: read'           "$($pm.TAG) $($pm.SYSTEM) $($pm.OS) [$($pm.UBUNTU)] $($pm.KERNEL) $($pm.KERNEL_ASSET) $($pm.ROOTFS_ASSET)" 'v2026.10.27 openwrt openwrt [] 6.18 mu300-kernel-6.18.tar.gz mu300-openwrt-rootfs.tar.gz'
foreach ($bad in @(@{ TAG = '2026.10.27' }, @{ TAG = 'v1..2' }, @{ TAG = 'v1;x' }, @{ SYSTEM = 'ubuntu' }, @{ ROOTFS_ASSET = '..\..\x' },
                   @{ KERNEL = '6.19' }, @{ KERNEL_ASSET = 'mu300-kernel.tar.gz' }, @{ SHA256_KERNEL = ('X' * 64) }, @{ SHA256_ROOTFS = 'ab' },
                   @{ SYSTEM = 'ubuntu-26.04'; OS = 'ubuntu'; UBUNTU = '26.04'; ROOTFS_ASSET = 'mu300-ubuntu-26.04-rootfs.tar.gz'; KERNEL = '5.4'; KERNEL_ASSET = 'mu300-kernel.tar.gz' })) {
    Check "package: refused $(@($bad.Keys) -join ',')=$(@($bad.Values) -join ',')" ($null -eq (PkgManifest $bad)) $true
}
# a key in another case is not one of the manifest's (the installer on the device reads TAG, not tag)
[IO.File]::WriteAllText((Join-Path $px 'mu300\manifest'), "tag=v2026.10.27`n")
Check 'package: keys are exact' ($null -eq (PackageManifest $px)) $true
$pm = PkgManifest
[IO.File]::WriteAllText((Join-Path $px 'mu300\SHA256SUMS'), "$hk  mu300-kernel-6.18.tar.gz`n$hr *mu300-openwrt-rootfs.tar.gz`n")
Check 'package: both match'     "[$(PackageCheck $px 'mu300-kernel-6.18.tar.gz' $hk)][$(PackageCheck $px 'mu300-openwrt-rootfs.tar.gz' $hr)]" '[][]'
Check 'package: not the manifest''s' (PackageCheck $px 'mu300-openwrt-rootfs.tar.gz' ('0' * 64)) 'mismatch'
[IO.File]::WriteAllText((Join-Path $px 'mu300\SHA256SUMS'), "$hk  mu300-kernel-6.18.tar.gz`n$('0' * 64)  mu300-openwrt-rootfs.tar.gz`n")
Check 'package: not SHA256SUMS''s' (PackageCheck $px 'mu300-openwrt-rootfs.tar.gz' $hr) 'mismatch'
[IO.File]::WriteAllText((Join-Path $px 'mu300\SHA256SUMS'), "$hk  mu300-kernel-6.18.tar.gz`n")
Check 'package: not in SHA256SUMS' (PackageCheck $px 'mu300-openwrt-rootfs.tar.gz' $hr) 'mismatch'
Remove-Item (Join-Path $px 'payload\mu300-kernel-6.18.tar.gz')
Check 'package: missing'        (PackageCheck $px 'mu300-kernel-6.18.tar.gz' $hk) 'missing'
Remove-Item -Recurse -Force $px -ErrorAction SilentlyContinue

Write-Host "$script:passed passed, $script:failed failed"
exit $script:failed
