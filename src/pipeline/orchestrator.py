"""端到端编排:抓 publicacao → 过滤 → 入主表 → 并发拉明细 → 入明细表。

只编排,不实现具体业务 — 业务在 ``src.fetchers`` / ``src.storage``
/ ``src.filters``。
"""
from __future__ import annotations

import asyncio
import json
import httpx
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from ..core.exceptions import HttpError
from ..core.http_client import HttpClient
from ..core.logger import logger
from ..core.settings import (
    get_enrichment_settings,
    get_filter_settings,
    get_translation_settings,
)
from ..core.utils import parse_pncp_id
from ..enrichers import (
    fetch_brl_to_cny_rate,
    lookup_region,
    to_cny,
)
from ..enrichers.translate import text_hash, translate_texts
from ..fetchers.pncp_atualizacao import fetch_atualizacao_all
from ..fetchers.pncp_itens import fetch_itens
from ..fetchers.pncp_publicacao import fetch_publicacao_all
from ..filters import apply_result, get_default_engine, write_filtered_log
from ..storage import (
    Contratacao,
    ContratacaoIn,
    ContratacaoRepository,
    CursorRepository,
    Item,
    ItemRaw,
    ItemRepository,
    PublicacaoRaw,
    TranslationRepository,
    init_db,
    session_scope,
)

# 首次跑(没有 cursor)时,atualizacao 兜底拉过去多少天
_FIRST_RUN_LOOKBACK_DAYS = 7

# atualizacao 在 sync_cursor 表的 fetcher_name
ATUALIZACAO_FETCHER_NAME = "atualizacao"


def _set_field_to_tag_id() -> dict[str, str]:
    """``set_field`` (是 ORM 列名) → ``rule id``(T001 等)的反向映射,用于统计。"""
    engine = get_default_engine()
    return {r.set_field: r.id for r in engine.rules.soft_tags}


async def run_publicacao_with_itens(
    date_start: date | str,
    date_end: date | str,
    modalidade_codes: list[int] | None = None,
    *,
    page_size: int | None = None,
    cache_root: Path | None = None,
    limit: int | None = None,
    itens_concurrency: int = 5,
) -> dict[str, Any]:
    """端到端 pipeline。

    流程:
        1. 抓主表(走 BrowserSession,解 F5 挑战)
        2. 应用 :class:`FilterEngine.evaluate` 过滤 + 标注
        3. UPSERT contratacoes 表(被硬过滤的根据 ``filter.mode`` 决定去向)
        4. 解析每条 record 的 ``pncp_id``,**并发**调 ``fetch_itens`` 拉明细
        5. UPSERT itens 表

    单条明细失败 → log warning,不中断整体。
    被硬过滤命中的记录 **不拉明细**(省时间)。

    Args:
        date_start / date_end: 主表日期范围。
        modalidade_codes: 主表 modalidade 列表;``None`` 取 yaml 默认。
        page_size: 主表分页大小。
        cache_root: 主表原始 JSON 缓存根目录。
        limit: 最多处理多少条主表记录(调试用)。
        itens_concurrency: 明细并发上限。

    Returns:
        统计字典::

            {
              "contratacoes_kept": int,
              "contratacoes_filtered": int,
              "rule_hits": {"F001": n, ...},   # 命中的 F 规则计数
              "tag_hits":  {"T001": n, ...},   # 命中的 T 规则计数
              "itens": int,
              "itens_failed_pncp_ids": int,
              "filter_mode": "hard_delete" | "soft_delete",
            }
    """
    init_db()
    return await _run_list_pipeline(
        fetch_iter=fetch_publicacao_all(
            date_start,
            date_end,
            modalidade_codes,
            page_size=page_size,
            cache_root=cache_root,
        ),
        limit=limit,
        itens_concurrency=itens_concurrency,
    )
# ─── atualizacao 增量编排 ──────────────────────────────────────────────


