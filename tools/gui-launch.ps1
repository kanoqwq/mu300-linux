# The graphical installer (gui\): the question that offers it, and the Python that can run it. Dot-sourced by
# install.ps1 (T, Ask, Say and $Top are its own; tools/gui-launch.sh is the same for install.sh). The window drives
# install.ps1 in answers mode (tools\answers.ps1), so nothing is installed differently from there.
#   $env:MU300_GUI = '0'   never ask: the text installer
#   $env:MU300_GUI = '1'   do not ask: the graphical installer (the text installer when it cannot start)
# Asked once, and only of a person: a console at both ends, no answers of either kind, not with -Check, -Backup or
# -AdbPath, and not when the window itself runs the script (MU300_FROM_GUI). Nothing is installed here without a yes
# to a question that shows the command first, and every failure and every "no" leads to the text installer, which
# goes on as if nothing had been asked.
# This file stays ASCII: Windows PowerShell 5.1 reads a script without a BOM as ANSI.

# GuiAsked: whether the question is put at all. $Console: both ends are a console; $Scripted: answers mode, -Answers,
# or one of the modes that install nothing.
function GuiAsked([string]$Setting, [string]$FromGui, [bool]$Scripted, [bool]$Console) {
    if ($Setting -eq '0') { return $false }
    if ($FromGui) { return $false }
    if ($Scripted) { return $false }
    if ($Setting -eq '1') { return $true }
    return $Console
}
# GuiDefault: yes with a screen for a window, no in an SSH session
function GuiDefault([string]$SshConnection, [string]$SshClient) {
    if ($SshConnection -or $SshClient) { return 'no' } else { return 'yes' }
}
function GuiConsole {
    try { return ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected) }
    catch { return $false }
}

# GuiPyCandidates: every place a Python 3 may be, the likeliest first, each once: @{ Exe; Pre } (Pre: arguments in
# front of the others, -3 for the py launcher).
#   on PATH: python, python3, the py launcher (py -3), python3.14 .. python3.8
#   %LOCALAPPDATA%\Programs\Python\Python3*\python.exe   python.org and winget, for this user (newest first)
#   %ProgramFiles%\Python3*\python.exe, and the (x86) one    the same, for all users
#   %LOCALAPPDATA%\Microsoft\WindowsApps\python.exe      the Microsoft Store's Python - or only its alias, a stub
#                                                         that prints nothing and exits with an error: GuiPyProbe
#                                                         runs each candidate, so the stub is never taken for one
# A Python that winget installed a moment ago is not on this process's PATH yet: the directories find it.
# $Leaf and $NoPath are for tests\: another file name for the interpreters of a fake tree, and no look at PATH.
function GuiPyCandidates([string]$LocalAppData, [string]$ProgramFiles, [string]$ProgramFilesX86, [string]$Leaf = 'python.exe', [switch]$NoPath) {
    $list = New-Object System.Collections.ArrayList
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $add = {
        param($exe, $pre)
        if ($exe -and $seen.Add("$exe|$($pre -join ' ')")) { [void]$list.Add(@{ Exe = [string]$exe; Pre = @($pre) }) }
    }
    if (-not $NoPath) {
        foreach ($n in 'python', 'python3') {
            foreach ($c in @(Get-Command $n -CommandType Application -ErrorAction SilentlyContinue)) {
                # the Store's python.exe has an empty .Source: fall back to .Path
                & $add $(if ($c.Source) { $c.Source } else { $c.Path }) @()
            }
        }
        foreach ($c in @(Get-Command py -CommandType Application -ErrorAction SilentlyContinue)) {
            & $add $(if ($c.Source) { $c.Source } else { $c.Path }) @('-3')
        }
        foreach ($v in 14, 13, 12, 11, 10, 9, 8) {
            foreach ($c in @(Get-Command "python3.$v" -CommandType Application -ErrorAction SilentlyContinue)) {
                & $add $(if ($c.Source) { $c.Source } else { $c.Path }) @()
            }
        }
    }
    foreach ($root in @($(if ($LocalAppData) { Join-Path $LocalAppData 'Programs\Python' }), $ProgramFiles, $ProgramFilesX86)) {
        if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
        # Python313 before Python39: by the number, not by the text
        $dirs = @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^Python3(\d+)' } |
            Sort-Object -Property @{ Expression = { [int](($_.Name -replace '^Python3(\d+).*$', '$1')) } } -Descending)
        foreach ($d in $dirs) {
            $exe = Join-Path $d.FullName $Leaf
            if (Test-Path -LiteralPath $exe) { & $add $exe @() }
        }
    }
    if ($LocalAppData) {
        foreach ($n in $Leaf, ($Leaf -replace '^python', 'python3')) {
            $exe = Join-Path (Join-Path $LocalAppData 'Microsoft\WindowsApps') $n
            if (Test-Path -LiteralPath $exe) { & $add $exe @() }
        }
    }
    return @($list)
}

