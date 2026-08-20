from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
from copy import deepcopy
from difflib import SequenceMatcher
from zipfile import ZipFile
from pathlib import Path

from PIL import Image
from PyPDF2 import PdfReader
from lxml import etree
from lxml import html as lxml_html

import rebuild_text_chm as common


ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "roguetrader_resources"
BUILD = ROOT / "build" / "roguetrader-chm"
CACHE = ROOT / "build" / "roguetrader-chm-cache"
TAU_BUILD = ROOT / "build" / "roguetrader-tau-chm"
FINAL = ROOT / "行商浪人整合v2.0.chm"
CORE_NAME = "40K·行商浪人核心规则书·V0.3(排版略优化).docx"
TAU_NAME = "《行商浪人》钛帝国角色手册.pdf"
FAITH_NAME = "rt信仰与钱币 介绍.docx"
ARMORY_NAME = "FFG军械库.docx"
SOUL_NAME = "RT灵魂掠夺者.docx"
SHIP_ROLES_NAME = "译文：舰船职务（行商浪人扩展进入风暴）.docx"
SCATTERED_TITLE = "RT散乱英雄碎片合集"
DISPLAY_TITLES = {
    CORE_NAME: "40K·行商浪人核心规则书·V0.3",
    TAU_NAME: "RT钛帝国扩",
    FAITH_NAME: "RT信仰与钱币（模组）",
    SOUL_NAME: "RT灵魂掠夺者（黑暗灵族扩）",
    SHIP_ROLES_NAME: "RT进入风暴·舰船职务",
}
PAGES_PER_CORE_PART = 10
CORE_BLOCKS_PER_PART = 10

ROGUETRADER_CSS = common.CSS + r"""
/* Semantic core-book pages. Keep the layout compatible with native CHM/IE9. */
body.core-topic,body.semantic-topic{font-size:16px;line-height:1.8;max-width:1120px;margin:0 auto;padding:28px 38px}
.core-topic h1,.semantic-topic h1{font-size:28px}.core-topic h2,.semantic-topic h2{font-size:22px}.core-topic h3,.semantic-topic h3{font-size:19px}
.core-topic p,.semantic-topic p{margin:10px 0}.core-topic p.prose,.semantic-topic p.prose{text-indent:2em}
.core-topic p.rule-entry,.semantic-topic p.rule-entry{text-indent:0;padding-left:12px;border-left:3px solid #d8dde2}
.core-topic blockquote,.semantic-topic blockquote{margin:12px 0;padding:10px 16px;background:#f7f7f5;border-left:4px solid #9f2636}
.semantic-nav{display:block;text-align:center;min-height:28px;line-height:28px;margin:16px 0;padding:9px 12px;background:#f3f5f7;border:1px solid #d8dde2}
.semantic-nav:after{content:"";display:block;clear:both}.semantic-nav .prev{float:left}.semantic-nav .next{float:right}
.semantic-path{color:#69727c;font-size:13px;margin:-12px 0 14px}.semantic-path a{color:#59636e}
.semantic-toc{margin:18px 0 24px;padding:14px 18px;background:#fffaf1;border:1px solid #d8c9ae;border-left:4px solid #9f2636}
.semantic-toc strong{color:#76202e}.semantic-toc ul{margin:8px 0 0;padding-left:24px;columns:2;column-gap:32px}.semantic-toc li{margin:4px 0;break-inside:avoid}
.semantic-toc .level-2{margin-left:18px}.semantic-toc .level-3{margin-left:36px;font-size:14px}
.table-scroll{width:100%;overflow:auto;margin:16px 0}.table-scroll table{margin:0;width:100%;min-width:560px;table-layout:auto}
.core-data-table caption{font-weight:700;text-align:left;color:#3f4852;padding:6px 0}
.core-data-table th,.core-data-table td{word-break:normal;overflow-wrap:break-word}
.core-data-table td:empty{padding:0;border-left:0;border-right:0}
.core-data-table blockquote{margin:0;padding:0;background:none;border:0}
.core-data-table p,.core-data-table p.rule-entry{margin:0;padding:0;text-indent:0;border:0;background:none}
.semantic-heading-image{text-align:center;margin:14px auto}.semantic-heading-image img{display:block;margin:auto}
.empty-topic{color:#69727c;background:#f3f5f7;border:1px solid #d8dde2;padding:12px 16px}
"""


def run(args: list[str], cwd: Path | None = None, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def find_pandoc() -> Path:
    temp = Path(os.environ.get("TEMP", str(ROOT / "build"))) / "deathwatch-chm-tools"
    return common.find_tool([temp / "pandoc-*" / "*" / "pandoc.exe"])


def wrap(title: str, source_name: str, body: str, body_class: str = "") -> str:
    class_attr = f' class="{html.escape(body_class)}"' if body_class else ""
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="gb2312">'
        '<meta http-equiv="X-UA-Compatible" content="IE=9">'
        f'<title>{html.escape(title)}</title><link rel="stylesheet" href="style.css"></head><body{class_attr}>'
        f'<h1>{html.escape(title)}</h1><div class="source-note">来源：{html.escape(source_name)}</div>{body}</body></html>'
    )


def visible(content: str) -> str:
    content = re.sub(r"<style\b.*?</style>|<script\b.*?</script>", " ", content, flags=re.I | re.S)
    content = html.unescape(re.sub(r"<[^>]+>", " ", content))
    return re.sub(r"\s+", " ", content).strip()


def display_title(source: Path) -> str:
    return DISPLAY_TITLES.get(source.name, source.stem)


def pandoc_docx(pandoc: Path, source: Path, topic_id: str) -> str:
    proc = run(
        [
            str(pandoc), "--from=docx", "--to=html5", "--wrap=none", "--strip-comments",
            f"--extract-media={topic_id}.media", str(source.resolve()),
        ],
        cwd=BUILD,
        capture=True,
    )
    fragment = proc.stdout.replace("\\", "/")
    # The topic wrapper already owns h1; preserve the source hierarchy below it.
    fragment = re.sub(r"<h1(\b[^>]*)>", r"<h2\1>", fragment, flags=re.I)
    fragment = re.sub(r"</h1>", "</h2>", fragment, flags=re.I)
    return fragment


def convert_legacy_doc(source: Path, target: Path) -> None:
    run([
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(ROOT / "tools" / "convert_doc_to_docx.ps1"),
        "-InputFile", str(source.resolve()), "-OutputFile", str(target.resolve()),
    ])


def core_outline(reader: PdfReader) -> list[tuple[int, str, int]]:
    entries: list[tuple[int, str, int]] = []

    def walk(items: list, depth: int = 1) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            try:
                page = reader.get_destination_page_number(item) + 1
            except Exception:
                continue
            label = re.sub(r"\s+", " ", getattr(item, "title", str(item))).strip()
            if label:
                entries.append((min(depth, 4), label, page))

    walk(reader.outline)
    return entries


def core_part_url(topic_id: str, page_no: int) -> str:
    part = (page_no - 1) // PAGES_PER_CORE_PART
    return f"{topic_id}.html" if part == 0 else f"{topic_id}-part{part + 1:03d}.html"


def numbered_part_url(topic_id: str, part: int) -> str:
    return f"{topic_id}.html" if part == 0 else f"{topic_id}-part{part + 1:03d}.html"


def core_toc_levels(source: Path) -> dict[str, int]:
    """Recover the core book's TOC hierarchy from its Word paragraph indents."""
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with ZipFile(source) as archive:
        document = etree.fromstring(archive.read("word/document.xml"))
    result: dict[str, int] = {}
    for paragraph in document.xpath(".//w:p", namespaces=namespace):
        instruction = " ".join(
            paragraph.xpath(".//w:instrText/text()", namespaces=namespace)
        )
        match = re.search(r'HYPERLINK\s+\\l\s+"([^"]+)"', instruction, re.I)
        if not match:
            continue
        indent_nodes = paragraph.xpath("./w:pPr/w:ind", namespaces=namespace)
        indent = 0
        if indent_nodes:
            node = indent_nodes[0]
            indent = int(
                node.get(f"{{{namespace['w']}}}left")
                or node.get(f"{{{namespace['w']}}}start")
                or 0
            )
        if indent <= 200:
            level = 0  # Book title, already represented by the parent topic.
        elif indent <= 700:
            level = 1  # Chapter.
        elif indent <= 1000:
            level = 2  # Section.
        else:
            level = 3  # Subsection.
        result[match.group(1)] = level
    return result


def clean_toc_label(label: str) -> str:
    return re.sub(r"\s+\d+\s*$", "", label).strip()


def core_topic_url(anchor: str) -> str:
    return "topic001.html" if anchor == "bookmark1" else f"core-{anchor}.html"


def normalized_heading(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text).lower()


def element_text(element: etree._Element) -> str:
    return "".join(element.itertext())


