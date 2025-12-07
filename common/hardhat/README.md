# Hardhat Testing Setup

This directory contains the Hardhat testing environment for blockchain development.

> 🚀 **New User?** Start with the [Quick Setup Guide](SETUP.md) for step-by-step instructions!

## Overview

This setup provides:
- **Local Ethereum blockchain** using Hardhat Network
- **Test contracts** for comprehensive testing scenarios
- **Automated deployment** scripts
- **Integration** with the Python blockchain library

## Quick Start

### 1. Install Dependencies

```bash
cd common/hardhat
npm install
```

### 2. Compile Contracts

```bash
npx hardhat compile
```

### 3. Run Hardhat Node

**Local Network (No Forking):**
```bash
npx hardhat node
```

**Forked Network (Mainnet Fork):**
```bash
# Set your API key first
export INFURA_API_KEY="your_infura_project_id"

# Fork Ethereum (default)
./scripts/fork.sh

# Or fork other chains
./scripts/fork.sh arbitrum
./scripts/fork.sh polygon
./scripts/fork.sh base
./scripts/fork.sh optimism
```

### 4. Deploy Contracts (in another terminal)

```bash
npx hardhat run scripts/deploy-all.js --network localhost
```

## Chain Fork Quick Reference

| Command | Chain | Chain ID | Description |
|---------|-------|----------|-------------|
| `./scripts/fork.sh` or `./scripts/fork.sh ethereum` | Ethereum | 1 | Ethereum Mainnet |
| `./scripts/fork.sh arbitrum` | Arbitrum | 42161 | Arbitrum One |
| `./scripts/fork.sh polygon` | Polygon | 137 | Polygon PoS |
| `./scripts/fork.sh base` | Base | 8453 | Base (Coinbase L2) |
| `./scripts/fork.sh optimism` | Optimism | 10 | Optimism Mainnet |
| `./scripts/fork.sh bsc` | BSC | 56 | BNB Smart Chain |
| `./scripts/fork.sh avalanche` | Avalanche | 43114 | Avalanche C-Chain |

## Available Scripts

- `npm run node` - Start Hardhat node
- `npm run compile` - Compile contracts
- `npm run test` - Run Hardhat tests
- `npm run deploy` - Deploy contracts to localhost
- `npm run clean` - Clean compilation artifacts

## Test Contracts

### TestToken.sol
- **Type**: ERC20 Token
- **Supply**: 1,000,000 tokens
- **Features**: Minting, standard ERC20 operations

### TestUniswapV2.sol
- **Type**: DEX Factory and Pair
- **Features**: Pair creation, liquidity operations, swapping
- **Usage**: DeFi protocol testing

### TestMultisig.sol
- **Type**: Multi-signature wallet
- **Features**: Multi-owner transactions, confirmations
- **Configuration**: 3 owners, 2 confirmations required

## Network Configuration

### Hardhat Network (Test)
- **Chain ID**: 31337
- **Accounts**: 20 accounts with 10,000 ETH each
- **Mining**: Auto-mining enabled
- **URL**: Internal (for `hardhat test`)

### Localhost Network
- **Chain ID**: 31337
- **URL**: http://127.0.0.1:8545
- **Accounts**: Same as Hardhat Network
- **Usage**: For external connections

### Mainnet Fork Network
- **Chain ID**: 1 (Ethereum Mainnet)
- **Accounts**: 20 accounts with 10,000 ETH each
- **Mining**: Configurable (auto or interval)
- **Usage**: Testing with real mainnet contracts and state

## Multi-Chain Forking

Hardhat forking allows you to test your code against real blockchain contracts and state locally. This configuration supports forking from multiple chains.

### Setting Up Environment Variables

**Option 1: Using a `.env` file (Recommended)**

1. Copy the template file:
```bash
cd common/hardhat
cp env.template .env
```

2. Edit `.env` and add your API keys:
```bash
# .env file
INFURA_API_KEY=your_actual_infura_key_here
FORK_CHAIN=ethereum
```

3. The `.env` file is automatically loaded by Hardhat (it's in `.gitignore` so it won't be committed)

**Option 2: Export environment variables manually**

```bash
export INFURA_API_KEY="your_key"
export FORK_CHAIN="arbitrum"
```

**Option 3: Inline with command**

```bash
INFURA_API_KEY="your_key" FORK_CHAIN="polygon" npx hardhat node
```

**Supported Chains:**
- 🔷 **Ethereum** (Chain ID: 1)
- 🔵 **Arbitrum** (Chain ID: 42161)
- 🟣 **Polygon** (Chain ID: 137)
- 🔵 **Base** (Chain ID: 8453)
- 🔴 **Optimism** (Chain ID: 10)
- 🟡 **BNB Smart Chain / BSC** (Chain ID: 56)
- 🔺 **Avalanche C-Chain** (Chain ID: 43114)

