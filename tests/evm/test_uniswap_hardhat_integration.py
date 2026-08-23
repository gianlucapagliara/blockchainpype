"""
End-to-end integration tests of the library stack against a live Hardhat node.

Unlike the unit suites (which stub the JSON-RPC boundary), every call here goes
through a real Hardhat node started by the session-scoped ``hardhat_env``
fixture and the contracts deployed by ``scripts/deploy-all.js``:

* the wallet lifecycle (:class:`EthereumWallet`): nonce syncing, native balance
  fetching, building/signing/broadcasting a transfer and polling its receipt;
* the ERC-20 interface (:class:`ERC20Contract`) bound to the deployed
  TestToken: metadata, balances, transfer, approve and transferFrom;
* the Uniswap V2 strategy (:class:`UniswapV2`) against the deployed
  TestUniswapV2Factory + SimpleV2Router: reserves, quoting and a real swap;
* the :class:`UniswapDEX` facade configured for the local network.

Amounts are asserted exactly: quotes are cross-checked both against the
canonical constant-product formula (hard-coded expected integers) and against
the router's own on-chain ``getAmountsOut``/``getAmountsIn``, and every
state-changing test compares raw balances before and after.

The whole module is marked ``integration`` (it needs a local Hardhat node and
npm dependencies), so it is skipped by the default pytest run. Run it with:

    uv run pytest tests/evm/test_uniswap_hardhat_integration.py -m "" --timeout=300

Tests that change chain state take the ``blockchain_snapshot`` fixture, so the
seeded deployment state (balances and pair reserves) is restored afterwards and
the exact-value assertions of the read-only tests keep holding.

All async tests run in the session-scoped event loop so they share the
session-scoped ``hardhat_env`` fixture (and its single Hardhat node).
"""

import math
from datetime import timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from financepype.operations.transactions.models import BlockchainTransactionState
from pydantic import SecretStr
from web3 import Web3
from web3.types import TxParams, Wei

from blockchainpype.dapps.router.models import SwapMode
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.abi import EthereumLocalFileABI
from blockchainpype.evm.dapp.erc20 import (
    ERC20Contract,
    ERC20ContractConfiguration,
    ERC20Token,
)
from blockchainpype.evm.dapp.uniswap.dex import UniswapConfiguration, UniswapDEX
from blockchainpype.evm.dapp.uniswap.v2 import UniswapV2
from blockchainpype.evm.dapp.unsigned import UNSIGNED_TX_DATA_KEY, unsigned_tx_params
from blockchainpype.evm.wallet.identifier import EthereumWalletIdentifier
from blockchainpype.evm.wallet.signer import EthereumSignerConfiguration
from blockchainpype.evm.wallet.wallet import EthereumWallet, EthereumWalletConfiguration
from blockchainpype.initializer import HARDHAT_CHAIN_ID
from tests.evm.hardhat import get_hardhat_accounts, get_hardhat_private_keys
from tests.evm.test_wallet import drain_background_tasks

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ONE_ETH_WEI = Web3.to_wei(1, "ether")

# === Deployment constants (see common/hardhat/scripts/deploy-all.js) ===

TOKEN_DECIMALS = 18
TOKEN_UNIT = 10**TOKEN_DECIMALS
TOKEN_NAME = "Test Token"
TOKEN_SYMBOL = "TEST"

# TestToken mints 1,000,000 to its deployer in the constructor; deploy-all.js
# mints another 1,000 to the deployer and 1,000 into the pair.
DEPLOYER_TOKEN_BALANCE_RAW = 1_001_000 * TOKEN_UNIT
SEEDED_RESERVE_RAW = 1_000 * TOKEN_UNIT

# === Exact constant-product results over the seeded 1000/1000 reserves ===
#
#   out = in * 997 * reserve_out // (reserve_in * 1000 + in * 997)
#   in  = reserve_in * out * 1000 // ((reserve_out - out) * 997) + 1

SWAP_INPUT_RAW = 10 * TOKEN_UNIT
SWAP_OUTPUT_RAW = 9_871_580_343_970_612_988
FACADE_INPUT_RAW = 5 * TOKEN_UNIT
FACADE_OUTPUT_RAW = 4_960_273_038_901_078_125
EXACT_OUTPUT_INPUT_RAW = 10_131_404_313_951_956_881