async def run_atualizacao_incremental(
    *,
    force_full: bool = False,
    date_start: date | str | None = None,
    date_end: date | str | None = None,
    modalidade_codes: list[int] | None = None,
    page_size: int | None = None,
    cache_root: Path | None = None,
    limit: int | None = None,
    itens_concurrency: int = 5,
    skip_itens: bool = False,
) -> dict[str, Any]:
    """增量同步:用 sync_cursor 决定 ``data_inicial``。

    三种模式(优先级从高到低):
        1. **显式日期**(``date_start`` + ``date_end``)— 跑这段范围,**不读不写 cursor**。
           适合补历史数据 / 回放。
        2. **force_full=True**(没显式日期)— 兜底跑 ``today-{lookback}`` 到 ``today``,
           不读 cursor;**写 cursor**(让后续增量从这次结束的地方继续)。
        3. **默认**(无显式日期且 ``force_full=False``)— 读 cursor:
           ``data_inicial = cursor.last_data_final``(首次没有 cursor → 用 ``today-7d``),
           ``data_final = today``;**写 cursor**。

    跟 :func:`run_publicacao_with_itens` 一样接 filter / 拉明细;
    被硬过滤的不拉明细。

    Args:
        force_full: 见上文。
        date_start / date_end: 显式日期(``YYYY-MM-DD`` 字符串或 ``date``)。
        page_size / cache_root / limit / itens_concurrency / skip_itens: 同
            :func:`run_publicacao_with_itens`。

    Returns:
        统计字典,跟 publicacao 的相同,另加 ``date_start`` / ``date_end`` /
        ``mode_used``。
    """
    init_db()

    explicit_dates = date_start is not None and date_end is not None

    # 决定 data_inicial / data_final 与 cursor 行为
    if explicit_dates:
        ds = _to_date(date_start)
        de = _to_date(date_end)
        update_cursor_at_end = False
        mode_used = "explicit_range"
    elif force_full:
        de = datetime.utcnow().date()
        ds = de - timedelta(days=_FIRST_RUN_LOOKBACK_DAYS)
        update_cursor_at_end = True
        mode_used = "full"
    else:
        de = datetime.utcnow().date()
        with session_scope() as session:
            cursor_repo = CursorRepository(session)
            existing = cursor_repo.get(ATUALIZACAO_FETCHER_NAME)
        if existing is not None and existing.last_data_final is not None:
            ds = existing.last_data_final  # 允许 1 天重叠 — UPSERT 幂等
            mode_used = "incremental"
        else:
            ds = de - timedelta(days=_FIRST_RUN_LOOKBACK_DAYS)
            mode_used = "incremental_first_run"
        update_cursor_at_end = True

    logger.bind(
        date_start=ds.isoformat(),
        date_end=de.isoformat(),
        mode=mode_used,
        cursor_write=update_cursor_at_end,
    ).info("pipeline.atualizacao.plan")

    # 跑 pipeline 内部公用部分
    counters = await _run_list_pipeline(
        fetch_iter=fetch_atualizacao_all(
            ds,
            de,
            modalidade_codes,
            page_size=page_size,
            cache_root=cache_root,
        ),
        limit=limit,
        itens_concurrency=itens_concurrency,
        skip_itens=skip_itens,
    )

    # 成功后写 cursor
    if update_cursor_at_end:
        with session_scope() as session:
            cursor_repo = CursorRepository(session)
            cursor_repo.update(ATUALIZACAO_FETCHER_NAME, ds, de)

    counters["date_start"] = ds.isoformat()
    counters["date_end"] = de.isoformat()
    counters["mode_used"] = mode_used
    logger.bind(**{k: v for k, v in counters.items() if not isinstance(v, dict)}).info(
        "pipeline.atualizacao.done"
    )
    return counters


def _to_date(value: date | str) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


# ─── 公共列表 pipeline ─────────────────────────────────────────────────


