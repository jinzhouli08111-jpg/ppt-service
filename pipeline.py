# -*- coding: utf-8 -*-
"""流水线：内容 → 逐页 SVG → 校验 → 修复 → 转 PPTX。

关键决策：
  · 逐页并发生成，把 8 页的串行等待压到 1 页左右
  · 每页最多修复 2 次，失败就用地板版本（保证整份能导出）
  · 校验器和转换器直接用 ppt-master 原脚本，不重写
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import concurrent.futures as futures
from pathlib import Path

import requests

import prompt as P

HERE = Path(__file__).resolve().parent
SKILL = HERE / "skill"
SCRIPTS = SKILL / "scripts"
SHIM = HERE / "_win_shim.py"

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
TIMEOUT_LLM = 120
TIMEOUT_TOOL = 180
MAX_REPAIR = 2

PALETTES = {
    "old-money": {"bg": "#FAF7F1", "main": "#1E3527", "accent": "#A98A57", "ink": "#23201C", "muted": "#7A7268"},
    "navy":      {"bg": "#F7F6F2", "main": "#152A4A", "accent": "#B08D57", "ink": "#1E2430", "muted": "#6E7480"},
    "wine":      {"bg": "#FAF6F3", "main": "#5C1F26", "accent": "#B08A3E", "ink": "#2A1C1E", "muted": "#7C6A6C"},
    "graphite":  {"bg": "#F8F8F6", "main": "#26262A", "accent": "#9A968C", "ink": "#1C1C1E", "muted": "#6E6E72"},
    "ochre":     {"bg": "#FBF7F0", "main": "#6E4526", "accent": "#B1905E", "ink": "#2E2118", "muted": "#7A6A5A"},
    "slate":     {"bg": "#F6F7F7", "main": "#3E4A52", "accent": "#8E9BA1", "ink": "#1F262A", "muted": "#696F73"},
}


class PipelineError(Exception):
    pass


# ────────────────────────── LLM ──────────────────────────

def _llm(prompt_text: str, api_key: str, max_tokens: int = 4000, temperature: float = 0.4) -> str:
    res = requests.post(
        DEEPSEEK_URL,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        json={
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt_text}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=TIMEOUT_LLM,
    )
    if res.status_code != 200:
        raise PipelineError(f"DeepSeek {res.status_code}: {res.text[:300]}")
    data = res.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""


def _extract_json(text: str):
    s = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", s, re.I)
    if fence:
        s = fence.group(1).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        body = s[i:j + 1]
        try:
            return json.loads(body)
        except Exception:
            try:
                return json.loads(re.sub(r",\s*([}\]])", r"\1", body))
            except Exception:
                return None
    return None


def _extract_svg(text: str) -> str:
    s = (text or "").strip()
    fence = re.search(r"```(?:svg|xml)?\s*([\s\S]*?)```", s, re.I)
    if fence:
        s = fence.group(1).strip()
    i = s.find("<svg")
    j = s.rfind("</svg>")
    if i >= 0 and j > i:
        return _sanitize_svg(s[i:j + 6])
    raise PipelineError("模型没有输出 <svg> 代码")


# SVG 里不该出现的控制字符（模型有时把换行写成了垂直制表符）
_CTRL = {
    "\u000b": " ",   # vertical tab
    "\u000c": " ",   # form feed
    "\u0000": "",
    "\u0001": "",
    "\u0008": "",
    "\u000e": "",
    "\u000f": "",
}


def _sanitize_svg(svg: str) -> str:
    """清掉会破坏渲染的控制字符，保留 \\n \\t。"""
    for bad, good in _CTRL.items():
        if bad in svg:
            svg = svg.replace(bad, good)
    return svg


# ────────────────────────── 工具调用 ──────────────────────────

def _env() -> dict:
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    e["TMPDIR"] = e.get("TMPDIR") or tempfile.gettempdir()
    return e


def run_checker(project: Path, quick: bool = True) -> tuple[bool, list[str]]:
    """跑 svg_quality_checker，返回 (是否通过, 错误列表)。"""
    cmd = [sys.executable, str(SCRIPTS / "svg_quality_checker.py"), str(project),
           "--canonical-authoring", "--stage", "final", "--json",
           "--primary-language", "zh-Hans-CN"]
    if quick:
        cmd.append("--quick-generate")

    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=_env(), timeout=TIMEOUT_TOOL)
    if proc.returncode == 0:
        return True, []

    errors: list[str] = []
    # 优先读 JSON 报告里的结构化错误
    report = project / "validation" / "svg_quality_report.json"
    if report.is_file():
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
            blocking = (data.get("categories") or {}).get("blocking") or {}
            for issue in (blocking.get("issues") or []):
                msg = issue.get("message") or issue.get("detail") or ""
                if msg:
                    errors.append(str(msg))
        except Exception:
            pass

    if not errors:
        for line in (proc.stdout or "").splitlines() + (proc.stderr or "").splitlines():
            if "[ERROR]" in line:
                errors.append(line.replace("[ERROR]", "").strip())

    if not errors:
        errors.append((proc.stderr or proc.stdout or "未知校验失败")[-600:])

    return False, errors[:10]


def run_export(project: Path) -> Path:
    """调 svg_to_pptx 生成 pptx，返回文件路径。"""
    cmd = [sys.executable, str(SCRIPTS / "svg_to_pptx.py"), str(project),
           "--quick-generate", "--no-notes", "--primary-language", "zh-Hans-CN"]
    if sys.platform == "win32":
        cmd = [sys.executable, str(SHIM), str(SCRIPTS / "svg_to_pptx.py"), str(project),
               "--quick-generate", "--no-notes", "--primary-language", "zh-Hans-CN"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=_env(), timeout=TIMEOUT_TOOL)
    if proc.returncode != 0:
        raise PipelineError("导出失败：" + ((proc.stderr or proc.stdout or "")[-800:]))

    exp = project / "exports"
    files = sorted(exp.glob("*.pptx"), key=lambda p: p.stat().st_mtime, reverse=True) if exp.is_dir() else []
    if not files:
        raise PipelineError("导出成功但找不到 pptx 文件")

    out = files[0]
    return out


# 注：python-pptx 读出的 \x0b 是 <a:br/> 换行元素的表示，PowerPoint 里显示正常，
# 不需要也不应该清洗。


# ────────────────────────── 主流程 ──────────────────────────

def build_outline(topic, audience, pages, style, api_key, extra="") -> dict:
    text = _llm(P.build_outline_prompt(topic, audience, pages, style, extra), api_key, max_tokens=3000)
    data = _extract_json(text)
    if not data or not isinstance(data.get("slides"), list) or not data["slides"]:
        raise PipelineError("大纲解析失败：" + text[:200])
    slides = data["slides"][:pages]
    # 补齐或裁剪到目标页数
    while len(slides) < pages:
        slides.append({"layout": "statement", "title": topic, "lines": ["补充内容"]})
    return {"title": data.get("title") or topic, "slides": slides}


def _one_page(args):
    """生成一页 SVG（含修复循环）。供线程池调用。"""
    index, total, spec, palette, api_key = args
    prompt_text = P.build_page_prompt(spec, palette, index, total)

    last_errors = []
    for attempt in range(MAX_REPAIR + 1):
        try:
            if attempt == 0:
                raw = _llm(prompt_text, api_key, max_tokens=4000, temperature=0.35)
            else:
                raw = _llm(prompt_text + "\n\n" + P.REPAIR_TASK.format(
                    errors="\n".join("- " + e[:300] for e in last_errors[:6])),
                    api_key, max_tokens=4000, temperature=0.2)
            svg = _extract_svg(raw)
        except Exception as e:  # noqa: BLE001
            last_errors = [str(e)]
            continue

        # 单页快检：写进临时目录跑检查器
        ok, errs = _quick_check_svg(svg)
        if ok:
            return index, svg, None
        last_errors = errs

    return index, None, last_errors


def _quick_check_svg(svg: str) -> tuple[bool, list[str]]:
    """把单页 SVG 放进临时工程跑一次检查器。"""
    tmp = Path(tempfile.mkdtemp(prefix="svgcheck-"))
    try:
        out = tmp / "svg_output"
        out.mkdir(parents=True, exist_ok=True)
        (out / "01_page.svg").write_text(svg, encoding="utf-8")
        return run_checker(tmp, quick=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


FALLBACK_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" width="1280" height="720" font-family="Microsoft YaHei" data-pptx-page-role="content">
  <rect id="bg" data-pptx-role="background" x="0" y="0" width="1280" height="720" fill="{bg}"/>
  <g id="header" data-pptx-bounds="80 56 1120 200">
    <text x="80" y="120" font-family="Georgia" font-size="20" font-weight="700" letter-spacing="4" fill="{accent}">SLIDE</text>
    <text x="80" y="200" font-family="Microsoft YaHei" font-size="46" font-weight="700" fill="{main}">{title}</text>
  </g>
  <g id="body" data-pptx-bounds="80 288 1120 300">
    <text x="80" y="360" font-family="Microsoft YaHei" font-size="22" fill="{ink}">{line1}</text>
    <text x="80" y="410" font-family="Microsoft YaHei" font-size="22" fill="{ink}">{line2}</text>
  </g>
</svg>"""


