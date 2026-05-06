"""
Hand-rolled BIP-322 'simple' signer for P2WPKH addresses.

We need this only because the bot's runtime dep `bip322` (Rust binding) exposes
verify but not sign. For tests we have to produce a known-good signature so we
can drive verify_simple_encoded through both happy and adversarial paths.

Spec: https://github.com/bitcoin/bips/blob/master/bip-0322.mediawiki
"""

from __future__ import annotations

import base64
import hashlib

from bitcoinutils.keys import PrivateKey, P2wpkhAddress
from bitcoinutils.script import Script
from bitcoinutils.transactions import Transaction, TxInput, TxOutput


def _tagged_hash(tag: str, msg: bytes) -> bytes:
    t = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(t + t + msg).digest()


def _compact_size(n: int) -> bytes:
    if n < 0xFD:
        return bytes([n])
    if n < 0x10000:
        return b"\xfd" + n.to_bytes(2, "little")
    if n < 0x100000000:
        return b"\xfe" + n.to_bytes(4, "little")
    return b"\xff" + n.to_bytes(8, "little")


def _serialize_witness(stack: list[bytes]) -> bytes:
    out = _compact_size(len(stack))
    for item in stack:
        out += _compact_size(len(item)) + item
    return out


def sign_bip322_simple_p2wpkh(priv: PrivateKey, address: str, message: str) -> str:
    """Return a base64 BIP-322 'simple' signature for a P2WPKH address."""
    pub = priv.get_public_key()
    addr = P2wpkhAddress(address)

    msg_hash = _tagged_hash("BIP0322-signed-message", message.encode())

    # to_spend: virtual tx whose output commits to the address being signed for.
    # bitcoin-utils detects this as a "coinbase" input (txid == 0*64) and emits
    # script_sig.script[0] as raw hex, so we pre-serialize OP_0 + PUSH32 + hash.
    raw_script_sig = "00" + "20" + msg_hash.hex()
    script_sig = Script([raw_script_sig])
    to_spend = Transaction(
        [TxInput("00" * 32, 0xFFFFFFFF, script_sig=script_sig, sequence=b"\x00\x00\x00\x00")],
        [TxOutput(0, addr.to_script_pub_key())],
        locktime=b"\x00\x00\x00\x00",
        version=b"\x00\x00\x00\x00",
        has_segwit=False,
    )
    to_spend_txid = to_spend.get_txid()

    # to_sign: spends to_spend:0 and is what we actually sign
    to_sign = Transaction(
        [TxInput(to_spend_txid, 0, sequence=b"\x00\x00\x00\x00")],
        [TxOutput(0, Script(["OP_RETURN"]))],
        locktime=b"\x00\x00\x00\x00",
        version=b"\x00\x00\x00\x00",
        has_segwit=True,
    )

    # BIP143 sighash for P2WPKH uses the equivalent P2PKH script as script_code.
    script_code = pub.get_address().to_script_pub_key()
    sig_hex = priv.sign_segwit_input(to_sign, 0, script_code, 0)

    witness = _serialize_witness([bytes.fromhex(sig_hex), bytes.fromhex(pub.to_hex())])
    return base64.b64encode(witness).decode()
