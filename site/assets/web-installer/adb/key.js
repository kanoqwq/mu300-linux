// The browser's adb key: an RSA-2048 key pair as adb uses it.
//
// adb's AUTH answer is RSASSA-PKCS1-v1_5 over the device's 20-byte token *as if the token were a SHA-1 digest*
// (AOSP: RSA_sign(NID_sha1, token, 20, ...)). WebCrypto's sign() always hashes its input first, so it cannot make
// that signature: the private operation is done here with BigInt. That is why the key is kept as numbers (n, d) and
// not as a non-extractable CryptoKey. It is not constant time; the key never leaves this browser profile and signs
// only tokens of a device on the user's own USB port.
//
// The public key goes to the device in Android's own format (AOSP libcrypto_utils, android_pubkey.cpp):
//   uint32 len (words, 64) | uint32 n0inv (-1 / n[0] mod 2^32) | n[256] little endian | rr[256] little endian
//   (R^2 mod n, R = 2^2048) | uint32 exponent
// base64 of those 524 bytes is the 700 characters of ~/.android/adbkey.pub ("QAAAA..." first), followed by a name.
import { base64, base64urlToBytes, bigIntToBytes, bytesToBigInt, concat, u32le } from "./bytes.js";
import { AdbError } from "./errors.js";

export const KEY_BITS = 2048;
const KEY_BYTES = KEY_BITS / 8;
export const TOKEN_SIZE = 20;
// DER prefix of DigestInfo for SHA-1 (RFC 8017, 9.2 note 1)
const SHA1_DIGEST_INFO = new Uint8Array([0x30, 0x21, 0x30, 0x09, 0x06, 0x05, 0x2b, 0x0e, 0x03, 0x02, 0x1a, 0x05, 0x00, 0x04, 0x14]);

export function modPow(base, exp, mod) {
  let result = 1n;
  base %= mod;
  while (exp > 0n) {
    if (exp & 1n) result = (result * base) % mod;
    base = (base * base) % mod;
    exp >>= 1n;
  }
  return result;
}

// A key as it is stored: { n, d, e } as lowercase hex strings (no BigInt in IndexedDB's older structured clones, and
// plain strings are easy to look at in DevTools), plus the name sent to the device.
export async function generateKey(name = "mu300-web", cryptoImpl = globalThis.crypto) {
  const pair = await cryptoImpl.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: KEY_BITS, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-1" },
    true, ["sign", "verify"]);
  const jwk = await cryptoImpl.subtle.exportKey("jwk", pair.privateKey);
  return keyFromJwk(jwk, name);
}

export function keyFromJwk(jwk, name = "mu300-web") {
  const n = bytesToBigInt(base64urlToBytes(jwk.n));
  const d = bytesToBigInt(base64urlToBytes(jwk.d));
  const e = bytesToBigInt(base64urlToBytes(jwk.e));
  return { n: n.toString(16), d: d.toString(16), e: e.toString(16), name: cleanName(name), created: Date.now() };
}

// the name goes into a file on the device (adb_keys, mu300-install.env): the characters storage.sh's adb_pubkey keeps
export function cleanName(name) {
  return (String(name).replace(/[^A-Za-z0-9@._:-]/g, "").slice(0, 64)) || "mu300-web";
}

export function validKey(k) {
  if (!k || typeof k.n !== "string" || typeof k.d !== "string" || typeof k.e !== "string") return false;
  if (!/^[0-9a-f]+$/.test(k.n) || !/^[0-9a-f]+$/.test(k.d) || !/^[0-9a-f]+$/.test(k.e)) return false;
  const n = BigInt("0x" + k.n);
  return n.toString(2).length === KEY_BITS && (n & 1n) === 1n;
}

export function signToken(key, token) {
  if (!(token instanceof Uint8Array) || token.length !== TOKEN_SIZE) {
    throw new AdbError("protocol", `the device sent an AUTH token of ${token ? token.length : 0} bytes, not ${TOKEN_SIZE}`);
  }
  const pad = new Uint8Array(KEY_BYTES - 3 - SHA1_DIGEST_INFO.length - TOKEN_SIZE).fill(0xff);
  const em = concat(new Uint8Array([0x00, 0x01]), pad, new Uint8Array([0x00]), SHA1_DIGEST_INFO, token);
  const s = modPow(bytesToBigInt(em), BigInt("0x" + key.d), BigInt("0x" + key.n));
  return bigIntToBytes(s, KEY_BYTES);
}

function modInverse(a, m) {  // extended Euclid; a and m coprime
  let [r0, r1] = [a % m, m];
  let [s0, s1] = [1n, 0n];
  while (r1 !== 0n) {
    const q = r0 / r1;
    [r0, r1] = [r1, r0 - q * r1];
    [s0, s1] = [s1, s0 - q * s1];
  }
  return ((s0 % m) + m) % m;
}

export function androidPublicKeyBytes(key) {
  const n = BigInt("0x" + key.n);
  const e = BigInt("0x" + key.e);
  const r32 = 1n << 32n;
  const n0inv = (r32 - modInverse(n & (r32 - 1n), r32)) % r32;
  const rr = (1n << BigInt(KEY_BITS * 2)) % n;
  return concat(u32le(KEY_BITS / 32), u32le(Number(n0inv)), bigIntToBytes(n, KEY_BYTES, true),
    bigIntToBytes(rr, KEY_BYTES, true), u32le(Number(e)));
}

// "BASE64 name": the line of adbkey.pub, and what /etc/mu300/adb_keys on our Linux holds
export function publicKeyLine(key) {
  return base64(androidPublicKeyBytes(key)) + " " + cleanName(key.name);
}

// A short fingerprint to show, so that a user can tell two browsers' keys apart: the first bytes of the SHA-256 of
// the public key, as the page shows them (not adb's own MD5 fingerprint).
export async function fingerprint(key, cryptoImpl = globalThis.crypto) {
  const h = new Uint8Array(await cryptoImpl.subtle.digest("SHA-256", androidPublicKeyBytes(key)));
  return Array.from(h.subarray(0, 6), (b) => b.toString(16).padStart(2, "0")).join(":");
}
