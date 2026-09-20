#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="${FACTORY_HOME:-$HOME/local-coding-factory}"

for name in factory.py runner.py guard_tests.py; do
  if [ ! -f "$RUNTIME/$name" ]; then
    echo "Missing: $RUNTIME/$name" >&2
    exit 1
  fi
done

mkdir -p "$ROOT/factory"
cp "$RUNTIME/factory.py" "$ROOT/factory/factory.py"
cp "$RUNTIME/runner.py" "$ROOT/factory/runner.py"
cp "$RUNTIME/guard_tests.py" "$ROOT/factory/guard_tests.py"

python3 - "$ROOT/factory/runner.py" "$ROOT/factory/factory.py" <<'PY'
from pathlib import Path
import sys

runner = Path(sys.argv[1])
factory = Path(sys.argv[2])

text = runner.read_text(encoding="utf-8")
old = '''    def branch_name(self, story):
        return (
            "local-agent/"
            + self.safe_id(story["id"])
        )
'''
new = '''    def branch_name(self, story):
        run_namespace = self.safe_id(
            self.args.integration_branch
        )

        return (
            "local-agent/"
            + run_namespace
            + "/"
            + self.safe_id(story["id"])
        )
'''
if old in text:
    text = text.replace(old, new)
elif "run_namespace = self.safe_id(" not in text:
    raise SystemExit("runner.py branch_name shape is unexpected; refusing to patch")
runner.write_text(text, encoding="utf-8")

text = factory.read_text(encoding="utf-8")
old = '''HOME = Path.home()
FACTORY_HOME = HOME / "local-coding-factory"
PROJECTS = FACTORY_HOME / "projects"
RUNS = FACTORY_HOME / "runs"
RUNNER = FACTORY_HOME / "runner.py"
'''
new = '''HOME = Path.home()
FACTORY_HOME = Path(
    os.environ.get(
        "FACTORY_HOME",
        str(HOME / "local-coding-factory"),
    )
).expanduser().resolve()
PROJECTS = FACTORY_HOME / "projects"
RUNS = FACTORY_HOME / "runs"
RUNNER = FACTORY_HOME / "runner.py"
'''
if old in text:
    text = text.replace(old, new)
elif 'os.environ.get(\n        "FACTORY_HOME"' not in text:
    raise SystemExit("factory.py FACTORY_HOME shape is unexpected; refusing to patch")
factory.write_text(text, encoding="utf-8")
PY

chmod +x "$ROOT/factory/factory.py" "$ROOT/factory/runner.py" "$ROOT/factory/guard_tests.py"

echo
echo "Imported Local Coding Factory source into:"
echo "  $ROOT/factory/"
echo
echo "Important:"
echo "  - runtime runs/, logs, inbox packages, current.json were NOT copied"
echo "  - runner Story branches are now namespaced by integration run"
echo "  - factory.py now honors FACTORY_HOME"
echo
echo "Review:"
echo "  git diff -- factory/"
