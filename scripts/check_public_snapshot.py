"""公開コピーの整合性・代表的な機密パターンを読み取り専用で点検する。

網羅的な秘密検出やライセンス監査の代わりにはならない。
検出した値は表示せず、相対パス・行番号・種類だけを報告する。
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
BLOCKED_DIRS = {
    "data", "logs", "outputs", "evidence", "reports", "screenshots",
    "backups", "assets", "addons", "config", "node_modules",
}
BLOCKED_SUFFIXES = {
    ".env", ".db", ".sqlite", ".sqlite3", ".jsonl", ".pem", ".key",
    ".p12", ".pfx", ".gguf", ".safetensors", ".pt", ".pth", ".onnx",
    ".vrm", ".glb", ".gltf", ".blend", ".png", ".jpg", ".wav",
    ".mp3", ".mp4", ".zip",
}
IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache"}
PATTERNS = {
    "秘密鍵": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHubトークン形式": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b"),
    "APIトークン形式": re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{32,}\b"),
    "AWSアクセスキー形式": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "個人保存先": re.compile(r"/(?:Users|Volumes)/[A-Za-z0-9_.-]+"),
    "Slackトークン形式": re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{20,}\b"),
}


def main() -> int:
    findings = []
    checked = 0
    total_bytes = 0
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if any(part in IGNORED_DIRS for part in relative.parts):
            continue
        if path.is_symlink():
            findings.append((str(relative), 0, "シンボリックリンク"))
            continue
        if not path.is_file():
            continue
        checked += 1
        if (set(relative.parts) & BLOCKED_DIRS or path.suffix in BLOCKED_SUFFIXES
                or path.name.startswith((".env", "._")) or path.name == ".DS_Store"):
            findings.append((str(relative), 0, "公開対象外の種類"))
        data = path.read_bytes()
        total_bytes += len(data)
        if len(data) > 2_000_000:
            findings.append((str(relative), 0, "想定外の大容量ファイル"))
        try:
            content = data.decode("utf-8")
            if "\x00" in content:
                raise ValueError("binary")
        except (UnicodeError, ValueError):
            findings.append((str(relative), 0, "テキスト以外"))
            continue
        for number, line in enumerate(content.splitlines(), 1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append((str(relative), number, label))
        if path.suffix == ".py":
            try:
                ast.parse(content, filename=str(relative))
            except SyntaxError as error:
                findings.append((str(relative), error.lineno or 0, "Python構文エラー"))
    manifest = json.loads((ROOT / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
    seen = set()
    for entry in manifest["source_files"]:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or str(relative) in seen:
            findings.append(("SOURCE_MANIFEST.json", 0, "不正・重複パス"))
            continue
        seen.add(str(relative))
        path = ROOT / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT):
            findings.append((str(relative), 0, "抽出ソース欠落・範囲外"))
            continue
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["public_sha256"] or len(data) != entry["bytes"]:
            findings.append((str(relative), 0, "抽出ソースの整合性不一致"))
    print(json.dumps({"checked_files": checked, "total_bytes": total_bytes,
                      "source_files": len(seen), "findings": findings}, ensure_ascii=False, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
