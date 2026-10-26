# The installers without a person at the console: every question answered by a variable, and a progress stream a
# program can read. Dot-sourced by install.ps1 and uninstall.ps1 (tools/answers.sh is the same for install.sh and
# uninstall.sh); T and Die are theirs. docs/INSTALLER-AUTOMATION.md has the whole interface.
#
# Answers mode - -NonInteractive, $env:MU300_NONINTERACTIVE = '1', or an answers file (-AnswersFile, $env:MU300_ANSWERS):
#   nothing is read from the console. Each question has a variable (the last argument of its Ask), taken from the
#   file or the environment. A question whose variable is not set stops the run with an error that names the
#   variable: nothing is ever defaulted silently. A variable set to nothing takes the question's default, which is
#   never the destructive answer. The value @stdin puts the question on the progress stream and reads one line from
#   standard input.
#   The file: one NAME=value per line, the value taken as it is to the end of the line, # for comments; names
#   MU300_* and ANDROID_SERIAL. It holds the password in plain text: keep it in your own profile folder (not in a
#   folder other users can read) and delete it afterwards.
# Secrets - the variables in $script:MuSecrets (the password): their values are never printed, never part of an
#   event and never in an error message, and they are not handed to the programs the installer starts: one given in
#   the environment is taken out of it at once, one from the file is never put there. The password reaches one
#   command only, on its standard input: the one that hashes it.
#   (install.ps1 -Answers is older and different: one answer per line, in the order of the questions.)
# Progress - $env:MU300_PROGRESS = '1': lines "@@mu300 {json}" on standard output, one event each, next to the text
#   for people; every value is a string, and characters outside ASCII are written as \uXXXX. The last event of a
#   run is done or error, whatever ends it.
# This file stays ASCII: Windows PowerShell 5.1 reads a script without a BOM as ANSI.
#
# The variables of this file live in the scope of the script that dot-sources it, next to that script's own - and
# PowerShell does not tell one spelling of a name from another: the dictionary of answers was once called "Ans", and
# install.ps1's own "$ans = Ask ..." replaced it with a string. Hence the Mu prefix on every one of them
# (tests/test_answers.py checks that the installers use none of these names).

$script:MuAnswersMode = $false
$script:MuAnswers = New-Object 'System.Collections.Generic.Dictionary[string,string]'
$script:MuAsked = New-Object 'System.Collections.Generic.Dictionary[string,string]'
$script:MuProgress = $false
# the answers that are secrets, in one place
$script:MuSecrets = @('MU300_ANS_PASSWORD', 'MU300_ANS_CURRENT_PASSWORD', 'MU300_ANS_CURRENT_PASSWORD_UBUNTU', 'MU300_ANS_CURRENT_PASSWORD_OPENWRT', 'MU300_ANS_CURRENT_PASSWORD_OPENWRT_LUCI')
function AnswerSecret([string]$Id) { return ($script:MuSecrets -ccontains $Id) }
$script:MuStepId = ''
$script:MuStepPct = ''

# a value inside a JSON string
function JsonEsc([string]$Text) {
    $sb = New-Object System.Text.StringBuilder
    foreach ($ch in $Text.ToCharArray()) {
        $c = [int]$ch
        if ($c -eq 34) { [void]$sb.Append('\"') }
        elseif ($c -eq 92) { [void]$sb.Append('\\') }
        elseif ($c -eq 9 -or $c -eq 10 -or $c -eq 13) { [void]$sb.Append(' ') }
        elseif ($c -lt 32 -or $c -eq 127) { }
        elseif ($c -gt 126) { [void]$sb.Append('\u' + $c.ToString('x4')) }
        else { [void]$sb.Append($ch) }
    }
    return $sb.ToString()
}
# ProgressLine NAME KEY VALUE...: the text of one event
function ProgressLine([string]$Name) {
    $j = '{"event":"' + $Name + '"'
    for ($i = 0; $i + 1 -lt $args.Count; $i += 2) { $j += ',"' + [string]$args[$i] + '":"' + (JsonEsc ([string]$args[$i + 1])) + '"' }
    return ('@@mu300 ' + $j + '}')
}
# Progress NAME KEY VALUE...: one event line; nothing without a progress stream
function Progress([string]$Name) {
    if (-not $script:MuProgress) { return }
    Write-Host (ProgressLine $Name @args)
}
function Info([string]$Key, $Value) { Progress 'info' 'key' $Key 'value' ([string]$Value) }
# Step ID PERCENT: what the next Say starts, for the stream: its id and how far the whole run is by then
function Step([string]$Id, $Percent) { $script:MuStepId = $Id; $script:MuStepPct = [string]$Percent }
function ProgressSay([string]$Title) {
    Progress 'step' 'id' $script:MuStepId 'percent' $script:MuStepPct 'title' $Title
    $script:MuStepId = ''; $script:MuStepPct = ''
}

