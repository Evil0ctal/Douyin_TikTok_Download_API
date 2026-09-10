"""The donation addresses, pinned and checksum-verified.

Three places publish them - the console's About page and both READMEs - and a
single wrong character in any of them sends somebody's money somewhere nobody
can retrieve it from. That is the rare kind of typo with no recovery and no
error message, so it gets a test rather than a review.

Two things are checked. That every copy agrees with the one list below, and
that each address still passes its own format's checksum: bech32 for Bitcoin,
EIP-55 for the EVM chains, base58check for Tron, and a 32-byte decode for
Solana. The second is what catches an edit that changed all three copies
consistently and wrongly.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: The addresses, as their owner gave them. Nothing here is derived.
WALLETS: dict[str, str] = {
    "solana": "HvtkxmDERbNXfCoojpdFAYN5mSWowjpXgedsG9eF7y9z",
    "tron": "TQwSM2vjcnrdRU7gY7KNp2tCgMnK33azkT",
    "ethereum": "0x2f210FdfD981B59eC130370E5b1Aa8A6a06fb5Ad",
    "bnb": "0x2f210FdfD981B59eC130370E5b1Aa8A6a06fb5Ad",
    "bitcoin": "bc1q785j55cxlnjqe8lkwy8cq57t8t9vn3ak9tlsfy",
}

#: Everywhere they are published.
PUBLISHED = ("README.md", "README.en.md", "web/src/pages/About.tsx")

#: Anything shaped like an address on one of these chains. Used to catch a
#: fourth address appearing somewhere without passing through this file.
ADDRESS_SHAPED = re.compile(
    r"0x[0-9a-fA-F]{40}"
    r"|bc1[a-z0-9]{25,}"
    r"|T[1-9A-HJ-NP-Za-km-z]{33}"
    r"|(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{43,44}(?![1-9A-HJ-NP-Za-km-z])"
)

_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _b58decode(text: str) -> bytes:
    value = 0
    for char in text:
        value = value * 58 + _BASE58.index(char)
    body = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return b"\x00" * (len(text) - len(text.lstrip("1"))) + body


def _bech32_polymod(values: list[int]) -> int:
    generator = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = (checksum & 0x1FFFFFF) << 5 ^ value
        for index in range(5):
            checksum ^= generator[index] if (top >> index) & 1 else 0
    return checksum


@pytest.mark.parametrize("where", PUBLISHED)
def test_every_published_copy_matches(where: str) -> None:
    text = (REPO / where).read_text(encoding="utf-8")
    for chain, address in WALLETS.items():
        assert address in text, f"{where} is missing the {chain} address"


@pytest.mark.parametrize("where", PUBLISHED)
def test_no_other_address_is_published(where: str) -> None:
    """An address nobody pinned is either a typo or somebody else's wallet."""
    text = (REPO / where).read_text(encoding="utf-8")
    found = set(ADDRESS_SHAPED.findall(text))
    assert found <= set(WALLETS.values()), sorted(found - set(WALLETS.values()))


def test_bitcoin_passes_its_bech32_checksum() -> None:
    address = WALLETS["bitcoin"]
    prefix, _, data = address.rpartition("1")
    decoded = [_BECH32.index(char) for char in data]
    expanded = [ord(c) >> 5 for c in prefix] + [0] + [ord(c) & 31 for c in prefix]
    assert _bech32_polymod(expanded + decoded) == 1, "bech32 checksum does not hold"


def test_tron_passes_its_base58check() -> None:
    raw = _b58decode(WALLETS["tron"])
    payload, checksum = raw[:-4], raw[-4:]
    assert payload[0] == 0x41, "not a Tron mainnet address"
    assert checksum == hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]


def test_solana_decodes_to_a_32_byte_key() -> None:
    assert len(_b58decode(WALLETS["solana"])) == 32


def test_the_evm_chains_share_one_address_on_purpose() -> None:
    """Said out loud beside the list too, because it reads as a copy-paste slip."""
    assert WALLETS["ethereum"] == WALLETS["bnb"]


def test_the_evm_address_passes_its_eip55_checksum() -> None:
    """The mixed case IS a checksum, and it is the only thing that catches a
    single wrong hex digit in an EVM address."""
    address = WALLETS["ethereum"]
    body = address[2:]
    lowered = body.lower()
    digest = _keccak256(lowered.encode())
    expected = "".join(
        char.upper() if int(digest[index], 16) >= 8 else char for index, char in enumerate(lowered)
    )
    assert expected == body, f"EIP-55 checksum fails; the address should read 0x{expected}"


# --------------------------------------------------------------------------
# Keccak-256
# --------------------------------------------------------------------------
#
# Not SHA3-256: Ethereum uses the original Keccak padding, so `hashlib.sha3_256`
# gives a different digest and would pass a wrong address. No dependency here
# provides it, and adding one for a test would be the larger change.

_ROUNDS = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)  # fmt: skip
_ROTATIONS = (
    (0, 36, 3, 41, 18), (1, 44, 10, 45, 2), (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56), (27, 20, 39, 8, 14),
)  # fmt: skip
_MASK = (1 << 64) - 1


def _rotate(value: int, count: int) -> int:
    return ((value << count) | (value >> (64 - count))) & _MASK


def _permute(state: list[list[int]]) -> list[list[int]]:
    for round_constant in _ROUNDS:
        parity = [
            state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3] ^ state[x][4] for x in range(5)
        ]
        theta = [parity[(x - 1) % 5] ^ _rotate(parity[(x + 1) % 5], 1) for x in range(5)]
        state = [[state[x][y] ^ theta[x] for y in range(5)] for x in range(5)]
        rotated = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                rotated[y][(2 * x + 3 * y) % 5] = _rotate(state[x][y], _ROTATIONS[x][y])
        state = [
            [
                rotated[x][y] ^ (~rotated[(x + 1) % 5][y] & _MASK & rotated[(x + 2) % 5][y])
                for y in range(5)
            ]
            for x in range(5)
        ]
        state[0][0] ^= round_constant
    return state


def _keccak256(data: bytes) -> str:
    rate = 136
    padded = data + b"\x01" + b"\x00" * ((-len(data) - 1) % rate)
    padded = padded[:-1] + bytes([padded[-1] ^ 0x80])
    state = [[0] * 5 for _ in range(5)]
    for offset in range(0, len(padded), rate):
        block = padded[offset : offset + rate]
        for index in range(rate // 8):
            state[index % 5][index // 5] ^= int.from_bytes(
                block[index * 8 : index * 8 + 8], "little"
            )
        state = _permute(state)
    out = b"".join(state[i % 5][i // 5].to_bytes(8, "little") for i in range(4))
    return out.hex()


def test_the_keccak_used_above_is_the_right_one() -> None:
    """Against the published digest of the empty string.

    Without this the EIP-55 test would be checking an address against whatever
    this implementation happened to compute.
    """
    assert _keccak256(b"") == ("c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")
    assert hashlib.sha3_256(b"").hexdigest() != _keccak256(b""), "sha3 is not keccak"
