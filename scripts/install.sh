#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"

cat > "$BIN_DIR/ai-hybrid-developer" <<LAUNCHER
#!/usr/bin/env bash
exec python3 "$ROOT/run.py" "\$@"
LAUNCHER
chmod +x "$BIN_DIR/ai-hybrid-developer"

cat <<MSG
Installed: $BIN_DIR/ai-hybrid-developer

Start the dashboard:
  ai-hybrid-developer --open

Then use:
  http://127.0.0.1:8787

The dashboard expects the existing local coding factory at:
  ${FACTORY_HOME:-$HOME/local-coding-factory}
MSG
