from __future__ import annotations

import concurrent.futures
import glob
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "resources"
BUILD = ROOT / "build" / "chm-text"
CACHE = ROOT / "build" / "chm-text-cache"
FINAL = ROOT / "死亡守望整合.chm"
CSS = """
body{font-family:'Microsoft YaHei','Segoe UI',sans-serif;color:#20242a;background:#fff;margin:0;padding:24px 34px;line-height:1.7;font-size:15px}
h1{font-size:26px;border-bottom:3px solid #8c2433;padding-bottom:10px;margin:0 0 22px}h2{font-size:21px;color:#76202e;border-left:4px solid #9f2636;padding-left:10px;margin-top:30px}h3{font-size:18px;color:#3f4852;margin-top:24px}h4{font-size:16px}
p{margin:8px 0}.page{border-top:1px solid #d9dde2;margin-top:26px;padding-top:18px}.page-label{color:#7a828c;font-size:12px;text-align:right}
table{border-collapse:collapse;margin:14px 0;width:100%;font-size:14px}th,td{border:1px solid #9da5ae;padding:6px 8px;vertical-align:top}th{background:#e9edf1;font-weight:700}tr:nth-child(even) td{background:#f8f9fa}
.source-note{color:#69727c;background:#f3f5f7;border:1px solid #d8dde2;padding:9px 12px;margin-bottom:20px}.image-alt{color:#69727c;font-style:italic}.empty{color:#8a929a}
pre{white-space:pre-wrap;font-family:'Microsoft YaHei',sans-serif;background:#f6f7f8;border:1px solid #d9dde2;padding:12px;line-height:1.55}
a{color:#7f1d2d}.searchbox{width:70%;font-size:16px;padding:9px;border:1px solid #929aa3}.searchbtn{font-size:16px;padding:9px 18px;margin-left:6px}.result{padding:13px 0;border-bottom:1px solid #ddd}.result a{font-size:17px;font-weight:700}.snippet{color:#4d555d;margin-top:5px}mark{background:#ffe58a}
img{max-width:100%;height:auto}.pdf-figures{margin:16px 0;padding:12px;background:#f5f6f7;border:1px solid #d9dde2}.pdf-figure{margin:10px auto;text-align:center}.pdf-figure img{display:block;margin:auto}.pdf-figure figcaption{font-size:12px;color:#68717b;margin-top:5px}
.preset-pages{text-align:center}.preset-page{display:block;max-width:100%;height:auto;margin:0 auto 24px;border:1px solid #9da5ae}.preset-note{color:#69727c;background:#f3f5f7;border:1px solid #d8dde2;padding:9px 12px;margin-bottom:20px}
.part-nav{display:flex;justify-content:space-between;align-items:center;margin:16px 0;padding:9px 12px;background:#f3f5f7;border:1px solid #d8dde2;color:#69727c}.part-nav a{font-weight:700}
"""


def load_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def find_tool(patterns: list[Path]) -> Path:
    for pattern in patterns:
        matches = sorted((Path(p) for p in glob.glob(str(pattern))), reverse=True)
        if matches:
            return matches[0].resolve()
    raise FileNotFoundError(f"Tool not found: {patterns}")


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, text=True, encoding="utf-8", errors="replace", **kwargs)


