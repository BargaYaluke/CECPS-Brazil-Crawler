"""葡萄牙语 → 中文 离线翻译(标的梗概)。

巴西采购标的(``objeto_compra``)高度模板化,所以用一本人工编的术语/短语词典
(``config/translation_pt_zh.yaml``)+ 规则替换,就能把多数标的翻成可读中文梗概
—— 不接外部 API。

匹配机制(关键设计):
    * **重音/大小写不敏感匹配,但在原文上替换** —— 命中的词组换成中文,**没命中的
      部分保留原文**(原样大小写 + 重音),不会出现"去重音后更难看"的问题。
    * 词典 key 写自然葡语;每个字母编译成"接受各重音变体"的字符类(à/á/â/ã→a 等),
      空格编译成 ``\\s+``,所以匹配对重音、大小写、多空格都鲁棒。
    * phrases 按 yaml 顺序替换(长模板句放前面先吃掉);terms 按词长降序整词替换。

质量:模板化文本(绝大多数)译文可读;残留的专有名词保留葡语原文。足够"快速判断
这是什么标"。
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import httpx
import yaml

from ..core.logger import logger
from ..core.settings import DEFAULT_CONFIG_DIR, get_translation_settings


def text_hash(text: str) -> str:
    """原文 → sha1 hex(翻译缓存主键,去重用)。"""
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()

# 基础字母 → 其重音变体字符类(用于构造重音不敏感正则)
_ACCENT_CLASS = {
    "a": "aàáâãä",
    "c": "cç",
    "e": "eéêëè",
    "i": "iíîïì",
    "o": "oóôõöò",
    "u": "uúûüù",
    "n": "nñ",
}


def _norm(s: str) -> str:
    """转小写 + 去重音(仅用于把 yaml key 规整成构造正则的基准)。"""
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _build_pattern(norm_key: str, *, word_boundary: bool) -> re.Pattern[str]:
    """把规整后的葡语 key 编译成 重音/大小写不敏感 的正则。"""
    parts: list[str] = []
    for ch in norm_key:
        if ch == " ":
            parts.append(r"\s+")
        elif ch in _ACCENT_CLASS:
            parts.append(f"[{_ACCENT_CLASS[ch]}]")
        elif ch.isalnum():
            parts.append(ch)
        else:
            parts.append(re.escape(ch))
    body = "".join(parts)
    if word_boundary:
        body = rf"\b{body}\b"
    return re.compile(body, re.IGNORECASE)


@lru_cache(maxsize=2)
def _load_rules(
    config_dir: Path | None = None,
) -> tuple[list[tuple[re.Pattern[str], str]], list[tuple[re.Pattern[str], str]]]:
    """加载并编译词典,返回 ``(phrase_rules, term_rules)``。

    phrase 保序;term 按 key 长度降序(长词优先,避免被短词截断)。
    """
    base = config_dir or DEFAULT_CONFIG_DIR
    path = base / "translation_pt_zh.yaml"
    if not path.exists():
        return [], []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    phrase_rules = [
        (_build_pattern(_norm(pt), word_boundary=False), zh)
        for pt, zh in (data.get("phrases") or [])
    ]
    terms_sorted = sorted(
        ((pt, zh) for pt, zh in (data.get("terms") or {}).items()),
        key=lambda kv: -len(_norm(kv[0])),
    )
    term_rules = [(_build_pattern(_norm(pt), word_boundary=True), zh) for pt, zh in terms_sorted]
    return phrase_rules, term_rules


def translate_objeto(
    text: str | None,
    *,
    max_len: int = 120,
    config_dir: Path | None = None,
) -> str | None:
    """把葡语标的翻成中文梗概;空 → ``None``。

    Args:
        text: 葡语标的(``objeto_compra``)。
        max_len: 梗概最大长度(截断,超出加省略号)。
        config_dir: 词典目录覆盖(测试用)。

    Returns:
        中文梗概(术语已译,残留专有名词保留**原文**);``text`` 为空返回 ``None``。
    """
    if not text or not text.strip():
        return None

    phrase_rules, term_rules = _load_rules(config_dir)
    s = re.sub(r"\s+", " ", text).strip()

    for patt, zh in phrase_rules:  # 保序:长模板句先替换
        s = patt.sub(zh, s)
    for patt, zh in term_rules:  # 长词优先,整词边界
        s = patt.sub(zh, s)

    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[:max_len].rstrip() + "…"
    return s or None


# ─── DeepSeek 在线批量翻译(OpenAI 兼容)─────────────────────────────────

_SYSTEM_PROMPT = (
    "你是巴西政府采购葡译中助手。把每条葡萄牙语招标标的翻成简洁、准确的中文梗概,"
    "保留关键品类/服务/金额信息,去掉冗长法律样板。"
    "严格只输出一个 JSON 数组(字符串列表),长度和顺序与输入完全一致,不要任何额外文字或解释。"
)


def _strip_code_fence(s: str) -> str:
    """去掉模型偶尔包的 ```json ... ``` 代码围栏。"""
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


async def deepseek_translate_batch(
    texts: list[str],
    *,
    client: httpx.AsyncClient | None = None,
) -> list[str] | None:
    """用 DeepSeek(OpenAI 兼容)一次翻一批葡语标的,返回等长中文列表。

    Args:
        texts: 葡语标的列表。
        client: 复用的 ``httpx.AsyncClient``;``None`` 时内建临时实例。

    Returns:
        等长中文译文列表;**任何失败(无 key / HTTP 错 / JSON 不合法 / 长度不符)
        返回 ``None``**,由调用方回退到离线词典。
    """
    if not texts:
        return []
    cfg = get_translation_settings()
    api_key = cfg.get("api_key") or ""
    if not api_key:
        logger.warning("translate.deepseek.no_api_key")
        return None

    max_chars = int(cfg["max_chars"])
    numbered = [f"{i + 1}. {t.strip()[:max_chars]}" for i, t in enumerate(texts)]
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(numbered)},
        ],
        "temperature": 0,
        "stream": False,
    }
    url = str(cfg["base_url"]).rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async def _call(c: httpx.AsyncClient) -> list[str] | None:
        try:
            r = await c.post(url, json=payload, headers=headers, timeout=float(cfg["timeout"]))
        except httpx.HTTPError as exc:
            logger.bind(error=str(exc)).warning("translate.deepseek.http_error")
            return None
        if r.status_code != 200:
            logger.bind(status=r.status_code, body=r.text[:200]).warning("translate.deepseek.bad_status")
            return None
        try:
            content = r.json()["choices"][0]["message"]["content"]
            arr = json.loads(_strip_code_fence(content))
        except (KeyError, IndexError, ValueError) as exc:
            logger.bind(error=str(exc)).warning("translate.deepseek.parse_failed")
            return None
        if not isinstance(arr, list) or len(arr) != len(texts):
            logger.bind(got=len(arr) if isinstance(arr, list) else None, want=len(texts)).warning(
                "translate.deepseek.length_mismatch"
            )
            return None
        return [str(x).strip() for x in arr]

    if client is not None:
        return await _call(client)
    async with httpx.AsyncClient() as c:
        return await _call(c)


async def translate_texts(
    texts: list[str],
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[list[str], list[str]]:
    """翻一组文本,返回 ``(译文列表, 来源列表)``,来源为模型名或 ``"offline"``。

    策略(最大化 API 覆盖 + 隔离坏样本):
        1. 整批调 DeepSeek;成功 → 全部用 API 译文。
        2. 失败且 >1 条 → **二分递归**(常见失败是模型把某条拆/并导致长度不符,
           拆小后多半能成,坏样本被孤立)。
        3. 单条仍失败 → 离线词典兜底。
    无 API key 时直接全部离线(不空跑递归)。
    """
    if not texts:
        return [], []
    cfg = get_translation_settings()
    model = str(cfg["model"])
    if not cfg.get("api_key"):
        return [translate_objeto(t) or "" for t in texts], ["offline"] * len(texts)

    zhs = await deepseek_translate_batch(texts, client=client)
    if zhs is not None:
        return zhs, [model] * len(texts)
    if len(texts) == 1:
        return [translate_objeto(texts[0]) or ""], ["offline"]
    mid = len(texts) // 2
    l_zh, l_m = await translate_texts(texts[:mid], client=client)
    r_zh, r_m = await translate_texts(texts[mid:], client=client)
    return l_zh + r_zh, l_m + r_m


__all__ = ["translate_objeto", "deepseek_translate_batch", "translate_texts", "text_hash"]
