// What the page shows and keeps about a device: serial numbers masked unless the user asks to see them, and nothing
// that looks like an IMEI, IMSI, ICCID or phone number in the log - neither on screen nor in the file the user can
// save and attach to a bug report. The page never asks the device for those numbers; this is for what a command's
// output may carry by accident (a kernel log line, a modem message).

// "324950664950" -> "••••••••4950": enough to tell two devices apart, not enough to identify one
export function maskSerial(serial) {
  const s = String(serial || "");
  if (!s) return "";
  if (s.length <= 4) return "•".repeat(s.length);
  return "•".repeat(Math.min(8, s.length - 4)) + s.slice(-4);
}

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// A run of 14 to 20 digits that is a word of its own: IMEI (15), IMEISV (16), IMSI (15), ICCID (19 or 20). Byte
// offsets and sizes on these devices have at most 11 digits, and a run of digits inside a checksum or a key is not a
// word of its own (letters or digits touch it).
const ID_NUMBER = /(?<![0-9A-Za-z])\d{14,20}(?![0-9A-Za-z])/g;
// an international phone number: + and 8 to 15 digits, with blanks or dashes between groups
const PHONE = /(?<![0-9A-Za-z])\+\d[\d -]{6,18}\d(?![0-9A-Za-z])/g;
// a MAC address (the Wi-Fi and Bluetooth addresses are per device)
const MAC = /(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])/g;
// the value behind a name that says it is a secret or an identity: "PSK=...", "password: ...", "ro.serialno=...",
// "SSID x imported" (the installer's own line about the hotspot it copied)
const NAMED = /\b((?:wpa_)?passphrase|psk|password|passwd|pwhash|ssid|imei|imsi|iccid|msisdn|(?:ro\.(?:boot\.)?)?serialno|serial(?:[ _]number)?)(\s*[=:]\s*|\s+)("[^"]*"|'[^']*'|\S+)/gi;
const KEEP_AFTER_NAME = /^(none|unchanged|kept|given|image|the|for|is|of|on|off|yes|no|not|\*\*\*|\[.*\]|•+.*)$/i;
// an adb public key line, or any long run of base64 (a key, a hash of a password)
const LONG_BASE64 = /(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{120,}={0,2}/g;
const CRYPT_HASH = /\$(?:1|5|6|y|2[aby])\$[^\s:'"]+/g;

export class Masker {
  constructor() { this.secrets = []; }
  // a string that must never appear: a serial number (replaced by its masked form), a password (replaced by ***)
  add(secret, replacement) {
    secret = String(secret || "");
    if (secret.length < 4) return;
    if (this.secrets.some((s) => s.secret === secret)) return;
    this.secrets.push({ secret, re: new RegExp(escapeRe(secret), "g"), replacement: replacement === undefined ? maskSerial(secret) : replacement });
    // longest first: a serial that contains another one is replaced as a whole
    this.secrets.sort((a, b) => b.secret.length - a.secret.length);
  }
  mask(text) {
    let t = String(text);
    for (const s of this.secrets) t = t.replace(s.re, s.replacement);
    return t.replace(CRYPT_HASH, "[password hash]").replace(LONG_BASE64, "[key]")
      .replace(/\bSSID .* imported\b/g, "SSID [masked] imported")
      .replace(NAMED, (m, name, sep, value) => (sep.trim() === "" && !/^ssid$/i.test(name) ? m : KEEP_AFTER_NAME.test(value) ? m : name + sep + "[masked]"))
      .replace(ID_NUMBER, (m) => "[" + m.length + " digits]").replace(PHONE, "[phone number]")
      .replace(MAC, (m) => m.slice(0, 8) + ":••:••:••");
  }
}

// One masker for the whole page: every line a device printed goes through it before it is shown, kept or saved
// (device.js applies it at the source, so no caller can forget). app.js adds the serial number and the password.
export const pageMasker = new Masker();
export const mask = (text) => pageMasker.mask(text);