def remove_empty_first_column(table: etree._Element) -> bool:
    """Delete exactly one empty logical first column, shifting later cells left."""
    rows = table.xpath("./thead/tr|./tbody/tr|./tfoot/tr|./tr")
    if not rows:
        return False

    def cells(row: etree._Element) -> list[etree._Element]:
        return row.xpath("./th|./td")

    def empty(cell: etree._Element) -> bool:
        return (
            not re.sub(r"\s+", "", element_text(cell))
            and not cell.get("id")
            and not cell.xpath(".//img|.//table|.//a|.//*[@id]")
        )

    logical_widths = {
        sum(max(1, int(cell.get("colspan", "1") or "1")) for cell in cells(row))
        for row in rows
    }
    if (
        len(logical_widths) != 1
        or not logical_widths
        or next(iter(logical_widths)) < 2
        or any(cell.get("rowspan") for row in rows for cell in cells(row))
        or any(not cells(row) or not empty(cells(row)[0]) for row in rows)
    ):
        return False
    for row in rows:
        first = cells(row)[0]
        span = max(1, int(first.get("colspan", "1") or "1"))
        if span == 1:
            row.remove(first)
        else:
            first.set("colspan", str(span - 1))
    columns = table.xpath("./colgroup/col")
    if columns:
        first_col = columns[0]
        span = max(1, int(first_col.get("span", "1") or "1"))
        if span == 1:
            first_col.getparent().remove(first_col)
        else:
            first_col.set("span", str(span - 1))
    return True


def core_toc_entries(
    root: etree._Element, source: Path
) -> list[tuple[int, str, str]]:
    levels = core_toc_levels(source)
    entries: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    for link in root.xpath('.//a[starts-with(@href, "#bookmark")]'):
        anchor = link.get("href", "")[1:]
        label = clean_toc_label(re.sub(r"\s+", " ", link.text_content()).strip())
        level = levels.get(anchor, 0)
        if anchor not in seen and label and level:
            seen.add(anchor)
            entries.append((level, label[:120], anchor))
    if len(entries) < 170:
        raise RuntimeError(f"Too few semantic core topics found: {len(entries)}")
    return entries


def repair_core_anchor_locations(
    root: etree._Element, entries: list[tuple[int, str, str]]
) -> int:
    """Move Word bookmarks that landed in the TOC back to their real heading."""
    repaired = 0
    for _, label, anchor in entries:
        matches = root.xpath(f'.//*[@id="{anchor}"]')
        if not matches:
            raise RuntimeError(f"Core bookmark is missing: {anchor}")
        marker = matches[0]
        paragraphs = marker.xpath("ancestor::p[1]")
        actual = element_text(paragraphs[0]) if paragraphs else element_text(marker)
        wanted = normalized_heading(label)
        if wanted and wanted in normalized_heading(actual):
            continue
        candidates = []
        for paragraph in root.xpath(".//p[strong]"):
            if paragraph.xpath('.//a[starts-with(@href, "#bookmark")]'):
                continue
            text = re.sub(r"\s+", " ", element_text(paragraph)).strip()
            if normalized_heading(text).startswith(wanted):
                candidates.append(paragraph)
        if len(candidates) != 1:
            raise RuntimeError(
                f"Cannot uniquely repair {anchor} ({label}): {len(candidates)} candidates"
            )
        parent = marker.getparent()
        if parent is not None:
            parent.remove(marker)
        candidates[0].insert(0, marker)
        repaired += 1
    return repaired


def make_core_table_segment(
    source_table: etree._Element, rows: list[etree._Element]
) -> etree._Element:
    wrapper = etree.Element("div", {"class": "table-scroll"})
    table = etree.Element("table")
    for key, value in source_table.attrib.items():
        if key != "class":
            table.set(key, value)
    table.set("class", "core-data-table")
    colgroup = source_table.find("colgroup")
    if colgroup is not None:
        table.append(deepcopy(colgroup))

    selected = [deepcopy(row) for row in rows]
    if selected:
        first_cells = selected[0].xpath("./td|./th")
        first_text = re.sub(r"\s+", " ", element_text(selected[0])).strip()
        populated_cells = [
            cell
            for cell in first_cells
            if re.sub(r"\s+", "", element_text(cell))
            or cell.xpath(".//img|.//table")
        ]
        if (
            len(populated_cells) == 1
            and len(first_text) <= 100
            and re.match(r"^表\s*[0-9一二三四五六七八九十]+", first_text)
        ):
            caption = etree.Element("caption")
            caption.text = first_text
            table.append(caption)
            selected.pop(0)

    tbody = etree.Element("tbody")
    for row in selected:
        tbody.append(row)
    table.append(tbody)
    if remove_empty_first_column(table):
        wrapper.set("data-empty-first-column-removed", "1")

    header_words = re.compile(
        r"投掷|名称|技能|效果|类型|属性|花费|武器|伤害|射程|稀有度|结果|描述|数值|等级|要求|先决|物品|影响"
    )
    data_rows = tbody.xpath("./tr")
    if data_rows:
        first_cells = data_rows[0].xpath("./td|./th")
        header_text = " ".join(element_text(cell) for cell in first_cells)
        if first_cells and header_words.search(header_text):
            for cell in first_cells:
                cell.tag = "th"
    wrapper.append(table)
    return wrapper


def linearize_core(root: etree._Element) -> tuple[list[etree._Element], dict[str, int]]:
    """Flatten Word page-layout containers while preserving real data tables."""
    units: list[etree._Element] = []
    stats = {
        "layout_tables_removed": 0,
        "data_tables_kept": 0,
        "mixed_tables_split": 0,
        "empty_first_columns_removed_after_split": 0,
    }

    def direct_rows(table: etree._Element) -> list[etree._Element]:
        return table.xpath("./thead/tr|./tbody/tr|./tfoot/tr|./tr")

    def row_cells(row: etree._Element) -> list[etree._Element]:
        return row.xpath("./td|./th")

    def emit_children(container: etree._Element) -> None:
        for child in container:
            if isinstance(child.tag, str):
                emit(child)

    def emit_cell(cell: etree._Element) -> None:
        if len(cell):
            emit_children(cell)
        elif re.sub(r"\s+", "", element_text(cell)):
            paragraph = etree.Element("p")
            paragraph.text = element_text(cell)
            units.append(paragraph)

    def row_is_flow(row: etree._Element, max_columns: int, row_count: int) -> bool:
        cells = row_cells(row)
        if row.xpath('.//*[@id and starts-with(@id, "bookmark")]'):
            return True
        if row.xpath(".//table"):
            return True
        if any(len(cell.xpath(".//p")) >= 3 for cell in cells):
            return True
        text_length = len(re.sub(r"\s+", "", element_text(row)))
        if len(cells) == 1 and max_columns > 1 and text_length > 120:
            return True
        if row_count == 1 and any(
            len(re.sub(r"\s+", "", element_text(cell))) > 200 for cell in cells
        ):
            return True
        return False

    def emit_table(table: etree._Element) -> None:
        rows = direct_rows(table)
        shapes = [len(row_cells(row)) for row in rows]
        if not rows or not shapes:
            stats["layout_tables_removed"] += 1
            emit_children(table)
            return
        max_columns = max(shapes)
        if max_columns <= 1 or table.xpath("./tbody/tr/td/table|./tr/td/table"):
            stats["layout_tables_removed"] += 1
            for row in rows:
                for cell in row_cells(row):
                    emit_cell(cell)
            return

        data_rows: list[etree._Element] = []

        def flush_data() -> None:
            if not data_rows:
                return
            segment = make_core_table_segment(table, data_rows)
            if segment.get("data-empty-first-column-removed") == "1":
                stats["empty_first_columns_removed_after_split"] += 1
                segment.attrib.pop("data-empty-first-column-removed", None)
            units.append(segment)
            stats["data_tables_kept"] += 1
            data_rows.clear()

        had_flow = False
        for row in rows:
            if row_is_flow(row, max_columns, len(rows)):
                flush_data()
                had_flow = True
                for cell in row_cells(row):
                    emit_cell(cell)
            else:
                data_rows.append(row)
        flush_data()
        if had_flow:
            stats["mixed_tables_split"] += 1

    def emit(node: etree._Element) -> None:
        tag = node.tag.lower() if isinstance(node.tag, str) else ""
        if tag == "table":
            emit_table(node)
        elif tag in {"blockquote", "div", "section", "article", "td", "th"}:
            emit_children(node)
        else:
            units.append(deepcopy(node))

    for child in root:
        if isinstance(child.tag, str):
            emit(child)
    return units, stats


def rewrite_core_links(element: etree._Element) -> None:
    for link in element.xpath('.//a[starts-with(@href, "#bookmark")]'):
        anchor = link.get("href", "")[1:]
        link.set("href", f"{core_topic_url(anchor)}#{anchor}")


