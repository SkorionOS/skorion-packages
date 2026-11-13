#!/usr/bin/env bash
# Dedicated reproducible rebuild for the 2026-11-10 SkorionOS image.
# Run only inside the disposable Arch build container, not on the host.
set -euo pipefail
export LC_ALL=C
readonly archive_date=2026/11/10
readonly expected_llvm='1:23.1.4-1'
readonly expected_version='1:26.2.4.sk-3'
[[ $EUID == 0 && -f /workspace/local/lib32-mesa/PKGBUILD ]]
printf 'Server = https://archive.archlinux.org/repos/%s/$repo/os/$arch\n' "$archive_date" > /etc/pacman.d/mirrorlist
if ! grep -q '^\[multilib\]' /etc/pacman.conf; then
  printf '\n[multilib]\nInclude = /etc/pacman.d/mirrorlist\n' >> /etc/pacman.conf
fi
pacman-key --init
pacman-key --populate archlinux
# A full date-aligned downgrade is required when archlinux:latest is newer.
pacman -Syyuu --noconfirm
pacman -S --needed --noconfirm base-devel sudo git python curl jq
# Keep runtime packages only; debug splits are not part of this publication.
printf '\nOPTIONS+=(!debug)\n' >> /etc/makepkg.conf
useradd -m builder
printf 'builder ALL=(ALL) NOPASSWD: ALL\n' > /etc/sudoers.d/90-archive-builder
chmod 0440 /etc/sudoers.d/90-archive-builder
mkdir -p /build /workspace/output
cp /workspace/local/lib32-mesa/PKGBUILD /build/PKGBUILD
chown -R builder:builder /build /workspace/output
# Verify exact archive provider, without weakening the existing ELF checks.
[[ $(pacman -Si lib32-llvm-libs | sed -n 's/^Version *: //p') == "$expected_llvm" ]]
sudo -u builder bash -euo pipefail -c '
  cd /build
  source PKGBUILD
  [[ "$epoch:$pkgver-$pkgrel" == "1:26.2.4.sk-3" ]]
  mkdir -p ~/.gnupg
  chmod 700 ~/.gnupg
  printf "keyserver hkps://keyserver.ubuntu.com\n" > ~/.gnupg/gpg.conf
  for key in "${validpgpkeys[@]}"; do gpg --batch --recv-keys "$key"; done
  export PKGDEST=/workspace/output MAKEFLAGS="-j$(nproc)"
  makepkg --syncdeps --noconfirm --cleanbuild
'
[[ $(pacman -Q lib32-llvm-libs) == "lib32-llvm-libs $expected_llvm" ]]
python /workspace/scripts/verify-lib32-mesa-archive.py /workspace/output
for file in /workspace/output/*:*.pkg.tar.zst; do
  [[ -f $file ]] || continue
  mv "$file" "${file//:/--}"
done
{
  printf 'archive_date=%s\npackage_version=%s\n' "$archive_date" "$expected_version"
  pacman -Q lib32-llvm-libs
  printf 'recipe_sha256='; sha256sum /build/PKGBUILD
} > /workspace/output/build-provenance.txt
(cd /workspace/output && sha256sum ./*.pkg.tar.zst > SHA256SUMS)
