from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Final


MAX_TTS_TEXT_CHARS: Final[int] = 120

# Fullwidth digits / decimal point → ASCII so decimal + number readers stay consistent.
_FULLWIDTH_DIGIT_TRANSLATION: Final = str.maketrans(
    "０１２３４５６７８９．",
    "0123456789.",
)

_URL_TOKEN_RE: Final = re.compile(
    r"\b(?:(?:https?|ftp)://|www\.)[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+",
)

_DASH_DATE_RE: Final = re.compile(
    r"(?<!\d)(?P<year>\d{4})-(?P<month>0?[1-9]|1[0-2])-(?P<day>0?[1-9]|[12]\d|3[01])(?!\d)",
)
_SLASH_DATE_RE: Final = re.compile(
    r"(?<!\d)(?P<year>\d{4})/(?P<month>0?[1-9]|1[0-2])/(?P<day>0?[1-9]|[12]\d|3[01])(?!\d)",
)
_TIME_RE: Final = re.compile(r"(?<!\d)(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?(?!\d)")
# Software versions like v1.2 / 1.2.3 — before decimal reader so "v1.二" cannot appear.
_VERSION_RE: Final = re.compile(
    r"(?<![A-Za-z0-9_])(?P<prefix>[vV])?(?P<head>\d+)(?P<rest>(?:\.\d+){1,3})(?![A-Za-z0-9_])"
)
_DECIMAL_RE: Final = re.compile(r"(?<![A-Za-z0-9_])(?P<integer>\d+)\.(?P<fraction>\d+)(?![A-Za-z0-9])")
_INTEGER_RE: Final = re.compile(r"(?<![A-Za-z0-9_])(?P<number>\d+)(?![A-Za-z0-9_])")
# Byte/time units glued to digits (10GB, 512MB, 16ms).
_NUMBER_UNIT_RE: Final = re.compile(
    r"(?<![A-Za-z0-9_])(?P<number>\d+)(?P<unit>GB|MB|KB|TB|gb|mb|kb|tb|MS|ms|Hz|HZ|hz)(?![A-Za-z0-9_])"
)
# Mixed alnum tokens (ABC123, ID42) — speak letters then digits.
_ALNUM_TOKEN_RE: Final = re.compile(
    r"(?<![A-Za-z0-9_])(?P<token>(?:[A-Za-z]{2,}\d+|\d+[A-Za-z]{2,}))(?![A-Za-z0-9_])"
)
# Uppercase or known mixed-case English acronyms (AI, WiFi, VRAM…).
_ACRONYM_RE: Final = re.compile(
    r"(?<![A-Za-z0-9_])(?P<token>[A-Z]{2,}|WiFi|Wi-Fi|Ok|OK)(?![A-Za-z0-9_])"
)
_THOUSANDS_SEP_RE: Final = re.compile(r"(?<=\d)[,，](?=\d{3}(?:\D|$))")
_FULLWIDTH_PERCENT_RE: Final = re.compile(r"[％%]")

_URL_MAP: Final = {
    "https": "エイチティーティーピーエス",
    "http": "エイチティーティーピー",
    "ftp": "エフティーピー",
}

_SYMBOL_MULTI_RE: Final = (
    ("...", "。"),
    ("…", "。"),
    ("!!", "。"),
    ("??", "。"),
    ("--", "ー"),
)

_SYMBOL_SINGLE_REPLACE: Final = {
    "#": "シャープ",
    "@": "アット",
    "%": "パーセント",
    "％": "パーセント",
    "&": "アンド",
    "*": "アスタリスク",
    "+": "プラス",
    "=": "イコール",
    "_": "アンダースコア",
    "-": "ハイフン",
    "|": "パイプ",
    "/": "スラッシュ",
    "\\": "バックスラッシュ",
    ",": "、",
    ";": "、",
    ":": "：",
    "?": "？",
    "!": "。",
    "¥": "円",
    "￥": "円",
    "$": "ドル",
    "€": "ユーロ",
    "℃": "度",
    "°": "度",
    "〜": "から",
    "±": "プラスマイナス",
    "×": "かける",
    "÷": "わる",
    "^": "の",
    "※": "注記",
    "（": "",
    "）": "",
    "(": "",
    ")": "",
    "[": "",
    "]": "",
    "{": "",
    "}": "",
    "<": "",
    ">": "",
    "„": "",
    "\"": "",
    "'": "",
    "`": "",
}

_NUMBER_TO_JAPANESE: Final = {
    "0": "ゼロ",
    "1": "一",
    "2": "二",
    "3": "三",
    "4": "四",
    "5": "五",
    "6": "六",
    "7": "七",
    "8": "八",
    "9": "九",
}

