#!/bin/bash
# Update repository database (runs in Arch Linux container)
# Usage: update-repo-database.sh <github-repository>

set -euo pipefail

GITHUB_REPOSITORY="${1:-}"
if [ -z "$GITHUB_REPOSITORY" ]; then
  echo "Error: GITHUB_REPOSITORY not provided"
  exit 1
fi

echo "==> Updating repository database"

# Install necessary tools
pacman -Syu --noconfirm pacman-contrib curl

# Clean up old files
rm -f skorion.db.tar.gz skorion.files.tar.gz skorion.db skorion.files

# Download both indexes so repo-add preserves unaffected packages and file lists.
# Only a definite HTTP 404 means absent; transient failures/corruption fail closed.
download_index() {
  local filename="$1" status
  if status=$(curl -fSL --http1.1 --retry 4 --retry-all-errors \
      --connect-timeout 30 --max-time 300 --write-out '%{http_code}' \
      "https://github.com/${GITHUB_REPOSITORY}/releases/download/latest/$filename" \
      -o "$filename"); then
    tar -tzf "$filename" >/dev/null
    return 0
  elif [ "$status" = "404" ]; then
    rm -f "$filename"
    return 0
  else
    echo "Cannot download $filename (HTTP $status)" >&2
    return 1
  fi
}
download_index skorion.db.tar.gz
download_index skorion.files.tar.gz
if { [ -f skorion.db.tar.gz ] && [ ! -f skorion.files.tar.gz ]; } || \
   { [ ! -f skorion.db.tar.gz ] && [ -f skorion.files.tar.gz ]; }; then
  echo "Incomplete live index pair; refusing a partial update" >&2
  exit 1
fi
repo-add skorion.db.tar.gz output/*.pkg.tar.zst

# Create symlinks
ln -sf skorion.db.tar.gz skorion.db
ln -sf skorion.files.tar.gz skorion.files

echo "✓ Database update complete"

