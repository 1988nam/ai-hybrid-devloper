#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$HOME/.local/bin"
FACTORY_RUNTIME="${FACTORY_HOME:-$HOME/local-coding-factory}"

mkdir -p "$BIN_DIR"
mkdir -p "$FACTORY_RUNTIME"
mkdir -p "$FACTORY_RUNTIME/projects"
mkdir -p "$FACTORY_RUNTIME/runs"
mkdir -p "$FACTORY_RUNTIME/inbox"

for name in factory.py runner.py guard_tests.py; do
  source_path="$ROOT/factory/$name"
  if [ ! -f "$source_path" ]; then
    echo "Missing tracked Factory source: $source_path" >&2
    exit 1
  fi
  install -m 0755 "$source_path" "$FACTORY_RUNTIME/$name"
done

cat > "$BIN_DIR/factory" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
RUNTIME="${FACTORY_HOME:-$HOME/local-coding-factory}"
exec env FACTORY_HOME="$RUNTIME" python3 "$RUNTIME/factory.py" "$@"
LAUNCHER
chmod +x "$BIN_DIR/factory"

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
echo "  $BIN_DIR/factory"
echo
echo "Local coding factory runtime:"
echo "  $FACTORY_RUNTIME"
echo
echo "Factory source synced from Git:"
echo "  $ROOT/factory/"
echo
echo "Preserved runtime state/config:"
echo "  $FACTORY_RUNTIME/projects/"
echo "  $FACTORY_RUNTIME/runs/"
echo "  $FACTORY_RUNTIME/inbox/"
echo
echo "Optional frontier planners:"

if command -v codex >/dev/null 2>&1; then
  echo "  [OK] OpenAI Codex: $(command -v codex)"
else
  echo "  [--] OpenAI Codex CLI not found"
fi

if command -v gemini >/dev/null 2>&1; then
  GEMINI_PATH="$(command -v gemini)"
  case "$GEMINI_PATH" in
    /mnt/[A-Za-z]/*)
      echo "  [!!] Gemini CLI is Windows-hosted: $GEMINI_PATH"
      echo "       Install a native WSL copy with: npm install -g @google/gemini-cli"
      ;;
    *)
      echo "  [OK] Gemini CLI: $GEMINI_PATH"
      ;;
  esac
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
