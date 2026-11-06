#!/usr/bin/env python3
"""Compile real RM and netdev callbacks; exercise grant/stop ordering offline."""
import os
from pathlib import Path
import re
import subprocess
import tempfile

def function(source,name):
 m=re.search(r'^(?:static )?(?:int|bool|void|netdev_tx_t) '+re.escape(name)+r'\([^;]*?\)\s*\{',source,re.M)
 if not m:raise AssertionError(name)
 at=m.end();depth=1
 while depth:depth+=(source[at]=='{')-(source[at]=='}');at+=1
 return source[m.start():at]

STUBS=r"""
#include <assert.h>
#include <stdbool.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <unistd.h>
typedef pthread_mutex_t spinlock_t;
static spinlock_t queue_lock=PTHREAD_MUTEX_INITIALIZER;
static _Thread_local int grant_thread;
static atomic_int attempted,finished;
static void lock(spinlock_t *p){if(p==&queue_lock && grant_thread)atomic_store(&attempted,1);assert(!pthread_mutex_lock(p));}
#define spin_lock_irqsave(p,f) do{f=0;lock(p);}while(0)
#define spin_unlock_irqrestore(p,f) do{(void)f;assert(!pthread_mutex_unlock(p));}while(0)
#define NIC_OPEN 0
#define atomic_read(p) (*(p))
#define pr_info(...) ((void)0)
struct net_device {atomic_int stopped;const char *name;void *priv;};
struct sk_buff {unsigned len;};
struct net_device_stats {unsigned tx_dropped,tx_errors,tx_packets,tx_bytes;};
struct sipa_usb_init_data {int netid,src_id;};
struct sipa_eth {struct net_device *netdev;};
struct sipa_usb {struct net_device *ndev;struct sipa_usb_init_data *pdata;struct net_device_stats *stats;int state,nic_id;};
static int netif_queue_stopped(struct net_device *d){return atomic_load(&d->stopped);}
static void netif_stop_queue(struct net_device *d){atomic_store(&d->stopped,1);}
static void netif_wake_queue(struct net_device *d){atomic_store(&d->stopped,0);}
struct sipa_nic_cons_res {spinlock_t lock;bool resource_requested,need_request,request_in_progress,rm_flow_ctrl;int cons;};
struct sipa_nic {int status;struct sipa_nic_cons_res rm_res;};
struct device {void *pm_domain;};
struct sipa_plat_drv_cfg {bool sipa_sys_eb;struct device *dev;};
static struct device dev={.pm_domain=(void *)1};
static struct sipa_plat_drv_cfg ipa={.sipa_sys_eb=true,.dev=&dev};
static struct sipa_plat_drv_cfg *sipa_get_ctrl_pointer(void){return &ipa;}
static void pm_stay_awake(struct device *d){(void)d;}
static struct sipa_nic *active;
static int early,result;
static void grant(void);
static int sipa_rm_request_resource(int cons){(void)cons;if(early)grant();return result;}
typedef int netdev_tx_t;
#define DEV_ON 1
#define NETDEV_TX_OK 0
#define NETDEV_TX_BUSY 16
#define unlikely(v) (v)
#define pr_err(...) ((void)0)
static int linearize_result,tx_result,freed,recovery;
static void *netdev_priv(struct net_device *d){return d->priv;}
static int skb_linearize(struct sk_buff *s){(void)s;return linearize_result;}
static void dev_kfree_skb_any(struct sk_buff *s){(void)s;freed++;}
static void netif_carrier_off(struct net_device *d){(void)d;}
static int sipa_nic_tx(int id,int src,int netid,struct sk_buff *s){(void)id;(void)src;(void)netid;(void)s;return tx_result;}
static bool sipa_nic_check_flow_ctrl(int id){(void)id;return active->rm_res.rm_flow_ctrl;}
static void sipa_nic_trigger_flow_ctrl_work(int id,int error){(void)id;(void)error;assert(!pthread_mutex_trylock(&queue_lock));assert(!pthread_mutex_unlock(&queue_lock));recovery++;}

"""
MAIN=r"""
static struct net_device net={.name="test"};static struct sipa_eth eth={.netdev=&net};static struct sipa_usb usb={.ndev=&net};
static void grant(void){(void)sipa_nic_rm_res_granted(active);}
static int usb_case;
static void *notify(void *unused){(void)unused;grant_thread=1;grant();if(usb_case)sipa_usb_flowctrl_handler(&usb,0);else sipa_eth_flowctrl_handler(&eth,0);atomic_store(&finished,1);return NULL;}
int main(int argc,char **argv){
 assert(argc==2);int test=atoi(argv[1]);struct sipa_nic nic={0};assert(!pthread_mutex_init(&nic.rm_res.lock,NULL));nic.rm_res.need_request=true;active=&nic;
 if(test<4){
  result=(test==0)?0:(test==3)?-EIO:-EINPROGRESS;early=test==1;
  int r=sipa_nic_rm_res_request(&nic);
  if(test==2)assert(r==-EINPROGRESS && nic.rm_res.request_in_progress && nic.rm_res.rm_flow_ctrl);
  else if(test==3)assert(r==-EIO && nic.rm_res.need_request && !nic.rm_res.rm_flow_ctrl);
  else assert(r==0 && !nic.rm_res.request_in_progress && !nic.rm_res.rm_flow_ctrl);
 }else if(test==6){
  struct sipa_usb_init_data pdata={0};struct net_device_stats stats={0};struct sk_buff skb={.len=100};
  usb.pdata=&pdata;usb.stats=&stats;usb.state=DEV_ON;net.priv=&usb;
  tx_result=-EINPROGRESS;assert(sipa_usb_start_xmit(&skb,&net)==NETDEV_TX_BUSY && !netif_queue_stopped(&net) && !freed);
  nic.rm_res.rm_flow_ctrl=true;assert(sipa_usb_start_xmit(&skb,&net)==NETDEV_TX_BUSY && netif_queue_stopped(&net) && !freed);
  grant();sipa_usb_flowctrl_handler(&usb,0);tx_result=-EAGAIN;
  assert(sipa_usb_start_xmit(&skb,&net)==NETDEV_TX_BUSY && netif_queue_stopped(&net) && !freed && recovery==3);
  tx_result=-EIO;assert(sipa_usb_start_xmit(&skb,&net)==NETDEV_TX_OK && freed==1 && !stats.tx_packets);
  linearize_result=-ENOMEM;assert(sipa_usb_start_xmit(&skb,&net)==NETDEV_TX_OK && freed==2);
  linearize_result=0;tx_result=0;assert(sipa_usb_start_xmit(&skb,&net)==NETDEV_TX_OK && stats.tx_packets==1 && stats.tx_bytes==100);
 }else{
  usb_case=test==5;nic.rm_res.rm_flow_ctrl=true;nic.rm_res.request_in_progress=true;
  pthread_t thread;lock(&queue_lock);assert(!pthread_create(&thread,NULL,notify,NULL));
  for(int i=0;!atomic_load(&attempted) && !atomic_load(&finished) && i<10000;i++)usleep(100);
  assert(atomic_load(&attempted)||atomic_load(&finished));
  /* TX read pending=true before the grant cleared it. Stop while holding its
   * queue lock, then the grant callback must still wake that stopped queue. */
  netif_stop_queue(&net);assert(!pthread_mutex_unlock(&queue_lock));assert(!pthread_join(thread,NULL));
  assert(!netif_queue_stopped(&net));
 }
 puts("RM completion / netdev wake ordering passed");return 0;
}
"""

