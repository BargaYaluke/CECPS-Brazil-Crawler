# -*- coding: utf-8 -*-
"""修复 verify_out 里因 evidence 等字符串内未转义双引号导致的坏 JSON。
JSON 为每字段一行的 pretty 格式 → 行级重转义:对 `"key": "value"[,]` 行,
把 value 内部未转义的 " 转成 \\"。仅重写真正修好的文件。"""
import sys, io, re, json
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
VOUT = Path(__file__).resolve().parent / "outputs" / "verify_out"

LINE = re.compile(r'^(?P<pre>\s*"[A-Za-z_]+":\s*")(?P<val>.*)(?P<post>"\s*,?)$')

def fix_line(ln):
    m = LINE.match(ln)
    if not m:
        return ln
    val = m.group("val")
    # 先还原已转义,再统一转义,避免双重转义
    val2 = val.replace('\\"', '"').replace('"', '\\"')
    return m.group("pre") + val2 + m.group("post")

fixed = []
for f in sorted(VOUT.glob("*.json")):
    raw = f.read_text(encoding="utf-8")
    try:
        json.loads(raw); continue            # 本就正常
    except Exception:
        pass
    new = "\n".join(fix_line(ln) for ln in raw.splitlines())
    try:
        json.loads(new)
        f.write_text(new, encoding="utf-8")
        fixed.append(f.name)
    except Exception as e:
        print(f"  仍坏 {f.name}: {e}")
print("已修复:", fixed if fixed else "(无需)")
