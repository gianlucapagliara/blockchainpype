# Multi-Chain Forking Guide

This guide explains how to use the multi-chain forking feature in this Hardhat setup.

## Overview

The Hardhat configuration now supports forking from multiple blockchain networks, allowing you to test your smart contracts against real on-chain state from different networks.

## Supported Chains

| Chain | Chain ID | Infura Support | Alchemy Support | Environment Variable |
|-------|----------|----------------|-----------------|---------------------|
| Ethereum | 1 | ✅ | ✅ | `ETHEREUM_FORK_URL` |
| Arbitrum | 42161 | ✅ | ✅ | `ARBITRUM_FORK_URL` |
| Polygon | 137 | ✅ | ✅ | `POLYGON_FORK_URL` |
| Base | 8453 | ✅ | ✅ | `BASE_FORK_URL` |
| Optimism | 10 | ✅ | ✅ | `OPTIMISM_FORK_URL` |
| BSC | 56 | ❌ | ❌ | `BSC_FORK_URL` (custom RPC required) |
| Avalanche | 43114 | ✅ | ❌ | `AVALANCHE_FORK_URL` |

## Setup

### 1. Create Your `.env` File

```bash
cd common/hardhat
cp env.template .env
```

Edit `.env` and add your API key:
```bash
INFURA_API_KEY=your_actual_key_here
# or
ALCHEMY_API_KEY=your_actual_key_here
```

The `.env` file is automatically loaded and is already in `.gitignore`.

### 2. Install Dependencies (if not done)

```bash
npm install
```

## Quick Start

### Method 1: Using Helper Scripts (Recommended)

```bash
# Fork Ethereum (default)
./scripts/fork.sh

# Fork specific chains
./scripts/fork.sh arbitrum
./scripts/fork.sh polygon
./scripts/fork.sh base
./scripts/fork.sh optimism
```

### Method 2: Using `.env` File

Create a `.env` file in `common/hardhat/`:
```bash
# .env
INFURA_API_KEY=your_api_key
FORK_CHAIN=arbitrum
```

Then simply:
```bash
cd common/hardhat
npx hardhat node
```

### Method 3: Manual Environment Variables

```bash
cd common/hardhat

# Set API key
export INFURA_API_KEY="your_api_key"

# Select chain
export FORK_CHAIN="arbitrum"

# Start node
npx hardhat node
```

## Configuration Priority

The system uses the following priority for RPC URLs:

1. **Infura** - If `INFURA_API_KEY` is set and chain is supported
2. **Alchemy** - If `ALCHEMY_API_KEY` is set and chain is supported
3. **Chain-Specific URL** - e.g., `ARBITRUM_FORK_URL`
4. **Generic URL** - `FORK_URL` as fallback

## Environment Variables

### Required (at least one)

- `INFURA_API_KEY` - Your Infura project ID
- `ALCHEMY_API_KEY` - Your Alchemy API key
- `[CHAIN]_FORK_URL` - Chain-specific custom RPC URL
- `FORK_URL` - Generic RPC URL

### Optional

- `FORK_CHAIN` - Chain to fork (default: `ethereum`)
- `FORK_ENABLED` - Enable/disable forking (default: `true` if URL available)
- `FORK_BLOCK_NUMBER` - Pin to specific block (default: latest)
- `FORK_AUTO_MINING` - Auto-mine blocks (default: `true`)

## Examples

### Fork Ethereum at Specific Block

```bash
export INFURA_API_KEY="your_key"
export FORK_CHAIN="ethereum"
export FORK_BLOCK_NUMBER="18000000"
npx hardhat node
```

### Fork Arbitrum with Interval Mining

```bash
export INFURA_API_KEY="your_key"
export FORK_CHAIN="arbitrum"
export FORK_AUTO_MINING="false"  # 12-14 second blocks
npx hardhat node
```

### Fork BSC with Custom RPC

```bash
export BSC_FORK_URL="https://bsc-dataseed.binance.org"
export FORK_CHAIN="bsc"
npx hardhat node
```

### Fork Multiple Chains Simultaneously

```bash
# Terminal 1: Fork Ethereum on default port
export FORK_CHAIN="ethereum"
export INFURA_API_KEY="your_key"
npx hardhat node

# Terminal 2: Fork Arbitrum on different port
export FORK_CHAIN="arbitrum"
export INFURA_API_KEY="your_key"
npx hardhat node --port 8546
```

## Testing Against Forked Networks

Once you have a forked network running, you can deploy contracts and run tests:

```bash
# Terminal 1: Start forked network
./scripts/fork.sh arbitrum

# Terminal 2: Deploy contracts
npx hardhat run scripts/deploy-all.js --network localhost

# Terminal 3: Run tests
npx hardhat test --network localhost
```

## Python Integration

```python
from blockchainpype.factory import BlockchainFactory

# Connect to the forked network (running on localhost:8545)
blockchain = BlockchainFactory.get_by_identifier("hardhat")

# The blockchain will have the chain ID of the forked network
# You can now interact with real contracts from that network
```

## Troubleshooting

### Chain not supported by provider

If you see errors about unsupported chains:
- BSC is not supported by Infura/Alchemy free tiers - use custom RPC
- Avalanche is not supported by Alchemy - use Infura or custom RPC

### Rate limiting

If you hit rate limits:
- Use `FORK_BLOCK_NUMBER` to pin to a specific block (reduces requests)
- Upgrade your RPC provider plan
- Use multiple API keys with load balancing

### Memory issues

For large forks:
```bash
export NODE_OPTIONS="--max-old-space-size=4096"
npx hardhat node
```

## Adding New Chains

To add support for a new chain, edit `hardhat.config.js`:

```javascript
const CHAIN_CONFIGS = {
  // ... existing chains ...
  newchain: {
    chainId: 12345,
    infuraPattern: (key) => `https://newchain.infura.io/v3/${key}`,
    alchemyPattern: (key) => `https://newchain.g.alchemy.com/v2/${key}`,
    customEnvVar: "NEWCHAIN_FORK_URL",
  },
};
```

## Best Practices

1. **Pin Block Numbers** for reproducible tests
2. **Use Auto-Mining** for faster development
3. **Use Interval Mining** to simulate real block times
4. **Cache Fork Data** to reduce RPC requests
5. **Monitor API Usage** to avoid rate limits

## Resources

- [Hardhat Forking Documentation](https://hardhat.org/hardhat-network/docs/guides/forking-other-networks)
- [Infura Documentation](https://docs.infura.io/)
- [Alchemy Documentation](https://docs.alchemy.com/)

