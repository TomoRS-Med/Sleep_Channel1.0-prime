#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This launcher is for Linux." >&2
  exit 1
fi

roots=("${MAMBA_ROOT_PREFIX:-$HOME/.local/share/micromamba}" "$HOME/micromamba")
candidates=("$(pwd)/.venv-prime/bin/python")
for root in "${roots[@]}"; do
  candidates+=("$root/envs/channel-prime/bin/python")
done
for executable in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$executable" >/dev/null 2>&1; then
    candidates+=("$(command -v "$executable")")
  fi
done
for python_path in "${candidates[@]}"; do
  if [[ -x "$python_path" ]] &&
     "$python_path" -c 'import sys, numpy, scipy, matplotlib; assert sys.version_info >= (3, 11)' >/dev/null 2>&1; then
    echo "Using $python_path"
    exec "$python_path" "$(pwd)/web_app.py" "$@"
  fi
done

echo "Python 3.11+ with numpy, scipy and matplotlib is needed." >&2
echo "Run 'bash install_linux.sh' from this folder, then retry." >&2
exit 1