async def _run_list_pipeline(
    *,
    fetch_iter,
    limit: int | None,
    itens_concurrency: int,
    skip_itens: bool = False,
) -> dict[str, Any]:
    """从 ``fetch_iter`` 收 publicacao-shape records → filter → upsert → itens。

    给 :func:`run_publicacao_with_itens` 和 :func:`run_atualizacao_incremental`
    共用。
    """
    engine = get_default_engine()
    filter_cfg = get_filter_settings()
    filter_mode: str = filter_cfg["mode"]
    filtered_log_dir = Path(filter_cfg["filtered_log_dir"])
    set_field_to_tag_id = _set_field_to_tag_id()

    rule_hits: Counter[str] = Counter()
    tag_hits: Counter[str] = Counter()
    state = {"failed_pncp_ids": 0}

    raw_records: list[dict[str, Any]] = []
    async for record in fetch_iter:
        raw_records.append(record)
        if limit is not None and len(raw_records) >= limit:
            break
    logger.bind(n=len(raw_records)).info("pipeline.publicacao.fetched")

    contratacoes_to_upsert: list[ContratacaoIn] = []
    filtered_for_log: list[dict[str, Any]] = []
    kept_pncp_ids: list[str] = []

    for r in raw_records:
        raw_model = PublicacaoRaw.model_validate(r)
        ci = raw_model.to_contratacao_in(raw_json=r)
        result = engine.evaluate(ci.model_dump())

        if result.keep:
            apply_result(ci, result)
            contratacoes_to_upsert.append(ci)
            kept_pncp_ids.append(ci.pncp_id)
            for set_field in result.tags:
                tag_id = set_field_to_tag_id.get(set_field)
                if tag_id:
                    tag_hits[tag_id] += 1
        else:
            assert result.hit_rule_id is not None
            rule_hits[result.hit_rule_id] += 1
            if filter_mode == "soft_delete":
                apply_result(ci, result)
                contratacoes_to_upsert.append(ci)
                kept_pncp_ids.append(ci.pncp_id)
            else:
                d = ci.model_dump()
                d["filter_rule_id"] = result.hit_rule_id
                d["filter_reason"] = result.reason
                d["filtered_at"] = datetime.utcnow().isoformat()
                filtered_for_log.append(d)

    logger.bind(
        kept=len(contratacoes_to_upsert),
        filtered=len(filtered_for_log),
        rule_hits=dict(rule_hits),
        tag_hits=dict(tag_hits),
        mode=filter_mode,
    ).info("pipeline.filter.done")

    with session_scope() as session:
        repo = ContratacaoRepository(session)
        for ci in contratacoes_to_upsert:
            repo.upsert(ci)
    logger.bind(n=len(contratacoes_to_upsert)).info("pipeline.contratacoes.upserted")

    if filtered_for_log:
        try:
            write_filtered_log(filtered_for_log, filtered_log_dir)
        except Exception as exc:
            logger.bind(error=str(exc)).error("pipeline.filtered_log.write_failed")

    total_items = 0
    if not skip_itens and kept_pncp_ids:
        sem = asyncio.Semaphore(itens_concurrency)

        async def _fetch_one(pncp_id: str, client: HttpClient) -> tuple[str, list[dict[str, Any]]]:
            async with sem:
                try:
                    cnpj, ano, seq = parse_pncp_id(pncp_id)
                    items = await fetch_itens(cnpj, ano, seq, client=client)
                    return pncp_id, items
                except (HttpError, ValueError) as exc:
                    state["failed_pncp_ids"] += 1
                    logger.bind(
                        pncp_id=pncp_id,
                        status=getattr(exc, "status_code", None),
                        error=str(exc),
                    ).warning("pipeline.itens.fetch_failed")
                    return pncp_id, []

        async with HttpClient() as client:
            results = await asyncio.gather(
                *[_fetch_one(pid, client) for pid in kept_pncp_ids],
            )

        with session_scope() as session:
            item_repo = ItemRepository(session)
            for pncp_id, raw_items in results:
                for raw_item in raw_items:
                    try:
                        item_model = ItemRaw.model_validate(raw_item)
                        item_repo.upsert(item_model.to_item_in(pncp_id, raw_json=raw_item))
                        total_items += 1
                    except Exception as exc:
                        logger.bind(
                            pncp_id=pncp_id,
                            numero_item=raw_item.get("numeroItem"),
                            error=str(exc),
                        ).warning("pipeline.items.upsert_failed")

    counters: dict[str, Any] = {
        "contratacoes_kept": len(contratacoes_to_upsert),
        "contratacoes_filtered": len(filtered_for_log),
        "rule_hits": dict(rule_hits),
        "tag_hits": dict(tag_hits),
        "itens": total_items,
        "itens_failed_pncp_ids": state["failed_pncp_ids"],
        "filter_mode": filter_mode,
    }
    logger.bind(**counters).info("pipeline.done")
    return counters


