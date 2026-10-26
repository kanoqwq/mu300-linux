# SMS

**In one sentence:** `sudo sms list` reads the text messages on your SIM and `sudo sms send NUMBER TEXT` sends one,
straight from the modem, with no Android involved.

**The simple version:** the modem keeps a small mailbox on the SIM card. These commands open the mailbox, read the
letters and post new ones.

## Commands

```sh
sudo sms list                       # every message, long ones joined back together
sudo sms read INDEX                 # one message in full
sudo sms send NUMBER TEXT...        # send one (Turkish and emoji included; it picks the encoding)
sudo sms delete INDEX               # remove one from the SIM (also: read, all)
sudo sms watch [COMMAND]            # wait for new messages, run COMMAND for each
```

No `sudo` on OpenWrt. All of it goes through `mu300-atd`, so it queues behind anything else talking to the modem.
From v2026.10.22 a send is one transaction: the channel is held from the setup to the submission, and the SMSC the
modem reports is written back to it unchanged first; some networks refused every send without that (issue #176, see
[Troubleshooting](Troubleshooting)).
`sms delete` accepts only a message index, `read` or `all` (from v2026.10.20); anything else is refused before the
modem sees it, so a stray `1,4` can no longer wipe the mailbox.

On Ubuntu with the web panel extra (Cockpit), the MU300 page reads, sends and deletes messages too.

## On OpenWrt with the control panel

Use **Cellular -> SMS** in the panel: read, send and delete. There a pool daemon syncs the SIM every 30 s with
`AT+CMGL`, which **marks unread messages on the SIM as read**; the panel keeps its own unread state.

> **Do not run `sms delete read` on that system.** Because the pool marks everything it has seen as read, it would
> delete messages nobody has looked at. Delete from the panel, or with `mu300-sms delete` (which touches the SIM only
> with `--sim`). For automation use the panel's hook, `/etc/mu300/sms-hook`, rather than `sms watch`.

## SMS forwarding

On OpenWrt with the control panel, **Cellular > SMS forwarding** passes incoming messages on to a webhook (JSON or a
form), a Telegram chat or another phone by SMS. On Ubuntu the same settings are on Cockpit's MU300 page (SMS tab);
there a service looks at the SIM for new messages every 20 s while forwarding is on, and only messages that arrive
while it is on are passed on. It is off until you turn it on. Each target has a template
(`{sender}`, `{time}`, `{text}`, `{device}`) and can filter by sender and keyword; there is a "Send test" button and
a delivery log, and failed deliveries are retried with a growing wait. Tokens, passwords and the webhook's address
stay in `/etc/mu300/sms-forward.conf` (root only) and never appear in a log. E-mail forwarding needs `msmtp`, which
the image does not include.

From v2026.10.23 the webhook can send a body of your own instead of the standard fields (`sender`, `time`, `text`,
`device`, `message`), for services that want their own format (Lark/Feishu, DingTalk, WeCom, Slack, Discord, Bark,
ntfy). Choose the type (JSON, form or plain text; it sets the `Content-Type`) and write the body under "Custom
body" with the placeholders `{sender}`, `{time}`, `{text}`, `{device}` and `{message}` (the message template filled
in). Each value is escaped for the type - in JSON as the inside of a string, so write the quotes yourself:

```json
{"msg_type": "text", "content": {"text": "{message}"}}
```

In a form body the values are percent-encoded (`title={sender}&body={text}`); in plain text they are put in as they
are, and an empty body is the message alone (enough for ntfy). The body may be 2000 bytes long. Unlike the address
and the headers it is shown again in the panel, so keep keys and tokens in the address or in a header. "Send test"
sends the same body.

A delivery counts as delivered on any HTTP 2xx. From the release after v2026.10.23 the webhook can also check the
answer: many chat services answer 200 even when they refuse the message and
say so in the body; for those, fill in "The answer must contain" with the key and the value a good answer has:

| Service | The answer must contain |
|---|---|
| DingTalk, WeCom | `errcode=0` |
| Feishu / Lark | `code=0` |
| Slack Web API, Telegram Bot API | `ok=true` |
| Bark | not needed (it answers HTTP 400/500 on failure); `code=200` works |
| Slack incoming webhook, Discord, ntfy | leave empty: they answer with the HTTP status, not with JSON |

The answer must then be a JSON object with that value under that key at its top level. If the key has another value,
the delivery fails and is retried like any other failure, and the log shows it (`HTTP 200, the service answered
errcode=310000`; a value that is not a whole number, `true`, `false` or `null` is not shown, only that it is
another one). If the answer has no such value (`no errcode in the answer`), the delivery fails and is **not** sent
again, because the message may well have arrived: check the rule with "Send test", which applies it too (one test
every 5 seconds at most). The device reads at most the first 4096 bytes of the answer, in memory, and keeps only
the result: the answer itself is never logged or shown. The key is a name of letters, digits and `_` (32 at most);
the value is a whole number or lowercase letters (16 at most).

The delivery log is kept in memory (the last 100 lines, gone after a reboot); "Clear" under it empties it. Messages
that still wait to be delivered are not affected.

Since v2026.10.18 a message leaves the SIM only once its copy is on the disk, so a power cut loses none, and a long
message whose other parts never arrive is shown after an hour, marked incomplete.

## USSD

Balance checks and similar codes:

```sh
sudo mu300-ussd '*101#'
```

## Why PDU mode

The modem's text mode garbles senders that are names rather than numbers and cannot tell that several stored parts
are one long message, so `sms` uses PDU mode and decodes the frames itself, including the Turkish national language
shift. Sending picks GSM 7-bit when every character fits, UCS-2 otherwise. Details:
[FINDINGS 26c](https://github.com/dikeckaan/mu300-linux/blob/main/docs/FINDINGS.md#26c-sms-needs-pdu-mode-and-turkish-needs-more-than-that).
