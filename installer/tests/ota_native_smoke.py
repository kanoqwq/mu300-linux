"""Check the shipped Windows app's real accessibility tree and sustained liveness.

Uses Windows UI Automation without a mocked bridge, debug build or open debugging
port. No device access or writes. The updater must load its exported manifest.
"""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def window_handle(pid, title):
    user = ctypes.windll.user32
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

    @callback_type
    def visit(window, _):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(window, ctypes.byref(owner))
        text = ctypes.create_unicode_buffer(256)
        user.GetWindowTextW(window, text, len(text))
        if owner.value == pid and text.value == title:
            found.append(window)
        return True

    user.EnumWindows(visit, 0)
    assert len(found) <= 1, f'Multiple native windows titled {title}'
    return found[0] if found else None


def window_text(window):
    # Only a numeric, locally obtained HWND enters this fixed PowerShell query.
    script = f"""
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{int(window)})
$nodes=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
ConvertTo-Json -Compress -InputObject @($nodes | ForEach-Object {{$_.Current.Name}})
"""
    result = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-Command', script],
                            encoding='utf-8', errors='replace', capture_output=True,
                            timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def check_launch(executable, page_name='diff.html', seconds=30):
    executable = Path(executable).resolve()
    title = 'OpenWrt Diff Studio' if page_name == 'diff.html' else 'OpenWrt Patch Updater'
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 4  # Render the real interactive window without stealing focus.
    with tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as output:
        process = subprocess.Popen([str(executable)], cwd=executable.parent,
                                   env=dict(os.environ, RUST_BACKTRACE='1'),
                                   startupinfo=startup, stderr=errors, stdout=output)
        window = None
        try:
            deadline = time.monotonic() + 30
            while True:
                assert process.poll() is None, f'Exited at startup: {process.returncode}'
                window = window_handle(process.pid, title)
                if window:
                    names = window_text(window)
                    if page_name == 'diff.html':
                        ready = names.count('选择 ZIP 文件') == 2 or names.count('Select ZIP file') == 2
                    else:
                        ready = any('mu300-r2' in text for text in names)
                    if ready:
                        break
                if time.monotonic() > deadline:
                    raise AssertionError(f'Expected page never rendered: {title}; accessibility={names if window else []}')
                time.sleep(.5)
            print(f'Loaded {title}: real frontend' + (' and backend manifest' if page_name == 'ota.html' else ''), flush=True)
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                assert process.poll() is None, f'Window crashed: {process.returncode}'
                time.sleep(.5)
            assert title in window_text(window), 'Rendered frontend disappeared'
            user = ctypes.windll.user32
            user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            assert user.PostMessageW(window, 0x0010, 0, 0), 'WM_CLOSE failed'
            process.wait(timeout=10)
            assert process.returncode == 0, f'Abnormal close: {process.returncode}'
            print(f'PASS {executable.name}: alive {seconds}s, clean close', flush=True)
        finally:
            if process.poll() is None:
                if window:
                    ctypes.windll.user32.PostMessageW(window, 0x0010, 0, 0)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                else:
                    process.terminate()
                process.wait(timeout=10)
            errors.seek(0)
            stderr = errors.read().decode('utf-8', 'replace')
            assert 'panicked' not in stderr, stderr


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('--page', default='diff.html', choices=['diff.html', 'ota.html'])
    parser.add_argument('--seconds', type=int, default=30)
    args = parser.parse_args()
    check_launch(args.executable, args.page, args.seconds)