**Use Cases:**
- Testing interactions with existing DeFi protocols (Uniswap, Aave, etc.)
- Debugging against real contract deployments
- Simulating transactions before executing on mainnet
- Reproducing production bugs in a local environment
- Cross-chain testing and development

### Environment Variables Reference

These variables can be set in your `.env` file, exported in your shell, or passed inline with commands:

| Variable | Description | Default | Priority |
|----------|-------------|---------|----------|
| `FORK_CHAIN` | Chain to fork (ethereum, arbitrum, polygon, base, optimism, bsc, avalanche) | `ethereum` | - |
| `INFURA_API_KEY` | Infura project ID for RPC access | - | 1 (Highest) |
| `ALCHEMY_API_KEY` | Alchemy API key for RPC access | - | 2 |
| `[CHAIN]_FORK_URL` | Chain-specific custom RPC URL (e.g., `ARBITRUM_FORK_URL`) | - | 3 |
| `FORK_URL` | Generic custom RPC endpoint URL | - | 4 (Lowest) |
| `FORK_ENABLED` | Enable/disable forking | `true` | - |
| `FORK_BLOCK_NUMBER` | Pin fork to specific block number | Latest | - |
| `FORK_AUTO_MINING` | Enable auto-mining (false = interval mining) | `true` | - |

**RPC Provider Priority**: The configuration tries Infura first, then Alchemy, then chain-specific custom URL, then generic custom URL.

**Chain-Specific URLs**: You can set custom RPC URLs for each chain:
- `ETHEREUM_FORK_URL`
- `ARBITRUM_FORK_URL`
- `POLYGON_FORK_URL`
- `BASE_FORK_URL`
- `OPTIMISM_FORK_URL`
- `BSC_FORK_URL`
- `AVALANCHE_FORK_URL`

### Usage Examples

#### Fork Ethereum Mainnet (Default)

```bash
# Using the helper script (easiest)
./common/hardhat/scripts/fork.sh

# Or manually with Infura
cd common/hardhat
export INFURA_API_KEY="your_infura_project_id"
export FORK_CHAIN="ethereum"
npx hardhat node
```

#### Fork Arbitrum

```bash
# Using the helper script
./common/hardhat/scripts/fork.sh arbitrum

# Or manually
cd common/hardhat
export INFURA_API_KEY="your_infura_project_id"
export FORK_CHAIN="arbitrum"
npx hardhat node
```

#### Fork Polygon

```bash
# Using the helper script
./common/hardhat/scripts/fork.sh polygon

# Or manually with Alchemy
cd common/hardhat
export ALCHEMY_API_KEY="your_alchemy_api_key"
export FORK_CHAIN="polygon"
npx hardhat node
```

#### Fork Base

```bash
./common/hardhat/scripts/fork.sh base
```

#### Fork Optimism

```bash
./common/hardhat/scripts/fork.sh optimism
```

#### Fork BSC (Requires Custom RPC)

```bash
# BSC requires custom RPC (not supported by Infura/Alchemy on all plans)
export BSC_FORK_URL="https://bsc-dataseed.binance.org"
export FORK_CHAIN="bsc"
npx hardhat node
```

#### Fork with Custom RPC

```bash
# Chain-specific custom URL
export ARBITRUM_FORK_URL="https://your-arbitrum-rpc-endpoint.com"
export FORK_CHAIN="arbitrum"
npx hardhat node

# Generic custom URL
export FORK_URL="https://your-custom-rpc-endpoint.com"
export FORK_CHAIN="polygon"
npx hardhat node
```

#### Pin to Specific Block Number

```bash
# Fork from a specific block (useful for reproducible tests)
export INFURA_API_KEY="your_infura_project_id"
export FORK_BLOCK_NUMBER="18000000"

npx hardhat node --network mainnet-fork
```

#### Use Interval Mining (Simulate Real Block Times)

```bash
# Disable auto-mining to simulate real Ethereum block times (12-14s)
export INFURA_API_KEY="your_infura_project_id"
export FORK_AUTO_MINING="false"

npx hardhat node --network mainnet-fork
```

#### Run Tests Against Forked Network

```bash
# Terminal 1: Start forked node
export INFURA_API_KEY="your_infura_project_id"
npx hardhat node --network mainnet-fork

# Terminal 2: Run tests
npx hardhat test --network localhost
```

#### Deploy Contracts to Fork

