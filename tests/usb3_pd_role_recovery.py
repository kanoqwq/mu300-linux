"""Check real TCPM recovery bounds and the opt-in patch on both prepared kernels."""
import importlib.util
from pathlib import Path
import re
import subprocess
import tempfile

TOP = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('recovery', TOP / 'upstream/port/tcpm_role_recovery.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


def function(text, name):
    match = re.search(r'static inline enum tcpm_state ' + re.escape(name) +
                      r'\([^;]*\)\n\{', text)
    assert match, 'missing real TCPM helper definition'
    start = match.start()
    begin = text.index('{', start)
    level = 1
    end = begin + 1
    while level:
        level += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


for version in ('6.18.54', '7.2.8'):
    kernel = Path('/src/linux-' + version)
    source = (kernel / 'drivers/usb/typec/tcpm/tcpm.c').read_text()
    header = (kernel / 'include/linux/usb/tcpm.h').read_text()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        h = root / 'include/linux/usb/tcpm.h'
        s = root / 'drivers/usb/typec/tcpm/tcpm.c'
        h.parent.mkdir(parents=True)
        s.parent.mkdir(parents=True)
        h.write_text(header)
        s.write_text(source)
        patch.apply(root, enable=True)
        once = (h.read_text(), s.read_text())
        patch.apply(root, enable=True)
        assert once == (h.read_text(), s.read_text()), 'patch must be idempotent'
        assert once[1].count(patch.NEW) == 1
        assert once[0].count('\tbool hard_reset_on_data_role_mismatch;') == 1
        # A changed kernel branch must fail before either file is written.
        broken = once[1].replace(patch.NEW, '/* unknown error recovery */')
        s.write_text(broken)
        try:
            patch.apply(root, enable=True)
        except ValueError:
            pass
        else:
            raise AssertionError('unknown branch silently accepted')
        assert h.read_text() == once[0] and s.read_text() == broken
        s.write_text(once[1])
        patch.apply(root, enable=False)
        restored = (h.read_text(), s.read_text())
        assert patch.FIELD not in restored[0] and patch.NEW not in restored[1]
        assert patch.OLD in restored[1] and patch.DECL not in restored[1]
        patch.apply(root, enable=False)
        assert restored == (h.read_text(), s.read_text())
        code = r'''
#include <assert.h>
#include <stdbool.h>
enum tcpm_state { HARD_RESET_SEND, ERROR_RECOVERY, SRC_UNATTACHED,
 SNK_WAIT_CAPABILITIES, SNK_WAIT_CAPABILITIES_TIMEOUT, SNK_READY, SNK_UNATTACHED };
#define PD_N_HARD_RESET_COUNT 2
#define TYPEC_SOURCE 1
struct tcpc_dev { bool hard_reset_on_data_role_mismatch; };
struct tcpm_port { struct tcpc_dev *tcpc; int hard_reset_count, pwr_role;
 bool pd_capable; enum tcpm_state state; };
static enum tcpm_state result;
#define tcpm_log(...) ((void)0)
static void tcpm_set_state(struct tcpm_port *p, enum tcpm_state state, int delay) {
 (void)p; assert(!delay); result=state;
}
''' + function(source, 'hard_reset_state') + r'''
static void mismatch(struct tcpm_port *port) {
''' + patch.NEW + r'''
}
int main(void) {
 struct tcpc_dev tcpc={0}; struct tcpm_port port={.tcpc=&tcpc};
 for(int count=0;count<5;count++) {
  port.hard_reset_count=count; mismatch(&port); assert(result==ERROR_RECOVERY);
 }
 tcpc.hard_reset_on_data_role_mismatch=true;
 for(int count=0;count<PD_N_HARD_RESET_COUNT;count++) {
  port.hard_reset_count=count; mismatch(&port); assert(result==HARD_RESET_SEND);
 }
 port.hard_reset_count=PD_N_HARD_RESET_COUNT;port.pd_capable=true;
 mismatch(&port);assert(result==ERROR_RECOVERY);
 port.pd_capable=false;port.pwr_role=TYPEC_SOURCE;
 mismatch(&port);assert(result==SRC_UNATTACHED);
 port.pwr_role=0;port.state=SNK_WAIT_CAPABILITIES;
 mismatch(&port);assert(result==SNK_READY);
 port.state=SNK_UNATTACHED;mismatch(&port);assert(result==SNK_UNATTACHED);
}
'''
        (root / 'test.c').write_text(code)
        subprocess.run(['cc', '-Wall', '-Werror', str(root / 'test.c'), '-o', str(root / 'test')], check=True)
        subprocess.run([str(root / 'test')], check=True)
    print(version + ': opt-in isolation, real reset bounds, idempotence and drift rejection passed')
