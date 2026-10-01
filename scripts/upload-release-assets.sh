#!/bin/bash
# Upload assets to an existing release, sequentially with per-file retries.
# Usage: bash scripts/upload-release-assets.sh OWNER/REPO TAG ASSET_DIR
# Requires GitHub CLI (preinstalled on the Ubuntu Actions runner) and GH_TOKEN.

set -euo pipefail

if [ "$#" -ne 3 ] || [ -z "$1" ] || [ -z "$2" ] || [ ! -d "$3" ]; then
    echo "Usage: $0 OWNER/REPO TAG ASSET_DIR (directory must exist)" >&2
    exit 1
fi

repository="$1"
tag="$2"
asset_dir="$3"
max_attempts=5

if ! command -v gh >/dev/null 2>&1; then
    echo "GitHub CLI (gh) is required to upload release assets" >&2
    exit 1
fi

# Quoted array entries preserve filenames with spaces and database symlinks.
# Match the previous output/* scope: regular files, excluding dotfiles.
shopt -s nullglob
assets=()
for asset in "$asset_dir"/*; do
    if [ -f "$asset" ]; then
        assets+=("$asset")
    fi
done
if [ "${#assets[@]}" -eq 0 ]; then
    echo "No release assets found in $asset_dir" >&2
    exit 1
fi

for asset in "${assets[@]}"; do
    for ((attempt = 1; attempt <= max_attempts; attempt++)); do
        echo "Uploading ${asset##*/} (attempt $attempt/$max_attempts)"
        # One file per invocation prevents concurrent uploads. --clobber also
        # replaces a partial upload after a lost response or a same-day rerun.
        if gh release upload "$tag" "$asset" --repo "$repository" --clobber; then
            break
        fi
        if [ "$attempt" -eq "$max_attempts" ]; then
            echo "Failed to upload ${asset##*/} after $max_attempts attempts" >&2
            exit 1
        fi
        delay=$((5 * (2 ** (attempt - 1))))
        echo "Retrying ${asset##*/} in ${delay}s" >&2
        sleep "$delay"
    done
done

echo "Uploaded ${#assets[@]} release assets to $repository ($tag)"
