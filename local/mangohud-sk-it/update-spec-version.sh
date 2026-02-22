#!/bin/bash
# Script to update mangohud-git.spec version from specified commit
# Usage:
#   ./update-spec-version.sh              # Use commit from spec file
#   ./update-spec-version.sh <commit>     # Use specified commit

set -e

SPEC_FILE="mangohud-git.spec"
REPO_URL="https://github.com/flightlessmango/MangoHud.git"
TEMP_DIR=$(mktemp -d)

cleanup() {
    rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

# Get commit: from argument or from spec file
if [ -n "$1" ]; then
    COMMIT="$1"
    echo "Using specified commit: $COMMIT"
else
    COMMIT=$(grep '^%global commit ' "$SPEC_FILE" | awk '{print $3}')
    echo "Using commit from spec: $COMMIT"
fi

if [ -z "$COMMIT" ]; then
    echo "Error: No commit specified and could not read from spec file"
    exit 1
fi

echo "Cloning MangoHud repository..."
git clone "$REPO_URL" "$TEMP_DIR/MangoHud"

cd "$TEMP_DIR/MangoHud"
git checkout "$COMMIT"

# Get version info similar to PKGBUILD's pkgver()
# Format: 0.8.3.r6.gd7654eb
PKGVER=$(git describe --tags --long --abbrev=7 | sed 's/^v//;s/-rc[0-9]\+-/-/;s/\([^-]*-g\)/r\1/;s/-/./g')
SHORT_COMMIT=$(git rev-parse --short=7 HEAD)
GIT_DATE=$(git log -1 --format=%cd --date=format:%Y%m%d "$COMMIT")

# Extract base version (e.g., 0.8.3 from 0.8.3.r6.gd7654eb)
BASE_VERSION=$(echo "$PKGVER" | sed 's/\.r[0-9]*\.g[a-f0-9]*$//')

echo ""
echo "Parsed info:"
echo "  Full version: $PKGVER"
echo "  Base version: $BASE_VERSION"
echo "  Commit: $COMMIT"
echo "  Short commit: $SHORT_COMMIT"
echo "  Commit date: $GIT_DATE"

cd - > /dev/null

# Update spec file
echo ""
echo "Updating $SPEC_FILE..."

sed -i \
    -e "s/^%global commit .*/%global commit $COMMIT/" \
    -e "s/^%global git_date .*/%global git_date $GIT_DATE/" \
    -e "s/^Version:.*/Version:        $BASE_VERSION/" \
    "$SPEC_FILE"

echo "Done!"
