# -*- coding: utf-8 -*-
"""DeepSeek (deepseek-v4-flash, OpenAI 兼容) 异步客户端 —— 设备产品表分类用。

自包含:从 matching/match_lib 抽出的 DeepSeek 客户端部分(异步 + 磁盘缓存 + 并发
限流 + JSON 解析兜底),供 find_bids.py 做"是否实物采购"分类。缓存落本目录 cache/。
密钥从环境变量 DEEPSEEK_API_KEY 读,缺失则回退解析仓库根 .env(已 gitignore)。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]          # equipment_bids/->analysis/->仓库根
CACHE = Path(__file__).resolve().parent / "cache"   # 按提示词 hash 缓存
CACHE.mkdir(parents=True, exist_ok=True)


def _load_deepseek_key() -> str:
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


def _cache_path(key: str) -> Path:
    return CACHE / f"{key}.json"


def _key(system: str, user: str, model: str) -> str:
    return hashlib.sha1(f"{model}\x00{system}\x00{user}".encode("utf-8")).hexdigest()


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
    """并发跑一批协程工厂(f(sem) -> coro),返回结果列表(保持顺序)。"""
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


__all__ = ["deepseek_json", "run_pool", "extract_json", "DEEPSEEK_MODEL"]
