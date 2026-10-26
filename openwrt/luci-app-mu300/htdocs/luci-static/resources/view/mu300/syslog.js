'use strict';
'require tools.views as views';

/* Status > System Log, lighter (menu.d/luci-mu300-status-overrides.json replaces LuCI's action with this one): LuCI's
 * own page, with 250 lines instead of 1000 and a refresh every 15 s instead of every poll (5 s), none while the tab is
 * hidden. Reading the log in rpcd and formatting it in the browser was measured as a load on this device while the
 * modem's AT round trips stretched; the page itself is unchanged. */

var LogPage = views.LogreadBox(null, null);
var EVERY_MS = 15000;

return LogPage.extend({
	fetchMaxRows: 250,

	pollLog: function() {
		var now = Date.now();
		if (document.hidden || (this._last && now - this._last < EVERY_MS))
			return Promise.resolve();
		this._last = now;
		return this.super('pollLog', arguments);
	}
});
