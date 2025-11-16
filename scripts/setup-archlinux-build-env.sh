#!/bin/bash
# Setup Arch Linux build environment in container

set -e

PACKAGE_NAME="${1:-}"

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

REMOVE_PACKAGES=""

if [ "$PACKAGE_NAME" == "lib32-mesa-git" ]; then
  echo "  → Adding skorion repository"
  sed -i '/^\[core\]/i [skorion]\nSigLevel = Optional TrustAll\nServer = https://github.com/SkorionOS/skorion-packages/releases/download/latest\n' /etc/pacman.conf
  DEPENDENCIES_PACKAGES+=" mesa-git"

  REMOVE_PACKAGES="mesa vulkan-intel vulkan-radeon vulkan-mesa-device-select"
fi

pacman-key --init
pacman-key --populate archlinux
pacman -Sy

echo "  → Removing packages: $REMOVE_PACKAGES"
for package in $REMOVE_PACKAGES; do
  echo "  → Removing package: $package"
  pacman -Rnsdd --noconfirm $package || true
done

echo "  → Installing dependencies packages: $DEPENDENCIES_PACKAGES"
echo "  → Installing base packages"
pacman -Su --noconfirm
pacman -S --noconfirm $DEPENDENCIES_PACKAGES
pacman -Scc --noconfirm

# Create builder user
echo "  → Creating builder user"
useradd -m -G wheel builder
echo "builder ALL=(ALL) NOPASSWD: ALL" >>/etc/sudoers

# Install pikaur
echo "  → Installing pikaur"
MAX_RETRIES=5
RETRY_COUNT=0
while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
  if sudo -u builder bash -c "
    cd /tmp
    rm -rf pikaur
    git clone https://aur.archlinux.org/pikaur.git
    cd pikaur
    makepkg -si --noconfirm
  "; then
    echo "  ✓ Pikaur installed successfully"
    break
  else
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -lt $MAX_RETRIES ]; then
      WAIT_TIME=$((2 ** RETRY_COUNT))  # Exponential backoff: 2, 4, 8, 16, 32 seconds
      echo "  ⚠ Pikaur installation failed, retrying in ${WAIT_TIME}s ($RETRY_COUNT/$MAX_RETRIES)..."
      sleep $WAIT_TIME
    else
      echo "  ✗ Failed to install pikaur after $MAX_RETRIES attempts"
      exit 1
    fi
  fi
done

# Set permissions for workspace
echo "  → Setting workspace permissions"
chown -R builder:builder /workspace

echo "✓ Build environment ready"
