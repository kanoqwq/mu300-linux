// No physical device commands: transport stubs and private regular-file fixtures.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');
const sourcePath = process.argv[2] || [
    path.join(__dirname, '../bundle/【插件】mu300系统切换器.js'),
    path.join(__dirname, '../android/magisk/webui-plugins/mu300-system-switch.js')
].find(p => fs.existsSync(p));
const source = fs.readFileSync(sourcePath, 'utf8');
const BEFORE = {
    a: '5f61000042434142010200009f001e000000000000000000000000000be17146',
    b: '5f62000042434142010200001e009f0000000000000000000000000039be0c19'
};
// Independent reference values computed with Python zlib, not the JS under test.
const WANTED = {
    a: '5f62000042434142010200009e002f000000000000000000000000009bf8546d',
    b: '5f61000042434142010200002f009e00000000000000000000000000fea6e768'
};
function load(runShell = async () => ({success: false, content: ''}), document = {querySelector: () => null}, extra = {}) {
    const sandbox = {document, runShellWithRoot: runShell, ...extra};
    const injected = source.replace('    const menu =', '    globalThis.api = {crc32, control, buildBlock, octal, writeScript, inspectBoot, probe, run};\n    const menu =');
    vm.runInNewContext(injected, sandbox, {filename: sourcePath});
    return sandbox.api;
}
const api = load();
function fixture(family = 'mu300', slot = 'a', command) {
    const head = Buffer.alloc(4096), tail = Buffer.alloc(512);
    head.write('ANDROID!'); head.writeUInt32LE(1048576, 8); head.writeUInt32LE(524288, 12);
    head.writeUInt32LE(1584, 20); head.writeUInt32LE(4, 40);
    head.write(command || (family === 'e5' ? 'loglevel=7 e5.part_v=2 e5.root=part' : 'console=tty0 loglevel=5'), 44);
    const end = 4096 + 1048576 + 524288;
    tail.write('AVBf', 448); tail.writeBigUInt64BE(BigInt(end), 460); tail.writeBigUInt64BE(BigInt(end), 468); tail.writeBigUInt64BE(4096n, 476);
    return {family, slot, other: slot === 'a' ? 'b' : 'a', live: BEFORE[slot], head: head.toString('hex'), tail: tail.toString('hex'), size: 64 * 1048576};
}
function repaired(bytes) {
    const raw = Buffer.from(bytes); raw.writeUInt32LE(api.crc32(raw.subarray(0, 28)), 28); return raw.toString('hex');
}
function transport(info, options = {}) {
    const commands = [];
    const fn = async command => {
        commands.push(command);
        if (command.includes('echo SLOT=')) return {success: true, content:
            `SLOT=${options.slot === undefined ? '_' + info.slot : options.slot}\nMODEL=${options.model || (info.family === 'e5' ? 'E5' : 'MU300')}\nDEVICE=${options.device || 'MU300'}\nPLATFORM=${options.platform || 'ums9620'}\nUNLOCKED=${options.unlocked || 'orange'}`};
        if (command.includes('blockdev --getsize64')) return {success: true, content: String(info.size)};
        if (command.includes('if=/dev/block/by-name/misc')) return {success: true, content: options.live || info.live};
        if (command.includes('if=/dev/block/by-name/boot_' + info.other)) return {success: true, content: command.includes('bs=512') ? info.tail : info.head};
        if (command.includes('if=/dev/block/by-name/boot_' + info.slot)) return {success: true, content: options.identical ? info.head : fixture('mu300', 'a', 'androidboot.hardware=stock').head};
        return {success: false, content: ''};
    };
    return {commands, fn};
}
test('CRC matches IEEE reference; both Android slots match independent misc fixtures', () => {
    assert.equal(api.crc32(Buffer.from('123456789')), 0xcbf43926);
    for (const slot of ['a', 'b']) assert.equal(api.buildBlock(BEFORE[slot], slot), WANTED[slot]);
});
test('preserve reserved bytes and Android health metadata', () => {
    const raw = Buffer.from(BEFORE.a, 'hex'); raw[12] = 0xbf; raw[11] = 0x5a; raw[20] = 0xa7;
    const result = Buffer.from(api.buildBlock(repaired(raw), 'a'), 'hex');
    assert.equal(result[12], 0xbe); assert.equal(result[11], 0x5a); assert.equal(result[20], 0xa7);
});
test('bad CRC, malformed hex, wrong version, verity, unsuccessful Android and pending priority are rejected', () => {
    assert.throws(() => api.buildBlock(BEFORE.a.slice(0, 62) + '00', 'a'), /CRC/);
    for (const bad of ['z'.repeat(64), BEFORE.a.slice(2), BEFORE.a + '00']) assert.throws(() => api.buildBlock(bad, 'a'));
    for (const [at, value] of [[4, 0], [8, 2], [9, 1], [12, 0x1f], [13, 1], [15, 1]]) {
        const raw = Buffer.from(BEFORE.a, 'hex'); raw[at] = value;
        assert.throws(() => api.buildBlock(repaired(raw), 'a'));
    }
    const raw = Buffer.from(BEFORE.a, 'hex'); raw[12] = 0x9e; raw[14] = 0x2f;
    assert.throws(() => api.buildBlock(repaired(raw), 'a'), /待执行/);
});
test('recognition accepts current boot families and refuses stock, corrupt and pending images', () => {
    for (const family of ['e5', 'mu300']) {
        const info = fixture(family); api.inspectBoot(info.head, info.tail, info.size, family);
        for (const command of ['androidboot.hardware=stock', family === 'e5' ? 'e5.part_v=2 e5.part_prepare=abc' : 'loglevel=5 openwrt.uninstall=1']) {
            const bad = fixture(family, 'a', command);
            assert.throws(() => api.inspectBoot(bad.head, bad.tail, bad.size, family));
        }
        assert.throws(() => api.inspectBoot(info.head, '00'.repeat(512), info.size, family));
        assert.throws(() => api.inspectBoot(info.head, info.tail, 2 ** 20, family));
    }
});
test('probe every supported family in either Android slot using actual getprop slot', async () => {
    for (const [family, models, slots] of [['e5', ['E5', 'T551M', 'VIPME M8', 'T512N', 'ZTE 7546N', 'angelcare K1'], ['a', 'b']], ['mu300', ['MU300', 'F50', 'U30AIR', 'V50'], ['a', 'b']]]) {
        for (const model of models) for (const slot of slots) {
            const info = fixture(family, slot), tr = transport(info, {model, device: model === 'V50' ? 'MU3351' : model});
            const result = await load(tr.fn).probe(); assert.equal(result.slot, slot); assert.equal(result.family, family);
            assert.ok(tr.commands.every(cmd => !cmd.includes('of=') && !cmd.includes('mount ') && !cmd.includes('default-boot')));
        }
    }
});
test('installed E5-family images do not require an orange verified-boot property', async () => {
    for (const slot of ['a', 'b']) for (const unlocked of ['green', 'yellow', 'unknown']) {
        const info = fixture('e5', slot), tr = transport(info, {unlocked, model: 'angelcare K1'});
        const result = await load(tr.fn).probe(); assert.equal(result.slot, slot); assert.equal(result.family, 'e5');
    }
});