# ─── 清理过期招标(周更前置:删已过期 + 写删除日志)──────────────────────


def run_purge_expired(
    today: date | None = None,
    log_path: Path | str | None = None,
) -> dict[str, Any]:
    """删除已过期招标(``data_encerramento_proposta < 今天 00:00``)及其 itens 明细。

    被删标的关键信息**先**追加写入 ``logs/expired_purged.csv``(累积留档),再删库。
    不删:截止日为今天/未来的、2099 哨兵(无截止)、截止日为 NULL 的(时效未知)。
    供周更增量更新前置调用,避免库里越攒越多过期标。

    Returns:
        ``{"expired_found", "contratacoes_deleted", "itens_deleted", "log_path"}``。
    """
    import csv

    init_db()
    today = today or date.today()
    cutoff = datetime.combine(today, datetime.min.time())  # 今天 00:00
    root = Path(__file__).resolve().parents[2]
    log_path = Path(log_path) if log_path is not None else root / "logs" / "expired_purged.csv"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    counters: dict[str, Any] = {
        "expired_found": 0, "contratacoes_deleted": 0, "itens_deleted": 0,
        "log_path": str(log_path),
    }

    with session_scope() as session:
        rows = session.execute(
            select(
                Contratacao.pncp_id, Contratacao.objeto_compra,
                Contratacao.valor_total_estimado, Contratacao.valor_cny_estimado,
                Contratacao.data_encerramento_proposta, Contratacao.orgao_razao_social,
                Contratacao.uf_sigla, Contratacao.municipio_nome, Contratacao.modalidade_nome,
            ).where(
                Contratacao.data_encerramento_proposta.is_not(None),
                Contratacao.data_encerramento_proposta < cutoff,
            )
        ).all()
        counters["expired_found"] = len(rows)
        if not rows:
            logger.info("pipeline.purge_expired.none")
            return counters

        # 1) 先把被删标的信息追加进删除日志(utf-8-sig 首建带 BOM,Excel 不乱码)
        is_new = not log_path.exists()
        with log_path.open("a", encoding=("utf-8-sig" if is_new else "utf-8"), newline="") as fh:
            w = csv.writer(fh)
            if is_new:
                w.writerow(["删除时间", "PNCP编号", "标的", "预估金额BRL", "预估金额CNY",
                            "提交截止", "采购机构", "州", "城市", "招标方式"])
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for r in rows:
                w.writerow([
                    now, r.pncp_id, (r.objeto_compra or "").replace("\n", " ")[:120],
                    r.valor_total_estimado, r.valor_cny_estimado,
                    str(r.data_encerramento_proposta or ""), r.orgao_razao_social or "",
                    r.uf_sigla or "", r.municipio_nome or "", r.modalidade_nome or "",
                ])

        # 2) 删 itens + contratacoes(按 pncp_id 分块,避开 SQLite 变量上限 ~999)
        ids = [r.pncp_id for r in rows]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            res = session.execute(delete(Item).where(Item.pncp_id.in_(chunk)))
            counters["itens_deleted"] += res.rowcount or 0
            session.execute(delete(Contratacao).where(Contratacao.pncp_id.in_(chunk)))
        counters["contratacoes_deleted"] = len(ids)

    logger.bind(
        expired_found=counters["expired_found"],
        contratacoes_deleted=counters["contratacoes_deleted"],
        itens_deleted=counters["itens_deleted"],
    ).info("pipeline.purge_expired.done")
    return counters


