#!/usr/bin/env bash
# One command: install, test, run all experiments, verify.   Usage:  bash setup_and_run.sh [smoke|quick|full]
#   smoke  ~2 min   tiny run, checks that everything works on your computer
#   quick  ~10-25 min  fewer seeds, same conclusions
#   full   ~15-45 min  exactly the run behind the README numbers (default)
set -e
cd "$(dirname "$0")"
MODE="${1:-full}"

say() { printf "\n\033[1;34m== %s\033[0m\n" "$1"; }

say "1/6  Checking Python"
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 was not found."
  echo "Ubuntu: run   sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
  echo "macOS : run   xcode-select --install   (click Install in the window, wait, then run this script again)"
  exit 1
fi
python3 -c 'import sys; print("Python", sys.version.split()[0]); assert sys.version_info >= (3, 9), "need Python 3.9 or newer"'

say "2/6  Creating a private Python environment (.venv)"
rm -rf .venv
python3 -m venv .venv || { echo "If this failed on Ubuntu, run:  sudo apt install -y python3-venv   and then run this script again."; exit 1; }
# shellcheck disable=SC1091
. .venv/bin/activate

say "3/6  Installing packages (needs internet, 1-3 minutes)"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

say "4/6  Running the unit tests"
python -m pytest -q tests

say "5/6  Running the experiments (mode: $MODE)"
# keep the shipped results for comparison, start from an empty results folder
if [ -d results ] && [ ! -d results_reference ]; then mv results results_reference; fi
if [ -d figures ] && [ ! -d figures_reference ]; then mv figures figures_reference; fi
rm -rf results figures; mkdir -p results figures
JOBS=$(python -c 'import os; print(max(1, (os.cpu_count() or 2) - 1))')
echo "Using $JOBS parallel workers. You can leave this running; progress lines appear below."
python run_all.py --mode "$MODE" --jobs "$JOBS"

say "6/6  Verifying the results"
python verify_results.py

echo
echo "Done. Figures are in the folder:  $(pwd)/figures"
echo "Tables are in the folder:         $(pwd)/results"
