#!/bin/zsh
# Install the LUT Match panel into Premiere Pro (per-user, no sudo).
# Re-run after any change to the cep/ files.
set -e

CEP_SRC="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(dirname "$CEP_SRC")"
DEST="$HOME/Library/Application Support/Adobe/CEP/extensions/LUTMatch"

mkdir -p "$DEST"
rsync -a --delete --exclude "install.sh" "$CEP_SRC/" "$DEST/"

# The installed panel is a copy — tell it where the project lives.
cat > "$DEST/config.json" <<EOF
{ "projectPath": "$PROJECT" }
EOF

echo "Installed to: $DEST"
echo "Project path: $PROJECT"
echo
echo "Restart Premiere Pro, then: Window → Extensions → LUT Match"