def build_pptx(topic: str, audience: str, pages: int, style: str, api_key: str,
               outline: dict | None = None, extra: str = "") -> tuple[bytes, dict]:
    """主入口：返回 (pptx 字节, 元信息)。"""
    t_start = time.time()
    palette = PALETTES.get(style, PALETTES["old-money"])

    deck = outline or build_outline(topic, audience, pages, style, api_key, extra)
    slides = deck["slides"]
    total = len(slides)

    # 并发生成所有页。
    # 并发度受内存约束：每页要起一个子进程跑质检，512 MB 的免费实例上
    # 超过 2 个并发就有 OOM 风险。可用 PPT_WORKERS 覆盖（本地可调高）。
    jobs = [(i + 1, total, s, palette, api_key) for i, s in enumerate(slides)]
    results: dict[int, str] = {}
    failed: dict[int, list[str]] = {}

    try:
        workers = int(os.environ.get("PPT_WORKERS", "2"))
    except ValueError:
        workers = 2
    workers = max(1, min(workers, 8, total))

    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for index, svg, errs in pool.map(_one_page, jobs):
            if svg:
                results[index] = svg
            else:
                failed[index] = errs or []

    # 生成失败的页用地板版本顶上，保证整份能导出
    for index in range(1, total + 1):
        if index not in results:
            spec = slides[index - 1]
            lines = spec.get("lines") or []
            title = spec.get("title") or topic
            results[index] = FALLBACK_SVG.format(
                bg=palette["bg"], main=palette["main"], accent=palette["accent"], ink=palette["ink"],
                title=_xml_escape(str(title)[:40]),
                line1=_xml_escape(str(lines[0])[:40] if lines else ""),
                line2=_xml_escape(str(lines[1])[:40] if len(lines) > 1 else ""),
            )

    # 写工程目录
    project = Path(tempfile.mkdtemp(prefix="pptbuild-"))
    try:
        out = project / "svg_output"
        out.mkdir(parents=True, exist_ok=True)
        for index in range(1, total + 1):
            (out / f"{index:02d}_page.svg").write_text(results[index], encoding="utf-8")

        # 整份校验：不过也不拦，最终以导出为准
        ok, errs = run_checker(project, quick=True)
        checker_ok = ok
        checker_errors = errs

        pptx_path = run_export(project)
        data = pptx_path.read_bytes()
    finally:
        shutil.rmtree(project, ignore_errors=True)

    meta = {
        "title": deck.get("title") or topic,
        "pages": total,
        "runs": round(time.time() - t_start, 1),
        "checker_ok": checker_ok,
        "checker_errors": checker_errors[:5] if not checker_ok else [],
        "fallback_pages": sorted(failed.keys()),
    }
    return data, meta


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))
