# Blockchain Pypeline

A powerful Python library for interacting with multiple blockchain networks, providing a unified interface for EVM-compatible chains and Solana. This library simplifies blockchain interactions, asset management, and transaction handling with a clean, type-safe API.

## Overview

Blockchain Pypeline is designed to streamline blockchain development by providing:
- A unified interface for multiple blockchain networks
- Type-safe interactions with smart contracts and programs
- Simplified wallet and transaction management
- Comprehensive asset handling across different chains

## Features

- **Multi-Chain Support**
  - EVM-compatible chains (Ethereum, Polygon, BSC, etc.)
  - Solana blockchain
  - Extensible architecture for adding new chains

- **Wallet Management**
  - Secure key management
  - Transaction signing
  - Multiple wallet support

- **Asset Operations**
  - Token transfers and management
  - NFT handling
  - Dapps interactions (Smart contracts, Programs)

- **Transaction Handling**
  - Transaction building and signing
  - Gas estimation and optimization
  - Transaction monitoring
  - Block explorer integration


## Installation

The package requires Python 3.13 or later. You can install it using Poetry:

```bash
poetry add blockchainpype
```

Or with pip:

```bash
pip install blockchainpype
```

## Quick Start

Here's a simple example of how to use blockchainpype:

```python
...
```

## Development

### Setup

1. Clone the repository:
```bash
git clone https://github.com/gianlucapagliara/blockchainpype.git
cd blockchainpype
```

2. Install dependencies with Poetry:
```bash
poetry install
```

3. Set up pre-commit hooks:
```bash
poetry run pre-commit install
```

### Testing

Run the test suite:

```bash
poetry run pytest
```

### Code Quality

The project uses several tools to maintain code quality:
- Black for code formatting
- isort for import sorting
- mypy for static type checking
- ruff for linting
- pre-commit hooks for automated checks

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
