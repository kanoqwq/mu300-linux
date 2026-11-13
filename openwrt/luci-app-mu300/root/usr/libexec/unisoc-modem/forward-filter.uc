/* POSIX ERE filtering, invoked only by SMS events or configuration saves. */
'use strict';
import * as fs from 'fs';
function failure(field, line, empty) {
    return { ok: 0, error: empty ? field + '_filter_empty' : 'invalid_' + field + '_filter_regex', line: line };
}
function compile_group(config, field, flags) {
    let key = field + '_filter_mode', mode = key in config ? config[key] : 'off';
    let rawkey = field + '_filter_regex', raw = rawkey in config ? config[rawkey] : '';
    if (!(mode in ['off', 'blacklist', 'whitelist']) || type(raw) != 'string' || index(raw, '\u0000') >= 0)
        return { error: { ok: 0, error: 'invalid_forward_filter' } };
    let compiled = [], lines = split(replace(raw, /\r\n/g, '\n'), '\n');
    for (let i = 0; i < length(lines); i++) {
        let pattern = lines[i];
        if (!length(pattern)) continue;
        if (length(pattern) > 512 || length(compiled) >= 32 || index(pattern, '\r') >= 0 || index(pattern, '\u0000') >= 0)
            return { error: failure(field, i + 1, false) };
        try { push(compiled, regexp(pattern, flags)); }
        catch (e) { return { error: failure(field, i + 1, false) }; }
    }
    if (mode == 'whitelist' && !length(compiled))
        return { error: failure(field, 0, true) };
    return { mode: mode, rules: compiled };
}
function passes(group, value) {
    if (group.mode == 'off') return true;
    let matched = false;
    for (let pattern in group.rules) {
        if (match(value, pattern) != null) { matched = true; break; }
    }
    return group.mode == 'whitelist' ? matched : !matched;
}
let action = ARGV[0], result;
try {
    let input = json(fs.readfile('/dev/stdin'));
    if (type(input) != 'object' || !(action in ['validate', 'check'])) die('invalid input');
    let ignore = 'filter_ignore_case' in input ? input.filter_ignore_case : false;
    if (!(ignore === false || ignore === true || ignore === 0 || ignore === 1)) die('invalid case flag');
    let flags = ignore ? 'i' : ''; // POSIX: omit REG_NEWLINE to match the whole SMS.
    let number = compile_group(input, 'number', flags), text = compile_group(input, 'text', flags);
    result = number.error || text.error || { ok: 1 };
    if (action == 'check' && result.ok) {
        if (type(input.number) != 'string' || type(input.text) != 'string') die('invalid message');
        result.allow = passes(number, input.number) && passes(text, input.text);
    }
} catch (e) { result = { ok: 0, error: 'invalid_forward_filter' }; }
if (action == 'validate') printf('%J\n', result);
else print(!result.ok ? 'invalid\n' : result.allow ? 'allow\n' : 'block\n');
