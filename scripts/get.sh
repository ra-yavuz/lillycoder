#!/usr/bin/env bash
# One-shot installer that adds the ra-yavuz apt repository, then installs
# lillycoder from it. Idempotent: re-running it is safe.
#
# Run with sudo. The recommended invocation is:
#
#   curl -fsSL https://raw.githubusercontent.com/ra-yavuz/lillycoder/main/scripts/get.sh \
#     | sudo bash
#
# Or, if you want to read it first (recommended for any 'curl | bash'):
#
#   curl -fsSL https://raw.githubusercontent.com/ra-yavuz/lillycoder/main/scripts/get.sh -o get.sh
#   less get.sh
#   sudo bash get.sh
#
# After install, run 'lillycoder' inside any project directory. lillycoder
# expects an OpenAI-compatible /v1 endpoint to be running already (llama.cpp,
# ollama, LM Studio, etc). It does not start an LLM server for you.
#
# DISCLAIMER: lillycoder runs an LLM that can read, write, and delete files
# in the current working directory and run shell commands. It is provided
# AS IS, WITHOUT WARRANTY OF ANY KIND. By installing or running this
# software you accept full responsibility for any damage to your data,
# hardware, or system. See the project README for the full disclaimer.

set -euo pipefail

REPO_HOST=ra-yavuz.github.io/apt
KEY_URL="https://${REPO_HOST}/pubkey.gpg"
KEYRING=/etc/apt/keyrings/ra-yavuz.gpg
SOURCES_LIST=/etc/apt/sources.list.d/ra-yavuz.list
SOURCE_LINE="deb [arch=amd64,arm64 signed-by=${KEYRING}] https://${REPO_HOST} stable main"
PKG=lillycoder

log()  { printf '[get.sh] %s\n' "$*"; }
fail() { printf '[get.sh] ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "must be run as root: sudo bash $0 (or pipe through 'sudo bash')"

# Sanity-check the host has a Debian-derived apt + curl available.
command -v apt-get >/dev/null 2>&1 || fail "apt-get not found; this script targets Debian/Ubuntu and derivatives."
command -v curl >/dev/null 2>&1 || {
    log "curl not found; installing it"
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y curl
}
command -v gpg >/dev/null 2>&1 || {
    log "gnupg not found; installing it"
    DEBIAN_FRONTEND=noninteractive apt-get install -y gnupg
}

# 1. Trust the signing key. We always (re)write the keyring file so a
# rotated key picks up on re-runs without manual cleanup.
log "fetching signing key from $KEY_URL"
install -m 0755 -d /etc/apt/keyrings
TMP_KEY=$(mktemp)
trap 'rm -f "$TMP_KEY"' EXIT
curl -fsSL "$KEY_URL" -o "$TMP_KEY"
# Validate the file is a real PGP keyring before installing it.
if ! gpg --no-default-keyring --keyring "$TMP_KEY" --list-keys >/dev/null 2>&1; then
    fail "fetched file is not a valid GPG keyring; aborting."
fi
install -m 0644 "$TMP_KEY" "$KEYRING"
log "installed keyring at $KEYRING"

# 2. Add the apt source. Re-run is safe; we replace the file.
log "adding apt source at $SOURCES_LIST"
echo "$SOURCE_LINE" > "$SOURCES_LIST"
chmod 0644 "$SOURCES_LIST"

# 3. Update and install.
log "running apt update"
DEBIAN_FRONTEND=noninteractive apt-get update
log "installing $PKG"
DEBIAN_FRONTEND=noninteractive apt-get install -y "$PKG"

# 4. Friendly summary at the very end so it does not scroll off-screen.
echo
echo "================================================================"
echo "  lillycoder installed. Quick reference:"
echo "================================================================"
echo
echo "  lillycoder                       (start REPL in current folder)"
echo "  lillycoder --api URL/v1          (skip auto-discovery)"
echo "  lillycoder --help                (full flag reference)"
echo
echo "  Future upgrades: sudo apt upgrade"
echo "  Full removal:    sudo apt purge lillycoder"
echo
echo "  lillycoder needs an OpenAI-compatible /v1 endpoint running"
echo "  somewhere reachable (llama.cpp, ollama, LM Studio, hydra-llm,"
echo "  etc). It does not ship or start a model for you."
echo
