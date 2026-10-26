# U30 Air SAE: remaining key-handoff work

> **The offload is disabled (2026-10-17).** The opt-in this page was written for was removed in every layer
> because its PMK record cannot be told from one a client sends ([U30AIR-SAE.md](U30AIR-SAE.md), FINDINGS 80):
> the hostapd patch, its SDK recipe and the fallback guard are gone, and the driver now drops the record and
> refuses the vendor command. What follows is what bringing WPA3 back would take. Where it names the SDK helper,
> the parser or the existing opt-in, that code is in git history (as of commit `2e5cf929`), not in the tree.

This is an implementation handoff for the blocking finding in
[PR #74's device review](https://github.com/dikeckaan/mu300-linux/pull/74#issuecomment-6106003178).
It describes work still needed, not an implemented or validated firmware API.

## What exists and what remains unproven

The SDK helper builds the patched hostapd/wpad pair. The runtime vendor request
sets the firmware SAE passphrase and group. The current association handler
parses a trailing `40:45:da:04` element and imports its PMK/PMKID through the
existing authenticator. Independent hardware testing confirmed that this
restores SAE connections, including required PMF and the four-way handshake.

The origin of that key is not established by the implementation. The driver
passes `evt_new_station.ie` through `sprd_report_softap()` as
`station_info.assoc_req_ies`, and hostapd receives it as `NL80211_ATTR_IE`.
That buffer includes client-controlled association IEs. Its `ie_len` bounds
the buffer but does not identify an independently trusted firmware section.
`struct evt_new_station` currently exposes connection state, MAC and IE length;
it has no separate SAE-completion/key-provenance field.

The parser's exact length, unique-match and trailing-position checks establish
syntax only. Neither required PMF nor a valid handshake MIC proves knowledge
of the passphrase if the imported PMK could have been selected by the client.
Moving the same trailing bytes to another netlink event would leave that
assumption unchanged. Cutting the last 54 bytes off is likewise insufficient
to determine which bytes originally came from the client.

## Firmware/driver contract to establish first

The maintainer needs to identify a firmware-originated indication or query
that independently establishes the key's authentication context. A suitable
contract would bind the PMK/PMKID to the BSS, station and successful SAE
exchange, or to a previously verified PMKSA resumption. It must distinguish
client IE bytes from key material produced by firmware.

The identifiers below describe proposed interface semantics; they are not
fields known to exist in the current firmware event:

- BSS/interface identity and station MAC.
- Authentication/association instance identity, with a reset generation so
  stale events cannot attach to a new station or a restarted BSS.
- Independently established authentication result and selected AKM.
- Explicit PMK length, PMK and PMKID from that verified context.

If the firmware supplies no such indication, do not manufacture one by
setting an `authenticated` flag after recognizing the IE marker. That is the
same trust problem with another name. The next step would require firmware
support or a different authentication path; it cannot be completed by adding
a userspace flag to the existing parser.

Rejecting private markers in the client portion is useful only if the driver
has an independent, reliable boundary for that original client portion. A
boundary inferred by assuming a firmware trailer is always present is circular.

## Driver changes once that contract is known

The relevant entry points are `sc2355/cmdevt.c`'s
`cmdevt_report_new_station_evt()`, `common/report.c`'s `sprd_report_softap()`,
and the vendor event registration in `sc2355/vendor.c`.

1. Validate the event header and payload lengths before reading any field.
   Keep client association IEs separate from independently verified firmware
   key material. Strip private key metadata from the association notification
   only after its origin and boundary have been established.
2. Deliver verified key information through a dedicated, reviewed interface.
   A vendor event is one candidate; its OUI, event number and attributes need
   maintainer agreement. Do not overload a standard attribute whose kernel
   interface does not provide these semantics.
3. Bind the key event and association to the same BSS/station/authentication
   instance. A MAC address alone is not sufficient to match stale or reordered
   events. If events arrive separately, use bounded pending state and a
   timeout; neither ordering should silently authorize an unmatched station.
4. Clear pending state on station removal, BSS teardown, WCN recovery and
   firmware reset. Zero temporary key buffers when consumed or discarded.
   Keep private bytes out of debug payload dumps as well as ordinary logs.

These checks belong at the firmware/kernel boundary. Userspace must not be
asked to infer a verified result from the client's association bytes.

## hostapd changes once the driver interface is available

- Consume the dedicated verified-key interface, checking its BSS/station and
  authentication instance. Remove PMK discovery from `req_ies` as a source of
  authorization; retain those IEs for normal RSN and association validation.
- Require the existing explicit opt-in, pure SAE, mandatory PMF, supported
  firmware mode and PMKSA caching. Keep the default/offload-disabled path
  unchanged.
- Import a PMK only after its authentication context has been established.
  Continue using `wpa_auth_pmksa_add_sae()` and the normal authenticator rather
  than replacing RSN, MIC or PMF validation. The callback ordering must still
  allow normal RSN/PMKID validation to use a verified cache entry.
- Missing, malformed, stale or mismatched key metadata must fail closed.
  Do not fall back to a key parsed from the association IE. PMKSA resumption
  needs its own verified-cache rules; it is not a fallback for untrusted input.
- Do not set `sta->auth_alg = WLAN_AUTH_SAE` solely because a private marker
  was found. That state must follow the verified authentication result.

## Remaining validation and acceptance

The maintainer's existing hardware results cover functional SAE, wrong-password
rejection, WPA2 with the option off, restart/reassociation and WCN recovery.
They do not resolve the key-origin finding.

After the interface is implemented, review needs to cover rejection of
client-supplied private markers and unverified authentication contexts, plus
missing/duplicate/truncated events, event reordering and stale generations.
Use synthetic keys for parser/interface tests. Repeat the existing functional
cases on the matching driver and package pair. No claim about rejecting an
authentication bypass should be made solely from a failed client connection.

The outstanding boundary and its detailed validation are being handed back
to the maintainer. No controlled-frame or authentication-manipulation test
implementation is included in this contribution. PR #74 was continued as #165;
this finding is open in both, and the mode stays an opt-in experiment until
it is resolved.

A first probe that needs no new interface, only a Linux client whose frames can
be shaped (a mac80211 card with wpa_supplicant): add a PMKSA of your own choice
for the access point (`wpa_cli pmksa_add`), so the client skips SAE and sends
Open System authentication with that PMKID, and append the matching
`dd 34 40 45 da 04 <PMK> <PMKID>` element to the association request
(`wpa_cli vendor_elem_add 13 ...`; it has to be the last element of the frame,
check with a capture). If the firmware refuses the association, or
appends its own element (two markers, rejected by the parser), the simplest
form of the bypass is closed on this firmware. If the client completes the
four-way handshake without knowing the passphrase, the bypass is real and the
mode must not be used. Either result goes into FINDINGS 80.
