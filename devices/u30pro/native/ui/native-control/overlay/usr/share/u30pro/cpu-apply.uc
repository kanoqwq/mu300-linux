/* boot: apply the stored CPU settings (u30pro_native_control.cpu), once; failures only get logged and never block the boot */
import * as cpu from '/usr/share/u30pro/cpu.uc';

let saved = cpu.load_saved();
if (saved == null) exit(0);
let r = cpu.set(saved);
printf('u30pro-cpu-apply: %s\n', r.ok ? 'stored CPU settings applied' : 'not applied: ' + r.error);
exit(0);
