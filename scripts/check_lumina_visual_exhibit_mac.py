#!/usr/bin/env python3
"""Read-only candidate/idle attestation. Never sends commands or changes services."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import re
import shlex
import subprocess
import stat
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PHASE_LOCK = ROOT / "logs/visual_heavy_phase.lock"
EPOCH_PATH = ROOT / "logs/visual_resource_epoch.json"
WIP15_SCENE = "res://assets/avatars/lumina_stella_wip_20260906_wip15/export/stella_lumina_candidate_wip15.vrm"
SERVICES = {
    11436: ("qwen_llama", "qwen35_4b_server_owner.json", "ai_companion_llama_cpp"),
    8790: ("lumina_next", "lumina_next_product_owner.json", "lumina_next_orchestrator"),
    8765: ("godot_bridge", "godot_bridge_product_owner.json", "ai_companion_godot_bridge"),
}
TTS_SERVICES = {
    5088: ("irodori_5088", "ai_companion_irodori_tts"),
    5055: ("irodori_adapter", "ai_companion_piper_tts"),
    5056: ("tts_gateway", "ai_companion_tts_gateway"),
}
VOICE = {"id": "tsukuyomi", "voices_json_sha256": "9fe0fd04f17964b861859efc42900c60bccdf317e57da2a0c40800981d136137", "ref_latent_sha256": "d0cbc72f03acaf1450f1d60d7e501f7a0053896307f902ad3fd86bb7f16136a5"}
QWEN_LOCAL_DIR = Path('/LOCAL_USER_NOT_INCLUDED/Documents/Codex/2026-09-04/lu/work/lumina_completion/runtime_models/qwen35_4b_pinned_v1')
QWEN_ASSET_LOCATION = {"schema": "lumina-qwen35-4b-asset-location-v1",
                       "mode": "workspace_pinned_v1", "directory": str(QWEN_LOCAL_DIR)}
QWEN_MODEL_NAME = 'Qwen3.5-4B-Q4_K_M.gguf'
QWEN_PROJECTOR_NAME = 'mmproj-Qwen3.5-4B-F16.gguf'
QWEN_MODEL_SIZE = 2740937888
QWEN_MODEL_SHA256 = '00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4'
QWEN_PROJECTOR_SIZE = 672423616
QWEN_PROJECTOR_SHA256 = 'cd88edcf8d031894960bb0c9c5b9b7e1fea6ebee02b9f7ce925a00d12891f864'
PORTABLE_SELECTION_POLICY_REVISION = "portable_open_vocab_explicit_single_target_speech_act_v11"
PORTABLE_GROUNDING_POLICY_REVISION = "rgbd_bbox_surface_partition_occluder_bilateral_role_continuity_v7"
SURFACE_ID_SEMANTICS_REVISION = "single_connected_component_world_pose_epoch_v2"
DEFAULT_CAPTURE_DIRECTORY = Path("/private/tmp/lumina_visual_exhibit_eye_capture")


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def portable_selection_policy_ready(navigation):
    """Attest the policy selected by the exhibit's mandatory portable mode."""
    return (
        isinstance(navigation, dict)
        and navigation.get("portable_navigation_enabled") is True
        and navigation.get("selection_policy_revision")
        == PORTABLE_SELECTION_POLICY_REVISION
        and navigation.get("grounding_policy_revision")
        == PORTABLE_GROUNDING_POLICY_REVISION
    )


def command(*args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=5)
    require(result.returncode == 0, "process inspection failed: " + args[0])
    return result.stdout.strip()


def get(port, path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}{path}", timeout=4) as response:
        data = response.read(2_000_001)
    require(len(data) <= 2_000_000, "oversized status response")
    return json.loads(data)


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def qwen_asset_paths(local=False):
    directory = QWEN_LOCAL_DIR if local else ROOT / 'data/lumina_next/models/llm'
    return {"mode": 'workspace_pinned_v1' if local else 'external_original',
            "directory": str(directory), "model": str(directory / QWEN_MODEL_NAME),
            "projector": str(directory / QWEN_PROJECTOR_NAME)}


def requested_qwen_assets():
    flag = os.environ.get('LUMINA_QWEN35_LOCAL_ASSETS', '0')
    require(flag in ('0', '1'), 'LUMINA_QWEN35_LOCAL_ASSETS must be 0 or 1')
    return qwen_asset_paths(flag == '1')


def requested_qwen_microbatch():
    flag = os.environ.get('LUMINA_QWEN35_VISION_UBATCH64', '0')
    require(flag in ('0', '1'), 'LUMINA_QWEN35_VISION_UBATCH64 must be 0 or 1')
    if flag == '1':
        require(os.environ.get('LUMINA_QWEN35_VISION') == '1'
                and os.environ.get('LUMINA_QWEN35_VISION_QUALITY') == '1',
                'microbatch 64 requires explicit vision and quality flags')
    return 64 if flag == '1' else 32


def claimed_qwen_microbatch(contract):
    # Legacy owners have no field. An explicit field exists only for the
    # quality-vision 64-token candidate, never as a generic numeric override.
    if not isinstance(contract, dict) or 'microbatch_tokens' not in contract:
        return 32
    if (type(contract['microbatch_tokens']) is int and contract['microbatch_tokens'] == 64
            and contract.get('enabled') is True and contract.get('quality') is True):
        return 64
    return None