def raw_to_decimal(raw_amount: int) -> Decimal:
    """Convert a raw 18-decimals token amount to its decimal representation."""
    return Decimal(raw_amount) / Decimal(TOKEN_UNIT)


def constant_product_amount_out(
    amount_in: int, reserve_in: int, reserve_out: int
) -> int:
    """Canonical Uniswap V2 output for an exact input (0.3% fee, integer math)."""
    amount_in_with_fee = amount_in * 997
    return (amount_in_with_fee * reserve_out) // (
        reserve_in * 1000 + amount_in_with_fee
    )


def constant_product_amount_in(
    amount_out: int, reserve_in: int, reserve_out: int
) -> int:
    """Canonical Uniswap V2 input for an exact output (0.3% fee, integer math)."""
    return (reserve_in * amount_out * 1000) // ((reserve_out - amount_out) * 997) + 1


# === Helpers ===


def build_hardhat_wallet(blockchain, account_index: int) -> EthereumWallet:
    """Build a wallet for one of the deterministic Hardhat accounts."""
    return EthereumWallet(
        configuration=EthereumWalletConfiguration(
            identifier=EthereumWalletIdentifier(
                platform=blockchain.platform,
                name=f"hardhat-account-{account_index}",
                address=EthereumAddress.from_string(
                    get_hardhat_accounts()[account_index]
                ),
            ),
            signer=EthereumSignerConfiguration(
                private_key=SecretStr(get_hardhat_private_keys()[account_index]),
            ),
        ),
        blockchain=blockchain,
    )


async def build_erc20_token(blockchain, address: str) -> ERC20Token:
    """Build an ERC20Token bound to a deployed contract, with its data loaded.

    The ABI is the canonical ERC-20 interface shipped with the library
    (``ERC20.json``), which the deployed TestToken implements.
    """
    contract = ERC20Contract(
        ERC20ContractConfiguration(
            platform=blockchain.platform,
            address=EthereumAddress.from_string(address),
        )
    )
    token = ERC20Token(
        platform=blockchain.platform,
        identifier=contract.address,
        contract=contract,
    )
    await token.initialize_data()
    return token


async def confirm_transaction(wallet: EthereumWallet, transaction):
    """Await the broadcast of a tracked transaction and its confirmation.

    ``sign_and_send_transaction`` broadcasts in a background task, so the tasks
    are drained first; the receipt is then polled through the wallet's own
    ``get_transaction_update``.
    """
    await drain_background_tasks(wallet)
    assert transaction.operator_operation_id is not None, (
        f"Transaction {transaction.client_operation_id} was rejected on broadcast "
        f"(state: {transaction.current_state})"
    )

    update = await wallet.get_transaction_update(
        transaction,
        timeout=timedelta(seconds=60),
        raise_timeout=True,
        poll_interval=0.1,
    )
    assert update.new_state == BlockchainTransactionState.CONFIRMED
    assert update.receipt is not None
    return update


# === Fixtures ===


@pytest_asyncio.fixture(loop_scope="session")
async def wallet(blockchain) -> EthereumWallet:
    """Wallet for Hardhat account 0 (the deployer), with its nonce synced."""
    wallet = build_hardhat_wallet(blockchain, 0)
    await drain_background_tasks(wallet)
    await wallet.sync_nonce()
    return wallet


@pytest_asyncio.fixture(loop_scope="session")
async def second_wallet(blockchain) -> EthereumWallet:
    """Wallet for Hardhat account 1, used as an allowance spender."""
    wallet = build_hardhat_wallet(blockchain, 1)
    await drain_background_tasks(wallet)
    await wallet.sync_nonce()
    return wallet


@pytest_asyncio.fixture(loop_scope="session")
async def test_token(blockchain, deployed_contracts) -> ERC20Token:
    """The deployed TestToken as an ERC20Token asset."""
    return await build_erc20_token(blockchain, deployed_contracts["TestToken"])