# an error before the script's own Die exists (the options, the answers file)
function AnswersFail([string]$Message) {
    Progress 'error' 'message' $Message
    Write-Host "`nERROR: $Message" -ForegroundColor Red
    exit 2
}
# AnswersParse LINES: @{ Ok; Error; Values } of the lines of an answers file. A name that is not one of the
# installer's is an error, not a variable.
function AnswersParse([string[]]$Lines) {
    $vals = New-Object 'System.Collections.Generic.Dictionary[string,string]'
    $n = 0
    foreach ($raw in @($Lines)) {
        $n++
        $line = ([string]$raw).TrimEnd("`r")
        if (-not $line -or $line.StartsWith('#')) { continue }
        $i = $line.IndexOf('=')
        # (the line itself is not shown: it may be a secret that lost its name)
        if ($i -lt 0) { return @{ Ok = $false; Error = "answers file: line $n is not NAME=value"; Values = $vals } }
        $k = $line.Substring(0, $i)
        if ($k -cnotmatch '^(MU300_[A-Z0-9_]+|ANDROID_SERIAL)$') { return @{ Ok = $false; Error = "answers file: not a variable of the installer: $k"; Values = $vals } }
        $vals[$k] = $line.Substring($i + 1)
    }
    return @{ Ok = $true; Error = ''; Values = $vals }
}
function AnswersLoad([string]$File) {
    if (-not (Test-Path -LiteralPath $File)) { AnswersFail "cannot read the answers file $File" }
    $p = AnswersParse ([IO.File]::ReadAllLines($File, [Text.Encoding]::UTF8))
    if (-not $p.Ok) { AnswersFail $p.Error }
    foreach ($k in @($p.Values.Keys)) {
        $script:MuAnswers[$k] = $p.Values[$k]
        # for the programs this one starts too (adb reads ANDROID_SERIAL) - but never the password. (An empty value
        # cannot be put into the environment on Windows: it stays in $script:MuAnswers alone.)
        if (AnswerSecret $k) { Remove-Item "env:$k" -ErrorAction SilentlyContinue }
        elseif ($p.Values[$k] -ne '') { Set-Item -Path "env:$k" -Value $p.Values[$k] }
    }
    $script:MuAnswersMode = $true
}
# AnswersSetup FILE SWITCH: after the parameters are read
function AnswersSetup([string]$File, [bool]$On) {
    if ($env:MU300_PROGRESS -eq '1') {
        $script:MuProgress = $true
        try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
    }
    if (-not $File -and $env:MU300_ANSWERS) { $File = $env:MU300_ANSWERS }
    if ($File) { AnswersLoad $File }
    if ($On -or $env:MU300_NONINTERACTIVE -eq '1') { $script:MuAnswersMode = $true }
    # a secret given in the environment stays in this script and leaves the environment: adb, python and the other
    # programs started from here do not inherit it
    foreach ($k in $script:MuSecrets) {
        $e = [Environment]::GetEnvironmentVariable($k)
        if ($null -ne $e) {
            if (-not $script:MuAnswers.ContainsKey($k)) { $script:MuAnswers[$k] = [string]$e }
            Remove-Item "env:$k" -ErrorAction SilentlyContinue
        }
    }
}
# AnswersReexport: before the script runs itself again (after its self-update), the secrets go back into the
# environment for that one start; the new run takes them out again
function AnswersReexport {
    foreach ($k in $script:MuSecrets) { if ($script:MuAnswers.ContainsKey($k) -and $script:MuAnswers[$k] -ne '') { Set-Item -Path "env:$k" -Value $script:MuAnswers[$k] } }
}
# AnswerLookup ID: @{ Set; Value } from the answers file, else from the environment
function AnswerLookup([string]$Id) {
    if ($script:MuAnswers.ContainsKey($Id)) { return @{ Set = $true; Value = [string]$script:MuAnswers[$Id] } }
    $e = [Environment]::GetEnvironmentVariable($Id)
    if ($null -ne $e) { return @{ Set = $true; Value = [string]$e } }
    return @{ Set = $false; Value = '' }
}
# AnswerNamed QUESTION DEFAULT ID [-Secret]: the answer of a question in answers mode, as it was given
function AnswerNamed([string]$Question, [string]$Default, [string]$Id, [switch]$Secret) {
    if (AnswerSecret $Id) { $Secret = $true }
    if (-not $Id) { Die (T 'this question has no variable and cannot be answered without a terminal: {1}' $Question) }
    $a = AnswerLookup $Id
    if (-not $a.Set) { Die (T 'no answer to the question "{1}": set {2} (nothing is asked and nothing is assumed in answers mode)' $Question $Id) }
    $v = [string]$a.Value
    if ($v -ceq '@stdin') {
        if ($Secret) { Progress 'question' 'id' $Id 'text' $Question 'default' $Default 'state' 'waiting' 'secret' '1' }
        else { Progress 'question' 'id' $Id 'text' $Question 'default' $Default 'state' 'waiting' }
        $v = [Console]::In.ReadLine()
        if ($null -eq $v) { Die (T 'no answer to the question "{1}" on standard input ({2}=@stdin)' $Question $Id) }
    } else {
        # a question that comes back was not happy with its answer, and the answer would be the same again
        if ($script:MuAsked.ContainsKey($Id)) { Die (T 'the answer in {1} was not accepted: {2}' $Id $(if ($Secret) { '******' } else { $v })) }
        $script:MuAsked[$Id] = '1'
    }
    Write-Host ("$Question [$Default]: " + $(if ($Secret) { '******' } else { $v }))
    return $v
}