# GuiPyProbe CANDIDATE: what this Python is - @{ Runs; Version; Old; Tk }. Version is "3.12", '' when it does not
# run or is no Python (the Store's stub); Old: older than 3.8; Tk: it can import a tkinter with Tk 8.6 or newer (the
# window is not drawn right by an older one). The two are asked separately, so the messages can say which is missing.
function GuiPyProbe($Candidate) {
    $ErrorActionPreference = 'Continue'
    $r = @{ Runs = $false; Version = ''; Old = $false; Tk = $false }
    $exe = $Candidate.Exe; $pre = @($Candidate.Pre)
    $out = @()
    try { $out = @(& $exe @pre -c 'import sys; print(sys.version_info[0]); print(sys.version_info[1])' 2>$null) } catch { return $r }
    if ($LASTEXITCODE -ne 0) { return $r }
    $nums = @($out | ForEach-Object { "$_".Trim() } | Where-Object { $_ -match '^\d+$' })
    if ($nums.Count -ne 2) { return $r }
    $r.Runs = $true
    $r.Version = "$($nums[0]).$($nums[1])"
    if ([int]$nums[0] -ne 3 -or [int]$nums[1] -lt 8) { $r.Old = $true; return $r }
    try { & $exe @pre -c 'import sys, tkinter; sys.exit(0 if tkinter.TkVersion >= 8.6 else 4)' 2>$null | Out-Null; $r.Tk = ($LASTEXITCODE -eq 0) } catch { $r.Tk = $false }
    return $r
}

# GuiPyFind CANDIDATES: @{ Python; NoTk; Old } - Python is the first candidate that is new enough and has tkinter
# ($null: none), with its Version; NoTk the first that is new enough without tkinter; Old the first that is too old.
function GuiPyFind($Candidates) {
    $res = @{ Python = $null; NoTk = $null; Old = $null }
    foreach ($c in @($Candidates)) {
        $p = GuiPyProbe $c
        if (-not $p.Runs) { continue }
        $found = @{ Exe = $c.Exe; Pre = @($c.Pre); Version = $p.Version }
        if ($p.Old) { if (-not $res.Old) { $res.Old = $found }; continue }
        if ($p.Tk) { $res.Python = $found; return $res }
        if (-not $res.NoTk) { $res.NoTk = $found }
    }
    return $res
}
function GuiPySearch {
    return (GuiPyFind (GuiPyCandidates $env:LOCALAPPDATA $env:ProgramFiles ${env:ProgramFiles(x86)}))
}

# The Python installation that is offered: 3.12 from winget, for this user (no administrator rights), which
# includes tkinter. The command as it is shown and as it is run.
$script:GuiWingetArgs = @('install', '-e', '--id', 'Python.Python.3.12', '--scope', 'user')
function GuiWingetCommand { return ('winget ' + ($script:GuiWingetArgs -join ' ')) }

