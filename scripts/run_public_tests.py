#!/usr/bin/env python3
"""公開スナップショットのオフライン試験を安全寄りの設定で実行する。

既定は標準ライブラリ + pytest のみで動く代表 4 スイート。
--extended は同梱する 24 スイートを対象にする。モデル、Godot、外部通信は不要。
この監査フックは誤接続・誤起動を検出する補助であり、悪意ある Python コードを
隔離する完全なサンドボックスではない。信頼できない試験の実行には使わない。
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import os
from pathlib import Path
import sys
import tempfile


REPOSITORY = Path(__file__).resolve().parents[1]
MINIMAL_TESTS = (
    "tests/test_portable_navigation_contract_v1.py",
    "tests/test_portable_navigation_core_v1.py",
    "tests/test_visual_direct_request_20260908.py",
    "tests/test_visual_label_normalization_20260906.py",
)
BLOCKED_EVENTS = frozenset({
    "socket.connect", "socket.connect_ex", "socket.bind", "socket.sendto",
    "socket.sendmsg", "socket.getaddrinfo", "socket.gethostbyname",
    "socket.gethostbyaddr", "subprocess.Popen", "os.system", "os.posix_spawn",
    "os.exec", "os.fork", "os.forkpty",
})
ENV_PREFIXES = (
    "LUMINA_", "TOHA_", "KARAKURI_", "IRODORI_", "SI_GODOT_", "GODOT_",
    "DISCORD_", "OPENAI_", "OLLAMA_", "HF_", "HUGGINGFACE_", "PYTEST_",
)


def _external_output(raw: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if path.is_relative_to(REPOSITORY):
        raise argparse.ArgumentTypeError("ログ/XML は公開リポジトリの外を指定してください")
    if path.exists():
        raise argparse.ArgumentTypeError("既存ファイルは上書きしません。新しい出力名を指定してください")
    if not path.parent.is_dir():
        raise argparse.ArgumentTypeError("出力先の親ディレクトリがありません")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extended", action="store_true", help="同梱の 24 スイートを実行")
    parser.add_argument("--xml", type=_external_output, help="外部の新規 JUnit XML 出力先")
    parser.add_argument("--log", type=_external_output, help="外部の新規テキストログ出力先")
    args = parser.parse_args()
    if args.xml is not None and args.xml == args.log:
        parser.error("ログと XML には異なる出力先を指定してください")

    # インストール済みプラグインや既存の運転設定を試験へ持ち込まない。
    inherited_pythonpath = {
        Path(entry or os.getcwd()).resolve()
        for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep)
        if "PYTHONPATH" in os.environ
    }
    sys.path[:] = [entry for entry in sys.path if Path(entry or os.getcwd()).resolve() not in inherited_pythonpath]
    for key in tuple(os.environ):
        if key.startswith(ENV_PREFIXES) or key in {"PYTHONPATH", "PYTHONSTARTUP"}:
            os.environ.pop(key, None)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    os.chdir(REPOSITORY)
    sys.path.insert(0, str(REPOSITORY))
    denied: list[str] = []

    def audit(event: str, _args: tuple) -> None:
        if event in BLOCKED_EVENTS:
            denied.append(event)
            raise RuntimeError(f"公開オフライン試験が禁止操作を検出: {event}")

    with ExitStack() as stack:
        scratch = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="lumina-public-test-"))).resolve()
        os.environ["LUMINA_RUNTIME_ROOT"] = str(scratch)
        os.environ["LUMINA_NEXT_CONFIG_PATH"] = str(scratch / "absent-config.json")
        os.environ["LUMINA_NEXT_SQLITE_PATH"] = str(scratch / "test.sqlite3")
        os.environ["LUMINA_NEXT_MEMORY_WRITE"] = "0"
        config = scratch / "pytest.ini"
        config.touch()
        if args.log is not None:
            log = stack.enter_context(args.log.open("x", encoding="utf-8"))
            stack.enter_context(redirect_stdout(log))
            stack.enter_context(redirect_stderr(log))
        sys.addaudithook(audit)
        import pytest

        pytest_args = [
            "-q", "-ra", "-p", "no:cacheprovider", "-c", str(config),
            "--rootdir", str(REPOSITORY), "--confcutdir", str(REPOSITORY),
            "--basetemp", str(scratch / "pytest"),
        ]
        if args.xml is not None:
            pytest_args.append("--junitxml=" + str(args.xml))
        pytest_args.extend(["tests"] if args.extended else MINIMAL_TESTS)
        result = int(pytest.main(pytest_args))
        print(f"\n実ネットワーク/実プロセスの禁止操作検出: {len(denied)} 件")
        if denied:
            print("検出種別: " + ", ".join(sorted(set(denied))))
            return result or 1
        return result


if __name__ == "__main__":
    raise SystemExit(main())
