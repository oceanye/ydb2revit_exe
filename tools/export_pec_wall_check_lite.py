# coding: utf-8
"""Zero-dependency PEC wall check drawing (R12 ASCII DXF + SVG preview).

Mirrors the WInfo v4 placement rules of ``export_pec_wall_check_dxf.py``
without requiring ezdxf, for machines where third-party packages are not
installable.  Entity text is deliberately ASCII so every CAD codepage reads
it; the SVG twin carries the Chinese labels.

Rules implemented (handoff-python-PEC墙提取与Revit建模.md §6):

* I-wall end H ......... center = endpoint -/+ u * h/2, h along the axis;
* L-wall corner H ...... center = Main.WStart + u * (h - Secondary.B) / 2;
* Main web ............. between the inner edges of both end H outlines;
* stiffeners ........... count plates equally spaced over the clear web
                          (1 at mid, 2 at thirds), thickness along the axis,
                          width across the section;
* Secondary flange ..... center = WEnd - u * t/2, width = wall thickness;
* Secondary web ........ from the connected H web edge to the flange.
"""

import argparse
import importlib.util
import json
import math
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_CONVERTER = None

LAYERS = {
    # layer: (color, svg stroke)
    "PEC_WALL_OUTLINE": (7, "#555555"),
    "PEC_NODE_AXIS": (8, "#999999"),
    "PEC_SOURCE_NODE": (8, "#bbbbbb"),
    "PEC_H_COLUMN": (1, "#c0392b"),
    "PEC_MAIN_WEB": (2, "#b8860b"),
    "PEC_STIFFENER": (5, "#1f4fa3"),
    "PEC_SECONDARY_WEB": (4, "#0e8a72"),
    "PEC_SECONDARY_FLANGE": (6, "#8e44ad"),
    "PEC_TEXT": (7, "#111111"),
    "PEC_DIM": (8, "#777777"),
}


