# Solana guide

Everything under `blockchainpype.solana` targets Solana through `solana-py`
(`AsyncClient`) and `solders`.

## Blockchain configuration

`SolanaBlockchainConfiguration` mirrors its EVM counterpart:

| Field | Type | Purpose |
| --- | --- | --- |
| `platform` | `BlockchainPlatform` | Identity: identifier, blockchain type, `chain_id` is `None` on Solana |
| `connectivity` | `SolanaConnectivityConfiguration` | Holds the `solana.rpc.async_api.AsyncClient` |
| `native_asset` | `SolanaNativeAssetConfiguration` | Defaults to SOL, 9 decimals |
| `explorer` | `SolscanConfiguration \| None` | Link builder |

The client's own `commitment` is adopted by the blockchain and used for balance
reads, so configure it on the `AsyncClient`.

```python
from financepype.platforms.blockchain import BlockchainPlatform
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed

from blockchainpype.solana.blockchain.blockchain import (
    SolanaBlockchain,
    SolanaBlockchainType,
)
from blockchainpype.solana.blockchain.configuration import (
    SolanaBlockchainConfiguration,
    SolanaConnectivityConfiguration,
)
from blockchainpype.solana.explorer.solscan import SolscanConfiguration

configuration = SolanaBlockchainConfiguration(
    platform=BlockchainPlatform(
        identifier="solana",
        type=SolanaBlockchainType,
        chain_id=None,
    ),
    connectivity=SolanaConnectivityConfiguration(
        rpc_provider=AsyncClient(
            "https://api.mainnet-beta.solana.com", commitment=Confirmed
        ),
    ),
    explorer=SolscanConfiguration(),
)
blockchain = SolanaBlockchain(configuration=configuration)
```

## Identifiers and the native-SOL sentinel

`SolanaPublicKey` (and its `SolanaAddress` alias) wrap a `solders` `Pubkey`;
`SolanaTransactionSignature` wraps a `Signature`. Both validate on
construction.

Native SOL has no mint account, so the library identifies it with a
**sentinel** address, `SolanaNullAddress`:

```python
from blockchainpype.solana.blockchain.identifier import (
    NATIVE_SOL_SENTINEL_ADDRESS,
    WRAPPED_SOL_MINT_ADDRESS,
    SolanaAddress,
    SolanaNullAddress,
)

# Note the trailing "1" vs "2": the sentinel is NOT the wrapped-SOL mint.
assert NATIVE_SOL_SENTINEL_ADDRESS == "So11111111111111111111111111111111111111111"
assert WRAPPED_SOL_MINT_ADDRESS == "So11111111111111111111111111111111111111112"
assert SolanaNullAddress().string == NATIVE_SOL_SENTINEL_ADDRESS

wsol = SolanaAddress.from_string(WRAPPED_SOL_MINT_ADDRESS)
print(wsol.raw)
```

Wrapped SOL is a real SPL token and must be modelled as an `SPLToken` on that
mint — never through the sentinel.

## Reading the chain

```python
import asyncio

from blockchainpype.factory import BlockchainFactory
from blockchainpype.solana.blockchain.identifier import (
    SolanaAddress,
    SolanaTransactionSignature,
)

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


async def main(signature: str) -> None:
    blockchain = BlockchainFactory.get_solana_blockchain_by_identifier("solana")
    owner = SolanaAddress.from_string("11111111111111111111111111111112")

    slot = await blockchain.fetch_block_number()
    block_time = await blockchain.fetch_block_timestamp(slot)
    blockhash = await blockchain.fetch_recent_blockhash()
    print(slot, block_time, blockhash)

    sol = await blockchain.fetch_native_asset_balance(owner)  # SOL, not lamports
    usdc = await blockchain.fetch_spl_token_balance(
        owner, SolanaAddress.from_string(USDC_MINT)
    )
    print(sol, usdc)

    tx_signature = SolanaTransactionSignature.from_string(signature)
    status = await blockchain.fetch_transaction_status(tx_signature)
    receipt = await blockchain.fetch_transaction_receipt(tx_signature)
    print(status, receipt)


asyncio.run(main("<a base58 transaction signature>"))
```

`fetch_block_data` passes `max_supported_transaction_version=0` by default, so
blocks containing versioned (v0) transactions do not error.
`fetch_spl_token_balance` derives the owner's associated token account and
scales the amount by the mint's decimals; when the ATA does not exist yet the
RPC error is translated into a **zero balance** rather than an exception.

## Wallets

A `SolanaWalletConfiguration` needs an identifier and, to sign, a
`SolanaSignerConfiguration` holding the base58 keypair string
(`Keypair.from_base58_string`).

