// The adb transport over WebUSB. It takes a USBDevice as the WebUSB API defines it, so the same code runs in the
// browser (navigator.usb) and in Node with the `usb` package, whose `webusb` object implements that API
// (tests/web/README.md says how).
//
// What USB adds to the protocol (AOSP adb/client/usb_*.cpp, and the same in ya-webadb's WebUSB backend):
//   * adbd sends every packet as two transfers, the 24-byte header and the payload. The header is read with a
//     request of one endpoint packet, the payload with a request of exactly its length, so no read ever waits for
//     an end mark that does not come.
//   * a write whose length is a whole number of endpoint packets must be followed by a zero-length packet, or the
//     device's controller keeps waiting for the rest of the transfer.
//   * the interface is found by its class (ff/42/01), never by vendor or product id: Android's adbd and the adbd of
//     MU300 Linux (FINDINGS 43) both announce it that way.
import { AdbError } from "./errors.js";

export const ADB_INTERFACE = { classCode: 0xff, subclassCode: 0x42, protocolCode: 0x01 };
// for navigator.usb.requestDevice: any device with an adb interface
export const ADB_FILTERS = [ADB_INTERFACE];

// { configuration, interface, alternate, inEndpoint, outEndpoint } of the device's adb interface, or null
export function findAdbInterface(device) {
  for (const configuration of device.configurations || []) {
    for (const iface of configuration.interfaces || []) {
      for (const alternate of iface.alternates || []) {
        if (alternate.interfaceClass !== ADB_INTERFACE.classCode || alternate.interfaceSubclass !== ADB_INTERFACE.subclassCode ||
            alternate.interfaceProtocol !== ADB_INTERFACE.protocolCode) continue;
        let inEndpoint = null, outEndpoint = null;
        for (const ep of alternate.endpoints || []) {
          if (ep.type !== "bulk") continue;
          if (ep.direction === "in" && !inEndpoint) inEndpoint = ep;
          if (ep.direction === "out" && !outEndpoint) outEndpoint = ep;
        }
        if (inEndpoint && outEndpoint) return { configuration, interface: iface, alternate, inEndpoint, outEndpoint };
      }
    }
  }
  return null;
}

const errName = (e) => (e && e.name) || "";

// What a failed open() or claimInterface() means, as far as the browser lets us tell. `platform` is
// "windows" | "mac" | "linux" | "android" | "other" (the caller reads it from the user agent).
export function classifyOpenError(e, stage, platform) {
  const n = errName(e);
  if (n === "NotFoundError") return new AdbError("disconnected", "the device was unplugged", e);
  if (stage === "claim") {
    // Chrome answers "Unable to claim interface" (NetworkError) when another program has it: a running adb server
    return new AdbError("busy", "another program is using the device's adb interface", e);
  }
  if (n === "SecurityError" || n === "NetworkError" || n === "InvalidStateError") {
    // open() itself failed. Windows: the interface has no WinUSB driver. Linux: the device node is not writable
    // for this user (no udev rule). macOS: rare, another program opened the device exclusively.
    const err = new AdbError("access", "the browser could not open the device", e);
    err.platform = platform || "other";
    return err;
  }
  return new AdbError("access", "the browser could not open the device", e);
}

export function platformOf(nav = globalThis.navigator) {
  const s = ((nav && (nav.userAgentData && nav.userAgentData.platform)) || (nav && nav.platform) || (nav && nav.userAgent) || "").toLowerCase();
  if (/android/.test((nav && nav.userAgent || "").toLowerCase())) return "android";
  if (s.includes("win")) return "windows";
  if (s.includes("mac")) return "mac";
  if (s.includes("linux") || s.includes("cros") || s.includes("chrome os")) return "linux";
  return "other";
}

// Opens the device and claims its adb interface. Returns the transport connection.js needs.
export async function openUsbTransport(device, platform) {
  const found = findAdbInterface(device);
  if (!found) throw new AdbError("no-interface", "this USB device has no adb interface");
  try {
    if (!device.opened) await device.open();
  } catch (e) { throw classifyOpenError(e, "open", platform); }
  try {
    if (!device.configuration || device.configuration.configurationValue !== found.configuration.configurationValue) {
      await device.selectConfiguration(found.configuration.configurationValue);
    }
  } catch (e) { throw classifyOpenError(e, "open", platform); }
  try {
    await device.claimInterface(found.interface.interfaceNumber);
  } catch (e) {
    try { await device.close(); } catch (e2) { /* nothing to close */ }
    throw classifyOpenError(e, "claim", platform);
  }
  try {
    if (found.alternate.alternateSetting) await device.selectAlternateInterface(found.interface.interfaceNumber, found.alternate.alternateSetting);
  } catch (e) { /* the default alternate setting is the adb one on every adbd seen so far */ }
  // The endpoints are NOT cleared here (adb's own client does that on macOS, ClearPipeStallBothEnds). Measured on an
  // F50 from this client: clearHalt() sets the host's side of the bulk endpoint back to its first packet while the
  // device's side stays where it was, and the next packet the device sends - the header of its answer to CNXN - is
  // then dropped as a repeat. connection.js copes with an answer whose header was lost; nothing here causes one.
  return new UsbTransport(device, found);
}

export class UsbTransport {
  constructor(device, found) {
    this.device = device;
    this.interfaceNumber = found.interface.interfaceNumber;
    this.inEp = found.inEndpoint.endpointNumber;
    this.outEp = found.outEndpoint.endpointNumber;
    this.packetSize = found.outEndpoint.packetSize || 512;
    this.headerReadSize = found.inEndpoint.packetSize || 512;
    this.framed = true;
    this.closed = false;
  }

  _gone(e) {
    if (e instanceof AdbError) return e;
    return new AdbError("disconnected", "the USB connection to the device was lost", e);
  }

  async read(length) {
    if (this.closed) throw new AdbError("disconnected", "the USB connection is closed");
    for (let stalls = 0; ; ) {
      let r;
      try { r = await this.device.transferIn(this.inEp, length); } catch (e) { throw this._gone(e); }
      if (r.status === "stall") {
        if (++stalls > 3) throw new AdbError("protocol", "the device keeps stalling its adb endpoint");
        try { await this.device.clearHalt("in", this.inEp); } catch (e) { throw this._gone(e); }
        continue;
      }
      if (r.status === "babble") throw new AdbError("protocol", "the device sent more than was asked for");
      const d = r.data;
      return d ? new Uint8Array(d.buffer, d.byteOffset, d.byteLength) : new Uint8Array(0);
    }
  }

  async write(bytes) {
    if (this.closed) throw new AdbError("disconnected", "the USB connection is closed");
    try {
      const r = await this.device.transferOut(this.outEp, bytes);
      if (r.status === "stall") {
        await this.device.clearHalt("out", this.outEp);
        throw new AdbError("protocol", "the device stalled its adb endpoint");
      }
      if (bytes.length && bytes.length % this.packetSize === 0) await this.device.transferOut(this.outEp, new Uint8Array(0));
    } catch (e) { throw this._gone(e); }
  }

  async close() {
    if (this.closed) return;
    this.closed = true;
    try { await this.device.releaseInterface(this.interfaceNumber); } catch (e) { /* unplugged */ }
    try { await this.device.close(); } catch (e) { /* unplugged */ }
  }
}
