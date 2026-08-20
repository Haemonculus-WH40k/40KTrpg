from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / "build" / "40KTrpg-pages"
SITES = [
    ("deathwatch", "死亡守望资料集", ROOT / "build" / "chm-text", "deathwatch.hhc"),
    ("roguetrader", "行商浪人整合v2.0", ROOT / "build" / "roguetrader-chm", "roguetrader.hhc"),
    ("darkheresy", "黑暗异端资料集", ROOT / "build" / "darkheresy-chm", "darkheresy.hhc"),
]
COPY_SUFFIXES = {".html", ".css", ".js", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}


def parse_hhc(path: Path) -> list[dict]:
    root: list[dict] = []
    stack: list[list[dict]] = [root]
    pending: dict | None = None
    for raw in path.read_text(encoding="gbk", errors="replace").splitlines():
        line = raw.strip()
        if line == "<UL>":
            if pending is not None:
                stack.append(pending["children"])
            pending = None
            continue
        if line == "</UL>":
            if len(stack) > 1:
                stack.pop()
            pending = None
            continue
        if "text/sitemap" not in line:
            continue
        name_match = re.search(r'<param name="Name" value="([^"]*)">', line, re.I)
        local_match = re.search(r'<param name="Local" value="([^"]*)">', line, re.I)
        if not name_match:
            continue
        node = {
            "name": html.unescape(name_match.group(1)),
            "local": html.unescape(local_match.group(1)).replace("\\", "/") if local_match else "",
            "children": [],
        }
        stack[-1].append(node)
        pending = node
    return root


def copy_content(source: Path, target: Path) -> int:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    copied = 0
    for path in source.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in COPY_SUFFIXES:
            continue
        relative = path.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        if destination.suffix.lower() in {".html", ".css", ".js"}:
            text = destination.read_text(encoding="gbk", errors="replace")
            if destination.suffix.lower() == ".html":
                text = re.sub(r"charset\s*=\s*[\"']?gb2312[\"']?", 'charset="utf-8"', text, flags=re.I)
            destination.write_text(text, encoding="utf-8")
        copied += 1
    return copied


def normalized_label(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", "", value))
    value = re.sub(r"\s+", "", value).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)


def label_tail(value: str) -> str:
    return re.sub(r"^[ivxlcdm0-9一二三四五六七八九十百]+", "", normalized_label(value))


def repair_heading_links(content_dir: Path) -> tuple[int, int]:
    repaired = 0
    unresolved = 0
    for path in content_dir.rglob("*.html"):
        source = path.read_text(encoding="utf-8", errors="replace")
        ids = set(re.findall(r'\b(?:id|name)=["\']([^"\']+)', source, re.I))
        headings: dict[str, list[str]] = {}
        for match in re.finditer(r'<h[1-6]\b[^>]*\bid=["\']([^"\']+)["\'][^>]*>(.*?)</h[1-6]>', source, re.I | re.S):
            anchor, label = match.group(1), match.group(2)
            for key in {normalized_label(label), label_tail(label)}:
                if key:
                    headings.setdefault(key, []).append(anchor)

        def replace(match: re.Match[str]) -> str:
            nonlocal repaired, unresolved
            anchor, body = match.group(2), match.group(4)
            if anchor in ids:
                return match.group(0)
            candidates: list[str] = []
            anchor_base = re.sub(r"-\d+$", "", anchor)
            body_text = html.unescape(re.sub(r"<[^>]+>", "", body))
            body_base = re.sub(r"\s+\d+\s*$", "", body_text)
            keys = {
                normalized_label(anchor), normalized_label(body), label_tail(anchor), label_tail(body),
                normalized_label(anchor_base), normalized_label(body_base), label_tail(anchor_base), label_tail(body_base),
            }
            duplicate_index = int(re.search(r"-(\d+)$", anchor).group(1)) if re.search(r"-(\d+)$", anchor) else 0
            for key in keys:
                matches = list(dict.fromkeys(headings.get(key, []))) if key else []
                if matches:
                    candidates.append(matches[min(duplicate_index, len(matches) - 1)])
            if not candidates:
                contained: list[str] = []
                for key in keys:
                    if not key:
                        continue
                    for heading_key, heading_ids in headings.items():
                        if heading_key.endswith(key) or key.endswith(heading_key):
                            contained.extend(heading_ids)
                if len(set(contained)) == 1:
                    candidates.extend(contained)
            unique = list(dict.fromkeys(candidates))
            if len(unique) == 1:
                repaired += 1
                return f'{match.group(1)}#{unique[0]}{match.group(3)}{body}</a>'
            unresolved += 1
            return match.group(0)

        updated = re.sub(
            r'(<a\b[^>]*\bhref=["\'])#([^"\']+)(["\'][^>]*>)(.*?)</a>',
            replace,
            source,
            flags=re.I | re.S,
        )
        if updated != source:
            path.write_text(updated, encoding="utf-8", errors="xmlcharrefreplace")
    return repaired, unresolved


