# -*- coding: utf-8 -*-
"""中葡企业 ↔ 巴西政府采购竞标匹配 —— 共享库。

包含:
  * DeepSeek (deepseek-v4-flash, OpenAI 兼容) 异步客户端 + 磁盘缓存 + 并发限流 + JSON 解析兜底
  * 葡语标的 物/服/工程 粗分类(正则,去重音不敏感)
  * 关键词命中(PT 去重音子串 / ZH 子串)
  * 路径与常量
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

import httpx

# ─── 常量 / 路径 ───────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[3]          # C:\巴西爬虫 (pipeline/->matching/->analysis/->根)
# 输出目录可用环境变量 MATCH_OUT 隔离(跑不同企业数据集时避免覆盖彼此的产物/人工裁决);
# 缓存 CACHE 按提示词 hash,跨数据集共用,不隔离。
OUT = Path(os.environ["MATCH_OUT"]) if os.environ.get("MATCH_OUT") else ROOT / "analysis" / "matching" / "outputs"
CACHE = ROOT / "analysis" / "matching" / "cache"
OUT.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)


def _load_deepseek_key() -> str:
    """从环境变量读 DeepSeek key;缺失则回退解析仓库根 .env(已 gitignore)。
    不再硬编码明文密钥到源码。"""
    key = os.getenv("DEEPSEEK_API_KEY")
    if key:
        return key
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("DEEPSEEK_API_KEY=") and not s.startswith("#"):
                return s.split("=", 1)[1].strip()
    return ""


DEEPSEEK_KEY = _load_deepseek_key()
DEEPSEEK_BASE = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-flash"
TODAY = "2026-06-09"           # 时效基准日(与 report 口径一致)

# 六大行业 + 招标 主维度 别名对齐(招标用「跨境电商」,企业用「跨境商贸」)
INDUSTRIES = ["高端制造", "跨境商贸", "医疗医药", "数字经济", "大宗商贸", "文化体育"]
DIM_ALIAS = {"跨境电商": "跨境商贸"}    # 招标主维度 -> 企业六大行业


def norm_industry(x: str | None) -> str | None:
    if not x or (isinstance(x, float)):
        return None
    x = str(x).strip()
    return DIM_ALIAS.get(x, x)


# ─── 葡语 去重音 / 物服工程 分类 ───────────────────────────────────────
def deaccent(s: str) -> str:
    if not s:
        return ""
    d = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in d if not unicodedata.combining(c)).lower()


# 工程(本地土建,中企无资质,硬剔除)
_RE_WORKS = re.compile(
    r"\b(obras?|reforma|pavimenta|recapeament|drenagem|terraplenagem|"
    r"construcao de|edificacao|implantacao de|pontes?|calcament|"
    r"recuperacao de (estrada|via|rodovia|ponte)|servicos de engenharia)",
)
# 本地人力 / 纯服务(中企难直接承接;down-rank,不硬剔除)
_RE_SERVICE = re.compile(
    r"\b(servicos? de (limpeza|vigilancia|seguranca|transporte|manutencao|coleta|"
    r"recepcao|portaria|jardinagem|copeiragem|motorista|conservacao)|"
    r"mao de obra|locacao de mao|capacitacao|treinamento|assessoria|consultoria|"
    r"buffet|coffee break|recrutamento|servicos medicos|servicos odontolog|"
    r"locacao de veiculo|passagens aereas|agenciamento de viagens|seguro )",
)
# 文体活动服务(聘演员/搭台/音响)
_RE_EVENT = re.compile(
    r"\b(artistas?|banda|shows?|apresentac(ao|oes)|locacao de (palco|som|estrutura|tenda)|"
    r"sonorizacao|iluminacao para event|festej|carnaval|sao joao)",
)
# 供货信号
_RE_GOODS = re.compile(
    r"\b(aquisic|fornecimento|compra de|materia|equipament|medicament|"
    r"generos alimenticios|veicul|mobiliario|moveis|insumo)",
)


def classify_obj(objeto_pt: str | None) -> str:
    """葡语标的 -> {goods, service, works, event} 粗类。"""
    t = deaccent(objeto_pt)
    if not t:
        return "unknown"
    if _RE_WORKS.search(t):
        return "works"
    if _RE_EVENT.search(t):
        return "event"
    has_goods = bool(_RE_GOODS.search(t))
    has_svc = bool(_RE_SERVICE.search(t))
    if has_svc and not has_goods:
        return "service"
    if has_goods:
        return "goods"
    if has_svc:
        return "service"
    return "goods"   # 默认按供货(aquisição 类居多)


def kw_hits_pt(objeto_pt: str | None, kws_pt: list[str]) -> list[str]:
    """葡语关键词子串命中;复数关键词(…s)额外回退到单数形式,
    避免画像给出 'veículos' 而标的写 'VEÍCULO 0 KM' 时漏召回。"""
    t = deaccent(objeto_pt)
    if not t:
        return []
    hits = []
    for k in kws_pt:
        if not k:
            continue
        kk = deaccent(k)
        if kk in t or (kk.endswith("s") and len(kk) > 4 and kk[:-1] in t):
            hits.append(k)
    return hits


def kw_hits_zh(text_zh: str | None, kws_zh: list[str]) -> list[str]:
    t = str(text_zh or "")
    return [k for k in kws_zh if k and len(k) >= 2 and k in t]


# ─── DeepSeek 异步客户端(缓存 + 限流 + 解析兜底)─────────────────────
def _cache_path(key: str) -> Path:
    return CACHE / f"{key}.json"


def _key(system: str, user: str, model: str) -> str:
    h = hashlib.sha1(f"{model}\x00{system}\x00{user}".encode("utf-8")).hexdigest()
    return h


def _strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def extract_json(content: str) -> Any:
    """从模型输出里抠出 JSON(数组或对象);失败抛 ValueError。"""
    s = _strip_fence(content)
    try:
        return json.loads(s)
    except ValueError:
        pass
    # 找第一个 [ 或 { 到最后一个 ] 或 }
    for op, cl in (("[", "]"), ("{", "}")):
        i, j = s.find(op), s.rfind(cl)
        if 0 <= i < j:
            try:
                return json.loads(s[i : j + 1])
            except ValueError:
                continue
    raise ValueError("no json found")


async def deepseek_json(
    client: httpx.AsyncClient,
    system: str,
    user: str,
    *,
    sem: asyncio.Semaphore,
    model: str = DEEPSEEK_MODEL,
    temperature: float = 0.0,
    max_retry: int = 4,
    use_cache: bool = True,
) -> Any:
    """调 DeepSeek 返回解析好的 JSON;磁盘缓存;失败重试。失败返回 None。"""
    k = _key(system, user, model)
    cp = _cache_path(k)
    if use_cache and cp.exists():
        try:
            return json.loads(cp.read_text(encoding="utf-8"))["parsed"]
        except Exception:
            pass

    url = DEEPSEEK_BASE.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "stream": False,
        "response_format": {"type": "json_object"},
    }

    async with sem:
        for attempt in range(max_retry):
            try:
                r = await client.post(url, json=payload, headers=headers, timeout=180.0)
                if r.status_code != 200:
                    # response_format 不支持时降级重试一次
                    if r.status_code == 400 and "response_format" in payload:
                        payload.pop("response_format", None)
                        continue
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                content = r.json()["choices"][0]["message"]["content"]
                parsed = extract_json(content)
                if use_cache:
                    cp.write_text(
                        json.dumps({"parsed": parsed}, ensure_ascii=False), encoding="utf-8"
                    )
                return parsed
            except (httpx.HTTPError, ValueError, KeyError, IndexError):
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
    return None


async def run_pool(coro_factories, concurrency: int = 8):
    """并发跑一批协程工厂(() -> coro),返回结果列表(保持顺序)。"""
    sem = asyncio.Semaphore(concurrency)

    async def _wrap(i, f):
        return i, await f(sem)

    tasks = [asyncio.create_task(_wrap(i, f)) for i, f in enumerate(coro_factories)]
    out: list[Any] = [None] * len(tasks)
    done = 0
    for fut in asyncio.as_completed(tasks):
        i, res = await fut
        out[i] = res
        done += 1
        if done % 20 == 0 or done == len(tasks):
            print(f"  [pool] {done}/{len(tasks)} done", flush=True)
    return out


__all__ = [
    "ROOT", "OUT", "CACHE", "DEEPSEEK_MODEL", "TODAY", "INDUSTRIES",
    "norm_industry", "deaccent", "classify_obj", "kw_hits_pt", "kw_hits_zh",
    "deepseek_json", "run_pool", "extract_json",
]
