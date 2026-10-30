//<script>
/*
 * OpenWrt system switch v7 — Minikano
 * Android web-panel plugin; detect installed boot format without a model whitelist.
 * Select the installed OpenWrt for the next boot, preserving default policy.
 * Write only misc[2048:2080]. No root mount, boot flash or boot-chain sync.
 * Shared with mu300-linux/android/magisk/webui-plugins/mu300-system-switch.js.
 */
(() => {
    'use strict';
    const MISC = '/dev/block/by-name/misc';
    const hex = bytes => Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
    const unhex = (text, length) => {
        if (!new RegExp(`^[0-9a-f]{${length * 2}}$`).test(text)) throw new Error('读取数据不完整或格式不正确，未切换');
        return Uint8Array.from(text.match(/../g), b => parseInt(b, 16));
    };
    const crc32 = bytes => {
        let value = 0xffffffff;
        for (const byte of bytes) {
            value ^= byte;
            for (let bit = 0; bit < 8; bit++) value = (value >>> 1) ^ ((value & 1) ? 0xedb88320 : 0);
        }
        return (value ^ 0xffffffff) >>> 0;
    };
    const u32 = (bytes, offset) => new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getUint32(offset, true);
    const ascii = bytes => Array.from(bytes, byte => String.fromCharCode(byte)).join('');
    const control = (text, slot) => {
        const bytes = unhex(text, 32);
        if (!['a', 'b'].includes(slot) || ascii(bytes.slice(4, 8)) !== 'BCAB' || bytes[8] !== 1 ||
            (bytes[9] & 7) !== 2 || u32(bytes, 28) !== crc32(bytes.slice(0, 28))) {
            throw new Error('misc 启动控制块格式或 CRC 校验错误，未切换；请保留备份并联系开发者');
        }
        const current = slot === 'a' ? 12 : 14, other = slot === 'a' ? 14 : 12;
        if (!(bytes[current] & 0x80) || !(bytes[current] & 15) || (bytes[13] & 1) || (bytes[15] & 1)) {
            throw new Error('Android 启动槽尚未就绪或存在校验错误，请先完成 Android 初始化及更新');
        }
        // misc describes the NEXT boot, not the running Android slot. A
        // previously armed OpenWrt is valid here; probe/writeScript separately
        // verify its image and exclude pending install/uninstall boots.
        return bytes;
    };
    const buildBlock = (text, slot) => {
        const bytes = control(text, slot);
        bytes.set([0x5f, slot === 'a' ? 0x62 : 0x61, 0, 0], 0);
        const current = slot === 'a' ? 12 : 14, other = slot === 'a' ? 14 : 12;
        bytes[current] = (bytes[current] & 0xf0) | 14;
        bytes[other] = 0x2f;
        new DataView(bytes.buffer).setUint32(28, crc32(bytes.slice(0, 28)), true);
        return hex(bytes);
    };
    const run = async (command, timeout = 15000) => {
        try {
            // The host may append an end marker to a persistent root shell.
            // Keep errexit, traps and exit inside our subshell.
            const result = await runShellWithRoot('(\n' + command + '\n)', timeout);
            return {ok: Boolean(result && result.success), text: String((result && result.content) || '').trim()};
        } catch (_) {
            return {ok: false, text: '设备命令执行中断，请检查连接及 root 授权'};
        }
    };
    const readHex = async (path, skip, count, bs = 1) => {
        const result = await run(`set -e; set -o pipefail; dd if=${path} bs=${bs} skip=${skip} count=${count} 2>/dev/null | od -An -tx1 -v | tr -d ' \\r\\n'; printf '\\n'`);
        if (!result.ok) throw new Error('无法读取设备数据，请检查 root 授权');
        unhex(result.text, count * bs);
        return result.text;
    };
    const inspectBoot = (headText, tailText, size, family) => {
        const head = unhex(headText, 4096), tail = unhex(tailText, 512);
        const kernel = u32(head, 8), ramdisk = u32(head, 12);
        if (size !== 64 * 1048576 || ascii(head.slice(0, 8)) !== 'ANDROID!' || u32(head, 20) !== 1584 ||
            u32(head, 40) !== 4 || !kernel || !ramdisk) throw new Error('另一槽未识别到支持的 OpenWrt 启动镜像，请用安装器检查');
        const raw = head.slice(44, 1580), end = raw.indexOf(0);
        if (end < 0) throw new Error('OpenWrt 启动参数不完整');
        const args = ascii(raw.slice(0, end)).split(/\s+/);
        // The installed image identifies its format; Android model names are
        // neither an installation marker nor a reliable compatibility gate.
        if (family === undefined) family = args.includes('e5.part_v=2') ? 'e5' : 'mu300';
        const recognized = family === 'e5' ? args.includes('e5.part_v=2') : args.includes('loglevel=5') && !args.some(arg => arg.startsWith('e5.'));
        if (!recognized) throw new Error('另一槽未识别到对应机型系列的 OpenWrt 镜像，请用安装器检查');
        if (args.some(arg => arg.startsWith('e5.part_prepare=')) || args.includes('openwrt.uninstall=1')) {
            throw new Error('设备仍有待完成的安装或卸载，请从安装器记录继续');
        }
        const footer = tail.slice(-64), view = new DataView(footer.buffer, footer.byteOffset, footer.byteLength);
        const qword = at => view.getUint32(at, false) === 0 ? view.getUint32(at + 4, false) : NaN;
        const payloadEnd = 4096 + Math.ceil(kernel / 4096) * 4096 + Math.ceil(ramdisk / 4096) * 4096;
        const length = qword(28), limit = (family === 'e5' ? 56 : 48) * 1048576;
        if (ascii(footer.slice(0, 4)) !== 'AVBf' || qword(12) !== payloadEnd || qword(20) !== payloadEnd ||
            !length || payloadEnd + length > limit) throw new Error('OpenWrt 启动镜像范围或尾部校验失败');
        return family;
    };
    const probe = async () => {
        const result = await run('set -e; [ "$(id -u)" = 0 ]; [ -n "$(getprop ro.build.version.release)" ]; ' +
            'echo SLOT=$(getprop ro.boot.slot_suffix)');
        if (!result.ok) throw new Error('请在 Android 中打开插件，并授予网页管理面板 root 权限');
        const values = {};
        for (const line of result.text.split(/\r?\n/)) {
            const at = line.indexOf('=');
            if (at > 0) values[line.slice(0, at)] = line.slice(at + 1).trim();
        }
        if (!/^_[ab]$/.test(values.SLOT || '')) throw new Error('无法确定当前 Android 槽位，已取消；不会使用 misc 猜测');
        const slot = values.SLOT.slice(1);
        const live = await readHex(MISC, 2048, 32);
        control(live, slot);
        const other = slot === 'a' ? 'b' : 'a', target = '/dev/block/by-name/boot_' + other;
        const sizeResult = await run('blockdev --getsize64 ' + target);
        if (!sizeResult.ok || !/^\d+$/.test(sizeResult.text)) throw new Error('无法确认目标启动分区容量');
        const size = Number(sizeResult.text);
        if (size !== 64 * 1048576) throw new Error('目标启动分区容量不受支持');
        const head = await readHex(target, 0, 1, 4096), tail = await readHex(target, size / 512 - 1, 1, 512);
        const family = inspectBoot(head, tail, size);
        const current = await readHex('/dev/block/by-name/boot_' + slot, 0, 1, 4096);
        if (ascii(unhex(current, 4096).slice(0, 8)) !== 'ANDROID!') throw new Error('当前 Android 启动镜像头不完整，未切换');
        if (head === current) throw new Error('两槽启动镜像头相同，未识别到独立 OpenWrt 镜像');
        return {slot, other, family, live, head, tail, size};
    };
    const octal = text => Array.from(unhex(text, 32), byte => '\\0' + byte.toString(8).padStart(3, '0')).join('');
    const writeScript = (info, wanted) => {
        control(info.live, info.slot); unhex(wanted, 32);
        inspectBoot(info.head, info.tail, info.size, info.family);
        if (info.other !== (info.slot === 'a' ? 'b' : 'a') || wanted !== buildBlock(info.live, info.slot)) throw new Error('启动计划不一致，已取消');
        // Decode to a regular file first. Backups and all writes live inside one
        // device-side lock. Preserve 4 KiB of evidence from the old 128-byte bug.
        return `set -eu
set -o pipefail
BY=/dev/block/by-name
[ "$(getprop ro.boot.slot_suffix)" = _${info.slot} ]
[ -b "$BY/misc" ] && [ -b "$BY/boot_a" ] && [ -b "$BY/boot_b" ]
LOCK=/data/local/tmp/openwrt-system-switch.lock
mkdir "$LOCK" 2>/dev/null || { echo SWITCH_BUSY; exit 1; }
trap 'rmdir "$LOCK"' EXIT
umask 077
mkdir -p /data/adb/openwrt-boot-control
backup=$(mktemp -d /data/adb/openwrt-boot-control/switch.XXXXXX)
echo BACKUP=$backup
read_bc() { dd if="$BY/misc" bs=1 skip=2048 count=32 2>/dev/null | od -An -tx1 -v | tr -d ' \\r\\n'; }
[ "$(read_bc)" = ${info.live} ] || { echo CONTROL_CHANGED; exit 1; }
[ "$(dd if="$BY/boot_${info.other}" bs=4096 count=1 2>/dev/null | od -An -tx1 -v | tr -d ' \\r\\n')" = ${info.head} ]
[ "$(dd if="$BY/boot_${info.other}" bs=512 skip=${info.size / 512 - 1} count=1 2>/dev/null | od -An -tx1 -v | tr -d ' \\r\\n')" = ${info.tail} ]
printf '%b' '${octal(wanted)}' > "$backup/wanted.bin"
[ "$(wc -c < "$backup/wanted.bin")" = 32 ]
[ "$(od -An -tx1 -v "$backup/wanted.bin" | tr -d ' \\r\\n')" = ${wanted} ]
dd if="$BY/misc" of="$backup/misc-head.bin" bs=4096 count=1 2>/dev/null
dd if="$BY/misc" of="$backup/before.bin" bs=1 skip=2048 count=32 2>/dev/null
[ "$(wc -c < "$backup/misc-head.bin")" = 4096 ]
[ "$(od -An -tx1 -v "$backup/before.bin" | tr -d ' \\r\\n')" = ${info.live} ]
sync
if dd if="$backup/wanted.bin" of="$BY/misc" bs=1 seek=2048 count=32 conv=notrunc,fsync && [ "$(read_bc)" = ${wanted} ]; then
    echo SWITCH_VERIFIED
else
    if dd if="$backup/before.bin" of="$BY/misc" bs=1 seek=2048 count=32 conv=notrunc,fsync && [ "$(read_bc)" = ${info.live} ]; then
        echo SWITCH_ROLLED_BACK
    else
        echo SWITCH_RESTORE_FAILED_DO_NOT_REBOOT
    fi
    exit 1
fi`;
    };
    const menu = document.querySelector('#collapseBtn_menu');
    const container = menu && menu.nextElementSibling && menu.nextElementSibling.querySelector('.collapse_box');
    if (!container) return;
    const button = document.createElement('button');
    button.textContent = '系统切换器';
    container.appendChild(button);
    button.onclick = () => {
        const {el, close} = createFixedToast('openwrt_system_switch', `
            <div style="pointer-events:all;width:80vw;max-width:340px">
                <div class="title" style="margin:0">启动 OpenWrt</div>
                <p style="font-size:.7rem">仅切换下次启动，不更改默认设置。永久默认启动请进入 OpenWrt 后设置。</p>
                <p class="os-status" style="font-size:.7rem;user-select:text">准备检查设备及已安装镜像。</p>
                <div class="os-actions" style="display:flex;gap:8px"></div>
            </div>`);
        const actions = el.querySelector('.os-actions'), status = el.querySelector('.os-status');
        const start = document.createElement('button'), dismiss = document.createElement('button');
        start.textContent = '检查并启动 OpenWrt'; dismiss.textContent = '关闭';
        actions.appendChild(start); actions.appendChild(dismiss);
        let busy = false;
        dismiss.onclick = () => { if (!busy) close(); };
        start.onclick = async () => {
            if (busy) return;
            busy = true; start.disabled = dismiss.disabled = true;
            try {
                status.textContent = '正在检查 Android 槽位、启动镜像和 misc…';
                const info = await probe(), wanted = buildBlock(info.live, info.slot);
                status.textContent = `Android: slot ${info.slot} · OpenWrt: slot ${info.other}。正在备份并设置下次启动…`;
                const result = await run(writeScript(info, wanted), 60000);
                const output = result.text.split(/\r?\n/), backup = output.find(line => line.startsWith('BACKUP='));
                if (!result.ok || !output.includes('SWITCH_VERIFIED')) {
                    const reason = output.includes('SWITCH_ROLLED_BACK') ? '写入未完成，已回读确认恢复原启动设置。' :
                        output.includes('SWITCH_BUSY') ? '另一个切换任务正在运行，请稍后再试。' :
                        output.includes('CONTROL_CHANGED') ? '启动设置已被其他任务更改，未写入。' : '切换未完成，写入结果尚未确认，请勿重启。';
                    throw new Error(reason + (backup ? ' 备份：' + backup.slice(7) : ''));
                }
                status.textContent = '启动设置已回读验证，正在重启。设备断连属于预期；如未重启，请手动重启。' + (backup ? ' 备份：' + backup.slice(7) : '');
                createToast('已选择下次启动 OpenWrt，正在重启', 'green');
                await run('reboot', 5000);
                dismiss.disabled = false; busy = false; // Keep start locked to avoid accidental re-arming.
            } catch (error) {
                status.textContent = String(error.message || error);
                createToast(status.textContent, 'red');
                busy = false; start.disabled = dismiss.disabled = false;
            }
        };
    };
})();
//</script>