# ─── 标的翻译(DeepSeek 葡→中,写入翻译缓存表)──────────────────────────


def _working_filter(stmt, *, active_only: bool, cny_min: float | None, cny_max: float | None):
    """给 contratacoes 的 select 加『未过期 + CNY 区间』过滤(translate / export 复用)。

    未过期 = ``data_encerramento_proposta >= 今天 00:00``;CNY 用 ``valor_cny_estimado``
    (需先跑 enrich 的 fx 才有值)。各条件都可选,不传则不过滤。
    """
    if active_only:
        stmt = stmt.where(
            Contratacao.data_encerramento_proposta.is_not(None),
            Contratacao.data_encerramento_proposta >= datetime.combine(date.today(), datetime.min.time()),
        )
    if cny_min is not None:
        stmt = stmt.where(Contratacao.valor_cny_estimado >= cny_min)
    if cny_max is not None:
        stmt = stmt.where(Contratacao.valor_cny_estimado <= cny_max)
    return stmt


async def run_translate(
    *,
    limit: int | None = None,
    batch_size: int | None = None,
    active_only: bool = False,
    cny_min: float | None = None,
    cny_max: float | None = None,
) -> dict[str, Any]:
    """把 ``contratacoes`` 的葡语标的批量翻成中文,写入 ``translation_cache``。

    * 按**原文 hash 去重**:只翻没缓存过的不同文本(语料高度重复,实际调用远少于行数)。
    * 调 DeepSeek(``deepseek-v4-flash``,见 ``settings.translation``);整批 JSON 返回。
    * 某批 API 失败 → 该批回退离线词典(``translate_objeto``),仍写缓存(标 model=offline)。
    * 导出时按 hash 查缓存;命中用译文,未命中再回退离线。

    Args:
        limit: 最多处理多少**不同**文本(调试用)。
        batch_size: 每批条数;``None`` 用 ``settings.translation.batch_size``。

    Returns:
        ``{"distinct", "already_cached", "to_translate", "translated_api",
           "translated_offline", "failed_batches"}``。
    """
    init_db()
    cfg = get_translation_settings()
    bs = int(batch_size if batch_size is not None else cfg["batch_size"])
    has_key = bool(cfg.get("api_key"))
    if not has_key:
        logger.warning("pipeline.translate.no_api_key")

    # 1. 取所有 distinct 标的 + 已 API 翻译的 hash(offline 兜底的下次重试升级)
    with session_scope() as session:
        _stmt = _working_filter(
            select(Contratacao.objeto_compra).where(Contratacao.objeto_compra.is_not(None)),
            active_only=active_only, cny_min=cny_min, cny_max=cny_max,
        ).distinct()
        objetos = [r[0] for r in session.execute(_stmt) if r[0] and r[0].strip()]
        repo = TranslationRepository(session)
        skip = repo.api_hashes()
        total_cached = repo.count()

    # 去重(同 hash 只留一条)+ 过滤已 API 翻译的
    by_hash: dict[str, str] = {}
    for o in objetos:
        h = text_hash(o)
        if h not in skip and h not in by_hash:
            by_hash[h] = o
    todo = list(by_hash.items())
    if limit is not None:
        todo = todo[:limit]

    counters: dict[str, Any] = {
        "distinct": len(objetos),
        "already_cached": total_cached,
        "to_translate": len(todo),
        "translated_api": 0,
        "translated_offline": 0,
        "failed_batches": 0,
    }
    logger.bind(**{k: counters[k] for k in ("distinct", "already_cached", "to_translate")}).info(
        "pipeline.translate.plan"
    )
    if not todo:
        logger.bind(**counters).info("pipeline.translate.done")
        return counters

    # 2. 分批翻译(批失败 → 二分递归 → 单条离线兜底)+ 写缓存
    async with httpx.AsyncClient() as client:
        for start in range(0, len(todo), bs):
            chunk = todo[start : start + bs]
            hashes = [h for h, _ in chunk]
            texts = [t for _, t in chunk]

            zhs, models = await translate_texts(texts, client=client)

            with session_scope() as session:
                repo = TranslationRepository(session)
                for h, src, zh, m in zip(hashes, texts, zhs, models):
                    repo.upsert(h, src, zh, m)
            n_off = sum(1 for m in models if m == "offline")
            counters["translated_offline"] += n_off
            counters["translated_api"] += len(chunk) - n_off
            if n_off:
                counters["failed_batches"] += 1
            logger.bind(
                done=counters["translated_api"] + counters["translated_offline"],
                total=len(todo),
                api=counters["translated_api"],
                offline=counters["translated_offline"],
            ).info("pipeline.translate.progress")

    logger.bind(**counters).info("pipeline.translate.done")
    return counters