def prepare_core_units(
    source_units: list[etree._Element], anchor: str, label: str
) -> list[etree._Element]:
    units = [deepcopy(unit) for unit in source_units]
    if units:
        marker_units = [
            unit for unit in units if unit.xpath(f'.//*[@id="{anchor}"]')
        ]
        if marker_units:
            title_unit = marker_units[0]
            title_text = re.sub(r"\s+", " ", element_text(title_unit)).strip()
            if normalized_heading(title_text).startswith(normalized_heading(label)):
                units.remove(title_unit)
    anchor_marker = etree.Element("span", {"id": anchor, "class": "anchor"})
    units.insert(0, anchor_marker)

    for unit in units:
        rewrite_core_links(unit)
        for element in unit.xpath(".//*[@style]"):
            if element.tag.lower() not in {"table", "col"}:
                element.attrib.pop("style", None)
        for image in unit.xpath(".//img"):
            image.attrib.pop("style", None)
            if not image.get("alt"):
                image.set("alt", f"{label}插图")
        paragraphs = ([unit] if unit.tag.lower() == "p" else []) + unit.xpath(".//p")
        for paragraph in paragraphs:
            if paragraph.xpath("ancestor::table"):
                continue
            text = re.sub(r"\s+", " ", element_text(paragraph)).strip()
            strong_text = " ".join(
                re.sub(r"\s+", " ", element_text(item)).strip()
                for item in paragraph.xpath("./strong")
            ).strip()
            if strong_text and normalized_heading(strong_text) == normalized_heading(text) and len(text) <= 48:
                paragraph.tag = "h2"
            elif paragraph.xpath("./strong"):
                paragraph.set("class", "rule-entry")
            elif len(text) >= 55 and re.search(r"[。！？）]$", text):
                paragraph.set("class", "prose")
    return units


def serialize_core_units(units: list[etree._Element]) -> str:
    return "\n".join(
        etree.tostring(unit, encoding="unicode", method="html") for unit in units
    )


def nested_core_toc(entries: list[tuple[int, str, str]]) -> str:
    items = [
        (level, label, f"{core_topic_url(anchor)}#{anchor}")
        for level, label, anchor in entries
    ]
    output = ['<nav class="semantic-toc"><strong>核心书目录</strong><ul>']
    previous = 1
    for index, (raw_level, label, target) in enumerate(items):
        level = max(1, min(3, raw_level))
        if index == 0:
            level = 1
        elif level > previous + 1:
            level = previous + 1
        if level > previous:
            output.extend("<ul>" for _ in range(level - previous))
        elif level < previous:
            output.extend("</ul>" for _ in range(previous - level))
        output.append(
            f'<li class="level-{level}"><a href="{html.escape(target)}">{html.escape(label)}</a></li>'
        )
        previous = level
    output.extend("</ul>" for _ in range(previous))
    output.append("</nav>")
    return "".join(output)


def normalize_core_tables(root: etree._Element) -> dict[str, int]:
    """Remove one fully empty first column and normalize obvious spacer rows."""
    removed_columns = 0
    merged_rows = 0
    removed_rows = 0
    for table in reversed(root.xpath(".//table")):
        rows = table.xpath("./thead/tr|./tbody/tr|./tfoot/tr|./tr")
        if not rows:
            continue

        def cells(row: etree._Element) -> list[etree._Element]:
            return row.xpath("./th|./td")

        def empty(cell: etree._Element) -> bool:
            return (
                not re.sub(r"\s+", "", element_text(cell))
                and not cell.get("id")
                and not cell.xpath(".//img|.//table|.//a|.//*[@id]")
            )

        for row in list(rows):
            row_cells = cells(row)
            if row_cells and all(empty(cell) for cell in row_cells):
                row.getparent().remove(row)
                removed_rows += 1
        rows = table.xpath("./thead/tr|./tbody/tr|./tfoot/tr|./tr")
        if remove_empty_first_column(table):
            removed_columns += 1

        for row in rows:
            row_cells = cells(row)
            if len(row_cells) < 2:
                continue
            populated = [cell for cell in row_cells if not empty(cell)]
            if len(populated) != 1:
                continue
            header = populated[0]
            for cell in list(row_cells):
                if cell is not header:
                    row.remove(cell)
            if row.index(header) != 0:
                row.remove(header)
                row.insert(0, header)
            header.set("colspan", str(len(row_cells)))
            merged_rows += 1
    return {
        "removed_columns": removed_columns,
        "merged_rows": merged_rows,
        "removed_rows": removed_rows,
    }


def build_core_docx(source: Path, topic_id: str, pandoc: Path) -> dict:
    """Build one semantic HTML page per core-book TOC node."""
    book_title = display_title(source)
    fragment = pandoc_docx(pandoc, source, topic_id)
    root = lxml_html.fragment_fromstring(fragment, create_parent="div")
    entries = core_toc_entries(root, source)
    repaired_anchors = repair_core_anchor_locations(root, entries)
    table_cleanup = normalize_core_tables(root)
    units, layout_cleanup = linearize_core(root)

    anchor_positions: dict[str, int] = {}
    for index, unit in enumerate(units):
        identifiers = []
        if unit.get("id"):
            identifiers.append(unit.get("id"))
        identifiers.extend(unit.xpath(".//*[@id]/@id"))
        for identifier in identifiers:
            if identifier.startswith("bookmark"):
                anchor_positions.setdefault(identifier, index)

    missing = [anchor for _, _, anchor in entries if anchor not in anchor_positions]
    if missing:
        raise RuntimeError(f"Semantic core anchors missing after layout cleanup: {missing}")
    ordered_positions = [anchor_positions[anchor] for _, _, anchor in entries]
    if ordered_positions != sorted(ordered_positions) or len(set(ordered_positions)) != len(ordered_positions):
        raise RuntimeError("Semantic core anchors are duplicated or out of order")

    navigation = [
        (level, label, f"{core_topic_url(anchor)}#{anchor}")
        for level, label, anchor in entries
    ]
    chapter_pattern = re.compile(r"^第[一二三四五六七八九十百零〇两]+章[：:]")
    chapter_navigation = [
        (level, label, target)
        for level, label, target in navigation
        if level == 1 and chapter_pattern.match(label)
    ]
    if len(chapter_navigation) < 10:
        raise RuntimeError(f"Too few core chapters found: {len(chapter_navigation)}")

    parent_indexes: list[int | None] = []
    for index, (level, _, _) in enumerate(entries):
        parent = None
        for candidate in range(index - 1, -1, -1):
            if entries[candidate][0] < level:
                parent = candidate
                break
        parent_indexes.append(parent)

    child_indexes: dict[int, list[int]] = {index: [] for index in range(len(entries))}
    for index, parent in enumerate(parent_indexes):
        if parent is not None and entries[index][0] == entries[parent][0] + 1:
            child_indexes[parent].append(index)

    toc_unit_indexes = [
        index
        for index, unit in enumerate(units)
        if unit.xpath('.//a[starts-with(@href, "#bookmark")]')
    ]
    landing_end = min(toc_unit_indexes) if toc_unit_indexes else anchor_positions[entries[0][2]]
    landing_units = [deepcopy(unit) for unit in units[:landing_end]]
    for unit in landing_units:
        rewrite_core_links(unit)
    landing_body = serialize_core_units(landing_units) + nested_core_toc(entries)
    (BUILD / f"{topic_id}.html").write_text(
        wrap(book_title, source.name, landing_body, "core-topic"),
        encoding="gbk",
        errors="xmlcharrefreplace",
    )

    parts: list[dict] = []
    empty_topics = 0
    for index, (level, label, anchor) in enumerate(entries):
        start = anchor_positions[anchor]
        end = (
            anchor_positions[entries[index + 1][2]]
            if index + 1 < len(entries)
            else len(units)
        )
        section_units = prepare_core_units(units[start:end], anchor, label)

        parent = parent_indexes[index]
        parent_url = (
            f"{core_topic_url(entries[parent][2])}#{entries[parent][2]}"
            if parent is not None
            else f"{topic_id}.html"
        )
        previous_link = ""
        next_link = ""
        if index > 0:
            previous_label = entries[index - 1][1]
            previous_anchor = entries[index - 1][2]
            previous_link = (
                f'<a class="prev" href="{core_topic_url(previous_anchor)}#{previous_anchor}">'
                f'← {html.escape(previous_label)}</a>'
            )
        if index + 1 < len(entries):
            next_label = entries[index + 1][1]
            next_anchor = entries[index + 1][2]
            next_link = (
                f'<a class="next" href="{core_topic_url(next_anchor)}#{next_anchor}">'
                f'{html.escape(next_label)} →</a>'
            )
        up_label = entries[parent][1] if parent is not None else "核心书目录"
        pager = (
            '<nav class="semantic-nav">'
            f'{previous_link}<a class="up" href="{html.escape(parent_url)}">'
            f'↑ {html.escape(up_label)}</a>{next_link}</nav>'
        )

        path_parts = []
        cursor = parent
        while cursor is not None:
            path_parts.append(entries[cursor])
            cursor = parent_indexes[cursor]
        path_links = [f'<a href="{topic_id}.html">核心书</a>']
        for _, ancestor_label, ancestor_anchor in reversed(path_parts):
            path_links.append(
                f'<a href="{core_topic_url(ancestor_anchor)}#{ancestor_anchor}">'
                f'{html.escape(ancestor_label)}</a>'
            )
        breadcrumb = '<div class="semantic-path">' + " › ".join(path_links) + "</div>"

        child_toc = ""
        if child_indexes[index]:
            child_links = "".join(
                f'<li><a href="{core_topic_url(entries[child][2])}#{entries[child][2]}">'
                f'{html.escape(entries[child][1])}</a></li>'
                for child in child_indexes[index]
            )
            child_toc = (
                '<nav class="semantic-toc"><strong>本节下级主题</strong>'
                f'<ul>{child_links}</ul></nav>'
            )

        meaningful = sum(
            len(re.sub(r"\s+", "", element_text(unit))) for unit in section_units
        )
        if meaningful <= len(label) + 2 and not any(
            unit.xpath(".//img|.//table") for unit in section_units
        ):
            empty_topics += 1
            empty_note = '<p class="empty-topic">本节点没有独立导言，请从下级主题继续阅读。</p>'
        else:
            empty_note = ""
        body = (
            breadcrumb + pager + child_toc + empty_note
            + serialize_core_units(section_units) + pager
        )
        url = core_topic_url(anchor)
        (BUILD / url).write_text(
            wrap(label, source.name, body, "core-topic"),
            encoding="gbk",
            errors="xmlcharrefreplace",
        )
        parts.append({"title": f"{book_title} · {label}", "url": f"{url}#{anchor}"})

    parts.insert(0, {"title": f"{book_title} · 目录", "url": f"{topic_id}.html"})
    return {
        "title": book_title, "url": f"{topic_id}.html", "type": "DOCX",
        "parts": parts, "navigation": navigation,
        "bookmarks": len(navigation), "chapters": len(chapter_navigation),
        "sections": sum(level == 2 for level, _, _ in navigation),
        "subsections": sum(level == 3 for level, _, _ in navigation),
        "semantic_pages": len(entries),
        "empty_topics": empty_topics,
        "repaired_anchors": repaired_anchors,
        "layout_cleanup": layout_cleanup,
        "table_cleanup": table_cleanup,
    }