```python
from pydantic import SecretStr

from blockchainpype.factory import BlockchainFactory
from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.wallet.identifier import SolanaWalletIdentifier
from blockchainpype.solana.wallet.signer import SolanaSignerConfiguration
from blockchainpype.solana.wallet.wallet import SolanaWallet, SolanaWalletConfiguration


def build_wallet(address: str, private_key: str | None) -> SolanaWallet:
    blockchain = BlockchainFactory.get_solana_blockchain_by_identifier("solana")
    return SolanaWallet(
        configuration=SolanaWalletConfiguration(
            identifier=SolanaWalletIdentifier(
                name="main-solana",
                platform=blockchain.platform,
                address=SolanaAddress.from_string(address),
            ),
            # Without a signer the wallet is read-only.
            signer=(
                SolanaSignerConfiguration(private_key=SecretStr(private_key))
                if private_key
                else None
            ),
        ),
        blockchain=blockchain,
    )
```

### Signing: legacy and versioned transactions

`wallet.sign_transaction(transaction, recent_blockhash, additional_signers=None)`
handles both shapes:

* **Legacy `Transaction`** — partially signed *in place* with the provided
  blockhash, so a partially signed transaction from a co-signer keeps its
  existing signatures.
* **`VersionedTransaction`** — rebuilt with the provided blockhash, preserving
  the compiled instructions, account keys and any address-table lookups, then
  fully re-signed by the wallet and every additional signer. A `MessageV0`
  message keeps its `address_table_lookups`; a legacy message inside a
  versioned transaction is rebuilt with
  `Message.new_with_compiled_instructions`.

```python
from solders.hash import Hash
from solders.transaction import Transaction, VersionedTransaction

from blockchainpype.solana.wallet.signer import SolanaSigner
from blockchainpype.solana.wallet.wallet import SolanaWallet


def sign_either(
    wallet: SolanaWallet,
    transaction: Transaction | VersionedTransaction,
    blockhash: Hash,
    cosigners: list[SolanaSigner],
) -> Transaction | VersionedTransaction:
    return wallet.sign_transaction(
        transaction, blockhash, additional_signers=cosigners
    )
```

`sign_transaction` raises `ValueError` when the wallet has no signer.

### Sending and tracking

`wallet.sign_and_send_transaction(client_operation_id, transaction, recent_blockhash, ...)`
is synchronous: it signs, records the transaction in the tracker and schedules
the broadcast as a background task on the running event loop. Re-calling it
with the `client_operation_id` of an already-signed transaction is an
idempotent retry.

`wallet.get_transaction_update(transaction, timeout, raise_timeout, **kwargs)`
polls `getSignatureStatuses` every 2 s (override with `poll_interval`) and maps
the result:

| Signature status | Reported state |
| --- | --- |
| `err` set | `FAILED` (with the receipt and `other_data["error"]`) |
| `Finalized` | `FINALIZED` (with the receipt) |
| `Confirmed` | `CONFIRMED` (with the receipt) |
| processed / unknown | keeps polling |

On timeout it raises `TimeoutError` or, with `raise_timeout=False`, returns an
update carrying the unchanged state.

```python
import asyncio
from datetime import timedelta

from solders.message import Message
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.wallet.wallet import SolanaWallet


async def send_sol(wallet: SolanaWallet, to: str, lamports: int) -> None:
    blockchain = wallet.blockchain
    instruction = transfer(
        TransferParams(
            from_pubkey=wallet.address.raw,
            to_pubkey=SolanaAddress.from_string(to).raw,
            lamports=lamports,
        )
    )
    recent_blockhash = await blockchain.fetch_recent_blockhash()
    message = Message.new_with_blockhash(
        [instruction], wallet.address.raw, recent_blockhash
    )

    transaction = wallet.sign_and_send_transaction(
        client_operation_id="transfer-sol-1",
        transaction=Transaction.new_unsigned(message),
        recent_blockhash=recent_blockhash,
    )
    update = await wallet.get_transaction_update(
        transaction, timeout=timedelta(seconds=60), raise_timeout=False
    )
    print(update.new_state)


if __name__ == "__main__":
    # Build the wallet as shown above, then:
    #     asyncio.run(send_sol(wallet, "<recipient>", 100_000_000))
    print(asyncio.iscoroutinefunction(send_sol))
```

## SPL tokens

### Associated token accounts

The ATA for a `(owner, mint)` pair is derived deterministically — no RPC call
is involved — both on the blockchain and on the token program:

