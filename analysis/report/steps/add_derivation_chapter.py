# -*- coding: utf-8 -*-
"""向 v2 报告(unpacked2/)插入第七章:从市场洞察到竞标匹配的推导方法。
   生成的 XML 完全复用报告既有样式(H1=style1 / H2=style21 / 正文sz20 / 项目符号a0 / 表格aff1)。"""
import re

DOC = "analysis/report/outputs/unpacked2/word/document.xml"

# ---- 唯一 id 计数器 ----
_pid = 0
def pid():
    global _pid; _pid += 1
    return f"0C1A{_pid:04X}"

def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ---- 样式构件 ----
def h1(text, bid, toc):
    return f'''    <w:p w14:paraId="{pid()}" w14:textId="77777777" w:rsidR="00501262" w:rsidRDefault="00000000">
      <w:pPr>
        <w:pStyle w:val="1"/>
        <w:rPr>
          <w:lang w:eastAsia="zh-CN"/>
        </w:rPr>
      </w:pPr>
      <w:bookmarkStart w:id="{bid}" w:name="{toc}"/>
      <w:r>
        <w:rPr>
          <w:rFonts w:ascii="微软雅黑" w:eastAsia="微软雅黑" w:hAnsi="微软雅黑" w:cs="微软雅黑"/>
          <w:lang w:eastAsia="zh-CN"/>
        </w:rPr>
        <w:t>{esc(text)}</w:t>
      </w:r>
      <w:bookmarkEnd w:id="{bid}"/>
    </w:p>
'''

def h2(text, bid, toc):
    return f'''    <w:p w14:paraId="{pid()}" w14:textId="77777777" w:rsidR="00501262" w:rsidRDefault="00000000">
      <w:pPr>
        <w:pStyle w:val="21"/>
      </w:pPr>
      <w:bookmarkStart w:id="{bid}" w:name="{toc}"/>
      <w:r>
        <w:rPr>
          <w:rFonts w:ascii="微软雅黑" w:eastAsia="微软雅黑" w:hAnsi="微软雅黑" w:cs="微软雅黑"/>
        </w:rPr>
        <w:t>{esc(text)}</w:t>
      </w:r>
      <w:bookmarkEnd w:id="{bid}"/>
    </w:p>
'''

def core(label, text):
    return f'''    <w:p w14:paraId="{pid()}" w14:textId="77777777" w:rsidR="00501262" w:rsidRDefault="00000000">
      <w:pPr>
        <w:spacing w:before="40" w:after="100"/>
        <w:rPr>
          <w:lang w:eastAsia="zh-CN"/>
        </w:rPr>
      </w:pPr>
      <w:r>
        <w:rPr>
          <w:b/>
          <w:color w:val="2F5597"/>
          <w:lang w:eastAsia="zh-CN"/>
        </w:rPr>
        <w:t xml:space="preserve">{esc(label)}  </w:t>
      </w:r>
      <w:r>
        <w:rPr>
          <w:b/>
          <w:lang w:eastAsia="zh-CN"/>
        </w:rPr>
        <w:t>{esc(text)}</w:t>
      </w:r>
    </w:p>
'''

def para(text):
    return f'''    <w:p w14:paraId="{pid()}" w14:textId="77777777" w:rsidR="00501262" w:rsidRDefault="00000000">
      <w:pPr>
        <w:spacing w:after="160"/>
        <w:rPr>
          <w:lang w:eastAsia="zh-CN"/>
        </w:rPr>
      </w:pPr>
      <w:r>
        <w:rPr>
          <w:sz w:val="20"/>
          <w:lang w:eastAsia="zh-CN"/>
        </w:rPr>
        <w:t>{esc(text)}</w:t>
      </w:r>
    </w:p>
'''

def bullet(lead, rest):
    lead_run = f'''      <w:r>
        <w:rPr>
          <w:b/>
          <w:lang w:eastAsia="zh-CN"/>
        </w:rPr>
        <w:t>{esc(lead)}</w:t>
      </w:r>
''' if lead else ""
    return f'''    <w:p w14:paraId="{pid()}" w14:textId="77777777" w:rsidR="00501262" w:rsidRDefault="00000000">
      <w:pPr>
        <w:pStyle w:val="a0"/>
        <w:spacing w:after="80"/>
        <w:rPr>
          <w:lang w:eastAsia="zh-CN"/>
        </w:rPr>
      </w:pPr>
{lead_run}      <w:r>
        <w:rPr>
          <w:lang w:eastAsia="zh-CN"/>
        </w:rPr>
        <w:t>{esc(rest)}</w:t>
      </w:r>
    </w:p>
'''

