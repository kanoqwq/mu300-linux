#!/usr/bin/env python3
"""Use a private STA scan context for SC2355 APs; preserve the running AP.

Applies to OpenWrt iwinfo f5dd57a8 (GPL-2.0). Its generic MASTER fast path
requests ACS from SC2355 firmware, not discovery of nearby BSSes.
"""
from pathlib import Path

HELPERS = r'''
/* MU300: AP firmware scans return ACS, so use an owned discovery context. */
static int nl80211_sprd_scan_device(const char *ifname)
{
	char path[PATH_MAX], target[PATH_MAX];
	ssize_t size;
	char *module;

	if (strlen(ifname) >= IFNAMSIZ || strchr(ifname, '/'))
		return 0;
	/* The netdev parent is a misc device; the PHY owns the platform driver. */
	snprintf(path, sizeof(path), "/sys/class/net/%s/phy80211/device/driver/module", ifname);
	size = readlink(path, target, sizeof(target) - 1);
	if (size < 0)
		return 0;
	target[size] = 0;
	module = strrchr(target, '/');
	return module && !strcmp(module + 1, "sprd_wlan_combo");
}

static int nl80211_sprd_ap_scan(const char *ifname, char *buf, int *len)
{
	struct nl80211_msg_conveyor *req;
	char scanif[IFNAMSIZ];
	int rv = -1;

	/* A process-private name; never reuse/delete another caller's interface. */
	snprintf(scanif, sizeof(scanif), "m3s%08x", (unsigned int)getpid());
	if (if_nametoindex(scanif))
		return -1;
	req = nl80211_msg(ifname, NL80211_CMD_NEW_INTERFACE, 0);
	if (!req)
		return -1;
	NLA_PUT_STRING(req->msg, NL80211_ATTR_IFNAME, scanif);
	NLA_PUT_U32(req->msg, NL80211_ATTR_IFTYPE, NL80211_IFTYPE_STATION);
	/* Kernel deletes the interface too if the caller dies during a scan. */
	NLA_PUT_FLAG(req->msg, NL80211_ATTR_SOCKET_OWNER);
	if (nl80211_send(req, NULL, NULL))
		return -1;
	if (iwinfo_ifmac(scanif) && iwinfo_ifup(scanif))
	{
		rv = nl80211_get_scanlist_nl(scanif, buf, len);
		iwinfo_ifdown(scanif);
	}
	nl80211_ifdel(scanif);
	return rv;

nla_put_failure:
	nl80211_free(req);
	return -1;
}

'''


def apply(root):
    path = Path(root)/'iwinfo_nl80211.c'
    source = path.read_text()
    if HELPERS in source:
        return
    anchor = 'static int nl80211_get_scanlist(const char *ifname, char *buf, int *len)'
    marker = '/* MU300: AP firmware scans return ACS, so use an owned discovery context. */'
    if marker in source:
        first = source.index(marker)
        last = source.index(anchor, first)
        owned = source[first:last]
        if source.count(marker) != 1 or owned.count('static int ') != 2:
            raise ValueError('Unexpected previously generated AP scan helpers')
        source = source[:first].removesuffix('\n') + source[last:]
    begin = source.index(anchor)
    end = source.index('\nstatic int nl80211_get_freqlist_cb(', begin)
    body = source[begin:end]
    old = '\t/* WPA supplicant */'
    new = '''\t/* Explicit SC2355 AP backend; do not fall back to AP teardown. */
\tif (!nl80211_get_mode(ifname, &mode) && mode == IWINFO_OPMODE_MASTER &&
\t    nl80211_sprd_scan_device(ifname))
\t\treturn nl80211_sprd_ap_scan(ifname, buf, len);

''' + old
    if body.count(old) != 1 or source.count(anchor) != 1:
        raise ValueError('Unexpected pinned iwinfo scan implementation')
    if new not in body:
        body = body.replace(old, new, 1)
    path.write_text(source[:begin] + HELPERS + body + source[end:])


if __name__ == '__main__':
    import sys
    apply(sys.argv[1])
