// The control panel's live rates (Status dashboard): the kernel's interface counters, every second. In rpcd itself
// (rpcd-mod-ucode), so no process starts and nothing asks the modem. One fixed file and no argument: the panel
// needs no file read access for it (a path glob on /proc/<pid>/... would have let the session read more than this).
'use strict';

import { readfile } from 'fs';

return {
	mu300rates: {
		netdev: {
			call: function() {
				return { data: readfile('/proc/net/dev') ?? '' };
			}
		}
	}
};
