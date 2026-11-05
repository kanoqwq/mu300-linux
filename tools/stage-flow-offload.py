#!/usr/bin/env python3
"""Stage fw4 software-flowtable topology discovery without changing user policy."""
import argparse
import os
from pathlib import Path
import stat

# OpenWrt firewall4 25.12: migrate only this owned function, never overwrite fw4.
STOCK = '''	resolve_offload_devices: function() {
		if (!this.default_option("flow_offloading"))
			return [];

		let devices = this.resolve_hw_offload_devices();

		if (!devices) {
			devices = [];

			for (let zone in this.zones())
				for (let device in zone.related_physdevs)
					if (fs.access(`/sys/class/net/${device}`))
						push(devices, device);

			devices = sort(uniq(devices));
		}

		return devices;
	},'''
LEGACY_ADDITION = '''			/* mu300cell exposes sipa_eth0 as l3_device only, so fw4's
			 * physical-device discovery misses its software ingress hook. */
			if (fs.access('/sys/class/net/sipa_eth0'))
				push(devices, 'sipa_eth0');

'''
LEGACY = STOCK.replace('\t\t\tdevices = sort(uniq(devices));',
                       LEGACY_ADDITION + '\t\t\tdevices = sort(uniq(devices));')
FIXED = r'''	resolve_offload_devices: function() {
		// unisoc-flowtable-ports-v1: use actual bridge/VLAN ingress ports.
		if (!this.default_option("flow_offloading"))
			return [];

		let devices = this.resolve_hw_offload_devices();

		if (!devices) {
			let bus = ubus.connect();
			if (!bus)
				die('Unable to read flowtable device topology from ubus\n');
			let devstatus;
			try {
				devstatus = bus.call("network.device", "status");
			}
			catch (e) {
				bus.disconnect();
				die(`Unable to read flowtable device topology: ${e}\n`);
			}
			bus.disconnect();
			if (type(devstatus) != "object")
				die('Invalid flowtable device topology from netifd\n');

			let selected = [];
			for (let zone in this.zones()) {
				push(selected, ...(zone.related_physdevs || []));
				// Include active L3-only protocols in their configured zones.
				// This follows SIM/PDP changes without pinning a SIPA index.
				for (let e in (zone.network || [])) {
					let net = this.state.networks[e.device];
					if (!e.invert && net?.up && net.device)
						push(selected, net.device);
				}
			}

			devices = [];
			for (let device in uniq(selected))
				push(devices, ...resolve_lower_devices(devstatus, device));
			devices = sort(uniq(devices));
		}

		return devices;
	},'''


def transform(source):
    """An explicit source migration, not a runtime choice between backends."""
    if source.count('\tresolve_offload_devices: function() {') != 1:
        raise ValueError('Unsupported firewall4 flowtable function layout')
    if FIXED in source:
        return source
    for before in (LEGACY, STOCK):
        if before in source:
            return source.replace(before, FIXED, 1)
    raise ValueError('Unsupported firewall4 flowtable implementation; review upstream changes')


def stage(root):
    root = Path(root).resolve()
    module = root/'usr/share/ucode/fw4.uc'
    if module.is_symlink() or not module.resolve().is_relative_to(root):
        raise ValueError('firewall4 module escapes root')
    original = module.read_text(encoding='utf8')
    updated = transform(original)
    if updated == original:
        return False
    pending = module.with_name(module.name+'.unisoc-new')
    if pending.exists() or pending.is_symlink():
        raise ValueError('Unfinished firewall4 source staging: '+str(pending))
    mode = stat.S_IMODE(module.stat().st_mode)
    created = False
    try:
        with pending.open('x', encoding='utf8', newline='\n') as stream:
            created = True
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        pending.chmod(mode)
        pending.replace(module)
    except BaseException:
        if created:
            pending.unlink(missing_ok=True)
        raise
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    print('fw4 topology discovery: '+('updated' if stage(args.root) else 'current'))
