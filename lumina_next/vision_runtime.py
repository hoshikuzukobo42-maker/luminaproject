"""On-demand local HTTP runtime for the Qwen VLM candidate.

The runtime deliberately contains no model import or download logic.  A Qwen
adapter is configured with ``QWEN_VLM_COMMAND`` and is launched once per
request.  This keeps startup cheap and makes it impossible for this candidate
to silently preload a model.  The command receives one JSON request on stdin
and must write one JSON SceneGraph object to stdout.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import dataclasses
import json
import logging
import math
import os
import shlex
import signal
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    import fcntl
except ImportError as exc:  # pragma: no cover - this candidate targets macOS/Linux.
    raise RuntimeError("vision_runtime requires an advisory-lock capable platform") from exc


LOGGER = logging.getLogger("lumina.qwen_vlm")
SCENE_GRAPH_SCHEMA_VERSION = "1.0"
DEFAULT_PORT = 11440
DEFAULT_LOCK_PATH = "/tmp/lumina_vision_runtime.lock"
DEFAULT_MAX_BODY_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 120.0


class ContractError(ValueError):
    """Raised when a request or model result violates the runtime contract."""


class BackendUnavailable(RuntimeError):
    """Raised when the optional on-demand Qwen command is not configured."""


class BackendError(RuntimeError):
    """Raised when the configured Qwen command fails or returns invalid JSON."""


class RuntimeBusyError(RuntimeError):
    """Raised when another vision backend owns the shared runtime lock."""


def _require_string(value: Any, name: str, *, max_length: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name} must be a non-empty string")
    value = value.strip()
    if len(value) > max_length:
        raise ContractError(f"{name} exceeds {max_length} characters")
    return value


def _number(value: Any, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ContractError(f"{name} must be a finite number")
    number = float(value)
    if minimum is not None and number < minimum:
        raise ContractError(f"{name} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise ContractError(f"{name} must be <= {maximum}")
    return number


@dataclass(frozen=True)
class BoundingBox:
    """Normalized image coordinates, each in the inclusive range 0..1."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        values = (self.x, self.y, self.width, self.height)
        if any(not math.isfinite(float(value)) for value in values):
            raise ContractError("bounding-box values must be finite")
        if not 0 <= self.x <= 1 or not 0 <= self.y <= 1:
            raise ContractError("bounding-box origin must be normalized to 0..1")
        if not 0 < self.width <= 1 or not 0 < self.height <= 1:
            raise ContractError("bounding-box size must be normalized and positive")
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ContractError("bounding box must remain inside the image")

    @classmethod
    def from_dict(cls, value: Any) -> "BoundingBox":
        if not isinstance(value, Mapping):
            raise ContractError("bbox must be an object")
        return cls(
            _number(value.get("x"), "bbox.x", minimum=0, maximum=1),
            _number(value.get("y"), "bbox.y", minimum=0, maximum=1),
            _number(value.get("width"), "bbox.width", minimum=0, maximum=1),
            _number(value.get("height"), "bbox.height", minimum=0, maximum=1),
        )

    def to_dict(self) -> dict[str, float]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class SceneObject:
    """An independently identifiable object in a SceneGraph."""

    object_id: str
    label: str
    confidence: float
    bbox: BoundingBox | None = None
    attributes: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "object_id", _require_string(self.object_id, "object_id", max_length=128))
        object.__setattr__(self, "label", _require_string(self.label, "label", max_length=256))
        object.__setattr__(self, "confidence", _number(self.confidence, "confidence", minimum=0, maximum=1))
        normalized = {}
        for key, value in dict(self.attributes).items():
            normalized[_require_string(key, "attribute name", max_length=128)] = _require_string(
                value, "attribute value", max_length=512
            )
        object.__setattr__(self, "attributes", normalized)

    @classmethod
    def from_dict(cls, value: Any) -> "SceneObject":
        if not isinstance(value, Mapping):
            raise ContractError("objects must contain objects")
        raw_attributes = value.get("attributes", {})
        if not isinstance(raw_attributes, Mapping):
            raise ContractError("object attributes must be an object")
        return cls(
            object_id=_require_string(value.get("id", value.get("object_id")), "object id", max_length=128),
            label=_require_string(value.get("label"), "object label", max_length=256),
            confidence=_number(value.get("confidence", 0), "object confidence", minimum=0, maximum=1),
            bbox=None if value.get("bbox") is None else BoundingBox.from_dict(value["bbox"]),
            attributes={str(key): str(item) for key, item in raw_attributes.items()},
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.object_id,
            "label": self.label,
            "confidence": self.confidence,
            "attributes": dict(self.attributes),
        }
        if self.bbox is not None:
            result["bbox"] = self.bbox.to_dict()
        return result