_LETTER_TO_JAPANESE: Final = {
    "A": "エー",
    "B": "ビー",
    "C": "シー",
    "D": "ディー",
    "E": "イー",
    "F": "エフ",
    "G": "ジー",
    "H": "エッチ",
    "I": "アイ",
    "J": "ジェイ",
    "K": "ケイ",
    "L": "エル",
    "M": "エム",
    "N": "エヌ",
    "O": "オー",
    "P": "ピー",
    "Q": "キュー",
    "R": "アール",
    "S": "エス",
    "T": "ティー",
    "U": "ユー",
    "V": "ブイ",
    "W": "ダブリュー",
    "X": "エックス",
    "Y": "ワイ",
    "Z": "ゼット",
}

# Common project/robotics abbreviations with explicit pronunciation.
_ACRONYM_PRONUNCIATION: Final[dict[str, str]] = {
    "AI": "エーアイ",
    "LLM": "エルエルエム",
    "API": "エーピーアイ",
    "URL": "ユーアールエル",
    "HTTP": "エイチティーティーピー",
    "HTTPS": "エイチティーティーピーエス",
    "TTS": "ティーティーエス",
    "VRM": "ブイアールエム",
    "VRAM": "ブイラム",
    "GPU": "ジーピーユー",
    "CPU": "シーピーユー",
    "FPS": "エフピーエス",
    "JSON": "ジェイソン",
    "UUID": "ユーユーアイディー",
    "SSD": "エスエスディー",
    "HDD": "エイチディーディー",
    "RAM": "ラム",
    "USB": "ユーエスビー",
    "WIFI": "ワイファイ",
    "WI-FI": "ワイファイ",
    "OK": "オーケー",
    "ID": "アイディー",
    "GB": "ギガバイト",
    "MB": "メガバイト",
    "KB": "キロバイト",
    "TB": "テラバイト",
    "MS": "ミリ秒",
    "HZ": "ヘルツ",
    "SENA": "ルミナ",
    "TOHA": "トハ",
    "PIPER": "パイパー",
    "VOICEVOX": "ボイスボックス",
}

_UNIT_PRONUNCIATION: Final[dict[str, str]] = {
    "GB": "ギガバイト",
    "MB": "メガバイト",
    "KB": "キロバイト",
    "TB": "テラバイト",
    "MS": "ミリ秒",
    "HZ": "ヘルツ",
}

# Optional user dictionary that can be extended at runtime.
_USER_DICTIONARY_HOOKS: dict[str, str] = {}


def register_user_dictionary_hooks(entries: Mapping[str, str]) -> None:
    """Register additional dictionary entries used by :func:`normalize_for_tts_japanese`.

    Entries with longer source strings are applied first for deterministic behavior.
    """

    for source, replacement in entries.items():
        src = str(source)
        if not src:
            continue
        _USER_DICTIONARY_HOOKS[src] = str(replacement)


def clear_user_dictionary_hooks() -> None:
    """Clear runtime user dictionary hooks."""

    _USER_DICTIONARY_HOOKS.clear()


def get_user_dictionary_hooks() -> dict[str, str]:
    """Return a copy of active user dictionary hooks."""

    return dict(_USER_DICTIONARY_HOOKS)


def _apply_dictionary(text: str, dictionary: Mapping[str, str] | None = None) -> str:
    replacements: list[tuple[str, str]] = []
    if dictionary:
        replacements.extend((str(k), str(v)) for k, v in dictionary.items() if str(k))
    replacements.extend((str(k), str(v)) for k, v in _USER_DICTIONARY_HOOKS.items() if str(k))
    for source, replacement in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        if source and replacement is not None:
            text = text.replace(source, replacement)
    return text


def _normalize_url_token(token: str) -> str:
    lower_token = token.lower()
    prefix = ""
    rest = token

    if "://" in lower_token:
        scheme, rest = token.split("://", 1)
        prefix = _URL_MAP.get(scheme.lower(), scheme.upper()) + "コロン スラッシュ スラッシュ"
    elif lower_token.startswith("www."):
        prefix = "ダブリュー ダブリュー ダブリュー"

    out_parts: list[str] = []
    if prefix:
        out_parts.append(prefix)

    for ch in rest:
        if ch in ".,-/:":
            if ch == ".":
                out_parts.append("ドット")
            elif ch == ",":
                out_parts.append("、")
            elif ch == "/":
                out_parts.append("スラッシュ")
            elif ch == "-":
                out_parts.append("ハイフン")
            elif ch == ":":
                out_parts.append("コロン")
        elif ch in {"?", "&", "=", "%", "_", "+", "#", "@", "~"}:
            out_parts.append(_SYMBOL_SINGLE_REPLACE.get(ch, ch))
        elif ch in _NUMBER_TO_JAPANESE:
            out_parts.append(_NUMBER_TO_JAPANESE[ch])
        else:
            out_parts.append(ch)

    return _normalize_whitespace(" ".join(part for part in out_parts if part))


