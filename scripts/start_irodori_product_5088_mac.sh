#!/usr/bin/env bash
# Attested local-only Irodori product engine on the fixed product port :5088.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/env_mac.sh"
# shellcheck source=lib/lumina_startup_safety_mac.sh
source "$SCRIPT_DIR/lib/lumina_startup_safety_mac.sh"

HOST=127.0.0.1
PORT=5088
BASE_URL="http://${HOST}:${PORT}"
SESSION=ai_companion_irodori_tts
SERVICE_ID=lumina-product-irodori-5088
SERVER_REVISION=2026-08-20-qwen35-ref-latent-v1
HF_CHECKPOINT=Aratako/Irodori-TTS-v4-Small-Quantized/int8-weight-only
IRODORI_SRC="${IRODORI_TTS_SERVER_DIR:-$ROOT/tools/Irodori-TTS-Server}"
IRODORI_VENV="${IRODORI_TTS_VENV:-$HOME/Library/Application Support/Lumina/irodori_tts_server/.venv}"
IRODORI_HF_HOME="${IRODORI_HF_HOME:-$HOME/Library/Application Support/Lumina/irodori_hf_home}"
SANDBOX_PROFILE="$ROOT/config/irodori_local_only.sb"
LOG_FILE="$ROOT/logs/irodori_tts_server.mac.log"
START_LOCK="$ROOT/logs/.start_irodori_product_5088.lock"
MODEL_DEVICE="${IRODORI_MODEL_DEVICE:-mps}"
CODEC_DEVICE="${IRODORI_CODEC_DEVICE:-mps}"
MODEL_PRECISION="${IRODORI_MODEL_PRECISION:-fp32}"
CODEC_PRECISION="${IRODORI_CODEC_PRECISION:-fp32}"
EMPTY_CACHE_INTERVAL="${IRODORI_EMPTY_CACHE_INTERVAL:-1}"
VISUAL_PHASE_SERIALIZATION="${LUMINA_VISUAL_PHASE_SERIALIZATION:-0}"
VISUAL_RESOURCE_EPOCH="${LUMINA_VISUAL_RESOURCE_EPOCH:-0}"
TTS_META_LOAD="${LUMINA_TTS_META_LOAD:-0}"
TTS_SERIAL_CFG="${LUMINA_TTS_SERIAL_CFG:-0}"
TTS_MEMORY_DIAGNOSTICS=0
[[ "${LUMINA_TTS_MEMORY_DIAGNOSTICS:-0}" == "1" ]] && TTS_MEMORY_DIAGNOSTICS=1
VISUAL_PHASE_LOCK_PATH="$ROOT/logs/visual_heavy_phase.lock"
PRODUCT_VOICE_ID=tsukuyomi
PRODUCT_VOICES_JSON="$IRODORI_SRC/voices/voices.json"
PRODUCT_REF_LATENT="$IRODORI_SRC/voices/tsukuyomi_ref_latent_v1.pt"
PRODUCT_VOICE_MANIFEST="$IRODORI_SRC/voices/tsukuyomi_ref_latent_v1.local.json"
PRODUCT_VOICES_JSON_SHA256=9fe0fd04f17964b861859efc42900c60bccdf317e57da2a0c40800981d136137
PRODUCT_REF_LATENT_SHA256=d0cbc72f03acaf1450f1d60d7e501f7a0053896307f902ad3fd86bb7f16136a5
PRODUCT_VOICE_MANIFEST_SHA256=f0b2c5e475cd40abd0ec21514d145bbfb544ba6c04831580d691154702950550

verify_product_voice_asset() {
  local label="$1" path="$2" expected_sha="$3" actual_sha
  [[ -f "$path" && ! -L "$path" ]] || {
    echo "[irodori-5088] product voice $label is missing/non-regular: $path" >&2
    return 1
  }
  actual_sha="$(shasum -a 256 "$path" | awk '{print $1}')"
  [[ "$actual_sha" == "$expected_sha" ]] || {
    echo "[irodori-5088] product voice $label SHA mismatch: $actual_sha" >&2
    return 1
  }
}

