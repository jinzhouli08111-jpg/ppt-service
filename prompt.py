# -*- coding: utf-8 -*-
"""提示词：让模型按 ppt-master 的 SVG 契约写页面。

设计原则：
  · 契约写得足够具体，模型才过得了校验器（bounds 不重叠是最常见的失败点）
  · 字体限定在 PPT 保真范围内（Windows PowerPoint 必须装有）
  · 给一个可复制的范式，比给十条抽象规则更有效
"""

# ── 全局 SVG 契约（每页都要遵守）────────────────────────────────
SVG_CONTRACT = """# 输出格式：一页 PPT 的 SVG

只输出 <svg> 代码本身，前后**不要**任何解释文字，**不要**用 markdown 代码块包裹。

## 画布
根节点必须是：
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" width="1280" height="720" font-family="Microsoft YaHei" data-pptx-page-role="{ROLE}">

`{ROLE}` 取值只能是 cover / toc / section / content / ending。

## 分组规则（最容易出错，务必逐条检查）

1. 背景矩形直接放在根下，不包进 <g>：
   <rect id="bg" data-pptx-role="background" x="0" y="0" width="1280" height="720" fill="#FAF7F1"/>

2. **每一个**顶层 <g> 必须同时有 `id`（页面内唯一）和 `data-pptx-bounds="x y w h"`。

3. **不同顶层 <g> 的 bounds 绝对不能互相重叠**，哪怕 1px 也不行。这是硬性拒绝条件。
   安全做法：把页面纵向切成互不相交的横条，每组的 bounds 严格限定在自己的横条内。

4. bounds 必须完整包住组内所有元素，建议四边各多留 8-16px 余量。

## 文字规则

- 一个段落用一个 <text>，不要用多个 <text> 拼一段话
- 手动换行用 <tspan>：
  <text x="96" y="200" font-size="28" fill="#23201C">
    <tspan x="96" dy="0">第一行</tspan>
    <tspan x="96" dy="42">第二行</tspan>
  </text>
- font-size 写无单位数字（如 "28"，不要 "28px"）
- <text> 上只允许这些属性：x y font-family font-size font-weight font-style text-anchor letter-spacing fill
- 颜色一律大写六位 HEX：#FAF7F1（不要写 #faf7f1，不要 rgba）

## 字体（只能用这些，否则导出后掉字）

- 中文：Microsoft YaHei
- 衬线（标题）：Georgia
- 每个 font-family 只写一个字体名，不要写字体栈

## 文字边界估算（用来算 bounds）

对一个 font-size 为 S 的 text，其视觉范围约为：
  上边 = y - 0.85 × S
  下边 = y + 0.35 × S
  宽度 = 中文字数 × S + 英文单词字符数 × 0.55 × S
bounds 要比这个范围更大。

## 禁止项

- 禁止 class 属性、<style>、<foreignObject>、mask、<animate>、<script>
- 禁止 HTML 实体（&nbsp; &mdash; 等），中文标点直接写原始字符
- 禁止渐变、滤镜、阴影、圆角以外的装饰（本阶段追求干净稳定）

## 范式（照这个结构写，只改变内容和坐标）

<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" width="1280" height="720" font-family="Microsoft YaHei" data-pptx-page-role="content">
  <rect id="bg" data-pptx-role="background" x="0" y="0" width="1280" height="720" fill="#FAF7F1"/>

  <g id="header" data-pptx-bounds="80 56 1120 180">
    <text x="80" y="104" font-family="Georgia" font-size="20" font-weight="700" letter-spacing="4" fill="#A98A57">KICKER HERE</text>
    <text x="80" y="178" font-family="Georgia" font-size="48" font-weight="700" fill="#1E3527">页面标题</text>
    <rect x="80" y="206" width="96" height="4" fill="#A98A57"/>
  </g>

  <g id="body-1" data-pptx-bounds="80 268 1120 180">
    <text x="80" y="332" font-family="Georgia" font-size="36" font-weight="700" fill="#A98A57">01</text>
    <text x="156" y="320" font-family="Microsoft YaHei" font-size="28" font-weight="700" fill="#1E3527">要点标题</text>
    <text x="156" y="366" font-family="Microsoft YaHei" font-size="19" fill="#23201C">说明文字，控制在一行以内，过长就拆成两条要点。</text>
  </g>

  <g id="body-2" data-pptx-bounds="80 468 1120 180">
    <text x="80" y="532" font-family="Georgia" font-size="36" font-weight="700" fill="#A98A57">02</text>
    <text x="156" y="520" font-family="Microsoft YaHei" font-size="28" font-weight="700" fill="#1E3527">要点标题</text>
    <text x="156" y="566" font-family="Microsoft YaHei" font-size="19" fill="#23201C">说明文字。</text>
  </g>
</svg>
"""


# ── 单页内容规格 ───────────────────────────────────────────────
PAGE_TASK = """# 本次要画的一页

- 页码：第 {index} 页，共 {total} 页
- 页面角色：{role}
- 页面标题：{title}
{content}

{layout_hint}

配色（严格遵守，不要引入其它颜色）：
- 背景 {bg}
- 主色（标题、重点） {main}
- 点缀色（编号、细线、英文小标题） {accent}
- 正文字色 {ink}
- 次要文字 {muted}

设计要求：
- 1280×720 画布，左右各留 80px 边距
- 标题区与正文区分开，bounds 不要重叠
- 中文正文每行不超过 30 字
- 版面要有留白，不要把文字铺满整页
"""

