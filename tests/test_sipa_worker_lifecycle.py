"""Run the real SIPA worker bodies with host pthread wait queues (no MMIO)."""
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SIPA = ROOT / 'upstream/modules/sprd_modem/sipa_v3'


def function(source, name):
    start = re.search(r'(?m)^static (?:int|bool) ' + name + r'\(', source).start()
    pos = source.index('{', start)
    depth = 1
    end = pos + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


class Workers(unittest.TestCase):
    def test_probe_resume_interrupt_and_stop(self):
        cc = shutil.which('cc')
        if not cc:
            self.skipTest('host C compiler required')
        send = (SIPA / 'sipa_skb_send.c').read_text()
        recv = (SIPA / 'sipa_skb_recv.c').read_text()
        harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <unistd.h>
#include <signal.h>
struct queue { pthread_mutex_t mutex; pthread_cond_t cond; };
struct sipa_endpoint { struct {struct {int fifo_depth;} rx_fifo;} send_fifo; };
struct sipa_skb_sender { bool init_flag; struct queue free_waitq;
    bool free_notify_net; atomic_int check_suspend, left_cnt; struct sipa_endpoint *ep; };
struct sipa_skb_receiver { bool init_flag; struct queue fill_recv_waitq; };
static struct queue *q;
static atomic_bool stopping, pending, interrupt_wait;
static atomic_int mmio_reads, work, waits, notifications;
static struct sipa_skb_sender *active_sender;
static bool sipa_sender_can_notify(struct sipa_skb_sender *sender);
#define READ_ONCE(x) __atomic_load_n(&(x), __ATOMIC_ACQUIRE)
#define WRITE_ONCE(x,v) __atomic_store_n(&(x),(v),__ATOMIC_RELEASE)
#define atomic_read(p) atomic_load(p)
#define current 0
#define sched_setscheduler(a,b,c) ((void)0)
#define kthread_should_stop() atomic_load(&stopping)
#define wait_event_interruptible(queue,condition) ({ \
    int rc = 0; pthread_mutex_lock(&(queue).mutex); \
    while (!(condition)) { atomic_fetch_add(&waits,1); \
        pthread_cond_wait(&(queue).cond, &(queue).mutex); \
        if (atomic_exchange(&interrupt_wait,false)) { rc = -1; break; } \
    } pthread_mutex_unlock(&(queue).mutex); rc; })
static bool sipa_sender_ck_unfree(void *unused) {
    atomic_fetch_add(&mmio_reads,1); return !atomic_load(&pending);
}
static bool sipa_check_need_fill_cnt(void) {
    atomic_fetch_add(&mmio_reads,1); return atomic_load(&pending);
}
static void sipa_free_sent_items(void) {
    atomic_store(&pending,false); atomic_fetch_add(&work,1);
    if(active_sender && sipa_sender_can_notify(active_sender)) {
        WRITE_ONCE(active_sender->free_notify_net,false);
        atomic_fetch_add(&notifications,1);
    }
}
static int sipa_fill_all_free_fifo(void) { sipa_free_sent_items(); return 0; }
#define usleep_range(a,b) usleep(a)
'''
        harness += function(send, 'sipa_sender_can_notify') + '\n'
        harness += function(send, 'sipa_free_thread') + '\n'
        harness += function(recv, 'sipa_fill_recv_thread') + '\n'
        harness += r'''