[[ "$MODEL_DEVICE" == "mps" && "$CODEC_DEVICE" == "mps" ]] \
  || { echo "[irodori-5088] product profile requires MPS model+codec" >&2; exit 2; }
[[ "$MODEL_PRECISION" == "fp32" && "$CODEC_PRECISION" == "fp32" ]] \
  || { echo "[irodori-5088] product profile requires supported fp32 precision" >&2; exit 2; }
[[ "$EMPTY_CACHE_INTERVAL" == "1" ]] \
  || { echo "[irodori-5088] product profile requires empty-cache interval=1" >&2; exit 2; }
[[ "$VISUAL_PHASE_SERIALIZATION" == "0" || "$VISUAL_PHASE_SERIALIZATION" == "1" ]] \
  || { echo "[irodori-5088] phase serialization flag must be 0 or 1" >&2; exit 2; }
[[ "$VISUAL_RESOURCE_EPOCH" == "0" || "$VISUAL_RESOURCE_EPOCH" == "1" ]] \
  || { echo "[irodori-5088] resource epoch flag must be 0 or 1" >&2; exit 2; }
[[ "$VISUAL_RESOURCE_EPOCH" == "0" || "$VISUAL_PHASE_SERIALIZATION" == "1" ]] \
  || { echo "[irodori-5088] resource epoch requires phase serialization" >&2; exit 2; }
[[ "$TTS_META_LOAD" == "0" || "$TTS_META_LOAD" == "1" ]] \
  || { echo "[irodori-5088] meta load flag must be 0 or 1" >&2; exit 2; }
[[ "$TTS_META_LOAD" == "0" || "$VISUAL_PHASE_SERIALIZATION" == "1" ]] \
  || { echo "[irodori-5088] meta load requires phase serialization" >&2; exit 2; }
[[ "$TTS_SERIAL_CFG" == "0" || "$TTS_SERIAL_CFG" == "1" ]] \
  || { echo "[irodori-5088] serial CFG flag must be 0 or 1" >&2; exit 2; }
[[ "$TTS_SERIAL_CFG" == "0" || "$VISUAL_PHASE_SERIALIZATION" == "1" ]] \
  || { echo "[irodori-5088] serial CFG requires phase serialization" >&2; exit 2; }
[[ "$IRODORI_SRC" == "$ROOT/tools/Irodori-TTS-Server" ]] \
  || { echo "[irodori-5088] unexpected source path: $IRODORI_SRC" >&2; exit 2; }
[[ -x "$IRODORI_VENV/bin/python" && -d "$IRODORI_SRC/src/irodori_openai_tts" ]] \
  || { echo "[irodori-5088] local runtime/source missing" >&2; exit 1; }
[[ -x /usr/bin/sandbox-exec && -f "$SANDBOX_PROFILE" ]] \
  || { echo "[irodori-5088] local-only sandbox unavailable" >&2; exit 1; }
/usr/bin/sandbox-exec -f "$SANDBOX_PROFILE" /usr/bin/true >/dev/null 2>&1 \
  || { echo "[irodori-5088] local-only sandbox invalid" >&2; exit 1; }
command -v screen >/dev/null 2>&1 \
  || { echo "[irodori-5088] screen is required" >&2; exit 1; }
command -v shasum >/dev/null 2>&1 \
  || { echo "[irodori-5088] shasum is required" >&2; exit 1; }
verify_product_voice_asset voices_json "$PRODUCT_VOICES_JSON" "$PRODUCT_VOICES_JSON_SHA256"
verify_product_voice_asset ref_latent "$PRODUCT_REF_LATENT" "$PRODUCT_REF_LATENT_SHA256"
verify_product_voice_asset manifest "$PRODUCT_VOICE_MANIFEST" "$PRODUCT_VOICE_MANIFEST_SHA256"
PRODUCT_VOICES_JSON="$PRODUCT_VOICES_JSON" \
PRODUCT_REF_LATENT="$PRODUCT_REF_LATENT" \
PRODUCT_VOICE_MANIFEST="$PRODUCT_VOICE_MANIFEST" \
PRODUCT_VOICE_ID="$PRODUCT_VOICE_ID" \
PRODUCT_REF_LATENT_SHA256="$PRODUCT_REF_LATENT_SHA256" \
"$PYTHON_BIN" - <<'PY'
import hashlib
import json
import os
from pathlib import Path