@pytest_asyncio.fixture(loop_scope="session")
async def test_token2(blockchain, deployed_contracts) -> ERC20Token:
    """The deployed TestToken2 as an ERC20Token asset."""
    return await build_erc20_token(blockchain, deployed_contracts["TestToken2"])


@pytest_asyncio.fixture(loop_scope="session")
async def uniswap_v2(blockchain, deployed_contracts, wallet) -> UniswapV2:
    """Uniswap V2 strategy bound to the deployed factory/router and wallet."""
    strategy = UniswapV2(
        blockchain=blockchain,
        factory_address=deployed_contracts["TestUniswapV2Factory"],
        router_address=deployed_contracts["SimpleV2Router"],
    )
    strategy.set_wallet(wallet)
    return strategy


@pytest_asyncio.fixture(loop_scope="session")
async def uniswap_dex(blockchain, deployed_contracts, wallet) -> UniswapDEX:
    """UniswapDEX facade configured for the local hardhat deployment."""
    configuration = UniswapConfiguration.local_network(
        platform=blockchain.platform,
        v2_factory_address=deployed_contracts["TestUniswapV2Factory"],
        v2_router_address=deployed_contracts["SimpleV2Router"],
    )
    return UniswapDEX(blockchain=blockchain, configuration=configuration, wallet=wallet)


@pytest_asyncio.fixture(loop_scope="session")
async def router_contract(blockchain, deployed_contracts):
    """The deployed SimpleV2Router read through web3 with the canonical ABI.

    Used as an independent on-chain oracle for the library's quoting math.
    """
    abi = await EthereumLocalFileABI(file_name="UniswapV2Router02.json").get_abi()
    return blockchain.web3.eth.contract(
        address=Web3.to_checksum_address(deployed_contracts["SimpleV2Router"]),
        abi=abi,
    )


