// Real ubus and RPC-script integration. All counters, ledgers and writes are
// isolated in /tmp and a private ubus object; never changes a device's plan.
import * as fs from 'fs';
const lib = getenv('TRAFFIC_TEST_LIB');
const rpcfile = getenv('TRAFFIC_TEST_RPC');
const string_slots = getenv('TRAFFIC_TEST_STRING_SLOTS') == '1';
let checks = 0, pid = null;
function check(ok, label) { if (!ok) die('FAIL: ' + label + '\n'); checks++; }
function q(s) { return "'" + replace(s, /'/g, "'\\''") + "'"; }
function run(cmd) {
    let p = fs.popen(cmd, 'r'), out = p.read('all'), code = p.close();
    return {out, code};
}
const root = trim(run('mktemp -d /tmp/traffic-rpc.XXXXXX').out);
check(match(root, /^\/tmp\/traffic-rpc\.[a-zA-Z0-9]+$/), 'private directory');
check(lib && fs.stat(lib + '/traffic') && fs.stat(rpcfile), 'test sources');
const bus = 'test.traffic.rpc.' + substr(root, -6);
for (let dir in ['bin', 'disk', 'ram', 'net', 'net/sipa_eth0', 'net/sipa_eth8',
                 'net/sipa_eth0/statistics', 'net/sipa_eth8/statistics']) fs.mkdir(root + '/' + dir, 448);
fs.writefile(root + '/card', '0');
function counters(slot, rx, tx) {
    let dev = root + '/net/' + (slot ? 'sipa_eth8' : 'sipa_eth0');
    fs.writefile(dev + '/ifindex', '' + (31 + slot));
    fs.writefile(dev + '/statistics/rx_bytes', '' + rx);
    fs.writefile(dev + '/statistics/tx_bytes', '' + tx);
}
counters(0, 1000, 100); counters(1, 2000, 200);
const real_ubus = trim(run('command -v ubus').out);
check(substr(real_ubus, 0, 1) == '/', 'real ubus executable');
fs.writefile(root + '/bin/ubus', '#!/bin/sh\n' +
    '[ "$1" = -t ] && [ "$3" = call ] && [ "$4" = unisoc.traffic ] || exit 99\n' +
    'shift 4\nprintf "%s\\n" "$2" > ' + q(root + '/forwarded.json') + '\n' +
    'exec ' + q(real_ubus) + ' -t 5 call ' + q(bus) + ' "$@"\n');
fs.chmod(root + '/bin/ubus', 448);
function call(method, args) {
    let r = run(q(real_ubus) + ' -t 5 call ' + q(bus) + ' ' + q(method) + ' ' + q(sprintf('%J', args || {})));
    check(r.code == 0, 'private ubus ' + method);
    return json(r.out);
}
function get(slot) { return call('get', {slot: string_slots ? '' + slot : slot}); }
function rpc(method, args) {
    let r = run('printf %s ' + q(sprintf('%J', args)) + ' | MU300_DASH_BIN=' + q(lib) +
        ' MU300_DASH_DIR=' + q(root + '/dash') + ' PATH=' + q(root + '/bin') + ':"$PATH" sh ' +
        q(rpcfile) + ' call ' + q(method));
    check(r.code == 0, 'public RPC ' + method);
    return json(r.out);
}
function forwarded(fields) {
    let args = json(fs.readfile(root + '/forwarded.json'));
    check(sprintf('%J', sort(keys(args))) == sprintf('%J', sort(fields)), 'argument allowlist');
}
function stop() {
    if (!pid) return;
    run('kill -TERM ' + pid);
    for (let i = 0; i < 30; i++) {
        if (trim(run(q(real_ubus) + ' -S list ' + q(bus)).out) == '') { pid = null; return; }
        sleep(100);
    }
    die('Fixture service did not stop\n');
}
try {
    let child = run('MU300_TRAFFIC_DIR=' + q(root + '/disk') + ' MU300_TRAFFIC_RUN=' + q(root + '/ram') +
        ' MU300_TRAFFIC_SYS=' + q(root + '/net') + ' MU300_TRAFFIC_CARD=' + q(root + '/card') +
        ' MU300_TRAFFIC_BUS=' + q(bus) + ' MU300_TRAFFIC_SIM_SLOTS=2' +
        ' MU300_TRAFFIC_DEVICES=' + q('["sipa_eth0","sipa_eth8"]') +
        ' ucode ' + q(lib + '/traffic') + ' >' + q(root + '/daemon.log') + ' 2>&1 & echo $!');
    pid = int(trim(child.out)); check(pid > 1, 'fixture process');
    let ready = false;
    for (let i = 0; i < 30; i++) {
        if (trim(run(q(real_ubus) + ' -S list ' + q(bus)).out) == bus) { ready = true; break; }
        sleep(100);
    }
    check(ready, 'fixture published: ' + fs.readfile(root + '/daemon.log'));
    const session = '00000000000000000000000000000000';
    // This extra field recreates rpcd's browser call, unlike a root CLI call.
    let plan = {plan_name:'不知道',device:'sipa_eth0',count_mode:'total',monthly_gb:1000,daily_gb:1000,reset_day:1};
    check(rpc('traffic_set', {payload:sprintf('%J', plan),slot:'0',ubus_rpc_session:session,extra:'ignored'}).ok == 1,
          'browser request saves the reported SIM 1 plan');
    forwarded(['payload', 'slot']);
    let saved = get(0).config;
    check(get(0).status.plan_name == plan.plan_name, 'SIM 1 snapshot publishes its own plan name');
    for (let key, value in plan) check(saved[key] == value, 'SIM 1 plan field ' + key);
    check(get(1).config.monthly_gb == 0, 'SIM 1 save leaves SIM 2 unchanged');
    plan = {plan_name:'SIM 2 "quoted" 套餐',device:'sipa_eth8',count_mode:'rx',monthly_gb:55,daily_gb:2.5,reset_day:31};
    check(rpc('traffic_set', {payload:sprintf('%J', plan),slot:'1',ubus_rpc_session:session}).ok == 1,
          'browser SIM 2 save');
    forwarded(['payload', 'slot']);
    check(get(1).config.plan_name == plan.plan_name && get(0).config.monthly_gb == 1000, 'independent plans');
    check(get(1).status.plan_name == plan.plan_name, 'SIM 2 snapshot publishes its own plan name');
    check(rpc('traffic_set', {payload:'invalid-json',slot:'0',ubus_rpc_session:session}).error == 'invalid_config',
          'validation error survives browser forwarding');
    counters(0, 1123, 109); counters(1, 2456, 228); call('flush');
    check(get(0).status.today.rx == 123 && get(1).status.today.rx == 456, 'live per-SIM counters');
    check(rpc('traffic_clear', {slot:'1',confirm:false,ubus_rpc_session:session}).error == 'confirmation_required',
          'browser cancel reaches confirmation guard');
    forwarded(['confirm', 'slot']);
    check(get(1).status.today.rx == 456, 'cancel preserves usage');
    check(rpc('traffic_clear', {slot:'1',confirm:true,ubus_rpc_session:session}).ok == 1, 'browser clear succeeds');
    forwarded(['confirm', 'slot']);
    check(get(1).status.today_used == 0 && get(0).status.today.rx == 123, 'clear affects only requested SIM');
    check(get(1).config.monthly_gb == 55, 'clear retains plan');
    stop();
} catch (e) { stop(); die('Traffic browser RPC: ' + e + '; fixtures ' + root + '\n'); }
printf('PASS: %d real ubus/browser RPC checks; fixtures %s\n', checks, root);