test('missing current slot never falls back to misc; unsupported models and identical boots stop', async () => {
    for (const options of [{slot: ''}, {slot: '_c'}, {model: 'FM_U20', device: 'FM_U20'}, {model: 'V50', device: 'MU3351', platform: 'wrong'}, {identical: true}]) {
        const tr = transport(fixture(), options); await assert.rejects(load(tr.fn).probe());
        if ('slot' in options) assert.equal(tr.commands.length, 1);
    }
});
function fixtureFiles(root, info) {
    fs.mkdirSync(path.join(root, 'by'));
    const before = Buffer.alloc(4096, 0xa5); Buffer.from(info.live, 'hex').copy(before, 2048);
    fs.writeFileSync(path.join(root, 'by/misc'), before);
    for (const slot of ['a', 'b']) {
        const file = path.join(root, 'by/boot_' + slot); fs.closeSync(fs.openSync(file, 'w')); fs.truncateSync(file, info.size);
        if (slot === info.other) {
            const fd = fs.openSync(file, 'r+'); fs.writeSync(fd, Buffer.from(info.head, 'hex'), 0, 4096, 0);
            fs.writeSync(fd, Buffer.from(info.tail, 'hex'), 0, 512, info.size - 512); fs.closeSync(fd);
        }
    }
    return before;
}
function commandFor(root, info, prefix = '') {
    return `getprop() { printf '%s' '_${info.slot}'; }\n${prefix}\n` + api.writeScript(info, WANTED[info.slot])
        .replaceAll('/dev/block/by-name', path.join(root, 'by'))
        .replaceAll('/data/local/tmp/openwrt-system-switch.lock', path.join(root, 'lock'))
        .replaceAll('/data/adb/openwrt-boot-control', path.join(root, 'backups'))
        .replaceAll('[ -b ', '[ -f '); // No block devices are ever allowed in this test.
}
const posix = process.platform !== 'win32';
for (const shell of ['bash', 'busybox']) {
    test(`${shell}: real writes change exactly 32 bytes for both families and either Android slot`, {skip: !posix}, () => {
        for (const [family, slot] of [['e5', 'a'], ['e5', 'b'], ['mu300', 'a'], ['mu300', 'b']]) {
            const root = fs.mkdtempSync(path.join(os.tmpdir(), 'switch-plugin-'));
            try {
                const info = fixture(family, slot), before = fixtureFiles(root, info), script = commandFor(root, info);
                const result = spawnSync(shell, [...(shell === 'busybox' ? ['ash'] : []), '-c', script], {encoding: 'utf8'});
                assert.equal(result.status, 0, result.stdout + result.stderr); assert.match(result.stdout, /SWITCH_VERIFIED/);
                const after = fs.readFileSync(path.join(root, 'by/misc'));
                assert.deepEqual(after.subarray(0, 2048), before.subarray(0, 2048)); assert.deepEqual(after.subarray(2080), before.subarray(2080));
                assert.equal(after.subarray(2048, 2080).toString('hex'), WANTED[slot]);
                const backup = result.stdout.match(/BACKUP=(.+)/)[1];
                assert.deepEqual(fs.readFileSync(path.join(backup, 'misc-head.bin')), before);
                assert.equal(fs.statSync(path.join(backup, 'wanted.bin')).size, 32);
                assert.ok(!fs.existsSync(path.join(root, 'lock')));
            } finally {fs.rmSync(root, {recursive: true, force: true});}
        }
    });
}
test('write fault and readback fault restore original block; rollback failure is explicit', {skip: !posix}, () => {
    for (const fault of ['partial', 'mismatch', 'rollback']) {
        const root = fs.mkdtempSync(path.join(os.tmpdir(), 'switch-plugin-fault-'));
        try {
            const info = fixture(), before = fixtureFiles(root, info);
            const prefix = `dd() { case "$1" in if=*/wanted.bin)
command dd if=/dev/zero of=${path.join(root, 'by/misc')} bs=1 seek=2048 count=16 conv=notrunc 2>/dev/null
return ${fault === 'mismatch' ? '0' : '7'};; ${fault === 'rollback' ? 'if=*/before.bin) return 9;;' : ''} esac; command dd "$@"; }`;
            const result = spawnSync('busybox', ['ash', '-c', commandFor(root, info, prefix)], {encoding: 'utf8'});
            assert.notEqual(result.status, 0); assert.doesNotMatch(result.stdout, /SWITCH_VERIFIED/);
            if (fault === 'rollback') assert.match(result.stdout, /SWITCH_RESTORE_FAILED_DO_NOT_REBOOT/);
            else {assert.match(result.stdout, /SWITCH_ROLLED_BACK/); assert.deepEqual(fs.readFileSync(path.join(root, 'by/misc')), before);}
            assert.ok(!fs.existsSync(path.join(root, 'lock')));
        } finally {fs.rmSync(root, {recursive: true, force: true});}
    }
});
test('device mutex, changed current slot, concurrent misc and changed boot all refuse writes', {skip: !posix}, () => {
    for (const change of ['lock', 'slot', 'misc', 'boot']) {
        const root = fs.mkdtempSync(path.join(os.tmpdir(), 'switch-plugin-guard-'));
        try {
            const info = fixture(); fixtureFiles(root, info);
            if (change === 'lock') fs.mkdirSync(path.join(root, 'lock'));
            if (change === 'misc') {const fd = fs.openSync(path.join(root, 'by/misc'), 'r+'); fs.writeSync(fd, Buffer.from('changed'), 0, 7, 2048); fs.closeSync(fd);}
            if (change === 'boot') {const fd = fs.openSync(path.join(root, 'by/boot_b'), 'r+'); fs.writeSync(fd, Buffer.from('changed')); fs.closeSync(fd);}
            const before = fs.readFileSync(path.join(root, 'by/misc'));
            const result = spawnSync('busybox', ['ash', '-c', commandFor(root, info, change === 'slot' ? "getprop() { echo _b; }" : '')], {encoding: 'utf8'});
            assert.notEqual(result.status, 0); assert.doesNotMatch(result.stdout, /SWITCH_VERIFIED/);
            assert.deepEqual(fs.readFileSync(path.join(root, 'by/misc')), before);
        } finally {fs.rmSync(root, {recursive: true, force: true});}
    }
});
test('old doubled-backslash printf reproduces 128 ASCII bytes, rather than 32 binary bytes', {skip: !posix}, () => {
    const argument = WANTED.a.match(/../g).map(b => '\\\\x' + b).join('');
    const result = spawnSync('busybox', ['ash', '-c', `printf '${argument}'`]);
    assert.equal(result.stdout.length, 128); assert.equal(result.stdout.subarray(0, 4).toString(), '\\x5f');
});
test('root socket host end marker still executes after success or script failure; lock is released', {skip: !posix}, async () => {
    for (const fault of [false, true]) {
        const root = fs.mkdtempSync(path.join(os.tmpdir(), 'switch-plugin-socket-'));
        try {
            const info = fixture(); fixtureFiles(root, info);
            if (fault) fs.mkdirSync(path.join(root, 'lock'));
            const socket = load(async command => {
                const result = spawnSync('busybox', ['ash', '-c', command + '\nprintf HOST_END_MARKER'], {encoding: 'utf8'});
                // UFI's API reports successful socket execution independently
                // of the exit code. The plugin must require its commit marker.
                assert.equal(result.status, 0); assert.match(result.stdout, /HOST_END_MARKER$/);
                return {success: true, content: result.stdout};
            });
            const response = await socket.run(commandFor(root, info));
            assert.equal(response.ok, true); assert.match(response.text, fault ? /SWITCH_BUSY/ : /SWITCH_VERIFIED/);
            if (!fault) assert.ok(!fs.existsSync(path.join(root, 'lock')));
        } finally {fs.rmSync(root, {recursive: true, force: true});}
    }
});
test('UI reboots only after verified commit, keeps failure visible and blocks repeated clicks', async () => {
    for (const result of [
        {success: true, content: 'BACKUP=/private/test\nSWITCH_VERIFIED'},
        {success: true, content: 'BACKUP=/private/test\nSWITCH_ROLLED_BACK'},
        {success: false, content: 'BACKUP=/private/test\nSWITCH_RESTORE_FAILED_DO_NOT_REBOOT'}
    ]) {
        const element = () => ({children: [], disabled: false, appendChild(child) {this.children.push(child);}});
        const menuContainer = element(), actions = element(), status = element();
        const document = {createElement: element, querySelector: () => ({nextElementSibling: {querySelector: () => menuContainer}})};
        const tr = transport(fixture()); let reboot = 0, writes = 0;
        load(async command => {
            if (command.includes('echo BACKUP=$backup')) {writes++; return result;}
            if (command === '(\nreboot\n)') {reboot++; return {success: false, content: 'expected disconnect'};}
            return tr.fn(command);
        }, document, {createToast: () => {}, createFixedToast: () => ({el: {querySelector: selector => selector === '.os-actions' ? actions : status}, close: () => {}})});
        menuContainer.children[0].onclick(); const start = actions.children[0];
        const first = start.onclick(); await start.onclick(); await first;
        const committed = result.content.includes('SWITCH_VERIFIED');
        assert.equal(writes, 1); assert.equal(reboot, committed ? 1 : 0);
        assert.equal(start.disabled, committed);
        assert.match(status.textContent, committed ? /断连属于预期/ : result.content.includes('ROLLED_BACK') ? /恢复原启动设置/ : /请勿重启/);
    }
});
