#!/usr/bin/ucode
// SPDX-License-Identifier: MIT
// One private daemon feed, delivered by uhttpd's native ubus SSE handler.
import * as socket from 'socket';
import * as uloop from 'uloop';
import * as ubus from 'ubus';

const PATH = getenv('UNISOC_BT_EVENTS_SOCKET') || '/run/unisoc-bluetooth/control.sock';
const NAME = 'unisoc-bluetooth-events';
const AUTH_LEASE = 30000;
uloop.init();
const bus = ubus.connect(null, 2);
if (!bus) die('Bluetooth events: ubus unavailable\n');
let object, peer, reader, buffer = '', sequence = 0, event_id = '';
let lease_armed = false;
let retry, deadline, lease;

function close_peer() {
    deadline.cancel();
    if (reader) { reader.delete(); reader = null; }
    if (peer) { peer.close(); peer = null; }
    buffer = '';
}

function connect_peer() {
    if (!object.subscribed() || peer) return;
    peer = socket.connect({ path: PATH }, null, null, 100);
    if (!peer) { retry.set(1000); return; }
    let request = sprintf('%J\n', {action:'events', after:sequence, event_id, wait:20});
    if (peer.send(request, socket.MSG_NOSIGNAL) != length(request)) {
        close_peer(); retry.set(1000); return;
    }
    deadline.set(27000);
    reader = uloop.handle(peer, function() {
        let chunk = peer.recv(16384, socket.MSG_DONTWAIT);
        if (chunk == null || !length(chunk)) { close_peer(); retry.set(1000); return; }
        buffer += chunk;
        if (length(buffer) > 262144) { close_peer(); retry.set(1000); return; }
        let newline = index(buffer, '\n');
        if (newline < 0) return;
        let text = substr(buffer, 0, newline), packet;
        close_peer();
        try { packet = json(text); } catch(e) { retry.set(1000); return; }
        if (packet.ok !== true || type(packet.sequence) != 'int' ||
            type(packet.event_id) != 'string' || type(packet.events) != 'array') {
            retry.set(1000); return;
        }
        sequence = packet.sequence; event_id = packet.event_id;
        // Large raw GATT payloads must not disconnect a bus client. The UI
        // resyncs status; measurements/forwarding remain owned by the daemon.
        if (length(text) > 131072)
            packet = {sequence, event_id, events:[], resync:true};
        if (object.subscribed()) object.notify('update', packet);
        retry.set(0);
    }, uloop.ULOOP_READ);
    if (!reader) { close_peer(); retry.set(1000); }
}

function subscriptions() {
    if (object.subscribed()) {
        if (!lease_armed) { lease.set(AUTH_LEASE); lease_armed = true; }
        retry.set(0);
    } else {
        close_peer(); retry.cancel(); lease.cancel(); lease_armed = false;
    }
}

function publish() {
    object = bus.publish(NAME, {}, subscriptions);
    if (!object) die('Bluetooth events: could not publish ubus object\n');
}
retry = uloop.timer(-1, connect_peer);
deadline = uloop.timer(-1, function() { close_peer(); retry.set(1000); });
lease = uloop.timer(-1, function() {
    // Native SSE checks access on subscription. End the lease server-side so
    // an expired/revoked session cannot retain an indefinitely open stream.
    close_peer();
    object.remove();
    lease_armed = false;
    publish();
});
publish();
uloop.run();
