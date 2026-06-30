#!/usr/bin/env bash
set -euo pipefail

MODEL="${MODEL:-qwen3:8b}"
OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"

# Make the NVIDIA GPU visible to Ollama. Ollama will offload as many layers as
# the available VRAM allows.
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export OLLAMA_HOST
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:--1m}"
export OLLAMA_FLASH_ATTENTION="${OLLAMA_FLASH_ATTENTION:-1}"

find_ollama() {
  if command -v ollama >/dev/null 2>&1; then
    command -v ollama
    return
  fi

  local win_userprofile="${USERPROFILE:-}"
  if [[ -z "$win_userprofile" ]] && command -v powershell.exe >/dev/null 2>&1; then
    win_userprofile="$(powershell.exe -NoProfile -Command '$env:USERPROFILE' | tr -d '\r')"
  fi

  if [[ -n "$win_userprofile" ]]; then
    local candidate="${win_userprofile}\\AppData\\Local\\Programs\\Ollama\\ollama.exe"
    if command -v cygpath >/dev/null 2>&1; then
      candidate="$(cygpath -u "$candidate")"
    fi

    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return
    fi
  fi

  printf 'ollama executable not found\n' >&2
  exit 1
}

OLLAMA_BIN="$(find_ollama)"

if ! curl -fsS "http://${OLLAMA_HOST}/api/version" >/dev/null 2>&1; then
  echo "Starting Ollama on ${OLLAMA_HOST} with CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES}..."
  nohup "$OLLAMA_BIN" serve >/tmp/ollama-serve.log 2>&1 &

  for _ in {1..60}; do
    if curl -fsS "http://${OLLAMA_HOST}/api/version" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi

curl -fsS "http://${OLLAMA_HOST}/api/version" >/dev/null

echo "Ensuring ${MODEL} is installed..."
"$OLLAMA_BIN" pull "$MODEL"

echo "Loading ${MODEL} and keeping it resident..."
curl -fsS "http://${OLLAMA_HOST}/api/generate" \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"${MODEL}\",\"prompt\":\"Reply with OK only.\",\"stream\":false,\"keep_alive\":\"${OLLAMA_KEEP_ALIVE}\"}" \
  >/dev/null

echo
"$OLLAMA_BIN" ps

if command -v nvidia-smi >/dev/null 2>&1; then
  echo
  nvidia-smi
fi
