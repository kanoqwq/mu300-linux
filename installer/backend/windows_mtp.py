"""Windows-only workaround for the MU300 Android MTP/ADB gadget conflict.

Only a present MTP interface below the selected USB serial is disabled. The
composite parent, ADB, network and serial interfaces are never disable targets.
No Android USB configuration is changed, and disabled MTP is not re-enabled.
"""
import base64
import json
import os
import re
import subprocess
import sys
import threading
import time

WPD = '{EEC5AD98-8080-425F-922A-DABF3DE3F69A}'
_lock = threading.Lock()
_inventory = (0, [])
_announced = set()
_failed = set()

# Avoid localized friendly names and global VID/PID disable operations.
INVENTORY = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$rows=@(Get-PnpDevice -PresentOnly | Where-Object {$_.InstanceId -like 'USB\VID_*'} | ForEach-Object {
  $p=@{}
  Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName 'DEVPKEY_Device_Parent','DEVPKEY_Device_ClassGuid','DEVPKEY_Device_CompatibleIds','DEVPKEY_Device_ProblemCode' -ErrorAction SilentlyContinue | ForEach-Object {$p[$_.KeyName]=$_.Data}
  @{id=$_.InstanceId;parent=$p['DEVPKEY_Device_Parent'];class_guid=[string]$p['DEVPKEY_Device_ClassGuid'];compatible=@($p['DEVPKEY_Device_CompatibleIds']);problem=$p['DEVPKEY_Device_ProblemCode']}
})
ConvertTo-Json -InputObject $rows -Depth 4 -Compress
"""


def powershell(script, timeout=45):
    executable = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                              'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    result = subprocess.run([executable, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, timeout=timeout,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('Windows MTP device operation failed')
    return result.stdout.decode('utf-8-sig', 'replace').strip()


def matching_mtp(rows, serial):
    """Select only WPD/MTP USB interfaces of one unambiguous composite parent."""
    roots = [r['id'] for r in rows if isinstance(r.get('id'), str) and
             re.fullmatch(r'USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}\\[^\\]+', r['id'], re.I) and
             r['id'].rsplit('\\', 1)[-1].casefold() == serial.casefold()]
    if len(roots) != 1:
        return []
    root = roots[0].casefold()
    return [r for r in rows if isinstance(r.get('id'), str) and
            re.fullmatch(r'USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}&MI_[0-9A-F]{2}\\[^\\]+', r['id'], re.I) and
            str(r.get('parent', '')).casefold() == root and
            (str(r.get('class_guid', '')).strip('{}').upper() == WPD.strip('{}') or
             any('MS_COMP_MTP' in str(v).upper() for v in r.get('compatible', [])))]


def disable_script(serial, instance):
    """Revalidate ancestry and class inside the elevated process, before writing."""
    values = base64.b64encode(json.dumps([serial, instance]).encode('utf-8')).decode('ascii')
    # Both values are data in a base64 JSON literal, never PowerShell syntax.
    child = r"""
$ErrorActionPreference='Stop'
try {
  $v=ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__VALUES__')))
  $id=[string]$v[1]; $serial=[string]$v[0]
  if ($id -notmatch '^USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}&MI_[0-9A-F]{2}\\[^\\]+$') {exit 10}
  $node=Get-PnpDevice -PresentOnly -InstanceId $id -ErrorAction Stop
  $p=@{}; Get-PnpDeviceProperty -InstanceId $id -KeyName 'DEVPKEY_Device_Parent','DEVPKEY_Device_ClassGuid','DEVPKEY_Device_CompatibleIds' | ForEach-Object {$p[$_.KeyName]=$_.Data}
  $parent=[string]$p['DEVPKEY_Device_Parent']
  if ($parent -notmatch '^USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}\\[^\\]+$' -or ($parent -split '\\')[-1] -ine $serial) {exit 11}
  if (([string]$p['DEVPKEY_Device_ClassGuid']).Trim('{}') -ine 'EEC5AD98-8080-425F-922A-DABF3DE3F69A' -and -not (@($p['DEVPKEY_Device_CompatibleIds']) -match 'MS_COMP_MTP')) {exit 12}
  Disable-PnpDevice -InstanceId $node.InstanceId -Confirm:$false -ErrorAction Stop
  exit 0
} catch {exit 13}
""".replace('__VALUES__', values)
    encoded = base64.b64encode(child.encode('utf-16le')).decode('ascii')
    return r"""
$ErrorActionPreference='Stop'
$admin=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($admin) { & "$PSHOME\powershell.exe" -NoProfile -NonInteractive -EncodedCommand '__CHILD__'; exit $LASTEXITCODE }
try {
  $p=Start-Process -FilePath "$PSHOME\powershell.exe" -ArgumentList @('-NoProfile','-NonInteractive','-EncodedCommand','__CHILD__') -Verb RunAs -WindowStyle Hidden -Wait -PassThru
  exit $p.ExitCode
} catch {exit 14}
""".replace('__CHILD__', encoded)


def prepare_devices(devices, notify=None):
    """Called after adb devices -l, before the first Android shell/transfer.

    Network serials (including mDNS) and non-MU300 models are excluded. Failures
    are surfaced as warnings, without claiming MTP was disabled or changing ADB.
    """
    global _inventory
    if sys.platform != 'win32':
        return
    targets = [d for d in devices if not d.get('wireless') and ':' not in d['serial'] and
               not d['serial'].startswith('adb-') and
               re.search(r'\b(?:MU300|F50|V50|MU3351|U30.?AIR)(?:$|[_ -])', d.get('label', '').replace('_', ' '), re.I)]
    if not targets:
        return
    def announce(code, device):
        if notify:
            notify({'code': code, 'label': device.get('label', 'MU300')})
        else:
            print(('MTP disabled for stable USB ADB / 已禁用 MTP 以稳定有线 ADB' if code == 'mtp_disabled' else
                   'Could not disable MTP; run the installer as administrator / 未能禁用 MTP，请以管理员身份运行安装器'), flush=True)
    with _lock:
        try:
            if time.monotonic() - _inventory[0] > 5:
                _inventory = (time.monotonic(), json.loads(powershell(INVENTORY)))
            for device in targets:
                for node in matching_mtp(_inventory[1], device['serial']):
                    key = node['id'].casefold()
                    if node.get('problem') != 22:
                        if key in _failed:
                            continue
                        try:
                            powershell(disable_script(device['serial'], node['id']), timeout=120)
                            fresh = json.loads(powershell(INVENTORY))
                            verified = next((r for r in matching_mtp(fresh, device['serial']) if r['id'].casefold() == key), None)
                            if not verified or verified.get('problem') != 22:
                                raise RuntimeError('MTP disable was not confirmed')
                            _inventory = (time.monotonic(), fresh)
                        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                            _failed.add(key)
                            announce('mtp_disable_failed', device)
                            continue
                    if key not in _announced:
                        _announced.add(key)
                        announce('mtp_disabled', device)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            if 'inventory' not in _failed:
                _failed.add('inventory')
                announce('mtp_disable_failed', targets[0])