def run(base):
 if os.name!='posix':raise RuntimeError('POSIX fixture required')
 base=Path(base);nic=(base/'sipa_nic.c').read_text();eth=(base/'sipa_eth.c').read_text();usb=(base/'sipa_usb.c').read_text()
 body='\n'.join([function(nic,'sipa_nic_rm_res_granted'),function(nic,'sipa_nic_rm_res_request'),function(eth,'sipa_eth_flowctrl_handler'),function(usb,'sipa_usb_flowctrl_handler'),function(usb,'sipa_usb_start_xmit')])
 with tempfile.TemporaryDirectory(prefix='sipa-flow-order-') as d:
  p=Path(d);src=p/'flow.c';out=p/'flow';src.write_text(STUBS+body+MAIN)
  subprocess.run(['cc','-std=gnu11','-O1','-g','-pthread','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-unused-function','-fsanitize=address,undefined','-fno-pie','-no-pie',str(src),'-o',str(out)],check=True)
  for case in range(7):subprocess.run([str(out),str(case)],check=True,timeout=8)
 print('PASS: immediate/early/pending/failed resource grants and deterministic Ethernet/USB late-stop races')

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('source',nargs='?',type=Path);a=p.parse_args()
 run(a.source or Path(__file__).resolve().parents[1]/'upstream/modules/sprd_modem/sipa_v3')