class TestWalletLifecycle:
    """The wallet against the live node: nonce, balances, transfer, receipt."""

    pytestmark = pytest.mark.asyncio(loop_scope="session")

    async def test_wallet_is_bound_to_the_hardhat_node(self, wallet, blockchain):
        """The wallet signs for account 0 on the hardhat platform."""
        assert wallet.address.string == get_hardhat_accounts()[0]
        assert wallet.blockchain is blockchain
        assert wallet.platform.chain_id == HARDHAT_CHAIN_ID
        assert wallet.signer is not None
        assert wallet.signer.address == get_hardhat_accounts()[0]

    async def test_sync_nonce_matches_the_node_transaction_count(
        self, wallet, blockchain
    ):
        """sync_nonce stores exactly the node's transaction count."""
        await wallet.sync_nonce()
        expected = await blockchain.fetch_transaction_count(wallet.address)
        assert wallet.last_nonce == expected

        # Allocation hands out consecutive nonces without touching the chain
        assert wallet.allocate_nonce() == expected
        assert wallet.allocate_nonce() == expected + 1
        assert wallet.last_nonce == expected + 2

    async def test_native_balance_matches_the_node(self, wallet, hardhat_env):
        """fetch_balance reports the node's wei balance in decimal ETH."""
        balance_wei = await hardhat_env.get_account_balance_wei(wallet.address.raw)
        balance = await wallet.fetch_balance(wallet.blockchain.native_asset)
        assert balance == Decimal(balance_wei) / Decimal(10**18)

    async def test_native_transfer_exact_wei_deltas(
        self, blockchain_snapshot, wallet, blockchain, hardhat_env
    ):
        """Build, sign, broadcast and confirm a 1 ETH transfer."""
        recipient = EthereumAddress.from_string(get_hardhat_accounts()[1])

        initial_sender = await hardhat_env.get_account_balance_wei(wallet.address.raw)
        initial_recipient = await hardhat_env.get_account_balance_wei(recipient.raw)
        initial_nonce = wallet.last_nonce

        tx_params = await wallet.build_transaction(
            tx_data=TxParams(to=recipient.raw, value=Wei(ONE_ETH_WEI))
        )
        assert tx_params["from"] == wallet.address.raw
        assert tx_params["chainId"] == HARDHAT_CHAIN_ID
        assert tx_params["to"] == recipient.raw
        assert tx_params["value"] == ONE_ETH_WEI
        # The node's own estimate, carrying the configured 1.3x buffer
        estimated_gas = await blockchain.web3.eth.estimate_gas(
            TxParams(
                {
                    "from": wallet.address.raw,
                    "to": recipient.raw,
                    "value": Wei(ONE_ETH_WEI),
                }
            )
        )
        assert tx_params["gas"] == math.ceil(estimated_gas * 1.3)
        assert tx_params["maxFeePerGas"] >= tx_params["maxPriorityFeePerGas"]

        transaction = wallet.sign_and_send_transaction(
            client_operation_id="hardhat-native-transfer",
            tx_data=dict(tx_params),
        )
        assert wallet.last_nonce == initial_nonce + 1

        update = await confirm_transaction(wallet, transaction)
        receipt = update.receipt
        assert receipt.status == 1
        assert receipt.gas_used == 21000

        fee_wei = int(receipt.fee_amount)
        assert fee_wei == receipt.gas_used * receipt.effective_gas_price
        assert update.other_data["fee"].amount == Decimal(fee_wei) / Decimal(10**18)
        assert update.other_data["fee"].asset == wallet.blockchain.native_asset

        final_sender = await hardhat_env.get_account_balance_wei(wallet.address.raw)
        final_recipient = await hardhat_env.get_account_balance_wei(recipient.raw)
        assert final_recipient == initial_recipient + ONE_ETH_WEI
        assert final_sender == initial_sender - ONE_ETH_WEI - fee_wei

    async def test_sign_and_send_is_idempotent_per_operation_id(
        self, blockchain_snapshot, wallet, hardhat_env
    ):
        """Re-sending the same operation id returns the tracked transaction."""
        recipient = EthereumAddress.from_string(get_hardhat_accounts()[2])
        tx_params = await wallet.build_transaction(
            tx_data=TxParams(to=recipient.raw, value=Wei(ONE_ETH_WEI))
        )

        transaction = wallet.sign_and_send_transaction(
            client_operation_id="hardhat-idempotent-transfer",
            tx_data=dict(tx_params),
        )
        await confirm_transaction(wallet, transaction)
        nonce_after_send = wallet.last_nonce
        balance_after_send = await hardhat_env.get_account_balance_wei(recipient.raw)

        retried = wallet.sign_and_send_transaction(
            client_operation_id="hardhat-idempotent-transfer",
            tx_data=dict(tx_params),
        )
        await drain_background_tasks(wallet)

        assert retried is transaction
        assert wallet.last_nonce == nonce_after_send
        assert (
            await hardhat_env.get_account_balance_wei(recipient.raw)
            == balance_after_send
        )