def _normalize_urls(text: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        return f" {_normalize_url_token(match.group(0))} "

    return _URL_TOKEN_RE.sub(replacer, text)


def _read_under_10000(value: int) -> str:
    if value == 0:
        return ""

    chunks: list[str] = []
    n = value

    thousands = n // 1000
    if thousands:
        if thousands > 1:
            chunks.append(_NUMBER_TO_JAPANESE[str(thousands)] + "千")
        else:
            chunks.append("千")
        n %= 1000

    hundreds = n // 100
    if hundreds:
        if hundreds > 1:
            chunks.append(_NUMBER_TO_JAPANESE[str(hundreds)] + "百")
        else:
            chunks.append("百")
        n %= 100

    tens = n // 10
    if tens:
        if tens > 1:
            chunks.append(_NUMBER_TO_JAPANESE[str(tens)] + "十")
        elif tens == 1:
            chunks.append("十")
        n %= 10

    if n:
        chunks.append(_NUMBER_TO_JAPANESE[str(n)])

    return "".join(chunks)


def _read_japanese_number(value: int) -> str:
    if value == 0:
        return "ゼロ"

    if value < 0:
        return "マイナス" + _read_japanese_number(-value)

    units = ["", "万", "億", "兆", "京", "垓", "秭", "穣", "溝", "澗", "正", "載", "極"]
    parts: list[str] = []
    n = value
    index = 0
    while n > 0:
        n, chunk = divmod(n, 10000)
        if chunk:
            spoken = _read_under_10000(chunk)
            parts.append(spoken + units[index])
        index += 1

    return "".join(reversed(parts))


def _read_digits(value: str) -> str:
    return "".join(_NUMBER_TO_JAPANESE[d] for d in value)


def _strip_thousands_separators(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = _THOUSANDS_SEP_RE.sub("", text)
    return text


def _normalize_decimal_number(text: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        integer = match.group("integer")
        fraction = match.group("fraction")

        if any(ch not in "0123456789" for ch in integer + fraction):
            return match.group(0)

        integer_part = _read_japanese_number(int(integer))
        decimal_part = "".join(_NUMBER_TO_JAPANESE[d] for d in fraction)
        return integer_part + "点" + decimal_part

    return _DECIMAL_RE.sub(replacer, text)


def _normalize_versions(text: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        prefix = match.group("prefix") or ""
        head = match.group("head")
        rest = match.group("rest")
        spoken_prefix = "ブイ" if prefix else ""
        parts = [head, *[chunk for chunk in rest.split(".") if chunk]]
        spoken_parts: list[str] = []
        for part in parts:
            if part.startswith("0") and len(part) > 1:
                spoken_parts.append(_read_digits(part))
            else:
                spoken_parts.append(_read_japanese_number(int(part)))
        return spoken_prefix + "点".join(spoken_parts)

    return _VERSION_RE.sub(replacer, text)


def _normalize_number_units(text: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        number = match.group("number")
        unit = match.group("unit").upper()
        spoken_unit = _UNIT_PRONUNCIATION.get(unit, unit)
        if number.startswith("0") and len(number) > 1:
            spoken_number = _read_digits(number)
        else:
            spoken_number = _read_japanese_number(int(number))
        return spoken_number + spoken_unit

    return _NUMBER_UNIT_RE.sub(replacer, text)


def _speak_latin_alnum_token(token: str) -> str:
    pieces: list[str] = []
    for ch in token:
        upper = ch.upper()
        if upper in _NUMBER_TO_JAPANESE:
            pieces.append(_NUMBER_TO_JAPANESE[upper])
        elif upper in _LETTER_TO_JAPANESE:
            pieces.append(_LETTER_TO_JAPANESE[upper])
        else:
            pieces.append(ch)
    return "".join(pieces)


def _normalize_alnum_tokens(text: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        return _speak_latin_alnum_token(match.group("token"))

    return _ALNUM_TOKEN_RE.sub(replacer, text)


def _normalize_fullwidth_percent(text: str) -> str:
    return _FULLWIDTH_PERCENT_RE.sub("パーセント", text)


def _normalize_numbers(text: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        token = match.group("number")
        # Zero-padded tokens (01, 007, 090…) read digit-by-digit.
        if token.startswith("0") and len(token) > 1:
            return _read_digits(token)
        return _read_japanese_number(int(token))

    return _INTEGER_RE.sub(replacer, text)


def _normalize_dates(text: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        if month > 12 or day > 31:
            return match.group(0)
        return f"{_read_japanese_number(year)}年{_read_japanese_number(month)}月{_read_japanese_number(day)}日"

    text = _DASH_DATE_RE.sub(replacer, text)
    return _SLASH_DATE_RE.sub(replacer, text)


def _normalize_time(text: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        hour = int(match.group("hour"))
        minute = int(match.group("minute"))
        second = match.group("second")

        if hour > 23 or minute > 59:
            return match.group(0)
        if second is not None and int(second) > 59:
            return match.group(0)

        output = f"{_read_japanese_number(hour)}時"
        if minute:
            output += f"{_read_japanese_number(minute)}分"
        if second:
            output += f"{_read_japanese_number(int(second))}秒"
        return output

    return _TIME_RE.sub(replacer, text)


def _normalize_acronyms(text: str, extra_acronyms: Mapping[str, str] | None = None) -> str:
    merged = dict(_ACRONYM_PRONUNCIATION)
    if extra_acronyms:
        for source, replacement in extra_acronyms.items():
            key = str(source).upper().replace(" ", "")
            merged[key] = str(replacement)

    def replacer(match: re.Match[str]) -> str:
        token = match.group("token")
        normalized = token.upper().replace(" ", "")
        explicit = merged.get(normalized)
        if explicit:
            return explicit

        pieces: list[str] = []
        for ch in normalized:
            if ch == "-":
                continue
            if ch in _NUMBER_TO_JAPANESE:
                pieces.append(_NUMBER_TO_JAPANESE[ch])
            else:
                pieces.append(_LETTER_TO_JAPANESE.get(ch, ch))
        return "".join(pieces)

    return _ACRONYM_RE.sub(replacer, text)


def _normalize_symbols(text: str) -> str:
    for old, new in _SYMBOL_MULTI_RE:
        text = text.replace(old, new)

    out: list[str] = []
    for ch in text:
        replacement = _SYMBOL_SINGLE_REPLACE.get(ch)
        if replacement is None:
            out.append(ch)
            continue
        if replacement:
            out.append(replacement)

    return _normalize_whitespace("".join(out))


def _normalize_whitespace(text: str) -> str:
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(?<=[ぁ-んァ-ン一-龥])\s+(?=[ぁ-んァ-ン一-龥])", "", text)
    return text.strip()


def _truncate_to_length(text: str, max_chars: int | None) -> str:
    if max_chars is None or max_chars <= 0:
        return text
    max_chars = int(max_chars)
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 1] + "…"


def normalize_for_tts_japanese(
    text: str | None,
    *,
    user_dictionary: Mapping[str, str] | None = None,
    acronym_dictionary: Mapping[str, str] | None = None,
    max_chars: int | None = None,
) -> str:
    """Normalize Japanese text for deterministic TTS pronunciation.

    The function intentionally scopes changes to non-Japanese script tokens:
    URLs, explicit acronyms, punctuation/symbols, numerals, dates, and times.
    """

    if text is None:
        return ""

    normalized = str(text)
    if not normalized:
        return ""

    normalized = _apply_dictionary(normalized, user_dictionary)
    # Normalize fullwidth digits / decimal point before numeric readers run.
    normalized = normalized.translate(_FULLWIDTH_DIGIT_TRANSLATION)
    normalized = _normalize_fullwidth_percent(normalized)
    normalized = _strip_thousands_separators(normalized)
    normalized = _normalize_urls(normalized)
    normalized = _normalize_dates(normalized)
    normalized = _normalize_time(normalized)
    # Versions / glued units before generic decimal+integer readers.
    normalized = _normalize_versions(normalized)
    normalized = _normalize_number_units(normalized)
    normalized = _normalize_decimal_number(normalized)
    normalized = _normalize_alnum_tokens(normalized)
    normalized = _normalize_acronyms(normalized, acronym_dictionary)
    normalized = _normalize_symbols(normalized)
    normalized = _normalize_numbers(normalized)
    normalized = _normalize_whitespace(normalized)
    normalized = _truncate_to_length(normalized, max_chars)

    return normalized


normalize_tts_text = normalize_for_tts_japanese
normalize_for_tts = normalize_for_tts_japanese
normalize_japanese_tts_text = normalize_for_tts_japanese


def normalize_tts_text_without_acronyms(
    text: str | None,
    *,
    user_dictionary: Mapping[str, str] | None = None,
    max_chars: int | None = None,
) -> str:
    return normalize_for_tts_japanese(
        text,
        user_dictionary=user_dictionary,
        acronym_dictionary={},
        max_chars=max_chars,
    )
