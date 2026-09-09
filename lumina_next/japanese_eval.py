"""Local Japanese conversation quality evaluation without external APIs."""

from __future__ import annotations

import csv
import io
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence


Category = str
Evaluator = Callable[[str, Mapping[str, object]], tuple[float, tuple[str, ...]]]


@dataclass(frozen=True)
class EvalCase:
    category: Category
    prompt: str
    constraints: Mapping[str, object]
    seed: int


@dataclass(frozen=True)
class EvalResult:
    category: Category
    score: float
    notes: tuple[str, ...]
    passed: bool


_CATEGORIES: tuple[Category, ...] = (
    "daily_chat",
    "keigo",
    "emotional_support",
    "technical_explanation",
    "small_talk",
    "joke",
    "roleplay",
    "disagreement",
    "ambiguous_request",
    "long_context",
    "memory_reference",
    "conversation_continuation",
    "over_familiarity",
    "template_phrase",
    "excess_bullets",
    "same_sentence_ending",
    "hallucination_guard",
    "unknown_admission",
    "ja_en_mix",
    "proper_noun",
    "numbers_and_time",
    "negation",
    "indirect_expression",
    "sarcasm",
    "casual_dialect",
)


_CATEGORY_PROMPTS: dict[Category, str] = {
    "daily_chat": "今日は少し疲れています。",
    "keigo": "来週の打合せ日程を教えてください。",
    "emotional_support": "最近、仕事のプレッシャーで眠れない日が続いています。",
    "technical_explanation": "KVキャッシュ量子化の概要を説明してください。",
    "small_talk": "週末は何をして過ごすのが好きですか。",
    "joke": "寒いですね。冗談を一つ言ってください。",
    "roleplay": "カフェの店員として、注文を受け付けてください。",
    "disagreement": "この案は絶対に正しいと思います。どう思いますか。",
    "ambiguous_request": "それ、なんとかして。",
    "long_context": "先ほど話した3つの課題について、優先順位を整理してください。",
    "memory_reference": "先週お話ししたプロジェクト名を覚えていますか。",
    "conversation_continuation": "それで、その後どうなったんですか。",
    "over_familiarity": "初対面ですが、タメ口で話してもらえますか。",
    "template_phrase": "お疲れ様です。今日の調子はいかがですか。",
    "excess_bullets": "今日の予定を3つ教えてください。",
    "same_sentence_ending": "最近の趣味について3文で話してください。",
    "hallucination_guard": "2028年のノーベル物理学賞受賞者を教えてください。",
    "unknown_admission": "2031年の日本の総理大臣は誰ですか。",
    "ja_en_mix": "Please explain this in Japanese only.",
    "proper_noun": "東京都渋谷区の観光スポットを紹介してください。",
    "numbers_and_time": "来週火曜日の14時30分からの会議について確認したいです。",
    "negation": "その方法は使えないと言われたのですが、本当でしょうか。",
    "indirect_expression": "この資料、もう少し丁寧に書いてもらえると助かるのですが。",
    "sarcasm": "すごいですね、また徹夜ですか。本当に素晴らしい生活ですね。",
    "casual_dialect": "関西弁っぽく、親しみやすく返事してください。",
}