def _cell(text, w, header=False, align="left"):
    jc = f'\n              <w:jc w:val="{align}"/>' if align in ("center", "right") else ""
    shd = '\n            <w:shd w:val="clear" w:color="auto" w:fill="2F5597"/>' if header else ""
    if header:
        rpr = '<w:b/>\n                <w:color w:val="FFFFFF"/>\n                <w:sz w:val="18"/>'
    else:
        rpr = '<w:sz w:val="18"/>'
    pPr = f'''            <w:pPr>{jc}
            </w:pPr>
''' if jc else ""
    return f'''        <w:tc>
          <w:tcPr>
            <w:tcW w:w="{w}" w:type="dxa"/>{shd}
          </w:tcPr>
          <w:p w14:paraId="{pid()}" w14:textId="77777777" w:rsidR="00501262" w:rsidRDefault="00000000">
{pPr}            <w:r>
              <w:rPr>
                {rpr}
              </w:rPr>
              <w:t>{esc(text)}</w:t>
            </w:r>
          </w:p>
        </w:tc>
'''

def table(headers, rows, widths, aligns):
    grid = "".join(f'        <w:gridCol w:w="{w}"/>\n' for w in widths)
    # header row
    hcells = "".join(_cell(h, widths[i], header=True, align="center") for i, h in enumerate(headers))
    trs = f'''      <w:tr w:rsidR="00501262" w14:paraId="{pid()}" w14:textId="77777777">
        <w:trPr>
          <w:jc w:val="center"/>
        </w:trPr>
{hcells}      </w:tr>
'''
    for row in rows:
        cells = "".join(_cell(v, widths[i], header=False, align=aligns[i]) for i, v in enumerate(row))
        trs += f'''      <w:tr w:rsidR="00501262" w14:paraId="{pid()}" w14:textId="77777777">
        <w:trPr>
          <w:jc w:val="center"/>
        </w:trPr>
{cells}      </w:tr>
'''
    return f'''    <w:tbl>
      <w:tblPr>
        <w:tblStyle w:val="aff1"/>
        <w:tblW w:w="0" w:type="auto"/>
        <w:jc w:val="center"/>
        <w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/>
      </w:tblPr>
      <w:tblGrid>
{grid}      </w:tblGrid>
{trs}    </w:tbl>
'''

# ---- 章节内容 ----
parts = []
parts.append(h1("七、从市场洞察到竞标匹配:三分类主表是怎么推导出来的", 17, "_Toc231823641"))
parts.append(core("核心结论",
    "把前六章的三条铁律——本地化是分水岭、时效是第一道门槛、卖产品而非工程——变成可计算的筛选与评分规则,"
    "即可从「市场长什么样」推导出「哪家中企该投哪个标、走什么路径」。这正是配套交付物《中葡企业_巴西竞标匹配_三分类主表》的来历。"))
parts.append(para(
    "前六章回答的是「市场是什么样」,本章回答「怎么把它变成一张可执行的清单」。匹配主表不是另起炉灶,而是本报告结论的算法化落地:"
    "报告里的每一条市场规律,都对应表里的一个筛选门槛或一个评分维度。下面按推导顺序拆解。"))

parts.append(h2("7.1 推导总览:五步流水线", 18, "_Toc231823642"))
parts.append(para("从原始数据到主表共五步,每一步都承接前面章节的一个结论:"))
parts.append(bullet("第一步·企业建库。", "合并企业登记表与重要程度表得 437 家唯一中企,标注「重要程度」(1高 50 / 2中 65 / 3低 18 / 无标识 304)与「本地化状态」(已在巴西 22 / 葡语区 5 / 需本地化 410)——后者直接决定时效门槛。"))
parts.append(bullet("第二步·标的清洗(对应第二章「卖产品而非工程」)。", "一周 8,357 条招标,剔除 642 条已过期、6 笔金额超 5 亿的笔误、1,630 条本地工程(obra),留下 6,079 条「可投标池」。"))
parts.append(bullet("第三步·企业画像(DeepSeek)。", "逐家提取主营产品线、单标价值上限、本地化难度、政府采购契合度,作为后续打分的依据。"))
parts.append(bullet("第四步·召回 + 评分(DeepSeek-v4-flash)。", "先按关键词为每家企业召回候选标,再逐标打三维分(契合 / 可行 / 时效),综合分 = 0.45×契合 + 0.30×可行 + 0.25×时效。"))
parts.append(bullet("第五步·三分类 + 对抗校验(独立 Claude 多智能体)。", "按时效硬门槛与品类把配对分成三类,再由独立模型对抗复核、剔除错配,最终落成主表。"))

