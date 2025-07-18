# Hardhat Testing Setup

This directory contains the Hardhat testing environment for blockchain development.

## Overview

This setup provides:
- **Local Ethereum blockchain** using Hardhat Network
- **Test contracts** for comprehensive testing scenarios
- **Automated deployment** scripts
- **Integration** with the Python blockchain library

## Quick Start

### 1. Install Dependencies

```bash
npm install
```

### 2. Compile Contracts

```bash
npx hardhat compile
```

### 3. Run Hardhat Node

```bash
npx hardhat node
```

### 4. Deploy Contracts (in another terminal)

```bash
npx hardhat run scripts/deploy-all.js --network localhost
```

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
