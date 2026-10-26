// One error type for everything the adb client can fail with. `code` is what the page explains to the user:
//   disconnected  the cable was pulled, the device rebooted, or the transport closed
//   busy          another program holds the adb interface (a local adb server)
//   access        the browser may not open the device (Windows without a WinUSB driver, Linux without a udev rule)
//   no-interface  the USB device has no adb interface
//   protocol      the device sent something that is not adb (or not what was asked for)
//   auth          the device will not accept this browser's key
//   unsupported   the device asks for something this client does not do (TLS)
//   timeout       no answer in time
//   closed        the stream was closed by the device
//   remote        the device answered with a failure message (sync FAIL, a refused service)
//   aborted       the caller cancelled
export class AdbError extends Error {
  constructor(code, message, cause) {
    super(message || code);
    this.name = "AdbError";
    this.code = code;
    if (cause !== undefined) this.cause = cause;
  }
}

export function isAdbError(e, code) {
  return e instanceof AdbError && (code === undefined || e.code === code);
}
