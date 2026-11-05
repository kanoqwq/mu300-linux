// Pure policy shared with offline tests. State comes from the existing AT
// owner, not a second modem manager or a guessed time-based registration.
export function radio_blocked(frozen, ready) {
    // U30 native SIM management keeps freeze for the whole boot: it inhibits
    // the legacy SFUN bring-up, not PDP dialing after native RF is ready.
    return frozen && !ready;
};

export function registration_key(state, slot) {
    if (type(state) != 'object' || state.slot !== slot ||
        index([1,5,9,10],state.stat) < 0)
        return null;
    if (state.rat != null && (type(state.rat) != 'int' || state.rat < 0 || state.rat >= 255))
        return null;
    return sprintf('%d:%d:%s', slot, state.stat, state.rat ?? 'eps');
};

export function can_resume(status, wait_slot, selected, blocked, device_ready) {
    if (blocked || type(status) != 'object' || status.proto != 'mu300cell' ||
        status.autostart !== true || status.up === true || status.pending === true ||
        status.available !== false || wait_slot !== selected)
        return false;
    for (let error in status.errors ?? [])
        if (error.code != 'REGISTRATION_WAIT' && error.code != 'SIM_MISSING' && error.code != 'ROAMING_DISABLED' &&
            !(error.code == 'NO_DEVICE' && device_ready === true))
            return false;
    return true;
};

export function recovery(call, state, wait_slot, selected, blocked, previous, sim, device_present) {
    let status = call('status', {});
    let same_sim = type(sim) == 'object' && sim.slot === selected;
    if (same_sim && sim.state == 'absent') {
        previous.key = null; previous.attempted = false;
        // Stop setup/renew attempts without overriding a user's ifdown or
        // cycling RF. The modem remains able to receive insertion events.
        if (!blocked && status?.proto == 'mu300cell' && status.autostart === true && status.available !== false)
            return call('notify_proto', { action: 5, available: false }) != null;
        return false;
    }
    let key = registration_key(state, selected);
    for (let error in status?.errors ?? [])
        if (error.code == 'SIM_MISSING' && same_sim && sim.state == 'ready')
            key = sprintf('sim:%d:ready', selected);
    if (key != previous.key) {
        previous.key = key;
        previous.attempted = false;
    }
    if (status?.up === true)
        previous.attempted = false;
    if (key == null || previous.attempted ||
        !can_resume(status, wait_slot, selected, blocked,
                    device_present === true && same_sim && sim.state == 'ready' && registration_key(state, selected) != null))
        return false;
    // Set before notifying: the resulting netifd event must not loop.
    previous.attempted = true;
    return call('notify_proto', { action: 5, available: true }) != null;
};