```python
from blockchainpype.solana.blockchain.blockchain import SolanaBlockchain
from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.dapp.token import (
    SPLTokenProgram,
    SPLTokenProgramConfiguration,
)


def derive(owner: SolanaAddress, mint: SolanaAddress) -> SolanaAddress:
    # Static helper on the blockchain class (canonical SPL Token program).
    return SolanaBlockchain.derive_associated_token_account(owner, mint)


def derive_via_program(
    platform, owner: SolanaAddress, mint: SolanaAddress
) -> SolanaAddress:
    # Program-scoped: uses the program's own address as token_program_id, so a
    # configuration pointing at another token program derives its own ATAs.
    program = SPLTokenProgram(SPLTokenProgramConfiguration(platform=platform))
    return program.get_associated_token_account(owner, mint)
```

### Program interface

`SPLTokenProgram` is a `SolanaProgram` whose configuration defaults to the
canonical SPL Token program address and to a small in-memory IDL
(`SPL_TOKEN_IDL`) that merely documents the supported instruction names —
instructions are built with the `spl.token` helpers shipped with `solana-py`,
so no IDL-driven encoding is needed.

| Method | What it does |
| --- | --- |
| `get_associated_token_account(owner, mint)` | Derives the ATA |
| `get_token_account_balance(token_account)` | Decimal-adjusted balance of a token account |
| `get_balance_of(owner, mint)` | Balance through the owner's ATA (the RPC call fails if the ATA does not exist) |
| `get_token_accounts_by_owner(owner, mint)` | Every token account of an owner for a mint |
| `build_transfer_instruction(source_owner, destination_owner, mint, raw_amount, decimals)` | A `transferChecked` instruction between the two owners' ATAs |
| `place_transfer(wallet, token, destination, amount, client_operation_id=None)` | Builds the transfer, wraps it in a legacy transaction with the wallet as fee payer, then signs, broadcasts and tracks it |

`transferChecked` is used rather than `transfer` because it asserts the mint
and decimals on-chain, so a decimals mismatch fails the instruction instead of
moving the wrong amount.

```python
import asyncio
from decimal import Decimal

from blockchainpype.solana.asset import SolanaAssetData
from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.dapp.token import (
    SPLToken,
    SPLTokenProgram,
    SPLTokenProgramConfiguration,
)
from blockchainpype.solana.wallet.wallet import SolanaWallet

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


async def transfer_usdc(wallet: SolanaWallet, destination: str) -> None:
    platform = wallet.blockchain.platform
    mint = SolanaAddress.from_string(USDC_MINT)

    program = SPLTokenProgram(SPLTokenProgramConfiguration(platform=platform))
    token = SPLToken(
        platform=platform,
        identifier=mint,
        mint=mint,
        program=program,
        data=SolanaAssetData(name="USD Coin", symbol="USDC", decimals=6),
    )

    transaction = await program.place_transfer(
        wallet,
        token,
        destination=SolanaAddress.from_string(destination),
        amount=Decimal("10"),
    )
    print(transaction.client_operation_id)


assert asyncio.iscoroutinefunction(transfer_usdc)
```

### The `SPLToken` asset

`SPLToken` is a `SolanaAsset` carrying the `mint` and, optionally, the
`program`. When `data` is omitted, `await token.initialize_data()` fetches the
mint's **decimals** from `getTokenSupply` (the only field that affects amount
scaling) and sets `name`/`symbol` to the mint address string — Solana mints
carry no on-chain name or symbol; that lives in Metaplex metadata, which this
library does not query. `initialize_data()` raises `ValueError` when no program
is set and the data is missing.

## Programs and IDLs

`SolanaProgram` is built from a `SolanaProgramConfiguration` (platform,
address, `idl_configuration`). `await program.initialize()` loads the IDL;
`program.is_initialized` guards it and `create_instruction` raises `ValueError`
before it.

Two IDL sources ship with the library:

* `SolanaLocalFileIDL(file_name=..., folder_path=common_idl_path)` — reads a
  JSON object from `common/idl` (bundled: `solend.json`, `jupiter_dca.json`).
* `SolanaDictIDL(idl=...)` — an in-memory dictionary.

Two IDL **layouts** are supported everywhere:

| Layout | `instructions` shape | Example |
| --- | --- | --- |
| Real Anchor IDL | a **list** of instruction definitions | `common/idl/jupiter_dca.json` |
| Simplified hand-written IDL | a **dict** keyed by instruction name | `common/idl/solend.json` |

`find_idl_instruction(idl, name)` looks a definition up in either shape.

### Anchor instructions: discriminators + borsh

For a list-form Anchor IDL, `create_instruction(name, accounts, args=...)`
encodes the data as the 8-byte discriminator followed by the borsh-encoded
arguments declared in the IDL:

* The discriminator is the instruction's explicit `discriminator` field when
  present (Anchor ≥ 0.30 emits one), otherwise the first 8 bytes of
  `sha256("global:<snake_case_name>")`. Anchor exposes camelCase names while
  discriminators derive from the Rust snake_case method name, so
  `to_snake_case` bridges the two (`openDcaV2` → `open_dca_v2`).
