const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

async function main() {
    console.log("Starting deployment of all test contracts...");

    const [deployer] = await hre.ethers.getSigners();
    console.log(`Deploying contracts with account: ${deployer.address}`);
    console.log(`Account balance: ${(await deployer.provider.getBalance(deployer.address)).toString()}`);

    const deployedContracts = {};

    // Deploy TestToken
    console.log("\n1. Deploying TestToken...");
    const TestToken = await hre.ethers.getContractFactory("TestToken");
    const testToken = await TestToken.deploy();
    await testToken.waitForDeployment();
    deployedContracts.TestToken = await testToken.getAddress();
    console.log(`TestToken deployed to: ${deployedContracts.TestToken}`);

    // Deploy another TestToken for pair creation
    console.log("\n2. Deploying TestToken2...");
    const testToken2 = await TestToken.deploy();
    await testToken2.waitForDeployment();
    deployedContracts.TestToken2 = await testToken2.getAddress();
    console.log(`TestToken2 deployed to: ${deployedContracts.TestToken2}`);

    // Deploy TestUniswapV2Factory
    console.log("\n3. Deploying TestUniswapV2Factory...");
    const TestUniswapV2Factory = await hre.ethers.getContractFactory("TestUniswapV2Factory");
    const factory = await TestUniswapV2Factory.deploy();
    await factory.waitForDeployment();
    deployedContracts.TestUniswapV2Factory = await factory.getAddress();
    console.log(`TestUniswapV2Factory deployed to: ${deployedContracts.TestUniswapV2Factory}`);

    // Create a pair
    console.log("\n4. Creating TestUniswapV2Pair...");
    const createPairTx = await factory.createPair(deployedContracts.TestToken, deployedContracts.TestToken2);
    await createPairTx.wait();
    const pairAddress = await factory.getPair(deployedContracts.TestToken, deployedContracts.TestToken2);
    deployedContracts.TestUniswapV2Pair = pairAddress;
    console.log(`TestUniswapV2Pair created at: ${pairAddress}`);

    // Deploy TestMultisig
    console.log("\n5. Deploying TestMultisig...");
    const owners = [deployer.address, "0x70997970C51812dc3A010C7d01b50e0d17dc79C8", "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"];
    const requiredConfirmations = 2;
    const TestMultisig = await hre.ethers.getContractFactory("TestMultisig");
    const multisig = await TestMultisig.deploy(owners, requiredConfirmations);
    await multisig.waitForDeployment();
    deployedContracts.TestMultisig = await multisig.getAddress();
    console.log(`TestMultisig deployed to: ${deployedContracts.TestMultisig}`);

    // Save deployment addresses to file
    const deploymentPath = path.join(__dirname, "..", "deployments.json");
    fs.writeFileSync(deploymentPath, JSON.stringify(deployedContracts, null, 2));

    console.log("\n=== Deployment Summary ===");
    console.log(JSON.stringify(deployedContracts, null, 2));
    console.log(`\nDeployment addresses saved to: ${deploymentPath}`);

    console.log("\n=== Initial Setup ===");
    // Mint some tokens to the deployer
    await testToken.mint(deployer.address, hre.ethers.parseEther("1000"));
    await testToken2.mint(deployer.address, hre.ethers.parseEther("1000"));
    console.log("Minted 1000 tokens to deployer for both TestToken and TestToken2");

    // Fund the multisig with some ETH
    await deployer.sendTransaction({
        to: deployedContracts.TestMultisig,
        value: hre.ethers.parseEther("1.0")
    });
    console.log("Funded multisig with 1 ETH");

    return deployedContracts;
}

main()
    .then((deployedContracts) => {
        console.log("\n✅ All contracts deployed successfully!");
        process.exit(0);
    })
    .catch((error) => {
        console.error("❌ Deployment failed:", error);
        process.exit(1);
    }); 