class TestERC20EndToEnd:
    """The ERC-20 interface against the deployed TestToken."""

    pytestmark = pytest.mark.asyncio(loop_scope="session")

    async def test_token_metadata(self, test_token, deployed_contracts):
        """Metadata is read from the deployed contract."""
        contract = test_token.contract
        assert contract.is_initialized
        assert test_token.address.raw == Web3.to_checksum_address(
            deployed_contracts["TestToken"]
        )
        assert await contract.get_name() == TOKEN_NAME
        assert await contract.get_symbol() == TOKEN_SYMBOL
        assert await contract.get_decimals() == TOKEN_DECIMALS
        assert test_token.data.name == TOKEN_NAME
        assert test_token.data.symbol == TOKEN_SYMBOL
        assert test_token.data.decimals == TOKEN_DECIMALS

    async def test_deployment_balances(self, test_token, wallet, deployed_contracts):
        """The deployer and the pair hold exactly the minted amounts."""
        contract = test_token.contract
        pair_address = EthereumAddress.from_string(
            deployed_contracts["TestUniswapV2Pair"]
        )

        assert (
            await contract.get_raw_balance_of(wallet.address)
            == DEPLOYER_TOKEN_BALANCE_RAW
        )
        assert await contract.get_balance_of(wallet.address) == Decimal("1001000")
        assert await contract.get_raw_balance_of(pair_address) == SEEDED_RESERVE_RAW

        # The wallet resolves ERC-20 balances through the same contract
        assert await wallet.fetch_balance(test_token) == Decimal("1001000")

        # 1,000,000 constructor mint + 1,000 deployer mint + 1,000 pair mint
        assert await contract.get_raw_total_supply() == 1_002_000 * TOKEN_UNIT
        assert await contract.get_total_supply() == Decimal("1002000")

    async def test_place_transfer_moves_exact_amounts(
        self, blockchain_snapshot, wallet, test_token
    ):
        """place_transfer transfers exactly the requested decimal amount."""
        contract = test_token.contract
        recipient = EthereumAddress.from_string(get_hardhat_accounts()[1])
        amount = Decimal("25.5")
        raw_amount = 25_500_000_000_000_000_000

        initial_sender = await contract.get_raw_balance_of(wallet.address)
        initial_recipient = await contract.get_raw_balance_of(recipient)

        transaction = await contract.place_transfer(
            wallet=wallet,
            recipient=recipient,
            amount=amount,
            client_operation_id="hardhat-erc20-transfer",
        )
        update = await confirm_transaction(wallet, transaction)
        assert update.receipt.status == 1

        assert (
            await contract.get_raw_balance_of(wallet.address)
            == initial_sender - raw_amount
        )
        assert (
            await contract.get_raw_balance_of(recipient)
            == initial_recipient + raw_amount
        )
        assert await contract.get_balance_of(recipient) == raw_to_decimal(
            initial_recipient + raw_amount
        )

    async def test_place_approve_sets_exact_allowance(
        self, blockchain_snapshot, wallet, test_token
    ):
        """place_approve grants exactly the requested allowance."""
        contract = test_token.contract
        spender = EthereumAddress.from_string(get_hardhat_accounts()[1])

        assert await contract.get_raw_allowance(wallet.address, spender) == 0

        transaction = await contract.place_approve(
            wallet=wallet,
            spender=spender,
            amount=Decimal("50"),
            client_operation_id="hardhat-erc20-approve",
        )
        update = await confirm_transaction(wallet, transaction)
        assert update.receipt.status == 1

        assert (
            await contract.get_raw_allowance(wallet.address, spender) == 50 * TOKEN_UNIT
        )
        assert await contract.get_allowance(wallet.address, spender) == Decimal("50")

    async def test_place_transfer_from_spends_the_allowance(
        self, blockchain_snapshot, wallet, second_wallet, test_token
    ):
        """A spender moves approved tokens, and the allowance is consumed."""
        contract = test_token.contract
        owner = wallet.address
        spender = second_wallet.address
        recipient = EthereumAddress.from_string(get_hardhat_accounts()[2])
        raw_amount = 30 * TOKEN_UNIT

        approval = await contract.place_approve(
            wallet=wallet,
            spender=spender,
            amount=Decimal("30"),
            client_operation_id="hardhat-erc20-approve-for-transfer-from",
        )
        await confirm_transaction(wallet, approval)

        initial_owner = await contract.get_raw_balance_of(owner)
        initial_recipient = await contract.get_raw_balance_of(recipient)

        transaction = await contract.place_transfer_from(
            wallet=second_wallet,
            sender=owner,
            recipient=recipient,
            amount=Decimal("30"),
            client_operation_id="hardhat-erc20-transfer-from",
        )
        update = await confirm_transaction(second_wallet, transaction)
        assert update.receipt.status == 1

        assert await contract.get_raw_balance_of(owner) == initial_owner - raw_amount
        assert (
            await contract.get_raw_balance_of(recipient)
            == initial_recipient + raw_amount
        )
        assert await contract.get_raw_allowance(owner, spender) == 0