parts.append(h2("7.2 三个评分维度:业务匹配、可行性、时效", 19, "_Toc231823643"))
parts.append(para("每个「企业 × 标的」配对由模型打三个 0–10 分再加权。这三个维度正好回答中企最关心的三个问题:"))
parts.append(bullet("契合度(权重 45%)= 业务匹配程度。", "标的采购的货物是否对得上企业主营产品;同属一个行业大类但产品对不上(如同为医疗,一个卖设备、一个要诊疗服务),判低分。"))
parts.append(bullet("可行性(权重 30%)= 接不接得住。", "金额是否超出企业产能 / 单标价值上限(远超上限的判 3 分及以下);本地工程、本地人力服务类中企无法承接,判低分。"))
parts.append(bullet("时效(权重 25%)= 投标时间来不来得及。", "这一维把「本地化」与「截止日」量化,是全表最关键的约束,单列 7.3 详解。"))
parts.append(para(
    "综合分 = 0.45×契合 + 0.30×可行 + 0.25×时效。一个配对要进入「可投」,还必须同时满足 契合≥6、可行≥5、时效≥4 三道硬门槛,任一不达标即出局——这保证了「分高」一定是三方面都过关,而非靠单项拉分。"))

parts.append(h2("7.3 时效硬门槛:本地化要多久 vs 投标还剩多久", 20, "_Toc231823644"))
parts.append(para(
    "这是把第四章「本地化是分水岭」、第三章「时效是第一道门槛」落成数字的关键一步。逻辑很直接:一家中企要参与某标,"
    "先问「它需要多久才能具备投标资格」(本地化周期),再问「这个标到截止还剩多久」(剩余天数);"
    "若 剩余天数 小于 本地化周期,就是根本来不及,直接降级、不计入「可投」。本地化周期按企业身份分档(时效基准日 2026-06-09):"))
parts.append(table(
    ["身份 / 本地化状态", "本地化周期", "说明"],
    [
        ["已在巴西(有 CNPJ)", "14 天", "可直接投标近端截止标"],
        ["葡语区(较易)", "90 天", "葡语国家主体,落地较快"],
        ["需本地化 · 低", "90 天", "注册门槛较低的品类"],
        ["需本地化 · 中", "150 天", "一般制造 / 商贸品类"],
        ["需本地化 · 高", "240 天", "需 ANVISA 等认证(如医疗器械、药品)"],
    ],
    [2520, 1440, 3240],
    ["left", "center", "left"]))
parts.append(para(
    "由此得到一个关键而反直觉的结论:同一个标,对不同企业的「时效」完全不同。一张剩约 20 天的医疗设备标,"
    "对「已在巴西」的企业(只需 14 天)是「来得及、可直投」;对一家「需本地化 · 高」的中企(需 240 天)就是「来不及」,"
    "只能转入下一周期、或改走可滚动入围的长期通道(SRP 价格登记 / Credenciamento 长期挂网)。这也解释了为什么近端截止标"
    "(快照中位仅剩约 12 天)对尚未落地的中企几乎不可投——不是不想投,而是来不及办身份。"))

parts.append(h2("7.4 三个类别:可投、待牌照、高价值候选", 21, "_Toc231823645"))
parts.append(para("过了时效门槛的配对,再按「产品对口程度 + 所需条件」分成三类,即主表的「类别」列:"))
parts.append(table(
    ["类别", "进入规则(均须先过时效门槛)", "数量", "含义 / 落地路径"],
    [
        ["① 核心匹配(可投)", "货物标 + 契合≥6 + 可行≥5 + 剩余天数≥本地化周期(或无截止)", "52", "现在或本地化后即可投"],
        ["② 本地化+牌照后可争取", "业务相邻但需本地牌照 / 资质,窗口更长(剩≥365 天或无截止)", "27", "标注所需牌照,办证后争取"],
        ["③ 高价值候选(待 JV / 本地伙伴)", "全标池中金额≥500 万且剩≥90 天的大单,不绑定企业", "17", "组联合体(consórcio)或找本地伙伴"],
    ],
    [1860, 2820, 660, 1860],
    ["left", "left", "center", "left"]))
