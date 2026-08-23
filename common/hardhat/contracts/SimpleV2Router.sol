// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";

interface ISimpleV2Factory {
    function getPair(
        address tokenA,
        address tokenB
    ) external view returns (address pair);
}

interface ISimpleV2Pair {
    function token0() external view returns (address);

    function token1() external view returns (address);

    function getReserves()
        external
        view
        returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast);

    function mint(address to) external returns (uint256 liquidity);

    function swap(uint256 amount0Out, uint256 amount1Out, address to) external;
}

/// @title SimpleV2Router
/// @notice Minimal UniswapV2Router02-compatible router for the test
///         TestUniswapV2Factory/TestUniswapV2Pair contracts. Implements the
///         quoting and swapping surface needed by integration tests:
///         getAmountOut/getAmountIn, getAmountsOut/getAmountsIn,
///         swapExactTokensForTokens/swapTokensForExactTokens and a simplified
///         addLiquidity. Pairs are resolved through the factory instead of the
///         canonical create2 pair-code-hash computation.
contract SimpleV2Router {
    address public immutable factory;

    constructor(address _factory) {
        factory = _factory;
    }

    modifier ensure(uint256 deadline) {
        require(deadline >= block.timestamp, "SimpleV2Router: EXPIRED");
        _;
    }

    // ─── Liquidity ──────────────────────────────────────────────────────

    /// @notice Simplified addLiquidity: transfers exactly the desired amounts
    ///         to the pair and mints LP tokens. Min-amount arguments are kept
    ///         for UniswapV2Router02 signature compatibility but the desired
    ///         amounts are always used as-is.
    function addLiquidity(
        address tokenA,
        address tokenB,
        uint256 amountADesired,
        uint256 amountBDesired,
        uint256 /* amountAMin */,
        uint256 /* amountBMin */,
        address to,
        uint256 deadline
    )
        external
        ensure(deadline)
        returns (uint256 amountA, uint256 amountB, uint256 liquidity)
    {
        address pair = _pairFor(tokenA, tokenB);
        amountA = amountADesired;
        amountB = amountBDesired;
        require(
            IERC20(tokenA).transferFrom(msg.sender, pair, amountA),
            "SimpleV2Router: TRANSFER_FROM_FAILED"
        );
        require(
            IERC20(tokenB).transferFrom(msg.sender, pair, amountB),
            "SimpleV2Router: TRANSFER_FROM_FAILED"
        );
        liquidity = ISimpleV2Pair(pair).mint(to);
    }

    // ─── Quoting ────────────────────────────────────────────────────────

    function getAmountOut(
        uint256 amountIn,
        uint256 reserveIn,
        uint256 reserveOut
    ) public pure returns (uint256 amountOut) {
        require(amountIn > 0, "SimpleV2Router: INSUFFICIENT_INPUT_AMOUNT");
        require(
            reserveIn > 0 && reserveOut > 0,
            "SimpleV2Router: INSUFFICIENT_LIQUIDITY"
        );
        uint256 amountInWithFee = amountIn * 997;
        amountOut =
            (amountInWithFee * reserveOut) /
            (reserveIn * 1000 + amountInWithFee);
    }

    function getAmountIn(
        uint256 amountOut,
        uint256 reserveIn,
        uint256 reserveOut
    ) public pure returns (uint256 amountIn) {
        require(amountOut > 0, "SimpleV2Router: INSUFFICIENT_OUTPUT_AMOUNT");
        require(
            reserveIn > 0 && reserveOut > 0,
            "SimpleV2Router: INSUFFICIENT_LIQUIDITY"
        );
        amountIn =
            (reserveIn * amountOut * 1000) /
            ((reserveOut - amountOut) * 997) +
            1;
    }

    function getAmountsOut(
        uint256 amountIn,
        address[] memory path
    ) public view returns (uint256[] memory amounts) {
        require(path.length >= 2, "SimpleV2Router: INVALID_PATH");
        amounts = new uint256[](path.length);
        amounts[0] = amountIn;
        for (uint256 i; i < path.length - 1; i++) {
            (uint256 reserveIn, uint256 reserveOut) = _getReserves(
                path[i],
                path[i + 1]
            );
            amounts[i + 1] = getAmountOut(amounts[i], reserveIn, reserveOut);
        }
    }

    function getAmountsIn(
        uint256 amountOut,
        address[] memory path
    ) public view returns (uint256[] memory amounts) {
        require(path.length >= 2, "SimpleV2Router: INVALID_PATH");
        amounts = new uint256[](path.length);
        amounts[path.length - 1] = amountOut;
        for (uint256 i = path.length - 1; i > 0; i--) {
            (uint256 reserveIn, uint256 reserveOut) = _getReserves(
                path[i - 1],
                path[i]
            );
            amounts[i - 1] = getAmountIn(amounts[i], reserveIn, reserveOut);
        }
    }

    // ─── Swapping ───────────────────────────────────────────────────────

    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external ensure(deadline) returns (uint256[] memory amounts) {
        amounts = getAmountsOut(amountIn, path);
        require(
            amounts[amounts.length - 1] >= amountOutMin,
            "SimpleV2Router: INSUFFICIENT_OUTPUT_AMOUNT"
        );
        require(
            IERC20(path[0]).transferFrom(
                msg.sender,
                _pairFor(path[0], path[1]),
                amounts[0]
            ),
            "SimpleV2Router: TRANSFER_FROM_FAILED"
        );
        _swap(amounts, path, to);
    }

    function swapTokensForExactTokens(
        uint256 amountOut,
        uint256 amountInMax,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external ensure(deadline) returns (uint256[] memory amounts) {
        amounts = getAmountsIn(amountOut, path);
        require(
            amounts[0] <= amountInMax,
            "SimpleV2Router: EXCESSIVE_INPUT_AMOUNT"
        );
        require(
            IERC20(path[0]).transferFrom(
                msg.sender,
                _pairFor(path[0], path[1]),
                amounts[0]
            ),
            "SimpleV2Router: TRANSFER_FROM_FAILED"
        );
        _swap(amounts, path, to);
    }

    // ─── Internals ──────────────────────────────────────────────────────

    function _sortTokens(
        address tokenA,
        address tokenB
    ) internal pure returns (address token0, address token1) {
        require(tokenA != tokenB, "SimpleV2Router: IDENTICAL_ADDRESSES");
        (token0, token1) = tokenA < tokenB
            ? (tokenA, tokenB)
            : (tokenB, tokenA);
        require(token0 != address(0), "SimpleV2Router: ZERO_ADDRESS");
    }

    function _pairFor(
        address tokenA,
        address tokenB
    ) internal view returns (address pair) {
        pair = ISimpleV2Factory(factory).getPair(tokenA, tokenB);
        require(pair != address(0), "SimpleV2Router: PAIR_NOT_FOUND");
    }

    function _getReserves(
        address tokenA,
        address tokenB
    ) internal view returns (uint256 reserveA, uint256 reserveB) {
        (address token0, ) = _sortTokens(tokenA, tokenB);
        (uint256 reserve0, uint256 reserve1, ) = ISimpleV2Pair(
            _pairFor(tokenA, tokenB)
        ).getReserves();
        (reserveA, reserveB) = tokenA == token0
            ? (reserve0, reserve1)
            : (reserve1, reserve0);
    }

    function _swap(
        uint256[] memory amounts,
        address[] memory path,
        address _to
    ) internal {
        for (uint256 i; i < path.length - 1; i++) {
            (address input, address output) = (path[i], path[i + 1]);
            (address token0, ) = _sortTokens(input, output);
            uint256 amountOut = amounts[i + 1];
            (uint256 amount0Out, uint256 amount1Out) = input == token0
                ? (uint256(0), amountOut)
                : (amountOut, uint256(0));
            address to = i < path.length - 2
                ? _pairFor(output, path[i + 2])
                : _to;
            ISimpleV2Pair(_pairFor(input, output)).swap(
                amount0Out,
                amount1Out,
                to
            );
        }
    }
}