voices = json.loads(Path(os.environ["PRODUCT_VOICES_JSON"]).read_text(encoding="utf-8"))
manifest = json.loads(Path(os.environ["PRODUCT_VOICE_MANIFEST"]).read_text(encoding="utf-8"))
voice_id = os.environ["PRODUCT_VOICE_ID"]
spec = voices.get(voice_id) if isinstance(voices, dict) else None
expected_name = Path(os.environ["PRODUCT_REF_LATENT"]).name
if not isinstance(spec, dict) or spec.get("ref_latent") != expected_name or "ref_wav" in spec:
    raise SystemExit("product voice alias is not latent-only")
if manifest.get("local_only") is not True or manifest.get("redistribution_allowed") is not False:
    raise SystemExit("product voice manifest policy mismatch")
if (manifest.get("latent") or {}).get("file_sha256") != os.environ["PRODUCT_REF_LATENT_SHA256"]:
    raise SystemExit("product voice manifest latent mismatch")
PY

mkdir -p "$(dirname "$LOG_FILE")"
lumina_acquire_start_lock "$START_LOCK" "start_irodori_product_5088_mac.sh" 150
STARTED_SCREEN=0
STARTED_LISTENER_PID=""
STARTED_LISTENER_START=""
RUN_SUCCESS=0

irodori_pid_owned() {
  local pid="$1"
  lumina_pid_matches_command "$pid" "irodori_openai_tts" "--port 5088" \
    && lumina_pid_matches_cwd "$pid" "$IRODORI_SRC"
}

irodori_listener_attested() {
  local pid="" count=0
  for pid in $(lumina_listener_pids "$PORT"); do
    count=$((count + 1))
    irodori_pid_owned "$pid" || return 1
  done
  [[ "$count" -gt 0 ]]
}

stop_irodori_pid() {
  local pid="$1" started=""
  irodori_pid_owned "$pid" || return 2
  started="$(lumina_process_start "$pid")"
  lumina_pid_matches_identity "$pid" "$started" "irodori_openai_tts" "--port 5088" \
    && lumina_pid_matches_cwd "$pid" "$IRODORI_SRC" \
    || { echo "[irodori-5088] pid identity changed: $pid" >&2; return 1; }
  kill -TERM "$pid" 2>/dev/null || true
  lumina_wait_pid_exit "$pid" 40 \
    || { echo "[irodori-5088] owned pid ignored TERM: $pid" >&2; return 1; }
}

record_started_listener() {
  local pid=""
  [[ "$STARTED_SCREEN" == "1" && -z "$STARTED_LISTENER_PID" ]] || return 0
  for pid in $(lumina_listener_pids "$PORT"); do
    if irodori_pid_owned "$pid"; then
      STARTED_LISTENER_PID="$pid"
      STARTED_LISTENER_START="$(lumina_process_start "$pid")"
      return 0
    fi
  done
}

cleanup_failed_irodori_start() {
  local status=$?
  if [[ "$status" -ne 0 && "$RUN_SUCCESS" != "1" ]]; then
    if [[ "$STARTED_SCREEN" == "1" ]]; then
      lumina_stop_exact_screen_session "$SESSION" "irodori-5088-cleanup" \
        "$IRODORI_SRC" "irodori_openai_tts" "--port" "5088" || true
    fi
    record_started_listener
    if [[ -n "$STARTED_LISTENER_PID" ]] \
      && lumina_pid_matches_identity "$STARTED_LISTENER_PID" "$STARTED_LISTENER_START" \
        "irodori_openai_tts" "--port 5088" \
      && lumina_pid_matches_cwd "$STARTED_LISTENER_PID" "$IRODORI_SRC"; then
      kill -TERM "$STARTED_LISTENER_PID" 2>/dev/null || true
      lumina_wait_pid_exit "$STARTED_LISTENER_PID" 40 || true
    fi
  fi
  lumina_release_start_lock
  return "$status"
}