class TestUniswapV2EndToEnd:
    """The Uniswap V2 strategy against the deployed factory/router/pair."""

    pytestmark = pytest.mark.asyncio(loop_scope="session")

    async def test_pool_exists_and_reserves_match_the_seeded_liquidity(
        self, uniswap_v2, test_token, test_token2, deployed_contracts
    ):
        """The seeded 1000/1000 pair is discovered through the factory."""
        assert await uniswap_v2.pool_exists(test_token, test_token2)

        raw_reserves = await uniswap_v2.get_raw_reserves(test_token, test_token2)
        assert raw_reserves == (SEEDED_RESERVE_RAW, SEEDED_RESERVE_RAW)

        reserves = await uniswap_v2.get_reserves(test_token, test_token2)
        assert reserves == (Decimal("1000"), Decimal("1000"))

        # Reserves are oriented on the requested asset order
        assert await uniswap_v2.get_raw_reserves(test_token2, test_token) == (
            SEEDED_RESERVE_RAW,
            SEEDED_RESERVE_RAW,
        )
        assert uniswap_v2.factory_contract.is_initialized
        assert uniswap_v2.factory_address == deployed_contracts["TestUniswapV2Factory"]
        assert uniswap_v2.router_address == deployed_contracts["SimpleV2Router"]

    async def test_quote_exact_input_matches_formula_and_router(
        self, uniswap_v2, router_contract, test_token, test_token2
    ):
        """A 10 token EXACT_INPUT quote matches the constant product exactly."""
        expected_out_raw = constant_product_amount_out(
            SWAP_INPUT_RAW, SEEDED_RESERVE_RAW, SEEDED_RESERVE_RAW
        )
        assert expected_out_raw == SWAP_OUTPUT_RAW

        route = await uniswap_v2.quote_swap(
            input_asset=test_token,
            output_asset=test_token2,
            amount=Decimal("10"),
            mode=SwapMode.EXACT_INPUT,
        )

        assert route.protocol == "uniswap_v2"
        assert route.mode == SwapMode.EXACT_INPUT
        assert route.input_asset is test_token
        assert route.output_asset is test_token2
        assert route.input_amount == Decimal("10")
        assert route.output_amount == raw_to_decimal(SWAP_OUTPUT_RAW)
        assert route.taxes == Decimal("0.003")
        assert route.max_slippage == Decimal("0.005")
        assert len(route.sequence) == 1
        assert route.sequence[0].output_amount == route.output_amount

        # The on-chain router agrees with the library's math
        amounts = await router_contract.functions.getAmountsOut(
            SWAP_INPUT_RAW, [test_token.address.raw, test_token2.address.raw]
        ).call()
        assert amounts == [SWAP_INPUT_RAW, SWAP_OUTPUT_RAW]

    async def test_quote_exact_output_matches_formula_and_router(
        self, uniswap_v2, router_contract, test_token, test_token2
    ):
        """A 10 token EXACT_OUTPUT quote matches the constant product exactly."""
        expected_in_raw = constant_product_amount_in(
            SWAP_INPUT_RAW, SEEDED_RESERVE_RAW, SEEDED_RESERVE_RAW
        )
        assert expected_in_raw == EXACT_OUTPUT_INPUT_RAW

        route = await uniswap_v2.quote_swap(
            input_asset=test_token,
            output_asset=test_token2,
            amount=Decimal("10"),
            mode=SwapMode.EXACT_OUTPUT,
        )
        assert route.output_amount == Decimal("10")
        assert route.input_amount == raw_to_decimal(EXACT_OUTPUT_INPUT_RAW)

        amounts = await router_contract.functions.getAmountsIn(
            SWAP_INPUT_RAW, [test_token.address.raw, test_token2.address.raw]
        ).call()
        assert amounts == [EXACT_OUTPUT_INPUT_RAW, SWAP_INPUT_RAW]

    async def test_build_swap_transaction_is_unsigned(
        self,
        blockchain_snapshot,
        uniswap_v2,
        wallet,
        test_token,
        test_token2,
        deployed_contracts,
    ):
        """build_swap_transaction only builds: no signature, no broadcast."""
        # The router call is gas-estimated against the live node, so the
        # allowance it will spend must already be in place.
        approval = await test_token.contract.place_approve(
            wallet=wallet,
            spender=EthereumAddress.from_string(deployed_contracts["SimpleV2Router"]),
            amount=Decimal("10"),
            client_operation_id="hardhat-v2-build-only-approve",
        )
        await confirm_transaction(wallet, approval)

        route = await uniswap_v2.quote_swap(
            input_asset=test_token,
            output_asset=test_token2,
            amount=Decimal("10"),
            mode=SwapMode.EXACT_INPUT,
        )
        nonce_before = wallet.last_nonce

        transaction = await uniswap_v2.build_swap_transaction(
            route, client_operation_id="hardhat-v2-build-only"
        )

        assert transaction.signed_transaction is None
        assert transaction.operator_operation_id is None
        assert transaction.current_state == BlockchainTransactionState.PENDING_BROADCAST
        assert wallet.last_nonce == nonce_before

        params = unsigned_tx_params(transaction)
        assert set(transaction.other_data) == {UNSIGNED_TX_DATA_KEY}
        assert params["from"] == wallet.address.raw
        assert params["to"] == uniswap_v2.router_address
        assert params["chainId"] == HARDHAT_CHAIN_ID

        # Nothing reached the chain: reserves are still the seeded ones
        assert await uniswap_v2.get_raw_reserves(test_token, test_token2) == (
            SEEDED_RESERVE_RAW,
            SEEDED_RESERVE_RAW,
        )

    async def test_create_swap_transaction_executes_on_chain(
        self,
        blockchain_snapshot,
        uniswap_v2,
        wallet,
        test_token,
        test_token2,
        deployed_contracts,
    ):
        """A quoted swap is signed, broadcast and settled with exact amounts."""
        token_contract = test_token.contract
        token2_contract = test_token2.contract
        router_address = EthereumAddress.from_string(
            deployed_contracts["SimpleV2Router"]
        )

        approval = await token_contract.place_approve(
            wallet=wallet,
            spender=router_address,
            amount=Decimal("10"),
            client_operation_id="hardhat-v2-swap-approve",
        )
        await confirm_transaction(wallet, approval)
        assert (
            await token_contract.get_raw_allowance(wallet.address, router_address)
            == SWAP_INPUT_RAW
        )

        initial_input = await token_contract.get_raw_balance_of(wallet.address)
        initial_output = await token2_contract.get_raw_balance_of(wallet.address)

        route = await uniswap_v2.quote_swap(
            input_asset=test_token,
            output_asset=test_token2,
            amount=Decimal("10"),
            mode=SwapMode.EXACT_INPUT,
        )
        transaction = await uniswap_v2.create_swap_transaction(
            route=route,
            client_operation_id="hardhat-v2-swap",
        )
        update = await confirm_transaction(wallet, transaction)
        assert update.receipt.status == 1
        assert transaction.signed_transaction is not None

        assert (
            await token_contract.get_raw_balance_of(wallet.address)
            == initial_input - SWAP_INPUT_RAW
        )
        assert (
            await token2_contract.get_raw_balance_of(wallet.address)
            == initial_output + SWAP_OUTPUT_RAW
        )
        # The allowance was fully consumed by the router
        assert (
            await token_contract.get_raw_allowance(wallet.address, router_address) == 0
        )

        assert await uniswap_v2.get_raw_reserves(test_token, test_token2) == (
            SEEDED_RESERVE_RAW + SWAP_INPUT_RAW,
            SEEDED_RESERVE_RAW - SWAP_OUTPUT_RAW,
        )
        assert await uniswap_v2.get_reserves(test_token, test_token2) == (
            Decimal("1010"),
            raw_to_decimal(SEEDED_RESERVE_RAW - SWAP_OUTPUT_RAW),
        )

    async def test_execute_swap_quotes_and_sends(
        self,
        blockchain_snapshot,
        uniswap_v2,
        wallet,
        test_token,
        test_token2,
        deployed_contracts,
    ):
        """execute_swap quotes and broadcasts in a single call."""
        token_contract = test_token.contract
        token2_contract = test_token2.contract
        router_address = EthereumAddress.from_string(
            deployed_contracts["SimpleV2Router"]
        )

        approval = await token_contract.place_approve(
            wallet=wallet,
            spender=router_address,
            amount=Decimal("10"),
            client_operation_id="hardhat-v2-execute-approve",
        )
        await confirm_transaction(wallet, approval)

        initial_output = await token2_contract.get_raw_balance_of(wallet.address)

        transaction = await uniswap_v2.execute_swap(
            input_asset=test_token,
            output_asset=test_token2,
            amount=Decimal("10"),
            mode=SwapMode.EXACT_INPUT,
            client_operation_id="hardhat-v2-execute-swap",
        )
        update = await confirm_transaction(wallet, transaction)
        assert update.receipt.status == 1

        assert (
            await token2_contract.get_raw_balance_of(wallet.address)
            == initial_output + SWAP_OUTPUT_RAW
        )