* Arguments are encoded little-endian by `encode_borsh_value`, which supports
  the primitives `u8`–`u128`, `i8`–`i128`, `f32`/`f64`, `bool`,
  `publicKey`/`pubkey`, `string`, `bytes`, and the composites
  `{"option": …}`, `{"vec": …}`, `{"array": [inner, length]}` and
  `{"defined": name}` (struct kinds and unit-variant enums, resolved against
  the IDL `types` section).
* Unknown argument names, missing non-optional arguments and values that do not
  fit their type all raise `ValueError`.

```python
from blockchainpype.solana.dapp.program import (
    anchor_discriminator,
    encode_borsh_value,
    to_snake_case,
)

assert to_snake_case("openDcaV2") == "open_dca_v2"
assert len(anchor_discriminator("openDcaV2")) == 8

assert encode_borsh_value(1, "u8") == b"\x01"
assert encode_borsh_value(258, "u16") == b"\x02\x01"
assert encode_borsh_value(None, {"option": "u64"}) == b"\x00"
assert encode_borsh_value("hi", "string") == b"\x02\x00\x00\x00hi"
```

```python
import asyncio

from solders.instruction import AccountMeta

from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.dapp.idl import SolanaLocalFileIDL
from blockchainpype.solana.dapp.program import (
    SolanaProgram,
    SolanaProgramConfiguration,
)


async def build_anchor_instruction(platform, program_id: str) -> None:
    program = SolanaProgram(
        SolanaProgramConfiguration(
            platform=platform,
            address=SolanaAddress.from_string(program_id),
            idl_configuration=SolanaLocalFileIDL(file_name="jupiter_dca.json"),
        )
    )
    await program.initialize()

    # Every non-optional argument declared by the IDL must be supplied;
    # `{"option": ...}` arguments may be omitted (encoded as None).
    instruction = program.create_instruction(
        "openDcaV2",
        accounts=[
            AccountMeta(SolanaAddress.from_string(program_id).raw, False, False),
        ],
        args={
            "applicationIdx": 0,
            "inAmount": 1_000_000,
            "inAmountPerCycle": 100_000,
            "cycleFrequency": 3600,
            "minPrice": None,
            "maxPrice": None,
            "startAt": None,
        },
    )
    print(instruction.data[:8].hex())


assert asyncio.iscoroutinefunction(build_anchor_instruction)
```

### Simplified IDLs: pre-encoded data

Dict-form IDLs carry no argument specs, so they act purely as a **name
registry**. Instructions with a payload must be given fully pre-encoded `data`
(including any protocol-specific discriminant); passing `args` for such an
instruction raises `ValueError`, as does passing both `data` and `args`.

This is exactly how the Solend integration works: Solend is a fork of the SPL
token-lending program, not an Anchor program, so every instruction is a
single-byte enum discriminant followed by little-endian fields.

```python
from solders.instruction import AccountMeta

from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.dapp.program import SolanaProgram


def build_raw(program: SolanaProgram, account: SolanaAddress) -> None:
    # Discriminant 14 = DepositReserveLiquidityAndObligationCollateral,
    # followed by the u64 little-endian liquidity amount.
    data = bytes([14]) + (1_000_000).to_bytes(8, "little")
    instruction = program.create_instruction(
        "deposit",
        accounts=[AccountMeta(account.raw, False, True)],
        data=data,
    )
    print(instruction.program_id)
```

## Explorer

`SolscanExplorer` is a pure link builder — it performs **no** network access
(Solscan's HTTP API is paid and intentionally not wrapped).

```python
from blockchainpype.solana.blockchain.identifier import (
    SolanaAddress,
    SolanaTransactionSignature,
)
from blockchainpype.solana.explorer.solscan import (
    SolscanConfiguration,
    SolscanExplorer,
)

explorer = SolscanExplorer(SolscanConfiguration())
mint = SolanaAddress.from_string("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")

print(explorer.get_address_link(mint))   # https://solscan.io/account/<pubkey>
print(explorer.get_token_link(mint))     # https://solscan.io/token/<mint>
print(explorer.get_block_link(123456))   # https://solscan.io/block/123456


def tx_link(signature: SolanaTransactionSignature) -> str:
    return explorer.get_transaction_link(signature)  # https://solscan.io/tx/<sig>
```

Point `base_url` at another deployment (e.g. a devnet view) to change every
generated link. When an explorer is configured on the blockchain, transaction
updates produced by the wallet and by `send_transaction` carry
`explorer_link` automatically.

## See also

* [DApps guide](dapps.md) — the Solend money-market integration.
* [Quickstart](../quickstart.md) — end-to-end setup.
