#!/bin/bash
# Setup Arch Linux build environment in container

set -e

echo "==> Setting up Arch Linux build environment"

# Setup GPG
echo "  → Setting up GPG"
mkdir -p /etc/gnupg/
echo -e "keyserver-options auto-key-retrieve" >>/etc/gnupg/gpg.conf

echo "  → Setting up multilib"
{
  echo ""
  echo "[multilib]"
  echo "Include = /etc/pacman.d/mirrorlist"
} >>/etc/pacman.conf

# Update system and install dependencies
DEPENDENCIES_PACKAGES="base-devel git sudo jq curl libdisplay-info lib32-libdisplay-info"

if [ "$PACKAGE_NAME" == "lib32-mesa-git" ]; then
  echo "  → Adding skorion repository"
  sed -i '/^\[core\]/i [skorion]\nSigLevel = Optional TrustAll\nServer = https://github.com/SkorionOS/skorion-packages/releases/download/latest\n' /etc/pacman.conf
  DEPENDENCIES_PACKAGES+=" mesa-git"
fi

echo "  → Installing dependencies packages: $DEPENDENCIES_PACKAGES"
echo "  → Installing base packages"
pacman-key --init
pacman-key --populate archlinux
pacman -Syu --noconfirm
pacman -S --noconfirm $DEPENDENCIES_PACKAGES
pacman -Scc --noconfirm

# Create builder user
echo "  → Creating builder user"
useradd -m -G wheel builder
echo "builder ALL=(ALL) NOPASSWD: ALL" >>/etc/sudoers

# Install pikaur
echo "  → Installing pikaur"
sudo -u builder bash -c "
  cd /tmp
  git clone https://aur.archlinux.org/pikaur.git
  cd pikaur
  makepkg -si --noconfirm
"

# Set permissions for workspace
echo "  → Setting workspace permissions"
chown -R builder:builder /workspace

echo "✓ Build environment ready"
