# Ordinal Verify Bot

Discord bot for verifying ownership of Bitcoin Ordinal NFTs using BIP322 signatures.

## Features

- Verify Ordinal NFT ownership via signed messages
- Check wallet balances and inscriptions
- Real-time verification using BIP322 standard
- Discord integration for community verification

## Installation

### Prerequisites

- Python 3.8+
- Discord bot token
- Bitcoin node or API access (optional for self-hosted)

### Steps

```bash
git clone https://github.com/novaoc/ordinal-verify-bot.git
cd ordinal-verify-bot
uv venv
uv pip install -r requirements.txt
```

## Usage

1. Create a Discord bot and invite it to your server
2. Run the bot with your token
3. Use commands like `!verify <address> <signature>` to verify ownership

## BIP322 Implementation

This bot uses the [BIP322](https://github.com/bitcoin/bips/blob/master/bip-0322.mediawiki) standard for generic signed messages, ensuring compatibility with all Bitcoin wallets that support message signing.

## Contributing

Fork the repository, make changes, and submit a PR.

## License

MIT