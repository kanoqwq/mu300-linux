"""Kernel-uevent screen wakeups: real UI, synthetic packets, no device writes."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[1]
NATIVE = TOP / 'devices/u30pro/native/tools'


@unittest.skipUnless(os.name == 'posix', 'Linux native screen simulator')
class PowerEvents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        base = Path(cls.temp.name)
        source = r'''#define UI_HOST
#define UI_RUN "TEST_RUN"
#define UI_SYSFS "TEST_RUN"
#define main simulator_main
#include "u30pro_ui.c"
#undef main
#include <assert.h>

static const char attach[] = "change@/devices/charger\0ACTION=change\0SUBSYSTEM=power_supply\0"
 "POWER_SUPPLY_NAME=u30pro-charger-master\0POWER_SUPPLY_ONLINE=1";
static const char detach[] = "change@/devices/charger\0ACTION=change\0SUBSYSTEM=power_supply\0"
 "POWER_SUPPLY_NAME=u30pro-charger-master\0POWER_SUPPLY_ONLINE=0";
static void dark(void) {
 asleep=1; locked=1; lock_soft=0; pin_mode=PIN_LOCKSCR;
 last_act=500; g_ms=vclock=10000; notice[0]=0;
}
static void expect_locked_awake(void) {
 assert(!asleep && locked && pin_mode==PIN_LOCKSCR && last_act==10000);
}
int main(int argc, char **argv) {
 assert(argc==2);ui_init_canvases();dark();
 switch(atoi(argv[1])) {
 case 1:
  // Startup is a baseline, not a plug event; same-state notifications
  // must not wake a screen that the owner deliberately switched off.
  power_observe(1);assert(asleep && last_act==500 && power_known && power_online);
  assert(power_uevent(attach,sizeof attach,0,0));assert(asleep && last_act==500);
  power_observe(-1);assert(asleep && power_online);
  break;
 case 2:
  power_observe(0);D.feed_ok=0;D.charging=0;D.full=1;
  assert(power_uevent(attach,sizeof attach,0,0));expect_locked_awake();
  assert(D.chg_online && strstr(toast.msg,"已连接外部电源"));
  // No feeder, charging current, PIN entry or unlock action is required.
  dark();assert(power_uevent(detach,sizeof detach,0,0));expect_locked_awake();
  assert(!D.chg_online && strstr(toast.msg,"已断开外部电源"));
  break;
 case 3:
  power_observe(1);
  for(int i=0;i<10;i++) {
   D.charging=i%2;D.full=!D.charging;
   assert(power_uevent(attach,sizeof attach,0,0));assert(asleep && last_act==500);
  }
  // A UI feed transition alone is never a power wakeup.
  D.feed_ok=1;detect_events();D.charging=!D.charging;detect_events();
  assert(asleep && last_act==500 && !strstr(toast.msg,"已断开外部电源"));
  break;
 case 4: {
  power_observe(0);
  const char other[]="ACTION=change\0SUBSYSTEM=power_supply\0POWER_SUPPLY_NAME=battery\0POWER_SUPPLY_ONLINE=1";
  const char wrong[]="ACTION=change\0SUBSYSTEM=net\0POWER_SUPPLY_NAME=u30pro-charger-master\0POWER_SUPPLY_ONLINE=1";
  const char bad[]="ACTION=change\0SUBSYSTEM=power_supply\0POWER_SUPPLY_NAME=u30pro-charger-master\0POWER_SUPPLY_ONLINE=invalid";
  assert(!power_uevent(other,sizeof other,0,0));
  assert(!power_uevent(wrong,sizeof wrong,0,0));
  assert(!power_uevent(bad,sizeof bad,0,0));
  assert(!power_uevent(attach,sizeof attach,123,0));
  assert(!power_uevent(attach,sizeof attach,0,1));
  assert(!power_uevent(attach,sizeof attach-1,0,0));
  assert(!power_uevent(attach,0,0,0));
  assert(asleep && !power_online && last_act==500);
  break;
 }
 case 5:
  power_observe(0);locked=0;lock_soft=1;pin_mode=PIN_NONE;
  assert(power_uevent(attach,sizeof attach,0,0));
  assert(!asleep && !locked && pin_mode==PIN_NONE && last_act==10000);
  dark();lock_soft=1;
  assert(power_uevent(detach,sizeof detach,0,0));expect_locked_awake();
  assert(lock_soft);
  break;
 case 6: {
  // A failed initial read is unknown, never an invented disconnect.
  power_snapshot();assert(!power_known && asleep);
  const char *dir=UI_SYSFS "/sys/class/power_supply/u30pro-charger-master";
  char command[1024];snprintf(command,sizeof command,"mkdir -p %s",dir);
  assert(!system(command));
  FILE *f=fopen(UI_SYSFS "/sys/class/power_supply/u30pro-charger-master/online","w");
  assert(f);fputs("1\n",f);fclose(f);
  power_snapshot();assert(power_known && power_online && asleep);
  break;
 }
 case 7:
  // After waking once, duplicates must not keep extending the idle timeout.
  power_observe(0);assert(power_uevent(attach,sizeof attach,0,0));
  vclock=g_ms=20000;assert(power_uevent(attach,sizeof attach,0,0));
  assert(last_act==10000);
  dark();assert(power_uevent(attach,sizeof attach,0,0));assert(asleep);
  break;
 case 8:
  // Exercise the actual nonblocking, CLOEXEC kernel multicast subscription
  // on the host. This never changes a real power_supply or simulates a cable.
  power_events_open();assert(powerfd>=0);
  assert(fcntl(powerfd,F_GETFL)&O_NONBLOCK);
  assert(fcntl(powerfd,F_GETFD)&FD_CLOEXEC);
  power_events_read();assert(asleep);
  close(powerfd);
  break;
 default:assert(0);
 }
 return 0;
}
'''.replace('TEST_RUN', str(base))
        # platform.h is excluded in UI_HOST, so fcntl belongs to this harness.
        source = '#include <fcntl.h>\n' + source
        (base/'test.c').write_text(source)
        cls.binary = base/'power-events'
        r = subprocess.run(['gcc', '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror',
            '-I', str(NATIVE), str(base/'test.c'), '-lm', '-o', str(cls.binary)],
            capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(r.stderr)

    def case(self, n):
        r = subprocess.run([str(self.binary), str(n)], capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_initial_state_and_duplicate_events_do_not_wake(self): self.case(1)
    def test_attach_and_detach_wake_without_unlocking_or_feeder(self): self.case(2)
    def test_full_limit_and_charge_status_are_not_disconnects(self): self.case(3)
    def test_unrelated_untrusted_or_malformed_events_are_ignored(self): self.case(4)
    def test_locked_and_unlocked_screen_state_is_preserved(self): self.case(5)
    def test_missing_initial_online_is_not_a_false_disconnect(self): self.case(6)
    def test_duplicate_events_do_not_extend_idle_timeout(self): self.case(7)
    def test_real_kernel_listener_is_nonblocking_and_no_timer(self): self.case(8)


if __name__ == '__main__': unittest.main(verbosity=2)
