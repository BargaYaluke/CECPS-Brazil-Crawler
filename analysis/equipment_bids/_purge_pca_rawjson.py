# -*- coding: utf-8 -*-
"""一次性维护:清空 pca_itens.raw_json(48GB 重复留底,无人读取)+ VACUUM 收回空间。
保留 pca_itens 全部结构化列与其余所有表。详见对话/README。"""
import sqlite3, time, os

DB = r"C:\巴西爬虫\data\procurement.db"

def gb(p): return os.path.getsize(p) / 1e9

con = sqlite3.connect(DB, isolation_level=None, timeout=600)  # autocommit(VACUUM 需要)
cur = con.cursor()
print("sqlite", sqlite3.sqlite_version, "| journal_mode",
      cur.execute("PRAGMA journal_mode").fetchone()[0], flush=True)
print(f"[start] DB = {gb(DB):.1f} GB", flush=True)

t = time.time()
cur.execute("UPDATE pca_itens SET raw_json = NULL WHERE raw_json IS NOT NULL")
print(f"[update] 置空 {cur.rowcount:,} 行 raw_json,用时 {time.time()-t:.0f}s,DB={gb(DB):.1f}GB", flush=True)

t2 = time.time()
cur.execute("VACUUM")
print(f"[vacuum] 完成,用时 {time.time()-t2:.0f}s", flush=True)

# 校验:结构化数据仍在,raw_json 已空
n = cur.execute("SELECT COUNT(*) FROM pca_itens").fetchone()[0]
nz = cur.execute("SELECT COUNT(*) FROM pca_itens WHERE raw_json IS NOT NULL").fetchone()[0]
nv = cur.execute("SELECT COUNT(*) FROM pca_itens WHERE valor_total IS NOT NULL").fetchone()[0]
con.close()
print(f"[verify] pca_itens 行数={n:,} | 残留raw_json={nz} | 有valor_total={nv:,}", flush=True)
print(f"[done] DB = {gb(DB):.1f} GB(原 ~48.5GB),总用时 {time.time()-t:.0f}s", flush=True)