parts.append(para(
    "第三类直接呼应第四章「头部大单是项目制、需联合体」的发现——这些大单中企单打接不住,但值得作为 JV / 本地伙伴合作的标的跟踪。"))
parts.append(para(
    "分类之后再过一道独立校验:由 Claude 多智能体对每条配对对抗复核,剔除模型漏判的本地服务标"
    "(餐补卡 / 福利券需金融牌照、加油站零售、医疗诊疗服务、本地人力清洁等)。第一类经此从 143 条剔到 52 条,第二类从 36 条留 27 条——"
    "这一步是防止「产品看着对口、实则是本地服务 / 需牌照」误入可投清单的正交防线。"))

parts.append(h2("7.5 一个完整的判断链(实例)", 22, "_Toc231823646"))
parts.append(para("以最受关注的「医疗 / 大宗商贸、需本地化」为例,走一遍从市场结论到表中一行的完整判断:"))
parts.append(bullet("例 A — 物美集团(跨境商贸,需本地化 · 中)。",
    "标的=某市学校午餐食品采购(Credenciamento 长期挂网,159 万元,剩 724 天)。① 业务匹配:食品 / 商贸对口主营,契合高;"
    "② 重要程度:2 中;③ 本地化要多久:中档 150 天;④ 投标来得及吗:剩 724 天远大于 150 天,且是长期挂网可滚动入围,时效高;"
    "⑤ 可行性:159 万在产能内,可行。结论:判入 ①核心匹配,落地建议「本地化后参与学校午餐供应招标」。"))
parts.append(bullet("例 B — 同一张医疗设备标(剩约 20 天),两种企业两种命运。",
    "对 Delta Global(已在巴西,周期 14 天):20 大于 14,来得及,判 ①直投;对一家「需本地化 · 高」的中企(周期 240 天):"
    "20 远小于 240,来不及,不进可投,降级为「下一周期 · 需求情报」,或转 ②(若该标可走 SRP / 长期挂网,本地化后滚动入围)。"))
parts.append(para(
    "可见同一张标,只因企业的「本地化周期」不同就走向完全不同的结论。这正是把第三、四章的「本地化 / 时效」洞察,逐行落到每一个「企业 × 标的」配对上的机制。"))

parts.append(h2("7.6 由此得到的判断与边界", 23, "_Toc231823647"))
parts.append(bullet("印证报告核心论点。",
    "第一类 52 条里有 46 条来自「已在巴西」企业,需本地化中企本轮「立即可投」几乎为零——与第四章「外企直投仅 15 笔、本地化是分水岭」完全吻合。"))
parts.append(bullet("对重要中企的真实价值。",
    "不在「本轮抢标」,而在 ②③ 与「下一周期需求情报」——即先想清楚「该让谁去办 CNPJ / ANVISA、办好之后主攻哪些对口品类」,再谈投标。"))
parts.append(bullet("口径边界。",
    "招标侧为一周快照,剩余天数随时间变化、不可年化;三维分含模型主观判断,已用独立校验纠偏,落地前仍建议人工复核标的原文与资质要求;金额未披露标按时效与品类保留。"))

chapter_xml = "".join(parts)

# ---- 插入到 附录 之前 ----
doc = open(DOC, encoding="utf-8").read()
anchor = ('    <w:p w14:paraId="10B5C050" w14:textId="77777777" w:rsidR="00501262" w:rsidRDefault="00000000">\n'
          '      <w:pPr>\n        <w:pStyle w:val="1"/>\n        <w:rPr>\n          <w:lang w:eastAsia="zh-CN"/>\n'
          '        </w:rPr>\n      </w:pPr>\n      <w:bookmarkStart w:id="16" w:name="_Toc231823640"/>')
assert anchor in doc, "附录锚点未找到!"
assert doc.count(anchor) == 1, "锚点不唯一!"
doc = doc.replace(anchor, chapter_xml + anchor)
open(DOC, "w", encoding="utf-8").write(doc)

# 校验良构
from xml.etree import ElementTree as ET
ET.fromstring(doc)
print("插入成功,XML 良构。新增段落/元素 paraId 数:", _pid)
print("章节字符数:", len(chapter_xml))