class TestUniswapDEXFacadeOnHardhat:
    """The UniswapDEX facade wired to the local hardhat deployment."""

    pytestmark = pytest.mark.asyncio(loop_scope="session")

    async def test_facade_configuration(
        self, uniswap_dex, blockchain, deployed_contracts
    ):
        """local_network wires exactly the V2 strategy for this platform."""
        assert uniswap_dex.supported_protocols == ["uniswap_v2"]
        assert uniswap_dex.blockchain is blockchain
        assert uniswap_dex.configuration.platform == blockchain.platform
        assert uniswap_dex.configuration.default_slippage == Decimal("0.005")
        assert uniswap_dex.configuration.default_deadline_minutes == 20

        protocol = uniswap_dex.configuration.protocols[0]
        assert protocol.factory_address == deployed_contracts["TestUniswapV2Factory"]
        assert protocol.router_address == deployed_contracts["SimpleV2Router"]
        assert protocol.fee_tiers == [Decimal("0.003")]

    async def test_facade_quote_and_reserves(
        self, uniswap_dex, router_contract, test_token, test_token2
    ):
        """Quoting through the facade returns the exact V2 route."""
        route = await uniswap_dex.quote_swap(
            input_asset=test_token,
            output_asset=test_token2,
            amount=Decimal("5"),
        )

        assert route.protocol == "uniswap_v2"
        assert route.input_amount == Decimal("5")
        assert route.output_amount == raw_to_decimal(FACADE_OUTPUT_RAW)
        assert route.max_slippage == Decimal("0.005")

        amounts = await router_contract.functions.getAmountsOut(
            FACADE_INPUT_RAW, [test_token.address.raw, test_token2.address.raw]
        ).call()
        assert amounts == [FACADE_INPUT_RAW, FACADE_OUTPUT_RAW]

        assert await uniswap_dex.get_reserves(test_token, test_token2) == (
            Decimal("1000"),
            Decimal("1000"),
        )

    async def test_facade_execute_swap_end_to_end(
        self,
        blockchain_snapshot,
        uniswap_dex,
        wallet,
        test_token,
        test_token2,
        deployed_contracts,
    ):
        """The facade dispatches to V2, and the built swap settles on-chain."""
        token_contract = test_token.contract
        token2_contract = test_token2.contract
        router_address = EthereumAddress.from_string(
            deployed_contracts["SimpleV2Router"]
        )

        approval = await token_contract.place_approve(
            wallet=wallet,
            spender=router_address,
            amount=Decimal("5"),
            client_operation_id="hardhat-facade-approve",
        )
        await confirm_transaction(wallet, approval)

        initial_input = await token_contract.get_raw_balance_of(wallet.address)
        initial_output = await token2_contract.get_raw_balance_of(wallet.address)

        route = await uniswap_dex.quote_swap(
            input_asset=test_token,
            output_asset=test_token2,
            amount=Decimal("5"),
            protocol="uniswap_v2",
        )
        unsigned = await uniswap_dex.execute_swap(route)

        assert unsigned.signed_transaction is None
        params = unsigned_tx_params(unsigned)
        assert params["to"] == deployed_contracts["SimpleV2Router"]
        assert params["from"] == wallet.address.raw

        transaction = wallet.sign_and_send_transaction(
            client_operation_id=unsigned.client_operation_id,
            tx_data=dict(params),
        )
        update = await confirm_transaction(wallet, transaction)
        assert update.receipt.status == 1

        assert (
            await token_contract.get_raw_balance_of(wallet.address)
            == initial_input - FACADE_INPUT_RAW
        )
        assert (
            await token2_contract.get_raw_balance_of(wallet.address)
            == initial_output + FACADE_OUTPUT_RAW
        )
        assert await uniswap_dex.get_reserves(test_token, test_token2) == (
            Decimal("1005"),
            raw_to_decimal(SEEDED_RESERVE_RAW - FACADE_OUTPUT_RAW),
        )