LAYOUT_HINTS = {
    "cover": """本页是**封面**：
- 一个大标题（font-size 72-90），一个副标题（24-28）
- 可以有一个英文小标题（letter-spacing 4-6）
- 底部一行细线 + 页脚小字
- 版面要有大片留白，标题偏左上或居中""",

    "statement": """本页是**观点页**：
- 标题 + 3-5 行展开文字（每行 font-size 26-30）
- 可以有一句总结放在底部（斜体，点缀色）""",

    "list": """本页是**要点列表页**：
- 标题 + N 条要点（N 见内容）
- 每条要点：编号（Georgia，font-size 36，点缀色）+ 小标题（28，粗体）+ 一行说明（19，正文色）
- 每条要点占一个独立的 <g>，bounds 互不重叠""",

    "compare": """本页是**对照页**：
- 标题 + 左右两栏
- 左栏白底带细边框，右栏深色底（主色）
- 两栏各 2-4 条要点
- 左右栏的 bounds 不能重叠（横向分开）""",

    "quote": """本页是**引语页**：
- 整页主色深底（不要浅底）
- 2-3 行大字引语（font-size 40-48，白色）
- 可有一行英文或补充说明（点缀色，斜体）
- 底部一条细线 + 出处
- 不要标题栏，只要引语本身""",

    "ending": """本页是**收尾页**：
- 2-3 行大字结论（font-size 40-56）
- 一段补充正文（19-22）
- 可以有一句金句（斜体，点缀色）
- 底部页脚""",
}


# ── 修复提示词：把校验错误喂回去 ──────────────────────────────
REPAIR_TASK = """你上一次输出的 SVG 没有通过校验器，错误如下：

{errors}

请修正这些问题，重新输出完整的这一页 SVG。

注意：
- 如果你的 bounds 互相重叠，把页面纵向切成互不相交的横条，每组严格待在自己那条里
- bounds 要完整包住组内元素，四边多留 12px
- 仍然只输出 <svg> 代码，不要任何解释"""


def build_page_prompt(spec, palette, index, total):
    """拼出单页生成提示词。"""
    role = spec.get("layout") or ("cover" if index == 1 else ("ending" if index == total else "content"))

    # 内容部分
    parts = []
    if spec.get("title"):
        parts.append("- 标题：" + str(spec["title"]))
    if spec.get("subtitle"):
        parts.append("- 副标题：" + str(spec["subtitle"]))
    if spec.get("lines"):
        parts.append("- 文字内容：")
        for line in spec["lines"]:
            parts.append("    " + str(line))
    if spec.get("items"):
        parts.append("- 要点清单：")
        for i, it in enumerate(spec["items"], 1):
            h = it.get("h") or it.get("title") or ""
            d = it.get("d") or it.get("desc") or it.get("text") or ""
            parts.append("    " + str(i) + ". " + str(h) + ("　—　" + str(d) if d else ""))
    if spec.get("left") or spec.get("right"):
        left = spec.get("left") or {}
        right = spec.get("right") or {}
        parts.append("- 左栏：" + str(left.get("h") or ""))
        for line in (left.get("lines") or []):
            parts.append("    " + str(line))
        parts.append("- 右栏：" + str(right.get("h") or ""))
        for line in (right.get("lines") or []):
            parts.append("    " + str(line))
    if spec.get("pull"):
        parts.append("- 底部总结句：" + str(spec["pull"]))
    if spec.get("body"):
        parts.append("- 补充正文：")
        for line in str(spec["body"]).split("\n"):
            parts.append("    " + line)

    contract_role = {
        "cover": "cover", "statement": "content", "list": "content",
        "compare": "content", "quote": "section", "ending": "ending",
    }.get(role, "content")

    contract = SVG_CONTRACT.replace("{ROLE}", contract_role)
    task = PAGE_TASK.format(
        index=index,
        total=total,
        role=role,
        title=spec.get("title") or "",
        content="\n".join(parts) if parts else "- （无额外内容，按标题展开）",
        layout_hint=LAYOUT_HINTS.get(role, LAYOUT_HINTS["statement"]),
        bg=palette["bg"], main=palette["main"], accent=palette["accent"],
        ink=palette["ink"], muted=palette["muted"],
    )
    return contract + "\n\n" + task


def build_outline_prompt(topic, audience, pages, style, extra=""):
    """让模型先出一份分页大纲（JSON）。"""
    return f"""把下面这个需求拆成 {pages} 页的演示大纲，只输出 JSON，不要任何解释。

主题：{topic}
受众：{audience}
风格倾向：{style}
{extra}

JSON 结构：
{{
  "title": "整体标题",
  "slides": [
    {{"layout": "cover", "title": "主标题", "subtitle": "副标题"}},
    {{"layout": "list", "title": "页面标题", "items": [{{"h": "小标题", "d": "一句说明"}}]}},
    {{"layout": "statement", "title": "页面标题", "lines": ["一行", "一行"]}},
    {{"layout": "compare", "title": "页面标题", "left": {{"h": "左", "lines": ["a"]}}, "right": {{"h": "右", "lines": ["b"]}}}},
    {{"layout": "quote", "lines": ["引语一行", "引语二行"]}},
    {{"layout": "ending", "lines": ["结论一行"], "body": "补充一句"}}
  ]
}}

硬性要求：
- 第一页必须 cover，最后一页必须 ending
- 中间页在 statement / list / compare / quote 里选，不要连续三页同一种
- 每页字数要少（这是演示稿）：每条说明不超过 24 字，每页要点不超过 5 条
- 一共 {pages} 页，不多不少"""
