# -*- coding: utf-8 -*-
"""中葡企业 ↔ 巴西竞标 匹配流水线 —— 统一入口(两条链)。

两份交付物(差别 = 是否考虑时效):
  needfit  纯需求契合,忽略时效/本地化 → data/exports/中葡企业_..._需求匹配版.xlsx
  timed    含时效(契合+可行+时效三轴)→ data/exports/中葡企业_..._v3.xlsx

每条链有一个人工/外部对抗校验断点(step12/step8 产出 cat_verify_in*,
外部把裁决写入 cat_verdicts*,再跑 post)。

用法:
  python analysis/matching/pipeline/run.py needfit [pre|post]
  python analysis/matching/pipeline/run.py timed   [pre|post]
  python analysis/matching/pipeline/run.py all      # 两条链 pre,然后(就绪后)两条 post
  省略 pre/post = 先 pre,提示裁决断点,再 post。

路径不依赖 cwd:各步经 match_lib.ROOT/OUT 解析绝对路径。
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable

SHARED = [("shared", "step1_company_master.py"),
          ("shared", "step3_profiles.py"),
          ("shared", "step3b_profiles_fill.py")]

CHAINS = {
    "needfit": {
        "pre":  SHARED + [("needfit", "step10_needfit_pool_retrieve.py"),
                          ("needfit", "step11_needfit_score.py"),
                          ("needfit", "step12_needfit_categorize.py")],
        "post": [("needfit", "nf_fetch_pncp_data.py"),
                 ("needfit", "step13_needfit_table.py")],
        "verdict_dir": "outputs/cat_verdicts_nf/",
        "out": "中葡企业_..._需求匹配版.xlsx",
    },
    "timed": {
        "pre":  SHARED + [("timed", "step2_tenders.py"),
                          ("timed", "step4_retrieve.py"),
                          ("timed", "step5_score.py"),
                          ("timed", "step8_categorize.py")],
        "post": [("timed", "v3_fetch_pncp_data.py"),
                 ("timed", "step9_main_table_v3.py")],
        "verdict_dir": "outputs/cat_verdicts/",
        "out": "中葡企业_..._全量匹配版.xlsx",
    },
}


def run(steps: list[tuple[str, str]]) -> None:
    for sub, name in steps:
        rel = f"{sub}/{name}"
        print(f"\n{'=' * 60}\n▶ {rel}\n{'=' * 60}", flush=True)
        r = subprocess.run([PY, str(HERE / sub / name)])
        if r.returncode != 0:
            print(f"✖ {rel} 失败 (exit {r.returncode}),中止。", flush=True)
            sys.exit(r.returncode)


def run_chain(variant: str, stage: str | None) -> None:
    c = CHAINS[variant]
    if stage in (None, "pre"):
        run(c["pre"])
        print(f"\n⏸ [{variant}] pre 完成。请把外部/人工裁决写入 {c['verdict_dir']},再跑 post。")
    if stage in (None, "post"):
        if stage is None:
            print(f"   (假设 {c['verdict_dir']} 已就绪,继续 post……)")
        run(c["post"])
        print(f"\n✅ [{variant}] 完成 → data/exports/{c['out']}")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in (*CHAINS, "all"):
        print(__doc__)
        sys.exit(1)
    variant = sys.argv[1]
    stage = sys.argv[2] if len(sys.argv) > 2 else None
    if variant == "all":
        for v in CHAINS:
            run_chain(v, stage)
    else:
        run_chain(variant, stage)


if __name__ == "__main__":
    main()