```bash
# Terminal 1: Start forked node
export INFURA_API_KEY="your_infura_project_id"
npx hardhat node --network mainnet-fork

# Terminal 2: Deploy contracts
npx hardhat run scripts/deploy-all.js --network localhost
```

### Python Integration with Forked Network

```python
from blockchainpype.initializer import BlockchainsInitializer
from blockchainpype.factory import BlockchainFactory

# Initialize blockchain configuration
BlockchainsInitializer.configure()

# Connect to forked network (running on localhost)
blockchain = BlockchainFactory.get_by_identifier("hardhat")

# Now you can interact with real mainnet contracts
# Example: Get USDC balance
usdc_address = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
wallet_address = "0xYourAddress"
# ... your code here
```

### Best Practices

1. **Use Block Pinning for Tests**: Pin to a specific block number for reproducible tests:
   ```bash
   export FORK_BLOCK_NUMBER="18000000"
   ```

2. **Cache Fork Data**: Hardhat caches forked data. To reset:
   ```bash
   npx hardhat clean
   rm -rf cache/
   ```

3. **Monitor RPC Requests**: Free tier RPC providers have rate limits. Consider:
   - Using block pinning to reduce requests
   - Upgrading to paid RPC plans for heavy usage
   - Using local archive nodes for unlimited access

4. **Impersonate Accounts**: You can impersonate any mainnet account to test interactions:
   ```javascript
   // In your tests
   await hre.network.provider.request({
     method: "hardhat_impersonateAccount",
     params: ["0xSomeMainnetAddress"],
   });
   ```

5. **Reset Fork State**: Reset to initial fork state during testing:
   ```bash
   curl -X POST -H "Content-Type: application/json" \
     --data '{"jsonrpc":"2.0","method":"hardhat_reset","params":[{"forking":{"jsonRpcUrl":"YOUR_RPC_URL"}}],"id":1}' \
     http://127.0.0.1:8545
   ```

### Troubleshooting Forking

#### Fork Not Starting

```bash
# Check if RPC URL is accessible
curl -X POST -H "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
  https://mainnet.infura.io/v3/YOUR_API_KEY

# Verify environment variables are set
echo $INFURA_API_KEY
```

#### Slow Forking Performance

- Pin to a specific block number to reduce initial sync time
- Use a faster RPC provider (Alchemy often faster than Infura)
- Consider using a local archive node for best performance

#### Rate Limiting Issues

```bash
# Use block pinning to reduce RPC calls
export FORK_BLOCK_NUMBER="18000000"

# Or upgrade RPC provider plan
# Or use multiple API keys with load balancing
```

#### Memory Issues with Large Forks

```bash
# Increase Node.js memory limit
export NODE_OPTIONS="--max-old-space-size=4096"
npx hardhat node --network mainnet-fork
```

### Common Use Cases

#### Testing Ethereum DeFi Protocols

```bash
# Fork Ethereum mainnet
export INFURA_API_KEY="your_key"
export FORK_CHAIN="ethereum"
npx hardhat node

# Interact with real protocols:
# - Uniswap V2/V3
# - Aave
# - Compound
# - MakerDAO
# Popular addresses:
# - UniswapV2Router02: 0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D
# - UniswapV3Router: 0xE592427A0AEce92De3Edee1F18E0157C05861564
# - USDC: 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48
# - DAI: 0x6B175474E89094C44Da98b954EedeAC495271d0F
```

#### Testing Arbitrum DeFi

```bash
# Fork Arbitrum
./common/hardhat/scripts/fork.sh arbitrum

# Test with Arbitrum-specific protocols:
# - GMX: 0xfc5A1A6EB076a2C7aD06eD22C90d7E710E35ad0a
# - Camelot DEX
# - Radiant Capital
```

#### Testing Polygon DeFi

```bash
# Fork Polygon
export FORK_CHAIN="polygon"
export INFURA_API_KEY="your_key"
npx hardhat node

# Test with Polygon protocols:
# - QuickSwap
# - Aave Polygon
# - Balancer
```

#### Testing Base Protocols

```bash
# Fork Base
./common/hardhat/scripts/fork.sh base

# Test with Base protocols:
# - Uniswap V3 on Base
# - Aerodrome
# - Compound on Base
```

#### Cross-Chain Testing

```bash
# Test the same contract on different chains
# Terminal 1: Fork Ethereum
export FORK_CHAIN="ethereum"
npx hardhat node --port 8545

# Terminal 2: Fork Arbitrum  
export FORK_CHAIN="arbitrum"
npx hardhat node --port 8546

# Now you can test cross-chain behavior
```

## Default Accounts

The following accounts are available for testing (deterministic):

