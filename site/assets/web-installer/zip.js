// Reading a Magisk zip the user picked, in the browser, before anything is sent to the device: which system and
// kernel it holds (its mu300/manifest) and whether its installer knows the web installer. Only the directory at the
// end of the file and the small entries asked for are read; the payload (the root filesystem) is never unpacked
// here. The device checks the whole file against its SHA-256 afterwards: this is for telling the user early that
// they picked the wrong file, not for trusting it.
const SIG_EOCD = 0x06054b50, SIG_CDIR = 0x02014b50, SIG_LOCAL = 0x04034b50;
const dec = new TextDecoder("utf-8", { fatal: false });

export class ZipError extends Error {
  constructor(code, message) { super(message || code); this.name = "ZipError"; this.code = code; }
}

async function bytesOf(blob, start, end) {
  return new Uint8Array(await blob.slice(start, end).arrayBuffer());
}

// openZip(blob) -> { entries: Map name -> { method, compressedSize, size, offset }, read(name, limit) }
export async function openZip(blob) {
  if (!blob || blob.size < 22) throw new ZipError("not-a-zip");
  const tailLen = Math.min(blob.size, 65557);
  const tail = await bytesOf(blob, blob.size - tailLen, blob.size);
  const tv = new DataView(tail.buffer);
  let e = -1;
  for (let i = tail.length - 22; i >= 0; i--) {
    if (tv.getUint32(i, true) === SIG_EOCD && i + 22 + tv.getUint16(i + 20, true) === tail.length) { e = i; break; }
  }
  if (e < 0) throw new ZipError("not-a-zip");
  const count = tv.getUint16(e + 10, true), cdSize = tv.getUint32(e + 12, true), cdOff = tv.getUint32(e + 16, true);
  if (count === 0xffff || cdOff === 0xffffffff) throw new ZipError("zip64", "a zip of this size is not one of this project");
  if (cdOff + cdSize > blob.size || cdSize > 4 * 1024 * 1024) throw new ZipError("not-a-zip");
  const cd = await bytesOf(blob, cdOff, cdOff + cdSize);
  const cv = new DataView(cd.buffer);
  const entries = new Map();
  let o = 0;
  for (let i = 0; i < count; i++) {
    if (o + 46 > cd.length || cv.getUint32(o, true) !== SIG_CDIR) throw new ZipError("not-a-zip");
    const method = cv.getUint16(o + 10, true), csize = cv.getUint32(o + 20, true), size = cv.getUint32(o + 24, true);
    const nlen = cv.getUint16(o + 28, true), xlen = cv.getUint16(o + 30, true), clen = cv.getUint16(o + 32, true);
    const offset = cv.getUint32(o + 42, true);
    if (o + 46 + nlen > cd.length) throw new ZipError("not-a-zip");
    const name = dec.decode(cd.subarray(o + 46, o + 46 + nlen));
    entries.set(name, { method, compressedSize: csize, size, offset });
    o += 46 + nlen + xlen + clen;
  }
  return {
    entries,
    // the bytes of a small entry (limit: refuse anything larger, compressed or not)
    async read(name, limit = 1024 * 1024) {
      const en = entries.get(name);
      if (!en) throw new ZipError("no-entry", name);
      if (en.size > limit || en.compressedSize > limit) throw new ZipError("too-large", name);
      const lh = await bytesOf(blob, en.offset, en.offset + 30);
      if (lh.length < 30 || new DataView(lh.buffer).getUint32(0, true) !== SIG_LOCAL) throw new ZipError("not-a-zip");
      const lv = new DataView(lh.buffer);
      const start = en.offset + 30 + lv.getUint16(26, true) + lv.getUint16(28, true);
      const raw = await bytesOf(blob, start, start + en.compressedSize);
      if (raw.length !== en.compressedSize) throw new ZipError("not-a-zip");
      if (en.method === 0) return raw;
      if (en.method !== 8) throw new ZipError("unsupported", name);
      const ds = new DecompressionStream("deflate-raw");
      const stream = new Blob([raw]).stream().pipeThrough(ds);
      const parts = [];
      let n = 0;
      const reader = stream.getReader();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        n += value.length;
        if (n > limit) { await reader.cancel(); throw new ZipError("too-large", name); }
        parts.push(value);
      }
      const out = new Uint8Array(n);
      let p = 0;
      for (const c of parts) { out.set(c, p); p += c.length; }
      return out;
    },
  };
}

const SYSTEMS = { "openwrt": ["openwrt", ""], "openwrt-luci": ["openwrt-luci", ""], "ubuntu-24.04": ["ubuntu", "24.04"], "ubuntu-26.04": ["ubuntu", "26.04"] };
const KERNELS = ["5.4", "6.18", "7.2", "7.3"];

// The manifest of a Magisk zip as its installer reads it (manifest_load): only these keys, and every value exactly
// one a release can have.
export function parseManifest(textValue) {
  const m = {};
  for (const line of String(textValue).split("\n")) {
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const k = line.slice(0, eq), v = line.slice(eq + 1).replace(/\r$/, "");
    if (["TAG", "SYSTEM", "OS", "UBUNTU", "KERNEL", "KERNEL_ASSET", "ROOTFS_ASSET", "SHA256_KERNEL", "SHA256_ROOTFS"].includes(k)) m[k] = v;
  }
  const sys = SYSTEMS[m.SYSTEM];
  if (!/^v[0-9][0-9A-Za-z._-]*$/.test(m.TAG || "") || (m.TAG || "").includes("..")) throw new ZipError("bad-manifest");
  if (!sys || sys[0] !== m.OS || sys[1] !== (m.UBUNTU || "") || !KERNELS.includes(m.KERNEL)) throw new ZipError("bad-manifest");
  if (!/^[0-9a-f]{64}$/.test(m.SHA256_KERNEL || "") || !/^[0-9a-f]{64}$/.test(m.SHA256_ROOTFS || "")) throw new ZipError("bad-manifest");
  return { tag: m.TAG, system: m.SYSTEM, os: m.OS, ubuntu: m.UBUNTU || "", kernel: m.KERNEL };
}

// inspectMagiskZip(blob) -> { tag, system, os, ubuntu, kernel, web }
// web: its installer takes MU300_ANSWERS and prints MU300-STEP lines (releases after v2026.10.23)
export async function inspectMagiskZip(blob) {
  const zip = await openZip(blob);
  for (const need of ["mu300/manifest", "mu300/install.sh", "mu300/android-install.sh", "switch.sh", "module.prop"]) {
    if (!zip.entries.has(need)) throw new ZipError("not-ours", need);
  }
  const manifest = parseManifest(dec.decode(await zip.read("mu300/manifest", 4096)));
  const installer = dec.decode(await zip.read("mu300/install.sh", 512 * 1024));
  return { ...manifest, web: installer.includes("MU300_ANSWERS") && installer.includes("MU300-STEP") };
}
