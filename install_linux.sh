#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer is for Linux." >&2
  exit 1
fi

roots=("${MAMBA_ROOT_PREFIX:-$HOME/.local/share/micromamba}" "$HOME/micromamba")
for root in "${roots[@]}"; do
  python_path="$root/envs/channel-prime/bin/python"
  if [[ -x "$python_path" ]]; then
    echo "Using existing micromamba environment: $python_path"
    if ! "$python_path" -c 'import numpy, scipy, matplotlib' >/dev/null 2>&1; then
      "$python_path" -m pip install -r requirements.txt
    fi
    if [[ "${1:-}" != "--no-launch" ]]; then exec ./run_linux.sh; fi
    exit 0
  fi
done

selected_python=""
for executable in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$executable" >/dev/null 2>&1 &&
     "$executable" -c 'import sys, venv; assert sys.version_info >= (3, 11)' >/dev/null 2>&1; then
    selected_python="$(command -v "$executable")"
    break
  fi
done
if [[ -n "$selected_python" ]]; then
  echo "Creating a per-user environment with $selected_python"
  "$selected_python" -m venv .venv-prime
  .venv-prime/bin/python -m pip install -r requirements.txt
elif command -v micromamba >/dev/null 2>&1; then
  echo "Creating a per-user micromamba environment"
  micromamba create -y -n channel-prime -c conda-forge \
    python=3.11 pip numpy scipy matplotlib pillow pypdfium2
else
  echo "Python 3.11+ with venv or micromamba is required. No sudo is needed." >&2
  echo "Install micromamba under your home directory, then rerun this script." >&2
  exit 1
fi

if [[ "${1:-}" == "--no-launch" ]]; then
  echo "Ready. Start with: bash run_linux.sh"
else
  exec ./run_linux.sh
fi