def qwen_batch_argv_matches(argv, microbatch):
    args = list(argv)
    if microbatch not in (32, 64):
        return False
    if any(arg in ('--batch-size', '--ubatch-size')
           or arg.startswith(('--batch-size=', '--ubatch-size='))
           or (arg.startswith('-b') and arg != '-b')
           or (arg.startswith('-ub') and arg != '-ub') for arg in args):
        return False
    def one(key, value):
        return args.count(key) == 1 and args.index(key) + 1 < len(args) and args[args.index(key) + 1] == str(value)
    return one('-b', 64) and one('-ub', microbatch)


def attest_qwen_assets(claim, argv):
    """Owner startup SHA attestations and exact live paths, never large file reads."""
    internal = 'asset_location' in claim
    require(not internal or claim['asset_location'] == QWEN_ASSET_LOCATION,
            'unknown model asset location claim')
    paths = qwen_asset_paths(internal)
    args = list(argv)
    def one(key, value):
        return args.count(key) == 1 and args.index(key) + 1 < len(args) and args[args.index(key) + 1] == value
    require(not any(arg == '--model' or arg.startswith(('--model=', '-m=', '--mmproj=')) for arg in args),
            'ambiguous model asset argument')
    require(one('-m', paths['model']) and claim.get('model') == paths['model'], 'model asset path mismatch')
    require(type(claim.get('model_size')) is int and claim['model_size'] == QWEN_MODEL_SIZE
            and claim.get('model_sha256') == QWEN_MODEL_SHA256, 'model startup SHA/size not attested')
    vision = claim.get('vision_contract')
    if isinstance(vision, dict) and vision.get('enabled') is True:
        require(one('--mmproj', paths['projector']) and vision.get('projector') == paths['projector'],
                'projector asset path mismatch or mixed pair')
        require(type(vision.get('projector_size')) is int and vision['projector_size'] == QWEN_PROJECTOR_SIZE
                and vision.get('projector_sha256') == QWEN_PROJECTOR_SHA256, 'projector startup SHA/size not attested')
        return dict(paths, attested=True, model_size=QWEN_MODEL_SIZE, model_sha256=QWEN_MODEL_SHA256,
                    projector_size=QWEN_PROJECTOR_SIZE, projector_sha256=QWEN_PROJECTOR_SHA256)
    require(not internal and '--mmproj' not in args, 'internal assets require attested vision pair')
    return dict(paths, attested=False)  # Known legacy text-only owner is migration-only.


def bounded_vision_contract(contract, argv, asset_location=None):
    if not isinstance(contract, dict) or type(contract.get("quality")) is not bool:
        return False
    if asset_location is not None and asset_location != QWEN_ASSET_LOCATION:
        return False
    paths = qwen_asset_paths(asset_location is not None)
    quality = contract["quality"]
    microbatch = claimed_qwen_microbatch(contract)
    if not qwen_batch_argv_matches(argv, microbatch):
        return False
    expected = {"schema": "lumina-qwen35-4b-vision-contract-v1", "enabled": True, "quality": quality,
                "server_context": 2048 if quality else 1024, "image_tokens": 1024 if quality else 256,
                "projector": paths['projector'],
                "projector_size": 672423616, "projector_sha256": "cd88edcf8d031894960bb0c9c5b9b7e1fea6ebee02b9f7ce925a00d12891f864",
                "cache_ram_mib": 0, "context_checkpoints": 0}
    if microbatch == 64:
        expected['microbatch_tokens'] = 64
    def one(key, value):
        return list(argv).count(key) == 1 and argv.index(key) + 1 < len(argv) and argv[argv.index(key) + 1] == str(value)
    if "idle_sleep_seconds" in contract:
        expected["idle_sleep_seconds"] = 3
        if not one("--sleep-idle-seconds", 3):
            return False
    elif "--sleep-idle-seconds" in argv:
        return False
    return contract == expected and all(one(key, value) for key, value in {
        "-c": expected["server_context"], "--mmproj": expected["projector"],
        "--image-min-tokens": expected["image_tokens"], "--image-max-tokens": expected["image_tokens"],
        "--cache-ram": 0, "--ctx-checkpoints": 0}.items())


