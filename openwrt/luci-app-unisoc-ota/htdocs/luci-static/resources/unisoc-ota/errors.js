// Shared by the native updater and LuCI. Raw diagnostics are always secondary.
export function explainError(value, english = false) {
    const original = String(value?.message ?? value?.error ?? value ?? '');
    let text = original.replace(/^(?:RuntimeError|ValueError|Error):\s*/, '').trim();
    // Accept old engines which wrap the error in JSON, optionally with stderr.
    for (const line of [text, ...text.split('\n').reverse()]) {
        try {
            const parsed = JSON.parse(line);
            if (typeof parsed?.error === 'string') { text = parsed.error; break; }
        } catch (_) { /* plain legacy diagnostics */ }
    }
    const tr = (zh, en) => english ? en : zh;
    let message;
    const base = text.match(/Device\/base mismatch: version; device=([^;]+); patch base=([^;]+); patch target=([^;]+)/);
    const conflict = text.match(/Rollback conflict: (.+)/);
    if (base) {
        message = tr(`设备当前为 ${base[1]}，补丁要求从 ${base[2]} 更新到 ${base[3]}。请使用与设备当前版本相同的旧包重新生成补丁。`,
            `The device runs ${base[1]}, but this patch updates ${base[2]} to ${base[3]}. Generate a patch using the device's current release as the old package.`);
        if (base[1] === base[3]) message = tr('设备已经是补丁的目标版本。请检查旧包、新包是否选反，或是否重复应用。', 'The device already runs the target version. Check whether the old/new packages were swapped or the patch was already applied.');
    } else if (conflict) {
        const [path, changed] = conflict[1].split('; changed=');
        const labels = {sha256:tr('内容','content'),size:tr('大小','size'),mode:tr('权限','permissions'),uid:tr('所有者','owner'),gid:tr('用户组','group'),kind:tr('文件类型','file type'),target:tr('链接目标','link target'),xattrs:tr('扩展属性','extended attributes'),boot:tr('启动镜像','boot image')};
        const fields = changed ? changed.split(',').map(k=>labels[k]||k).join(english?', ':'、') : '';
        message = tr(`无法撤销：${path} 在更新后又发生了变化${fields?'（'+fields+'）':''}。已停止以保留后续修改。`, `Rollback stopped to preserve later changes to ${path}${fields?' ('+fields+')':''}.`);
    } else if (/[\u3400-\u9fff]/.test(text) && !/[{}]|Traceback/.test(text) || /^(?:Select an? |Drop |Enter the device management address)/.test(text)) {
        // Validation messages produced by the current UI are already localized.
        message = text;
    } else {
        const rules = [
            [/Release versions are identical/, '旧版和新版是同一个版本，请重新选择两份发行包。', 'The old and new releases are identical. Select two different releases.'],
            [/Unsupported OTA format/, '此补丁格式不受支持，请使用新版工具重新生成补丁。', 'This patch format is unsupported. Regenerate it with the latest tool.'],
            [/Unsafe |Invalid |Duplicate|excessive|Cyclic|escapes root|Cannot find payload content|Unsupported forward tar hardlink|Root image exceeds|Oversized package database/, '安装包或补丁的结构不符合要求。请确认文件完整，并选择可信的完整发行包或补丁。', 'The package or patch structure is invalid. Verify the file and select a trusted complete release or patch.'],
            [/Device\/base mismatch: (?:family|profile)|different device families|Incompatible full releases: profile/, '补丁与设备型号不匹配，请选择该机型的补丁。', 'The patch is for a different device model. Select a matching patch.'],
            [/Device\/base mismatch: version/, '设备版本与补丁起始版本不一致，请检查旧包是否选对。', 'The device version does not match the patch base. Check the selected old release.'],
            [/No completed update to roll back|journal\.json.*No such file/, '没有可撤销的已完成补丁。', 'There is no completed patch to roll back.'],
            [/Rollback backup corrupt|Boot recovery backup checksum mismatch|Missing transaction recovery runtime/, '撤销所需的恢复文件缺失或校验失败，无法安全恢复。请保留现场及技术详情。', 'Recovery files are missing or corrupt. Rollback cannot proceed safely; retain the diagnostics.'],
            [/Insufficient|No space left|insufficient space/, '设备可用空间不足。请释放空间后重新检查。', 'The device has insufficient free space. Free space and check again.'],
            [/checksum|verification failed|Invalid.*blob|Bad CRC|BadZipFile/i, '文件校验失败，补丁或恢复文件可能不完整。请重新获取可信文件。', 'File verification failed. The patch or recovery file may be incomplete; obtain a trusted copy again.'],
            [/portable (?:updater )?runtime|Packaged updater BusyBox lacks|applet not found|(?:python3|stat|mountpoint):.*not found/i, '更新环境不完整，请使用新版 Diff Studio 重新生成补丁并完整解压更新器。', 'The updater environment is incomplete. Regenerate the patch with the latest Diff Studio and extract the complete updater.'],
            [/Authentication|authentication failed|Bad authentication|Permission denied.*password/i, 'SSH 登录失败，请检查 root 密码。', 'SSH login failed. Check the root password.'],
            [/Connection refused|Unable to connect|No route to host|timed out|Timeout|NetworkError|Failed to fetch|XHR|offline|EOF|Connection.*(?:closed|reset)|Socket.*closed/i, '无法与设备完成通信。请检查连接和管理地址；若正在更新，可稍后重新检查状态。', 'Communication with the device failed. Check the connection and management address; if updating, check its status again later.'],
            [/Resource temporarily unavailable|BlockingIOError|still updating|recovery is pending|still running/, '设备正在更新或恢复，请等待完成后重新检查状态。', 'The device is updating or recovering. Wait and check its status again.'],
            [/Object not found|RPC.*not.*register|RPC.*unavailable/, '系统补丁服务暂不可用，请重新连接网页或检查插件是否已安装并启用。', 'The system patch service is unavailable. Reconnect or check that the plugin is installed and enabled.'],
            [/Unexpected RPC arguments|RPC arguments|Unknown OTA command|Unsupported.*command/, '更新器与设备插件的接口不兼容，请更新工具或插件后重试。', 'The updater and device plugin interfaces are incompatible. Update the tool or plugin and retry.'],
            [/File changed during update/, '系统文件在写入期间发生变化，本次更新已中止，请检查设备状态。', 'A system file changed during the update. The update stopped; check the device status.'],
            [/already exists|Output staging exists/, '输出文件已存在，请选择其他文件名或目录。', 'The output file already exists. Choose another filename or directory.'],
            [/Select a single complete release ZIP|full release|Missing release identity|Full release has no/, '无法识别完整发行包或版本信息，请选择该项目的完整发行 ZIP。', 'The full release or version information could not be identified. Select a complete release ZIP from this project.'],
            [/Inspect this patch|Review this device|reviewed patch|Device SSH identity changed/, '检查结果已失效或设备发生变化，请重新连接并检查后再应用。', 'The review is stale or the device changed. Connect and review again before applying.'],
            [/Permission denied|Operation not permitted/i, '操作权限不足，请检查 root 权限及目标文件系统状态。', 'Permission was denied. Check root access and the target filesystem state.'],
            [/Read-only file system|Input\/output error/i, '设备存储无法正常写入，请检查文件系统及存储错误。', 'Device storage cannot be written. Check filesystem and storage errors.'],
        ];
        const rule = rules.find(([pattern]) => pattern.test(text));
        message = rule ? rule[english ? 2 : 1] : tr('操作未完成。请展开技术详情查看具体原因。', 'The operation did not complete. Expand technical details for the cause.');
    }
    return {message, details: original};
}

export function isConnectionError(value) {
    const text = String(value?.message ?? value ?? '');
    return /XHR|NetworkError|Failed to fetch|offline|timed?\s*out|Connection.*(?:closed|reset)|Socket.*closed|abort/i.test(text);
}
