"""Exercise rapid input against the real screen UI with a deterministic clock."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[1]
NATIVE = TOP / 'devices/u30pro/native/tools'


@unittest.skipUnless(os.name == 'posix', 'native screen simulator requires POSIX')
class Navigation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        base = Path(cls.temp.name)
        source = r'''#define UI_HOST
#define UI_RUN "TEST_RUN"
#define main simulator_main
#include "u30pro_ui.c"
#undef main
#include <assert.h>

static void clock_step(long ms) { vclock += ms; g_ms = vclock; }
static void start(void) {
 ui_init_canvases(); g_ms = vclock = 10000; last_act = g_ms;
 D.feed_ok = 1; pin_mode = locked = asleep = lock_soft = 0;
 go_home(); compose();
}
static void swipe(int direction, int distance) {
 int from = direction > 0 ? 200 : 40, to = from - direction * distance;
 compose(); ui_touch(T_DOWN,from,120,now_ms()); clock_step(10);
 ui_touch(T_MOVE,to,120,now_ms()); clock_step(10);
 ui_touch(T_UP,to,120,now_ms());
}
static void settle(void) { clock_step(300); nav_tick(now_ms()); compose(); }
static void expect_page(int p) {
 assert(page == wrap_page(p)); assert(!anim_on && !anim_commit && !dragging);
 assert(tx_off == 0.f);
}
int main(int argc, char **argv) {
 assert(argc == 2); start();
 switch (atoi(argv[1])) {
 case 1:
  // Each press arrives before a single transition can finish, including a
  // burst processed before any display frame. All relative requests count.
  for(int i=0;i<11;i++) { ui_key(102,100); clock_step(20); }
  settle(); expect_page(11);
  for(int i=0;i<9;i++) { ui_key(114,100); clock_step(20); }
  settle(); expect_page(2);
  break;
 case 2:
  for(int i=0;i<7;i++) { swipe(1,140); clock_step(25); nav_tick(now_ms()); }
  settle(); expect_page(7);
  for(int i=0;i<6;i++) { swipe(-1,140); clock_step(25); nav_tick(now_ms()); }
  settle(); expect_page(1);
  break;
 case 3:
  swipe(1,140); clock_step(35); nav_tick(now_ms());
  swipe(-1,140); settle(); expect_page(0);
  // A spring-back is not a committed page change.
  swipe(1,25); assert(anim_on && anim_commit == 0);
  ui_key(102,100); settle(); expect_page(1);
  break;
 case 4: {
  page=PG_FUNC; compose(); ui_key(102,100);
  clock_step(80); nav_tick(now_ms()); compose(); assert(anim_on && nhits == 0);
  // The new finger lands on HOME's signal card. Hit boxes from an
  // intermediate frame must not swallow it or invoke an outgoing control.
  ui_touch(T_DOWN,120,140,now_ms()); assert(page == PG_HOME && !anim_on);
  clock_step(10); ui_touch(T_UP,120,140,now_ms());
  expect_page(PG_SIGNAL);
  break;
 }
 case 5:
  // A key supersedes an in-flight touch, whose release must not submit
  // another navigation based on its stale drag coordinates.
  ui_touch(T_DOWN,200,120,now_ms()); clock_step(10);
  ui_touch(T_MOVE,60,120,now_ms()); assert(dragging);
  ui_key(102,100); assert(tc.swallow && !dragging); clock_step(10);
  ui_touch(T_MOVE,55,120,now_ms()); ui_touch(T_UP,55,120,now_ms());
  settle(); expect_page(1);
  break;
 case 6:
  // Slow navigation still has the original 240 ms animation and commits once.
  ui_key(102,100); assert(page == PG_HOME && anim_on);
  clock_step(100); nav_tick(now_ms()); assert(page == PG_HOME && tx_off < 0);
  settle(); expect_page(PG_SIGNAL);
  nav_tick(now_ms()+500); expect_page(PG_SIGNAL);
  break;
 default: assert(0);
 }
 return 0;
}
'''.replace('TEST_RUN', str(base))
        (base/'test.c').write_text(source)
        cls.binary = base/'navigation'
        result = subprocess.run(['gcc', '-std=gnu11', '-O1', '-Wall', '-Wextra',
            '-Werror', '-I', str(NATIVE), str(base/'test.c'), '-lm', '-o', str(cls.binary)],
            capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr)

    def check_case(self, case):
        result = subprocess.run([str(self.binary), str(case)], capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fast_buttons_advance_across_pages_in_both_directions(self):
        self.check_case(1)

    def test_fast_swipes_advance_across_pages_in_both_directions(self):
        self.check_case(2)

    def test_reverse_and_cancelled_drag_do_not_lose_or_add_a_page(self):
        self.check_case(3)

    def test_touch_during_animation_uses_destination_hit_boxes(self):
        self.check_case(4)

    def test_button_supersedes_unreleased_touch(self):
        self.check_case(5)

    def test_normal_animation_commits_once(self):
        self.check_case(6)


if __name__ == '__main__':
    unittest.main()
