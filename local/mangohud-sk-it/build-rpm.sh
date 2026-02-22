#!/bin/bash
# Build MangoHud RPM package using Docker
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_NAME="mangohud-builder"

# Check if Docker image exists, build if not
if ! docker image inspect "$IMAGE_NAME" &>/dev/null; then
    echo "Building Docker image..."
    docker build --platform linux/amd64 -t "$IMAGE_NAME" -f "$SCRIPT_DIR/Dockerfile.build" "$SCRIPT_DIR"
fi

echo "Building RPM..."
docker run --rm --platform linux/amd64 -v "$SCRIPT_DIR:/src" "$IMAGE_NAME" bash -c '
set -e
cp /src/mangohud-git.spec ~/rpmbuild/SPECS/
cp /src/*.patch ~/rpmbuild/SOURCES/

cd ~/rpmbuild/SOURCES
spectool -g -R ../SPECS/mangohud-git.spec

rpmbuild -ba ~/rpmbuild/SPECS/mangohud-git.spec

cp ~/rpmbuild/RPMS/x86_64/*.rpm /src/
cp ~/rpmbuild/RPMS/noarch/*.rpm /src/

echo ""
echo "=== Build completed ==="
ls -lh /src/*.rpm
'