# GuiPyByHand PATH: the candidate for a python.exe the user names (quotes around it, or its folder, are fine)
function GuiPyByHand([string]$Path) {
    $p = $Path.Trim().Trim('"').Trim("'")
    if (-not $p) { return $null }
    if (Test-Path -LiteralPath $p -PathType Container) { $p = Join-Path $p 'python.exe' }
    if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { return $null }
    return @{ Exe = $p; Pre = @() }
}

# GuiOffer: the question, the search and the start. Returns when the text installer should go on (a "no", no
# Python, a window that could not start); ends the script when the graphical installer ran and ended by itself.
function GuiOffer([bool]$Scripted) {
    if (-not (GuiAsked ([string]$env:MU300_GUI) ([string]$env:MU300_FROM_GUI) $Scripted (GuiConsole))) { return }
    if ($env:MU300_GUI -ne '1') {
        if ((Ask (T 'Use the graphical installer (a window with the same steps)? (yes/no)') (GuiDefault $env:SSH_CONNECTION $env:SSH_CLIENT) 'MU300_GUI') -ne 'yes') { return }
    }
    $found = GuiPySearch
    if (-not $found.Python) {
        if ($found.NoTk) {
            Write-Host ('  ' + (T 'Python {1} is there ({2}), but without tkinter, which the window is made with.' $found.NoTk.Version $found.NoTk.Exe))
            Write-Host ('  ' + (T 'The installers of python.org and winget include it: this one was set up without "tcl/tk and IDLE".'))
        } elseif ($found.Old) {
            Write-Host ('  ' + (T 'Python {1} is there ({2}), but the window needs Python 3.8 or newer.' $found.Old.Version $found.Old.Exe))
        } else {
            Write-Host ('  ' + (T 'No Python 3 was found for the window.'))
        }
        $how = Ask (T 'Type the path of a python.exe, or winget to install Python 3.12 with winget, or no for the text installer') 'no' 'MU300_GUI_PYTHON'
        if ($how -eq 'winget') {
            if (-not (Get-Command winget -CommandType Application -ErrorAction SilentlyContinue)) {
                Write-Host ('  ' + (T 'winget is not on this computer. Python is at https://www.python.org/downloads/ (leave "tcl/tk and IDLE" ticked).'))
            } else {
                Write-Host ('  ' + (T 'This command installs it:'))
                Write-Host ('    ' + (GuiWingetCommand))
                if ((Ask (T 'Run that command now? (yes/no)') 'no' 'MU300_GUI_INSTALL_PYTHON') -eq 'yes') {
                    $wa = $script:GuiWingetArgs
                    try { & winget @wa } catch { }
                    if ($LASTEXITCODE -ne 0) { Write-Host ('  ' + (T 'the command failed')) }
                    # the new interpreter is not on this process's PATH: the installation directories find it
                    $found = GuiPySearch
                }
            }
        } elseif ($how -ne 'no') {
            $hand = GuiPyByHand $how
            if ($hand) { $found = GuiPyFind @($hand) }
            if (-not $hand -or -not $found.Python) { Write-Host ('  ' + (T 'That is not a Python 3.8 or newer with tkinter.')) }
        }
    }
    $gui = Join-Path (Join-Path $Top 'gui') 'mu300-gui.py'
    if (-not $found.Python -or -not (Test-Path -LiteralPath $gui)) {
        Write-Host ('  ' + (T 'Continuing with the text installer.'))
        return
    }
    Say (T 'Starting the graphical installer (Python {1})' $found.Python.Version)
    $exe = $found.Python.Exe; $pre = @($found.Python.Pre)
    $env:MU300_LANG = $Lang
    $code = 1
    try { & $exe @pre $gui; $code = $LASTEXITCODE } catch { $code = 1 }
    if ($code -eq 0) { exit 0 }
    Write-Host ('  ' + (T 'The graphical installer could not start or stopped with an error ({1}).' $code))
    Write-Host ('  ' + (T 'Continuing with the text installer.'))
}
