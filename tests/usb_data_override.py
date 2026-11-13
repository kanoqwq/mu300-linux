"""Execute the driver's policy against simultaneous Type-C data-role events."""
from pathlib import Path
import runpy,subprocess,tempfile

top=Path(__file__).resolve().parents[1]
function=runpy.run_path(str(top/'tests/test_u30air_pd.py'))['function']
text=(top/'upstream/port/drivers/usb/typec/tcpm/sc27xx-ump9620-role.h').read_text()
before=r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <pthread.h>
#define READ_ONCE(x) (x)
enum usb_role {USB_ROLE_NONE,USB_ROLE_HOST,USB_ROLE_DEVICE};
struct sc27xx_pd {void *role_sw;pthread_mutex_t role_lock;enum usb_role data_override,negotiated_role;bool stopping;};
static enum usb_role actual;
static int calls,error;
static void mutex_lock(pthread_mutex_t *m){assert(!pthread_mutex_lock(m));}
static void mutex_unlock(pthread_mutex_t *m){assert(!pthread_mutex_unlock(m));}
static int usb_role_switch_set_role(void *sw,enum usb_role role){(void)sw;calls++;if(error)return error;actual=role;return 0;}
'''
after=r'''
static void *events(void *arg) {
 struct sc27xx_pd *p=arg;enum usb_role applied;
 for(int i=0;i<500;i++) {
  assert(!sc27xx_apply_data_role(p,i&1?USB_ROLE_HOST:USB_ROLE_DEVICE,&applied));
  assert(applied==p->data_override);
 }
 return NULL;
}
int main(void) {
 struct sc27xx_pd p={.role_lock=PTHREAD_MUTEX_INITIALIZER,.negotiated_role=USB_ROLE_DEVICE};
 enum usb_role applied;
 assert(!sc27xx_apply_data_role(&p,USB_ROLE_HOST,&applied)&&applied==USB_ROLE_HOST);
 for(enum usb_role mode=USB_ROLE_HOST;mode<=USB_ROLE_DEVICE;mode++) {
  assert(!sc27xx_set_data_override(&p,mode));
  pthread_t a,b;assert(!pthread_create(&a,NULL,events,&p));assert(!pthread_create(&b,NULL,events,&p));
  assert(!pthread_join(a,NULL));assert(!pthread_join(b,NULL));
  assert(!sc27xx_apply_data_role(&p,USB_ROLE_DEVICE,&applied)&&applied==mode);
  assert(actual==mode&&p.negotiated_role==USB_ROLE_DEVICE);
  /* Releasing force applies the latest negotiated role, not its old snapshot. */
  assert(!sc27xx_set_data_override(&p,USB_ROLE_NONE)&&actual==USB_ROLE_DEVICE);
 }
 assert(!sc27xx_set_data_override(&p,USB_ROLE_HOST));
 error=-EIO;
 assert(sc27xx_set_data_override(&p,USB_ROLE_DEVICE)==-EIO&&p.data_override==USB_ROLE_HOST);
 error=0;p.stopping=true;int old=calls;
 assert(sc27xx_set_data_override(&p,USB_ROLE_DEVICE)==-ESHUTDOWN&&calls==old);
 assert(!sc27xx_apply_data_role(&p,USB_ROLE_DEVICE,&applied)&&applied==USB_ROLE_DEVICE);
 return 0;
}
'''
with tempfile.TemporaryDirectory() as d:
    source=Path(d)/'override.c';binary=Path(d)/'override'
    source.write_text(before+function(text,'sc27xx_apply_data_role')+function(text,'sc27xx_set_data_override')+after)
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-pthread',str(source),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
print('PASS: concurrent attach/PD events preserve force; auto uses latest role; failures and shutdown retain ownership')
