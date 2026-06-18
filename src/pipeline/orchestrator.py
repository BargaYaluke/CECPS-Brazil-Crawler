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

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

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
    derive_categoria,
    fetch_brl_to_cny_rate,
    fetch_cnpj_profile,
    lookup_region,
    to_cny,
)
from ..enrichers.translate import text_hash, translate_texts
from ..fetchers.compras_gov_catalogo import fetch_material_all, fetch_servico_all
from ..fetchers.edital_pdf import fetch_edital_text
from ..fetchers.pncp_atualizacao import fetch_atualizacao_all
from ..fetchers.pncp_contratos import fetch_contratos_all
from ..fetchers.pncp_itens import fetch_itens
from ..fetchers.pncp_pca import fetch_pca_all
from ..fetchers.pncp_proposta import fetch_proposta_all
from ..fetchers.pncp_publicacao import fetch_publicacao_all
from ..classifiers.dimension_engine import get_default_dimension_engine
from ..filters import apply_result, get_default_engine, write_filtered_log
from ..storage import (
    CatalogoRepository,
    Contratacao,
    ContratacaoIn,
    ContratacaoRepository,
    TranslationRepository,
    ContratoRaw,
    ContratoRepository,
    CursorRepository,
    Item,
    ItemRaw,
    ItemRepository,
    Orgao,
    PcaRaw,
    PcaRepository,
    PublicacaoRaw,
    catalogo_in_from_material,
    catalogo_in_from_servico,
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


# ─── proposta 编排 ─────────────────────────────────────────────────────


async def run_proposta_with_itens(
    data_final: date | str,
    modalidade_codes: list[int] | None = None,
    *,
    page_size: int | None = None,
    cache_root: Path | None = None,
    limit: int | None = None,
    itens_concurrency: int = 5,
    skip_itens: bool = False,
) -> dict[str, Any]:
    """端到端:按"投标截止日 ≥ data_final"拉招标 → 过滤 → 入库 → 拉明细。

    跟 :func:`run_publicacao_with_itens` 同一套 pipeline,**不涉及 cursor** —
    proposta 是快照型查询("此刻还能投的"),每天跑一次直接 UPSERT 即可。
    新 pncp_id 会新增,已入库的会被更新到最新状态。

    Args:
        data_final: 投标截止日参数(``YYYY-MM-DD`` 或 ``date``)。
        modalidade_codes: ``None`` → 用 modalidades.yaml 的 default_iter。
        其余参数同 :func:`run_publicacao_with_itens`。
    """
    init_db()
    return await _run_list_pipeline(
        fetch_iter=fetch_proposta_all(
            data_final,
            modalidade_codes,
            page_size=page_size,
            cache_root=cache_root,
        ),
        limit=limit,
        itens_concurrency=itens_concurrency,
        skip_itens=skip_itens,
    )


# ─── contratos 编排(独立,不走招标 pipeline)────────────────────────────


async def run_contratos(
    date_start: date | str,
    date_end: date | str,
    *,
    page_size: int | None = None,
    cache_root: Path | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """端到端拉取已签合同 → 入 ``contratos`` 表。

    跟招标 pipeline 完全分开:
        * **不**做 filter(合同没有 modalidade 等过滤维度)
        * **不**拉明细(明细 API 是为招标设计的)
        * **不**碰 contratacoes 表(分开存,clean separation)

    UPSERT 幂等:同 ``pncp_id``(合同的)被覆盖更新,允许重跑。

    Args:
        date_start / date_end: ``dataPublicacaoPncp`` 过滤范围。
        page_size: 每页条数;``None`` 用 settings.pncp.page_size,自动夹到 ≥ 10。
        cache_root: 原始 JSON 缓存根。
        limit: 最多入库多少条(调试)。

    Returns:
        ``{"contratos": N, "date_start": ..., "date_end": ...}``。
    """
    init_db()

    raw_records: list[dict[str, Any]] = []
    async for record in fetch_contratos_all(
        date_start, date_end, page_size=page_size, cache_root=cache_root
    ):
        raw_records.append(record)
        if limit is not None and len(raw_records) >= limit:
            break
    logger.bind(n=len(raw_records)).info("pipeline.contratos.fetched")

    inserted = 0
    with session_scope() as session:
        repo = ContratoRepository(session)
        for r in raw_records:
            try:
                raw_model = ContratoRaw.model_validate(r)
                repo.upsert(raw_model.to_contrato_in(raw_json=r))
                inserted += 1
            except Exception as exc:
                logger.bind(
                    pncp_id=r.get("numeroControlePNCP"),
                    error=str(exc),
                ).warning("pipeline.contratos.upsert_failed")

    counters: dict[str, Any] = {
        "contratos": inserted,
        "date_start": _to_date(date_start).isoformat(),
        "date_end": _to_date(date_end).isoformat(),
    }
    logger.bind(**counters).info("pipeline.contratos.done")
    return counters


# ─── PCA 编排(独立) ───────────────────────────────────────────────────


async def run_pca(
    date_start: date | str,
    date_end: date | str,
    *,
    page_size: int | None = None,
    cache_root: Path | None = None,
    limit: int | None = None,
    commit_every: int = 500,
) -> dict[str, Any]:
    """端到端拉取 PCA(年度采购计划)→ 拍扁 itens → 入 ``pca_itens`` 表。

    跟招标/合同完全分开:
        * 不做 filter(PCA 是未来计划,F001-F006 都不适用)
        * 嵌套 itens 自动 explode 成多行
        * UPSERT 幂等(冲突键 ``(id_pca_pncp, numero_item)``)

    数据规模警告:7 天范围 100 万+ 条,**用 ``limit`` 控制**(limit 是 PCA 头部数;
    实际入库 item 数 = limit × 每个 PCA 的 item 数)。

    Args:
        date_start / date_end: ``dataAtualizacaoGlobalPCA`` 过滤范围。
        page_size: 每页 PCA 头数;自动 ≥ 10。
        cache_root: 原始 JSON 缓存根。
        limit: 最多处理多少条 PCA 头(调试用)。
        commit_every: 每抓多少个 PCA 头就提交一次(**逐页提交**,中断也保住已提交的)。

    Returns:
        ``{"pcas": int, "items": int, "date_start": ..., "date_end": ...}``。

    Note:
        **逐页提交**:不再"全抓完一次性 commit"(那样 Ctrl+C 会回滚全部),而是每
        ``commit_every`` 个头独立事务提交一次,中断只丢未满一批的尾巴。
    """
    init_db()

    buffer: list[dict[str, Any]] = []
    total_heads = 0
    total_items = 0
    failed_pcas = 0

    async for record in fetch_pca_all(
        date_start, date_end, page_size=page_size, cache_root=cache_root
    ):
        buffer.append(record)
        total_heads += 1
        if len(buffer) >= commit_every:
            items, failed = _upsert_pca_heads(buffer)
            total_items += items
            failed_pcas += failed
            buffer = []
            logger.bind(committed_heads=total_heads, items=total_items).info(
                "pipeline.pca.progress"
            )
        if limit is not None and total_heads >= limit:
            break
    if buffer:
        items, failed = _upsert_pca_heads(buffer)
        total_items += items
        failed_pcas += failed

    counters: dict[str, Any] = {
        "pcas": total_heads,
        "items": total_items,
        "pcas_failed": failed_pcas,
        "date_start": _to_date(date_start).isoformat(),
        "date_end": _to_date(date_end).isoformat(),
    }
    logger.bind(**counters).info("pipeline.pca.done")
    return counters


def _upsert_pca_heads(heads: list[dict[str, Any]]) -> tuple[int, int]:
    """把一批 PCA 头 explode + UPSERT 入库(单独事务提交)。

    Args:
        heads: PCA 头 dict 列表(每个含嵌套 ``itens``)。

    Returns:
        ``(入库 item 行数, 失败头数)``。单个头解析/入库失败只记 warning,不影响同批其它头。
    """
    items = 0
    failed = 0
    with session_scope() as session:
        repo = PcaRepository(session)
        for r in heads:
            try:
                pca_model = PcaRaw.model_validate(r)
                for item_in in pca_model.explode_items(raw_json=r):
                    repo.upsert(item_in)
                    items += 1
            except Exception as exc:
                failed += 1
                logger.bind(
                    id_pca_pncp=r.get("idPcaPncp"),
                    error=str(exc),
                ).warning("pipeline.pca.upsert_failed")
    return items, failed


def load_pca_from_cache(
    *,
    cache_root: Path | str | None = None,
    limit: int | None = None,
    commit_every: int = 500,
) -> dict[str, Any]:
    """从 ``data/raw/pca/`` 已落盘的原始 JSON 把 PCA 救回入库(**不重抓网络**)。

    用途:``run_pca`` 旧版"全抓完才提交",中断会回滚全部 → 库空但 raw 文件还在。
    本函数读那些 ``page_*.json`` envelope,解析 ``data`` 里的 PCA 头,explode + UPSERT。
    UPSERT 幂等,可安全重跑。

    Args:
        cache_root: 原始缓存根目录;``None`` → ``./data/raw``。
        limit: 最多处理多少个 PCA 头。
        commit_every: 每多少个头提交一次。

    Returns:
        ``{"files": int, "pcas": int, "items": int, "pcas_failed": int}``。
    """
    init_db()
    root = Path(cache_root) if cache_root is not None else Path("./data/raw")
    pca_dir = root / "pca"
    files = sorted(pca_dir.rglob("page_*.json")) if pca_dir.exists() else []
    logger.bind(n_files=len(files), dir=str(pca_dir)).info("pipeline.pca_cache.files")

    buffer: list[dict[str, Any]] = []
    total_heads = 0
    total_items = 0
    failed_pcas = 0
    stop = False

    for f in files:
        if stop:
            break
        try:
            envelope = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.bind(file=str(f), error=str(exc)).warning("pipeline.pca_cache.bad_file")
            continue
        for rec in envelope.get("data") or []:
            buffer.append(rec)
            total_heads += 1
            if len(buffer) >= commit_every:
                items, failed = _upsert_pca_heads(buffer)
                total_items += items
                failed_pcas += failed
                buffer = []
                logger.bind(committed_heads=total_heads, items=total_items).info(
                    "pipeline.pca_cache.progress"
                )
            if limit is not None and total_heads >= limit:
                stop = True
                break
    if buffer:
        items, failed = _upsert_pca_heads(buffer)
        total_items += items
        failed_pcas += failed

    counters: dict[str, Any] = {
        "files": len(files),
        "pcas": total_heads,
        "items": total_items,
        "pcas_failed": failed_pcas,
    }
    logger.bind(**counters).info("pipeline.pca_cache.done")
    return counters


# ─── 标的翻译(DeepSeek 葡→中,写入翻译缓存表)──────────────────────────


async def run_translate(
    *,
    limit: int | None = None,
    batch_size: int | None = None,
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
        objetos = [
            r[0]
            for r in session.execute(
                select(Contratacao.objeto_compra).where(Contratacao.objeto_compra.is_not(None)).distinct()
            )
            if r[0] and r[0].strip()
        ]
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
            ).info("pipeline.translate.progress")

    logger.bind(**counters).info("pipeline.translate.done")
    return counters


# ─── PDF 富化编排(Edital 全文 + 二次过滤) ────────────────────────────


async def run_pdf_enrichment(
    *,
    limit: int | None = None,
    concurrency: int = 3,
    redo: bool = False,
) -> dict[str, Any]:
    """对主表里"还没解析过 PDF"的招标补 Edital 全文 + 跑二次过滤。

    流程(三阶段,避免 async I/O 跟同步 session 混):
        1. **查**:从 ``contratacoes`` 取 ``edital_pdf_extracted=false`` 的 pncp_id
           (``redo=True`` 则取全部),限 ``limit``。
        2. **并发下载解析**(``concurrency`` 路;PDF 慢,默认只 3 路):
           每个 pncp_id → arquivos → 主 Edital → 流式下载 → pdfplumber 提取文本。
        3. **更新 + 二次过滤**:写 ``edital_text`` / ``edital_pdf_url`` /
           ``edital_pdf_extracted`` 字段;然后 ``FilterEngine.re_evaluate``
           跑 F004-F006,**二次命中则从主表删除 + 写 filtered_log**。

    单条失败(下载超时 / 解析错 / 扫描件)→ 标记 extracted=true 但 text 空,
    不中断整体。

    ⚠️ PDF 下载 + 解析很慢(一条 ~30s),务必用 ``limit`` 控制。

    Args:
        limit: 最多处理多少条招标。
        concurrency: 并发数(PDF 服务器慢,别太高)。
        redo: True 则连已解析过的也重做(规则 / PDF 更新后回灌)。

    Returns:
        ``{"processed": int, "with_text": int, "empty_text": int,
           "refiltered_out": int, "rule_hits": {...}}``。
    """
    init_db()
    engine = get_default_engine()
    filter_cfg = get_filter_settings()
    filter_mode: str = filter_cfg["mode"]
    filtered_log_dir = Path(filter_cfg["filtered_log_dir"])

    # ─── 1. 查待处理 ────────────────────────────────────────────────
    with session_scope() as session:
        stmt = select(Contratacao.pncp_id)
        if not redo:
            stmt = stmt.where(Contratacao.edital_pdf_extracted.is_(False))
        if limit is not None:
            stmt = stmt.limit(limit)
        pncp_ids = list(session.scalars(stmt))
    logger.bind(n=len(pncp_ids)).info("pipeline.pdf.candidates")

    if not pncp_ids:
        return {
            "processed": 0,
            "with_text": 0,
            "empty_text": 0,
            "refiltered_out": 0,
            "rule_hits": {},
        }

    # ─── 2. 并发下载解析 ────────────────────────────────────────────
    sem = asyncio.Semaphore(concurrency)

    async def _one(pncp_id: str, client: HttpClient) -> tuple[str, str | None, str]:
        async with sem:
            try:
                cnpj, ano, seq = parse_pncp_id(pncp_id)
                url, text = await fetch_edital_text(cnpj, ano, seq, client=client)
                return pncp_id, url, text
            except (HttpError, ValueError) as exc:
                logger.bind(pncp_id=pncp_id, error=str(exc)).warning(
                    "pipeline.pdf.fetch_failed"
                )
                return pncp_id, None, ""

    async with HttpClient() as client:
        results = await asyncio.gather(*[_one(p, client) for p in pncp_ids])

    # ─── 3. 更新 + 二次过滤 ─────────────────────────────────────────
    rule_hits: Counter[str] = Counter()
    with_text = 0
    empty_text = 0
    refiltered_out = 0
    filtered_for_log: list[dict[str, Any]] = []

    with session_scope() as session:
        for pncp_id, url, text in results:
            row = session.get(Contratacao, pncp_id)
            if row is None:
                continue
            row.edital_pdf_url = url
            row.edital_text = text or None
            row.edital_pdf_extracted = True
            row.edital_pdf_extracted_at = datetime.utcnow()
            if text:
                with_text += 1
            else:
                empty_text += 1

            # 二次过滤(只在有文本时有意义)
            if text:
                row_dict = {c.name: getattr(row, c.name) for c in row.__table__.columns}
                result = engine.re_evaluate(row_dict, edital_text=text)
                if not result.keep:
                    assert result.hit_rule_id is not None
                    rule_hits[result.hit_rule_id] += 1
                    refiltered_out += 1
                    if filter_mode == "hard_delete":
                        d = dict(row_dict)
                        d["filter_rule_id"] = result.hit_rule_id
                        d["filter_reason"] = result.reason
                        d["filtered_at"] = datetime.utcnow().isoformat()
                        d.pop("edital_text", None)  # filtered_log 不留全文(太大)
                        filtered_for_log.append(d)
                        session.delete(row)
                    else:
                        row.filter_reason = result.reason
                        row.filter_rule_id = result.hit_rule_id

    if filtered_for_log:
        try:
            write_filtered_log(filtered_for_log, filtered_log_dir)
        except Exception as exc:
            logger.bind(error=str(exc)).error("pipeline.pdf.filtered_log_write_failed")

    counters: dict[str, Any] = {
        "processed": len(results),
        "with_text": with_text,
        "empty_text": empty_text,
        "refiltered_out": refiltered_out,
        "rule_hits": dict(rule_hits),
    }
    logger.bind(**counters).info("pipeline.pdf.done")
    return counters


# ─── 六大维度分类(对已入库 contratacoes 回灌) ────────────────────────


async def run_classification(
    *,
    limit: int | None = None,
    redo: bool = False,
) -> dict[str, Any]:
    """对 ``contratacoes`` 表跑六大维度分类,更新 4 个 dimension 字段。

    纯 CPU + DB 操作,无网络;不分 async 阶段(分类很快)。

    文本来源:``objeto_compra`` + ``informacao_complementar`` + ``edital_text``
    (如果 PDF 已解析,全文也参与分类 — 更准)。

    Args:
        limit: 最多处理多少条。
        redo: True 则连已分类的(dimension_primary 非空)也重做(词典更新后回灌)。

    Returns:
        ``{"processed": int, "classified": int, "uncategorized": int,
           "by_dimension": {dim: count}, "low_confidence": int}``。
    """
    init_db()
    engine = get_default_dimension_engine()

    by_dimension: Counter[str] = Counter()
    classified = 0
    uncategorized = 0
    low_conf = 0
    processed = 0

    with session_scope() as session:
        stmt = select(Contratacao)
        if not redo:
            stmt = stmt.where(Contratacao.dimension_primary.is_(None))
        if limit is not None:
            stmt = stmt.limit(limit)

        for row in session.scalars(stmt):
            processed += 1
            text = " ".join(
                filter(
                    None,
                    [row.objeto_compra, row.informacao_complementar, row.edital_text],
                )
            )
            result = engine.classify(text)
            row.dimension_primary = result.primary
            row.dimension_secondary = result.secondary or None
            row.dimension_confidence = result.confidence
            row.dimension_match_reason = result.match_reason

            if result.primary:
                classified += 1
                by_dimension[result.primary] += 1
                if result.low_confidence:
                    low_conf += 1
            else:
                uncategorized += 1

    counters: dict[str, Any] = {
        "processed": processed,
        "classified": classified,
        "uncategorized": uncategorized,
        "by_dimension": dict(by_dimension),
        "low_confidence": low_conf,
    }
    logger.bind(**counters).info("pipeline.classification.done")
    return counters


# ─── Compras.gov.br 目录采集(CATMAT/CATSER → catalogo_compras)─────────


async def run_catalogo(
    *,
    tipo: str = "both",
    page_size: int | None = None,
    cache_root: Path | None = None,
    limit: int | None = None,
    commit_every: int = 2000,
) -> dict[str, Any]:
    """拉 Compras.gov.br 标准品类目录 → UPSERT ``catalogo_compras`` 维表。

    富化层的**参考数据**(不是招标列表):把裸编码映射成可读类目,供富化 itens
    与维度分类用。

    流式分批入库:每 ``commit_every`` 条提交一次,避免把 34 万条全堆内存 / 一个
    超大事务。UPSERT 幂等(冲突键 ``(tipo, codigo)``),可断点重跑。

    Args:
        tipo: ``material`` / ``servico`` / ``both``。
        page_size: 每页条数;``None`` 用 ``compras_gov.page_size``(默认 500)。
        cache_root: 原始 JSON 缓存根目录。
        limit: 每个 ``tipo`` 最多入库多少条(调试用;全量传 ``None``)。
        commit_every: 每多少条提交一次。

    Returns:
        ``{"material": int, "servico": int, "total": int}``。
    """
    init_db()

    sources: list[tuple[str, Any, Any, str]] = []
    if tipo in ("material", "both"):
        sources.append(("material", fetch_material_all, catalogo_in_from_material, "codigoItem"))
    if tipo in ("servico", "both"):
        sources.append(("servico", fetch_servico_all, catalogo_in_from_servico, "codigoServico"))
    if not sources:
        raise ValueError(f"tipo 必须是 material/servico/both,收到: {tipo!r}")

    counters: dict[str, Any] = {"material": 0, "servico": 0}

    def _flush(buf: list[Any]) -> None:
        if not buf:
            return
        with session_scope() as session:
            CatalogoRepository(session).upsert_batch(buf)

    for modulo, fetch_fn, mapper, code_key in sources:
        buffer: list[Any] = []
        n = 0
        async for rec in fetch_fn(page_size=page_size, cache_root=cache_root):
            if rec.get(code_key) is None:
                continue
            buffer.append(mapper(rec, raw_json=rec))
            n += 1
            if len(buffer) >= commit_every:
                _flush(buffer)
                buffer = []
            if limit is not None and n >= limit:
                break
        _flush(buffer)
        counters[modulo] = n
        logger.bind(modulo=modulo, n=n).info("pipeline.catalogo.module_done")

    counters["total"] = counters["material"] + counters["servico"]
    logger.bind(**counters).info("pipeline.catalogo.done")
    return counters


# ─── 富化层编排(region / fx / orgao 画像 / itens 目录)──────────────────


def _orgao_tier(total_valor: float) -> str:
    """按 2 年累计采购额给机构分层(启发式阈值,业务可调)。"""
    if total_valor >= 50_000_000:
        return "high"
    if total_valor >= 5_000_000:
        return "middle"
    return "low"


async def run_enrichment(
    *,
    do_region: bool = True,
    do_fx: bool = True,
    do_orgao: bool = True,
    do_catalogo_itens: bool = True,
    with_brasilapi: bool = False,
    limit: int | None = None,
    redo: bool = False,
) -> dict[str, Any]:
    """富化已入库数据(四块,各自可开关):

    * **region**(离线):``contratacoes.uf_sigla`` → ``region_macro`` / ``region_gdp_tier``
      (查 ``regions.yaml``)。
    * **fx**:取 BRL→CNY 实时汇率,填 ``contratacoes.valor_cny_estimado``。
    * **orgao**:聚合 ``contratacoes`` → ``orgaos`` 机构画像(条数 / 累计额 / 最近活跃 /
      分层 / 名称 / UF);``with_brasilapi=True`` 时用 BrasilAPI 补权威名称与所在地。
    * **catalogo_itens**:``itens.catalogo_codigo_item`` → ``catalogo_compras`` 可读类目,
      填 ``itens.categoria_item_catalogo``(需先 ``fetch-catalogo`` 灌目录)。

    Args:
        do_region / do_fx / do_orgao / do_catalogo_itens: 各块开关。
        with_brasilapi: orgao 富化是否调 BrasilAPI(每个 distinct CNPJ 一次,慢)。
        limit: region / fx / catalogo_itens 各自最多处理多少行(orgao 是聚合,不受限)。
        redo: True 则连已富化过的(对应列非空)也重做。

    Returns:
        ``{"region_filled", "fx_filled", "fx_rate", "orgaos_upserted",
           "itens_catalogo_filled"}``。
    """
    init_db()
    counters: dict[str, Any] = {
        "region_filled": 0,
        "fx_filled": 0,
        "fx_rate": None,
        "orgaos_upserted": 0,
        "itens_catalogo_filled": 0,
    }

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

    # ─── orgao 画像(聚合 contratacoes → orgaos)────────────────────
    if do_orgao:
        agg = (
            select(
                Contratacao.orgao_cnpj.label("cnpj"),
                func.count().label("cnt"),
                func.coalesce(func.sum(Contratacao.valor_total_estimado), 0.0).label("total"),
                func.max(Contratacao.data_publicacao_pncp).label("last_pub"),
                func.max(Contratacao.orgao_razao_social).label("nome"),
                func.max(Contratacao.orgao_esfera_id).label("esfera"),
                func.max(Contratacao.uf_sigla).label("uf"),
                func.max(Contratacao.municipio_nome).label("municipio"),
            )
            .where(Contratacao.orgao_cnpj.is_not(None))
            .group_by(Contratacao.orgao_cnpj)
        )
        with session_scope() as session:
            agg_rows = [
                {
                    "cnpj": r.cnpj,
                    "cnt": int(r.cnt),
                    "total": float(r.total or 0.0),
                    "last_pub": r.last_pub,
                    "nome": r.nome,
                    "esfera": r.esfera,
                    "uf": r.uf,
                    "municipio": r.municipio,
                }
                for r in session.execute(agg)
            ]

        # 可选:BrasilAPI 补权威名称 / 所在地(在 session 外做网络 I/O)
        profiles: dict[str, dict[str, Any]] = {}
        if with_brasilapi and agg_rows:
            cfg = get_enrichment_settings()
            async with HttpClient(rate_limit_per_second=float(cfg["rate_limit_per_sec"])) as client:
                for r in agg_rows:
                    try:
                        prof = await fetch_cnpj_profile(r["cnpj"], client=client)
                    except (HttpError, ValueError) as exc:
                        logger.bind(cnpj=r["cnpj"], error=str(exc)).warning(
                            "pipeline.enrichment.brasilapi_failed"
                        )
                        prof = None
                    if prof:
                        profiles[r["cnpj"]] = prof

        with session_scope() as session:
            for r in agg_rows:
                prof = profiles.get(r["cnpj"]) or {}
                last_active = r["last_pub"].date() if r["last_pub"] is not None else None
                values = {
                    "cnpj": r["cnpj"],
                    "nome": prof.get("razao_social") or r["nome"],
                    "esfera": r["esfera"],
                    "uf": prof.get("uf") or r["uf"],
                    "municipio": prof.get("municipio") or r["municipio"],
                    "total_contratacoes_2y": r["cnt"],
                    "total_valor_2y": r["total"],
                    "last_active_date": last_active,
                    "tier": _orgao_tier(r["total"]),
                }
                stmt = sqlite_insert(Orgao).values(**values)
                update_cols = {k: getattr(stmt.excluded, k) for k in values if k != "cnpj"}
                stmt = stmt.on_conflict_do_update(index_elements=["cnpj"], set_=update_cols)
                session.execute(stmt)
                counters["orgaos_upserted"] += 1
        logger.bind(
            n=counters["orgaos_upserted"], with_brasilapi=with_brasilapi
        ).info("pipeline.enrichment.orgao_done")

    # ─── itens 目录富化(裸编码 → 可读类目)─────────────────────────
    if do_catalogo_itens:
        with session_scope() as session:
            cat_repo = CatalogoRepository(session)
            stmt = select(Item).where(Item.catalogo_codigo_item.is_not(None))
            if not redo:
                stmt = stmt.where(Item.categoria_item_catalogo.is_(None))
            if limit is not None:
                stmt = stmt.limit(limit)
            for item in session.scalars(stmt):
                ms = (item.material_ou_servico or "").upper()
                tipo = "material" if ms.startswith("M") else "servico" if ms.startswith("S") else None
                cat = derive_categoria(cat_repo.get_by_codigo(item.catalogo_codigo_item, tipo=tipo))
                if cat:
                    item.categoria_item_catalogo = cat
                    counters["itens_catalogo_filled"] += 1
        logger.bind(n=counters["itens_catalogo_filled"]).info(
            "pipeline.enrichment.catalogo_itens_done"
        )

    logger.bind(**{k: v for k, v in counters.items()}).info("pipeline.enrichment.done")
    return counters


__all__ = [
    "run_publicacao_with_itens",
    "run_atualizacao_incremental",
    "run_proposta_with_itens",
    "run_contratos",
    "run_pca",
    "load_pca_from_cache",
    "run_translate",
    "run_pdf_enrichment",
    "run_classification",
    "run_catalogo",
    "run_enrichment",
]