def semantic_document_url(anchor: str) -> str:
    return f"{anchor}.html"


def assign_document_heading_ids(
    root: etree._Element, topic_id: str
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for index, heading in enumerate(root.xpath(".//h2|.//h3|.//h4"), 1):
        legacy_id = heading.get("id", "")
        stable_id = f"{topic_id}-h{index:04d}"
        heading.set("id", stable_id)
        records.append(
            {
                "level": int(heading.tag[-1]),
                "label": re.sub(r"\s+", " ", element_text(heading)).strip(),
                "anchor": stable_id,
                "legacy_id": legacy_id,
            }
        )
    return records


def promote_linked_bold_headings(root: etree._Element) -> int:
    """Promote TOC-linked bold-only paragraphs that Word did not style as headings."""
    existing_headings = root.xpath(".//h2|.//h3|.//h4")
    existing_ids = {heading.get("id", "") for heading in existing_headings}
    existing_labels = [
        normalized_heading(re.sub(r"\s+", " ", element_text(heading)).strip())
        for heading in existing_headings
    ]
    candidates: list[tuple[str, etree._Element]] = []
    for paragraph in root.xpath(".//p[strong and not(.//a)]"):
        paragraph_text = re.sub(r"\s+", " ", element_text(paragraph)).strip()
        strong_text = " ".join(
            re.sub(r"\s+", " ", element_text(item)).strip()
            for item in paragraph.xpath("./strong")
        ).strip()
        if strong_text and normalized_heading(strong_text) == normalized_heading(paragraph_text):
            candidates.append((normalized_heading(strong_text), paragraph))

    promoted = 0
    used: set[int] = set()
    for link in root.xpath('.//a[starts-with(@href, "#")]'):
        target = link.get("href", "")[1:]
        label = re.sub(r"\s+\d+\s*$", "", element_text(link)).strip()
        wanted = normalized_heading(label)
        existing_matches = [
            key
            for key in existing_labels
            if wanted and (key == wanted or key.endswith(wanted) or wanted.endswith(key))
        ]
        if target in existing_ids or len(existing_matches) == 1:
            continue
        matches = [
            (key, paragraph)
            for key, paragraph in candidates
            if id(paragraph) not in used and wanted and (key == wanted or key.endswith(wanted) or wanted.endswith(key))
        ]
        if len(matches) != 1:
            continue
        _, paragraph = matches[0]
        if paragraph.tag.lower() in {"h2", "h3", "h4"}:
            continue
        paragraph.tag = "h2" if re.match(r"^[一二三四五六七八九十]+、", label) else "h3"
        paragraph.set("id", target)
        used.add(id(paragraph))
        promoted += 1
    return promoted


def customize_armory_headings(root: etree._Element) -> dict[str, int]:
    """Merge selected armory H2 ranges into their preceding section pages."""
    ranges = [
        ("古代科技镜面盾", "护身符"),
        ("梅洛维奇联合公司", "林德温武器库"),
    ]
    normalized_ranges = [
        (normalized_heading(start), normalized_heading(end)) for start, end in ranges
    ]
    active_end = ""
    merged = 0
    removed_empty = 0
    for heading in list(root.xpath(".//h2")):
        label = normalized_heading(element_text(heading))
        if label == normalized_heading("道具"):
            heading.getparent().remove(heading)
            removed_empty += 1
            continue
        if not active_end:
            for start, end in normalized_ranges:
                if label == start:
                    active_end = end
                    break
        if active_end:
            heading.tag = "h3"
            merged += 1
            if label == active_end:
                active_end = ""
    if active_end:
        raise RuntimeError("Armory merge range did not reach its configured end heading")
    if merged != 14 or removed_empty != 1:
        raise RuntimeError(
            f"Unexpected armory customization result: merged={merged}, removed={removed_empty}"
        )
    return {"merged_headings": merged, "removed_empty_sections": removed_empty}


def semantic_toc_html(
    navigation: list[tuple[int, str, str]], title: str
) -> str:
    links = "".join(
        f'<li class="level-{max(1, min(3, level))}"><a href="{html.escape(target)}">'
        f"{html.escape(label)}</a></li>"
        for level, label, target in navigation
    )
    return (
        f'<nav class="semantic-toc"><strong>{html.escape(title)}</strong>'
        f"<ul>{links}</ul></nav>"
    )


def rewrite_document_links(
    element: etree._Element,
    targets: dict[str, str],
    records: list[dict[str, object]],
) -> int:
    unresolved = 0
    normalized_records: list[tuple[str, str]] = [
        (normalized_heading(str(record["label"])), targets[str(record["anchor"])])
        for record in records
    ]
    for link in element.xpath('.//a[starts-with(@href, "#")]'):
        old_target = link.get("href", "")[1:]
        target = targets.get(old_target)
        if target is None:
            label = re.sub(r"\s+\d+\s*$", "", element_text(link)).strip()
            wanted = normalized_heading(label)
            exact = [value for key, value in normalized_records if key == wanted]
            if len(exact) == 1:
                target = exact[0]
            else:
                suffix = [
                    value
                    for key, value in normalized_records
                    if wanted and (key.endswith(wanted) or wanted.endswith(key))
                ]
                if len(suffix) == 1:
                    target = suffix[0]
        if target is not None:
            link.set("href", target)
        else:
            unresolved += 1
    return unresolved


def prepare_semantic_units(
    source_units: list[etree._Element], anchor: str, label: str
) -> list[etree._Element]:
    units = [deepcopy(unit) for unit in source_units]
    heading_media: list[etree._Element] = []
    for index, unit in enumerate(units):
        if unit.get("id") == anchor:
            for image in unit.xpath(".//img"):
                figure = etree.Element("figure", {"class": "semantic-heading-image"})
                figure.append(deepcopy(image))
                heading_media.append(figure)
            units.pop(index)
            break
    units.insert(0, etree.Element("span", {"id": anchor, "class": "anchor"}))
    for offset, figure in enumerate(heading_media, 1):
        units.insert(offset, figure)
    for unit in units:
        for element in unit.xpath(".//*[@style]"):
            if element.tag.lower() not in {"table", "col"}:
                element.attrib.pop("style", None)
        for image in unit.xpath(".//img"):
            image.attrib.pop("style", None)
            if not image.get("alt"):
                image.set("alt", f"{label}插图")
        paragraphs = ([unit] if unit.tag.lower() == "p" else []) + unit.xpath(".//p")
        for paragraph in paragraphs:
            if paragraph.xpath("ancestor::table"):
                continue
            text = re.sub(r"\s+", " ", element_text(paragraph)).strip()
            if len(text) >= 55 and re.search(r"[。！？）]$", text):
                paragraph.set("class", "prose")
    return units


def build_semantic_document(
    source: Path,
    actual: Path,
    topic_id: str,
    pandoc: Path,
    converted: bool,
) -> dict:
    book_title = display_title(source)
    fragment = pandoc_docx(pandoc, actual, topic_id)
    root = lxml_html.fragment_fromstring(fragment, create_parent="div")
    armory_customization = (
        customize_armory_headings(root)
        if source.name == ARMORY_NAME
        else {"merged_headings": 0, "removed_empty_sections": 0}
    )
    detailed = source.name in {SOUL_NAME, SHIP_ROLES_NAME}
    promoted_headings = promote_linked_bold_headings(root) if detailed else 0
    records = assign_document_heading_ids(root, topic_id)
    table_cleanup = normalize_core_tables(root)
    units, layout_cleanup = linearize_core(root)

    positions: dict[str, int] = {}
    for index, unit in enumerate(units):
        identifiers = ([unit.get("id")] if unit.get("id") else []) + unit.xpath(".//*[@id]/@id")
        for identifier in identifiers:
            positions.setdefault(identifier, index)

    split_records = [
        record
        for record in records
        if int(record["level"]) == 2 or (detailed and int(record["level"]) in {3, 4})
    ]
    split_records = [record for record in split_records if str(record["anchor"]) in positions]
    split_positions = [positions[str(record["anchor"])] for record in split_records]
    if split_positions != sorted(split_positions) or len(set(split_positions)) != len(split_positions):
        raise RuntimeError(f"Semantic headings are duplicated or out of order: {source.name}")

    owner_for_anchor: dict[str, str] = {}
    current_owner = f"{topic_id}.html"
    split_by_position = {
        positions[str(record["anchor"])]: semantic_document_url(str(record["anchor"]))
        for record in split_records
    }
    records_by_position = {
        positions[str(record["anchor"])]: record
        for record in records
        if str(record["anchor"]) in positions
    }
    for index in range(len(units)):
        if index in split_by_position:
            current_owner = split_by_position[index]
        record = records_by_position.get(index)
        if record is not None:
            owner_for_anchor[str(record["anchor"])] = current_owner

    targets: dict[str, str] = {}
    for record in records:
        anchor = str(record["anchor"])
        owner = owner_for_anchor.get(anchor, f"{topic_id}.html")
        target = f"{owner}#{anchor}"
        targets[anchor] = target
        legacy_id = str(record.get("legacy_id", ""))
        if legacy_id:
            targets[legacy_id] = target

    unresolved_links = 0
    for unit in units:
        unresolved_links += rewrite_document_links(unit, targets, records)

    if not split_records:
        body_units = prepare_semantic_units(units, f"{topic_id}-start", book_title)
        (BUILD / f"{topic_id}.html").write_text(
            wrap(book_title, source.name, serialize_core_units(body_units), "semantic-topic"),
            encoding="gbk",
            errors="xmlcharrefreplace",
        )
        return {
            "title": book_title,
            "url": f"{topic_id}.html",
            "type": "DOC（已转换）" if converted else "DOCX",
            "semantic_pages": 0,
            "unresolved_links": unresolved_links,
            "promoted_headings": promoted_headings,
            "armory_customization": armory_customization,
            "layout_cleanup": layout_cleanup,
            "table_cleanup": table_cleanup,
        }

    base_level = min(int(record["level"]) for record in split_records)
    navigation: list[tuple[int, str, str]] = []
    for record in split_records:
        level = int(record["level"]) - base_level + 1 if detailed else 1
        anchor = str(record["anchor"])
        navigation.append(
            (level, str(record["label"]), f"{semantic_document_url(anchor)}#{anchor}")
        )

    landing_end = split_positions[0]
    landing_units = [deepcopy(unit) for unit in units[:landing_end]]
    landing_body = serialize_core_units(landing_units) + semantic_toc_html(
        navigation, f"{book_title}目录"
    )
    (BUILD / f"{topic_id}.html").write_text(
        wrap(book_title, source.name, landing_body, "semantic-topic"),
        encoding="gbk",
        errors="xmlcharrefreplace",
    )

    parent_indexes: list[int | None] = []
    for index, (level, _, _) in enumerate(navigation):
        parent = None
        for candidate in range(index - 1, -1, -1):
            if navigation[candidate][0] < level:
                parent = candidate
                break
        parent_indexes.append(parent)
    child_indexes: dict[int, list[int]] = {index: [] for index in range(len(navigation))}
    for index, parent in enumerate(parent_indexes):
        if parent is not None and navigation[index][0] == navigation[parent][0] + 1:
            child_indexes[parent].append(index)

    parts: list[dict[str, str]] = [
        {"title": f"{book_title} · 目录", "url": f"{topic_id}.html"}
    ]
    empty_topics = 0
    for index, record in enumerate(split_records):
        label = str(record["label"])
        anchor = str(record["anchor"])
        start = positions[anchor]
        end = split_positions[index + 1] if index + 1 < len(split_positions) else len(units)
        section_units = prepare_semantic_units(units[start:end], anchor, label)
        parent = parent_indexes[index]
        parent_url = navigation[parent][2] if parent is not None else f"{topic_id}.html"
        parent_label = navigation[parent][1] if parent is not None else f"{book_title}目录"
        previous_link = ""
        next_link = ""
        if index > 0:
            previous_link = (
                f'<a class="prev" href="{html.escape(navigation[index - 1][2])}">'
                f'← {html.escape(navigation[index - 1][1])}</a>'
            )
        if index + 1 < len(navigation):
            next_link = (
                f'<a class="next" href="{html.escape(navigation[index + 1][2])}">'
                f'{html.escape(navigation[index + 1][1])} →</a>'
            )
        pager = (
            '<nav class="semantic-nav">'
            f'{previous_link}<a class="up" href="{html.escape(parent_url)}">'
            f'↑ {html.escape(parent_label)}</a>{next_link}</nav>'
        )
        breadcrumb = (
            '<div class="semantic-path">'
            f'<a href="{topic_id}.html">{html.escape(book_title)}</a></div>'
        )
        child_toc = ""
        if child_indexes[index]:
            child_navigation = [navigation[child] for child in child_indexes[index]]
            child_toc = semantic_toc_html(child_navigation, "本节下级主题")
        meaningful = sum(len(re.sub(r"\s+", "", element_text(unit))) for unit in section_units)
        empty_note = ""
        if meaningful <= len(label) + 2 and not any(unit.xpath(".//img|.//table") for unit in section_units):
            empty_topics += 1
            empty_note = '<p class="empty-topic">本节点没有独立导言，请从下级主题继续阅读。</p>'
        body = breadcrumb + pager + child_toc + empty_note + serialize_core_units(section_units) + pager
        url = semantic_document_url(anchor)
        (BUILD / url).write_text(
            wrap(label, source.name, body, "semantic-topic"),
            encoding="gbk",
            errors="xmlcharrefreplace",
        )
        parts.append({"title": f"{book_title} · {label}", "url": f"{url}#{anchor}"})

    return {
        "title": book_title,
        "url": f"{topic_id}.html",
        "type": "DOC（已转换）" if converted else "DOCX",
        "parts": parts,
        "navigation": navigation,
        "semantic_pages": len(split_records),
        "empty_topics": empty_topics,
        "unresolved_links": unresolved_links,
        "promoted_headings": promoted_headings,
        "armory_customization": armory_customization,
        "layout_cleanup": layout_cleanup,
        "table_cleanup": table_cleanup,
    }


def extract_core_images(pdfimages: Path, source: Path, topic_id: str) -> dict[int, list[str]]:
    listing = run([str(pdfimages), "-list", str(source)], capture=True).stdout
    selected: list[tuple[int, int]] = []
    for line in listing.splitlines():
        match = re.match(r"^\s*(\d+)\s+(\d+)\s+(\S+)", line)
        if match and match.group(3).lower() == "image":
            selected.append((int(match.group(1)), int(match.group(2))))
    if not selected:
        return {}
    media = BUILD / f"{topic_id}.media"
    media.mkdir(parents=True, exist_ok=True)
    run([str(pdfimages), "-all", str(source), str(media / "raw")], capture=True)
    result: dict[int, list[str]] = {}
    for image_index, (page_no, number) in enumerate(selected, 1):
        candidates = sorted(media.glob(f"raw-{number:03d}.*"))
        if not candidates:
            continue
        raw = candidates[0]
        target = media / f"image-{image_index:03d}.jpg"
        try:
            with Image.open(raw) as image:
                image.convert("RGB").save(target, "JPEG", quality=92, optimize=True)
        except Exception:
            target = media / f"image-{image_index:03d}{raw.suffix.lower()}"
            shutil.copy2(raw, target)
        result.setdefault(page_no, []).append(target.relative_to(BUILD).as_posix())
    for raw in media.glob("raw-*.*"):
        raw.unlink()
    return result


def inject_images(fragment: str, page_no: int, paths: list[str]) -> str:
    if not paths:
        return fragment
    figures = ['<div class="pdf-figures">']
    for index, path in enumerate(paths, 1):
        figures.append(
            f'<figure class="pdf-figure"><img src="{html.escape(path)}" alt="原 PDF 第 {page_no} 页图片 {index}">'
            f'<figcaption>原 PDF 第 {page_no} 页图片 {index}</figcaption></figure>'
        )
    figures.append("</div>")
    pattern = re.compile(
        rf'(<section\b[^>]*\bid=["\']page-{page_no:03d}["\'][^>]*>)', re.I
    )
    return pattern.sub(r"\1" + "".join(figures), fragment, count=1)


def build_core(source: Path, topic_id: str, pdftotext: Path, pdfimages: Path) -> dict:
    ascii_pdf = BUILD / "core-source.pdf"
    shutil.copy2(source, ascii_pdf)
    text_path = BUILD / "core-source.txt"
    run([str(pdftotext), "-q", "-layout", "-enc", "UTF-8", str(ascii_pdf), str(text_path)])
    raw_pages = text_path.read_text(encoding="utf-8", errors="replace").split("\f")
    if raw_pages and not raw_pages[-1].strip():
        raw_pages.pop()
    reader = PdfReader(str(ascii_pdf))
    if len(raw_pages) != len(reader.pages):
        raise RuntimeError(f"Core page mismatch: text={len(raw_pages)}, PDF={len(reader.pages)}")
    images = extract_core_images(pdfimages, ascii_pdf, topic_id)
    parts: list[dict] = []
    for start in range(0, len(raw_pages), PAGES_PER_CORE_PART):
        page_numbers = range(start + 1, min(start + PAGES_PER_CORE_PART, len(raw_pages)) + 1)
        sections = []
        for page_no in page_numbers:
            section = common.local_page(page_no, raw_pages[page_no - 1])
            section = inject_images(section, page_no, images.get(page_no, []))
            sections.append(section)
        url = core_part_url(topic_id, start + 1)
        end = min(start + PAGES_PER_CORE_PART, len(raw_pages))
        title = source.stem if start == 0 else f"{source.stem}（第 {start + 1}–{end} 页）"
        content = wrap(title, source.name, "\n".join(sections))
        (BUILD / url).write_text(content, encoding="gbk", errors="xmlcharrefreplace")
        parts.append({"title": title, "url": url})
    outline = core_outline(reader)
    return {
        "title": source.stem, "url": f"{topic_id}.html", "type": "PDF 文本层",
        "parts": parts, "outline": outline, "pages": len(raw_pages),
        "images": sum(len(paths) for paths in images.values()),
    }


def copy_tau(topic_id: str) -> dict:
    book_title = DISPLAY_TITLES[TAU_NAME]
    if not (TAU_BUILD / "manifest.json").is_file():
        raise RuntimeError("Tau test build is missing; run build_roguetrader_tau_chm.py first")
    source_media = TAU_BUILD / "topic001.media"
    target_media = BUILD / f"{topic_id}.media"
    if source_media.is_dir():
        shutil.copytree(source_media, target_media)
    reader = PdfReader(str(SOURCE_DIR / TAU_NAME))
    source_pages = [TAU_BUILD / "topic001.html"] + sorted(
        TAU_BUILD.glob("topic001-part*.html"), key=lambda path: path.name
    )
    page_sections: dict[int, etree._Element] = {}
    for source_page in source_pages:
        content = source_page.read_text(encoding="gbk").replace(
            "topic001.media/", f"{topic_id}.media/"
        )
        document = lxml_html.fromstring(content)
        for section in document.xpath('//section[contains(concat(" ", normalize-space(@class), " "), " page ")]'):
            match = re.search(r"(\d+)$", section.get("id", ""))
            if match:
                page_sections[int(match.group(1))] = deepcopy(section)
    if len(page_sections) != len(reader.pages):
        raise RuntimeError(f"Tau page extraction mismatch: {len(page_sections)} / {len(reader.pages)}")

    combined = etree.Element("div")
    for page_no in range(1, len(reader.pages) + 1):
        marker = etree.Element("span", {"id": f"page-{page_no:03d}", "class": "anchor"})
        combined.append(marker)
        section = page_sections[page_no]
        for child in section:
            combined.append(deepcopy(child))
    table_cleanup = normalize_core_tables(combined)
    units, layout_cleanup = linearize_core(combined)

    positions: dict[str, int] = {}
    for index, unit in enumerate(units):
        identifiers = ([unit.get("id")] if unit.get("id") else []) + unit.xpath(".//*[@id]/@id")
        for identifier in identifiers:
            positions.setdefault(identifier, index)
    page_positions = {page: positions[f"page-{page:03d}"] for page in range(1, len(reader.pages) + 1)}

    outline = core_outline(reader)
    kept_outline = outline[8:]
    intro_label = "为了上上善道"
    intro_wanted = normalized_heading(intro_label)
    intro_candidates: list[tuple[float, int, etree._Element]] = []
    intro_start = page_positions[3]
    intro_end = page_positions.get(6, len(units))
    for unit_index in range(intro_start, intro_end):
        unit = units[unit_index]
        tag = unit.tag.lower() if isinstance(unit.tag, str) else ""
        if tag not in {"h2", "h3", "h4", "p"}:
            continue
        candidate = normalized_heading(element_text(unit))
        score = SequenceMatcher(None, intro_wanted, candidate).ratio()
        if candidate == intro_wanted:
            score = 1.0
        if tag.startswith("h"):
            score += 0.04
        intro_candidates.append((score, unit_index, unit))
    if not intro_candidates:
        raise RuntimeError("Tau introduction heading is missing")
    intro_score, intro_position, intro_unit = max(intro_candidates, key=lambda item: item[0])
    if intro_score < 0.9:
        raise RuntimeError(f"Tau introduction heading match is too weak: {element_text(intro_unit)}")
    intro_anchor = f"{topic_id}-tau0001"
    intro_unit.set("id", intro_anchor)
    entries: list[dict[str, object]] = [
        {"level": 1, "label": intro_label, "anchor": intro_anchor, "position": intro_position, "score": intro_score}
    ]
    previous_position = intro_position
    for entry_index, (level, label, expected_page) in enumerate(kept_outline, 2):
        wanted = normalized_heading(label)
        low_page = max(1, expected_page - 1)
        high_page = min(len(reader.pages), expected_page + 1)
        start = page_positions[low_page]
        end = page_positions.get(high_page + 1, len(units))
        candidates: list[tuple[float, int, etree._Element]] = []
        for unit_index in range(max(start, previous_position + 1), end):
            unit = units[unit_index]
            tag = unit.tag.lower() if isinstance(unit.tag, str) else ""
            if tag not in {"h2", "h3", "h4", "p"}:
                continue
            text = re.sub(r"\s+", " ", element_text(unit)).strip()
            candidate = normalized_heading(text)
            if not candidate or len(candidate) > max(60, len(wanted) * 4):
                continue
            score = SequenceMatcher(None, wanted, candidate).ratio()
            if wanted == candidate:
                score = 1.0
            elif wanted in candidate or candidate in wanted:
                score = max(score, 0.88)
            if tag.startswith("h"):
                score += 0.04
            candidates.append((score, unit_index, unit))
        if not candidates:
            raise RuntimeError(f"Tau heading has no candidate: {label} (page {expected_page})")
        score, unit_index, unit = max(candidates, key=lambda item: (item[0], -abs(item[1] - page_positions[expected_page])))
        if score < 0.62:
            raise RuntimeError(f"Tau heading match is too weak: {label} -> {element_text(unit)} ({score:.2f})")
        anchor = f"{topic_id}-tau{entry_index:04d}"
        unit.set("id", anchor)
        entries.append(
            {"level": level, "label": label, "anchor": anchor, "position": unit_index, "score": score}
        )
        previous_position = unit_index

    entry_positions = [int(entry["position"]) for entry in entries]
    if entry_positions != sorted(entry_positions) or len(set(entry_positions)) != len(entry_positions):
        raise RuntimeError("Tau semantic headings are duplicated or out of order")
    navigation = [
        (
            int(entry["level"]),
            str(entry["label"]),
            f'{semantic_document_url(str(entry["anchor"]))}#{entry["anchor"]}',
        )
        for entry in entries
    ]

    page_targets: dict[str, str] = {}
    owner = navigation[0][2]
    navigation_by_position = {int(entry["position"]): navigation[index][2] for index, entry in enumerate(entries)}
    for unit_index, unit in enumerate(units):
        if unit_index in navigation_by_position:
            owner = navigation_by_position[unit_index]
        page_id = unit.get("id", "")
        if re.fullmatch(r"page-\d{3}", page_id):
            page_targets[page_id] = owner
    for unit in units:
        for link in unit.xpath('.//a[starts-with(@href, "#page-")]'):
            target = page_targets.get(link.get("href", "")[1:])
            if target:
                link.set("href", target)

    landing_body = semantic_toc_html(navigation, f"{book_title}目录")
    (BUILD / f"{topic_id}.html").write_text(
        wrap(book_title, TAU_NAME, landing_body, "semantic-topic"),
        encoding="gbk",
        errors="xmlcharrefreplace",
    )

    parent_indexes: list[int | None] = []
    for index, (level, _, _) in enumerate(navigation):
        parent = None
        for candidate in range(index - 1, -1, -1):
            if navigation[candidate][0] < level:
                parent = candidate
                break
        parent_indexes.append(parent)
    child_indexes: dict[int, list[int]] = {index: [] for index in range(len(navigation))}
    for index, parent in enumerate(parent_indexes):
        if parent is not None and navigation[index][0] == navigation[parent][0] + 1:
            child_indexes[parent].append(index)

    parts: list[dict[str, str]] = [
        {"title": f"{book_title} · 目录", "url": f"{topic_id}.html"}
    ]
    empty_topics = 0
    for index, entry in enumerate(entries):
        label = str(entry["label"])
        anchor = str(entry["anchor"])
        start = int(entry["position"])
        end = int(entries[index + 1]["position"]) if index + 1 < len(entries) else len(units)
        section_units = prepare_semantic_units(units[start:end], anchor, label)
        parent = parent_indexes[index]
        parent_url = navigation[parent][2] if parent is not None else f"{topic_id}.html"
        parent_label = navigation[parent][1] if parent is not None else f"{book_title}目录"
        previous_link = ""
        next_link = ""
        if index > 0:
            previous_link = f'<a class="prev" href="{html.escape(navigation[index - 1][2])}">← {html.escape(navigation[index - 1][1])}</a>'
        if index + 1 < len(navigation):
            next_link = f'<a class="next" href="{html.escape(navigation[index + 1][2])}">{html.escape(navigation[index + 1][1])} →</a>'
        pager = (
            '<nav class="semantic-nav">'
            f'{previous_link}<a class="up" href="{html.escape(parent_url)}">↑ {html.escape(parent_label)}</a>'
            f"{next_link}</nav>"
        )
        child_toc = ""
        if child_indexes[index]:
            child_toc = semantic_toc_html([navigation[child] for child in child_indexes[index]], "本节下级主题")
        meaningful = sum(len(re.sub(r"\s+", "", element_text(unit))) for unit in section_units)
        empty_note = ""
        if meaningful <= len(label) + 2 and not any(unit.xpath(".//img|.//table") for unit in section_units):
            empty_topics += 1
            empty_note = '<p class="empty-topic">本节点没有独立导言，请从下级主题继续阅读。</p>'
        body = (
            f'<div class="semantic-path"><a href="{topic_id}.html">{html.escape(book_title)}</a></div>'
            + pager + child_toc + empty_note
            + ('<p class="source-note">汉化：即食自走型拉拉肥　排版：Hill（ljtc0922）</p>' if index == 0 else '')
            + serialize_core_units(section_units) + pager
        )
        url = semantic_document_url(anchor)
        (BUILD / url).write_text(
            wrap(label, TAU_NAME, body, "semantic-topic"),
            encoding="gbk",
            errors="xmlcharrefreplace",
        )
        parts.append({"title": f"{book_title} · {label}", "url": f"{url}#{anchor}"})

    return {
        "title": book_title, "url": f"{topic_id}.html", "type": "PDF OCR + DeepSeek",
        "parts": parts, "navigation": navigation, "pages": len(reader.pages),
        "images": len(list(target_media.glob("*"))) if target_media.is_dir() else 0,
        "semantic_pages": len(entries), "empty_topics": empty_topics,
        "outline_matches_min_score": min(float(entry["score"]) for entry in entries),
        "layout_cleanup": layout_cleanup, "table_cleanup": table_cleanup,
    }


def build_document(source: Path, topic_id: str, pandoc: Path) -> dict:
    actual = source
    converted = False
    if source.suffix.lower() == ".doc":
        actual = CACHE / "converted" / f"{hashlib.sha256(source.read_bytes()).hexdigest()}.docx"
        actual.parent.mkdir(parents=True, exist_ok=True)
        if not actual.exists():
            convert_legacy_doc(source, actual)
        converted = True
    return build_semantic_document(source, actual, topic_id, pandoc, converted)


def group_scattered_topics(source_topics: list[dict]) -> list[dict]:
    scattered = [topic for topic in source_topics if int(topic.get("semantic_pages") or 0) == 0]
    if len(scattered) != 13:
        raise RuntimeError(f"Expected 13 unsectioned topics, found {len(scattered)}")
    grouped_ids = {id(topic) for topic in scattered}
    navigation = [(1, topic["title"], topic["url"]) for topic in scattered]
    body = (
        '<p>本卷收录未识别到独立红色章节标题的短篇规则、职业与背景资料。</p>'
        + semantic_toc_html(navigation, SCATTERED_TITLE)
    )
    group_url = "scattered.html"
    (BUILD / group_url).write_text(
        wrap(SCATTERED_TITLE, "整合资料", body, "semantic-topic"),
        encoding="gbk",
        errors="xmlcharrefreplace",
    )
    group = {
        "title": SCATTERED_TITLE,
        "url": group_url,
        "type": "合集",
        "parts": [
            {"title": f"{SCATTERED_TITLE} · 目录", "url": group_url},
            *({"title": topic["title"], "url": topic["url"]} for topic in scattered),
        ],
        "navigation": navigation,
        "source_count": len(scattered),
        "grouped_titles": [topic["title"] for topic in scattered],
    }
    return [topic for topic in source_topics if id(topic) not in grouped_ids] + [group]


def write_search(topics: list[dict]) -> int:
    documents: list[dict[str, str]] = []
    for topic in topics:
        for document in topic.get("parts") or [{"title": topic["title"], "url": topic["url"]}]:
            file_url = document["url"].partition("#")[0]
            content = (BUILD / file_url).read_text(encoding="gbk")
            documents.append({"title": document["title"], "url": document["url"], "text": visible(content)})
    (BUILD / "search-data.js").write_text(
        "var SEARCH_DOCS=" + json.dumps(documents, ensure_ascii=False, separators=(",", ":")) + ";",
        encoding="gbk", errors="replace",
    )
    search_html = '''<!doctype html><html lang="zh-CN"><head><meta charset="gb2312"><meta http-equiv="X-UA-Compatible" content="IE=9"><title>全文搜索</title><link rel="stylesheet" href="style.css"></head><body><h1>全文搜索</h1><p>搜索全部文件正文、表格与标题。多个关键词需同时出现。</p><input id="q" class="searchbox" autofocus><button class="searchbtn" onclick="go()">搜索</button><div id="status"></div><div id="results"></div><script src="search-data.js"></script><script>
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function go(){var q=document.getElementById('q').value.replace(/^\s+|\s+$/g,'');var box=document.getElementById('results');if(!q){box.innerHTML='';return;}var terms=q.toLowerCase().split(/\s+/),hits=[];for(var i=0;i<SEARCH_DOCS.length;i++){var d=SEARCH_DOCS[i],hay=(d.title+' '+d.text).toLowerCase(),ok=true,pos=hay.length;for(var j=0;j<terms.length;j++){var p=hay.indexOf(terms[j]);if(p<0){ok=false;break;}if(p<pos)pos=p;}if(ok)hits.push({d:d,p:pos,score:(d.title.toLowerCase().indexOf(terms[0])>=0?100000:0)-pos});}hits.sort(function(a,b){return b.score-a.score;});document.getElementById('status').innerHTML='<p>找到 '+hits.length+' 个主题</p>';var out='';for(var k=0;k<hits.length;k++){var h=hits[k],start=Math.max(0,h.p-70),sn=h.d.text.substring(start,start+220);out+='<div class="result"><a href="'+h.d.url+'">'+esc(h.d.title)+'</a><div class="snippet">…'+esc(sn)+'…</div></div>';}box.innerHTML=out;}
document.getElementById('q').onkeydown=function(e){e=e||window.event;if(e.keyCode==13)go();};
</script></body></html>'''
    (BUILD / "search.html").write_text(search_html, encoding="gbk", errors="xmlcharrefreplace")
    return len(documents)


def append_nested_toc(
    output: list[str], children: list[tuple[int, str, str]]
) -> None:
    previous_depth = 1
    for index, (raw_depth, label, target) in enumerate(children):
        depth = max(1, min(3, raw_depth))
        if index == 0:
            depth = 1
        elif depth > previous_depth + 1:
            depth = previous_depth + 1
        if depth > previous_depth:
            output.extend("<UL>" for _ in range(depth - previous_depth))
        elif depth < previous_depth:
            output.extend("</UL>" for _ in range(previous_depth - depth))
        output.append(common.toc_item(label[:120], target))
        previous_depth = depth
    if children and previous_depth > 1:
        output.extend("</UL>" for _ in range(previous_depth - 1))


def write_navigation(topics: list[dict]) -> None:
    toc = ['<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML//EN"><HTML><BODY><UL>']
    keyword = ['<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML//EN"><HTML><BODY><UL>', common.toc_item("全文搜索", "search.html")]
    for topic in topics:
        toc.append(common.toc_item(topic["title"], topic["url"]))
        children: list[tuple[int, str, str]] = []
        if topic.get("navigation"):
            children = topic["navigation"]
        elif topic.get("outline"):
            for _, label, page in topic["outline"]:
                if topic["title"] == DISPLAY_TITLES[CORE_NAME]:
                    url = core_part_url("topic001", page)
                    children.append((1, label, f"{url}#page-{page:03d}"))
                else:
                    part = (page - 1) // 5
                    url = topic["url"] if part == 0 else f"topic002-part{part + 1:03d}.html"
                    children.append((1, label, f"{url}#page-{page:03d}"))
        else:
            children = [
                (1, label, f'{topic["url"]}#{anchor}')
                for _, label, anchor in topic.get("headings", [])
                if label
            ]
        if children:
            toc.append("<UL>")
            append_nested_toc(toc, children)
            toc.append("</UL>")
        keyword.append(common.toc_item(topic["title"], topic["url"]))
        keyword.extend(
            common.toc_item(label[:120], target)
            for _, label, target in children
        )
    toc.extend([common.toc_item("全文搜索（备用）", "search.html"), common.toc_item("首页", "index.html"), "</UL></BODY></HTML>"])
    keyword.append("</UL></BODY></HTML>")
    (BUILD / "roguetrader.hhc").write_text("\r\n".join(toc), encoding="gbk", errors="xmlcharrefreplace")
    (BUILD / "roguetrader.hhk").write_text("\r\n".join(keyword), encoding="gbk", errors="xmlcharrefreplace")


def compile_chm(topics: list[dict], search_count: int) -> dict:
    links = "".join(
        f'<li><a href="{topic["url"]}">{html.escape(topic["title"])}</a></li>'
        for topic in topics
    )
    home = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="gb2312"><title>行商浪人整合v2.0</title>'
        '<link rel="stylesheet" href="style.css"></head><body><h1>行商浪人整合v2.0</h1>'
        '<p class="source-note">本次更新重新识别设置了章节名及索引，修复了部分表格太丑问题和核心书的框，懒狗耶利米选择休息</p>'
        '<p><a href="search.html">进入全文搜索</a></p>'
        f'<p>共 {len(topics)} 个卷。左侧为卷目录，展开后可查看识别到的章节。</p>'
        f'<ol>{links}</ol></body></html>'
    )
    (BUILD / "index.html").write_text(home, encoding="gbk", errors="xmlcharrefreplace")
    write_navigation(topics)
    included = [
        str(path.relative_to(BUILD)).replace("/", "\\")
        for path in BUILD.rglob("*")
        if path.is_file() and path.suffix.lower() not in {".txt", ".json", ".hhp", ".chm", ".pdf"}
    ]
    project = '''[OPTIONS]
Compatibility=1.1 or later
Compiled file=roguetrader.chm
Contents file=roguetrader.hhc
Index file=roguetrader.hhk
Default Window=roguetrader_v1
Default topic=index.html
Display compile progress=No
Full-text search=Yes
Language=0x804 Chinese (Simplified)
Title=行商浪人整合v2.0

[WINDOWS]
roguetrader_v1="行商浪人整合v2.0","roguetrader.hhc","roguetrader.hhk","index.html","index.html",,,,,0x63520,,0x304e,[90,70,1280,850],0x0,,,,,,0

[FILES]
''' + "\n".join(included)
    project_path = BUILD / "roguetrader.hhp"
    project_path.write_text(project, encoding="gbk", errors="xmlcharrefreplace")
    compiler = Path(os.environ.get("TEMP", str(ROOT / "build"))) / "deathwatch-chm-tools" / "hhc" / "hhc.exe"
    if not compiler.is_file():
        raise FileNotFoundError(compiler)
    subprocess.run([str(compiler), str(project_path)], cwd=BUILD, check=False)
    built = BUILD / "roguetrader.chm"
    if not built.is_file() or built.stat().st_size < 10000:
        raise RuntimeError("CHM compilation failed")
    shutil.copy2(built, FINAL)
    chmls = Path(os.environ.get("TEMP", str(ROOT / "build"))) / "deathwatch-fpc" / "install" / "bin" / "i386-win32" / "chmls.exe"
    native_search = False
    if chmls.is_file():
        listing = run([str(chmls), "-n", "list", str(FINAL)], capture=True).stdout
        native_search = "/$FIftiMain" in listing
        if not native_search:
            raise RuntimeError("Native CHM full-text search index is missing")
    return {
        "source_count": sum(int(topic.get("source_count", 1)) for topic in topics),
        "top_level_volumes": len(topics), "search_documents": search_count,
        "native_search": native_search, "bytes": FINAL.stat().st_size,
        "sha256": hashlib.sha256(FINAL.read_bytes()).hexdigest().upper(),
        "output": str(FINAL),
    }


