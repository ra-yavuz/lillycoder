#!/usr/bin/env bash
# Build a .deb without debhelper. Mirrors the inhibit-charge template.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VERSION=$(sed -nE '1 s/^[^(]*\(([^)]+)\).*/\1/p' "$ROOT/debian/changelog")
[ -n "$VERSION" ] || { echo "could not parse version from debian/changelog" >&2; exit 1; }

PKG_DIR="$ROOT/dist/lillycoder_${VERSION}_all"
DEB_OUT="$ROOT/dist/lillycoder_${VERSION}_all.deb"

rm -rf "$PKG_DIR" "$DEB_OUT"
mkdir -p "$PKG_DIR/DEBIAN" \
         "$PKG_DIR/usr/bin" \
         "$PKG_DIR/usr/lib/lillycoder" \
         "$PKG_DIR/usr/share/doc/lillycoder"

# Bin shim
install -m 0755 "$ROOT/bin/lillycoder" "$PKG_DIR/usr/bin/lillycoder"

# Library: copy the lillycoder package (lib/lillycoder/) to /usr/lib/lillycoder/lillycoder/
cp -r "$ROOT/lib/lillycoder" "$PKG_DIR/usr/lib/lillycoder/"

# Strip __pycache__ from the package payload.
find "$PKG_DIR/usr/lib/lillycoder" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
find "$PKG_DIR/usr/lib/lillycoder" -name '*.pyc' -delete 2>/dev/null || true

# Docs
install -m 0644 "$ROOT/README.md" "$PKG_DIR/usr/share/doc/lillycoder/README.md"
install -m 0644 "$ROOT/LICENSE"   "$PKG_DIR/usr/share/doc/lillycoder/copyright"

# Maintainer scripts
install -m 0755 "$ROOT/debian/postinst" "$PKG_DIR/DEBIAN/postinst"
install -m 0755 "$ROOT/debian/postrm"   "$PKG_DIR/DEBIAN/postrm"

cat > "$PKG_DIR/DEBIAN/control" <<EOF
Package: lillycoder
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-httpx, python3-prompt-toolkit, python3-rich, python3-pydantic
Recommends: ripgrep
Suggests: nodejs, npm
Maintainer: Ramazan Yavuz <yavuzramazan1994@gmail.com>
Homepage: https://github.com/ra-yavuz/lillycoder
Description: local-first coder REPL with file and shell tools
 lillycoder drops you into a chat REPL inside any folder. The model on the
 other end can read, write, and edit files, run shell commands, install
 packages, and grep your project. It talks to any OpenAI-compatible /v1
 endpoint, so you pair it with whichever local LLM server you already use
 (llama.cpp, ollama, LM Studio, etc.). No cloud, no API key, no telemetry.
 .
 Every mutating action is gated. Hard-banned commands (sudo, rm -rf /,
 mkfs, fork bombs) are refused even with --bypass-permissions.
 .
 DISCLAIMER: provided AS IS, no warranty. The LLM can read, write, and
 delete files in the current working directory and run shell commands.
 You alone are responsible for any damage. By installing you accept full
 risk. See /usr/share/doc/lillycoder/README.md for the full disclaimer.
EOF

: > "$PKG_DIR/DEBIAN/conffiles"

dpkg-deb --build --root-owner-group "$PKG_DIR" "$DEB_OUT"
echo
echo "Built: $DEB_OUT"
ls -la "$DEB_OUT"
