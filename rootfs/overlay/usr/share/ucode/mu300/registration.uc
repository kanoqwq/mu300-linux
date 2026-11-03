// Compatibility with lib/registration.sh: passive state from the one AT owner.
// Keep the on-disk schema and ubus events consumed by network recovery. No AT I/O.
import { readfile, open, mkdir, lstat, chmod, rename, unlink } from 'fs';

function cache(root, notify) {
    function prepare() {
        mkdir(root, 0700);
        let s = lstat(root);
        if (!s || s.type != 'directory' || s.uid != 0) return false;
        return chmod(root, 0700);
    }
    function put(name, value) {
        let path = root + '/' + name;
        if (readfile(path) == value) return false;
        if (!prepare()) return false;
        let temp = path + '.modemd-part';
        let f = open(temp, 'w', 0600);
        if (!f) return false;
        let ok = f.write(value) == length(value);
        f.close();
        if (ok && rename(temp, path)) return true;
        unlink(temp);
        return false;
    }
    function event(slot, line) {
        if (slot != 0 && slot != 1) return false;
        let m = match(line, /^\+ECIND:\s*([0-9]+(\s*,\s*[0-9]+)+)\s*$/);
        if (!m) return false;
        let fields = split(m[1], /\s*,\s*/);
        for (let v in fields) if (length(v) > 10 || +v > 4294967295) return false;
        let t = +fields[0], v = +fields[1], c = fields[2] != null ? +fields[2] : null;
        if (t != 3 || (v == 1 ? index([1, 2, 7, 25, 34], c) < 0 : index([0, 2, 4, 100], v) < 0)) return false;
        notify('mu300.sim-change', {slot});
        return true;
    }
    function registration(slot, line, query) {
        if (slot != 0 && slot != 1) return false;
        let m = match(line, /^\+(CEREG|C5GREG):\s*(.*)$/);
        if (!m) return false;
        let name='slot'+slot+(m[1]=='C5GREG' ? '-nr' : '')+'.json';
        let f = split(m[2], ','), s = trim(f[query ? 1 : 0] ?? ''), r = trim(f[query ? 4 : 3] ?? '');
        if (!match(s, /^([0-9]|10)$/)) return false;
        let rat = match(r, /^(0|[1-9][0-9]{0,2})$/) ? +r : null;
        let value = sprintf('{"slot":%d,"stat":%d,"rat":%J}\n', slot, +s, rat);
        if (!put(name, value)) return false;
        notify('mu300.registration', {slot});
        return true;
    }
    function sim(slot, state) {
        if (state=='absent') {
            unlink(root+'/slot'+slot+'.json'); unlink(root+'/slot'+slot+'-nr.json');
        }
        if (put('sim' + slot + '.json', sprintf('{"slot":%d,"state":%J}\n', slot, state)))
            notify('mu300.sim-state', {slot});
    }
    function reply(command, text) {
        // Only query replies with an explicit firmware SIM context may update
        // registration. Unscoped replies must never be assigned to data_sim.
        let query=match(command,/^AT\+SPACTCARD=([01]);(.+)$/);
        if (query) {
            for (let domain in ['CEREG','C5GREG']) {
                if (index(split(query[2],';'),'+'+domain+'?')<0) continue;
                for (let line in split(replace(text,/\r/g,''),'\n'))
                    if (index(line,'+'+domain+':')==0) registration(+query[1],line,true);
            }
        }
        let m = match(command, /^AT\+SPACTCARD=([01]);(\+CPIN\?|\+SFUN=[2345]|\+CFUN=0|\+CFUN\?)$/);
        if (!m) return;
        let slot = +m[1], cmd = m[2], state = null, power = root + '/sim-power' + slot;
        for (let line in split(replace(text, /\r/g, ''), '\n')) {
            if (line == '+CFUN: 1') state = 'powered';
            else if (line == '+CPIN: READY') state = 'ready';
            else if (match(line, /^\+CPIN: (SIM PIN2?|SIM PUK2?|PH-.*)$/)) state = 'locked';
            else if (line == '+CPIN: NOT INSERTED' || line == '+CPIN: SIM NOT INSERTED') state = 'absent';
            else if (line == '+CME ERROR: 10' || line == '+CME ERROR: SIM not inserted') {
                if (cmd == '+CPIN?' && lstat(power)) sim(slot, 'absent');
                return;
            }
            else if (line == 'OK') {
                if (cmd == '+SFUN=2' || cmd == '+SFUN=4' || (cmd == '+CFUN?' && state == 'powered')) put('sim-power' + slot, '');
                else if (cmd == '+SFUN=3' || cmd == '+SFUN=5' || cmd == '+CFUN=0') {
                    unlink(power); unlink(root + '/sim' + slot + '.json');
                    unlink(root+'/slot'+slot+'.json'); unlink(root+'/slot'+slot+'-nr.json');
                }
                else if (cmd == '+CPIN?' && index(['ready', 'locked', 'absent'], state) >= 0) {
                    if (state == 'absent' && !lstat(power)) return;
                    put('sim-power' + slot, ''); sim(slot, state);
                }
                return;
            }
        }
    }
    function reset(slot) {
        if (slot != 0 && slot != 1) return;
        for (let name in ['slot' + slot + '.json', 'slot'+slot+'-nr.json', 'sim' + slot + '.json', 'sim-power' + slot]) unlink(root + '/' + name);
    }
    return {event, registration, reply, reset};
}
export {cache};
