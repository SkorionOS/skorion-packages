#!/bin/bash
# Update repository database (runs in Arch Linux container)
# Usage: update-repo-database.sh <github-repository>

set -e

GITHUB_REPOSITORY="${1:-}"
if [ -z "$GITHUB_REPOSITORY" ]; then
  echo "Error: GITHUB_REPOSITORY not provided"
  exit 1
fi

echo "==> Updating repository database"

# Install necessary tools
pacman -Sy --noconfirm pacman-contrib curl

# Clean up old files
rm -f skorion.db.tar.gz skorion.files.tar.gz skorion.db skorion.files

# Download existing database
DB_EXISTS=false
echo "  → Downloading existing database"
if curl -sfL "https://github.com/${GITHUB_REPOSITORY}/releases/download/latest/skorion.db.tar.gz" \
    -o skorion.db.tar.gz; then
  echo "  ✓ Downloaded existing database"
  
  # Verify file integrity
  if [ -s skorion.db.tar.gz ] && tar -tzf skorion.db.tar.gz >/dev/null 2>&1; then
    echo "  ✓ Database file verified"
    DB_EXISTS=true
  else
    echo "  ⚠ Database file corrupted, creating new database"
    rm -f skorion.db.tar.gz
    DB_EXISTS=false
  fi
else
  echo "  → Creating first database (no existing database found)"
  DB_EXISTS=false
fi

# Update or create database
if [ "$DB_EXISTS" = "true" ]; then
  echo "  → Updating existing database"
  repo-add skorion.db.tar.gz output/*.pkg.tar.zst
else
  echo "  → Creating new database"
  repo-add skorion.db.tar.gz output/*.pkg.tar.zst
fi

# Create symlinks
ln -sf skorion.db.tar.gz skorion.db
ln -sf skorion.files.tar.gz skorion.files

echo "✓ Database update complete"