def main() -> None:
    sources = [path for path in SOURCE_DIR.iterdir() if path.is_file() and path.suffix.lower() in {".pdf", ".docx", ".doc"}]
    if len(sources) != 20:
        print(f"Warning: expected 20 supported files, found {len(sources)}", flush=True)
    source_names = {path.name for path in sources}
    if CORE_NAME not in source_names or TAU_NAME not in source_names:
        raise RuntimeError("Core or Tau source is missing")
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    (BUILD / "style.css").write_text(ROGUETRADER_CSS, encoding="gbk", errors="xmlcharrefreplace")
    pandoc = find_pandoc()
    ordered = sorted(
        sources,
        key=lambda path: (0 if path.name == CORE_NAME else 1 if path.name == TAU_NAME else 2, path.name.lower()),
    )
    source_topics: list[dict] = []
    for index, source in enumerate(ordered, 1):
        topic_id = f"topic{index:03d}"
        print(f"[{index}/{len(ordered)}] {source.name}", flush=True)
        if source.name == CORE_NAME:
            topic = build_core_docx(source, topic_id, pandoc)
        elif source.name == TAU_NAME:
            topic = copy_tau(topic_id)
        else:
            topic = build_document(source, topic_id, pandoc)
        source_topics.append(topic)
    topics = group_scattered_topics(source_topics)
    search_count = write_search(topics)
    manifest = compile_chm(topics, search_count)
    manifest.update({
        "core_semantic_pages": source_topics[0].get("semantic_pages"),
        "core_bookmarks": source_topics[0].get("bookmarks"), "core_sidebar_chapters": source_topics[0].get("chapters"),
        "core_sidebar_sections": source_topics[0].get("sections"),
        "core_sidebar_subsections": source_topics[0].get("subsections"),
        "core_empty_topics": source_topics[0].get("empty_topics"),
        "core_repaired_anchors": source_topics[0].get("repaired_anchors"),
        "core_layout_cleanup": source_topics[0].get("layout_cleanup"),
        "core_table_cleanup": source_topics[0].get("table_cleanup"),
        "tau_pages": source_topics[1].get("pages"),
        "tau_semantic_pages": source_topics[1].get("semantic_pages"),
        "tau_empty_topics": source_topics[1].get("empty_topics"),
        "tau_outline_matches_min_score": source_topics[1].get("outline_matches_min_score"),
        "docx_doc_count": sum(t["type"].startswith("DOC") for t in source_topics),
        "semantic_pages_total": sum(int(t.get("semantic_pages") or 0) for t in source_topics),
        "semantic_pages_by_source": {
            t["title"]: int(t.get("semantic_pages") or 0) for t in source_topics
        },
        "semantic_empty_topics_total": sum(int(t.get("empty_topics") or 0) for t in source_topics),
        "unresolved_source_links": sum(int(t.get("unresolved_links") or 0) for t in source_topics),
        "promoted_linked_headings": sum(int(t.get("promoted_headings") or 0) for t in source_topics),
        "armory_merged_headings": sum(
            int((t.get("armory_customization") or {}).get("merged_headings", 0))
            for t in source_topics
        ),
        "armory_removed_empty_sections": sum(
            int((t.get("armory_customization") or {}).get("removed_empty_sections", 0))
            for t in source_topics
        ),
        "empty_first_columns_removed": sum(
            int((t.get("table_cleanup") or {}).get("removed_columns", 0))
            + int((t.get("layout_cleanup") or {}).get("empty_first_columns_removed_after_split", 0))
            for t in source_topics
        ),
        "layout_tables_removed_total": sum(
            int((t.get("layout_cleanup") or {}).get("layout_tables_removed", 0)) for t in source_topics
        ),
        "data_tables_kept_total": sum(
            int((t.get("layout_cleanup") or {}).get("data_tables_kept", 0)) for t in source_topics
        ),
        "scattered_group_title": SCATTERED_TITLE,
        "scattered_group_children": 13,
        "images_in_topics": sum(
            len(re.findall(r"<img\b", path.read_text(encoding="gbk"), re.I))
            for path in BUILD.glob("*.html")
            if path.name not in {"index.html", "search.html"}
        ),
    })
    (BUILD / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
