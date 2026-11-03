// Data permission only. APN "roaming" is an IP protocol, not this policy.
// Prefer a registered 5GS domain; an unregistered domain cannot hide EPS.
export function registration(eps, nr, slot) {
    for (let state in [nr, eps])
        if (type(state) == 'object' && state.slot === slot &&
            index([1, 5, 9, 10], state.stat) >= 0)
            return state;
    return null;
};
export function blocked(state, allowed) {
    return allowed !== true && type(state) == 'object' &&
        index([5, 10], state.stat) >= 0;
};
export function suspend(call, state, allowed, user_blocked) {
    if (user_blocked || !blocked(state,allowed)) return false;
    let status=call('status',{});
    if (status?.proto!='mu300cell' || status.autostart!==true || status.available===false) return false;
    call('notify_proto',{action:3,error:['ROAMING_DISABLED']});
    return call('notify_proto',{action:5,available:false})!=null;
};
