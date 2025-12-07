#!/bin/bash
#
# Fork Hardhat Node Script
# 
# This script starts a Hardhat node with blockchain forking enabled.
# You can configure it using:
#   1. A .env file (create from env.template) - RECOMMENDED
#   2. Environment variables
#   3. Command line argument for chain selection
#
# Usage: ./fork.sh [chain]
#   Examples:
#     ./fork.sh              # Fork Ethereum (default)
#     ./fork.sh arbitrum     # Fork Arbitrum
#     ./fork.sh polygon      # Fork Polygon

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# Get the hardhat directory (parent of scripts)
HARDHAT_DIR="$(dirname "$SCRIPT_DIR")"

# Change to the hardhat directory
cd "$HARDHAT_DIR" || exit 1

# Check if node_modules exists, if not install dependencies
if [ ! -d "node_modules" ]; then
    echo "📦 Installing Hardhat dependencies (first time setup)..."
    npm install
    if [ $? -ne 0 ]; then
        echo "❌ Failed to install dependencies. Please run 'npm install' manually in $HARDHAT_DIR"
        exit 1
    fi
    echo "✅ Dependencies installed successfully!"
fi

# Set chain to fork (default: ethereum)
# Options: ethereum, arbitrum, polygon, base, optimism, bsc, avalanche
export FORK_CHAIN="${1:-ethereum}"

# Verify API key or custom URL is set
if [ -z "$INFURA_API_KEY" ] && [ -z "$ALCHEMY_API_KEY" ] && [ -z "$FORK_URL" ]; then
    echo "❌ Error: No RPC provider configured"
    echo ""
    echo "Please configure an RPC provider using one of these methods:"
    echo ""
    echo "1. Create a .env file (RECOMMENDED):"
    echo "   cp env.template .env"
    echo "   # Then edit .env and add your INFURA_API_KEY or ALCHEMY_API_KEY"
    echo ""
    echo "2. Export environment variable:"
    echo "   export INFURA_API_KEY=\"your_key\""
    echo ""
    echo "3. Pass inline:"
    echo "   INFURA_API_KEY=\"your_key\" ./scripts/fork.sh"
    echo ""
    exit 1
fi

# Display chain info
case "$FORK_CHAIN" in
    ethereum)
        CHAIN_NAME="Ethereum Mainnet"
        CHAIN_ID="1"
        ;;
    arbitrum)
        CHAIN_NAME="Arbitrum One"
        CHAIN_ID="42161"
        ;;
    polygon)
        CHAIN_NAME="Polygon"
        CHAIN_ID="137"
        ;;
    base)
        CHAIN_NAME="Base"
        CHAIN_ID="8453"
        ;;
    optimism)
        CHAIN_NAME="Optimism"
        CHAIN_ID="10"
        ;;
    bsc)
        CHAIN_NAME="BNB Smart Chain"
        CHAIN_ID="56"
        ;;
    avalanche)
        CHAIN_NAME="Avalanche C-Chain"
        CHAIN_ID="43114"
        ;;
    *)
        CHAIN_NAME="$FORK_CHAIN"
        CHAIN_ID="unknown"
        ;;
esac

# Start forked node
echo "🚀 Starting Hardhat node with $CHAIN_NAME fork..."
echo "   Chain: $CHAIN_NAME"
echo "   Chain ID: $CHAIN_ID"
[ ! -z "$INFURA_API_KEY" ] && echo "   RPC Provider: Infura"
[ ! -z "$ALCHEMY_API_KEY" ] && [ -z "$INFURA_API_KEY" ] && echo "   RPC Provider: Alchemy"
npx hardhat node