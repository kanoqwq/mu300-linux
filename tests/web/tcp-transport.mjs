// An adb transport over TCP for Node: the same packets as over USB, on a socket (adbd listens on 5555 where it is
// built to). Only for tests against a real adbd (interop.test.mjs); the page itself speaks USB only.
import net from "node:net";

export function tcpTransport(host, port) {
  return new Promise((resolve, reject) => {
    const sock = net.connect({ host, port });
    let buf = Buffer.alloc(0), waiter = null, ended = null;
    const wake = () => { if (waiter) { const w = waiter; waiter = null; w(); } };
    sock.on("data", (d) => { buf = Buffer.concat([buf, d]); wake(); });
    sock.on("error", (e) => { ended = e; wake(); reject(e); });
    sock.on("close", () => { ended = ended || new Error("closed"); wake(); });
    sock.on("connect", () => resolve({
      framed: false,
      async read(length) {
        while (!buf.length) {
          if (ended) throw ended;
          await new Promise((r) => { waiter = r; });
        }
        const out = new Uint8Array(buf.subarray(0, length));
        buf = buf.subarray(out.length);
        return out;
      },
      write(bytes) {
        return new Promise((res, rej) => sock.write(Buffer.from(bytes), (e) => (e ? rej(e) : res())));
      },
      async close() { sock.destroy(); },
    }));
  });
}
