#!/bin/bash
# Update both indexes without treating a failed download as an empty repository.
# Initial publication/recovery is deliberately a separate, explicit operation.
set -euo pipefail

GITHUB_REPOSITORY="${1:-}"
if [ -z "$GITHUB_REPOSITORY" ]; then
  echo "Error: GITHUB_REPOSITORY not provided" >&2
  exit 1
fi

pacman -Sy --noconfirm pacman-contrib curl
root="$PWD"
staging=$(mktemp -d "$root/.repo-update.XXXXXX")
trap 'rm -rf "$staging"' EXIT

for index in skorion.db.tar.gz skorion.files.tar.gz; do
  echo "Downloading existing $index"
  # HTTP errors, timeouts and invalid archives must abort before repo-add/upload.
  curl --fail --show-error --location --retry 4 --retry-all-errors \
    --connect-timeout 20 --max-time 180 \
    "https://github.com/${GITHUB_REPOSITORY}/releases/download/latest/$index" \
    --output "$staging/$index"
  if [ ! -s "$staging/$index" ] || ! tar -tzf "$staging/$index" > "$staging/$index.list"; then
    echo "Invalid existing $index; refusing to replace the published repository" >&2
    exit 1
  fi
  if ! grep -q '/desc$' "$staging/$index.list"; then
    echo "Empty existing $index; use explicit index recovery" >&2
    exit 1
  fi
done
sort "$staging/skorion.db.tar.gz.list" | grep '/desc$' > "$staging/db-packages"
sort "$staging/skorion.files.tar.gz.list" | grep '/desc$' > "$staging/files-packages"
if ! cmp -s "$staging/db-packages" "$staging/files-packages"; then
  echo "Existing package and file indexes disagree; use explicit recovery" >&2
  exit 1
fi
old_count=$(wc -l < "$staging/db-packages")

shopt -s nullglob
packages=("$root"/output/*.pkg.tar.zst)
if [ "${#packages[@]}" -eq 0 ]; then
  echo "No new packages to add" >&2
  exit 1
fi
(
  cd "$staging"
  repo-add skorion.db.tar.gz "${packages[@]}"
  for index in skorion.db.tar.gz skorion.files.tar.gz; do
    tar -tzf "$index" > "$index.list"
    grep -q '/desc$' "$index.list"
  done
  sort skorion.db.tar.gz.list | grep '/desc$' > db-packages
  sort skorion.files.tar.gz.list | grep '/desc$' > files-packages
  cmp -s db-packages files-packages
  test "$(wc -l < db-packages)" -ge "$old_count"
)
# Publishable files are only replaced after the complete update succeeds.
cp "$staging/skorion.db.tar.gz" "$root/skorion.db.tar.gz"
cp "$staging/skorion.files.tar.gz" "$root/skorion.files.tar.gz"
ln -sf skorion.db.tar.gz skorion.db
ln -sf skorion.files.tar.gz skorion.files
echo "Database update complete"
