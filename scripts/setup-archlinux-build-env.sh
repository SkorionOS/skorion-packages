#!/bin/bash
# Setup Arch Linux build environment in container

set -e

echo "==> Setting up Arch Linux build environment"

# Update system and install dependencies
echo "  → Installing base packages"
pacman -Syu --noconfirm
pacman -S --noconfirm base-devel git sudo jq curl
pacman -Scc --noconfirm

# Create builder user
echo "  → Creating builder user"
useradd -m -G wheel builder
echo "builder ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers

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

