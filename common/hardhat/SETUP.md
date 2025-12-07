# Quick Setup Guide

## Initial Setup (One Time)

### 1. Install Dependencies

```bash
cd common/hardhat
npm install
```

This will install Hardhat and all required packages including `dotenv` for automatic `.env` file loading.

### 2. Create Your `.env` File

```bash
cp env.template .env
```

### 3. Add Your API Key

Edit the `.env` file and add your RPC provider API key:

**Option A: Using Infura (Recommended)**

Get a free API key from [infura.io](https://infura.io/) and add it to `.env`:

```bash
# .env
INFURA_API_KEY=your_infura_project_id_here
```

**Option B: Using Alchemy**

Get a free API key from [alchemy.com](https://www.alchemy.com/) and add it to `.env`:

```bash
# .env
ALCHEMY_API_KEY=your_alchemy_api_key_here
```

**Option C: Using Custom RPC**

```bash
# .env
ETHEREUM_FORK_URL=https://your-rpc-endpoint.com
# or for a specific chain
ARBITRUM_FORK_URL=https://your-arbitrum-rpc.com
```

### 4. You're Ready!

The `.env` file is:
- ✅ Automatically loaded by Hardhat
- ✅ Already in `.gitignore` (won't be committed to git)
- ✅ Safe to store API keys locally

## Usage

### Start Local Hardhat Node (No Forking)

```bash
npx hardhat node
```

### Start Forked Node

**Fork Ethereum Mainnet:**
```bash
./scripts/fork.sh
```

**Fork Other Chains:**
```bash
./scripts/fork.sh arbitrum
./scripts/fork.sh polygon
./scripts/fork.sh base
./scripts/fork.sh optimism
```

**Change Default Chain in `.env`:**
```bash
# .env
INFURA_API_KEY=your_key
FORK_CHAIN=arbitrum  # Now ./scripts/fork.sh will fork Arbitrum by default
```

## Configuration Examples

### Example 1: Simple Ethereum Fork

```bash
# .env
INFURA_API_KEY=abc123xyz
```

Then run:
```bash
./scripts/fork.sh
```

### Example 2: Fork Arbitrum at Specific Block

```bash
# .env
INFURA_API_KEY=abc123xyz
FORK_CHAIN=arbitrum
FORK_BLOCK_NUMBER=150000000
```

Then run:
```bash
npx hardhat node
```

### Example 3: Fork with Interval Mining (Realistic Block Times)

```bash
# .env
INFURA_API_KEY=abc123xyz
FORK_CHAIN=polygon
FORK_AUTO_MINING=false  # Enables 12-14 second block intervals
```

Then run:
```bash
npx hardhat node
```

### Example 4: Multiple Chains with Different Providers

```bash
# .env
# Use Infura for Ethereum
INFURA_API_KEY=your_infura_key

# Use custom RPC for BSC (not supported by Infura)
BSC_FORK_URL=https://bsc-dataseed.binance.org
```

Then:
```bash
# Fork Ethereum with Infura
FORK_CHAIN=ethereum npx hardhat node

# Or fork BSC with custom RPC
FORK_CHAIN=bsc npx hardhat node
```

## Environment Variables Priority

When you set the same variable in multiple places, this is the priority order:

1. **Command line** (highest): `FORK_CHAIN=arbitrum npx hardhat node`
2. **Exported in shell**: `export FORK_CHAIN=arbitrum`
3. **`.env` file** (lowest): `FORK_CHAIN=arbitrum` in `.env`

## Troubleshooting

### "No RPC provider configured" Error

You need to set at least one of these:
- `INFURA_API_KEY` in `.env`
- `ALCHEMY_API_KEY` in `.env`
- `FORK_URL` or chain-specific URL in `.env`

### `.env` File Not Loading

Make sure:
1. The file is named exactly `.env` (not `env` or `.env.txt`)
2. The file is in the `common/hardhat/` directory
3. You've run `npm install` to install the `dotenv` package

### API Key Not Working

Check:
1. API key is correct (no extra spaces)
2. API key format in `.env` is: `INFURA_API_KEY=your_key` (no quotes needed)
3. Your RPC provider supports the chain you're trying to fork

## Security Notes

⚠️ **Important:**
- Never commit your `.env` file to git (it's already in `.gitignore`)
- Never share your API keys publicly
- Use different API keys for development and production
- Free tier API keys have rate limits - use block pinning to reduce requests

## Next Steps

- Read [README.md](README.md) for detailed documentation
- Read [MULTI_CHAIN_FORKING.md](MULTI_CHAIN_FORKING.md) for advanced multi-chain usage
- Check [env.template](env.template) for all available configuration options