def clean_fragment(fragment: str) -> str:
    fragment = fragment.strip()
    fragment = re.sub(r"^```(?:html)?\s*", "", fragment, flags=re.I)
    fragment = re.sub(r"\s*```$", "", fragment)
    fragment = re.sub(r"</?(?:html|body)[^>]*>", "", fragment, flags=re.I)
    fragment = re.sub(r"<script\b.*?</script>", "", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<style\b.*?</style>", "", fragment, flags=re.I | re.S)
    return fragment.strip()


def visible_text(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return html.unescape(re.sub(r"\s+", "", text))


def local_page(page_no: int, text: str) -> str:
    if not text.strip():
        return f'<section class="page" id="page-{page_no:03d}"><div class="page-label">第 {page_no} 页</div><p class="empty">（原文件空白页）</p></section>'
    lines = [line.rstrip() for line in text.splitlines()]
    out = [f'<section class="page" id="page-{page_no:03d}"><div class="page-label">第 {page_no} 页</div>']
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        table_rows: list[list[str]] = []
        j = i
        while j < len(lines) and lines[j].strip():
            cols = [c.strip() for c in re.split(r"\s{2,}", lines[j].strip()) if c.strip()]
            if len(cols) < 2:
                break
            table_rows.append(cols)
            j += 1
        if len(table_rows) >= 2:
            width = max(len(row) for row in table_rows)
            out.append("<table>")
            for r, row in enumerate(table_rows):
                tag = "th" if r == 0 else "td"
                padded = row + [""] * (width - len(row))
                out.append("<tr>" + "".join(f"<{tag}>{html.escape(cell)}</{tag}>" for cell in padded) + "</tr>")
            out.append("</table>")
            i = j
            continue
        line = lines[i].strip()
        if len(line) <= 42 and not re.search(r"[。；，,、:]$", line):
            out.append(f"<h2>{html.escape(line)}</h2>")
        else:
            para = [line]
            i += 1
            while i < len(lines) and lines[i].strip() and len(lines[i].strip()) > 18:
                if len(re.split(r"\s{2,}", lines[i].strip())) >= 2:
                    break
                para.append(lines[i].strip())
                i += 1
            out.append(f"<p>{html.escape(''.join(para))}</p>")
            continue
        i += 1
    out.append("</section>")
    return "\n".join(out)


def deepseek_request(api_key: str, model: str, pages: list[tuple[int, str]], cache_dir: Path) -> tuple[str, str]:
    page_blob = "\n\n".join(f"<<<PAGE {n:03d}>>>\n{text}" for n, text in pages)
    prompt = """你是中文资料数字化排版器。把下面的 PDF 文字层转换为语义化 HTML 片段。
硬性要求：
1. 保留全部原文，不总结、不翻译、不改写、不补充；页码、数值、单位和专有名词不得遗漏。
2. 每页必须输出 <section class="page" id="page-NNN"><div class="page-label">第 N 页</div>...</section>。
3. 识别章节标题并使用 h2/h3/h4；普通正文使用 p。
4. 对齐形成的表格必须转换为 table/tr/th/td；跨页表格可按页拆分，但不能删行。
5. 不保留图片或页面画面。无内容页写“（原文件空白页）”。
6. 只输出 HTML 片段，不要 Markdown 围栏、解释、html/body/style/script 标签。

PDF 文字层：
""" + page_blob
    digest = hashlib.sha256((model + "\0" + prompt).encode("utf-8")).hexdigest()
    cached = cache_dir / f"{digest}.html"
    if cached.exists():
        method = "local-fallback-cache" if cached.with_suffix(".fallback").exists() else "cache"
        return cached.read_text(encoding="utf-8"), method
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "thinking": {"type": "disabled"},
        "max_tokens": 30000,
    }
    request = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                data = json.loads(response.read().decode("utf-8").strip())
            fragment = clean_fragment(data["choices"][0]["message"]["content"])
            expected = [f'id="page-{n:03d}"' for n, _ in pages]
            source_len = sum(len(re.sub(r"\s+", "", text)) for _, text in pages)
            ratio = len(visible_text(fragment)) / max(source_len, 1)
            if all(marker in fragment for marker in expected) and ratio >= 0.72:
                cached.write_text(fragment, encoding="utf-8")
                return fragment, "deepseek"
            last_error = RuntimeError(f"validation failed ratio={ratio:.2f}")
        except urllib.error.HTTPError as exc:
            if exc.code == 402:
                fragment = "\n".join(local_page(*page) for page in pages)
                cached.write_text(fragment, encoding="utf-8")
                cached.with_suffix(".fallback").write_text("api-402", encoding="ascii")
                print(
                    f"DeepSeek quota unavailable; local fallback pages {pages[0][0]}-{pages[-1][0]}",
                    file=sys.stderr,
                )
                return fragment, "local-fallback-api-402"
            last_error = exc
        except Exception as exc:
            last_error = exc
        time.sleep(2 ** attempt)
    if len(pages) > 1:
        middle = len(pages) // 2
        left, lm = deepseek_request(api_key, model, pages[:middle], cache_dir)
        right, rm = deepseek_request(api_key, model, pages[middle:], cache_dir)
        fragment = left + "\n" + right
        cached.write_text(fragment, encoding="utf-8")
        if "local-fallback" in lm or "local-fallback" in rm:
            cached.with_suffix(".fallback").write_text("split-fallback", encoding="ascii")
        return fragment, f"split({lm},{rm})"
    print(f"DeepSeek fallback page {pages[0][0]}: {last_error}", file=sys.stderr)
    fragment = local_page(*pages[0])
    cached.write_text(fragment, encoding="utf-8")
    cached.with_suffix(".fallback").write_text("validation-fallback", encoding="ascii")
    return fragment, "local-fallback"


def add_heading_ids(fragment: str, prefix: str) -> tuple[str, list[tuple[int, str, str]]]:
    headings: list[tuple[int, str, str]] = []
    counter = 0
    pattern = re.compile(r"<h([2-4])([^>]*)>(.*?)</h\1>", re.I | re.S)
    def repl(match: re.Match[str]) -> str:
        nonlocal counter
        counter += 1
        level = int(match.group(1))
        label = re.sub(r"<[^>]+>", "", match.group(3))
        label = html.unescape(re.sub(r"\s+", " ", label)).strip()
        anchor = f"{prefix}-h{counter:04d}"
        headings.append((level, label[:100], anchor))
        attrs = re.sub(r"\s+id=(?:\"[^\"]*\"|'[^']*')", "", match.group(2), flags=re.I)
        return f'<h{level}{attrs} id="{anchor}">{match.group(3)}</h{level}>'
    return pattern.sub(repl, fragment), headings


CORE_CHAPTERS = [
    ("I 创建角色", "Ⅰ 创建角色"),
    ("II 专长", "Ⅱ 专长"),
    ("III 技能", "Ⅲ 技能"),
    ("IV 天赋和特性", "Ⅳ 天赋和特性"),
    ("V 军械库", "Ⅴ 军械库"),
    ("VI 灵能", "Ⅵ 灵能"),
    ("VII 进行游戏", "Ⅶ 进行游戏"),
    ("VIII 战斗", "VIII 战斗"),
    ("IX 游戏主持人", "IX 游戏主持人"),
    # The converted DOCX labels these last three as XI-XIII.  The CHM uses
    # the requested continuous I-XII chapter numbering.
    ("X 死亡守望", "XI 死亡守望"),
    ("XI 耶利哥边缘", "XII 耶利哥边缘"),
    ("XII 敌手", "XIII 敌手"),
]


def add_core_chapter_ids(fragment: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Turn the core DOCX's plain chapter-title paragraphs into link targets."""
    wanted = {source_label: (display_label, f"core-chapter-{number:02d}")
              for number, (display_label, source_label) in enumerate(CORE_CHAPTERS, 1)}
    found: set[str] = set()
    pattern = re.compile(r"<p([^>]*)>(.*?)</p>", re.I | re.S)

    def repl(match: re.Match[str]) -> str:
        label = re.sub(r"<[^>]+>", "", match.group(2))
        label = html.unescape(re.sub(r"\s+", " ", label)).strip()
        target = wanted.get(label)
        if not target or label in found:
            return match.group(0)
        found.add(label)
        display_label, anchor = target
        return f'<h2 id="{anchor}"><a name="{anchor}"></a>{html.escape(display_label)}</h2>'

    fragment = pattern.sub(repl, fragment)
    missing = [source_label for _, source_label in CORE_CHAPTERS if source_label not in found]
    if missing:
        raise RuntimeError(f"Core chapter markers not found in DOCX: {missing}")
    headings = [(2, display_label, f"core-chapter-{number:02d}")
                for number, (display_label, _) in enumerate(CORE_CHAPTERS, 1)]
    return fragment, headings


def wrap_topic(title: str, source_name: str, body: str) -> str:
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="gb2312"><meta http-equiv="X-UA-Compatible" content="IE=9"><title>{html.escape(title)}</title><link rel="stylesheet" href="style.css"></head><body><h1>{html.escape(title)}</h1><div class="source-note">来源：{html.escape(source_name)}</div>{body}</body></html>'''


def pandoc_docx(pandoc: Path, source: Path, topic_id: str) -> str:
    media_name = f"{topic_id}.media"
    proc = run(
        [str(pandoc), "--from=docx", "--to=html5", "--wrap=none", "--strip-comments", f"--extract-media={media_name}", str(source.resolve())],
        cwd=BUILD,
        capture_output=True,
    )
    return proc.stdout.replace("\\", "/")


def extract_pdf_images(pdfimages: Path, source: Path, topic_id: str) -> dict[int, list[str]]:
    listing = run([str(pdfimages), "-list", str(source)], capture_output=True).stdout
    rows: list[tuple[int, int, str]] = []
    for line in listing.splitlines():
        match = re.match(r"^\s*(\d+)\s+(\d+)\s+(\S+)", line)
        if match and match.group(3).lower() not in {"smask", "mask"}:
            rows.append((int(match.group(1)), int(match.group(2)), match.group(3).lower()))
    if not rows:
        return {}
    media_dir = BUILD / f"{topic_id}.media"
    media_dir.mkdir(parents=True, exist_ok=True)
    prefix = media_dir / "pdf-image"
    run([str(pdfimages), "-all", str(source), str(prefix)])
    by_page: dict[int, list[str]] = {}
    for page_no, image_no, _ in rows:
        candidates = sorted(media_dir.glob(f"pdf-image-{image_no:03d}.*"))
        if not candidates:
            continue
        relative = candidates[0].relative_to(BUILD).as_posix()
        by_page.setdefault(page_no, []).append(relative)
    return by_page


def inject_pdf_images(fragment: str, images_by_page: dict[int, list[str]]) -> str:
    for page_no, paths in images_by_page.items():
        figures = ['<div class="pdf-figures">']
        for image_no, path in enumerate(paths, 1):
            figures.append(
                f'<figure class="pdf-figure"><img src="{html.escape(path)}" alt="原 PDF 第 {page_no} 页图片 {image_no}">'
                f'<figcaption>原 PDF 第 {page_no} 页图片 {image_no}</figcaption></figure>'
            )
        figures.append("</div>")
        marker = re.compile(rf'(<section\b[^>]*\bid=["\']page-{page_no:03d}["\'][^>]*>)', re.I)
        fragment, count = marker.subn(r"\1" + "".join(figures), fragment, count=1)
        if not count:
            print(f"Warning: image page marker not found: page {page_no}", file=sys.stderr)
    return fragment


def render_preset_pdf(pdftoppm: Path, source: Path, topic_id: str) -> tuple[str, int]:
    media_dir = BUILD / f"{topic_id}.media"
    media_dir.mkdir(parents=True, exist_ok=True)
    prefix = media_dir / "raw"
    run([str(pdftoppm), "-png", "-r", "170", str(source), str(prefix)])
    rendered = sorted(media_dir.glob("raw-*.png"))
    if not rendered:
        raise RuntimeError(f"Preset-card rendering produced no pages: {source}")
    parts = ['<div class="preset-note">此预设卡按原 PDF 页面图片收录。</div><div class="preset-pages">']
    for page_no, png_path in enumerate(rendered, 1):
        target = media_dir / f"page-{page_no:03d}.jpg"
        with Image.open(png_path) as image:
            image.convert("RGB").save(target, format="JPEG", quality=91, optimize=True)
        png_path.unlink()
        relative = target.relative_to(BUILD).as_posix()
        parts.append(f'<img class="preset-page" src="{html.escape(relative)}" alt="{html.escape(source.stem)} 第 {page_no} 页">')
    parts.append("</div>")
    return "".join(parts), len(rendered)


def xlsx_html(source: Path, json_path: Path) -> str:
    run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "tools" / "xlsx_to_json.ps1"), "-InputFile", str(source), "-OutputFile", str(json_path)])
    sheets = json.loads(json_path.read_text(encoding="utf-8-sig"))
    if isinstance(sheets, dict):
        sheets = [sheets]
    parts: list[str] = []
    for sheet in sheets:
        parts.append(f"<h2>{html.escape(sheet['name'])}</h2><table>")
        for r, row in enumerate(sheet["rows"]):
            tag = "th" if r == 0 else "td"
            parts.append("<tr>" + "".join(f"<{tag}>{html.escape(str(cell or ''))}</{tag}>" for cell in row) + "</tr>")
        parts.append("</table>")
    return "\n".join(parts)


def toc_item(name: str, local: str | None = None) -> str:
    local_param = f'<param name="Local" value="{html.escape(local)}">' if local else ""
    return f'<LI><OBJECT type="text/sitemap"><param name="Name" value="{html.escape(name)}">{local_param}</OBJECT>'


def main() -> None:
    env = load_env(ROOT / ".env")
    api_key = env.get("DEEPSEEK_API_KEY", "")
    model = env.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is missing")
    temp = Path(os.environ.get("TEMP", ROOT / "build")) / "deathwatch-chm-tools"
    pandoc = find_tool([temp / "pandoc-*" / "*" / "pandoc.exe"])
    chmcmd_candidates = [
        os.environ.get("CHMCMD", ""),
        shutil.which("chmcmd.exe") or "",
        str(Path(os.environ.get("TEMP", ROOT / "build")) / "deathwatch-fpc" / "install" / "bin" / "i386-win32" / "chmcmd.exe"),
    ]
    chmcmd = next((Path(p).resolve() for p in chmcmd_candidates if p and Path(p).is_file()), None)
    hhc_candidates = [os.environ.get("HHC", ""), str(temp / "hhc" / "hhc.exe")]
    hhc = next((Path(p).resolve() for p in hhc_candidates if p and Path(p).is_file()), None)
    compiler = hhc or chmcmd
    if compiler is None:
        raise FileNotFoundError("Neither hhc.exe nor chmcmd.exe was found")
    pdftotext_name = shutil.which("pdftotext.exe")
    if not pdftotext_name:
        raise FileNotFoundError("pdftotext.exe not found")
    pdftotext = Path(pdftotext_name)
    pdfimages_name = shutil.which("pdfimages.exe")
    pdftoppm_name = shutil.which("pdftoppm.exe")
    if not pdfimages_name or not pdftoppm_name:
        raise FileNotFoundError("pdfimages.exe or pdftoppm.exe not found")
    pdfimages = Path(pdfimages_name)
    pdftoppm = Path(pdftoppm_name)
    build_resolved = BUILD.resolve()
    if ROOT.resolve() not in build_resolved.parents:
        raise RuntimeError(f"Unsafe build path: {BUILD}")
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    (BUILD / "style.css").write_text(CSS, encoding="gbk", errors="xmlcharrefreplace")

    sources = [p for p in SOURCE.rglob("*") if p.is_file() and p.suffix.lower() in {".docx", ".pdf", ".xlsx"}]
    core_book_stem = "武器勘误版DeathWatch核心规则书1.31b"
    sources.sort(key=lambda p: (0 if p.stem == core_book_stem else 1, str(p.relative_to(SOURCE)).lower()))
    topics: list[dict] = []
    pdf_jobs: list[tuple[int, Path, list[tuple[int, str]], dict[int, list[str]]]] = []
    preset_count = 0
    preset_pages = 0

    for index, source in enumerate(sources, 1):
        topic = f"topic{index:03d}.html"
        relative = str(source.relative_to(SOURCE))
        topic_id = f"topic{index:03d}"
        is_preset = source.suffix.lower() == ".pdf" and "预设卡" in source.relative_to(SOURCE).parts
        print(f"[{index:02d}/{len(sources)}] {relative}", flush=True)
        if source.suffix.lower() == ".docx":
            fragment = pandoc_docx(pandoc, source, topic_id)
            if source.stem == core_book_stem:
                fragment, headings = add_core_chapter_ids(fragment)
            else:
                fragment, headings = add_heading_ids(fragment, f"t{index:03d}")
            content = wrap_topic(source.stem, relative, fragment)
            (BUILD / topic).write_text(content, encoding="gbk", errors="xmlcharrefreplace")
            topics.append({"title": source.stem, "relative": relative, "url": topic, "type": "DOCX", "headings": headings})
        elif source.suffix.lower() == ".xlsx":
            fragment = xlsx_html(source, BUILD / f"topic{index:03d}.json")
            fragment, headings = add_heading_ids(fragment, f"t{index:03d}")
            content = wrap_topic(source.stem, relative, fragment)
            (BUILD / topic).write_text(content, encoding="gbk", errors="xmlcharrefreplace")
            topics.append({"title": source.stem, "relative": relative, "url": topic, "type": "XLSX", "headings": headings})
        elif is_preset:
            fragment, page_count = render_preset_pdf(pdftoppm, source, topic_id)
            content = wrap_topic(source.stem, relative, fragment)
            (BUILD / topic).write_text(content, encoding="gbk", errors="xmlcharrefreplace")
            topics.append({"title": source.stem, "relative": relative, "url": topic, "type": "预设卡", "headings": [], "pages": page_count})
            preset_count += 1
            preset_pages += page_count
        else:
            text_path = BUILD / f"topic{index:03d}.txt"
            subprocess.run([str(pdftotext), "-q", "-layout", "-enc", "UTF-8", str(source), str(text_path)], check=True)
            raw = text_path.read_text(encoding="utf-8", errors="replace")
            pages_raw = raw.split("\f")
            if pages_raw and not pages_raw[-1].strip():
                pages_raw.pop()
            pages = [(n, text) for n, text in enumerate(pages_raw, 1)]
            images_by_page = extract_pdf_images(pdfimages, source, topic_id)
            pdf_jobs.append((index, source, pages, images_by_page))
            topics.append({"title": source.stem, "relative": relative, "url": topic, "type": "PDF", "headings": [], "pages": len(pages)})

    chunk_jobs: list[tuple[int, int, list[tuple[int, str]]]] = []
    for index, _, pages, _ in pdf_jobs:
        for chunk_no, start in enumerate(range(0, len(pages), 10)):
            chunk_jobs.append((index, chunk_no, pages[start:start + 10]))
    results: dict[tuple[int, int], tuple[str, str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(deepseek_request, api_key, model, pages, CACHE): (index, chunk_no) for index, chunk_no, pages in chunk_jobs}
        done = 0
        for future in concurrent.futures.as_completed(futures):
            key = futures[future]
            results[key] = future.result()
            done += 1
            print(f"[DeepSeek {done}/{len(chunk_jobs)}] topic{key[0]:03d} chunk {key[1] + 1}: {results[key][1]}", flush=True)

    pdf_metrics: list[dict] = []
    for index, source, pages, images_by_page in pdf_jobs:
        chunks = []
        for chunk_no in range((len(pages) + 9) // 10):
            page_start = chunk_no * 10 + 1
            page_end = min(page_start + 9, len(pages))
            chunk_images = {page: paths for page, paths in images_by_page.items() if page_start <= page <= page_end}
            chunks.append(inject_pdf_images(results[(index, chunk_no)][0], chunk_images))
        methods = [results[(index, n)][1] for n in range((len(pages) + 9) // 10)]
        relative = str(source.relative_to(SOURCE))
        topic = f"topic{index:03d}.html"
        target = next(t for t in topics if t["url"] == topic)
        if source.stem == core_book_stem:
            parts = []
            all_headings = []
            for chunk_no, chunk in enumerate(chunks):
                page_start = chunk_no * 10 + 1
                page_end = min(page_start + 9, len(pages))
                part_url = topic if chunk_no == 0 else f"topic{index:03d}-part{chunk_no + 1:03d}.html"
                part_fragment, part_headings = add_heading_ids(chunk, f"t{index:03d}p{chunk_no + 1:03d}")
                part_title = source.stem if chunk_no == 0 else f"{source.stem}（第 {page_start}–{page_end} 页）"
                content = wrap_topic(part_title, relative, part_fragment)
                (BUILD / part_url).write_text(content, encoding="gbk", errors="xmlcharrefreplace")
                parts.append({"title": part_title, "url": part_url, "headings": part_headings})
                all_headings.extend((level, label, anchor, part_url) for level, label, anchor in part_headings)
            target["parts"] = parts
            target["heading_links"] = all_headings
            target["headings"] = [(level, label, anchor) for level, label, anchor, _ in all_headings]
            fragment = "\n".join(chunks)
        else:
            fragment = "\n".join(chunks)
            fragment, headings = add_heading_ids(fragment, f"t{index:03d}")
            content = wrap_topic(source.stem, relative, fragment)
            (BUILD / topic).write_text(content, encoding="gbk", errors="xmlcharrefreplace")
            target["headings"] = headings
        input_chars = sum(len(re.sub(r"\s+", "", text)) for _, text in pages)
        output_chars = len(visible_text(fragment))
        pdf_metrics.append({"file": relative, "pages": len(pages), "input_chars": input_chars, "output_chars": output_chars, "ratio": round(output_chars / max(input_chars, 1), 3), "tables": len(re.findall(r"<table\b", fragment, re.I)), "embedded_images": sum(len(paths) for paths in images_by_page.values()), "headings": len(target["headings"]), "methods": methods})

    search_docs = []
    for topic in topics:
        documents = topic.get("parts") or [topic]
        for document in documents:
            content = (BUILD / document["url"]).read_text(encoding="gbk")
            text = re.sub(r"<style\b.*?</style>|<script\b.*?</script>", " ", content, flags=re.I | re.S)
            text = html.unescape(re.sub(r"<[^>]+>", " ", text))
            text = re.sub(r"\s+", " ", text).strip()
            search_docs.append({"title": document["title"], "url": document["url"], "text": text})
    search_js = "var SEARCH_DOCS=" + json.dumps(search_docs, ensure_ascii=False, separators=(",", ":")) + ";"
    (BUILD / "search-data.js").write_text(search_js, encoding="gbk", errors="replace")
    search_html = '''<!doctype html><html lang="zh-CN"><head><meta charset="gb2312"><meta http-equiv="X-UA-Compatible" content="IE=9"><title>全文搜索</title><link rel="stylesheet" href="style.css"></head><body><h1>全文搜索</h1><p>搜索全部文件正文、表格与标题。多个关键词需同时出现。</p><input id="q" class="searchbox" autofocus><button class="searchbtn" onclick="go()">搜索</button><div id="status"></div><div id="results"></div><script src="search-data.js"></script><script>
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function go(){var q=document.getElementById('q').value.replace(/^\s+|\s+$/g,'');var box=document.getElementById('results');if(!q){box.innerHTML='';return;}var terms=q.toLowerCase().split(/\s+/),hits=[];for(var i=0;i<SEARCH_DOCS.length;i++){var d=SEARCH_DOCS[i],hay=(d.title+' '+d.text).toLowerCase(),ok=true,pos=hay.length;for(var j=0;j<terms.length;j++){var p=hay.indexOf(terms[j]);if(p<0){ok=false;break;}if(p<pos)pos=p;}if(ok)hits.push({d:d,p:pos,score:(d.title.toLowerCase().indexOf(terms[0])>=0?100000:0)-pos});}hits.sort(function(a,b){return b.score-a.score;});document.getElementById('status').innerHTML='<p>找到 '+hits.length+' 个文件</p>';var out='';for(var k=0;k<hits.length;k++){var h=hits[k],start=Math.max(0,h.p-70),sn=h.d.text.substring(start,start+220);out+='<div class="result"><a href="'+h.d.url+'">'+esc(h.d.title)+'</a><div class="snippet">…'+esc(sn)+'…</div></div>';}box.innerHTML=out;}
document.getElementById('q').onkeydown=function(e){e=e||window.event;if(e.keyCode==13)go();};
</script></body></html>'''
    (BUILD / "search.html").write_text(search_html, encoding="gbk", errors="xmlcharrefreplace")

    home_links = "".join(f'<li><a href="{t["url"]}">{html.escape(t["title"])}</a> <small>({t["type"]})</small></li>' for t in topics)
    home = f'<!doctype html><html lang="zh-CN"><head><meta charset="gb2312"><title>Deathwatch 资料集</title><link rel="stylesheet" href="style.css"></head><body><h1>Deathwatch 资料集（图文版）</h1><p><a href="search.html">进入全文搜索</a></p><p>共 {len(topics)} 个文件。左侧为文件目录，展开文件可查看识别到的章节；预设卡按原 PDF 页面图片收录。</p><p class="source-note">译者有没有标取决于文件内有没有译者说明；chm制作人耶利米，有问题请抓住耶利米拷打</p><ol>{home_links}</ol></body></html>'
    (BUILD / "index.html").write_text(home, encoding="gbk", errors="xmlcharrefreplace")

    toc = ['<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML//EN"><HTML><BODY><UL>']
    keyword = ['<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML//EN"><HTML><BODY><UL>', toc_item("全文搜索", "search.html")]
    for topic in topics:
        toc.append(toc_item(topic["title"], topic["url"]))
        heading_links = topic.get("heading_links")
        headings = [h for h in topic["headings"] if h[1]][:180]
        if heading_links:
            toc.append("<UL>")
            for _, label, anchor, part_url in [h for h in heading_links if h[1]][:180]:
                toc.append(toc_item(label, f'{part_url}#{anchor}'))
            toc.append("</UL>")
        elif headings:
            toc.append("<UL>")
            for _, label, anchor in headings:
                toc.append(toc_item(label, f'{topic["url"]}#{anchor}'))
            toc.append("</UL>")
        keyword.append(toc_item(topic["title"], topic["url"]))
        if heading_links:
            for _, label, anchor, part_url in [h for h in heading_links if h[1]][:180]:
                keyword.append(toc_item(label, f'{part_url}#{anchor}'))
        else:
            for _, label, anchor in headings:
                keyword.append(toc_item(label, f'{topic["url"]}#{anchor}'))
    toc.append(toc_item("全文搜索（备用）", "search.html"))
    toc.append(toc_item("首页", "index.html"))
    toc.append("</UL></BODY></HTML>")
    keyword.append("</UL></BODY></HTML>")
    (BUILD / "deathwatch.hhc").write_text("\r\n".join(toc), encoding="gbk", errors="xmlcharrefreplace")
    (BUILD / "deathwatch.hhk").write_text("\r\n".join(keyword), encoding="gbk", errors="xmlcharrefreplace")

    included = [str(p.relative_to(BUILD)).replace("/", "\\") for p in BUILD.rglob("*") if p.is_file() and p.suffix.lower() not in {".txt", ".json", ".hhp", ".chm"}]
    project = f'''[OPTIONS]\nCompatibility=1.1 or later\nCompiled file=deathwatch_text.chm\nContents file=deathwatch.hhc\nIndex file=deathwatch.hhk\nDefault Window=deathwatch_text_v3\nDefault topic=index.html\nDisplay compile progress=No\nFull-text search=Yes\nLanguage=0x804 Chinese (Simplified)\nTitle=Deathwatch 资料集（图文版）\n\n[WINDOWS]\ndeathwatch_text_v3="Deathwatch 资料集","deathwatch.hhc","deathwatch.hhk","index.html","index.html",,,,,0x63520,,0x304e,[90,70,1280,850],0x0,,,,,,0\n\n[FILES]\n''' + "\n".join(included)
    project_path = BUILD / "deathwatch.hhp"
    project_path.write_text(project, encoding="gbk", errors="xmlcharrefreplace")
    built = BUILD / "deathwatch_text.chm"
    if built.exists():
        built.unlink()
    if compiler.name.lower() == "chmcmd.exe":
        subprocess.run([str(compiler), "--no-html-scan", project_path.name], cwd=BUILD, check=True)
    else:
        subprocess.run([str(compiler), str(project_path)], cwd=BUILD, check=False)
    if not built.exists() or built.stat().st_size < 10000:
        raise RuntimeError("CHM compilation failed")
    chmls = compiler.with_name("chmls.exe")
    if chmls.is_file():
        listing = subprocess.run(
            [str(chmls), "-n", "list", built.name], cwd=BUILD,
            check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
        ).stdout
        if "/$FIftiMain" not in listing:
            raise RuntimeError("CHM full-text search index was not generated")
    shutil.copy2(built, FINAL)
    manifest = {
        "source_count": len(sources), "preset_card_count": preset_count, "preset_card_pages": preset_pages,
        "docx_count": sum(p.suffix.lower() == ".docx" for p in sources),
        "pdf_count": sum(p.suffix.lower() == ".pdf" and "预设卡" not in p.relative_to(SOURCE).parts for p in sources),
        "xlsx_count": sum(p.suffix.lower() == ".xlsx" for p in sources),
        "topics": len(topics), "search_documents": len(search_docs),
        "images_in_topics": sum(len(re.findall(r"<img\b", p.read_text(encoding="gbk"), re.I)) for p in BUILD.glob("topic*.html")),
        "pdf_metrics": pdf_metrics,
        "output": str(FINAL), "bytes": FINAL.stat().st_size,
        "sha256": hashlib.sha256(FINAL.read_bytes()).hexdigest().upper(),
    }
    (BUILD / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
