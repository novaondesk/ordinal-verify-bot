"""
Shared fixtures: deterministic throwaway BTC keys + signed BIP-322 challenges.

Nothing in this directory is real money. Keys are derived from a fixed integer
so failures are reproducible across machines.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import pytest
from bitcoinutils.keys import PrivateKey
from bitcoinutils.setup import setup as bu_setup

# make the bot module importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.bip322_sign import sign_bip322_simple_p2wpkh


@dataclass
class Wallet:
    priv: PrivateKey
    address: str


@pytest.fixture(scope="session", autouse=True)
def _bitcoinutils_network():
    bu_setup("mainnet")


@pytest.fixture(scope="session")
def wallet() -> Wallet:
    # fixed secret exponent — deterministic test wallet, mainnet, P2WPKH
    priv = PrivateKey(secret_exponent=0xC0FFEE_BABE_DEAD_BEEF_1337_C0DE_F00D_FACE)
    address = priv.get_public_key().get_segwit_address().to_string()
    return Wallet(priv=priv, address=address)


@pytest.fixture(scope="session")
def other_wallet() -> Wallet:
    priv = PrivateKey(secret_exponent=0xDECAF_BAD_C0FFEE_F00D_BAAA_AAAD_CAFE_C0DE)
    address = priv.get_public_key().get_segwit_address().to_string()
    return Wallet(priv=priv, address=address)


@pytest.fixture
def signed_challenge(wallet):
    """A (address, message, signature) triple that should verify."""
    message = "OrdinalVerifyBot|discord:111222333|addr:" + wallet.address + "|nonce:abc123xyz"
    sig = sign_bip322_simple_p2wpkh(wallet.priv, wallet.address, message)
    return wallet.address, message, sig
