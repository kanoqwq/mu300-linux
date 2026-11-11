"""Compile the SC2355 iwinfo backend, including all private-interface cleanup paths."""
from pathlib import Path
import runpy
import shutil
import shlex
import subprocess
import tempfile
import unittest

from helpers import TOP

patch = runpy.run_path(str(TOP/'openwrt/patch-iwinfo-ap-scan.py'))

HEAD = r'''
#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>
#include <limits.h>
#define IFNAMSIZ 16
#define NL80211_CMD_NEW_INTERFACE 1
#define NL80211_ATTR_IFNAME 2
#define NL80211_ATTR_IFTYPE 3
#define NL80211_IFTYPE_STATION 4
#define NL80211_ATTR_SOCKET_OWNER 5
struct nl80211_msg_conveyor { void *msg; };
static struct nl80211_msg_conveyor req;
static int collision, allocation_failure, attribute_failure, send_failure, mac_ok, up_ok, scan_result;
static unsigned int sent, freed, up, down, deleted, scanned, owned;
static char scan_name[16];
static const char *module;
static int test_getpid(void) { return 42; }
#define getpid test_getpid
static ssize_t test_readlink(const char *path, char *out, size_t size) {
    assert(strstr(path,"/sys/class/net/wlan0/phy80211/device/driver/module"));
    if (!module) return -1;
    size_t n=strlen(module); if(n>size)n=size;memcpy(out,module,n);return n;
}
#define readlink test_readlink
static unsigned int if_nametoindex(const char *name) { assert(strcmp(name,"wlan0"));return collision; }
static struct nl80211_msg_conveyor *nl80211_msg(const char *name, int cmd, int flags) {
    assert(!strcmp(name,"wlan0") && cmd==NL80211_CMD_NEW_INTERFACE);
    req.msg=&req;return allocation_failure ? NULL : &req;
}
#define NLA_PUT_STRING(m,k,s) do { if(attribute_failure)goto nla_put_failure;snprintf(scan_name,sizeof(scan_name),"%s",s); } while(0)
#define NLA_PUT_U32(m,k,v) do { assert((v)==NL80211_IFTYPE_STATION); } while(0)
#define NLA_PUT_FLAG(m,k) do { assert((k)==NL80211_ATTR_SOCKET_OWNER);owned=1; } while(0)
static int nl80211_send(struct nl80211_msg_conveyor *r, void *cb, void *arg) { ++sent;return send_failure; }
static void nl80211_free(struct nl80211_msg_conveyor *r) { ++freed; }
static int iwinfo_ifmac(const char *name) { assert(!strcmp(name,scan_name));return mac_ok; }
static int iwinfo_ifup(const char *name) { assert(!strcmp(name,scan_name));++up;return up_ok; }
static int iwinfo_ifdown(const char *name) { assert(!strcmp(name,scan_name));++down;return 1; }
static void nl80211_ifdel(const char *name) { assert(!strcmp(name,scan_name));++deleted; }
static int nl80211_get_scanlist_nl(const char *name,char *buf,int *len) {
    assert(!strcmp(name,scan_name));++scanned;*len=scan_result?0:7;return scan_result;
}
'''
CASES = r'''
int main(void) {
    module="../../module/sprd_wlan_combo";assert(nl80211_sprd_scan_device("wlan0"));
    module="../../module/other";assert(!nl80211_sprd_scan_device("wlan0"));
    module=NULL;assert(!nl80211_sprd_scan_device("wlan0"));
    assert(!nl80211_sprd_scan_device("../../wlan0"));
    assert(!nl80211_sprd_scan_device("interface-name-too-long"));
    for(int stage=0;stage<8;++stage) {
        collision=stage==1;allocation_failure=stage==2;attribute_failure=stage==3;
        send_failure=stage==4;mac_ok=stage!=5;up_ok=stage!=6;scan_result=stage==7?-1:0;
        sent=freed=up=down=deleted=scanned=owned=0;int len=0;char buf[32];
        int ret=nl80211_sprd_ap_scan("wlan0",buf,&len);
        if(stage==0){assert(!ret && len==7 && owned && scanned==1 && down==1 && deleted==1);}
        else assert(ret==-1);
        if(stage==1||stage==2)assert(!sent && !deleted && !up);
        if(stage==3)assert(freed==1 && !sent && !deleted);
        if(stage==4)assert(sent==1 && !deleted && !up);
        if(stage==5)assert(deleted==1 && !up && !down && !scanned);
        if(stage==6)assert(deleted==1 && up==1 && !down && !scanned);
        if(stage==7)assert(deleted==1 && down==1 && scanned==1);
    }
    return 0;
}
'''


class Patch(unittest.TestCase):
    @unittest.skipUnless(shutil.which('sh'), 'Requires a POSIX shell')
    def test_container_rootfs_shell_parses(self):
        source = (TOP/'openwrt/build-rootfs.sh').read_text()
        start = source.index("/bin/sh -eu -c '")
        def body(text):
            lexer = shlex.shlex(text[start:], posix=True)
            lexer.whitespace_split = True
            lexer.commenters = ''
            return [next(lexer) for _ in range(4)][3]
        result = subprocess.run(['sh','-n'], input=body(source), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        broken = source.replace('echo "AP scan library missing from rootfs"',
                                "echo 'AP scan library missing from rootfs'")
        self.assertNotEqual(subprocess.run(['sh','-n'], input=body(broken), text=True,
                                          capture_output=True).returncode, 0)

    def test_dispatch_is_explicit_and_idempotent(self):
        original = '''static int nl80211_get_scanlist(const char *ifname, char *buf, int *len)
{
\t/* WPA supplicant */
\treturn 0;
}
static int nl80211_get_freqlist_cb(void) { return 0; }
'''
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'iwinfo_nl80211.c';path.write_text(original)
            patch['apply'](d);first=path.read_text();patch['apply'](d)
            self.assertEqual(path.read_text(),first)
            # Upgrade an earlier generated helper without duplicating dispatch.
            path.write_text(first.replace('/phy80211/device/driver/module', '/device/driver/module'))
            patch['apply'](d)
            self.assertEqual(path.read_text(),first)
            self.assertIn('mode == IWINFO_OPMODE_MASTER',first)
            self.assertIn('return nl80211_sprd_ap_scan(ifname, buf, len);',first)
            helper=patch['HELPERS']
            self.assertNotIn('iwinfo_ifdown(ifname)',helper)
            self.assertNotIn('nl80211_hostapd_hup',helper)
            self.assertIn('NL80211_ATTR_SOCKET_OWNER',helper)

    @unittest.skipUnless(shutil.which('cc'),'Requires a native C compiler')
    def test_owned_interface_and_failure_paths(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'test.c';exe=Path(d)/'test'
            source.write_text(HEAD+patch['HELPERS']+CASES)
            subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-unused-parameter',
                            str(source),'-o',str(exe)],check=True)
            subprocess.run([str(exe)],check=True)


if __name__=='__main__':
    unittest.main()
