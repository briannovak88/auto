"""Приближённое восстановление редактируемых таблиц и двух колонок без Qt."""
from statistics import median


def _inside(rect, outer):
    x, y = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    return outer[0] <= x <= outer[2] and outer[1] <= y <= outer[3]


def _groups(values, tolerance=3):
    groups = []
    for value in sorted(values):
        if groups and value - groups[-1][-1] <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(group) / len(group) for group in groups]


def _cell_text(lines, rect):
    ordered = sorted((line for line in lines if _inside(line["bbox"], rect)),
                     key=lambda line: (round(line["bbox"][1] / 4), line["bbox"][0]))
    rows = []
    for line in ordered:
        center = (line["bbox"][1] + line["bbox"][3]) / 2
        if rows and abs(center - rows[-1][0]) <= max(3,(line["bbox"][3]-line["bbox"][1])*.45):
            rows[-1][1].append(line)
        else:
            rows.append((center, [line]))
    return "\n".join(" ".join(line["text"] for line in sorted(row,key=lambda b:b["bbox"][0])) for _,row in rows)


def native_layout(page):
    """PyMuPDF даёт строки со шрифтами и ячейки таблиц цифрового PDF."""
    lines = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = line["spans"]
            text = "".join(span["text"] for span in spans)
            if text.strip():
                lines.append({"kind": "text", "bbox": list(line["bbox"]), "text": text,
                              "size": median(span["size"] for span in spans),
                              "bold": any(span["flags"] & 16 for span in spans)})
    tables = []
    for table in page.find_tables(strategy="lines_strict").tables:
        rectangles = list(dict.fromkeys(tuple(cell) for row in table.rows for cell in row.cells if cell))
        xs = _groups([cell[i] for cell in rectangles for i in (0, 2)], 1)
        ys = _groups([cell[i] for cell in rectangles for i in (1, 3)], 1)
        if len(xs) < 3 or len(ys) < 2:
            continue
        cells = []
        for rect in rectangles:
            r0, r1 = [min(range(len(ys)), key=lambda k: abs(ys[k] - rect[i])) for i in (1, 3)]
            c0, c1 = [min(range(len(xs)), key=lambda k: abs(xs[k] - rect[i])) for i in (0, 2)]
            cells.append({"row": r0, "column": c0, "rowspan": r1 - r0, "colspan": c1 - c0,
                          "text": _cell_text(lines, rect)})
        tables.append({"kind": "table", "bbox": list(table.bbox), "xs": xs, "ys": ys, "cells": cells})
    return _finish_layout(page.rect.width, page.rect.height, lines, tables)


