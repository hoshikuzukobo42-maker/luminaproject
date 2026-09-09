from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .config import load_runtime_config
from .karakuri_world import KarakuriWorldClient, KarakuriWorldConfig, KarakuriWorldError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Operator-only lifecycle commands for Lumina on Karakuri World",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    login = subparsers.add_parser("login", help="Log Lumina into Karakuri World")
    login.add_argument("--node-id", default=None, help="Optional restore node, for example 3-2")
    subparsers.add_parser("logout", help="Log Lumina out of Karakuri World")
    subparsers.add_parser("config", help="Show non-secret integration configuration")
    return parser


async def _run(args: argparse.Namespace) -> dict:
    runtime = load_runtime_config(Path(__file__).resolve().parents[1])
    config = KarakuriWorldConfig.from_env(runtime.runtime_root)
    if args.command == "config":
        return {
            "ok": config.ready,
            "api_base": config.api_base,
            "api_key_present": bool(config.api_key),
            "webhook_secret_present": bool(config.webhook_secret),
            "ledger_path": str(config.ledger_path),
        }
    client = KarakuriWorldClient(config)
    if args.command == "login":
        return await client.login(args.node_id)
    if args.command == "logout":
        return await client.logout()
    raise KarakuriWorldError(f"unsupported lifecycle command: {args.command}")


def main() -> int:
    args = _parser().parse_args()
    try:
        result = asyncio.run(_run(args))
    except KarakuriWorldError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