# ---------------------------------------------------------------- geometry
def _add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def _subtract(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _scale(v, factor):
    return (v[0] * factor, v[1] * factor)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _axis(start, end):
    vector = _subtract(end, start)
    length = math.hypot(*vector)
    if length <= 0:
        raise ValueError("zero-length wall leg")
    direction = (vector[0] / length, vector[1] / length)
    normal = (-direction[1], direction[0])
    return direction, normal, length


def _axis_rectangle(start, end, width):
    direction, normal, _ = _axis(start, end)
    half = _scale(normal, width / 2.0)
    return [
        _add(start, half), _add(end, half),
        _subtract(end, half), _subtract(start, half),
    ]


def _centered_rectangle(center, direction, normal, length, width):
    a = _scale(direction, length / 2.0)
    b = _scale(normal, width / 2.0)
    return [
        _add(_add(center, a), b), _add(_subtract(center, a), b),
        _subtract(_subtract(center, a), b), _subtract(_add(center, a), b),
    ]


def _h_outline_points(h, b, tw, tf):
    """Classic 12-point H outline, u along the wall axis, v across it."""
    return [
        (-h / 2, -b / 2), (h / 2, -b / 2), (h / 2, -b / 2 + tf),
        (tw / 2, -b / 2 + tf), (tw / 2, b / 2 - tf), (h / 2, b / 2 - tf),
        (h / 2, b / 2), (-h / 2, b / 2), (-h / 2, b / 2 - tf),
        (-tw / 2, b / 2 - tf), (-tw / 2, -b / 2 + tf), (-h / 2, -b / 2 + tf),
    ]


def _h_web_points(h, b, tw, tf):
    return [(tw / 2, b / 2 - tf), (tw / 2, -(b / 2 - tf)),
            (-tw / 2, -(b / 2 - tf)), (-tw / 2, b / 2 - tf)]


def _place(points, center, direction, normal):
    return [_add(center, _add(_scale(direction, u), _scale(normal, v)))
            for u, v in points]


def _parse_h_section(text):
    # 截面文本契约的权威实现是 CreateNewExtern/SectionTextParser.cs
    # （E:\revit-external-tool2.git，只读参考）；解析统一复用转换器内的
    # 契约实现，避免本工具与 C# 端两套规则漂移。
    if _CONVERTER is None:
        return None
    parsed = _CONVERTER.parse_h_section_text(text)
    if parsed is None:
        return None
    height, width, web, flange, _ = parsed
    return {"h": height, "b": width, "tw": web, "tf": flange}


def _format_number(value):
    number = float(value)
    return str(int(number)) if number.is_integer() else format(number, ".4g")


# ------------------------------------------------------------------ model
def _load_converter():
    global _CONVERTER
    repo_root = str(ROOT)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    spec = importlib.util.spec_from_file_location("ydb_converter", ROOT / "ydb转换.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _CONVERTER = module
    return module


def _rows(connection, sql, parameters=()):
    connection.row_factory = sqlite3.Row
    return [dict(row) for row in connection.execute(sql, parameters)]


def _load_handoff(database_path):
    connection = sqlite3.connect(str(database_path))
    try:
        walls = _rows(
            connection,
            "SELECT WGroupID,WLegID,WLegRole,WShape,WSection,"
            "WStartX,WStartY,WEndX,WEndY,WInfo FROM tbl4 WHERE WInfo IS NOT NULL",
        )
        for wall in walls:
            wall["info"] = json.loads(wall["WInfo"])
        columns = {
            row["ID"]: row
            for row in _rows(connection, "SELECT ID,CSection FROM tbl2")
        }
        return walls, columns
    finally:
        connection.close()


# --------------------------------------------------------------- drawing
class Drawing:
    """Collects primitives, then serialises R12 DXF and SVG twins."""

    def __init__(self):
        self.lines = []      # (layer, p1, p2)
        self.circles = []    # (layer, center, radius)
        self.texts = []      # (layer, position, height, text, rotation_deg)

    def line(self, layer, p1, p2):
        self.lines.append((layer, p1, p2))

    def closed_polyline(self, layer, points):
        for index, point in enumerate(points):
            self.line(layer, point, points[(index + 1) % len(points)])

    def circle(self, layer, center, radius):
        self.circles.append((layer, center, radius))

    def text(self, layer, position, height, text, rotation_deg=0.0):
        self.texts.append((layer, position, height, text, rotation_deg))

    def bounds(self):
        xs, ys = [], []
        for _, p1, p2 in self.lines:
            xs += [p1[0], p2[0]]
            ys += [p1[1], p2[1]]
        for _, (x, y), _ in self.circles:
            xs.append(x)
            ys.append(y)
        for _, (x, y), _, _, _ in self.texts:
            xs.append(x)
            ys.append(y)
        if not xs:
            return 0.0, 0.0, 1000.0, 1000.0
        return min(xs), min(ys), max(xs), max(ys)

    # -- DXF (R12, ASCII) ------------------------------------------------
    def write_dxf(self, path):
        def pair(code, value):
            return "%d\n%s\n" % (code, value)

        parts = ["0\nSECTION\n2\nHEADER\n",
                 pair(9, "$ACADVER"), pair(1, "AC1009"),
                 "0\nENDSEC\n",
                 "0\nSECTION\n2\nTABLES\n",
                 "0\nTABLE\n2\nLAYER\n", pair(70, len(LAYERS))]
        for name, (color, _) in LAYERS.items():
            parts += ["0\nLAYER\n", pair(2, name), pair(70, 0),
                      pair(62, color), pair(6, "CONTINUOUS")]
        parts += ["0\nENDTAB\n", "0\nENDSEC\n", "0\nSECTION\n2\nENTITIES\n"]
        for layer, p1, p2 in self.lines:
            parts += ["0\nLINE\n", pair(8, layer),
                      pair(10, "%.6f" % p1[0]), pair(20, "%.6f" % p1[1]),
                      pair(30, "0.0"),
                      pair(11, "%.6f" % p2[0]), pair(21, "%.6f" % p2[1]),
                      pair(31, "0.0")]
        for layer, center, radius in self.circles:
            parts += ["0\nCIRCLE\n", pair(8, layer),
                      pair(10, "%.6f" % center[0]), pair(20, "%.6f" % center[1]),
                      pair(30, "0.0"), pair(40, "%.6f" % radius)]
        for layer, position, height, text, rotation in self.texts:
            parts += ["0\nTEXT\n", pair(8, layer),
                      pair(10, "%.6f" % position[0]),
                      pair(20, "%.6f" % position[1]), pair(30, "0.0"),
                      pair(40, "%.6f" % height), pair(1, text),
                      pair(50, "%.4f" % rotation)]
        parts += ["0\nENDSEC\n", "0\nEOF\n"]
        Path(path).write_text("".join(parts), encoding="ascii", errors="replace")

    # -- SVG preview ------------------------------------------------------
    def write_svg(self, path, title):
        min_x, min_y, max_x, max_y = self.bounds()
        pad = 600.0
        width = max_x - min_x + 2 * pad
        height = max_y - min_y + 2 * pad

        def tx(p):
            return (p[0] - min_x + pad, max_y - p[1] + pad)

        out = ['<?xml version="1.0" encoding="utf-8"?>',
               '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
               'viewBox="0 0 %d %d" font-family="system-ui, sans-serif">' % (
                   width, height, width, height),
               '<rect width="100%%" height="100%%" fill="#fcfcfb"/>',
               '<text x="%d" y="40" font-size="34" fill="#102c29">%s</text>' % (
                   pad, title)]
        for layer, p1, p2 in self.lines:
            a, b = tx(p1), tx(p2)
            out.append('<line x1="%.3f" y1="%.3f" x2="%.3f" y2="%.3f" '
                       'stroke="%s" stroke-width="6"/>' % (
                           a[0], a[1], b[0], b[1], LAYERS[layer][1]))
        for layer, center, radius in self.circles:
            c = tx(center)
            out.append('<circle cx="%.3f" cy="%.3f" r="%.3f" fill="none" '
                       'stroke="%s" stroke-width="5"/>' % (
                           c[0], c[1], radius, LAYERS[layer][1]))
        for layer, position, height, text, rotation in self.texts:
            p = tx(position)
            out.append('<text x="%.3f" y="%.3f" font-size="%d" fill="%s" '
                       'transform="rotate(%.3f %.3f %.3f)">%s</text>' % (
                           p[0], p[1], height * 1.1, LAYERS[layer][1],
                           -rotation, p[0], p[1], text))
        legend = "".join(
            '<line x1="0" y1="%d" x2="46" y2="%d" stroke="%s" stroke-width="8"/>'
            '<text x="58" y="%d" font-size="26" fill="#333">%s</text>' % (
                76 + i * 40, 76 + i * 40, color, 76 + i * 40, name)
            for i, (name, (color, _)) in enumerate(LAYERS.items()))
        out.append('<g transform="translate(%d,90)">%s</g>' % (pad, legend))
        out.append("</svg>")
        Path(path).write_text("\n".join(out), encoding="utf-8")


# ------------------------------------------------------------------ main
def _derive_h_geometries(walls, columns_by_id):
    secondary_widths = {
        wall["WGroupID"]: float(wall["WSection"])
        for wall in walls if wall["WLegRole"] == "SECONDARY"
    }
    geometries = {}
    for wall in walls:
        if wall["WLegRole"] != "MAIN":
            continue
        start = (float(wall["WStartX"]), float(wall["WStartY"]))
        end = (float(wall["WEndX"]), float(wall["WEndY"]))
        direction, _, _ = _axis(start, end)
        references = wall["info"]["tbl2_column_refs"]
        for endpoint_name, endpoint in (("start", start), ("end", end)):
            column = columns_by_id.get(references.get(endpoint_name))
            dimensions = _parse_h_section(column["CSection"]) if column else None
            if dimensions is None:
                continue
            if endpoint_name == "start" and wall["WShape"] == "L" \
                    and wall["WGroupID"] in secondary_widths:
                offset = (dimensions["h"] - secondary_widths[wall["WGroupID"]]) / 2.0
                rule = "(H-SEC_B)/2"
            elif endpoint_name == "start":
                offset = dimensions["h"] / 2.0
                rule = "H/2"
            else:
                offset = -dimensions["h"] / 2.0
                rule = "-H/2"
            center = _add(endpoint, _scale(direction, offset))
            geometries[references[endpoint_name]] = {
                "dimensions": dimensions,
                "center": center,
                "outline": _place(_h_outline_points(**dimensions), center,
                                  direction, _axis(start, end)[1]),
                "web": _place(_h_web_points(**dimensions), center,
                              direction, _axis(start, end)[1]),
                "offset": offset,
                "rule": rule,
            }
    return geometries


def build_drawing(walls, columns_by_id):
    drawing = Drawing()
    h_geometries = _derive_h_geometries(walls, columns_by_id)
    legend = []

    for column_id, geometry in sorted(h_geometries.items()):
        drawing.closed_polyline("PEC_H_COLUMN", geometry["outline"])

    for wall in walls:
        start = (float(wall["WStartX"]), float(wall["WStartY"]))
        end = (float(wall["WEndX"]), float(wall["WEndY"]))
        direction, normal, node_length = _axis(start, end)
        info = wall["info"]
        steel = info["steel_configuration"]
        concrete = float(wall["WSection"])

        drawing.closed_polyline(
            "PEC_WALL_OUTLINE", _axis_rectangle(start, end, concrete))
        drawing.line("PEC_NODE_AXIS", start, end)
        for point in (start, end):
            drawing.circle("PEC_SOURCE_NODE", point, 35.0)

        label = "%s %s %s | NODE-L=%s B=%s" % (
            wall["WLegID"], wall["WLegRole"], steel["cross_section_form"],
            _format_number(node_length), _format_number(concrete))
        drawing.text(
            "PEC_TEXT",
            _add(_add(start, _scale(direction, node_length / 2.0)),
                 _scale(normal, concrete / 2.0 + 160.0)),
            80.0, label, math.degrees(math.atan2(direction[1], direction[0])))

        if wall["WLegRole"] == "MAIN":
            references = info["tbl2_column_refs"]
            start_h = h_geometries.get(references.get("start"))
            end_h = h_geometries.get(references.get("end"))
            stiffener = steel["internal_stiffener"]
            legend.append("%s MAIN B=%s web=%s partitions=%s stiffener=%s x %sx%s"
                          % (wall["WLegID"], _format_number(concrete),
                             _format_number(steel["web_thickness_mm"]),
                             steel["partition_count"], stiffener["count"],
                             _format_number(stiffener["width_mm"]),
                             _format_number(stiffener["thickness_mm"])))
            if start_h is None or end_h is None:
                continue
            start_inner = max(_dot(_subtract(p, start), direction)
                              for p in start_h["outline"])
            end_inner = min(_dot(_subtract(p, start), direction)
                            for p in end_h["outline"])
            start_inner = max(0.0, min(node_length, start_inner))
            end_inner = max(0.0, min(node_length, end_inner))
            if end_inner <= start_inner:
                continue
            web_start = _add(start, _scale(direction, start_inner))
            web_end = _add(start, _scale(direction, end_inner))
            drawing.closed_polyline("PEC_MAIN_WEB", _axis_rectangle(
                web_start, web_end, float(steel["web_thickness_mm"])))
            count = int(stiffener["count"])
            for index in range(count):
                fraction = float(index + 1) / float(count + 1)
                center = _add(web_start, _scale(
                    _subtract(web_end, web_start), fraction))
                drawing.closed_polyline("PEC_STIFFENER", _centered_rectangle(
                    center, direction, normal,
                    float(stiffener["thickness_mm"]),
                    float(stiffener["width_mm"])))
                drawing.text(
                    "PEC_TEXT",
                    _add(center, _scale(normal,
                                        float(stiffener["width_mm"]) / 2 + 120)),
                    70.0, "STIF%d %sx%s" % (
                        index + 1, _format_number(stiffener["width_mm"]),
                        _format_number(stiffener["thickness_mm"])),
                    math.degrees(math.atan2(direction[1], direction[0])))
        else:
            flange = float(steel["flange_thickness_mm"])
            flange_center = _add(end, _scale(direction, -flange / 2.0))
            drawing.closed_polyline("PEC_SECONDARY_FLANGE", _centered_rectangle(
                flange_center, direction, normal, flange, concrete))
            connected = h_geometries.get(
                info["tbl2_column_refs"].get("connected_main"))
            web_from = 0.0
            if connected is not None:
                web_from = max(_dot(_subtract(p, start), direction)
                               for p in connected["web"])
            web_to = node_length - flange
            if web_to > web_from:
                drawing.closed_polyline("PEC_SECONDARY_WEB", _axis_rectangle(
                    _add(start, _scale(direction, web_from)),
                    _add(start, _scale(direction, web_to)),
                    float(steel["web_thickness_mm"])))
            legend.append("%s SECONDARY B=%s web=%s flange=%sx%s"
                          % (wall["WLegID"], _format_number(concrete),
                             _format_number(steel["web_thickness_mm"]),
                             _format_number(concrete),
                             _format_number(flange)))

    for column_id, geometry in sorted(h_geometries.items()):
        dimensions = geometry["dimensions"]
        legend.append("tbl2#%d H%sx%sx%sx%s @%s (%s=%s)" % (
            column_id, _format_number(dimensions["h"]),
            _format_number(dimensions["b"]), _format_number(dimensions["tw"]),
            _format_number(dimensions["tf"]),
            _format_number(abs(geometry["offset"])), geometry["rule"],
            _format_number(geometry["offset"])))

    min_x, min_y, max_x, _ = drawing.bounds()
    for index, line in enumerate(legend):
        drawing.text("PEC_DIM", (max_x + 900.0, min_y + index * 190.0),
                     100.0, line)
    return drawing


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Zero-dependency PEC wall check drawing (DXF + SVG)")
    parser.add_argument("source", help="input .ydb file")
    parser.add_argument("-o", "--output", help="output base path (no suffix)")
    args = parser.parse_args(argv)

    source = Path(args.source).expanduser().resolve()
    base = Path(args.output).expanduser().resolve() if args.output \
        else source.with_name(source.stem + "_PEC_check_lite")

    converter = _load_converter()
    pending = tempfile.mktemp(suffix=".db")
    try:
        converter.convert_ydb(str(source), pending)
        walls, columns = _load_handoff(pending)
    finally:
        Path(pending).unlink(missing_ok=True)

    drawing = build_drawing(walls, columns)
    dxf_path = base.with_suffix(".dxf")
    svg_path = base.with_suffix(".svg")
    drawing.write_dxf(dxf_path)
    drawing.write_svg(svg_path, "%s PEC wall check" % source.name)
    print("walls=%d h_columns=%d" % (len(walls), len(columns)))
    print("DXF: %s" % dxf_path)
    print("SVG: %s" % svg_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
