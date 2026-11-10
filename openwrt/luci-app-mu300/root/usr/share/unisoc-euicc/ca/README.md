# eUICC HTTPS trust anchor

Public GSMA consumer RSP production CA certificate, not a device credential.
Subject: O=GSM Association, CN=GSM Association - RSP2 Root CI1
SHA-256 (DER): 5e3e91fd454327c3af5d32a7a73bbc59fe43aa7d85fd32d5db44423f80a56bb3

Source: ChromiumOS Hermes, platform2 commit `4318e9f2e64eb8c5c7d5124f7cc306d56a005790`,
`hermes/certs/prod/gsma-ci.pem`:
https://chromium.googlesource.com/chromiumos/platform2/+/4318e9f2e64eb8c5c7d5124f7cc306d56a005790/hermes/certs/prod/gsma-ci.pem

Issuer listing: https://www.gsma.com/solutions-and-impact/technologies/esim/gsma-root-ci/
Protocol: GSMA SGP.22 v3.0 section 2.6.6 permits eSIM CAs or public CAs on ES9+.
https://www.gsma.com/esim/wp-content/uploads/2022/10/SGP.22-v3.0.pdf

Retrieved 2026-11-10 over verified HTTPS; DER fingerprint checked before vendoring.
Used only by the eUICC stdio HTTP transport, in addition to the system web CA
bundle. Never installed into the global trust store. TLS chain, hostname,
validity and server-purpose checks remain enabled. No test or user roots are
trusted automatically. The SM-DP+ server's certificate is not a trust source.
