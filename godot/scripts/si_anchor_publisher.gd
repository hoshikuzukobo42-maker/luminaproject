extends Node
class_name SiAnchorPublisher

## Phase 3 main main: anchor position publisher.
##
## Walks future_room_v008 anchor Node3Ds and POSTs their positions to the bridge.
## Default endpoint: {bridge_url}/world/anchor-publish with JSON:
##   {
##     "schema": "lumina.anchor_publish.v0",
##     "room_id": "future_room_v008",
##     "published_at_iso": "...",
##     "anchors": [
##       {"name": "Table", "x": 0.0, "y": 0.0, "z": 1.20, "kind": "furniture"},
##       ...
##     ]
##   }
##
## Usage:
##   var pub = SiAnchorPublisher.new()
##   pub.bridge_url = "http://127.0.0.1:8000"
##   pub.room_root = $RoomFutureV008
##   pub.publish_once()
##
## To re-publish on demand: call publish_once() again.

@export var bridge_url: String = "http://127.0.0.1:8000"
@export var enabled: bool = false  ## off by default
@export var anchor_endpoint: String = "/world/anchor-publish"
@export var anchor_name_prefix: String = "Anchor_"  ## by-convention prefix for anchor children
@export var room_id: String = "future_room_v008"

var room_root: Node3D = null
var _http: HTTPRequest = null


func _ready() -> void:
    _http = HTTPRequest.new()
    add_child(_http)


func publish_once() -> void:
    if not enabled:
        push_warning("[SiAnchorPublisher] enabled=false; not publishing")
        return
    var anchors = _collect_anchors()
    var ts = Time.get_datetime_string_from_system(true) + "Z"
    var payload = {
        "schema": "lumina.anchor_publish.v0",
        "room_id": room_id,
        "published_at_iso": ts,
        "anchors": anchors,
    }
    var body = JSON.stringify(payload)
    if _http:
        _http.request(
            bridge_url + anchor_endpoint,
            ["Content-Type: application/json"],
            HTTPClient.METHOD_POST,
            body
        )


func _collect_anchors() -> Array:
    var out: Array = []
    if not room_root:
        return out

    var known_names = ["Table", "Sofa", "Chair_A", "Chair_B", "Plant", "SideTable"]

    for name in known_names:
        var node = room_root.find_child(name, true, false)
        if node and node is Node3D:
            var pos = (node as Node3D).global_position
            out.append({
                "name": name,
                "x": pos.x,
                "y": pos.y,
                "z": pos.z,
                "kind": _classify_anchor_kind(name),
            })
        else:
            # Also try with anchor_name_prefix
            node = room_root.find_child(anchor_name_prefix + name, true, false)
            if node and node is Node3D:
                var pos = (node as Node3D).global_position
                out.append({
                    "name": name,
                    "x": pos.x,
                    "y": pos.y,
                    "z": pos.z,
                    "kind": _classify_anchor_kind(name),
                })

    return out


func _classify_anchor_kind(name: String) -> String:
    if name in ["Sofa", "Chair_A", "Chair_B"]:
        return "seating"
    if name == "Plant":
        return "decoration"
    return "furniture"
