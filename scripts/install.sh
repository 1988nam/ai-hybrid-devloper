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

echo
echo "===== AI HYBRID DEVELOPER ====="
echo
echo "Installed:"
echo "  $BIN_DIR/ai-hybrid-developer"
echo
echo "Local coding factory:"
echo "  ${FACTORY_HOME:-$HOME/local-coding-factory}"
echo
echo "Optional frontier planners:"

if command -v codex >/dev/null 2>&1; then
  echo "  [OK] OpenAI Codex: $(command -v codex)"
else
  echo "  [--] OpenAI Codex CLI not found"
fi

if command -v gemini >/dev/null 2>&1; then
  echo "  [OK] Gemini CLI: $(command -v gemini)"
else
  echo "  [--] Gemini CLI not found"
fi

echo
echo "A missing frontier CLI does not prevent Local Factory use."
echo "Install/connect a provider only if you want in-dashboard design generation."
echo
echo "Start the dashboard:"
echo "  ai-hybrid-developer --open"
echo
echo "Then use:"
echo "  http://127.0.0.1:8787"
