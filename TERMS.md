# Terms of Service — Tapseal

_Last updated: 2026-05-05_

## What this is

Tapseal is an open-source Discord bot that verifies Bitcoin address ownership
via BIP-322 signed messages and optionally grants a Discord role on success.

Source code: <https://github.com/novaoc/ordinal-verify-bot>

## Use at your own risk

Tapseal is provided **as-is, with no warranty of any kind**, express or
implied. The operator running this instance is not liable for any damages,
losses, or service interruptions resulting from its use, including but not
limited to lost role assignments, missed verifications, or inaccurate
inscription data returned by upstream APIs.

## What you agree to

By interacting with Tapseal in this server you agree to:

- Not abuse the bot — for example, by spamming `/verify` to flood the rate
  limits of upstream services.
- Not impersonate other users' Bitcoin addresses. The bot enforces this
  cryptographically via BIP-322; circumvention attempts will be treated as
  abuse.
- Accept that the operator may remove your verification record, ban you
  from using the bot, or shut down the bot at any time without notice.

## Not a custodian

Tapseal never receives, holds, transmits, or has any access to Bitcoin or any
other asset. It only inspects message signatures and reads public on-chain
data via Xverse's API. Your private keys never leave your wallet — Tapseal
cannot see them.

## Third-party services

Tapseal calls Xverse's Ordinals API to look up inscriptions held by an
address. Their terms apply to that data: <https://www.xverse.app/>.

Discord is the platform Tapseal runs on. Discord's terms apply to all
interactions: <https://discord.com/terms>.

## Changes

The operator may update these terms at any time by committing a new version
to the public repository. Continued use of the bot after a change constitutes
acceptance.

## Contact

Direct questions to the server administrator who deployed this instance.
