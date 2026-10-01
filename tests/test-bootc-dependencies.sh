#!/usr/bin/env bash
# Offline regression check for bootc's mandatory SELinux and bindgen dependencies.
set -euo pipefail
cd "$(dirname "$0")/.."
# PKGBUILD top-level declarations are inspected without invoking its build functions.
source local/bootc-git/PKGBUILD
contains() {
  local wanted=$1 item
  shift
  for item in "$@"; do
    [[ "$item" == "$wanted" ]] && return 0
  done
  return 1
}
contains libselinux "${depends[@]}"
contains clang "${makedepends[@]}"
# Runtime dependencies must also be published for pacman users, not just built by pikaur.
for dependency in libselinux libsepol; do
  grep -Fxq "$dependency" aur.conf
done
[[ "$pkgrel" -ge 2 ]]
printf '%s\n' 'PASS: bootc runtime/build dependencies and repository coverage'
