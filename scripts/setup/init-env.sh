#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_EXAMPLE="${ROOT_DIR}/.env.example"
ENV_FILE="${ROOT_DIR}/.env"

force=false
openai_key=""
opik_url=""
enable_opik=""

usage() {
  cat <<'USAGE'
Usage:
  scripts/setup/init-env.sh [options]

Options:
  --force                    Overwrite existing .env without prompt
  --openai-key <key>         Set OPENAI_API_KEY directly
  --enable-opik              Enable Opik with default URL
  --opik-url <url>           Set OPIK_URL explicitly
  -h, --help                 Show this help
USAGE
}

log() {
  printf '[setup-env] %s\n' "$*"
}

set_env_key() {
  local key="$1"
  local value="$2"
  local tmp
  tmp="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { done = 0 }
    $0 ~ "^" key "=" {
      print key "=" value
      done = 1
      next
    }
    { print }
    END {
      if (done == 0) {
        print key "=" value
      }
    }
  ' "${ENV_FILE}" > "${tmp}"
  mv "${tmp}" "${ENV_FILE}"
}

prompt_yes_no() {
  local question="$1"
  local default_value="$2"
  local answer
  local suffix="[y/N]"
  if [[ "${default_value}" == "y" ]]; then
    suffix="[Y/n]"
  fi

  read -r -p "${question} ${suffix} " answer
  answer="$(echo "${answer}" | tr '[:upper:]' '[:lower:]')"

  if [[ -z "${answer}" ]]; then
    answer="${default_value}"
  fi

  [[ "${answer}" == "y" || "${answer}" == "yes" ]]
}

prompt_with_default() {
  local question="$1"
  local default_value="$2"
  local answer
  read -r -p "${question} [${default_value}] " answer
  if [[ -z "${answer}" ]]; then
    printf '%s' "${default_value}"
  else
    printf '%s' "${answer}"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)
      force=true
      shift
      ;;
    --openai-key)
      openai_key="$2"
      shift 2
      ;;
    --enable-opik)
      enable_opik="y"
      shift
      ;;
    --opik-url)
      opik_url="$2"
      enable_opik="y"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ ! -f "${ENV_EXAMPLE}" ]]; then
  printf 'Missing .env.example at %s\n' "${ENV_EXAMPLE}" >&2
  exit 1
fi

if [[ -f "${ENV_FILE}" && "${force}" != "true" ]]; then
  if ! prompt_yes_no ".env already exists. Overwrite it?" "n"; then
    log "aborted"
    exit 0
  fi
fi

cp "${ENV_EXAMPLE}" "${ENV_FILE}"
log "copied .env.example -> .env"

if [[ -z "${openai_key}" ]]; then
  read -r -p "Enter OPENAI_API_KEY (leave blank to keep example value): " openai_key
fi
if [[ -n "${openai_key}" ]]; then
  set_env_key "OPENAI_API_KEY" "${openai_key}"
fi

if [[ -z "${enable_opik}" ]]; then
  if prompt_yes_no "Enable Opik by default?" "n"; then
    enable_opik="y"
  else
    enable_opik="n"
  fi
fi

if [[ "${enable_opik}" == "y" ]]; then
  if [[ -z "${opik_url}" ]]; then
    opik_url="$(prompt_with_default "Opik URL" "http://opik-backend:8080")"
  fi
  set_env_key "OPIK_URL" "${opik_url}"
else
  set_env_key "OPIK_URL" ""
fi

if prompt_yes_no "Customize service ports now?" "n"; then
  neo4j_http_port="$(prompt_with_default "NEO4J_HTTP_PORT" "7474")"
  neo4j_bolt_port="$(prompt_with_default "NEO4J_BOLT_PORT" "7687")"
  extraction_api_port="$(prompt_with_default "EXTRACTION_API_PORT" "8001")"
  chat_port="$(prompt_with_default "CHAT_INTERFACE_PORT" "8501")"

  set_env_key "NEO4J_HTTP_PORT" "${neo4j_http_port}"
  set_env_key "NEO4J_BOLT_PORT" "${neo4j_bolt_port}"
  set_env_key "EXTRACTION_API_PORT" "${extraction_api_port}"
  set_env_key "CHAT_INTERFACE_PORT" "${chat_port}"
fi

log "done: ${ENV_FILE}"
log "next: make up"