static int (*worker)(void *);
static void *entry(void *arg) { worker(arg); return NULL; }
static void wake(void) {
    pthread_mutex_lock(&q->mutex); pthread_cond_broadcast(&q->cond);
    pthread_mutex_unlock(&q->mutex);
}
static void settle(void) { usleep(30000); }
static void run_case(bool receive, bool resume) {
    struct sipa_skb_sender s = { .init_flag = true };
    struct sipa_skb_receiver r = { .init_flag = true };
    pthread_t thread;
    q = receive ? &r.fill_recv_waitq : &s.free_waitq;
    pthread_mutex_init(&q->mutex,NULL); pthread_cond_init(&q->cond,NULL);
    atomic_store(&stopping,false); atomic_store(&pending,true);
    atomic_store(&interrupt_wait,false); atomic_store(&mmio_reads,0);
    atomic_store(&work,0); atomic_store(&waits,0);
    worker = receive ? sipa_fill_recv_thread : sipa_free_thread;
    assert(!pthread_create(&thread,NULL,entry,receive ? (void*)&r : (void*)&s));
    settle(); assert(atomic_load(&waits)>0);
    wake(); settle();
    atomic_store(&interrupt_wait,true); wake(); settle();
    /* Queued work, spurious wake, and signal cannot touch MMIO before resume. */
    assert(atomic_load(&mmio_reads)==0 && atomic_load(&work)==0);
    if (resume) {
        if (receive) WRITE_ONCE(r.init_flag,false);
        else WRITE_ONCE(s.init_flag,false);
        wake(); settle(); assert(atomic_load(&work)==1);
        assert(atomic_load(&mmio_reads)>0);
        wake(); settle(); assert(atomic_load(&work)==1);
        atomic_store(&pending,true); wake(); settle();
        assert(atomic_load(&work)==2);
    }
    int before = atomic_load(&work);
    atomic_store(&stopping,true); wake();
    assert(!pthread_join(thread,NULL)); assert(atomic_load(&work)==before);
    pthread_cond_destroy(&q->cond); pthread_mutex_destroy(&q->mutex);
}
static void run_flow_case(void) {
    struct sipa_endpoint ep={.send_fifo.rx_fifo.fifo_depth=8};
    struct sipa_skb_sender s={.ep=&ep,.free_notify_net=true};pthread_t thread;
    q=&s.free_waitq;pthread_mutex_init(&q->mutex,NULL);pthread_cond_init(&q->cond,NULL);
    active_sender=&s;worker=sipa_free_thread;
    atomic_store(&stopping,false);atomic_store(&pending,false);
    atomic_store(&interrupt_wait,false);atomic_store(&work,0);atomic_store(&waits,0);
    atomic_store(&notifications,0);
    assert(!pthread_create(&thread,NULL,entry,&s));settle();
    assert(atomic_load(&work)==0 && atomic_load(&waits)>0);
    atomic_store(&s.left_cnt,2);wake();settle();assert(atomic_load(&work)==0);
    atomic_store(&s.left_cnt,3);wake();settle();
    assert(atomic_load(&work)==1 && atomic_load(&notifications)==1 && !s.free_notify_net);
    wake();settle();assert(atomic_load(&work)==1);
    atomic_store(&s.check_suspend,1);WRITE_ONCE(s.free_notify_net,true);
    int reads=atomic_load(&mmio_reads);wake();settle();
    assert(atomic_load(&work)==1 && atomic_load(&mmio_reads)==reads);
    atomic_store(&s.check_suspend,0);wake();settle();
    assert(atomic_load(&work)==2 && atomic_load(&notifications)==2);
    atomic_store(&s.left_cnt,0);atomic_store(&pending,true);wake();settle();
    assert(atomic_load(&work)==3);
    atomic_store(&stopping,true);wake();assert(!pthread_join(thread,NULL));
    active_sender=NULL;pthread_cond_destroy(&q->cond);pthread_mutex_destroy(&q->mutex);
}
int main(void) {
    alarm(5);
    run_case(false,false); run_case(true,false);
    run_case(false,true); run_case(true,true);
    run_flow_case();
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='sipa-workers-') as tmp:
            src = pathlib.Path(tmp) / 'test.c'
            exe = pathlib.Path(tmp) / 'test'
            src.write_text(harness)
            result = subprocess.run([cc, '-std=gnu11', '-pthread', '-O2', str(src),
                                     '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([str(exe)], check=True, timeout=8)

    def test_managed_threads_and_initialization(self):
        for name in ('sipa_skb_send.c', 'sipa_skb_recv.c'):
            source = (SIPA / name).read_text()
            self.assertIn('= kthread_run(', source)
            self.assertIn('devm_add_action_or_reset(', source)
            self.assertIn('kthread_stop(data)', source)
            self.assertNotIn('= kthread_create(', source)


if __name__ == '__main__':
    unittest.main()
