#!/usr/bin/env bash
set -euo pipefail

YES=false
BUMP_TYPE="patch"

for arg in "$@"; do
    case "$arg" in
        -y|--yes) YES=true ;;
        major|minor|patch) BUMP_TYPE="$arg" ;;
        *) echo "Error: unknown argument '$arg'"; exit 1 ;;
    esac
done

PYPROJECT="pyproject.toml"

if [[ "$BUMP_TYPE" != "major" && "$BUMP_TYPE" != "minor" && "$BUMP_TYPE" != "patch" ]]; then
    echo "Error: bump type must be one of: major, minor, patch"
    exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
    echo "Error: working tree is not clean. Commit or stash changes first."
    exit 1
fi

BRANCH="$(git branch --show-current)"
if [[ "$BRANCH" != "main" ]]; then
    echo "Error: must be on main branch (currently on '$BRANCH')"
    exit 1
fi

git fetch origin main
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then
    echo "Error: local main is not up to date with origin/main"
    exit 1
fi

CURRENT_VERSION="$(grep '^version = ' "$PYPROJECT" | head -1 | sed 's/version = "\(.*\)"/\1/')"
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT_VERSION"

echo "Current version: $CURRENT_VERSION"

case "$BUMP_TYPE" in
    major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
    minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
    patch) PATCH=$((PATCH + 1)) ;;
esac

NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}"
echo "New version:     $NEW_VERSION"

if [[ "$YES" != true ]]; then
    read -rp "Release v${NEW_VERSION}? [y/N] " CONFIRM
    if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
        echo "Aborted."
        exit 0
    fi
fi

sed -i '' "s/^version = \"${CURRENT_VERSION}\"/version = \"${NEW_VERSION}\"/" "$PYPROJECT"

uv sync --quiet

echo "Running checks..."
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict blockchainpype/
uv run pytest tests/ -x -q --ignore=tests/evm/test_hardhat.py --ignore=tests/evm/test_uniswap_hardhat_integration.py -m "not network"

git add "$PYPROJECT" uv.lock
git commit -m "chore: bump version to ${NEW_VERSION}"
git tag "v${NEW_VERSION}"
git push origin main "v${NEW_VERSION}"

gh release create "v${NEW_VERSION}" \
    --title "v${NEW_VERSION}" \
    --generate-notes

echo ""
echo "Released v${NEW_VERSION}!"
echo "Publish workflow: https://github.com/$(gh repo view --json nameWithOwner -q .nameWithOwner)/actions"
