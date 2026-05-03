#!/usr/bin/env bash
set -euo pipefail

REPO="${HPT_REPO:-tickernelz/hermes-progress-tail}"
REF="${HPT_REF:-main}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
DRY_RUN="${HPT_DRY_RUN:-0}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required" >&2
  exit 1
fi

if command -v curl >/dev/null 2>&1; then
  FETCH=(curl -fsSL)
elif command -v wget >/dev/null 2>&1; then
  FETCH=(wget -qO-)
else
  echo "error: curl or wget is required" >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

ARCHIVE_URL="https://github.com/${REPO}/archive/${REF}.tar.gz"
echo "Downloading hermes-progress-tail from ${REPO}@${REF}"
"${FETCH[@]}" "$ARCHIVE_URL" | tar -xz -C "$TMP_DIR"

SRC_DIR="$(find "$TMP_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [[ -z "$SRC_DIR" ]]; then
  echo "error: downloaded archive did not contain a source directory" >&2
  exit 1
fi

ARGS=(install --hermes-home "$HERMES_HOME" --source-dir "$SRC_DIR" --set-display-off)
if [[ "$DRY_RUN" == "1" || "$DRY_RUN" == "true" ]]; then
  ARGS+=(--dry-run)
fi

(cd "$SRC_DIR" && python3 -m hermes_progress_tail.installer "${ARGS[@]}")
echo "Restart Hermes manually after install: /restart"