def scan_layout(image, tokens, width, height):
    """Сетка скана выделяется морфологией; OCR-токены распределяются по ячейкам."""
    import cv2
    import numpy as np
    ih, iw = image.shape[:2]
    sx, sy = width / iw, height / ih
    lines = []
    for polygon, text, confidence in tokens:
        if not str(text).strip():
            continue
        xs, ys = zip(*polygon)
        rect = [min(xs) * sx, min(ys) * sy, max(xs) * sx, max(ys) * sy]
        lines.append({"kind": "text", "bbox": rect, "text": str(text),
                      "size": max(6, min(28, (rect[3] - rect[1]) * .8)), "bold": False})
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, max(20, iw // 35)), np.uint8))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((max(20, ih // 35), 1), np.uint8))
    contours, _ = cv2.findContours(horizontal | vertical, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    tables = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < iw * .15 or h < 25 or w * h < iw * ih * .005:
            continue
        xs = _groups([x + i for i in np.flatnonzero(np.count_nonzero(vertical[y:y+h, x:x+w], axis=0) > h * .25)])
        ys = _groups([y + i for i in np.flatnonzero(np.count_nonzero(horizontal[y:y+h, x:x+w], axis=1) > w * .25)])
        if not 3 <= len(xs) <= 40 or not 3 <= len(ys) <= 150:
            continue
        rows, cols = len(ys) - 1, len(xs) - 1
        parents = list(range(rows * cols))

        def find(index):
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def join(a, b):
            parents[find(a)] = find(b)

        for row in range(rows):
            for col in range(cols):
                if col + 1 < cols:
                    xx = round(xs[col + 1])
                    segment = vertical[round(ys[row])+3:round(ys[row+1])-2, max(0, xx-2):xx+3]
                    if segment.size and np.count_nonzero(np.any(segment, axis=1)) < segment.shape[0] * .25:
                        join(row * cols + col, row * cols + col + 1)
                if row + 1 < rows:
                    yy = round(ys[row + 1])
                    segment = horizontal[max(0, yy-2):yy+3, round(xs[col])+3:round(xs[col+1])-2]
                    if segment.size and np.count_nonzero(np.any(segment, axis=0)) < segment.shape[1] * .25:
                        join(row * cols + col, (row + 1) * cols + col)
        members = {}
        for index in range(rows * cols):
            members.setdefault(find(index), []).append(divmod(index, cols))
        cells = []
        for group in members.values():
            r0, r1 = min(r for r, c in group), max(r for r, c in group)
            c0, c1 = min(c for r, c in group), max(c for r, c in group)
            # Word не поддерживает непрямоугольные объединения ячеек.
            rectangles = [(r0, r1, c0, c1)] if len(group) == (r1-r0+1)*(c1-c0+1) else [(r,r,c,c) for r,c in group]
            for r0, r1, c0, c1 in rectangles:
                rect = [xs[c0]*sx, ys[r0]*sy, xs[c1+1]*sx, ys[r1+1]*sy]
                cells.append({"row": r0, "column": c0, "rowspan": r1-r0+1, "colspan": c1-c0+1,
                              "text": _cell_text(lines, rect)})
        tables.append({"kind": "table", "bbox": [xs[0]*sx,ys[0]*sy,xs[-1]*sx,ys[-1]*sy],
                       "xs": [v*sx for v in xs], "ys": [v*sy for v in ys], "cells": cells})
    return _finish_layout(width, height, lines, tables)


def _finish_layout(width, height, lines, tables):
    blocks = [line for line in lines if not any(_inside(line["bbox"], table["bbox"]) for table in tables)] + tables
    return {"width": width, "height": height, "blocks": sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))}


def column_cut(blocks, width):
    lines = [block for block in blocks if block["kind"] == "text"]
    candidates = []
    for percent in range(35, 66):
        cut = width * percent / 100
        left = [b for b in lines if b["bbox"][2] < cut - 6]
        right = [b for b in lines if b["bbox"][0] > cut + 6]
        crossing = len(lines) - len(left) - len(right)
        if min(len(left), len(right)) < 2 or crossing > max(1, len(lines) * .2):
            continue
        overlap = min(max(b["bbox"][3] for b in left), max(b["bbox"][3] for b in right)) - max(min(b["bbox"][1] for b in left), min(b["bbox"][1] for b in right))
        if overlap < 15:
            continue
        gap = min(b["bbox"][0] for b in right) - max(b["bbox"][2] for b in left)
        if gap >= 18:
            candidates.append((crossing, -min(len(left), len(right)), abs(percent-50), cut))
    return min(candidates)[-1] if candidates else None


