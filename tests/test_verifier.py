"""End-to-end tests of bip322.verify_simple_encoded — the core security boundary."""

from __future__ import annotations

import bip322
import pytest

from tests.bip322_sign import sign_bip322_simple_p2wpkh


def test_valid_signature_verifies(signed_challenge):
    address, message, sig = signed_challenge
    # verify_simple_encoded returns None on success, raises on failure
    assert bip322.verify_simple_encoded(address, message, sig) is None


def test_tampered_message_rejected(signed_challenge):
    address, message, sig = signed_challenge
    with pytest.raises(bip322.VerificationError):
        bip322.verify_simple_encoded(address, message + "x", sig)


def test_wrong_address_rejected(signed_challenge, other_wallet):
    _, message, sig = signed_challenge
    with pytest.raises(bip322.VerificationError):
        bip322.verify_simple_encoded(other_wallet.address, message, sig)


def test_signature_from_different_key_rejected(wallet, other_wallet):
    message = "challenge for " + wallet.address
    # `other_wallet` signs but we claim it's for `wallet.address`
    sig = sign_bip322_simple_p2wpkh(other_wallet.priv, other_wallet.address, message)
    with pytest.raises(bip322.VerificationError):
        bip322.verify_simple_encoded(wallet.address, message, sig)


def test_truncated_signature_rejected(signed_challenge):
    address, message, sig = signed_challenge
    with pytest.raises(bip322.VerificationError):
        bip322.verify_simple_encoded(address, message, sig[:-4])


def test_garbage_signature_rejected(signed_challenge):
    address, message, _ = signed_challenge
    with pytest.raises(bip322.VerificationError):
        bip322.verify_simple_encoded(address, message, "AAAA")


def test_empty_signature_rejected(signed_challenge):
    address, message, _ = signed_challenge
    with pytest.raises(bip322.VerificationError):
        bip322.verify_simple_encoded(address, message, "")
