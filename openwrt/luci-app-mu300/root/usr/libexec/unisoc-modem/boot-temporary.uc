// Keep the existing transactional boot helper as the sole misc/default owner.
import * as fs from 'fs';
const helper = getenv('BOOT_CONTROL_HELPER') || '/opt/mu300/bin/mu300-next-boot';
let error = null;
if (!fs.access(helper, 'x')) error = '启动控制不可用';
else {
    let child = fs.popen("'" + replace(helper, /'/g, "'\\''") + "' android 2>&1", 'r');
    let output = child ? child.read('all') : '';
    let code = child ? child.close() : 1;
    if (code != 0) {
        if (index(output, 'rollback failed') >= 0) error = '启动控制回退失败，请检查启动设置';
        else if (index(output, 'boot control busy') >= 0) error = '启动控制正忙，请稍后重试';
        else if (index(output, 'misc verify failed') >= 0) error = '启动控制回读校验失败';
        else error = '启动方式设置失败，请重试';
    }
}
printf('%J\n', error ? {ok:0, error} : {ok:1, op:'os linux-temporary'});
exit(error ? 1 : 0);