@dataclass(frozen=True)
class SceneRelation:
    """A directed relationship between two SceneGraph object IDs."""

    subject_id: str
    predicate: str
    object_id: str
    confidence: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject_id", _require_string(self.subject_id, "relation subject", max_length=128))
        object.__setattr__(self, "predicate", _require_string(self.predicate, "relation predicate", max_length=128))
        object.__setattr__(self, "object_id", _require_string(self.object_id, "relation object", max_length=128))
        object.__setattr__(self, "confidence", _number(self.confidence, "relation confidence", minimum=0, maximum=1))

    @classmethod
    def from_dict(cls, value: Any) -> "SceneRelation":
        if not isinstance(value, Mapping):
            raise ContractError("relations must contain objects")
        return cls(
            subject_id=_require_string(value.get("subject", value.get("subject_id")), "relation subject", max_length=128),
            predicate=_require_string(value.get("predicate"), "relation predicate", max_length=128),
            object_id=_require_string(value.get("object", value.get("object_id")), "relation object", max_length=128),
            confidence=_number(value.get("confidence", 0), "relation confidence", minimum=0, maximum=1),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject_id,
            "predicate": self.predicate,
            "object": self.object_id,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class SceneGraph:
    """Validated, serializable result returned by the vision runtime."""

    request_id: str
    objects: tuple[SceneObject, ...]
    relations: tuple[SceneRelation, ...] = ()
    summary: str = ""
    model: str = "qwen-vlm-candidate"
    schema_version: str = SCENE_GRAPH_SCHEMA_VERSION
    image_width: int | None = None
    image_height: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _require_string(self.request_id, "request_id", max_length=128))
        if self.schema_version != SCENE_GRAPH_SCHEMA_VERSION:
            raise ContractError(f"unsupported SceneGraph schema version: {self.schema_version!r}")
        object_ids = [item.object_id for item in self.objects]
        if len(object_ids) != len(set(object_ids)):
            raise ContractError("SceneGraph object IDs must be unique")
        valid_ids = set(object_ids)
        for relation in self.relations:
            if relation.subject_id not in valid_ids or relation.object_id not in valid_ids:
                raise ContractError("SceneGraph relations must reference known object IDs")
        if not isinstance(self.summary, str) or len(self.summary) > 4000:
            raise ContractError("summary must be a string of at most 4000 characters")
        object.__setattr__(self, "model", _require_string(self.model, "model", max_length=256))
        for name, value in (("image_width", self.image_width), ("image_height", self.image_height)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
                raise ContractError(f"{name} must be a positive integer when present")

    @classmethod
    def from_dict(cls, value: Any, *, request_id: str | None = None) -> "SceneGraph":
        if not isinstance(value, Mapping):
            raise ContractError("SceneGraph result must be an object")
        if isinstance(value.get("scene_graph"), Mapping):
            value = value["scene_graph"]
        raw_objects = value.get("objects", [])
        raw_relations = value.get("relations", [])
        if not isinstance(raw_objects, Sequence) or isinstance(raw_objects, (str, bytes, bytearray)):
            raise ContractError("objects must be an array")
        if not isinstance(raw_relations, Sequence) or isinstance(raw_relations, (str, bytes, bytearray)):
            raise ContractError("relations must be an array")
        return cls(
            request_id=request_id or value.get("request_id") or str(uuid.uuid4()),
            objects=tuple(SceneObject.from_dict(item) for item in raw_objects),
            relations=tuple(SceneRelation.from_dict(item) for item in raw_relations),
            summary=value.get("summary", ""),
            model=value.get("model", "qwen-vlm-candidate"),
            schema_version=value.get("schema_version", SCENE_GRAPH_SCHEMA_VERSION),
            image_width=value.get("image_width"),
            image_height=value.get("image_height"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "model": self.model,
            "objects": [item.to_dict() for item in self.objects],
            "relations": [item.to_dict() for item in self.relations],
            "summary": self.summary,
            "image_width": self.image_width,
            "image_height": self.image_height,
        }


@dataclass(frozen=True)
class RuntimeConfig:
    """Runtime settings sourced from environment variables and CLI flags."""

    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    lock_path: str = DEFAULT_LOCK_PATH
    command: tuple[str, ...] | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES
    model_name: str = "qwen-vlm-candidate"

    @classmethod
    def from_environment(cls, *, host: str, port: int, lock_path: str) -> "RuntimeConfig":
        command_text = os.environ.get("QWEN_VLM_COMMAND", "").strip()
        try:
            command = tuple(shlex.split(command_text)) if command_text else None
        except ValueError as exc:
            raise ContractError(f"invalid QWEN_VLM_COMMAND: {exc}") from exc
        timeout = float(os.environ.get("QWEN_VLM_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
        max_body = int(os.environ.get("QWEN_VLM_MAX_BODY_BYTES", str(DEFAULT_MAX_BODY_BYTES)))
        max_image = int(os.environ.get("QWEN_VLM_MAX_IMAGE_BYTES", str(DEFAULT_MAX_IMAGE_BYTES)))
        if timeout <= 0 or max_body <= 0 or max_image <= 0 or max_image > max_body:
            raise ContractError("invalid Qwen runtime size or timeout limits")
        return cls(
            host=host,
            port=port,
            lock_path=lock_path,
            command=command,
            timeout_seconds=timeout,
            max_body_bytes=max_body,
            max_image_bytes=max_image,
            model_name=os.environ.get("QWEN_VLM_MODEL", "qwen-vlm-candidate"),
        )


class AdvisoryRuntimeLock:
    """Exclusive lock shared by Qwen and Shisa vision backends."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._handle: Any = None

    def __enter__(self) -> "AdvisoryRuntimeLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._handle.close()
            self._handle = None
            raise RuntimeBusyError(f"another vision backend owns lock {self.path}") from exc
        self._handle.seek(0)
        self._handle.truncate()
        self._handle.write(f"pid={os.getpid()} backend=qwen-vlm\n")
        self._handle.flush()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


class QwenCommandAdapter:
    """Invoke a configured Qwen command only when a scene request arrives."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    @property
    def configured(self) -> bool:
        return bool(self.config.command)

    def infer(self, payload: Mapping[str, Any], request_id: str) -> SceneGraph:
        if not self.config.command:
            raise BackendUnavailable("QWEN_VLM_COMMAND is not configured; runtime is liveness-only")
        serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        try:
            completed = subprocess.run(
                self.config.command,
                input=serialized,
                text=True,
                capture_output=True,
                timeout=self.config.timeout_seconds,
                check=False,
                env={**os.environ, "LUMINA_VLM_ON_DEMAND": "1"},
            )
        except subprocess.TimeoutExpired as exc:
            raise BackendError(f"Qwen command timed out after {self.config.timeout_seconds:g}s") from exc
        except OSError as exc:
            raise BackendError(f"unable to launch Qwen command: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip()[-1000:]
            raise BackendError(f"Qwen command exited {completed.returncode}: {detail or 'no stderr'}")
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise BackendError(f"Qwen command did not return JSON: {exc.msg}") from exc
        graph = SceneGraph.from_dict(result, request_id=request_id)
        return dataclasses.replace(graph, model=self.config.model_name)


class VisionHTTPServer(ThreadingHTTPServer):
    """HTTP server carrying immutable runtime configuration and adapter state."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], config: RuntimeConfig) -> None:
        super().__init__(address, RuntimeRequestHandler)
        self.config = config
        self.adapter = QwenCommandAdapter(config)


class RuntimeRequestHandler(BaseHTTPRequestHandler):
    """Minimal JSON HTTP surface for health and SceneGraph inference."""

    server: VisionHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)

    def _send_json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "runtime": "qwen-vlm",
                    "candidate_port": self.server.config.port,
                    "on_demand": True,
                    "backend_configured": self.server.adapter.configured,
                    "model_loaded": False,
                },
            )
            return
        if self.path == "/readyz":
            ready = self.server.adapter.configured
            self._send_json(
                HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
                {"status": "ready" if ready else "backend_unconfigured", "on_demand": True},
            )
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/v1/scene-graph":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        request_id = str(uuid.uuid4())
        try:
            payload = self._read_request()
            request_id = payload["request_id"]
            graph = self.server.adapter.infer(payload, request_id)
            self._send_json(HTTPStatus.OK, graph.to_dict())
        except BackendUnavailable as exc:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "backend_unconfigured", "message": str(exc)})
        except BackendError as exc:
            LOGGER.warning("request %s failed: %s", request_id, exc)
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": "backend_error", "request_id": request_id, "message": str(exc)})
        except (ContractError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request", "request_id": request_id, "message": str(exc)})
        except Exception:
            LOGGER.exception("unexpected failure for request %s", request_id)
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal_error", "request_id": request_id})

    def _read_request(self) -> dict[str, Any]:
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            raise ContractError("Content-Length is required")
        try:
            length = int(content_length)
        except ValueError as exc:
            raise ContractError("Content-Length must be an integer") from exc
        if length < 0 or length > self.server.config.max_body_bytes:
            raise ContractError("request body exceeds configured limit")
        try:
            raw = self.rfile.read(length).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractError("request body must be UTF-8 JSON") from exc
        value = json.loads(raw)
        if not isinstance(value, Mapping):
            raise ContractError("request body must be a JSON object")
        request_id = value.get("request_id", str(uuid.uuid4()))
        request_id = _require_string(request_id, "request_id", max_length=128)
        image = value.get("image")
        if not isinstance(image, Mapping):
            raise ContractError("image must be an object")
        mime_type = _require_string(image.get("mime_type"), "image.mime_type", max_length=128)
        if not mime_type.startswith("image/"):
            raise ContractError("image.mime_type must start with image/")
        encoded = _require_string(image.get("data_base64"), "image.data_base64", max_length=self.server.config.max_body_bytes)
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ContractError("image.data_base64 is not valid base64") from exc
        if not decoded or len(decoded) > self.server.config.max_image_bytes:
            raise ContractError("decoded image exceeds configured limit or is empty")
        normalized: dict[str, Any] = {
            "request_id": request_id,
            "image": {"mime_type": mime_type, "data_base64": encoded},
        }
        if "prompt" in value:
            normalized["prompt"] = _require_string(value["prompt"], "prompt", max_length=4000)
        for name in ("width", "height"):
            if name in image:
                dimension = image[name]
                if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0 or dimension > 100000:
                    raise ContractError(f"image.{name} must be a positive integer")
                normalized["image"][name] = dimension
        return normalized


def _serve(config: RuntimeConfig) -> int:
    try:
        with AdvisoryRuntimeLock(config.lock_path):
            server = VisionHTTPServer((config.host, config.port), config)
            previous_handlers = {}

            def request_shutdown(signum: int, _frame: Any) -> None:
                LOGGER.info("received signal %s; shutting down", signum)
                threading.Thread(target=server.shutdown, daemon=True).start()

            for signum in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[signum] = signal.signal(signum, request_shutdown)
            LOGGER.info("Qwen VLM candidate listening on %s:%d (on-demand)", config.host, config.port)
            try:
                server.serve_forever(poll_interval=0.2)
            finally:
                server.server_close()
                for signum, handler in previous_handlers.items():
                    signal.signal(signum, handler)
    except RuntimeBusyError as exc:
        LOGGER.error("cannot start Qwen VLM candidate: %s", exc)
        return 2
    except OSError as exc:
        LOGGER.error("cannot bind Qwen VLM candidate on %s:%d: %s", config.host, config.port, exc)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="run the local HTTP runtime")
    parser.add_argument("--host", default=os.environ.get("QWEN_VLM_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("QWEN_VLM_PORT", DEFAULT_PORT)))
    parser.add_argument("--lock-path", default=os.environ.get("LUMINA_VISION_LOCK_PATH", DEFAULT_LOCK_PATH))
    args = parser.parse_args(argv)
    if not args.serve:
        parser.error("--serve is required; this module does not run a model by itself")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        config = RuntimeConfig.from_environment(host=args.host, port=args.port, lock_path=args.lock_path)
    except (ContractError, ValueError) as exc:
        parser.error(str(exc))
    return _serve(config)


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LUMINA_LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