def attest_owner(port, pid, reader):
    from find_lumina_residual_processes_mac import classify_process
    kind, filename, session = SERVICES[port]
    record = reader.record(pid)
    require(record and classify_process(record, ROOT) == kind, f"unknown owner on :{port}")
    require(list(record.argv).count("--host") == 1 and record.argv[record.argv.index("--host") + 1] == "127.0.0.1", "nonlocal/ambiguous bind")
    claim = json.loads((ROOT / "logs" / filename).read_text())
    schema = {11436: "lumina-qwen35-4b-server-owner-v2", 8790: "lumina-next-product-owner-v1", 8765: "lumina-godot-bridge-owner-v1"}[port]
    require(claim.get("schema") == schema, "unknown owner schema")
    signature = claim.pop("manifest_fingerprint" if port == 11436 else "fingerprint", "")
    require(signature == fingerprint(claim), f"owner fingerprint mismatch :{port}")
    require(claim.get("pid") == pid and claim.get("port") == port, "owner PID/port mismatch")
    require(claim.get("pid_lstart") == command("/bin/ps", "-p", str(pid), "-ww", "-o", "lstart="), "owner PID reused")
    if port != 11436:
        require(claim.get("runtime_root") == str(ROOT) and claim.get("python") == str(ROOT / ".venv/bin/python"), "wrong runtime claim")
    else:
        args = shlex.split(command("/bin/ps", "-p", str(pid), "-ww", "-o", "command="))
        digest = hashlib.sha256(json.dumps(args, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        require(claim.get("argv_fingerprint") == digest, "model argv changed")
    screen_pid = claim.get("screen_pid")
    if screen_pid:
        require(claim.get("screen_id") == f"{screen_pid}.{session}", "screen claim mismatch")
        require(claim.get("screen_lstart") == command("/bin/ps", "-p", str(screen_pid), "-ww", "-o", "lstart="), "screen PID reused")
        screen = reader.record(screen_pid)
        require(screen and Path(screen.executable).name.lower() == "screen" and session in screen.argv, "unknown screen owner")
    result = {"pid": pid, "pid_lstart": claim["pid_lstart"], "screen_id": claim.get("screen_id") or ""}
    if port == 11436:
        microbatch = claimed_qwen_microbatch(claim.get('vision_contract'))
        require(qwen_batch_argv_matches(record.argv, microbatch), 'model batch owner/argv contract mismatch')
        bounded = bounded_vision_contract(claim.get('vision_contract'), record.argv, claim.get('asset_location'))
        require(microbatch != 64 or bounded, 'microbatch 64 requires attested bounded quality vision')
        # Legacy owned models remain attestable for an explicit restart only.
        result.update(vision_contract=claim.get("vision_contract"),
                      bounded_vision_cache=bounded,
                      qwen_microbatch_tokens=microbatch,
                      model_sleep_enabled="--sleep-idle-seconds" in record.argv,
                      qwen_assets=attest_qwen_assets(claim, record.argv))
    return result


def attest_tts_owner(port, pid, reader):
    from find_lumina_residual_processes_mac import classify_process
    started = command("/bin/ps", "-p", str(pid), "-ww", "-o", "lstart=")
    record = reader.record(pid)
    require(record and classify_process(record, ROOT) == TTS_SERVICES[port][0], f"unknown TTS owner on :{port}")
    expected_cwd = ROOT / "tools/Irodori-TTS-Server" if port == 5088 else ROOT
    require(record.cwd == str(expected_cwd), "wrong TTS runtime directory")
    if port != 5056:
        require(list(record.argv).count("--host") == 1 and record.argv[record.argv.index("--host") + 1] == "127.0.0.1", "nonlocal/ambiguous TTS bind")
    endpoints = command("/usr/sbin/lsof", "-nP", "-a", "-p", str(pid), f"-iTCP:{port}", "-sTCP:LISTEN", "-Fn")
    require({line[1:] for line in endpoints.splitlines() if line.startswith("n")} == {f"127.0.0.1:{port}"}, "TTS listener not exclusively loopback")
    require(reader.record(pid) == record and command("/bin/ps", "-p", str(pid), "-ww", "-o", "lstart=") == started, "TTS PID changed during inspection")
    return {"pid": pid, "pid_lstart": started, "screen_id": "", "attestation": "exact_live_process"}


def attest_tts_screen(screen_id, port, owner, reader):
    screen_pid = int(screen_id.split(".", 1)[0])
    screen = reader.record(screen_pid)
    require(screen and Path(screen.executable).resolve() == Path("/usr/bin/screen").resolve() and TTS_SERVICES[port][1] in screen.argv, "unknown TTS screen owner")
    ancestor = owner["pid"]
    for _ in range(8):
        ancestor = int(command("/bin/ps", "-p", str(ancestor), "-o", "ppid="))
        if ancestor == screen_pid:
            owner["screen_id"] = screen_id
            owner["screen_lstart"] = command("/bin/ps", "-p", str(screen_pid), "-o", "lstart=")
            return
        if ancestor <= 1:
            break
    raise ValueError("TTS screen is not the listener's supervisor")


def inventory():
    from find_lumina_residual_processes_mac import MacProcessReader, classify_process
    reader, owned = MacProcessReader(), {}
    for port in (*SERVICES, *TTS_SERVICES):
        result = subprocess.run(
            ["/usr/sbin/lsof", "-nP", f"-tiTCP:{port}", "-sTCP:LISTEN"],
            text=True,
            capture_output=True,
            timeout=10,
        )
        require(result.returncode in (0, 1) and not result.stderr.strip(), "cannot inspect port ownership")
        pids = set(result.stdout.split())
        require(len(pids) <= 1, f"ambiguous listeners :{port}")
        if pids:
            owned[port] = (attest_tts_owner if port in TTS_SERVICES else attest_owner)(port, int(pids.pop()), reader)
    # Refuse loading/orphan screens before stopping any other component.
    listing = subprocess.run(["/usr/bin/screen", "-ls"], text=True, capture_output=True, timeout=5)
    require(listing.returncode in (0, 1) and not listing.stderr.strip(), "screen registry unavailable")
    screens = listing.stdout
    for port, (_, session) in TTS_SERVICES.items():
        records = [word for word in screens.split() if word.endswith("." + session)]
        require(len(records) <= 1 and (not records or port in owned), "unattested/orphan TTS screen: " + session)
        if records:
            attest_tts_screen(records[0], port, owned[port], reader)
    for port, (_, _, session) in SERVICES.items():
        records = [word for word in screens.split() if word.endswith("." + session)]
        require(len(records) <= 1 and (not records or port in owned), "unattested/orphan screen: " + session)
        expected = owned.get(port, {}).get("screen_id")
        require(records == ([expected] if expected else []), "screen registry differs from owner claim")
    godot = []
    for pid in reader.pids():
        record = reader.record(pid)
        if record and classify_process(record, ROOT) == "godot":
            require("--script" not in record.argv and "--editor" not in record.argv, "isolated/editor Godot must finish first")
            godot.append(pid)
    require(len(godot) <= 1, "multiple Godot project owners")
    return owned, godot


def phase_lease_status(path=PHASE_LOCK):
    """Never create/truncate/unlink the lease; a snapshot, not a runtime lease."""
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        require(stat.S_ISREG(os.fstat(fd).st_mode), "phase lease is not a regular file")
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return {"initialized": True, "busy": False}
    except FileNotFoundError:
        return {"initialized": False, "busy": False}
    except BlockingIOError:
        return {"initialized": True, "busy": True}
    finally:
        if fd is not None:
            os.close(fd)


def check_phase(phase, *, tts=False, required=False):
    if phase is None and not required:
        return
    require(isinstance(phase, dict), "unknown heavy phase status")
    if not tts and not required and phase.get("enabled") is False and phase.get("busy") is False:
        return  # Old flag-off Next may be explicitly migrated.
    require(phase.get("enabled") is True and phase.get("lock_path") == str(PHASE_LOCK), "phase serialization not loaded or wrong lease path")
    if tts:
        require(phase.get("idle") is True and all(type(phase.get(key)) is int and phase[key] == 0 for key in ("outstanding_workers", "waiting_workers", "active_workers")), "TTS native worker active/waiting or phase unknown")
        require(all(phase.get(key) == value for key, value in {"minimum_start_free_percent": 40, "minimum_free_percent": 22, "maximum_swap_growth_mib": 256, "baseline_scope": "engine_lifetime_first_serialized_worker", "native_abort_supported": False}.items()) and "blocked_reason" in phase, "TTS phase resource contract unknown")
        if required:
            require(phase.get("enforcement_revision") == "shared_epoch_authoritative_local_telemetry_v2"
                    and phase.get("enforcement_scope") == "shared_exhibition_epoch"
                    and phase.get("local_lifetime_measurement_only") is True
                    and phase.get("start_memory_recovery_revision") == "shared_epoch_bounded_start_memory_recovery_v1"
                    and phase.get("start_memory_recovery_enabled") is True
                    and phase.get("start_memory_recovery_wait_seconds") == 60.0
                    and phase.get("start_memory_recovery_poll_seconds") == 1.0,
                    "TTS shared resource enforcement policy not loaded")
            require(phase["blocked_reason"] is None, "TTS phase resource budget latched")
            require(phase.get("max_chunk_chars") == 16 and phase.get("chunking_policy") == "punctuation_first_hard_cap" and phase.get("chunk_resource_checkpoint") is True, "TTS bounded native chunk policy not loaded")
            require(phase.get("idle_residency_policy") == "unload_after_worker", "TTS cold-on-demand residency policy not loaded")
    else:
        require(phase.get("busy") is False, "heavy phase busy or unknown")


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def assess_voice(owned, fetch, require_candidate):
    phase, engine, runtime = None, {}, {}
    cold_available = False
    if require_candidate:
        require(set(TTS_SERVICES) <= set(owned), "required voice service absent")
    if 5088 in owned:
        engine = fetch(5088, "/health")
        phase = engine.get("phase_serialization")
        check_phase(phase, tts=True, required=require_candidate)
        require(all(engine.get(key) == value for key, value in {"status": "ok", "service": "lumina-product-irodori-5088", "server_revision": "2026-08-20-qwen35-ref-latent-v1", "owned": True, "offline": True}.items()), "Irodori engine identity/offline health mismatch")
        require(engine.get("product_voice") == VOICE, "Irodori product voice/hash mismatch")
        model = engine.get("model", {})
        require(model.get("hf_checkpoint") == "Aratako/Irodori-TTS-v4-Small-Quantized/int8-weight-only" and all(model.get(key) == value for key, value in {"model_device": "mps", "codec_device": "mps", "model_precision": "fp32", "codec_precision": "fp32"}.items()), "Irodori model/device profile mismatch")
        runtime = engine.get("runtime", {})
        require(runtime.get("empty_cache_interval") == 1 and runtime.get("loading") is False, "Irodori cache policy/loading state unsafe")
        if "unloading" in runtime or (phase or {}).get("idle_residency_policy") == "unload_after_worker":
            require(runtime.get("unloading") is False and "unload_error" in runtime and runtime["unload_error"] is None, "Irodori unload incomplete/failed or state unknown")
        cold_available = (
            isinstance(phase, dict) and phase.get("idle_residency_policy") == "unload_after_worker"
            and phase.get("blocked_reason") is None and "blocked_reason" in phase
            and engine.get("cold_on_demand") is True and engine.get("available") is True
            and engine.get("ready") is False and runtime.get("loaded") is False
            and runtime.get("loading") is False and runtime.get("unloading") is False
            and runtime.get("residency_state") == "cold_on_demand"
            and "unload_error" in runtime and runtime["unload_error"] is None
        )
        if require_candidate:
            require(cold_available, "Irodori cold-on-demand availability not attested; loaded readiness is not idle availability")
            require(runtime.get("checkpoint_release_revision") == "cpu_state_early_release_v1", "Irodori checkpoint early-release code not loaded")
            require(runtime.get("meta_model_init") is True and runtime.get("meta_load_revision") == "meta_assign_modernbert_v1", "Irodori audited meta model initialization not enabled/loaded")
            require(runtime.get("serial_cfg") is True and runtime.get("serial_cfg_revision") == "independent_cfg_shared_kv_v2", "Irodori serial CFG execution not enabled/loaded")
            read = (phase.get("shared_epoch") or {}).get("health_read") or {}
            require(read.get("revision") == "bounded_lock_retry_v1"
                    and read.get("max_attempts") == 3 and read.get("lock_timeout_seconds") == .05
                    and read.get("retry_delay_seconds") == .01 and read.get("budget_seconds") == .2
                    and type(read.get("attempts")) is int and 1 <= read["attempts"] <= 3
                    and finite_number(read.get("elapsed_seconds")) and 0 <= read["elapsed_seconds"] <= .2
                    and read.get("budget_exceeded") is False,
                    "Irodori bounded health epoch read not loaded or not fresh within budget")
    if 5055 in owned:
        adapter = fetch(5055, "/health")
        require(adapter.get("status") == "ok" and adapter.get("ready") is True and adapter.get("backend") == "irodori" and adapter.get("irodori_base_url") == "http://127.0.0.1:5088" and adapter.get("irodori_voice") == "tsukuyomi", "TTS adapter/backend/voice not ready")
    if 5056 in owned:
        gateway = fetch(5056, "/health")
        require(all(gateway.get(key) == value for key, value in {"status": "ok", "gateway": True, "alive": True, "upstream_ok": True, "upstream": "http://127.0.0.1:5055", "product_voice_url": "http://127.0.0.1:5056/voice-lipsync", "queue_size": 0, "pid": owned[5056]["pid"]}.items()), "TTS gateway/upstream/queue not ready")
    # Keep actual model-ready semantics: a healthy on-demand idle engine is
    # available but NOT loaded/ready. No synthesis is performed by this check.
    return {"ready": engine.get("ready"), "available": cold_available and set(TTS_SERVICES) <= set(owned),
            "availability": "cold_on_demand" if cold_available else "unavailable_or_unattested",
            "cold_on_demand": engine.get("cold_on_demand"), "engine_available": engine.get("available"),
            "runtime": {key: runtime[key] for key in ("loaded", "loading", "unloading", "residency_state", "unload_error", "empty_cache_interval", "checkpoint_release_revision", "meta_model_init", "meta_load_revision", "serial_cfg", "serial_cfg_revision", "memory_diagnostics", "factory_memory_revision") if key in runtime},
            "backend": "irodori", "voice": "tsukuyomi", "playback_acceptance": "not_assessed", "phase_serialization": phase}


def read_shared_epoch(path=None):
    """Explicit trusted module, bounded read only; never initialize or reset."""
    path = EPOCH_PATH if path is None else Path(path)
    result = {"path": str(path), "read_only": True, "observed_unix": time.time(), "error": None, "state": None}
    try:
        spec = importlib.util.spec_from_file_location("lumina_ops_resource_epoch", ROOT / "services/lumina_resource_epoch.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            state = module.read_epoch(path=path, lock_timeout=.05)
        except module.EpochError as error:
            result["error"] = error.code
        else:
            result["state"] = {key: value for key, value in state.items() if key != "source_notes"}
    except (OSError, ValueError, ImportError, AttributeError, TypeError):
        result["error"] = "visual_resource_epoch_module_unavailable"
    return result


def shared_epoch_attestation(direct, navigation, voice):
    """Old idle sources may migrate; known errors/latches/changed IDs may not."""
    result = dict(direct)
    state = direct.get("state")
    def number(value):
        return type(value) in (int, float) and 0 <= value <= 1e15 and math.isfinite(value)
    healthy = (direct.get("error") is None and isinstance(state, dict)
               and state.get("schema") == "lumina-resource-epoch-v1"
               and state.get("baseline_scope") == "shared_exhibition_epoch"
               and type(state.get("epoch_id")) is str and bool(state["epoch_id"])
               and type(state.get("minimum_free_percent")) is int and state["minimum_free_percent"] == 22
               and type(state.get("maximum_swap_growth_mib")) is int and state["maximum_swap_growth_mib"] == 256
               and number(state.get("baseline_swap_mib")) and number(state.get("peak_swap_growth_mib"))
               and state["peak_swap_growth_mib"] <= 256 and "blocked_reason" in state and state["blocked_reason"] is None)
    peers = {
        "next_job": (navigation.get("resource_budget") or {}).get("shared_epoch"),
        "next_phase": (navigation.get("heavy_phase") or {}).get("shared_epoch"),
        "tts": (voice.get("phase_serialization") or {}).get("shared_epoch"),
    }
    errors, migration_only, unsafe = {}, [], False
    for name, peer in peers.items():
        if peer is None:
            migration_only.append(name)
            continue
        if isinstance(peer, dict) and peer.get("enabled") is False:
            prior = peer.get("snapshot" if name != "tts" else "state")
            if peer.get("error") or (isinstance(prior, dict) and prior.get("blocked_reason")):
                errors[name] = "disabled_peer_retains_failure"
                unsafe = True
            else:
                migration_only.append(name)
            continue
        if not isinstance(peer, dict) or peer.get("enabled") is not True or "error" not in peer or peer["error"] is not None:
            errors[name] = "shared_epoch_disabled_unknown_or_error"
            unsafe = True
            continue
        cached = name != "tts"
        snapshot = peer.get("snapshot" if cached else "state")
        if cached and peer.get("cached_only") is not True:
            errors[name] = "unknown_cache_contract"
        elif cached and snapshot is None and "snapshot" in peer and peer.get("last_observed_unix") is None:
            continue  # Fresh independent file read, not this empty cache, authorizes candidate status.
        elif not isinstance(snapshot, dict):
            errors[name] = "epoch_snapshot_unknown"
        elif snapshot.get("blocked_reason") is not None or "blocked_reason" not in snapshot:
            errors[name] = "epoch_snapshot_latched_or_unknown"
        elif not healthy:
            errors[name] = "independent_epoch_unavailable"
        elif any(type(snapshot.get(key)) is not type(state[key]) or snapshot[key] != state[key] for key in (
                "epoch_id", "baseline_scope", "baseline_swap_mib", "minimum_free_percent", "maximum_swap_growth_mib")):
            errors[name] = "epoch_identity_or_limits_changed"
        elif not number(snapshot.get("peak_swap_growth_mib")) or snapshot["peak_swap_growth_mib"] > state["peak_swap_growth_mib"]:
            errors[name] = "epoch_peak_regressed_or_unknown"
        else:
            observed = peer.get("last_observed_unix" if cached else "observed_unix")
            if not number(observed) or observed > time.time() or (not cached and time.time() - observed > 10):
                errors[name] = "epoch_observation_unknown_or_stale"
        unsafe = unsafe or name in errors
    scope = navigation.get("resource_monitor_scope")
    result.update(safe_for_explicit_start=bool(healthy and not unsafe), peer_errors=errors,
                  migration_only_peers=migration_only, resource_monitor_scope=scope,
                  candidate_attested=bool(healthy and not unsafe and not migration_only
                                          and scope == "visual_job_including_speech"))
    return result


def assess(owned, godot, fetch=get, preflight=False, *, epoch_reader=None):
    requested_assets = requested_qwen_assets()
    requested_microbatch = requested_qwen_microbatch()
    lease = phase_lease_status()
    require(lease.get("busy") is False, "heavy phase lease busy or unknown")
    props = fetch(11436, "/props") if 11436 in owned else {}
    if props:
        require(type(props.get("is_sleeping")) is bool, "model sleep state unknown; refusing waking probes")
    voice = assess_voice(owned, fetch, require_candidate=not preflight)
    navigation = fetch(8790, "/visual-navigation") if 8790 in owned else {}
    phase = navigation.get("heavy_phase")
    check_phase(phase, required=not preflight)
    sleep_owner = owned.get(11436, {}).get("model_sleep_enabled") is True
    if 8790 in owned and (sleep_owner or props.get("is_sleeping") is True):
        # Inspect the phase contract before Next health; a mixed old/new
        # serialized stack is not an attested status path for this checker.
        check_phase(phase, required=True)
    budget = None
    bridge = fetch(8765, "/health?compact=true") if 8765 in owned else {}
    nxt = fetch(8790, "/health") if 8790 in owned else {}
    world = fetch(8765, "/world-state") if bridge.get("godot_connected") else {}
    avatar = world.get("raw", {}).get("avatar_state", {})
    if bridge:
        require(bridge.get("pending_commands") == 0 and bridge.get("audio_output_active") is False, "bridge has pending commands/audio")
    if nxt:
        resources = fetch(8790, "/resource" if preflight else "/resource?compact=true")
        require(resources.get("active_request_ids") == [], "active chat or unknown chat status")
        require(not nxt.get("visual_navigation", {}).get("active_request_id"), "visual navigation active")
        if "visual_navigation" in nxt:
            require(not navigation.get("active_request_id") and not navigation.get("unconfirmed_stop_command_id"), "visual navigation active or stop unconfirmed")
            budget = navigation.get("resource_budget")
    if godot or world:
        require(bridge.get("godot_connected") and 0 <= time.time() - float(world.get("updated_at", 0)) < 3, "Godot idle state unavailable/stale")
        require(not world.get("active_command_id") and avatar.get("is_moving") is False and avatar.get("is_speaking") is False, "avatar busy or unknown motion state")
    if 11436 in owned:
        if props.get("is_sleeping") is not True:
            # With auto-sleep, even props(false)->slots has a wakeup race. Wait
            # for sleep instead; never poll /slots/models/metrics for that owner.
            require(not sleep_owner, "model awaiting idle sleep; no waking probe sent")
            slots = fetch(11436, "/slots")
            require(isinstance(slots, list) and bool(slots) and all(s.get("is_processing") is False for s in slots), "model generation active or unknown")
    shared = shared_epoch_attestation((epoch_reader or read_shared_epoch)(), navigation, voice)
    if preflight:
        return {"safe_idle_preflight": True, "owners": owned, "godot_pids": godot, "resource_budget": budget, "tts_phase": voice["phase_serialization"], "voice": voice, "heavy_phase": phase, "lease": lease, "shared_epoch": shared, "retained_wip15_pid": godot[0] if len(godot) == 1 and avatar.get("vrm_loaded") is True and avatar.get("vrm_scene_path") == WIP15_SCENE else 0}
    require(set(owned) == set(SERVICES) | set(TTS_SERVICES), "required service absent")
    require(bridge.get("status") == "ok" and nxt.get("ok") is True, "service health not ready")
    require(bridge.get("visual_navigation", {}).get("enabled") is True and nxt.get("visual_navigation", {}).get("enabled") is True, "visual profile not loaded in bridge/Next")
    require(
        bridge.get("visual_navigation", {}).get("portable_navigation_enabled") is True
        and nxt.get("visual_navigation", {}).get("portable_navigation_enabled") is True
        and navigation.get("portable_navigation_enabled") is True
        and bridge.get("visual_navigation", {}).get("grounding_basis") == "raw_eye_image_then_metric_depth"
        and navigation.get("grounding_basis") == "rgb_bbox_then_metric_depth",
        "portable depth/map navigation profile not loaded in bridge/Next",
    )
    require(
        navigation.get("grounding_policy_revision") == PORTABLE_GROUNDING_POLICY_REVISION,
        "portable RGB-D grounding policy not loaded in Next",
    )
    require(owned[11436].get("bounded_vision_cache") is True, "vision model lacks attested bounded cache contract; explicit model restart required")
    require(owned[11436].get('qwen_microbatch_tokens') == requested_microbatch
            and claimed_qwen_microbatch(owned[11436].get('vision_contract')) == requested_microbatch,
            'requested microbatch mode does not match attested owner/argv')
    assets = owned[11436].get('qwen_assets')
    require(isinstance(assets, dict) and assets.get('attested') is True
            and all(assets.get(key) == value for key, value in requested_assets.items())
            and assets.get('model_size') == QWEN_MODEL_SIZE and assets.get('model_sha256') == QWEN_MODEL_SHA256
            and assets.get('projector_size') == QWEN_PROJECTOR_SIZE and assets.get('projector_sha256') == QWEN_PROJECTOR_SHA256,
            'requested model asset location/pinned startup identity not loaded')
    require(props.get('model_path') == requested_assets['model'], 'actual props model_path differs from attested asset location')
    require(sleep_owner and owned[11436].get("vision_contract", {}).get("idle_sleep_seconds") == 3, "vision idle-sleep contract not loaded")
    require(isinstance(budget, dict) and budget.get("minimum_free_percent") == 22 and budget.get("maximum_swap_growth_mib") == 256 and budget.get("baseline_scope") == "service_lifetime_first_visual_request" and budget.get("enforcement_revision") == "shared_epoch_authoritative_local_telemetry_v2" and budget.get("enforcement_scope") == "shared_exhibition_epoch" and budget.get("local_lifetime_measurement_only") is True and "blocked_reason" in budget, "visual resource guard not loaded/attested")
    require(budget["blocked_reason"] is None, "visual resource budget latched: " + str(budget["blocked_reason"]))
    require(shared["candidate_attested"] is True, "shared resource epoch or whole-job monitor not attested: " + json.dumps(shared, ensure_ascii=False))
    require(navigation.get("inventory_wire_format") == "compact_tuple_v1", "compact inventory wire not loaded; explicit Next restart required")
    require(
        portable_selection_policy_ready(navigation),
        "portable open-vocabulary single-target policy not loaded; explicit Next restart required",
    )
    require(navigation.get("status_probe_revision") == "offloop_singleflight_phase_status_v1"
            and phase.get("probe_revision") == "offloop_singleflight_phase_status_v1"
            and phase.get("probe_pending") is False and not phase.get("error")
            and finite_number(phase.get("probe_observed_unix"))
            and 0 <= time.time() - phase["probe_observed_unix"] < 3,
            "Next nonblocking fresh phase status not loaded/attested")
    activity = resources
    require(activity.get("ok") is True and "_error" not in activity
            and activity.get("activity_probe_revision") == "memory_only_activity_v1"
            and activity.get("activity_only") is True and activity.get("memory_measured") is False
            and activity.get("active_request_ids") == []
            and finite_number(activity.get("observed_unix"))
            and 0 <= time.time() - activity["observed_unix"] < 3,
            "Next memory-only idle activity probe not loaded/attested")
    require(props.get("modalities", {}).get("vision") is True and props.get("model_alias") == "qwen3.5-4b-q4_K_M", "model vision not loaded or alias mismatch")
    require(nxt.get("llm", {}).get("health_probe") == "non_waking_health_props", "Next non-waking provider health not loaded")
    # Adapter keeps its source VRM path while loading the attested sibling preview.
    require(len(godot) == 1 and avatar.get("vrm_loaded") is True and avatar.get("vrm_scene_path") == WIP15_SCENE, "WIP15 not loaded")
    capture_dir = configured_capture_directory(navigation)
    frame = verify_frame(capture_dir)
    require(str(frame.get("frame_id", "")).split("-", 1)[0] == str(godot[0]), "eye frame belongs to another Godot process")
    return {"candidate_ready": True, "exhibit_acceptance": "not_assessed", "recognition_accuracy": "not_assessed", "owners": owned, "frame": frame, "capture_directory": str(capture_dir), "voice": voice, "resource_budget": budget, "heavy_phase": phase, "lease": lease, "shared_epoch": shared, "model_sleeping": props.get("is_sleeping")}


def configured_capture_directory(navigation):
    explicit = os.environ.get("LUMINA_EYE_CAPTURE_DIR", "").strip()
    configured = Path(explicit).expanduser() if explicit else DEFAULT_CAPTURE_DIRECTORY
    directory = configured.resolve()
    require(directory.is_absolute(), "eye capture directory must be absolute")
    reported = navigation.get("capture_directory")
    # An explicit relocation must attest the loaded Next configuration, not
    # just a fresh PNG belonging to a different publisher/consumer directory.
    if explicit or reported is not None:
        require(isinstance(reported, str) and Path(reported).expanduser().resolve() == directory,
                "Next eye capture directory mismatch")
    return directory


def verify_frame(directory):
    require((directory / "latest.json").stat().st_size <= 2_000_000, "oversized eye metadata")
    meta = json.loads((directory / "latest.json").read_text())
    age = time.time() - float(meta.get("captured_unix", 0))
    require(0 <= age < 3, "eye frame stale/future")
    path = Path(meta["image_path"]).resolve()
    require(path.parent == directory.resolve() and path.suffix == ".png", "eye image outside capture directory")
    require(meta.get("pose_source") == "animated_eye_bones", "camera not following real eye bones")
    require(meta.get("render_source") == "dedicated_eye_subviewport",
            "eye pixels lack dedicated viewport attestation")
    render_viewport = meta.get("render_viewport_instance_id")
    render_camera = meta.get("render_camera_instance_id")
    active_camera = meta.get("active_camera_instance_id")
    require(type(render_viewport) is int and render_viewport > 0
            and type(render_camera) is int and render_camera > 0
            and type(active_camera) is int and active_camera > 0
            and active_camera == render_camera,
            "eye viewport was not rendered by the attested eye camera")
    for key in ("camera_position", "camera_forward"):
        require(all(math.isfinite(float(meta[key][axis])) for axis in "xyz"), "invalid eye pose")
    norm = sum(float(meta["camera_forward"][axis]) ** 2 for axis in "xyz")
    require(0.98 < norm < 1.02, "eye forward is not normalized")
    require(path.stat().st_size <= 4_000_000, "oversized eye image")
    image = path.read_bytes()
    require(image.startswith(b"\x89PNG\r\n\x1a\n") and hashlib.sha256(image).hexdigest() == meta.get("image_sha256"), "eye image/hash mismatch")
    depth = meta.get("portable_depth")
    require(
        isinstance(depth, dict)
        and depth.get("available") is True
        and depth.get("representation") == "sparse_rays"
        and depth.get("measurement_model") == "ray_range_m"
        and depth.get("alignment") == "registered_normalized_to_rgb",
        "portable metric depth unavailable",
    )
    require(
        depth.get("surface_id_semantics_revision")
        == SURFACE_ID_SEMANTICS_REVISION,
        "portable surface identity semantics unavailable",
    )
    attempted = int(depth.get("attempted_ray_count", 0))
    valid = int(depth.get("valid_ray_count", 0))
    samples = depth.get("samples")
    require(attempted > 0 and 0 < valid <= attempted and isinstance(samples, list) and len(samples) == valid, "portable depth ray counts invalid")
    for sample in samples:
        uv = sample.get("uv_norm") if isinstance(sample, dict) else None
        surface_id = sample.get("surface_id") if isinstance(sample, dict) else None
        require(
            isinstance(uv, list) and len(uv) == 2
            and all(math.isfinite(float(value)) and 0 <= float(value) <= 1 for value in uv)
            and math.isfinite(float(sample.get("distance_m", 0))) and float(sample.get("distance_m", 0)) > 0
            and math.isfinite(float(sample.get("confidence", 0))) and 0 <= float(sample.get("confidence", 0)) <= 1,
            "portable depth sample invalid",
        )
        require(
            isinstance(surface_id, str)
            and re.fullmatch(r"surface:[0-9a-f]{64}", surface_id) is not None,
            "portable depth surface identity unavailable",
        )
    return {"frame_id": meta.get("frame_id"), "age_seconds": age, "image_sha256": meta["image_sha256"],
            "pose_source": meta["pose_source"], "portable_depth_valid_rays": valid,
            "portable_depth_attempted_rays": attempted,
            "surface_id_semantics_revision": SURFACE_ID_SEMANTICS_REVISION}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true", help="idle/owner check only; permits absent services")
    mode.add_argument("--retained-godot-pid", type=int, help="process identity only; validate a WIP15 PID saved by a prior idle preflight")
    parser.add_argument("--require-startable", action="store_true", help="with --preflight, additionally refuse unknown/latched epochs before any service stop")
    args = parser.parse_args()
    try:
        require(not args.require_startable or args.preflight, "--require-startable requires --preflight")
        owned, godot = inventory()
        if args.retained_godot_pid is not None:
            require(args.retained_godot_pid > 0 and godot == [args.retained_godot_pid], "previous Godot owner changed; refusing implicit replacement")
            result = {"retained_godot_pid": args.retained_godot_pid, "identity_only": True, "candidate_ready": False}
        else:
            result = assess(owned, godot, preflight=args.preflight)
            if args.require_startable:
                require(result["shared_epoch"]["safe_for_explicit_start"] is True and not any(
                    (result.get(key) or {}).get("blocked_reason") for key in ("resource_budget", "tts_phase")),
                    "resource epoch/budget unavailable or latched; no automatic initialization/reset")
    except (OSError, ValueError, TypeError, KeyError, IndexError, subprocess.SubprocessError) as error:
        print(json.dumps({"candidate_ready": False, "exhibit_acceptance": "not_assessed", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