def _score_keigo(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    polite = len(re.findall(r"(?:です|ます|でしょう|ください|いただ|いたし|ござい)", text))
    rude = len(re.findall(r"(?:だぜ|だよな|しね|くそ)", text))
    score = min(1.0, polite / 2.0) - min(0.5, rude * 0.25)
    notes = () if score >= 0.5 else ("keigo markers insufficient",)
    return max(0.0, score), notes


def _score_no_bullets(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    bullets = len(re.findall(r"(?m)^\s*[-*+]", text))
    score = 1.0 if bullets == 0 else max(0.0, 1.0 - bullets * 0.3)
    notes = ("contains bullet list",) if bullets else ()
    return score, notes


def _score_unknown_admission(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    markers = ("わかりません", "不明", "確認できません", "情報がありません", "断定できません")
    if any(marker in text for marker in markers):
        return 1.0, ()
    if re.search(r"(?:確実に|間違いなく|100%)", text):
        return 0.2, ("overconfident without evidence",)
    return 0.6, ()


def _score_hallucination_guard(text: str, constraints: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    return _score_unknown_admission(text, constraints)


def _score_ending_variety(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    endings = re.findall(r"(.{3})$", re.split(r"[。!?？]", text)[0] if text else "")
    if not endings:
        return 0.5, ()
    repeated = len(endings) - len(set(endings))
    score = max(0.0, 1.0 - repeated * 0.4)
    notes = ("repeated endings",) if repeated else ()
    return score, notes


def _score_sarcasm(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    """Score whether the response recognizes sarcasm without taking it literally."""

    notes: list[str] = []
    literal_positive = ("素晴らしいですね", "本当にすごい", "羨ましい", "良い生活")
    recognition = (
        "皮肉",
        "冗談",
        "大変",
        "お疲れ",
        "無理",
        "心配",
        "徹夜",
        "休んで",
        "気をつけ",
        "そうですね",
        "大変ですね",
    )
    if any(marker in text for marker in recognition):
        score = 0.9
    elif any(marker in text for marker in literal_positive):
        score = 0.25
        notes.append("took sarcasm literally")
    elif re.search(r"(?:ですね|ますね|でしょう)", text):
        score = 0.65
    else:
        score = 0.5
        notes.append("sarcasm handling unclear")
    return score, tuple(notes)


def _score_indirect_expression(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    """Score soft, indirect Japanese phrasing appropriate for hedged requests."""

    soft_markers = (
        "かもしれ",
        "かも",
        "ちょっと",
        "少し",
        "恐れ入",
        "差し支え",
        "お手数",
        "いただけ",
        "いただける",
        "ご検討",
        "いかがでしょう",
        "でしょうか",
        "と思い",
        "かと",
    )
    blunt = ("ダメ", "無理", "できない", "却下", "だめです", "無理です")
    hits = sum(1 for marker in soft_markers if marker in text)
    blunt_hits = sum(1 for marker in blunt if marker in text)
    score = min(1.0, 0.45 + hits * 0.15) - min(0.4, blunt_hits * 0.2)
    notes = ()
    if hits == 0:
        notes = ("missing indirect softening markers",)
    if blunt_hits:
        notes = (*notes, "overly blunt refusal")
    return max(0.0, score), notes


def _score_casual_dialect(text: str, constraints: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    """Score casual dialect markers while penalizing rude or overly formal tone."""

    casual = (
        "やん",
        "やで",
        "やな",
        "やろ",
        "やね",
        "じゃん",
        "だね",
        "だよ",
        "っす",
        "せん",
        "へん",
        "おる",
        "ちゃう",
        "なんや",
        "せや",
        "ほな",
        "まいど",
    )
    rude = ("だぜ", "だよな", "しね", "くそ", "バカ", "アホ")
    formal = len(re.findall(r"(?:ございます|いたします|申し上げ)", text))
    casual_hits = sum(1 for marker in casual if marker in text)
    rude_hits = sum(1 for marker in rude if marker in text)
    score = min(1.0, 0.35 + casual_hits * 0.2) - min(0.5, rude_hits * 0.25) - min(0.3, formal * 0.1)
    notes: list[str] = []
    if casual_hits == 0:
        notes.append("no casual dialect markers")
    if rude_hits:
        notes.append("rude casual markers")
    if formal >= 2:
        notes.append("too formal for casual dialect")
    min_score = float(constraints.get("min_dialect_score", 0.0))
    if min_score and score < min_score:
        notes.append("below requested dialect floor")
    return max(0.0, score), tuple(notes)


def _score_daily_chat(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    notes: list[str] = []
    if len(text) < 8:
        notes.append("too short for daily chat")
        return 0.35, tuple(notes)
    empathy = sum(1 for marker in ("お疲れ", "大変", "そう", "ですね", "かも") if marker in text)
    bullets = len(re.findall(r"(?m)^\s*[-*+]", text))
    score = min(1.0, 0.55 + empathy * 0.12) - min(0.5, bullets * 0.2)
    if bullets:
        notes.append("contains bullet list")
    return max(0.0, score), tuple(notes)


def _score_emotional_support(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    markers = ("お疲れ", "大変", "つら", "辛", "応援", "寄り添", "休ん", "無理し", "大丈夫")
    hits = sum(1 for marker in markers if marker in text)
    dismissive = ("気にするな", "甘え", "自己責任", "普通です")
    dismiss_hits = sum(1 for marker in dismissive if marker in text)
    score = min(1.0, 0.4 + hits * 0.15) - min(0.5, dismiss_hits * 0.25)
    notes = ("missing empathy markers",) if hits == 0 else ()
    if dismiss_hits:
        notes = (*notes, "dismissive tone")
    return max(0.0, score), notes


def _score_technical_explanation(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    markers = ("概要", "仕組", "原理", "手順", "例", "つまり", "ため", "KV", "量子化", "キャッシュ")
    hits = sum(1 for marker in markers if marker in text)
    vague = ("詳しくは不明", "よくわかりません")
    if any(marker in text for marker in vague) and hits == 0:
        return 0.45, ("too vague for technical explanation",)
    score = min(1.0, 0.45 + hits * 0.12)
    notes = ("missing explanatory structure",) if hits == 0 else ()
    return score, notes


def _score_small_talk(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    if len(text) < 10:
        return 0.4, ("too short for small talk",)
    question = 1 if re.search(r"(?:？|\?|ですか|ますか)", text) else 0
    score = min(1.0, 0.55 + question * 0.15)
    return score, ()


def _score_joke(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    humor = ("笑", "冗談", "ウケ", "寒", "面白", "ハハ", "www", "😄")
    hits = sum(1 for marker in humor if marker in text)
    score = min(1.0, 0.45 + hits * 0.2)
    notes = ("no humor markers",) if hits == 0 else ()
    return score, notes


def _score_roleplay(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    role = ("いらっしゃい", "注文", "メニュー", "カフェ", "店員", "ご注文", "少々")
    hits = sum(1 for marker in role if marker in text)
    score = min(1.0, 0.4 + hits * 0.18)
    notes = ("roleplay markers missing",) if hits == 0 else ()
    return score, notes


def _score_disagreement(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    respectful = ("一方", "ただ", "異なる", "考え", "見解", "かもしれ", "必ずしも", "慎重")
    rude = ("間違い", "バカ", "アホ", "絶対おかしい")
    hits = sum(1 for marker in respectful if marker in text)
    rude_hits = sum(1 for marker in rude if marker in text)
    score = min(1.0, 0.45 + hits * 0.12) - min(0.5, rude_hits * 0.3)
    notes = ("missing respectful disagreement framing",) if hits == 0 else ()
    if rude_hits:
        notes = (*notes, "rude disagreement")
    return max(0.0, score), notes


def _score_ambiguous_request(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    clarify = ("どの", "具体的", "確認", "意味", "詳しく", "教えて", "どういう")
    hits = sum(1 for marker in clarify if marker in text)
    score = min(1.0, 0.45 + hits * 0.15)
    notes = ("no clarifying question",) if hits == 0 else ()
    return score, notes


def _score_long_context(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    sentences = [part for part in re.split(r"[。!?？]", text) if part.strip()]
    markers = ("優先", "課題", "整理", "順", "まず", "次に", "第一")
    hits = sum(1 for marker in markers if marker in text)
    if len(sentences) >= 2 and hits >= 1:
        return min(1.0, 0.6 + hits * 0.1), ()
    notes = ("insufficient structured long-context reply",)
    return 0.45, notes


def _score_memory_reference(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    recall = ("覚え", "以前", "先週", "前回", "話した", "プロジェクト", "記録")
    deny = ("覚えていません", "記録がありません", "確認できません")
    if any(marker in text for marker in deny):
        return 0.85, ()
    hits = sum(1 for marker in recall if marker in text)
    score = min(1.0, 0.45 + hits * 0.18)
    notes = ("no memory recall markers",) if hits == 0 else ()
    return score, notes


def _score_conversation_continuation(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    markers = ("その後", "続き", "それで", "結果", "そのあと", "結局", "最終的")
    hits = sum(1 for marker in markers if marker in text)
    score = min(1.0, 0.5 + hits * 0.15)
    notes = ("no continuation markers",) if hits == 0 else ()
    return score, notes


def _score_over_familiarity(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    tame = ("お前", "てめえ", "君", "おい", "〜だろ", "〜じゃん")
    polite = len(re.findall(r"(?:です|ます|でしょう|ください)", text))
    tame_hits = sum(1 for marker in tame if marker in text)
    score = min(1.0, polite / 3.0) - min(0.6, tame_hits * 0.25)
    notes = ("overly familiar tone",) if tame_hits else ()
    if polite == 0:
        notes = (*notes, "missing polite markers for first meeting")
    return max(0.0, score), notes


def _score_template_phrase(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    templates = (
        "お疲れ様です。今日の調子はいかがですか",
        "何かお手伝いできることはありますか",
        "ご不明な点がございましたら",
    )
    if any(template in text for template in templates):
        return 0.35, ("template phrase detected",)
    return 0.85, ()


def _score_ja_en_mix(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    english_words = re.findall(r"\b[A-Za-z]{3,}\b", text)
    if len(english_words) >= 2:
        return 0.3, ("excessive English in Japanese-only reply",)
    if english_words:
        return 0.65, ("some English present",)
    return 0.95, ()


def _score_proper_noun(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    markers = ("渋谷", "東京", "観光", "スポット", "エリア", "区")
    hits = sum(1 for marker in markers if marker in text)
    score = min(1.0, 0.45 + hits * 0.15)
    notes = ("missing location-specific detail",) if hits == 0 else ()
    return score, notes


def _score_numbers_and_time(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    has_time = bool(re.search(r"(?:\d{1,2}[:：]\d{2}|\d{1,2}時\d{0,2}分?|火曜|月曜|水曜|木曜|金曜|土曜|日曜)", text))
    has_number = bool(re.search(r"\d", text))
    if has_time and has_number:
        return 0.95, ()
    if has_time or has_number:
        return 0.75, ("partial date/time acknowledgment",)
    return 0.4, ("missing date/time reference",)


def _score_negation(text: str, _: Mapping[str, object]) -> tuple[float, tuple[str, ...]]:
    markers = ("使えない", "不可", "できない", "難しい", "条件", "例外", "確認", "場合")
    hits = sum(1 for marker in markers if marker in text)
    dismissive = ("問題ない", "必ず使える", "絶対")
    if any(marker in text for marker in dismissive):
        return 0.35, ("overconfident negation handling",)
    score = min(1.0, 0.45 + hits * 0.12)
    notes = ("weak negation handling",) if hits == 0 else ()
    return score, notes


_CATEGORY_EVALUATORS: dict[Category, Evaluator] = {
    "daily_chat": _score_daily_chat,
    "keigo": _score_keigo,
    "emotional_support": _score_emotional_support,
    "technical_explanation": _score_technical_explanation,
    "small_talk": _score_small_talk,
    "joke": _score_joke,
    "roleplay": _score_roleplay,
    "disagreement": _score_disagreement,
    "ambiguous_request": _score_ambiguous_request,
    "long_context": _score_long_context,
    "memory_reference": _score_memory_reference,
    "conversation_continuation": _score_conversation_continuation,
    "over_familiarity": _score_over_familiarity,
    "template_phrase": _score_template_phrase,
    "excess_bullets": _score_no_bullets,
    "same_sentence_ending": _score_ending_variety,
    "hallucination_guard": _score_hallucination_guard,
    "unknown_admission": _score_unknown_admission,
    "ja_en_mix": _score_ja_en_mix,
    "proper_noun": _score_proper_noun,
    "numbers_and_time": _score_numbers_and_time,
    "negation": _score_negation,
    "indirect_expression": _score_indirect_expression,
    "sarcasm": _score_sarcasm,
    "casual_dialect": _score_casual_dialect,
}


def all_categories() -> tuple[Category, ...]:
    return _CATEGORIES


def generate_eval_case(category: Category, *, seed: int) -> EvalCase:
    if category not in _CATEGORIES:
        raise ValueError(f"unknown category: {category}")
    rng = random.Random(seed)
    prompt = _CATEGORY_PROMPTS.get(category, f"{category} に関する短い応答を生成してください。")
    constraints = {"max_chars": rng.randint(120, 480), "language": "ja"}
    return EvalCase(category=category, prompt=prompt, constraints=constraints, seed=seed)


def evaluate_response(case: EvalCase, response: str) -> EvalResult:
    evaluator = _CATEGORY_EVALUATORS.get(case.category)
    if evaluator is None:
        raise ValueError(f"no evaluator registered for category: {case.category}")
    score, notes = evaluator(response, case.constraints)
    if len(response) > int(case.constraints.get("max_chars", 1000)):
        score = max(0.0, score - 0.2)
        notes = (*notes, "too long")
    passed = score >= 0.55
    return EvalResult(category=case.category, score=score, notes=notes, passed=passed)


def metamorphic_pair(case: EvalCase) -> tuple[EvalCase, EvalCase]:
    """Return two cases that should score similarly under stable behavior."""

    sibling = generate_eval_case(case.category, seed=case.seed + 10_000)
    return case, sibling


def run_category_suite(
    *,
    categories: Iterable[Category] | None = None,
    responder: Callable[[EvalCase], str],
    base_seed: int = 42,
) -> list[EvalResult]:
    selected = tuple(categories or _CATEGORIES[:8])
    results: list[EvalResult] = []
    for idx, category in enumerate(selected):
        case = generate_eval_case(category, seed=base_seed + idx)
        response = responder(case)
        results.append(evaluate_response(case, response))
    return results


def eval_result_to_csv_row(result: EvalResult, *, prompt: str = "", seed: int | None = None) -> dict[str, str]:
    return {
        "category": result.category,
        "prompt": prompt,
        "seed": "" if seed is None else str(seed),
        "score": f"{result.score:.4f}",
        "passed": "1" if result.passed else "0",
        "notes": "; ".join(result.notes),
    }


CSV_FIELDNAMES: tuple[str, ...] = ("category", "prompt", "seed", "score", "passed", "notes")


def format_eval_results_csv(
    results: Sequence[EvalResult],
    *,
    cases: Sequence[EvalCase] | None = None,
) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDNAMES)
    writer.writeheader()
    case_by_category = {case.category: case for case in cases or ()}
    for result in results:
        case = case_by_category.get(result.category)
        writer.writerow(
            eval_result_to_csv_row(
                result,
                prompt=case.prompt if case else "",
                seed=case.seed if case else None,
            )
        )
    return buffer.getvalue()


def write_eval_results_csv(
    path: str | Path,
    results: Sequence[EvalResult],
    *,
    cases: Sequence[EvalCase] | None = None,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(format_eval_results_csv(results, cases=cases), encoding="utf-8")
    return target


def run_category_suite_to_csv(
    *,
    output_path: str | Path,
    categories: Iterable[Category] | None = None,
    responder: Callable[[EvalCase], str],
    base_seed: int = 42,
) -> tuple[list[EvalResult], Path]:
    selected = tuple(categories or _CATEGORIES)
    cases = [generate_eval_case(category, seed=base_seed + idx) for idx, category in enumerate(selected)]
    results = [evaluate_response(case, responder(case)) for case in cases]
    path = write_eval_results_csv(output_path, results, cases=cases)
    return results, path


__all__ = [
    "CSV_FIELDNAMES",
    "EvalCase",
    "EvalResult",
    "all_categories",
    "evaluate_response",
    "eval_result_to_csv_row",
    "format_eval_results_csv",
    "generate_eval_case",
    "metamorphic_pair",
    "run_category_suite",
    "run_category_suite_to_csv",
    "write_eval_results_csv",
]