trap cleanup_failed_irodori_start EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

health_matches() {
  local payload
  payload="$(curl --noproxy '*' -fsS --max-time 4 "$BASE_URL/health" 2>/dev/null || true)"
  [[ -n "$payload" ]] || return 1
  IRODORI_HEALTH_PAYLOAD="$payload" \
  IRODORI_EXPECTED_SERVICE_ID="$SERVICE_ID" \
  IRODORI_EXPECTED_SERVER_REVISION="$SERVER_REVISION" \
  IRODORI_EXPECTED_HF_CHECKPOINT="$HF_CHECKPOINT" \
  IRODORI_EXPECTED_VOICE_ID="$PRODUCT_VOICE_ID" \
  IRODORI_EXPECTED_VOICES_JSON_SHA256="$PRODUCT_VOICES_JSON_SHA256" \
  IRODORI_EXPECTED_REF_LATENT_SHA256="$PRODUCT_REF_LATENT_SHA256" \
  IRODORI_EXPECTED_PHASE_SERIALIZATION="$VISUAL_PHASE_SERIALIZATION" \
  IRODORI_EXPECTED_RESOURCE_EPOCH="$VISUAL_RESOURCE_EPOCH" \
  IRODORI_EXPECTED_MEMORY_DIAGNOSTICS="$TTS_MEMORY_DIAGNOSTICS" \
  IRODORI_EXPECTED_META_LOAD="$TTS_META_LOAD" \
  IRODORI_EXPECTED_SERIAL_CFG="$TTS_SERIAL_CFG" \
  IRODORI_EXPECTED_PHASE_LOCK_PATH="$VISUAL_PHASE_LOCK_PATH" \
  "$PYTHON_BIN" - <<'PY'
import json, os
try:
    p = json.loads(os.environ.get("IRODORI_HEALTH_PAYLOAD", "{}"))
except json.JSONDecodeError:
    raise SystemExit(1)
m = p.get("model") or {}
r = p.get("runtime") or {}
v = p.get("product_voice") or {}
ok = (
    p.get("status") == "ok"
    and p.get("service") == os.environ["IRODORI_EXPECTED_SERVICE_ID"]
    and p.get("server_revision") == os.environ["IRODORI_EXPECTED_SERVER_REVISION"]
    and p.get("owned") is True
    and p.get("offline") is True
    and m.get("model_device") == "mps"
    and m.get("codec_device") == "mps"
    and m.get("hf_checkpoint") == os.environ["IRODORI_EXPECTED_HF_CHECKPOINT"]
    and m.get("model_precision") == "fp32"
    and m.get("codec_precision") == "fp32"
    and r.get("empty_cache_interval") == 1
    and v.get("id") == os.environ["IRODORI_EXPECTED_VOICE_ID"]
    and v.get("voices_json_sha256") == os.environ["IRODORI_EXPECTED_VOICES_JSON_SHA256"]
    and v.get("ref_latent_sha256") == os.environ["IRODORI_EXPECTED_REF_LATENT_SHA256"]
)
if os.environ["IRODORI_EXPECTED_PHASE_SERIALIZATION"] == "1":
    phase = p.get("phase_serialization") or {}
    ok = (ok and phase.get("enabled") is True
          and phase.get("lock_path") == os.environ["IRODORI_EXPECTED_PHASE_LOCK_PATH"]
          and phase.get("idle") is True and phase.get("blocked_reason") is None
          and phase.get("outstanding_workers") == 0
          and phase.get("waiting_workers") == 0 and phase.get("active_workers") == 0
          and phase.get("max_chunk_chars") == 16
          and phase.get("chunking_policy") == "punctuation_first_hard_cap"
          and phase.get("chunk_resource_checkpoint") is True
          and phase.get("idle_residency_policy") == "unload_after_worker"
          and p.get("cold_on_demand") is True and p.get("available") is True
          and p.get("ready") is False
          and r.get("loaded") is False and r.get("loading") is False
          and r.get("unloading") is False and r.get("residency_state") == "cold_on_demand"
          and "unload_error" in r and r["unload_error"] is None)
    if os.environ["IRODORI_EXPECTED_RESOURCE_EPOCH"] == "1":
        shared = phase.get("shared_epoch") or {}
        state = shared.get("state") or {}
        ok = (ok and phase.get("enforcement_revision") == "shared_epoch_authoritative_local_telemetry_v2"
              and phase.get("enforcement_scope") == "shared_exhibition_epoch"
              and phase.get("local_lifetime_measurement_only") is True
              and phase.get("start_memory_recovery_revision") == "shared_epoch_bounded_start_memory_recovery_v1"
              and phase.get("start_memory_recovery_enabled") is True
              and phase.get("start_memory_recovery_wait_seconds") == 60.0
              and phase.get("start_memory_recovery_poll_seconds") == 1.0
              and shared.get("enabled") is True and "error" in shared and shared["error"] is None
              and state.get("baseline_scope") == "shared_exhibition_epoch"
              and isinstance(state.get("epoch_id"), str) and bool(state["epoch_id"])
              and "blocked_reason" in state and state["blocked_reason"] is None
              and state.get("minimum_free_percent") == 22 and state.get("maximum_swap_growth_mib") == 256
              and r.get("checkpoint_release_revision") == "cpu_state_early_release_v1")
    if os.environ["IRODORI_EXPECTED_MEMORY_DIAGNOSTICS"] == "1":
        ok = (ok and r.get("memory_diagnostics") is True
              and r.get("factory_memory_revision") == "factory_stages_v1")
    if os.environ["IRODORI_EXPECTED_META_LOAD"] == "1":
        ok = (ok and r.get("meta_model_init") is True
              and r.get("meta_load_revision") == "meta_assign_modernbert_v1")
    if os.environ["IRODORI_EXPECTED_SERIAL_CFG"] == "1":
        ok = (ok and r.get("serial_cfg") is True
              and r.get("serial_cfg_revision") == "independent_cfg_shared_kv_v2")
if os.environ["IRODORI_EXPECTED_META_LOAD"] != "1" and r.get("meta_model_init") is True:
    ok = False  # Do not reuse an opt-in engine when its flag was not requested.
if os.environ["IRODORI_EXPECTED_SERIAL_CFG"] != "1" and r.get("serial_cfg") is True:
    ok = False
raise SystemExit(0 if ok else 1)
PY
}