# ─── PDF 富化编排(Edital 全文 + 二次过滤) ────────────────────────────


# ─── 富化层编排(region / fx → 回灌 contratacoes)──────────────────────


async def run_enrichment(
    *,
    do_region: bool = True,
    do_fx: bool = True,
    limit: int | None = None,
    redo: bool = False,
) -> dict[str, Any]:
    """富化已入库 contratacoes(两块,各自可开关):

    * **region**(离线):``uf_sigla`` → ``region_macro`` / ``region_gdp_tier``(查 regions.yaml)。
    * **fx**:取 BRL→CNY 实时汇率,填 ``valor_cny_estimado``(设备产品表金额区间必需)。

    Args:
        do_region / do_fx: 各块开关。
        limit: 各自最多处理多少行。
        redo: True 则连已富化过的也重做。

    Returns:
        ``{"region_filled", "fx_filled", "fx_rate"}``。
    """
    init_db()
    counters: dict[str, Any] = {"region_filled": 0, "fx_filled": 0, "fx_rate": None}

    # ─── region(离线查表)──────────────────────────────────────────
    if do_region:
        with session_scope() as session:
            stmt = select(Contratacao)
            if not redo:
                stmt = stmt.where(Contratacao.region_macro.is_(None))
            if limit is not None:
                stmt = stmt.limit(limit)
            for row in session.scalars(stmt):
                macro, tier = lookup_region(row.uf_sigla)
                if macro or tier:
                    row.region_macro = macro
                    row.region_gdp_tier = tier
                    counters["region_filled"] += 1
        logger.bind(n=counters["region_filled"]).info("pipeline.enrichment.region_done")

    # ─── fx(BRL→CNY)────────────────────────────────────────────────
    if do_fx:
        rate = await fetch_brl_to_cny_rate()
        counters["fx_rate"] = rate
        if rate is not None:
            with session_scope() as session:
                stmt = select(Contratacao).where(Contratacao.valor_total_estimado.is_not(None))
                if not redo:
                    stmt = stmt.where(Contratacao.valor_cny_estimado.is_(None))
                if limit is not None:
                    stmt = stmt.limit(limit)
                for row in session.scalars(stmt):
                    row.valor_cny_estimado = to_cny(row.valor_total_estimado, rate)
                    counters["fx_filled"] += 1
        logger.bind(n=counters["fx_filled"], rate=rate).info("pipeline.enrichment.fx_done")

    logger.bind(**{k: v for k, v in counters.items()}).info("pipeline.enrichment.done")
    return counters


__all__ = [
    "run_publicacao_with_itens",
    "run_atualizacao_incremental",
    "run_purge_expired",
    "run_translate",
    "run_enrichment",
]
