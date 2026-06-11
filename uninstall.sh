#!/usr/bin/env bash
set -euo pipefail

REPO="${HPT_REPO:-tickernelz/hermes-progress-tail}"
REF="${HPT_REF:-v0.1.69}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
DRY_RUN="${HPT_DRY_RUN:-0}"
SOURCE_DIR="${HPT_SOURCE_DIR:-}"
UNINSTALL_ARGS=()
if [[ -n "${HPT_PROFILES:-}" ]]; then
  UNINSTALL_ARGS+=(--profile "$HPT_PROFILES")
fi
if [[ "${HPT_ALL_PROFILES:-0}" == "1" || "${HPT_ALL_PROFILES:-0}" == "true" ]]; then
  UNINSTALL_ARGS+=(--all-profiles)
fi
INTERACTIVE_DEFAULT=1
if [[ "$DRY_RUN" == "1" || "$DRY_RUN" == "true" || -n "${HPT_PROFILES:-}" || "${HPT_ALL_PROFILES:-0}" == "1" || "${HPT_ALL_PROFILES:-0}" == "true" ]]; then
  INTERACTIVE_DEFAULT=0
fi
INTERACTIVE="${HPT_INTERACTIVE:-$INTERACTIVE_DEFAULT}"
if [[ "$INTERACTIVE" != "0" && "$INTERACTIVE" != "false" ]]; then
  if [[ -r /dev/tty ]]; then
    UNINSTALL_ARGS+=(--interactive --prompt-input /dev/tty)
  else
    echo "warning: interactive uninstall requested but /dev/tty is unavailable; falling back to non-interactive" >&2
  fi
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required" >&2
  exit 1
fi

TMP_DIR=""
cleanup() {
  if [[ -n "$TMP_DIR" ]]; then
    rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT

if [[ -n "$SOURCE_DIR" ]]; then
  SRC_DIR="$SOURCE_DIR"
else
  if command -v curl >/dev/null 2>&1; then
    FETCH=(curl -fsSL)
  elif command -v wget >/dev/null 2>&1; then
    FETCH=(wget -qO-)
  else
    echo "error: curl or wget is required" >&2
    exit 1
  fi

  TMP_DIR="$(mktemp -d)"
  ARCHIVE_URL="https://github.com/${REPO}/archive/${REF}.tar.gz"
  echo "Downloading hermes-progress-tail from ${REPO}@${REF}"
  "${FETCH[@]}" "$ARCHIVE_URL" | tar -xz -C "$TMP_DIR"

  SRC_DIR="$(find "$TMP_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
fi

if [[ -z "$SRC_DIR" || ! -d "$SRC_DIR" ]]; then
  echo "error: source directory not found" >&2
  exit 1
fi

ARGS=(uninstall --hermes-home "$HERMES_HOME")
ARGS+=("${UNINSTALL_ARGS[@]}")
if [[ "$DRY_RUN" == "1" || "$DRY_RUN" == "true" ]]; then
  ARGS+=(--dry-run)
fi

(cd "$SRC_DIR" && python3 -m hermes_progress_tail.installer "${ARGS[@]}")
echo "Restart Hermes manually after uninstall: /restart"
