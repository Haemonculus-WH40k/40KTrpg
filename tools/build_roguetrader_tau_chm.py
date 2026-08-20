from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image
from PyPDF2 import PdfReader

import rebuild_text_chm as common


ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "roguetrader_resources"
SOURCE_NAME = "《行商浪人》钛帝国角色手册.pdf"
BUILD = ROOT / "build" / "roguetrader-tau-chm"
CACHE = ROOT / "build" / "roguetrader-tau-cache"
FINAL = ROOT / "行商浪人_钛帝国角色手册.chm"
TITLE = "《行商浪人》钛帝国角色手册"
PAGES_PER_TOPIC = 5


def run(args: list[str], cwd: Path | None = None, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=capture,
    )


def baidu_token(api_key: str, secret_key: str) -> str:
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": api_key,
        "client_secret": secret_key,
    }).encode("ascii")
    request = urllib.request.Request("https://aip.baidubce.com/oauth/2.0/token", data=data, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
    if "access_token" not in result:
        raise RuntimeError(f"Baidu token request failed: {result.get('error_description') or result}")
    return result["access_token"]


def baidu_ocr(data: bytes, token: str) -> str:
    form = urllib.parse.urlencode({
        "image": base64.b64encode(data).decode("ascii"),
        "detect_direction": "true",
        "paragraph": "true",
    }).encode("ascii")
    url = "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic?access_token=" + urllib.parse.quote(token)
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            request = urllib.request.Request(
                url,
                data=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=90) as response:
                result = json.loads(response.read().decode("utf-8"))
            if result.get("error_code"):
                raise RuntimeError(f"Baidu OCR {result.get('error_code')}: {result.get('error_msg')}")
            return "\n".join(item.get("words", "") for item in result.get("words_result", [])).strip()
        except Exception as exc:
            last_error = exc
            time.sleep(min(8, 2 ** attempt))
    raise RuntimeError(f"Baidu OCR failed after retries: {last_error}")


def image_bytes(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.convert("RGB").save(stream, format="JPEG", quality=93, optimize=True)
    return stream.getvalue()


def ocr_page(page_no: int, image_path: Path, token: str, cache_key: str) -> tuple[int, str, str]:
    cached = CACHE / "ocr" / cache_key / f"page-{page_no:03d}.txt"
    if cached.exists():
        return page_no, cached.read_text(encoding="utf-8"), "cache"
    with Image.open(image_path) as page:
        width, height = page.size
        overlap = max(24, width // 40)
        left = page.crop((0, 0, width // 2 + overlap, height))
        right = page.crop((width // 2 - overlap, 0, width, height))
        left_text = baidu_ocr(image_bytes(left), token)
        right_text = baidu_ocr(image_bytes(right), token)
    text = f"【左栏】\n{left_text}\n\n【右栏】\n{right_text}"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(text, encoding="utf-8")
    return page_no, text, "baidu-general-basic-columns"


def render_pages(gs: Path, source: Path, page_count: int) -> list[Path]:
    render_dir = BUILD / "rendered"
    render_dir.mkdir(parents=True, exist_ok=True)
    output = render_dir / "page-%03d.jpg"
    env = os.environ.copy()
    tlgs = gs.parent.parent
    env["GS_LIB"] = ";".join([
        str(tlgs / "Resource" / "Init"),
        str(tlgs / "lib"),
        str(tlgs / "Resource"),
        str(tlgs / "kanji"),
    ])
    proc = subprocess.run([
        str(gs), "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=jpeg", "-dJPEGQ=92", "-r144",
        f"-sOutputFile={output}", str(source),
    ], env=env, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if proc.returncode:
        raise RuntimeError(f"Ghostscript failed: {proc.stderr[-2000:]}")
    pages = sorted(render_dir.glob("page-*.jpg"))
    if len(pages) != page_count:
        raise RuntimeError(f"Rendered {len(pages)} pages, expected {page_count}")
    return pages


def extract_art(pdfimages: Path, source: Path, cache_key: str) -> dict[int, list[str]]:
    media = BUILD / "topic001.media"
    media.mkdir(parents=True, exist_ok=True)
    art_cache = CACHE / "art" / cache_key
    manifest_path = art_cache / "manifest.json"
    if manifest_path.exists():
        cached = json.loads(manifest_path.read_text(encoding="utf-8"))
        for image_path in art_cache.glob("art-*.jpg"):
            shutil.copy2(image_path, media / image_path.name)
        return {int(page): [f"topic001.media/{name}" for name in names] for page, names in cached.items()}
    listing = run([str(pdfimages), "-list", str(source)], capture=True).stdout
    chosen: list[tuple[int, int]] = []
    for line in listing.splitlines():
        match = re.match(r"^\s*(\d+)\s+(\d+)\s+(\S+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\d+)", line)
        if not match:
            continue
        page, number = int(match.group(1)), int(match.group(2))
        kind, width, height, colour, components = match.group(3), int(match.group(4)), int(match.group(5)), match.group(6), int(match.group(7))
        if kind != "image" or components < 3:
            continue
        is_cover = page == 1 and width >= 700 and height >= 900
        is_art = width >= 250 and height >= 180 and not (width >= 1400 and height >= 900)
        if is_cover or is_art:
            chosen.append((page, number))
    if not chosen:
        return {}
    run([str(pdfimages), "-all", str(source), str(media / "raw")], capture=True)
    result: dict[int, list[str]] = {}
    seen: set[str] = set()
    counter = 0
    for page, number in chosen:
        candidates = sorted(media.glob(f"raw-{number:03d}.*"))
        if not candidates:
            continue
        raw = candidates[0]
        digest = hashlib.sha256(raw.read_bytes()).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        counter += 1
        target = media / f"art-{counter:03d}.jpg"
        with Image.open(raw) as image:
            image.convert("RGB").save(target, format="JPEG", quality=92, optimize=True)
        result.setdefault(page, []).append(target.relative_to(BUILD).as_posix())
    for raw in media.glob("raw-*.*"):
        raw.unlink()
    art_cache.mkdir(parents=True, exist_ok=True)
    for image_path in media.glob("art-*.jpg"):
        shutil.copy2(image_path, art_cache / image_path.name)
    manifest_path.write_text(
        json.dumps({str(page): [Path(path).name for path in paths] for page, paths in result.items()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def inject_art(fragment: str, art: dict[int, list[str]]) -> str:
    for page_no, paths in art.items():
        figures = ['<div class="pdf-figures">']
        for number, path in enumerate(paths, 1):
            figures.append(
                f'<figure class="pdf-figure"><img src="{html.escape(path)}" alt="原 PDF 第 {page_no} 页插图 {number}">'
                f'<figcaption>原 PDF 第 {page_no} 页插图</figcaption></figure>'
            )
        figures.append("</div>")
        section = re.compile(rf'(<section\b[^>]*\bid=["\']page-{page_no:03d}["\'][^>]*>.*?)(</section>)', re.I | re.S)
        fragment, count = section.subn(r"\1" + "".join(figures) + r"\2", fragment, count=1)
        if not count:
            raise RuntimeError(f"Page section missing for artwork: {page_no}")
    return fragment


def bookmarks(reader: PdfReader) -> list[tuple[int, str, int]]:
    outline = reader.outline
    result: list[tuple[int, str, int]] = []

    def walk(items: list, level: int = 1) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item, level + 1)
                continue
            try:
                page = reader.get_destination_page_number(item) + 1
            except Exception:
                continue
            label = re.sub(r"\s+", " ", getattr(item, "title", str(item))).strip()
            if label:
                result.append((min(level, 3), label, page))
    walk(outline)
    return result


def deepseek_reflow_intro(api_key: str, model: str, pages: list[tuple[int, str]]) -> tuple[str, str]:
    """Reflow the prose immediately before the Tau Explorer chapter."""
    page_blob = "\n\n".join(f"<<<PAGE {number:03d}>>>\n{text}" for number, text in pages)
    prompt = """你是中文桌面角色扮演资料的校订排版员。请把下面三页 OCR 结果重新排成语义化 HTML。
硬性要求：
1. 保留全部原文，不总结、不翻译、不润色、不补充；页码、数值、专名不得遗漏。
2. OCR 按【左栏】、【右栏】提供。阅读顺序必须是先左栏后右栏，并删除这两个栏目标记。
3. OCR 的每一行不是一个自然段。必须合并同一段内的错误换行、断开的中文短语和跨行句子；绝对不要一行一个 p。仅在语义上确实换段时才新建 p。
4. 去除因两栏裁切重叠造成的重复文字，但不要删除真实内容。
5. 标题使用 h2/h3/h4，正文使用 p，确实属于表格的内容使用 table/tr/th/td。
6. 每页恰好输出一个 <section class="page" id="page-NNN"><div class="page-label">第 N 页</div>...</section>。
7. 只输出 HTML 片段，不要 Markdown 围栏、解释、html/body/style/script 标签。

OCR 结果：
""" + page_blob
    digest = hashlib.sha256(("tau-intro-reflow-v2\0" + model + "\0" + prompt).encode("utf-8")).hexdigest()
    cache_dir = CACHE / "deepseek-reflow"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{digest}.html"
    if cached.exists():
        return cached.read_text(encoding="utf-8"), "cache"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "thinking": {"type": "disabled"},
        "max_tokens": 18000,
    }
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(
                "https://api.deepseek.com/chat/completions",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=900) as response:
                data = json.loads(response.read().decode("utf-8"))
            fragment = common.clean_fragment(data["choices"][0]["message"]["content"])
            expected = [f'id="page-{number:03d}"' for number, _ in pages]
            if all(marker in fragment for marker in expected) and "【左栏】" not in fragment and "【右栏】" not in fragment:
                cached.write_text(fragment, encoding="utf-8")
                return fragment, "deepseek-reflow"
            last_error = RuntimeError("reflow output validation failed")
        except Exception as exc:
            last_error = exc
        time.sleep(2 ** attempt)
    raise RuntimeError(f"DeepSeek intro reflow failed: {last_error}")


def replace_page_section(fragment: str, page_no: int, replacement: str) -> str:
    section = re.search(
        rf'<section\b[^>]*\bid=["\']page-{page_no:03d}["\'][^>]*>.*?</section>',
        replacement,
        flags=re.I | re.S,
    )
    if not section:
        raise RuntimeError(f"Reflow result is missing page {page_no}")
    pattern = re.compile(
        rf'<section\b[^>]*\bid=["\']page-{page_no:03d}["\'][^>]*>.*?</section>',
        flags=re.I | re.S,
    )
    updated, count = pattern.subn(section.group(0), fragment, count=1)
    if count != 1:
        raise RuntimeError(f"Original formatted result is missing page {page_no}")
    return updated


def part_url(chunk_no: int) -> str:
    return "topic001.html" if chunk_no == 0 else f"topic001-part{chunk_no + 1:03d}.html"


def wrap(title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="gb2312">'
        f'<meta http-equiv="X-UA-Compatible" content="IE=9"><title>{html.escape(title)}</title>'
        '<link rel="stylesheet" href="style.css"></head><body>'
        f'<h1>{html.escape(title)}</h1><div class="source-note">来源：{html.escape(SOURCE_NAME)}</div>{body}</body></html>'
    )


def write_search(documents: list[dict[str, str]]) -> None:
    search_js = "var SEARCH_DOCS=" + json.dumps(documents, ensure_ascii=False, separators=(",", ":")) + ";"
    (BUILD / "search-data.js").write_text(search_js, encoding="gbk", errors="replace")
    search_html = '''<!doctype html><html lang="zh-CN"><head><meta charset="gb2312"><meta http-equiv="X-UA-Compatible" content="IE=9"><title>全文搜索</title><link rel="stylesheet" href="style.css"></head><body><h1>全文搜索</h1><p>搜索全部正文、表格与标题。多个关键词需同时出现。</p><input id="q" class="searchbox" autofocus><button class="searchbtn" onclick="go()">搜索</button><div id="status"></div><div id="results"></div><script src="search-data.js"></script><script>
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function go(){var q=document.getElementById('q').value.replace(/^\s+|\s+$/g,'');var box=document.getElementById('results');if(!q){box.innerHTML='';return;}var terms=q.toLowerCase().split(/\s+/),hits=[];for(var i=0;i<SEARCH_DOCS.length;i++){var d=SEARCH_DOCS[i],hay=(d.title+' '+d.text).toLowerCase(),ok=true,pos=hay.length;for(var j=0;j<terms.length;j++){var p=hay.indexOf(terms[j]);if(p<0){ok=false;break;}if(p<pos)pos=p;}if(ok)hits.push({d:d,p:pos,score:(d.title.toLowerCase().indexOf(terms[0])>=0?100000:0)-pos});}hits.sort(function(a,b){return b.score-a.score;});document.getElementById('status').innerHTML='<p>找到 '+hits.length+' 个主题</p>';var out='';for(var k=0;k<hits.length;k++){var h=hits[k],start=Math.max(0,h.p-70),sn=h.d.text.substring(start,start+220);out+='<div class="result"><a href="'+h.d.url+'">'+esc(h.d.title)+'</a><div class="snippet">…'+esc(sn)+'…</div></div>';}box.innerHTML=out;}
document.getElementById('q').onkeydown=function(e){e=e||window.event;if(e.keyCode==13)go();};
</script></body></html>'''
    (BUILD / "search.html").write_text(search_html, encoding="gbk", errors="xmlcharrefreplace")


def main() -> None:
    source = SOURCE_DIR / SOURCE_NAME
    if not source.is_file():
        raise FileNotFoundError(source)
    env = common.load_env(ROOT / ".env")
    for key in ("OCR_API_KEY", "OCR_SECRET_KEY", "DEEPSEEK_API_KEY"):
        if not env.get(key):
            raise RuntimeError(f"{key} is missing")
    build_resolved = BUILD.resolve()
    if ROOT.resolve() not in build_resolved.parents:
        raise RuntimeError(f"Unsafe build path: {BUILD}")
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    (BUILD / "style.css").write_text(common.CSS, encoding="gbk", errors="xmlcharrefreplace")
    ascii_pdf = BUILD / "source.pdf"
    shutil.copy2(source, ascii_pdf)

    reader = PdfReader(str(ascii_pdf))
    page_count = len(reader.pages)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    gs = Path(r"D:\texlive\2024\tlpkg\tlgs\bin\gswin64c.exe")
    if not gs.is_file():
        raise FileNotFoundError(gs)
    pages = render_pages(gs, ascii_pdf, page_count)
    print(f"Rendered {len(pages)} pages with Ghostscript", flush=True)

    token = baidu_token(env["OCR_API_KEY"], env["OCR_SECRET_KEY"])
    ocr: dict[int, str] = {}
    methods: dict[int, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(ocr_page, n, path, token, source_hash): n for n, path in enumerate(pages, 1)}
        done = 0
        for future in concurrent.futures.as_completed(futures):
            number, text, method = future.result()
            ocr[number] = text
            methods[number] = method
            done += 1
            print(f"[Baidu OCR {done}/{page_count}] page {number}: {method}", flush=True)

    pdfimages_name = shutil.which("pdfimages.exe")
    if not pdfimages_name:
        raise FileNotFoundError("pdfimages.exe")
    art = extract_art(Path(pdfimages_name), ascii_pdf, source_hash)
    print(f"Selected {sum(map(len, art.values()))} unique artwork images", flush=True)

    chunks = [list(range(start, min(start + PAGES_PER_TOPIC, page_count + 1))) for start in range(1, page_count + 1, PAGES_PER_TOPIC)]
    deepseek_cache = CACHE / "deepseek"
    deepseek_cache.mkdir(parents=True, exist_ok=True)
    formatted: dict[int, tuple[str, str]] = {}
    model = env.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(common.deepseek_request, env["DEEPSEEK_API_KEY"], model, [(n, ocr[n]) for n in chunk], deepseek_cache): index
            for index, chunk in enumerate(chunks)
        }
        done = 0
        for future in concurrent.futures.as_completed(futures):
            index = futures[future]
            formatted[index] = future.result()
            done += 1
            print(f"[DeepSeek {done}/{len(chunks)}] part {index + 1}: {formatted[index][1]}", flush=True)

    intro, intro_method = deepseek_reflow_intro(
        env["DEEPSEEK_API_KEY"], model, [(number, ocr[number]) for number in range(4, 7)]
    )
    for number in range(4, 7):
        chunk_index = (number - 1) // PAGES_PER_TOPIC
        fragment, method = formatted[chunk_index]
        formatted[chunk_index] = (replace_page_section(fragment, number, intro), f"{method}+{intro_method}")
    print(f"[DeepSeek reflow] pages 4-6: {intro_method}", flush=True)

    documents: list[dict[str, str]] = []
    for index, chunk in enumerate(chunks):
        fragment = inject_art(formatted[index][0], {n: art[n] for n in chunk if n in art})
        url = part_url(index)
        start, end = chunk[0], chunk[-1]
        part_title = TITLE if index == 0 else f"{TITLE}（第 {start}–{end} 页）"
        content = wrap(part_title, fragment)
        (BUILD / url).write_text(content, encoding="gbk", errors="xmlcharrefreplace")
        visible = html.unescape(re.sub(r"<[^>]+>", " ", content))
        visible = re.sub(r"\s+", " ", visible).strip()
        documents.append({"title": part_title, "url": url, "text": visible})

    write_search(documents)
    home = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="gb2312"><title>行商浪人资料集</title>'
        '<link rel="stylesheet" href="style.css"></head><body><h1>行商浪人资料集（钛帝国角色手册测试版）</h1>'
        '<p><a href="search.html">进入全文搜索</a></p><p>当前仅收录《行商浪人》钛帝国角色手册。</p>'
        '<p class="source-note">译者有没有标取决于文件内有没有译者说明；chm制作人耶利米，有问题请抓住耶利米拷打</p>'
        '<ol><li><a href="topic001.html">《行商浪人》钛帝国角色手册</a> <small>(PDF OCR)</small></li></ol></body></html>'
    )
    (BUILD / "index.html").write_text(home, encoding="gbk", errors="xmlcharrefreplace")

    toc = ['<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML//EN"><HTML><BODY><UL>', common.toc_item(TITLE, "topic001.html"), "<UL>"]
    keyword = ['<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML//EN"><HTML><BODY><UL>', common.toc_item("全文搜索", "search.html"), common.toc_item(TITLE, "topic001.html")]
    outline = bookmarks(reader)
    for _, label, page in outline:
        url = part_url((page - 1) // PAGES_PER_TOPIC)
        local = f"{url}#page-{page:03d}"
        toc.append(common.toc_item(label, local))
        keyword.append(common.toc_item(label, local))
    toc.extend(["</UL>", common.toc_item("全文搜索（备用）", "search.html"), common.toc_item("首页", "index.html"), "</UL></BODY></HTML>"])
    keyword.append("</UL></BODY></HTML>")
    (BUILD / "roguetrader.hhc").write_text("\r\n".join(toc), encoding="gbk", errors="xmlcharrefreplace")
    (BUILD / "roguetrader.hhk").write_text("\r\n".join(keyword), encoding="gbk", errors="xmlcharrefreplace")

    included = [str(p.relative_to(BUILD)).replace("/", "\\") for p in BUILD.rglob("*") if p.is_file() and p.suffix.lower() not in {".txt", ".json", ".hhp", ".chm", ".pdf", ".jpg"}]
    included.extend(str(p.relative_to(BUILD)).replace("/", "\\") for p in (BUILD / "topic001.media").rglob("*") if p.is_file())
    project = f'''[OPTIONS]\nCompatibility=1.1 or later\nCompiled file=roguetrader_tau.chm\nContents file=roguetrader.hhc\nIndex file=roguetrader.hhk\nDefault Window=roguetrader_tau_v1\nDefault topic=index.html\nDisplay compile progress=No\nFull-text search=Yes\nLanguage=0x804 Chinese (Simplified)\nTitle=行商浪人资料集（钛帝国角色手册）\n\n[WINDOWS]\nroguetrader_tau_v1="行商浪人资料集","roguetrader.hhc","roguetrader.hhk","index.html","index.html",,,,,0x63520,,0x304e,[90,70,1280,850],0x0,,,,,,0\n\n[FILES]\n''' + "\n".join(included)
    project_path = BUILD / "roguetrader.hhp"
    project_path.write_text(project, encoding="gbk", errors="xmlcharrefreplace")
    compiler = Path(os.environ.get("TEMP", str(ROOT / "build"))) / "deathwatch-chm-tools" / "hhc" / "hhc.exe"
    if not compiler.is_file():
        raise FileNotFoundError(compiler)
    proc = subprocess.run([str(compiler), str(project_path)], cwd=str(BUILD), text=True, encoding="gbk", errors="replace", capture_output=True)
    built = BUILD / "roguetrader_tau.chm"
    if not built.is_file() or built.stat().st_size < 10000:
        raise RuntimeError(f"CHM compile failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    shutil.copy2(built, FINAL)
    chmls = Path(os.environ.get("TEMP", str(ROOT / "build"))) / "deathwatch-fpc" / "install" / "bin" / "i386-win32" / "chmls.exe"
    if chmls.is_file():
        listing = subprocess.run([str(chmls), "-n", "list", str(FINAL)], text=True, encoding="utf-8", errors="replace", capture_output=True).stdout
        if "/$FIftiMain" not in listing:
            raise RuntimeError("Native CHM full-text search index is missing")
    manifest = {
        "source": SOURCE_NAME,
        "pages": page_count,
        "bookmarks": len(outline),
        "artwork_images": sum(map(len, art.values())),
        "ocr_methods": {method: list(methods.values()).count(method) for method in set(methods.values())},
        "search_documents": len(documents),
        "output": str(FINAL),
        "bytes": FINAL.stat().st_size,
        "sha256": hashlib.sha256(FINAL.read_bytes()).hexdigest().upper(),
    }
    (BUILD / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
