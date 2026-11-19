#!/usr/bin/env bash
# Upload one member of the Mesa build pair without touching indexes or overwriting history.
set -euo pipefail

repository=$1 package=$2 revision=$3 directory=$4
case "$package" in
    mesa-git|lib32-mesa-git) ;;
    *) echo "Invalid Mesa git package: $package" >&2; exit 1 ;;
esac
[[ "$revision" =~ ^[0-9a-f]{40}$ ]]

shopt -s nullglob
packages=("$directory"/*.pkg.tar.zst)
if [ "${#packages[@]}" -ne 1 ]; then
    echo "Expected one $package binary, found ${#packages[@]}" >&2
    exit 1
fi
asset=${packages[0]}
name=${asset##*/}
pattern="^${package}-.+[.][0-9]+[.]([0-9a-f]{7,40})[.][0-9a-f]{7}-[0-9.]+-x86_64[.]pkg[.]tar[.]zst$"
if [[ ! "$name" =~ $pattern ]] || [[ "$revision" != "${BASH_REMATCH[1]}"* ]]; then
    echo "$name was not built from expected Mesa commit $revision" >&2
    exit 1
fi

# Existing packages, even unindexed ones, are immutable; preserve the original
# when a later job compiles the same recipe and source. Verification of the two
# selected payloads is performed by the index rebuild before publication.
for ((attempt = 1; attempt <= 5; attempt++)); do
    if manifest=$(gh api "repos/$repository/releases/tags/latest"); then
        if jq -e --arg name "$name" '.assets | any(.name == $name and .size > 0 and
            ((.digest // "") | test("^sha256:[0-9a-f]{64}$")))' <<< "$manifest" >/dev/null; then
            echo "Retaining existing Mesa asset: $name"
            exit 0
        fi
        if gh release upload latest "$asset" --repo "$repository"; then
            echo "Staged Mesa asset: $name"
            exit 0
        fi
    fi
    if ((attempt < 5)); then sleep "$((5 * attempt))"; fi
done
echo "Could not stage Mesa asset: $name" >&2
exit 1
