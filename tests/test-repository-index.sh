#!/bin/bash
# Arch-only smoke test: real pacman upgrade and repo-add, no compilation/publish.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/remote" "$work/output" "$work/bin"
make_fixture() {
  local name="$1"
  mkdir -p "$work/stage/$name/usr/share/$name"
  printf 'fixture\n' > "$work/stage/$name/usr/share/$name/data"
  cat > "$work/stage/$name/.PKGINFO" <<EOF
pkgname = $name
pkgbase = $name
pkgver = 1.0-1
pkgdesc = Repository smoke test fixture
url = https://example.invalid
builddate = 1
packager = Test
size = 8
arch = any
license = MIT
EOF
  bsdtar --zstd -cf "$work/$name-1.0-1-any.pkg.tar.zst" -C "$work/stage/$name" .PKGINFO usr
}
make_fixture fixture-old
make_fixture fixture-new
cd "$work"
repo-add remote/skorion.db.tar.gz fixture-old-1.0-1-any.pkg.tar.zst
cp fixture-new-1.0-1-any.pkg.tar.zst output/
# Only the index HTTP transfer is mocked; pacman and repo-add remain real.
cat > bin/curl <<'EOF'
#!/bin/bash
set -eu
while [ "$#" -gt 0 ]; do
  if [ "$1" = '-o' ]; then shift; output="$1"; fi
  shift
done
cp "remote/$output" "$output"
printf 200
EOF
chmod +x bin/curl
PATH="$work/bin:$PATH" bash "$root/scripts/update-repo-database.sh" example/fixture
for name in fixture-old fixture-new; do
  bsdtar -xOf skorion.db.tar.gz "$name-1.0-1/desc" | grep -Fq "$name-1.0-1-any.pkg.tar.zst"
  bsdtar -xOf skorion.files.tar.gz "$name-1.0-1/files" | grep -Fq "usr/share/$name/data"
done
echo 'PASS: full upgrade and repo-add preserve both old/new package metadata and file lists'