1. `0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266` (Deployer)
2. `0x70997970C51812dc3A010C7d01b50e0d17dc79C8`
3. `0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC`
4. ... (17 more accounts)

## Deployment

### Automatic Deployment

The `deploy-all.js` script deploys all test contracts and creates:
- `deployments.json` - Contract addresses
- Initial token balances
- Multisig funding
- DEX pair creation

### Manual Deployment

```bash
# Deploy specific contract
npx hardhat run scripts/deploy.js --network localhost

# Deploy all contracts
npx hardhat run scripts/deploy-all.js --network localhost
```

## Integration with Python

This Hardhat setup integrates with the Python blockchain library:

```python
from blockchainpype.initializer import BlockchainsInitializer
from blockchainpype.factory import BlockchainFactory

# Initialize blockchain configuration
BlockchainsInitializer.configure()

# Connect to local Hardhat network
blockchain = BlockchainFactory.get_by_identifier("hardhat")
```

## Configuration Files

### hardhat.config.js
Main configuration file with:
- Solidity compiler settings
- Network configurations
- Gas reporting settings
- Test timeout settings

### package.json
NPM package configuration with:
- Dependencies for testing
- Scripts for common operations
- Development tools

## Gas Reporting

Enable gas reporting:

```bash
REPORT_GAS=true npx hardhat test
```

## Testing with Hardhat

### Run JavaScript Tests

```bash
npx hardhat test
```

### Run with Coverage

```bash
npx hardhat coverage
```

### Run Specific Test

```bash
npx hardhat test test/TestToken.js
```

## Troubleshooting

### Port Already in Use

```bash
# Kill process on port 8545
lsof -ti:8545 | xargs kill -9

# Or use different port
npx hardhat node --port 8546
```

### Compilation Errors

```bash
# Clean and recompile
npx hardhat clean
npx hardhat compile
```

### Node Not Responding

```bash
# Check if node is running
curl -X POST -H "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
  http://127.0.0.1:8545
```

### Reset Network State

```bash
# Restart the node to reset state
# Or use hardhat_reset RPC call
curl -X POST -H "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","method":"hardhat_reset","params":[],"id":1}' \
  http://127.0.0.1:8545
```

## Development Workflow

### 1. Start Development

```bash
# Terminal 1: Start Hardhat node
npx hardhat node

# Terminal 2: Deploy contracts
npm run deploy

# Terminal 3: Run Python tests
cd ../..
poetry run pytest tests/test_hardhat_example.py -v
```

### 2. Contract Changes

```bash
# After modifying contracts
npx hardhat compile
npm run deploy
```

### 3. Testing

```bash
# Test JavaScript (Hardhat)
npx hardhat test

# Test Python integration
cd ../..
poetry run pytest tests/test_hardhat_example.py -v
```

## File Structure

```
common/hardhat/
├── contracts/              # Solidity contracts
│   ├── TestToken.sol
│   ├── TestUniswapV2.sol
│   └── TestMultisig.sol
├── scripts/                # Deployment scripts
│   ├── deploy.js
│   └── deploy-all.js
├── test/                   # JavaScript tests
├── artifacts/              # Compiled contracts (generated)
├── cache/                  # Hardhat cache (generated)
├── deployments.json        # Deployed contract addresses (generated)
├── hardhat.config.js       # Hardhat configuration
├── package.json            # NPM configuration
└── README.md              # This file
```

## Best Practices

### 1. Always Compile First

```bash
npx hardhat compile
```

### 2. Check Node Status

```bash
# Before deploying, ensure node is running
curl -s http://127.0.0.1:8545 || echo "Node not running"
```

### 3. Use Snapshots for Testing

```bash
# In tests, use evm_snapshot and evm_revert
curl -X POST -H "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","method":"evm_snapshot","params":[],"id":1}' \
  http://127.0.0.1:8545
```

### 4. Monitor Gas Usage

```bash
REPORT_GAS=true npx hardhat test
```

## Contributing

When adding new contracts:

1. **Add contract** to `contracts/` directory
2. **Update deployment script** in `scripts/deploy-all.js`
3. **Add JavaScript tests** to `test/` directory
4. **Update documentation** in this README
5. **Test integration** with Python library

## Resources

- [Hardhat Documentation](https://hardhat.org/docs)
- [Hardhat Network](https://hardhat.org/hardhat-network/)
- [Hardhat Testing](https://hardhat.org/tutorial/testing-contracts)
- [OpenZeppelin Contracts](https://docs.openzeppelin.com/contracts/)

---

This Hardhat setup provides a complete testing environment for blockchain development with the Python blockchain library.