if health_matches && irodori_listener_attested; then
  echo "[irodori-5088] attested engine already ready"
  RUN_SUCCESS=1
  exit 0
fi

# Opt-in must not replace an old, busy, or mismatched owned engine implicitly.
# Its coordinated reload is a separate explicitly owned operation.
if [[ "$VISUAL_PHASE_SERIALIZATION" == "1" ]] \
  && [[ -n "$(lumina_listener_pids "$PORT")" ]]; then
  echo "[irodori-5088] phase profile mismatch; refusing implicit engine replacement" >&2
  exit 1
fi

# Replace only an exact local Irodori listener.  Refuse an unrelated owner.
while IFS= read -r pid; do
  [[ -n "$pid" ]] || continue
  if ! irodori_pid_owned "$pid"; then
    echo "[irodori-5088] refusing unrelated listener pid=$pid" >&2
    exit 1
  fi
done < <(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true)

if lumina_screen_session_exists "$SESSION"; then
  lumina_stop_exact_screen_session "$SESSION" "irodori-5088" \
    "$IRODORI_SRC" "irodori_openai_tts" "--port" "5088" || exit 1
fi
while IFS= read -r pid; do
  [[ -n "$pid" ]] || continue
  stop_irodori_pid "$pid" || exit 1
done < <(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true)
if ! lumina_wait_port_closed "$PORT" 40; then
  echo "[irodori-5088] listener did not stop" >&2
  exit 1
