#!/usr/bin/env bash
# Push this repo to GitHub as a private repo and trigger the Windows build.
#
#   bash tools/push-to-github.sh [--public]
#
# Mirrors the older dubai-estate app's helper: installs gh via brew if
# missing, authenticates interactively, creates the repo, pushes main, and
# fires the desktop build workflow. The data snapshot is NOT pushed — it is
# a release asset; see desktop/README.md ("The data snapshot").

set -euo pipefail
cd "$(dirname "$0")/.."

REPO_NAME="real-estate-app"
VISIBILITY="${1:---private}"

if ! command -v gh >/dev/null 2>&1; then
  echo "==> installing GitHub CLI (brew)"
  brew install gh
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "==> login to GitHub (browser will open)"
  gh auth login --web --git-protocol https
fi

if [ ! -d .git ]; then
  echo "==> initializing git repo"
  git init -b main
fi

echo "==> committing (pre-commit hooks are skipped for this bootstrap commit;"
echo "    install them for normal development: uv run --project elt pre-commit install)"
git add -A
git -c core.hooksPath=/dev/null commit -m "Real Estate App New — Dubai analytics platform + Windows desktop packaging" || true

echo "==> creating $VISIBILITY repo $REPO_NAME"
gh repo create "$REPO_NAME" "$VISIBILITY" --source . --push || \
  git push -u origin main

echo "==> triggering the Windows desktop build"
gh workflow run build-desktop.yml

echo
echo "Done. Watch the build at:"
gh repo view --json url -q .url | sed 's#$#/actions#'
echo "After it finishes: Actions → latest run → RealEstateAppNew-installer artifact."
echo
echo "To ship real data, publish the snapshot as a release (see desktop/README.md):"
echo "  gh release create data-snapshot-v1 desktop/data/dxb.db"
