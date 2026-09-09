"""Lexical RAG for the unpromoted Gemma 4 E4B candidate.

Embeddings stay off.  Archive_* is never walked.  Live chat does not inject
hits into the persistent LiteRT KV; callers may use a retrieved line only as a
post-generation sanitizer fallback.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
WAREHOUSE = ROOT / "data" / "gemma4_e4b_rag"
DUMP = ROOT / "data" / "gemma4_e4b_rag_ALL_NATURAL_CARDS.txt"
LIVE_DIRS = ("seed", "synth_candidates")

_TOKEN_RE = re.compile(
    r"[A-Za-z0-9_]{2,}|[\u3400-\u4dbf\u4e00-\u9fff]+|[\u3040-\u309f]{2,}|[\u30a0-\u30ff]{2,}"
)
_STOP = frozenset("はがをにへでとのもだよなのにねさ")
_CARD_CACHE: dict[str, tuple[tuple[tuple[str, int], ...], list[dict]]] = {}
_QUERY_ALIASES = (
    (re.compile(r"おはよう"), "おはよ"),
    (re.compile(r"眠[いくっ]|ねむい|ねむく"), "眠い"),
    (re.compile(r"お腹空|おなかす|ペコペコ|腹減"), "お腹すいた"),
    (re.compile(r"疲れ[たて]|しんど"), "疲れた"),
    (re.compile(r"のど渇|喉乾|喉渇"), "のどかわいた"),
    (re.compile(r"さむい|寒っ"), "寒い"),
    (re.compile(r"あつい|暑っ"), "暑い"),
    (re.compile(r"むかつ|イライラ|腹立つ"), "むかつく"),
    (re.compile(r"うま[くっ]いった|うまくいった"), "うまくいった"),
)


def tokenize(text: str) -> set[str]:
    tokens = {tok.lower() for tok in _TOKEN_RE.findall(text or "") if tok.strip()}
    return {tok for tok in tokens if tok not in _STOP and len(tok) >= 2}


def expand_queries(query: str) -> list[str]:
    raw = unicodedata.normalize("NFKC", query or "").strip()
    out: list[str] = []
    if raw:
        out.append(raw)
    for cre, alias in _QUERY_ALIASES:
        if raw and cre.search(raw) and alias not in out:
            out.append(alias)
    return out


def live_jsonl_paths(warehouse: Path) -> list[Path]:
    paths: list[Path] = []
    for name in LIVE_DIRS:
        folder = warehouse / name
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.jsonl")):
            if path.name.startswith("._"):
                continue
            if any(part.startswith("archive") for part in path.parts):
                continue
            paths.append(path)
    return paths


def load_cards(warehouse: Path) -> list[dict]:
    warehouse = Path(warehouse)
    paths = live_jsonl_paths(warehouse)
    stamp = tuple((str(path), path.stat().st_mtime_ns) for path in paths)
    key = str(warehouse.resolve())
    cached = _CARD_CACHE.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    rows: list[dict] = []
    for path in paths:
        rel = str(path.relative_to(warehouse))
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict) or not str(row.get("text") or "").strip():
                    continue
                row["_path"] = rel
                rows.append(row)
    _CARD_CACHE[key] = (stamp, rows)
    return rows


def user_search_text(card: dict) -> str:
    kind = str(card.get("kind") or "")
    text = str(card.get("text") or "")
    if kind != "chat":
        return text
    users = [
        raw.split(":", 1)[1].strip()
        for raw in text.splitlines()
        if raw.startswith("君:") and ":" in raw
    ]
    return "\n".join(users)


def lumina_lines(text: str, kind: str = "") -> list[str]:
    lines: list[str] = []
    for raw in str(text).splitlines():
        if raw.startswith("ルミナ:"):
            body = raw.split(":", 1)[1].strip()
            if body:
                lines.append(body)
    if not lines and kind == "reaction":
        body = str(text).strip()
        if body:
            lines.append(body)
    return lines


def _score_one(query: str, hay: str, card: dict) -> float:
    if not query or not hay:
        return 0.0
    score = 0.0
    lines = [ln for ln in hay.splitlines() if ln]
    if query in lines:
        score += 1.5
    elif query in hay:
        score += 1.0
    q_tokens = tokenize(query)
    h_tokens = tokenize(hay)
    if q_tokens and h_tokens:
        overlap = q_tokens & h_tokens
        if overlap:
            score += len(overlap) / float(len(q_tokens | h_tokens))
    kind = str(card.get("kind") or "")
    if kind == "reaction" and query not in hay and hay not in query:
        score *= 0.2
    return score


def score_card(query: str, card: dict) -> float:
    hay = user_search_text(card)
    best = 0.0
    for item in expand_queries(query):
        best = max(best, _score_one(item, hay, card))
    return best


def retrieve(query: str, *, warehouse: Path = WAREHOUSE, k: int = 3) -> list[dict]:
    query = unicodedata.normalize("NFKC", query or "").strip()
    cards = load_cards(warehouse)
    ranked: list[tuple[float, dict]] = []
    for card in cards:
        kind = str(card.get("kind") or "")
        if kind in {"persona_rule", "runtime_fact"}:
            continue
        sc = score_card(query, card)
        if sc <= 0:
            continue
        ranked.append((sc, card))
    ranked.sort(
        key=lambda item: (
            -item[0],
            0 if item[1].get("kind") == "chat" else 1,
            len(str(item[1].get("text") or "")),
        )
    )
    hits: list[dict] = []
    for sc, card in ranked[: max(1, min(8, k))]:
        text = str(card.get("text") or "")
        hits.append(
            {
                "score": round(sc, 4),
                "kind": card.get("kind"),
                "id": card.get("id"),
                "path": card.get("_path"),
                "lumina_lines": lumina_lines(text, str(card.get("kind") or "")),
                "text": text,
            }
        )
    return hits


def hint_for(query: str, *, warehouse: Path = WAREHOUSE) -> str:
    """Compact style hint for logs/tests.  Not injected into live KV history."""
    line = best_lumina_line(query, warehouse=warehouse)
    return line[:80]


def best_lumina_line(query: str, *, warehouse: Path = WAREHOUSE) -> str:
    hits = retrieve(query, warehouse=warehouse, k=1)
    if not hits:
        return ""
    lines: Iterable[str] = hits[0].get("lumina_lines") or []
    for line in lines:
        body = str(line).strip()
        if body:
            return body[:80]
    return ""


def clear_card_cache() -> None:
    _CARD_CACHE.clear()