def viewer(title: str, tree: list[dict], slug: str) -> str:
    data = json.dumps(tree, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    download_link = (
        ' · <a class="home" href="downloads/行商浪人整合v2.0.chm">下载 CHM</a>'
        if slug == "roguetrader"
        else ""
    )
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
*{{box-sizing:border-box}}html,body{{height:100%;margin:0;font-family:"Microsoft YaHei","Segoe UI",sans-serif;color:#20242a}}
body{{display:grid;grid-template-columns:330px 1fr;background:#eef1f4}}aside{{height:100vh;overflow:auto;background:#fff;border-right:1px solid #cfd5dc;padding:16px 12px}}
.brand{{display:block;color:#76202e;font-size:20px;font-weight:700;text-decoration:none;margin:2px 8px 12px}}.home{{font-size:13px;margin-left:8px}}
#filter{{width:100%;padding:9px 10px;border:1px solid #aab2bb;border-radius:5px;margin:14px 0}}.node{{margin:2px 0 2px 8px}}.node a{{color:#27313b;text-decoration:none;line-height:1.55}}.node a:hover{{color:#8c2433;text-decoration:underline}}
details>summary{{cursor:pointer;color:#5c6670}}details>summary::marker{{color:#8c2433}}details .children{{border-left:1px solid #d9dde2;margin-left:5px;padding-left:5px}}
main{{height:100vh;min-width:0}}iframe{{width:100%;height:100%;border:0;background:#fff}}#toggle{{display:none}}
@media(max-width:760px){{body{{display:block}}aside{{position:fixed;z-index:5;width:min(88vw,340px);transform:translateX(-105%);transition:.2s;box-shadow:2px 0 12px #0003}}body.open aside{{transform:none}}main{{height:100vh}}#toggle{{display:block;position:fixed;z-index:6;right:14px;top:12px;border:0;border-radius:5px;background:#76202e;color:white;padding:9px 12px}}}}
</style></head><body><button id="toggle" type="button">目录</button><aside><a class="brand" href="../">{html.escape(title)}</a><a class="home" href="content/index.html" target="content">资料首页</a> · <a class="home" href="content/search.html" target="content">全文搜索</a>{download_link}<input id="filter" placeholder="筛选目录…"><nav id="tree"></nav></aside><main><iframe name="content" title="正文" src="content/index.html"></iframe></main>
<script>
var TREE={data};
function esc(s){{return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}}
function render(nodes,q){{var out='';for(var i=0;i<nodes.length;i++){{var n=nodes[i],kids=render(n.children||[],q),match=!q||n.name.toLowerCase().indexOf(q)>=0||kids;if(!match)continue;var link=n.local?'<a target="content" href="content/'+esc(n.local)+'">'+esc(n.name)+'</a>':esc(n.name);if(n.children&&n.children.length)out+='<details '+(q?'open':'')+'><summary>'+link+'</summary><div class="children">'+kids+'</div></details>';else out+='<div class="node">'+link+'</div>'}}return out}}
function refresh(){{document.getElementById('tree').innerHTML=render(TREE,document.getElementById('filter').value.toLowerCase())}}
document.getElementById('filter').oninput=refresh;document.getElementById('toggle').onclick=function(){{document.body.classList.toggle('open')}};document.getElementById('tree').onclick=function(e){{if(e.target.tagName==='A'&&innerWidth<=760)document.body.classList.remove('open')}};refresh();
</script></body></html>'''


def landing() -> str:
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Warhammer 40,000 TRPG 中文资料</title><style>
body{margin:0;background:#15191f;color:#eef1f4;font-family:"Microsoft YaHei","Segoe UI",sans-serif;min-height:100vh;display:grid;place-items:center}.wrap{width:min(920px,92vw);padding:50px 0}h1{font-size:clamp(30px,5vw,52px);margin:0 0 10px;color:#fff}.sub{color:#aeb8c4;margin-bottom:36px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:20px}.card{display:block;padding:28px;border:1px solid #46515e;border-radius:10px;background:#20262e;color:#fff;text-decoration:none;transition:.15s}.card:hover{transform:translateY(-2px);border-color:#b33449}.card h2{margin:0 0 8px;color:#d85a6e}.card p{margin:0;color:#bdc5ce;line-height:1.7}.note{margin-top:32px;color:#7f8a96;font-size:13px}
</style></head><body><div class="wrap"><h1>Warhammer 40,000 TRPG 中文资料</h1><p class="sub">网页版资料集：左侧目录、右侧正文，支持全文搜索。</p><div class="cards"><a class="card" href="deathwatch/"><h2>死亡守望</h2><p>Deathwatch 中文规则与扩展资料。</p></a><a class="card" href="roguetrader/"><h2>行商浪人</h2><p>Rogue Trader 中文规则与扩展资料。</p></a><a class="card" href="darkheresy/"><h2>黑暗异端</h2><p>Dark Heresy 中文规则与扩展资料。</p></a></div><p class="note">译者有没有标取决于文件内有没有译者说明；网页与 CHM 制作人耶利米，有问题请抓住耶利米拷打。</p></div></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description="Export compiled CHM sources as a GitHub Pages site.")
    parser.add_argument("--repo", type=Path, default=REPO, help="Target Pages repository")
    parser.add_argument(
        "--site",
        action="append",
        choices=[site[0] for site in SITES],
        help="Site(s) to export; defaults to all",
    )
    parser.add_argument("--write-landing", action="store_true", help="Regenerate the repository landing page")
    args = parser.parse_args()
    repo = args.repo.resolve()
    if not (repo / ".git").is_dir():
        raise RuntimeError(f"Pages repository is missing: {repo}")
    selected = set(args.site or [site[0] for site in SITES])
    stats = []
    for slug, title, source, hhc_name in SITES:
        if slug not in selected:
            continue
        if not (source / hhc_name).is_file():
            raise FileNotFoundError(source / hhc_name)
        target = repo / slug
        content = target / "content"
        copied = copy_content(source, content)
        repaired, unresolved = repair_heading_links(content)
        tree = parse_hhc(source / hhc_name)
        target.mkdir(parents=True, exist_ok=True)
        (target / "index.html").write_text(viewer(title, tree, slug), encoding="utf-8")
        stats.append({"site": slug, "files": copied, "toc_roots": len(tree), "links_repaired": repaired, "links_unresolved": unresolved})
    if args.write_landing:
        (repo / "index.html").write_text(landing(), encoding="utf-8")
    (repo / ".nojekyll").write_text("", encoding="ascii")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
