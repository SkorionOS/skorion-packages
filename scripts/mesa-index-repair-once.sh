#!/usr/bin/env bash
set -Eeuo pipefail
[[ "${GH_REPO:-}" == SkorionOS/skorion-packages ]]
[[ "${GITHUB_REF:-}" == refs/heads/maintenance/mesa-index-repair-20261119 ]]
[[ "${GITHUB_RUN_ATTEMPT:-}" == 1 ]] || { echo "Not a first attempt; refusing repeat mutation" >&2; exit 1; }
base="repos/$GH_REPO/releases"
old_pkg=lib32-mesa-git-27.0.0_devel.234174.0e9e1086bb8.d41d8cd-1-x86_64.pkg.tar.zst
new_pkg=lib32-mesa-git-27.0.0_devel.234237.6f23942285e.d41d8cd-1-x86_64.pkg.tar.zst
names=(skorion.db skorion.files skorion.db.tar.gz skorion.files.tar.gz)
declare -A expected live_id stage_id candidate
for name in "${names[@]}"; do
    if [[ "$name" == skorion.db* ]]; then
        expected[$name]=1d8c793e9bb5f5ee0460999d78a30b1333dce4ce5b3478f8f0ec52027cd29004
    else
        expected[$name]=e6f450dc6726d39fc2e8caa61f2e2e99e3894722270a93ac9f2f76c26a70fc28
    fi
done
work="$(mktemp -d)"
mkdir "$work/live" "$work/dated" "$work/repaired" "$work/verify"
suffix=mesa-pair-20261119
stage_name() { printf '%s.stage-%s\n' "$1" "$suffix"; }
backup_name() { printf '%s.backup-%s\n' "$1" "$suffix"; }
get_sha() { sha256sum "$1" | cut -d' ' -f1; }
require_sha() {
    [[ "$(get_sha "$1")" == "$2" ]] || { echo "Unexpected digest for $1" >&2; return 1; }
}
fetch() {
    local tag="$1" name="$2" directory="$3" i
    for i in 1 2 3; do
        if gh release download "$tag" --pattern "$name" --dir "$directory" --clobber; then return 0; fi
        sleep "$i"
    done
    return 1
}
refresh() {
    gh api --paginate --slurp "$base/$live_release/assets?per_page=100" | jq -e 'add | arrays' > "$work/assets.json"
}
asset_id() {
    jq -er --arg name "$1" '[.[] | select(.name == $name)] | if length == 1 then .[0].id else error("asset absent or ambiguous: " + $name) end' "$work/assets.json"
}
metadata_sha() {
    jq -er --argjson id "$1" '[.[] | select(.id == $id)] | if length == 1 then .[0].digest else error("asset absent or ambiguous") end' "$work/assets.json"
}
absent() {
    jq -e --arg name "$1" 'all(.[]; .name != $name)' "$work/assets.json" >/dev/null
}
name_by_id() { gh api "$base/assets/$1" --jq .name; }
rename_id() {
    local id="$1" target="$2"
    if [[ "$(name_by_id "$id")" == "$target" ]]; then return 0; fi
    if ! gh api -X PATCH "$base/assets/$id" -f name="$target" --jq .name; then
        # A timed-out response may still have changed the name. Do not blindly retry.
        [[ "$(name_by_id "$id")" == "$target" ]] || return 1
    fi
    [[ "$(name_by_id "$id")" == "$target" ]]
}

live_release="$(gh api "$base/tags/latest" --jq 'select(.tag_name == "latest" and .draft == false) | .id')"
[[ "$live_release" =~ ^[0-9]+$ ]]
refresh
for name in "${names[@]}"; do
    absent "$(stage_name "$name")"
    absent "$(backup_name "$name")"
    live_id[$name]="$(asset_id "$name")"
    [[ "$(metadata_sha "${live_id[$name]}")" == "sha256:${expected[$name]}" ]]
    fetch latest "$name" "$work/live"
    require_sha "$work/live/$name" "${expected[$name]}"
    # This remote copy is verified before making any release changes; never modify it.
    fetch 2026.11.19 "$name" "$work/dated"
    require_sha "$work/dated/$name" "${expected[$name]}"
done
for package in "$old_pkg" "$new_pkg"; do
    asset_id "$package" >/dev/null
    fetch latest "$package" "$work/live"
done
python3 scripts/mesa-index-repair.py "$work/live" "$work/repaired"
# Record all existing package asset names, IDs and digests; none may be changed by this run.
jq -e '[.[] | select(.name | endswith(".pkg.tar.zst")) | [.id, .name, .digest]] | sort' "$work/assets.json" > "$work/packages.json"

for name in "${names[@]}"; do
    stage="$(stage_name "$name")"
    cp "$work/repaired/$name" "$work/repaired/$stage"
    candidate[$name]="$(get_sha "$work/repaired/$name")"
    if ! gh release upload latest "$work/repaired/$stage"; then
        # Never overwrite or reupload after an uncertain response.
        refresh
        asset_id "$stage" >/dev/null || exit 1
    fi
    refresh
    stage_id[$name]="$(asset_id "$stage")"
    [[ "$(metadata_sha "${stage_id[$name]}")" == "sha256:${candidate[$name]}" ]]
    fetch latest "$stage" "$work/verify"
    require_sha "$work/verify/$stage" "${candidate[$name]}"
done

rollback() {
    local status=$? failed=0 name
    if (( status == 0 )); then return; fi
    set +e
    echo "::warning::Repair interrupted; restoring original latest aliases by asset ID"
    for name in "${names[@]}"; do
        # Remove a conflicting canonical name by moving only our own staged asset.
        if [[ "$(name_by_id "${stage_id[$name]}")" == "$name" ]]; then
            rename_id "${stage_id[$name]}" "$(stage_name "$name")" || failed=1
        fi
        rename_id "${live_id[$name]}" "$name" || failed=1
    done
    if (( failed )); then
        echo "::error::Rollback not fully verified; unchanged 2026.11.19 has all four verified original binaries"
    else
        echo "Original canonical alias names restored."
    fi
    exit "$status"
}

# Reverify preconditions just before moving aliases.
refresh
for name in "${names[@]}"; do
    [[ "$(asset_id "$name")" == "${live_id[$name]}" ]]
    [[ "$(metadata_sha "${live_id[$name]}")" == "sha256:${expected[$name]}" ]]
    absent "$(backup_name "$name")"
    [[ "$(asset_id "$(stage_name "$name")")" == "${stage_id[$name]}" ]]
done
trap rollback EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
for name in "${names[@]}"; do
    rename_id "${live_id[$name]}" "$(backup_name "$name")"
    rename_id "${stage_id[$name]}" "$name"
done
refresh
for name in "${names[@]}"; do
    [[ "$(asset_id "$name")" == "${stage_id[$name]}" ]]
    [[ "$(asset_id "$(backup_name "$name")")" == "${live_id[$name]}" ]]
    [[ "$(metadata_sha "${stage_id[$name]}")" == "sha256:${candidate[$name]}" ]]
    [[ "$(metadata_sha "${live_id[$name]}")" == "sha256:${expected[$name]}" ]]
    fetch latest "$name" "$work/verify"
    require_sha "$work/verify/$name" "${candidate[$name]}"
done
jq -e '[.[] | select(.name | endswith(".pkg.tar.zst")) | [.id, .name, .digest]] | sort' "$work/assets.json" > "$work/packages-after.json"
cmp "$work/packages.json" "$work/packages-after.json"
trap - EXIT TERM INT
echo "All four latest aliases and backup assets verified; every package asset is unchanged."
