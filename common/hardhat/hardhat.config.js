require("@nomicfoundation/hardhat-toolbox");
require("hardhat-gas-reporter");
require("solidity-coverage");
// require("dotenv").config(); // Load environment variables from .env file

// Chain configurations with RPC URL patterns and chain IDs
const CHAIN_CONFIGS = {
  ethereum: {
    chainId: 1,
    infuraPattern: (key) => `https://mainnet.infura.io/v3/${key}`,
    alchemyPattern: (key) => `https://eth-mainnet.g.alchemy.com/v2/${key}`,
    customEnvVar: "ETHEREUM_FORK_URL",
  },
  arbitrum: {
    chainId: 42161,
    infuraPattern: (key) => `https://arbitrum-mainnet.infura.io/v3/${key}`,
    alchemyPattern: (key) => `https://arb-mainnet.g.alchemy.com/v2/${key}`,
    customEnvVar: "ARBITRUM_FORK_URL",
  },
  polygon: {
    chainId: 137,
    infuraPattern: (key) => `https://polygon-mainnet.infura.io/v3/${key}`,
    alchemyPattern: (key) => `https://polygon-mainnet.g.alchemy.com/v2/${key}`,
    customEnvVar: "POLYGON_FORK_URL",
  },
  base: {
    chainId: 8453,
    infuraPattern: (key) => `https://base-mainnet.infura.io/v3/${key}`,
    alchemyPattern: (key) => `https://base-mainnet.g.alchemy.com/v2/${key}`,
    customEnvVar: "BASE_FORK_URL",
  },
  optimism: {
    chainId: 10,
    infuraPattern: (key) => `https://optimism-mainnet.infura.io/v3/${key}`,
    alchemyPattern: (key) => `https://opt-mainnet.g.alchemy.com/v2/${key}`,
    customEnvVar: "OPTIMISM_FORK_URL",
  },
  bsc: {
    chainId: 56,
    infuraPattern: null, // Infura doesn't support BSC
    alchemyPattern: null, // Alchemy doesn't support BSC
    customEnvVar: "BSC_FORK_URL",
  },
  avalanche: {
    chainId: 43114,
    infuraPattern: (key) => `https://avalanche-mainnet.infura.io/v3/${key}`,
    alchemyPattern: null, // Alchemy doesn't support Avalanche
    customEnvVar: "AVALANCHE_FORK_URL",
  },
};

// Get the selected chain (default: ethereum)
const getSelectedChain = () => {
  return process.env.FORK_CHAIN?.toLowerCase() || "ethereum";
};

// Get chain configuration
const getChainConfig = () => {
  const chainName = getSelectedChain();
  const config = CHAIN_CONFIGS[chainName];

  if (!config) {
    console.warn(`⚠️  Unknown chain "${chainName}", falling back to ethereum`);
    return CHAIN_CONFIGS.ethereum;
  }

  return config;
};

// Configure forking RPC URL with fallback priority: Infura > Alchemy > Custom
const getForkUrl = () => {
  const chainConfig = getChainConfig();

  // Try Infura first
  if (process.env.INFURA_API_KEY && chainConfig.infuraPattern) {
    return chainConfig.infuraPattern(process.env.INFURA_API_KEY);
  }

  // Try Alchemy second
  if (process.env.ALCHEMY_API_KEY && chainConfig.alchemyPattern) {
    return chainConfig.alchemyPattern(process.env.ALCHEMY_API_KEY);
  }

  // Try custom URL for specific chain
  if (process.env[chainConfig.customEnvVar]) {
    return process.env[chainConfig.customEnvVar];
  }

  // Fallback to generic custom URL
  return process.env.FORK_URL || "";
};

// Configure mining settings for forked network
const getForkMiningConfig = () => {
  const autoMining = process.env.FORK_AUTO_MINING !== "false";
  return autoMining
    ? { auto: true, interval: 0 }
    : { auto: false, interval: [12000, 14000] };
};

// Configure forking settings
const getForkingConfig = () => {
  const forkUrl = getForkUrl();
  if (!forkUrl) {
    return undefined;
  }

  const config = {
    url: forkUrl,
    enabled: process.env.FORK_ENABLED !== "false",
  };

  // Add block number if specified
  if (process.env.FORK_BLOCK_NUMBER) {
    config.blockNumber = parseInt(process.env.FORK_BLOCK_NUMBER);
  }

  return config;
};

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: {
    version: "0.8.20",
    settings: {
      optimizer: {
        enabled: true,
        runs: 200,
      },
    },
  },
  networks: {
    hardhat: {
      chainId: getForkingConfig() ? getChainConfig().chainId : 31337, // Use chain's chainId when forking
      accounts: {
        count: 20,
        accountsBalance: "10000000000000000000000", // 10000 ETH
      },
      mining: getForkingConfig() ? getForkMiningConfig() : { auto: true, interval: 0 },
      forking: getForkingConfig(),
    },
    localhost: {
      url: "http://127.0.0.1:8545",
      chainId: 31337,
    },
    // Add more networks as needed
    sepolia: {
      url: process.env.SEPOLIA_URL || "",
      accounts: process.env.PRIVATE_KEY !== undefined ? [process.env.PRIVATE_KEY] : [],
    },
  },
  paths: {
    sources: "./contracts",
    tests: "./test",
    cache: "./cache",
    artifacts: "./artifacts"
  },
  gasReporter: {
    enabled: process.env.REPORT_GAS !== undefined,
    currency: "USD",
  },
  mocha: {
    timeout: 40000,
  },
};