def write_page(document, layout, marker, first_page=False):
    """Настоящие w:tbl и w:cols; каждая исходная страница — отдельная секция."""
    from docx.enum.section import WD_SECTION_START, WD_ORIENT
    from docx.enum.text import WD_BREAK
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt
    from ocr_engine import clean_xml_text
    width, height = layout["width"], layout["height"]
    section = document.sections[0] if first_page else document.add_section(WD_SECTION_START.NEW_PAGE)
    section.page_width, section.page_height = Pt(width), Pt(height)
    section.orientation = WD_ORIENT.LANDSCAPE if width > height else WD_ORIENT.PORTRAIT
    blocks = layout["blocks"]
    if not blocks:
        document.add_paragraph()
    left = max(18, min(54, min((b["bbox"][0] for b in blocks), default=36)))
    right = max(18, min(54, width-max((b["bbox"][2] for b in blocks), default=width-36)))
    section.left_margin, section.right_margin = Pt(left), Pt(right)
    section.top_margin = section.bottom_margin = Pt(36)
    section.header_distance = Pt(8)
    section.header.is_linked_to_previous = False
    header = section.header.paragraphs[0]
    header.text = clean_xml_text(marker)
    for run in header.runs:
        run.font.size = Pt(8)

    def set_columns(target, count, cut=None, gap=24):
        node = target._sectPr.find(qn("w:cols"))
        if node is None:
            node = OxmlElement("w:cols")
            target._sectPr.append(node)
        for child in list(node):
            node.remove(child)
        node.set(qn("w:num"), str(count))
        node.set(qn("w:space"), str(round(gap*20)))
        node.set(qn("w:equalWidth"), "1" if count == 1 else "0")
        if count == 2:
            for size, space in ((cut-left-gap/2, gap), (width-right-cut-gap/2, 0)):
                col = OxmlElement("w:col")
                col.set(qn("w:w"), str(round(max(20,size)*20)))
                col.set(qn("w:space"), str(round(space*20)))
                node.append(col)

    set_columns(section, 1)

    def render(items, available):
        previous = None
        for block in sorted(items, key=lambda b: (b["bbox"][1], b["bbox"][0])):
            if block["kind"] == "table":
                table = document.add_table(rows=len(block["ys"])-1, cols=len(block["xs"])-1)
                table.style = "Table Grid"
                table.autofit = False
                scale = min(1, available / max(1, block["xs"][-1]-block["xs"][0]))
                for index, column in enumerate(table.columns):
                    column.width = Pt((block["xs"][index+1]-block["xs"][index])*scale)
                    for cell in column.cells:
                        cell.width = column.width
                for index, row in enumerate(table.rows):
                    row.height = Pt(block["ys"][index+1]-block["ys"][index])
                for cell in block["cells"]:
                    r, c = cell["row"], cell["column"]
                    target = table.cell(r, c)
                    if cell["rowspan"] > 1 or cell["colspan"] > 1:
                        target = target.merge(table.cell(r+cell["rowspan"]-1,c+cell["colspan"]-1))
                    target.width = Pt((block["xs"][c+cell["colspan"]]-block["xs"][c])*scale)
                    target.text = clean_xml_text(cell["text"])
                    for paragraph in target.paragraphs:
                        paragraph.paragraph_format.space_after = Pt(0)
                        for run in paragraph.runs:
                            run.font.size = Pt(10)
            else:
                paragraph = document.add_paragraph()
                paragraph.paragraph_format.space_after = Pt(0)
                paragraph.paragraph_format.line_spacing = 1
                if previous is not None:
                    paragraph.paragraph_format.space_before = Pt(min(30, max(0,block["bbox"][1]-previous)))
                run = paragraph.add_run(clean_xml_text(block["text"]))
                run.font.size = Pt(max(6,min(36,block.get("size",11))))
                run.bold = bool(block.get("bold",False))
            previous = block["bbox"][3]

    cut = column_cut(blocks, width)
    if cut is None:
        render(blocks, width-left-right)
        return
    spanning = [b for b in blocks if b["bbox"][0] < cut < b["bbox"][2]]
    pending = [b for b in blocks if b not in spanning]

    def band(items):
        if not items:
            return
        lhs = [b for b in items if (b["bbox"][0]+b["bbox"][2])/2 < cut]
        rhs = [b for b in items if b not in lhs]
        if not lhs or not rhs:
            render(items, width-left-right)
            return
        two = document.add_section(WD_SECTION_START.CONTINUOUS)
        set_columns(two, 2, cut)
        render(lhs, cut-left-12)
        document.add_paragraph().add_run().add_break(WD_BREAK.COLUMN)
        render(rhs, width-right-cut-12)
        one = document.add_section(WD_SECTION_START.CONTINUOUS)
        set_columns(one, 1)

    for block in sorted(spanning, key=lambda b:b["bbox"][1]):
        before = [b for b in pending if (b["bbox"][1]+b["bbox"][3])/2 < block["bbox"][1]]
        band(before)
        pending = [b for b in pending if b not in before]
        render([block], width-left-right)
    band(pending)