fi

mkdir -p "$IRODORI_HF_HOME"
RUN_WRAP=$(cat <<EOF
export COPYFILE_DISABLE=1
export HF_HOME="$IRODORI_HF_HOME"
export HUGGINGFACE_HUB_CACHE="$IRODORI_HF_HOME/hub"
export TRANSFORMERS_CACHE="$IRODORI_HF_HOME/transformers"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export IRODORI_OFFLINE=true
export IRODORI_SERVICE_ID="$SERVICE_ID"
export IRODORI_SERVER_REVISION="$SERVER_REVISION"
export IRODORI_OWNED=true
export IRODORI_HF_CHECKPOINT="$HF_CHECKPOINT"
export IRODORI_MODEL_DEVICE="$MODEL_DEVICE"
export IRODORI_CODEC_DEVICE="$CODEC_DEVICE"
export IRODORI_MODEL_PRECISION="$MODEL_PRECISION"
export IRODORI_CODEC_PRECISION="$CODEC_PRECISION"
export IRODORI_EMPTY_CACHE_INTERVAL="$EMPTY_CACHE_INTERVAL"
export LUMINA_VISUAL_PHASE_SERIALIZATION="$VISUAL_PHASE_SERIALIZATION"
export LUMINA_VISUAL_RESOURCE_EPOCH="$VISUAL_RESOURCE_EPOCH"
export LUMINA_TTS_MEMORY_DIAGNOSTICS="$TTS_MEMORY_DIAGNOSTICS"
export LUMINA_TTS_META_LOAD="$TTS_META_LOAD"
export LUMINA_TTS_SERIAL_CFG="$TTS_SERIAL_CFG"
export PYTHONPATH="$ROOT"
export IRODORI_PRODUCT_VOICE_ID="$PRODUCT_VOICE_ID"
export IRODORI_PRODUCT_VOICES_JSON_SHA256="$PRODUCT_VOICES_JSON_SHA256"
export IRODORI_PRODUCT_REF_LATENT_SHA256="$PRODUCT_REF_LATENT_SHA256"
export NO_PROXY=127.0.0.1,localhost,::1
export no_proxy=127.0.0.1,localhost,::1
export HF_HUB_DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
unset DISCORD_BOT_TOKEN DISCORD_TOKEN KARAKURI_DISCORD_BOT_TOKEN KARAKURI_API_KEY KARAKURI_WEBHOOK_SECRET
cd "$IRODORI_SRC"
exec "$IRODORI_VENV/bin/python" -m irodori_openai_tts --host "$HOST" --port "$PORT" >> "$LOG_FILE" 2>&1
EOF
)
if lumina_screen_session_exists "$SESSION"; then
  echo "[irodori-5088] refusing duplicate screen session=$SESSION" >&2
  exit 1
fi
/usr/bin/env -i \
  HOME="$HOME" USER="$(id -un)" SHELL=/bin/bash \
  PATH=/usr/bin:/bin:/usr/sbin:/sbin TMPDIR="${TMPDIR:-/tmp}" TERM="${TERM:-xterm-256color}" \
  /usr/bin/screen -dmS "$SESSION" \
    /usr/bin/sandbox-exec -f "$SANDBOX_PROFILE" /bin/bash -c "$RUN_WRAP"
STARTED_SCREEN=1

for _ in $(seq 1 120); do
  record_started_listener
  if health_matches && irodori_listener_attested; then
    echo "[irodori-5088] ready local-only cache_interval=$EMPTY_CACHE_INTERVAL"
    RUN_SUCCESS=1
    exit 0
  fi
  sleep 1
done

echo "[irodori-5088] failed to become attested; see $LOG_FILE" >&2
exit 1
