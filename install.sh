#!/usr/bin/env bash
set -euo pipefail

REPO="${HPT_REPO:-tickernelz/hermes-progress-tail}"
REF="${HPT_REF:-v0.2.02}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
DRY_RUN="${HPT_DRY_RUN:-0}"
SOURCE_DIR="${HPT_SOURCE_DIR:-}"
INSTALL_ARGS=()
if [[ -n "${HPT_PROFILES:-}" ]]; then
  INSTALL_ARGS+=(--profile "$HPT_PROFILES")
fi
if [[ "${HPT_ALL_PROFILES:-0}" == "1" || "${HPT_ALL_PROFILES:-0}" == "true" ]]; then
  INSTALL_ARGS+=(--all-profiles)
fi
if [[ "${HPT_TELEGRAM_FLOOD_SAFE:-0}" == "1" || "${HPT_TELEGRAM_FLOOD_SAFE:-0}" == "true" ]]; then
  INSTALL_ARGS+=(--telegram-flood-safe)
fi
INTERACTIVE_DEFAULT=1
if [[ "$DRY_RUN" == "1" || "$DRY_RUN" == "true" || -n "${HPT_PROFILES:-}" || "${HPT_ALL_PROFILES:-0}" == "1" || "${HPT_ALL_PROFILES:-0}" == "true" ]]; then
  INTERACTIVE_DEFAULT=0
fi
INTERACTIVE="${HPT_INTERACTIVE:-$INTERACTIVE_DEFAULT}"
if [[ "$INTERACTIVE" != "0" && "$INTERACTIVE" != "false" ]]; then
  if [[ -r /dev/tty ]]; then
    INSTALL_ARGS+=(--interactive --prompt-input /dev/tty)
  else
    echo "warning: interactive install requested but /dev/tty is unavailable; falling back to non-interactive" >&2
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

ARGS=(install --hermes-home "$HERMES_HOME" --source-dir "$SRC_DIR" --native-gateway-suppress)
ARGS+=("${INSTALL_ARGS[@]}")
if [[ "$DRY_RUN" == "1" || "$DRY_RUN" == "true" ]]; then
  ARGS+=(--dry-run)
fi

(cd "$SRC_DIR" && python3 -m hermes_progress_tail.installer "${ARGS[@]}")
echo "Restart Hermes manually after install: /restart"
