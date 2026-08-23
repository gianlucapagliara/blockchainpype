"""
Unit tests for the SPL Token program layer.

Covers:
- Default SPL Token program configuration (canonical program id, bundled IDL)
- Associated token account derivation (asserted against spl.token helpers)
- transferChecked instruction building: exact data bytes (discriminant 12 +
  amount LE + decimals) and account ordering
- Decimal scaling of token balances (RPC boundary mocked with realistic
  solders response payloads)
- place_transfer wiring: raw amount scaling, legacy message construction, and
  hand-off to the wallet's public sign-and-send API
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from financepype.operators.blockchains.models import BlockchainPlatform
from solders.account_decoder import UiTokenAmount
from solders.hash import Hash
from solders.pubkey import Pubkey
from solders.rpc.responses import (
    GetLatestBlockhashResp,
    GetTokenAccountBalanceResp,
    GetTokenSupplyResp,
    RpcBlockhash,
    RpcResponseContext,
)
from solders.transaction import Transaction
from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.instructions import get_associated_token_address

from blockchainpype.initializer import BlockchainsInitializer, SupportedBlockchainType
from blockchainpype.solana.asset import SolanaAssetData
from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.dapp.token import (
    SPLToken,
    SPLTokenProgram,
    SPLTokenProgramConfiguration,
)

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


@pytest.fixture(scope="session", autouse=True)
def setup_blockchains():
    """Setup blockchain configurations for testing."""
    from blockchainpype.factory import BlockchainFactory

    BlockchainFactory.reset()
    BlockchainsInitializer.configure()


@pytest.fixture
def test_platform():
    """Create a test Solana blockchain platform."""
    return BlockchainPlatform(
        identifier="solana",
        type=SupportedBlockchainType.SOLANA.value,
        chain_id=None,
    )


@pytest.fixture
def token_program(test_platform):
    return SPLTokenProgram(SPLTokenProgramConfiguration(platform=test_platform))


@pytest.fixture
def mint():
    return SolanaAddress.from_string(USDC_MINT)


@pytest.fixture
def owner():
    return SolanaAddress.from_raw(Pubkey.new_unique())


@pytest.fixture
def recipient():
    return SolanaAddress.from_raw(Pubkey.new_unique())


@pytest.fixture
def usdc_token(test_platform, mint, token_program):
    return SPLToken(
        platform=test_platform,
        identifier=mint,
        data=SolanaAssetData(name="USD Coin", symbol="USDC", decimals=6),
        mint=mint,
        program=token_program,
    )


class StubWallet:
    """Stand-in for SolanaWallet exposing only the public sign/send API."""

    def __init__(self, address: SolanaAddress) -> None:
        self.address = address
        self.calls: list[dict] = []
        self.result = MagicMock(name="tracked_transaction")

    def sign_and_send_transaction(
        self, client_operation_id, transaction, recent_blockhash
    ):
        self.calls.append(
            {
                "client_operation_id": client_operation_id,
                "transaction": transaction,
                "recent_blockhash": recent_blockhash,
            }
        )
        return self.result


class TestSPLTokenProgramConfiguration:
    """Test SPL Token program defaults."""

    def test_default_address_is_token_program(self, token_program):
        assert token_program.address.raw == TOKEN_PROGRAM_ID
        assert token_program.address.string == str(TOKEN_PROGRAM_ID)

    async def test_initialize_uses_bundled_idl(self, token_program):
        assert not token_program.is_initialized
        await token_program.initialize()
        assert token_program.is_initialized
        assert token_program.idl is not None
        assert "transferChecked" in token_program.idl["instructions"]


class TestAssociatedTokenAccounts:
    """Test ATA derivation against the spl.token reference helper."""

    def test_get_associated_token_account(self, token_program, owner, mint):
        derived = token_program.get_associated_token_account(owner, mint)
        expected = get_associated_token_address(owner.raw, mint.raw)
        assert derived.raw == expected
        assert derived.string == str(expected)


class TestBuildTransferInstruction:
    """Test transferChecked instruction construction."""

    def test_instruction_data_and_accounts(self, token_program, owner, recipient, mint):
        raw_amount = 1_500_000
        decimals = 6
        instruction = token_program.build_transfer_instruction(
            source_owner=owner,
            destination_owner=recipient,
            mint=mint,
            raw_amount=raw_amount,
            decimals=decimals,
        )

        # TransferChecked discriminant (12) + u64 LE amount + u8 decimals
        assert instruction.data == bytes([12]) + raw_amount.to_bytes(
            8, "little"
        ) + bytes([decimals])
        assert instruction.program_id == TOKEN_PROGRAM_ID

        source_ata = get_associated_token_address(owner.raw, mint.raw)
        dest_ata = get_associated_token_address(recipient.raw, mint.raw)

        accounts = instruction.accounts
        assert len(accounts) == 4
        assert accounts[0].pubkey == source_ata
        assert accounts[0].is_writable and not accounts[0].is_signer
        assert accounts[1].pubkey == mint.raw
        assert not accounts[1].is_writable and not accounts[1].is_signer
        assert accounts[2].pubkey == dest_ata
        assert accounts[2].is_writable and not accounts[2].is_signer
        assert accounts[3].pubkey == owner.raw
        assert accounts[3].is_signer and not accounts[3].is_writable


class TestTokenBalances:
    """Test balance fetching with the RPC boundary mocked."""

    def _mock_balance_client(self, amount: str, decimals: int) -> MagicMock:
        client = MagicMock()
        client.get_token_account_balance = AsyncMock(
            return_value=GetTokenAccountBalanceResp(
                context=RpcResponseContext(slot=1000),
                value=UiTokenAmount(
                    ui_amount=float(Decimal(amount) / 10**decimals),
                    decimals=decimals,
                    amount=amount,
                    ui_amount_string=str(Decimal(amount) / 10**decimals),
                ),
            )
        )
        return client

    async def test_get_token_account_balance_scales_by_decimals(
        self, token_program, owner, monkeypatch
    ):
        client = self._mock_balance_client(amount="1500000", decimals=6)
        monkeypatch.setattr(token_program.blockchain, "rpc_client", client)

        balance = await token_program.get_token_account_balance(owner)

        assert balance == Decimal("1.5")
        client.get_token_account_balance.assert_awaited_once_with(owner.raw)

    async def test_get_balance_of_derives_ata(
        self, token_program, owner, mint, monkeypatch
    ):
        client = self._mock_balance_client(amount="2500123456", decimals=6)
        monkeypatch.setattr(token_program.blockchain, "rpc_client", client)

        balance = await token_program.get_balance_of(owner, mint)

        assert balance == Decimal("2500.123456")
        expected_ata = get_associated_token_address(owner.raw, mint.raw)
        client.get_token_account_balance.assert_awaited_once_with(expected_ata)


class TestPlaceTransfer:
    """Test place_transfer end-to-end up to the wallet boundary."""

    @pytest.fixture
    def blockhash(self):
        return Hash.new_unique()

    @pytest.fixture
    def rpc_client(self, blockhash):
        client = MagicMock()
        client.get_latest_blockhash = AsyncMock(
            return_value=GetLatestBlockhashResp(
                context=RpcResponseContext(slot=1000),
                value=RpcBlockhash(
                    blockhash=blockhash, last_valid_block_height=100_000
                ),
            )
        )
        return client

    async def test_place_transfer_builds_and_sends(
        self,
        token_program,
        usdc_token,
        owner,
        recipient,
        mint,
        blockhash,
        rpc_client,
        monkeypatch,
    ):
        monkeypatch.setattr(token_program.blockchain, "rpc_client", rpc_client)
        wallet = StubWallet(owner)

        result = await token_program.place_transfer(
            wallet=wallet,
            token=usdc_token,
            destination=recipient,
            amount=Decimal("1.5"),
        )

        assert result is wallet.result
        assert len(wallet.calls) == 1
        call = wallet.calls[0]

        assert call["recent_blockhash"] == blockhash
        assert usdc_token.mint.string in call["client_operation_id"]
        assert recipient.string in call["client_operation_id"]

        transaction = call["transaction"]
        assert isinstance(transaction, Transaction)
        message = transaction.message
        assert message.recent_blockhash == blockhash
        # The wallet (source owner) is the fee payer and only signer
        assert message.account_keys[0] == owner.raw
        assert message.header.num_required_signatures == 1

        assert len(message.instructions) == 1
        compiled = message.instructions[0]
        # Decimal("1.5") with 6 decimals -> 1_500_000 raw units
        assert compiled.data == bytes([12]) + (1_500_000).to_bytes(8, "little") + bytes(
            [6]
        )
        assert message.account_keys[compiled.program_id_index] == TOKEN_PROGRAM_ID

        source_ata = get_associated_token_address(owner.raw, mint.raw)
        dest_ata = get_associated_token_address(recipient.raw, mint.raw)
        referenced_keys = [message.account_keys[index] for index in compiled.accounts]
        assert referenced_keys == [source_ata, mint.raw, dest_ata, owner.raw]

    async def test_place_transfer_custom_operation_id(
        self,
        token_program,
        usdc_token,
        owner,
        recipient,
        rpc_client,
        monkeypatch,
    ):
        monkeypatch.setattr(token_program.blockchain, "rpc_client", rpc_client)
        wallet = StubWallet(owner)

        await token_program.place_transfer(
            wallet=wallet,
            token=usdc_token,
            destination=recipient,
            amount=Decimal("0.000001"),
            client_operation_id="my-transfer-1",
        )

        call = wallet.calls[0]
        assert call["client_operation_id"] == "my-transfer-1"
        # Smallest representable unit for 6 decimals -> 1 raw unit
        compiled = call["transaction"].message.instructions[0]
        assert compiled.data == bytes([12]) + (1).to_bytes(8, "little") + bytes([6])


class TestSPLTokenAsset:
    """Test the SPLToken asset model."""

    def test_construction_and_conversions(self, usdc_token, mint):
        assert usdc_token.mint == mint
        assert usdc_token.data.decimals == 6
        assert usdc_token.convert_to_raw(Decimal("1.5")) == 1_500_000
        assert usdc_token.convert_to_decimals(2_500_000) == Decimal("2.5")

    def test_program_is_optional(self, test_platform, mint):
        token = SPLToken(
            platform=test_platform,
            identifier=mint,
            data=SolanaAssetData(name="USD Coin", symbol="USDC", decimals=6),
            mint=mint,
        )
        assert token.program is None

    async def test_initialize_data_noop_when_data_present(self, usdc_token):
        original = usdc_token.data
        await usdc_token.initialize_data()
        assert usdc_token.data is original

    async def test_initialize_data_requires_program(self, test_platform, mint):
        token = SPLToken(platform=test_platform, identifier=mint, mint=mint)
        assert token.data is None
        with pytest.raises(ValueError, match="program is not initialized"):
            await token.initialize_data()

    async def test_initialize_data_fetches_decimals_from_chain(
        self, test_platform, mint, token_program, monkeypatch
    ):
        client = MagicMock()
        client.get_token_supply = AsyncMock(
            return_value=GetTokenSupplyResp(
                context=RpcResponseContext(slot=1000),
                value=UiTokenAmount(
                    ui_amount=1000.0,
                    decimals=6,
                    amount="1000000000",
                    ui_amount_string="1000",
                ),
            )
        )
        monkeypatch.setattr(token_program.blockchain, "rpc_client", client)

        token = SPLToken(
            platform=test_platform,
            identifier=mint,
            mint=mint,
            program=token_program,
        )
        await token.initialize_data()

        assert token.data is not None
        assert token.data.decimals == 6
        assert token.data.name == mint.string
        assert token.data.symbol == mint.string
        client.get_token_supply.assert_awaited_once_with(mint.raw)
