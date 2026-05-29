"""命令行入口。

用法::

    python -m src.cli --help
    python -m src.cli fetch-publicacao --start 2026-05-26 --end 2026-05-26
    python -m src.cli fetch-publicacao --start 2026-05-26 --end 2026-05-26 --modalidade 6 --limit 5
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

import click

from . import __version__
from .core.logger import logger
from .fetchers.pncp_publicacao import fetch_publicacao_all
from .exporters.excel import export_to_excel
from .pipeline.orchestrator import (
    load_pca_from_cache,
    run_atualizacao_incremental,
    run_catalogo,
    run_classification,
    run_contratos,
    run_enrichment,
    run_pca,
    run_pdf_enrichment,
    run_proposta_with_itens,
    run_publicacao_with_itens,
)


# ─── 实时进度 sink(挂到 loguru,只过滤关键事件,转成单行人读输出) ─────


def _make_progress_sink():
    """构造一个 loguru sink,把关键事件转成简短可读的进度行打到 stderr。

    监听:
        browser_session.start / ready / closed
        fetcher.publicacao.run / done / not_found / empty_page / no_content
        fetcher.publicacao.no_modalidades

    其它事件被现有 JSON sink 处理,本 sink 直接忽略。
    """
    state = {"fetched_total": 0}

    def sink(message: Any) -> None:
        rec = message.record
        event = rec["message"]
        x = rec["extra"]
        ts = rec["time"].strftime("%H:%M:%S")

        if event == "browser_session.start":
            click.echo(f"[{ts}] 浏览器启动中 ...", err=True)
        elif event == "browser_session.ready":
            n = x.get("ts_cookie_count", "?")
            click.echo(f"[{ts}] 浏览器就绪({n} 个 TS cookies,F5 挑战已解)", err=True)
        elif event == "browser_session.closed":
            click.echo(f"[{ts}] 浏览器关闭", err=True)
        elif event == "fetcher.publicacao.run":
            mods = x.get("modalidades", [])
            ds = x.get("date_start", "?")
            de = x.get("date_end", "?")
            click.echo(
                f"[{ts}] 开始抓取 {ds}..{de} modalidades={mods} page_size={x.get('page_size')}",
                err=True,
            )
        elif event == "fetcher.publicacao.done":
            m = x.get("modalidade", "?")
            p = x.get("page", "?")
            tp = x.get("total_paginas", "?")
            r = x.get("records_in_page", 0) or 0
            tr = x.get("total_registros", "?")
            state["fetched_total"] += r if isinstance(r, int) else 0
            click.echo(
                f"[{ts}] mod={m:<3} page={p}/{tp:<3}  本页 {r:>3} 条  |  "
                f"累计 {state['fetched_total']} (服务端 {tr})",
                err=True,
            )
        elif event == "fetcher.publicacao.not_found":
            m = x.get("modalidade", "?")
            p = x.get("page", "?")
            click.echo(f"[{ts}] mod={m} page={p} → 404,跳过", err=True)
        elif event == "fetcher.publicacao.empty_page":
            m = x.get("modalidade", "?")
            p = x.get("page", "?")
            click.echo(f"[{ts}] mod={m} page={p} → 空页,结束此 modalidade", err=True)
        elif event == "fetcher.publicacao.no_content":
            m = x.get("modalidade", "?")
            p = x.get("page", "?")
            click.echo(f"[{ts}] mod={m} page={p} → 无内容,结束此 modalidade", err=True)
        elif event == "fetcher.publicacao.no_modalidades":
            click.echo(f"[{ts}] 没有配置 modalidade,流程退出", err=True)
        # ─── atualizacao fetcher 事件 ──────────────────────────────
        elif event == "fetcher.atualizacao.run":
            ds = x.get("date_start", "?")
            de = x.get("date_end", "?")
            click.echo(
                f"[{ts}] 开始抓取(atualizacao 增量){ds}..{de} page_size={x.get('page_size')}",
                err=True,
            )
        elif event == "fetcher.atualizacao.done":
            p = x.get("page", "?")
            tp = x.get("total_paginas", "?")
            r = x.get("records_in_page", 0) or 0
            tr = x.get("total_registros", "?")
            state["fetched_total"] += r if isinstance(r, int) else 0
            click.echo(
                f"[{ts}] atualizacao page={p}/{tp:<3}  本页 {r:>3} 条  |  "
                f"累计 {state['fetched_total']} (服务端 {tr})",
                err=True,
            )
        elif event == "fetcher.atualizacao.empty_page":
            p = x.get("page", "?")
            click.echo(f"[{ts}] atualizacao page={p} → 空页,结束", err=True)
        elif event == "fetcher.atualizacao.not_found":
            p = x.get("page", "?")
            click.echo(f"[{ts}] atualizacao page={p} → 404,结束", err=True)
        # ─── proposta fetcher 事件 ─────────────────────────────────
        elif event == "fetcher.proposta.run":
            df = x.get("data_final", "?")
            mods = x.get("modalidades", [])
            click.echo(
                f"[{ts}] 开始抓取(proposta 还能投标)data_final={df} "
                f"modalidades={mods} page_size={x.get('page_size')}",
                err=True,
            )
        elif event == "fetcher.proposta.done":
            m = x.get("modalidade", "?")
            p = x.get("page", "?")
            tp = x.get("total_paginas", "?")
            r = x.get("records_in_page", 0) or 0
            tr = x.get("total_registros", "?")
            state["fetched_total"] += r if isinstance(r, int) else 0
            click.echo(
                f"[{ts}] proposta mod={m:<3} page={p}/{tp:<3}  本页 {r:>3} 条  |  "
                f"累计 {state['fetched_total']} (服务端 {tr})",
                err=True,
            )
        elif event == "fetcher.proposta.empty_page":
            m = x.get("modalidade", "?")
            p = x.get("page", "?")
            click.echo(f"[{ts}] proposta mod={m} page={p} → 空页,跳过", err=True)
        elif event == "fetcher.proposta.not_found":
            m = x.get("modalidade", "?")
            p = x.get("page", "?")
            click.echo(f"[{ts}] proposta mod={m} page={p} → 404,跳过", err=True)
        # ─── contratos fetcher 事件 ────────────────────────────────
        elif event == "fetcher.contratos.run":
            ds = x.get("date_start", "?")
            de = x.get("date_end", "?")
            click.echo(
                f"[{ts}] 开始抓取(contratos 已签合同){ds}..{de} "
                f"page_size={x.get('page_size')}",
                err=True,
            )
        elif event == "fetcher.contratos.done":
            p = x.get("page", "?")
            tp = x.get("total_paginas", "?")
            r = x.get("records_in_page", 0) or 0
            tr = x.get("total_registros", "?")
            state["fetched_total"] += r if isinstance(r, int) else 0
            click.echo(
                f"[{ts}] contratos page={p}/{tp:<5}  本页 {r:>3} 条  |  "
                f"累计 {state['fetched_total']} (服务端 {tr})",
                err=True,
            )
        elif event == "fetcher.contratos.empty_page":
            p = x.get("page", "?")
            click.echo(f"[{ts}] contratos page={p} → 空页,结束", err=True)
        # ─── contratos pipeline 事件 ───────────────────────────────
        elif event == "pipeline.contratos.fetched":
            click.echo(f"[{ts}] 合同抓取完成,共 {x.get('n', 0)} 条", err=True)
        elif event == "pipeline.contratos.done":
            n = x.get("contratos", 0)
            ds = x.get("date_start", "?")
            de = x.get("date_end", "?")
            click.echo(
                f"[{ts}] 合同入库完成: {n} 条  date={ds}..{de}", err=True
            )
        # ─── PCA fetcher 事件 ──────────────────────────────────────
        elif event == "fetcher.pca.run":
            ds = x.get("date_start", "?")
            de = x.get("date_end", "?")
            click.echo(
                f"[{ts}] 开始抓取(PCA 年度采购计划){ds}..{de} "
                f"page_size={x.get('page_size')}",
                err=True,
            )
        elif event == "fetcher.pca.done":
            p = x.get("page", "?")
            tp = x.get("total_paginas", "?")
            n_pcas = x.get("n_pcas_in_page", 0)
            n_items = x.get("n_items_in_page", 0)
            tr = x.get("total_registros", "?")
            state["fetched_total"] += n_pcas if isinstance(n_pcas, int) else 0
            click.echo(
                f"[{ts}] pca page={p}/{tp}  本页 {n_pcas:>3} PCA × {n_items} items  |  "
                f"累计 {state['fetched_total']} PCA (服务端 {tr})",
                err=True,
            )
        elif event == "fetcher.pca.empty_page":
            p = x.get("page", "?")
            click.echo(f"[{ts}] pca page={p} → 空页,结束", err=True)
        # ─── PCA pipeline 事件 ─────────────────────────────────────
        elif event == "pipeline.pca.fetched":
            click.echo(f"[{ts}] PCA 抓取完成,共 {x.get('n', 0)} 条头部", err=True)
        elif event == "pipeline.pca.progress":
            click.echo(
                f"[{ts}]   已提交 {x.get('committed_heads', 0)} 个 PCA 头 "
                f"({x.get('items', 0)} items)...",
                err=True,
            )
        elif event == "pipeline.pca.done":
            click.echo(
                f"[{ts}] PCA 入库完成: {x.get('pcas', 0)} 个 PCA × "
                f"{x.get('items', 0)} items  failed={x.get('pcas_failed', 0)} "
                f"date={x.get('date_start')}..{x.get('date_end')}",
                err=True,
            )
        # ─── PCA 缓存救回事件 ──────────────────────────────────────
        elif event == "pipeline.pca_cache.files":
            click.echo(f"[{ts}] 发现 {x.get('n_files', 0)} 个 PCA 缓存文件,开始救回...", err=True)
        elif event == "pipeline.pca_cache.progress":
            click.echo(
                f"[{ts}]   已提交 {x.get('committed_heads', 0)} 个 PCA 头 "
                f"({x.get('items', 0)} items)...",
                err=True,
            )
        elif event == "pipeline.pca_cache.done":
            click.echo(
                f"[{ts}] PCA 缓存救回完成: {x.get('files', 0)} 文件 → "
                f"{x.get('pcas', 0)} 个 PCA × {x.get('items', 0)} items "
                f"failed={x.get('pcas_failed', 0)}",
                err=True,
            )
        # ─── Compras.gov.br 目录事件 ───────────────────────────────
        elif event == "fetcher.compras_catalogo.page.done":
            modulo = x.get("modulo", "?")
            p = x.get("page", "?")
            tp = x.get("total_paginas", "?")
            r = x.get("records_in_page", 0) or 0
            tr = x.get("total_registros", "?")
            click.echo(
                f"[{ts}] 目录[{modulo}] page={p}/{tp}  本页 {r:>3} 条 (服务端 {tr})",
                err=True,
            )
        elif event == "pipeline.catalogo.module_done":
            click.echo(
                f"[{ts}] 目录[{x.get('modulo')}] 入库 {x.get('n', 0)} 条", err=True
            )
        elif event == "pipeline.catalogo.done":
            click.echo(
                f"[{ts}] 目录入库完成: material={x.get('material', 0)} "
                f"servico={x.get('servico', 0)} total={x.get('total', 0)}",
                err=True,
            )
        # ─── 富化(region / fx / orgao / catalogo-itens)事件 ──────
        elif event == "pipeline.enrichment.done":
            click.echo(
                f"[{ts}] 富化完成: "
                f"region={x.get('region_filled', 0)} "
                f"fx={x.get('fx_filled', 0)} "
                f"orgaos={x.get('orgaos_upserted', 0)} "
                f"itens_catalogo={x.get('itens_catalogo_filled', 0)}",
                err=True,
            )
        # ─── PDF 富化事件 ──────────────────────────────────────────
        elif event == "pipeline.pdf.candidates":
            click.echo(f"[{ts}] 待解析 PDF 的招标: {x.get('n', 0)} 条", err=True)
        elif event == "fetcher.pdf.extracted":
            tl = x.get("text_len", 0)
            pb = x.get("pdf_bytes", 0)
            click.echo(f"[{ts}]   PDF 解析: {pb//1024} KB → {tl} 字符", err=True)
        elif event == "fetcher.pdf.too_large_skip":
            click.echo(f"[{ts}]   PDF 过大跳过 ({x.get('size',0)//1024} KB)", err=True)
        elif event == "pipeline.pdf.done":
            click.echo(
                f"[{ts}] PDF 富化完成: 处理 {x.get('processed',0)} 条, "
                f"有文本 {x.get('with_text',0)}, 空 {x.get('empty_text',0)}, "
                f"二次过滤掉 {x.get('refiltered_out',0)}",
                err=True,
            )
            rh = x.get("rule_hits", {})
            if rh:
                hits = ", ".join(f"{k}={v}" for k, v in sorted(rh.items()))
                click.echo(f"           二次硬过滤命中: {hits}", err=True)
        # ─── 维度分类事件 ──────────────────────────────────────────
        elif event == "pipeline.classification.done":
            click.echo(
                f"[{ts}] 分类完成: 处理 {x.get('processed',0)} 条, "
                f"已分类 {x.get('classified',0)}, 未分类 {x.get('uncategorized',0)}, "
                f"低置信 {x.get('low_confidence',0)}",
                err=True,
            )
            bd = x.get("by_dimension", {})
            if bd:
                dist = ", ".join(f"{k}={v}" for k, v in sorted(bd.items(), key=lambda kv: -kv[1]))
                click.echo(f"           维度分布: {dist}", err=True)
        # ─── atualizacao pipeline 事件 ─────────────────────────────
        elif event == "pipeline.atualizacao.plan":
            ds = x.get("date_start", "?")
            de = x.get("date_end", "?")
            mode = x.get("mode", "?")
            write_cursor = "写 cursor" if x.get("cursor_write") else "不写 cursor"
            click.echo(
                f"[{ts}] 增量计划: {ds}..{de}  (mode={mode}, {write_cursor})",
                err=True,
            )
        elif event == "pipeline.atualizacao.done":
            ck = x.get("contratacoes_kept", 0)
            cf = x.get("contratacoes_filtered", 0)
            click.echo(
                f"[{ts}] 增量同步完成 kept={ck} filtered={cf} "
                f"date={x.get('date_start')}..{x.get('date_end')}",
                err=True,
            )
        # ─── pipeline 事件 ─────────────────────────────────────────
        elif event == "pipeline.publicacao.fetched":
            n = x.get("n", 0)
            click.echo(f"[{ts}] 主表抓取完成,共 {n} 条 record", err=True)
        elif event == "pipeline.contratacoes.upserted":
            n = x.get("n", 0)
            click.echo(f"[{ts}] contratacoes 表已 UPSERT {n} 条", err=True)
        elif event == "pipeline.itens.fetch_failed":
            pid = x.get("pncp_id", "?")
            st = x.get("status")
            click.echo(f"[{ts}] 拉明细失败 pncp_id={pid} status={st},跳过", err=True)
        elif event == "pipeline.filter.done":
            kept = x.get("kept", 0)
            filtered = x.get("filtered", 0)
            rh = x.get("rule_hits", {})
            th = x.get("tag_hits", {})
            mode = x.get("mode", "?")
            click.echo(
                f"[{ts}] 过滤完成 (mode={mode}): keep={kept}  filtered={filtered}",
                err=True,
            )
            if rh:
                hits = ", ".join(f"{k}={v}" for k, v in sorted(rh.items()))
                click.echo(f"           硬过滤命中: {hits}", err=True)
            if th:
                hits = ", ".join(f"{k}={v}" for k, v in sorted(th.items()))
                click.echo(f"           软标注命中: {hits}", err=True)
        elif event == "pipeline.done":
            ck = x.get("contratacoes_kept", x.get("contratacoes", 0))
            cf = x.get("contratacoes_filtered", 0)
            i = x.get("itens", 0)
            f = x.get("itens_failed_pncp_ids", 0)
            click.echo(
                f"[{ts}] Pipeline 完成: kept={ck} filtered={cf} itens={i} itens_failed={f}",
                err=True,
            )

    return sink


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="brazil-crawler")
def cli() -> None:
    """巴西 PNCP 招投标爬虫 CLI。

    服务于六大维度商机捕捉:
    数字经济 / 医疗医药 / 高端制造 / 大宗商贸 / 跨境电商 / 文化体育。
    """


# ─── fetch-publicacao ────────────────────────────────────────────────────


@cli.command("fetch-publicacao")
@click.option("--start", "date_start", required=True, help="起始发布日期 YYYY-MM-DD")
@click.option("--end", "date_end", required=True, help="截止发布日期 YYYY-MM-DD")
@click.option(
    "--modalidade",
    "modalidade_codes",
    type=int,
    multiple=True,
    help="modalidade 代码,可多次传(--modalidade 6 --modalidade 4);不传时用 modalidades.yaml 的 default_iter",
)
@click.option(
    "--page-size",
    type=int,
    default=None,
    help="每页条数(默认读 settings.yaml 的 pncp.page_size)",
)
@click.option(
    "--cache-dir",
    type=click.Path(),
    default="./data/raw",
    show_default=True,
    help="原始 JSON 缓存根目录",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="最多输出多少条到 stdout(调试用);不传则全部",
)
@click.option(
    "--progress/--no-progress",
    default=True,
    show_default=True,
    help="实时进度行打到 stderr。关掉就只剩 JSON 审计日志",
)
def fetch_publicacao_cmd(
    date_start: str,
    date_end: str,
    modalidade_codes: tuple[int, ...],
    page_size: int | None,
    cache_dir: str,
    limit: int | None,
    progress: bool,
) -> None:
    """按发布日期范围拉取 PNCP /contratacoes/publicacao。

    \b
    示例:
        python -m src.cli fetch-publicacao --start 2026-05-26 --end 2026-05-26
        python -m src.cli fetch-publicacao --start 2026-05-26 --end 2026-05-26 --modalidade 6 --limit 5
    """
    try:
        ds = date.fromisoformat(date_start)
        de = date.fromisoformat(date_end)
    except ValueError as exc:
        raise click.BadParameter(f"日期格式必须是 YYYY-MM-DD: {exc}") from exc

    codes: list[int] | None = list(modalidade_codes) or None
    cache_root = Path(cache_dir).resolve()

    async def _run() -> None:
        count = 0
        printed = 0
        async for record in fetch_publicacao_all(
            ds, de, codes, page_size=page_size, cache_root=cache_root
        ):
            count += 1
            if limit is None or printed < limit:
                summary = {
                    "pncp_id": record.get("numeroControlePNCP"),
                    "modalidade": record.get("modalidadeNome"),
                    "orgao": (record.get("orgaoEntidade") or {}).get("razaoSocial"),
                    "uf": (record.get("unidadeOrgao") or {}).get("ufSigla"),
                    "valor_estimado": record.get("valorTotalEstimado"),
                    "objeto": (record.get("objetoCompra") or "")[:80],
                }
                click.echo(json.dumps(summary, ensure_ascii=False))
                printed += 1
            if limit is not None and count >= limit:
                # 仍要把生成器拉完,否则 HttpClient 不会关掉;但 --limit 时只取这么多条
                break

        logger.bind(count=count, cache_dir=str(cache_root)).info("cli.fetch_publicacao.done")
        click.echo(f"\nTotal fetched: {count}  (cache at: {cache_root})", err=True)

    # --progress 模式下:把现有 JSON 审计 sink 全部移除,只挂进度 sink。
    # 这样 stderr 上只有干净的进度行,而不是被 200 字符的 JSON 长行刷屏。
    # ERROR 还是会通过 progress sink 走(我们能补打一行人读 ERROR),不过现在没必要。
    # --no-progress 时保留默认 JSON 审计行为,适合自动化 / 排查。
    progress_sink_id: int | None = None
    if progress:
        logger.remove()  # 清掉默认 JSON sink,避免干扰
        progress_sink_id = logger.add(_make_progress_sink(), level="INFO")
    try:
        asyncio.run(_run())
    finally:
        if progress_sink_id is not None:
            logger.remove(progress_sink_id)


# ─── fetch-and-store ────────────────────────────────────────────────────


@cli.command("fetch-and-store")
@click.option("--start", "date_start", required=True, help="起始发布日期 YYYY-MM-DD")
@click.option("--end", "date_end", required=True, help="截止发布日期 YYYY-MM-DD")
@click.option(
    "--modalidade",
    "modalidade_codes",
    type=int,
    multiple=True,
    help="modalidade 代码,可多次传;不传时用 modalidades.yaml 的 default_iter",
)
@click.option("--page-size", type=int, default=None, help="主表每页条数")
@click.option(
    "--cache-dir",
    type=click.Path(),
    default="./data/raw",
    show_default=True,
    help="原始 JSON 缓存根目录",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="最多处理多少条主表 record(调试用)",
)
@click.option(
    "--itens-concurrency",
    type=int,
    default=5,
    show_default=True,
    help="明细 fetcher 的最大并发数",
)
@click.option(
    "--progress/--no-progress",
    default=True,
    show_default=True,
    help="实时进度行打到 stderr",
)
def fetch_and_store_cmd(
    date_start: str,
    date_end: str,
    modalidade_codes: tuple[int, ...],
    page_size: int | None,
    cache_dir: str,
    limit: int | None,
    itens_concurrency: int,
    progress: bool,
) -> None:
    """端到端:抓 publicacao + 拉明细 + 都入 SQLite。

    \b
    示例:
        python -m src.cli fetch-and-store --start 2026-05-26 --end 2026-05-26 --modalidade 6 --limit 5
    """
    try:
        ds = date.fromisoformat(date_start)
        de = date.fromisoformat(date_end)
    except ValueError as exc:
        raise click.BadParameter(f"日期格式必须是 YYYY-MM-DD: {exc}") from exc

    codes: list[int] | None = list(modalidade_codes) or None
    cache_root = Path(cache_dir).resolve()

    progress_sink_id: int | None = None
    if progress:
        logger.remove()
        progress_sink_id = logger.add(_make_progress_sink(), level="INFO")
    try:
        counters = asyncio.run(
            run_publicacao_with_itens(
                ds,
                de,
                codes,
                page_size=page_size,
                cache_root=cache_root,
                limit=limit,
                itens_concurrency=itens_concurrency,
            )
        )
        rule_hits = counters.get("rule_hits", {})
        tag_hits = counters.get("tag_hits", {})
        click.echo(
            "\nDone: "
            f"kept={counters['contratacoes_kept']}  "
            f"filtered={counters['contratacoes_filtered']}  "
            f"itens={counters['itens']}  "
            f"itens_failed={counters['itens_failed_pncp_ids']}  "
            f"mode={counters['filter_mode']}",
            err=True,
        )
        if rule_hits:
            click.echo(
                f"  硬过滤命中: " + ", ".join(f"{k}={v}" for k, v in sorted(rule_hits.items())),
                err=True,
            )
        if tag_hits:
            click.echo(
                f"  软标注命中: " + ", ".join(f"{k}={v}" for k, v in sorted(tag_hits.items())),
                err=True,
            )
    finally:
        if progress_sink_id is not None:
            logger.remove(progress_sink_id)


# ─── fetch-atualizacao(增量同步)──────────────────────────────────────


@cli.command("fetch-atualizacao")
@click.option(
    "--start",
    "date_start",
    default=None,
    help="起始 dataAtualizacao YYYY-MM-DD;不传时走 cursor 或兜底",
)
@click.option(
    "--end",
    "date_end",
    default=None,
    help="截止 dataAtualizacao YYYY-MM-DD;不传时用 today",
)
@click.option(
    "--full",
    "force_full",
    is_flag=True,
    default=False,
    help="强制兜底全量(today-7d → today),不读 cursor,但会写 cursor",
)
@click.option(
    "--modalidade",
    "modalidade_codes",
    type=int,
    multiple=True,
    help="modalidade 代码,可多次传;不传时用 modalidades.yaml 的 default_iter",
)
@click.option(
    "--page-size", type=int, default=None, help="每页条数(默认 settings.pncp.page_size)"
)
@click.option(
    "--cache-dir",
    type=click.Path(),
    default="./data/raw",
    show_default=True,
    help="原始 JSON 缓存根目录",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="最多处理多少条主表 record(调试用)",
)
@click.option(
    "--itens-concurrency",
    type=int,
    default=5,
    show_default=True,
    help="明细 fetcher 的最大并发数",
)
@click.option(
    "--skip-itens",
    is_flag=True,
    default=False,
    help="只拉主表,不拉明细(更快;明细可后续单独跑)",
)
@click.option(
    "--progress/--no-progress",
    default=True,
    show_default=True,
    help="实时进度行打到 stderr",
)
def fetch_atualizacao_cmd(
    date_start: str | None,
    date_end: str | None,
    force_full: bool,
    modalidade_codes: tuple[int, ...],
    page_size: int | None,
    cache_dir: str,
    limit: int | None,
    itens_concurrency: int,
    skip_itens: bool,
    progress: bool,
) -> None:
    """增量同步 PNCP /contratacoes/atualizacao。

    \b
    三种模式:
      默认(无参数):走 sync_cursor 增量。首次跑用 today-7d 兜底
      --full:不读 cursor,跑 today-7d → today;**写** cursor
      --start/--end:显式日期范围,**不读不写** cursor(用于补历史)

    \b
    示例:
        python -m src.cli fetch-atualizacao                          # 增量
        python -m src.cli fetch-atualizacao --full                   # 兜底全量
        python -m src.cli fetch-atualizacao --start 2026-05-01 --end 2026-05-15
    """
    cache_root = Path(cache_dir).resolve()

    # 校验日期
    if (date_start is None) != (date_end is None):
        raise click.UsageError("--start 和 --end 要么都传要么都不传")

    codes: list[int] | None = list(modalidade_codes) or None

    progress_sink_id: int | None = None
    if progress:
        logger.remove()
        progress_sink_id = logger.add(_make_progress_sink(), level="INFO")
    try:
        counters = asyncio.run(
            run_atualizacao_incremental(
                force_full=force_full,
                date_start=date_start,
                date_end=date_end,
                modalidade_codes=codes,
                page_size=page_size,
                cache_root=cache_root,
                limit=limit,
                itens_concurrency=itens_concurrency,
                skip_itens=skip_itens,
            )
        )
        rule_hits = counters.get("rule_hits", {})
        tag_hits = counters.get("tag_hits", {})
        click.echo(
            "\nDone: "
            f"date={counters['date_start']}..{counters['date_end']}  "
            f"mode={counters['mode_used']}  "
            f"kept={counters['contratacoes_kept']}  "
            f"filtered={counters['contratacoes_filtered']}  "
            f"itens={counters['itens']}",
            err=True,
        )
        if rule_hits:
            click.echo(
                "  硬过滤命中: " + ", ".join(f"{k}={v}" for k, v in sorted(rule_hits.items())),
                err=True,
            )
        if tag_hits:
            click.echo(
                "  软标注命中: " + ", ".join(f"{k}={v}" for k, v in sorted(tag_hits.items())),
                err=True,
            )
    finally:
        if progress_sink_id is not None:
            logger.remove(progress_sink_id)


# ─── fetch-proposta(投标期内的招标)────────────────────────────────────


@cli.command("fetch-proposta")
@click.option(
    "--data-final",
    "data_final",
    default=None,
    help="投标截止日上限 YYYY-MM-DD;不传时用 today。实测语义:返回截止日 ≤ 该日期的招标",
)
@click.option(
    "--modalidade",
    "modalidade_codes",
    type=int,
    multiple=True,
    help="modalidade 代码,可多次传;不传时用 modalidades.yaml 的 default_iter",
)
@click.option("--page-size", type=int, default=None, help="每页条数")
@click.option(
    "--cache-dir",
    type=click.Path(),
    default="./data/raw",
    show_default=True,
    help="原始 JSON 缓存根目录",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="最多处理多少条主表 record(调试用)",
)
@click.option(
    "--itens-concurrency",
    type=int,
    default=5,
    show_default=True,
    help="明细 fetcher 的最大并发数",
)
@click.option(
    "--skip-itens",
    is_flag=True,
    default=False,
    help="只拉主表,不拉明细(更快)",
)
@click.option(
    "--progress/--no-progress",
    default=True,
    show_default=True,
    help="实时进度行打到 stderr",
)
def fetch_proposta_cmd(
    data_final: str | None,
    modalidade_codes: tuple[int, ...],
    page_size: int | None,
    cache_dir: str,
    limit: int | None,
    itens_concurrency: int,
    skip_itens: bool,
    progress: bool,
) -> None:
    """抓取"投标截止日 ≤ data_final"的招标(实测,跟 docs 描述有出入)。

    \b
    proposta 是"快照型"查询,不涉及 cursor — 每天跑一次,
    新 pncp_id 入库,已入库的被 UPSERT 到最新状态。
    业务方可用 record 内的 dataEncerramentoProposta(精确到时分秒)做二次筛选,
    比如"截止日 ≥ now() 的才是真的还能投"。

    \b
    示例:
        python -m src.cli fetch-proposta                          # data_final=today
        python -m src.cli fetch-proposta --data-final 2026-06-01 --modalidade 6 --limit 20
    """
    from datetime import datetime as _dt

    if data_final is None:
        df = _dt.utcnow().date()
    else:
        try:
            df = date.fromisoformat(data_final)
        except ValueError as exc:
            raise click.BadParameter(f"日期格式必须是 YYYY-MM-DD: {exc}") from exc

    codes: list[int] | None = list(modalidade_codes) or None
    cache_root = Path(cache_dir).resolve()

    progress_sink_id: int | None = None
    if progress:
        logger.remove()
        progress_sink_id = logger.add(_make_progress_sink(), level="INFO")
    try:
        counters = asyncio.run(
            run_proposta_with_itens(
                df,
                codes,
                page_size=page_size,
                cache_root=cache_root,
                limit=limit,
                itens_concurrency=itens_concurrency,
                skip_itens=skip_itens,
            )
        )
        rule_hits = counters.get("rule_hits", {})
        tag_hits = counters.get("tag_hits", {})
        click.echo(
            "\nDone: "
            f"data_final={df.isoformat()}  "
            f"kept={counters['contratacoes_kept']}  "
            f"filtered={counters['contratacoes_filtered']}  "
            f"itens={counters['itens']}  "
            f"mode={counters['filter_mode']}",
            err=True,
        )
        if rule_hits:
            click.echo(
                "  硬过滤命中: " + ", ".join(f"{k}={v}" for k, v in sorted(rule_hits.items())),
                err=True,
            )
        if tag_hits:
            click.echo(
                "  软标注命中: " + ", ".join(f"{k}={v}" for k, v in sorted(tag_hits.items())),
                err=True,
            )
    finally:
        if progress_sink_id is not None:
            logger.remove(progress_sink_id)


# ─── fetch-contratos(已签合同)────────────────────────────────────────


@cli.command("fetch-contratos")
@click.option("--start", "date_start", required=True, help="起始 dataPublicacaoPncp YYYY-MM-DD")
@click.option("--end", "date_end", required=True, help="截止 dataPublicacaoPncp YYYY-MM-DD")
@click.option(
    "--page-size",
    type=int,
    default=None,
    help="每页条数(默认 settings.pncp.page_size,API 强制 ≥10)",
)
@click.option(
    "--cache-dir",
    type=click.Path(),
    default="./data/raw",
    show_default=True,
    help="原始 JSON 缓存根目录",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="最多入库多少条合同(调试用)",
)
@click.option(
    "--progress/--no-progress",
    default=True,
    show_default=True,
    help="实时进度行打到 stderr",
)
def fetch_contratos_cmd(
    date_start: str,
    date_end: str,
    page_size: int | None,
    cache_dir: str,
    limit: int | None,
    progress: bool,
) -> None:
    """抓取已签合同(Contrato),入 contratos 表。

    \b
    跟招标 fetcher 完全独立:
    * 合同没有 modalidade 概念 — 不传 --modalidade
    * 不做 filter(filter 是为招标设计的)
    * 不拉明细
    * 入独立 contratos 表,不污染 contratacoes
    * UPSERT 幂等,可重跑

    \b
    示例:
        python -m src.cli fetch-contratos --start 2026-05-20 --end 2026-05-27 --limit 100
    """
    try:
        ds = date.fromisoformat(date_start)
        de = date.fromisoformat(date_end)
    except ValueError as exc:
        raise click.BadParameter(f"日期格式必须是 YYYY-MM-DD: {exc}") from exc

    cache_root = Path(cache_dir).resolve()

    progress_sink_id: int | None = None
    if progress:
        logger.remove()
        progress_sink_id = logger.add(_make_progress_sink(), level="INFO")
    try:
        counters = asyncio.run(
            run_contratos(
                ds,
                de,
                page_size=page_size,
                cache_root=cache_root,
                limit=limit,
            )
        )
        click.echo(
            f"\nDone: contratos={counters['contratos']}  "
            f"date={counters['date_start']}..{counters['date_end']}",
            err=True,
        )
    finally:
        if progress_sink_id is not None:
            logger.remove(progress_sink_id)


# ─── fetch-pca(年度采购计划)──────────────────────────────────────────


@cli.command("fetch-pca")
@click.option("--start", "date_start", required=True, help="起始 dataAtualizacaoGlobalPCA YYYY-MM-DD")
@click.option("--end", "date_end", required=True, help="截止 dataAtualizacaoGlobalPCA YYYY-MM-DD")
@click.option(
    "--page-size",
    type=int,
    default=None,
    help="每页 PCA 头数(默认 settings.pncp.page_size,API 强制 ≥10)",
)
@click.option(
    "--cache-dir",
    type=click.Path(),
    default="./data/raw",
    show_default=True,
    help="原始 JSON 缓存根目录",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="最多处理多少条 PCA 头部(注意:每条 PCA 内含多个 items,入库总数会更多)",
)
@click.option(
    "--progress/--no-progress",
    default=True,
    show_default=True,
    help="实时进度行打到 stderr",
)
def fetch_pca_cmd(
    date_start: str,
    date_end: str,
    page_size: int | None,
    cache_dir: str,
    limit: int | None,
    progress: bool,
) -> None:
    """抓取 PCA(年度采购计划)→ 拍扁嵌套 items → 入 pca_itens 表。

    \b
    业务价值:提前 6-12 个月预知商机(销售管线前置)。
    数据规模警告:7 天范围 PCA 头部约 100 万条,**强烈建议用 --limit**。

    \b
    示例:
        python -m src.cli fetch-pca --start 2026-05-20 --end 2026-05-27 --limit 30
        python -m src.cli fetch-pca --start 2026-05-27 --end 2026-05-27 --limit 100
    """
    try:
        ds = date.fromisoformat(date_start)
        de = date.fromisoformat(date_end)
    except ValueError as exc:
        raise click.BadParameter(f"日期格式必须是 YYYY-MM-DD: {exc}") from exc

    cache_root = Path(cache_dir).resolve()

    progress_sink_id: int | None = None
    if progress:
        logger.remove()
        progress_sink_id = logger.add(_make_progress_sink(), level="INFO")
    try:
        counters = asyncio.run(
            run_pca(
                ds,
                de,
                page_size=page_size,
                cache_root=cache_root,
                limit=limit,
            )
        )
        click.echo(
            f"\nDone: pcas={counters['pcas']}  items={counters['items']}  "
            f"failed={counters['pcas_failed']}  "
            f"date={counters['date_start']}..{counters['date_end']}",
            err=True,
        )
    finally:
        if progress_sink_id is not None:
            logger.remove(progress_sink_id)


# ─── load-pca-cache(从已落盘的 raw 救回 PCA 入库)────────────────────────


@cli.command("load-pca-cache")
@click.option(
    "--cache-dir",
    type=click.Path(),
    default="./data/raw",
    show_default=True,
    help="原始 JSON 缓存根目录(会读其下 pca/**/page_*.json)",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="最多处理多少个 PCA 头(不传则全部)",
)
@click.option(
    "--progress/--no-progress",
    default=True,
    show_default=True,
    help="实时进度行打到 stderr",
)
def load_pca_cache_cmd(cache_dir: str, limit: int | None, progress: bool) -> None:
    """从已落盘的 raw 缓存(data/raw/pca/)把 PCA 救回入库,**不重抓网络**。

    \b
    用途:fetch-pca 旧版"全抓完才提交",中断会回滚全部 → 库空但 raw 文件还在。
    本命令读那些 page_*.json 直接入库(UPSERT 幂等,可重跑)。

    \b
    示例:
        python -m src.cli load-pca-cache
        python -m src.cli load-pca-cache --limit 50000
    """
    cache_root = Path(cache_dir).resolve()

    progress_sink_id: int | None = None
    if progress:
        logger.remove()
        progress_sink_id = logger.add(_make_progress_sink(), level="INFO")
    try:
        counters = load_pca_from_cache(cache_root=cache_root, limit=limit)
        click.echo(
            f"\nDone: files={counters['files']}  pcas={counters['pcas']}  "
            f"items={counters['items']}  failed={counters['pcas_failed']}",
            err=True,
        )
    finally:
        if progress_sink_id is not None:
            logger.remove(progress_sink_id)


# ─── fetch-catalogo(Compras.gov.br CATMAT/CATSER 目录)──────────────────


@cli.command("fetch-catalogo")
@click.option(
    "--tipo",
    type=click.Choice(["material", "servico", "both"], case_sensitive=False),
    default="both",
    show_default=True,
    help="拉哪类目录:material(CATMAT)/ servico(CATSER)/ both",
)
@click.option(
    "--page-size",
    type=int,
    default=None,
    help="每页条数(默认 settings.compras_gov.page_size=500,API 强制 10~500)",
)
@click.option(
    "--cache-dir",
    type=click.Path(),
    default="./data/raw",
    show_default=True,
    help="原始 JSON 缓存根目录",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="每个 tipo 最多入库多少条(调试用;全量不传)。注意 material 全量约 34 万条",
)
@click.option(
    "--progress/--no-progress",
    default=True,
    show_default=True,
    help="实时进度行打到 stderr",
)
def fetch_catalogo_cmd(
    tipo: str,
    page_size: int | None,
    cache_dir: str,
    limit: int | None,
    progress: bool,
) -> None:
    """抓 Compras.gov.br 标准品类目录(CATMAT/CATSER)→ 入 catalogo_compras 维表。

    \b
    富化层参考数据:把裸编码映射成可读「大类/类/PDM + 描述」,供富化 itens 品类
    与维度分类用。UPSERT 幂等,可断点重跑。

    \b
    ⚠️ material(CATMAT)全量约 34 万条(~700 页),首次全量拉几分钟。
    servico(CATSER)约 3 千条,很快。

    \b
    示例:
        python -m src.cli fetch-catalogo --tipo servico            # 服务目录(小,先试)
        python -m src.cli fetch-catalogo --tipo material --limit 500
        python -m src.cli fetch-catalogo                           # both,全量
    """
    cache_root = Path(cache_dir).resolve()

    progress_sink_id: int | None = None
    if progress:
        logger.remove()
        progress_sink_id = logger.add(_make_progress_sink(), level="INFO")
    try:
        counters = asyncio.run(
            run_catalogo(
                tipo=tipo.lower(),
                page_size=page_size,
                cache_root=cache_root,
                limit=limit,
            )
        )
        click.echo(
            f"\nDone: material={counters['material']}  servico={counters['servico']}  "
            f"total={counters['total']}",
            err=True,
        )
    finally:
        if progress_sink_id is not None:
            logger.remove(progress_sink_id)


# ─── fetch-editais(Edital PDF 全文 + 二次过滤)──────────────────────


@cli.command("fetch-editais")
@click.option(
    "--limit",
    type=int,
    default=20,
    show_default=True,
    help="最多处理多少条招标(PDF 慢,默认 20;一条约 30 秒)",
)
@click.option(
    "--concurrency",
    type=int,
    default=3,
    show_default=True,
    help="并发下载数(PDF 服务器慢,别太高)",
)
@click.option(
    "--redo",
    is_flag=True,
    default=False,
    help="连已解析过的也重做(规则/PDF 更新后回灌)",
)
@click.option(
    "--progress/--no-progress",
    default=True,
    show_default=True,
    help="实时进度行打到 stderr",
)
def fetch_editais_cmd(limit: int, concurrency: int, redo: bool, progress: bool) -> None:
    """对主表里未解析的招标,下载主 Edital PDF → 提取全文 → 跑 F004-F006 二次过滤。

    \b
    流程:arquivos 元数据 → 挑主 Edital → 流式下载到内存 → pdfplumber 提取
    → 写 edital_text → re_evaluate(F004/F005/F006)→ 命中则移到 filtered_log。
    PDF 不落盘(方案 C)。

    \b
    ⚠️ 慢操作:一条招标约 30 秒(下载 + 解析)。务必用 --limit。

    \b
    示例:
        python -m src.cli fetch-editais --limit 10
        python -m src.cli fetch-editais --limit 50 --concurrency 5
    """
    progress_sink_id: int | None = None
    if progress:
        logger.remove()
        progress_sink_id = logger.add(_make_progress_sink(), level="INFO")
    try:
        counters = asyncio.run(
            run_pdf_enrichment(limit=limit, concurrency=concurrency, redo=redo)
        )
        click.echo(
            f"\nDone: processed={counters['processed']}  "
            f"with_text={counters['with_text']}  "
            f"empty_text={counters['empty_text']}  "
            f"refiltered_out={counters['refiltered_out']}",
            err=True,
        )
        rh = counters.get("rule_hits", {})
        if rh:
            click.echo(
                "  二次硬过滤命中: " + ", ".join(f"{k}={v}" for k, v in sorted(rh.items())),
                err=True,
            )
    finally:
        if progress_sink_id is not None:
            logger.remove(progress_sink_id)


# ─── classify(六大维度分类)──────────────────────────────────────────


@cli.command("classify")
@click.option(
    "--limit",
    type=int,
    default=None,
    help="最多分类多少条(不传则全部未分类的)",
)
@click.option(
    "--redo",
    is_flag=True,
    default=False,
    help="连已分类的也重做(dimension_keywords.yaml 更新后回灌)",
)
@click.option(
    "--progress/--no-progress",
    default=True,
    show_default=True,
    help="进度行打到 stderr",
)
def classify_cmd(limit: int | None, redo: bool, progress: bool) -> None:
    """对已入库的招标跑六大维度分类(数字经济/医疗医药/高端制造/大宗商贸/跨境电商/文化体育)。

    \b
    文本源:objeto_compra + informacao_complementar + edital_text(若 PDF 已解析)。
    更新 dimension_primary / secondary / confidence / match_reason 四个字段。

    \b
    示例:
        python -m src.cli classify              # 分类所有还没分类的
        python -m src.cli classify --redo       # 词典更新后全部重分
    """
    progress_sink_id: int | None = None
    if progress:
        logger.remove()
        progress_sink_id = logger.add(_make_progress_sink(), level="INFO")
    try:
        counters = asyncio.run(run_classification(limit=limit, redo=redo))
        click.echo(
            f"\nDone: processed={counters['processed']}  "
            f"classified={counters['classified']}  "
            f"uncategorized={counters['uncategorized']}  "
            f"low_confidence={counters['low_confidence']}",
            err=True,
        )
        bd = counters.get("by_dimension", {})
        if bd:
            click.echo(
                "  维度分布: "
                + ", ".join(f"{k}={v}" for k, v in sorted(bd.items(), key=lambda kv: -kv[1])),
                err=True,
            )
    finally:
        if progress_sink_id is not None:
            logger.remove(progress_sink_id)


# ─── enrich(富化:区域 / 汇率 / 机构画像 / itens 目录)──────────────────


@cli.command("enrich")
@click.option("--region", is_flag=True, default=False, help="只跑区域富化(UF→大区/GDP 分层)")
@click.option("--fx", is_flag=True, default=False, help="只跑汇率富化(BRL→CNY)")
@click.option("--orgao", is_flag=True, default=False, help="只跑机构画像(聚合 contratacoes→orgaos)")
@click.option("--catalogo", is_flag=True, default=False, help="只跑 itens 目录富化(裸编码→可读类目)")
@click.option(
    "--with-brasilapi",
    is_flag=True,
    default=False,
    help="机构画像额外调 BrasilAPI 补权威名称/所在地(每个 CNPJ 一次,慢)",
)
@click.option("--limit", type=int, default=None, help="region/fx/catalogo 各自最多处理多少行")
@click.option("--redo", is_flag=True, default=False, help="连已富化过的(对应列非空)也重做")
@click.option(
    "--progress/--no-progress",
    default=True,
    show_default=True,
    help="进度行打到 stderr",
)
def enrich_cmd(
    region: bool,
    fx: bool,
    orgao: bool,
    catalogo: bool,
    with_brasilapi: bool,
    limit: int | None,
    redo: bool,
    progress: bool,
) -> None:
    """富化已入库数据:区域 / 汇率(BRL→CNY)/ 机构画像 / itens 目录类目。

    \b
    不传任何 --region/--fx/--orgao/--catalogo 开关 → 四块全跑。
    传了任意一个 → 只跑被指定的那些。

    \b
    示例:
        python -m src.cli enrich                       # 四块全跑
        python -m src.cli enrich --region --fx         # 只跑区域+汇率
        python -m src.cli enrich --orgao --with-brasilapi
        python -m src.cli enrich --redo                # 全部重做(汇率刷新后回灌)
    """
    any_specified = region or fx or orgao or catalogo
    do_region = region if any_specified else True
    do_fx = fx if any_specified else True
    do_orgao = orgao if any_specified else True
    do_catalogo = catalogo if any_specified else True

    progress_sink_id: int | None = None
    if progress:
        logger.remove()
        progress_sink_id = logger.add(_make_progress_sink(), level="INFO")
    try:
        counters = asyncio.run(
            run_enrichment(
                do_region=do_region,
                do_fx=do_fx,
                do_orgao=do_orgao,
                do_catalogo_itens=do_catalogo,
                with_brasilapi=with_brasilapi,
                limit=limit,
                redo=redo,
            )
        )
        rate = counters.get("fx_rate")
        click.echo(
            "\nDone: "
            f"region={counters['region_filled']}  "
            f"fx={counters['fx_filled']}"
            + (f" (1 BRL={rate} CNY)" if rate else "")
            + f"  orgaos={counters['orgaos_upserted']}  "
            f"itens_catalogo={counters['itens_catalogo_filled']}",
            err=True,
        )
    finally:
        if progress_sink_id is not None:
            logger.remove(progress_sink_id)


# ─── filter(对已入库数据重跑过滤)────────────────────────────────────


@cli.command("filter")
@click.option(
    "--mode",
    type=click.Choice(["hard_delete", "soft_delete"], case_sensitive=False),
    default=None,
    help="覆盖 settings.yaml 的 filter.mode;不传则用配置",
)
@click.option(
    "--progress/--no-progress",
    default=True,
    show_default=True,
    help="进度行打到 stderr",
)
def filter_cmd(mode: str | None, progress: bool) -> None:
    """重跑过滤:遍历 contratacoes 表所有 record,按当前规则重新求值。

    \b
    hard_delete:命中 → 从主表删除并写入 filtered_log
    soft_delete:命中 → 主表保留,只更新 filter_reason / filter_rule_id

    用法:
        python -m src.cli filter                  # 用 settings.yaml 的 mode
        python -m src.cli filter --mode soft_delete
    """
    from collections import Counter
    from datetime import datetime

    from sqlalchemy import select

    from .core.settings import get_filter_settings
    from .filters import apply_result, get_default_engine, write_filtered_log
    from .storage import Contratacao, session_scope

    progress_sink_id: int | None = None
    if progress:
        logger.remove()
        progress_sink_id = logger.add(_make_progress_sink(), level="INFO")

    try:
        engine = get_default_engine()
        cfg = get_filter_settings()
        effective_mode = (mode or cfg["mode"]).lower()
        filtered_log_dir = Path(cfg["filtered_log_dir"])
        set_field_to_tag_id = {r.set_field: r.id for r in engine.rules.soft_tags}

        rule_hits: Counter[str] = Counter()
        tag_hits: Counter[str] = Counter()
        deletions: list[dict[str, Any]] = []
        total = 0
        retagged = 0

        with session_scope() as session:
            for row in session.scalars(select(Contratacao)):
                total += 1
                data = {col.name: getattr(row, col.name) for col in row.__table__.columns}
                result = engine.evaluate(data)

                if result.keep:
                    if result.tags:
                        apply_result(row, result)
                        retagged += 1
                        for set_field in result.tags:
                            tag_id = set_field_to_tag_id.get(set_field)
                            if tag_id:
                                tag_hits[tag_id] += 1
                else:
                    assert result.hit_rule_id is not None
                    rule_hits[result.hit_rule_id] += 1
                    if effective_mode == "soft_delete":
                        apply_result(row, result)
                    else:
                        d = dict(data)
                        d["filter_rule_id"] = result.hit_rule_id
                        d["filter_reason"] = result.reason
                        d["filtered_at"] = datetime.utcnow().isoformat()
                        deletions.append(d)
                        session.delete(row)

        if deletions:
            try:
                write_filtered_log(deletions, filtered_log_dir)
            except Exception as exc:
                logger.bind(error=str(exc)).error("cli.filter.filtered_log_write_failed")

        click.echo(
            f"\n过滤回灌完成 (mode={effective_mode}):\n"
            f"  扫描总数:   {total}\n"
            f"  保留:       {total - sum(rule_hits.values())}  (其中重新打标 {retagged} 条)\n"
            f"  硬过滤命中: {sum(rule_hits.values())}  "
            + (
                f"({', '.join(f'{k}={v}' for k, v in sorted(rule_hits.items()))})"
                if rule_hits
                else ""
            )
            + "\n"
            f"  软标注命中: " + (
                f"{', '.join(f'{k}={v}' for k, v in sorted(tag_hits.items()))}"
                if tag_hits
                else "(无)"
            ),
            err=True,
        )
    finally:
        if progress_sink_id is not None:
            logger.remove(progress_sink_id)


# ─── export-excel(多 Sheet 业务报告)──────────────────────────────────


@cli.command("export-excel")
@click.option(
    "--out",
    "out_path",
    default="./data/exports/report.xlsx",
    show_default=True,
    help="输出 .xlsx 路径",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="每张表最多导多少行(不传则全部)",
)
def export_excel_cmd(out_path: str, limit: int | None) -> None:
    """把数据库导成多 Sheet Excel 报告(业务方直接打开分析)。

    \b
    6 个 Sheet:招标主表 / 采购明细 / 已签合同 / 年度采购计划 / 维度透视 / 过滤审计。
    招标主表带六大维度中文标签;金额日期已格式化;首行冻结 + 自动筛选。

    \b
    示例:
        python -m src.cli export-excel --out report.xlsx
    """
    stats = export_to_excel(out_path, limit=limit)
    click.echo(f"\n已导出: {Path(out_path).resolve()}", err=True)
    for sheet, n in stats.items():
        click.echo(f"  {sheet}: {n} 行", err=True)


if __name__ == "__main__":
    cli()
