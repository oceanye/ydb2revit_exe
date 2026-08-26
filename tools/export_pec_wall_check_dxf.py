# coding: utf-8
"""Export a measurable PEC wall plan from a YDB file.

This is a diagnostic exporter, not a Revit modeling path.  It first invokes
``ydb转换.py`` so that the DXF is generated from the exact tbl2/tbl4 handoff
contract consumed by Revit.  All drawing coordinates and dimensions are mm.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

import ezdxf
from ezdxf import bbox, units


ROOT = Path(__file__).resolve().parents[1]
CONVERTER_PATH = ROOT / "ydb转换.py"

LAYERS = {
    "PEC_WALL_OUTLINE": {"color": 8, "lineweight": 35},
    "PEC_NODE_AXIS": {"color": 3, "linetype": "CENTER", "lineweight": 13},
    "PEC_SOURCE_NODE": {"color": 3, "lineweight": 25},
    "PEC_H_COLUMN": {"color": 1, "lineweight": 50},
    "PEC_H_DERIVED_OFFSET": {"color": 30, "linetype": "DASHED", "lineweight": 25},
    "PEC_MAIN_WEB": {"color": 2, "lineweight": 50},
    "PEC_STIFFENER": {"color": 5, "lineweight": 50},
    "PEC_SECONDARY_WEB": {"color": 4, "lineweight": 50},
    "PEC_SECONDARY_FLANGE": {"color": 6, "lineweight": 50},
    "PEC_DIM": {"color": 7, "lineweight": 18},
    "PEC_TEXT": {"color": 7, "lineweight": 18},
}

H_SECTION_RE = re.compile(
    r"^H(?P<h>[-+0-9.eE]+)x(?P<b>[-+0-9.eE]+)x"
    r"(?P<tw>[-+0-9.eE]+)x(?P<tf>[-+0-9.eE]+)(?:@PEC)?$",
    re.IGNORECASE,
)


def _load_converter():
    spec = importlib.util.spec_from_file_location("ydb_converter", CONVERTER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load converter: " + str(CONVERTER_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows(connection, sql, parameters=()):
    return [dict(row) for row in connection.execute(sql, parameters)]


def _load_handoff(database_path):
    connection = sqlite3.connect(str(database_path))
    connection.row_factory = sqlite3.Row
    try:
        walls = _rows(
            connection,
            "SELECT * FROM tbl4 WHERE WInfo IS NOT NULL ORDER BY ID",
        )
        columns = _rows(
            connection,
            "SELECT * FROM tbl2 WHERE CSection LIKE '%@PEC' ORDER BY ID",
        )
        floors = _rows(connection, "SELECT * FROM tbl3 ORDER BY rowid")
    finally:
        connection.close()
    for wall in walls:
        wall["info"] = json.loads(wall["WInfo"])
    return walls, columns, floors


def _xy(point):
    return float(point[0]), float(point[1])


def _add(first, second):
    return first[0] + second[0], first[1] + second[1]


def _subtract(first, second):
    return first[0] - second[0], first[1] - second[1]


def _scale(vector, factor):
    return vector[0] * factor, vector[1] * factor


def _dot(first, second):
    return first[0] * second[0] + first[1] * second[1]


def _axis(start, end):
    delta = _subtract(end, start)
    length = math.hypot(*delta)
    if length <= 1e-9:
        raise ValueError("Zero-length PEC wall leg")
    direction = _scale(delta, 1.0 / length)
    normal = -direction[1], direction[0]
    return direction, normal, length


def _axis_rectangle(start, end, width):
    _, normal, _ = _axis(start, end)
    half_width = float(width) / 2.0
    offset = _scale(normal, half_width)
    return [
        _add(start, offset),
        _add(end, offset),
        _subtract(end, offset),
        _subtract(start, offset),
    ]


def _centered_rectangle(center, direction, normal, length, width):
    along = _scale(direction, float(length) / 2.0)
    across = _scale(normal, float(width) / 2.0)
    return [
        _add(_subtract(center, along), across),
        _add(_add(center, along), across),
        _subtract(_add(center, along), across),
        _subtract(_subtract(center, along), across),
    ]


def _transform_local(points, center, rotation_deg):
    angle = math.radians(float(rotation_deg or 0.0))
    cosine = math.cos(angle)
    sine = math.sin(angle)
    result = []
    for x_value, y_value in points:
        result.append((
            center[0] + x_value * cosine - y_value * sine,
            center[1] + x_value * sine + y_value * cosine,
        ))
    return result


def _h_outline(center, height, width, web_thickness, flange_thickness, rotation_deg=0.0):
    half_height = float(height) / 2.0
    half_width = float(width) / 2.0
    half_web = float(web_thickness) / 2.0
    flange = float(flange_thickness)
    points = [
        (-half_width, -half_height),
        (half_width, -half_height),
        (half_width, -half_height + flange),
        (half_web, -half_height + flange),
        (half_web, half_height - flange),
        (half_width, half_height - flange),
        (half_width, half_height),
        (-half_width, half_height),
        (-half_width, half_height - flange),
        (-half_web, half_height - flange),
        (-half_web, -half_height + flange),
        (-half_width, -half_height + flange),
    ]
    return _transform_local(points, center, rotation_deg)


def _h_web_outline(geometry):
    dimensions = geometry["dimensions"]
    half_web = float(dimensions["tw"]) / 2.0
    clear_half_height = (
        float(dimensions["h"]) / 2.0
        - float(dimensions["tf"])
    )
    return _transform_local(
        [
            (-half_web, -clear_half_height),
            (half_web, -clear_half_height),
            (half_web, clear_half_height),
            (-half_web, clear_half_height),
        ],
        geometry["center"],
        geometry["rotation_deg"],
    )


def _parse_h_section(section_text):
    match = H_SECTION_RE.match(str(section_text or ""))
    if not match:
        return None
    return {name: float(value) for name, value in match.groupdict().items()}


def _format_number(value):
    if value is None or value == "":
        return ""
    number = float(value)
    if abs(number) < 5e-7:
        number = 0.0
    if number.is_integer():
        return str(int(number))
    return ("%.6f" % number).rstrip("0").rstrip(".")


def _add_closed_polyline(modelspace, points, layer):
    return modelspace.add_lwpolyline(
        [_xy(point) for point in points],
        close=True,
        dxfattribs={"layer": layer},
    )


def _add_text(modelspace, text, position, height=85.0, layer="PEC_TEXT"):
    entity = modelspace.add_text(
        str(text),
        dxfattribs={"height": float(height), "layer": layer},
    )
    entity.set_placement(_xy(position))
    return entity


def _add_aligned_dimension(modelspace, first, second, distance, text="<>"):
    override = {
        "dimtxt": 80.0,
        "dimasz": 55.0,
        "dimexo": 25.0,
        "dimexe": 45.0,
        # The EZDXF setup style defaults to DIMLFAC=100 for architectural
        # examples.  This diagnostic drawing is already in mm, so dimensions
        # must display the raw model-space measurement without another scale.
        "dimlfac": 1.0,
        "dimdec": 3,
        "dimzin": 8,
    }
    dimension = modelspace.add_aligned_dim(
        p1=_xy(first),
        p2=_xy(second),
        distance=float(distance),
        text=text,
        dimstyle="EZDXF",
        override=override,
        dxfattribs={"layer": "PEC_DIM"},
    )
    dimension.render()
    return dimension.dimension


def _derive_h_geometries(walls, columns_by_id):
    secondary_widths = {
        wall["WGroupID"]: float(wall["WSection"])
        for wall in walls
        if wall["WLegRole"] == "SECONDARY"
    }
    geometries = {}
    for wall in walls:
        if wall["WLegRole"] != "MAIN":
            continue
        start = float(wall["WStartX"]), float(wall["WStartY"])
        end = float(wall["WEndX"]), float(wall["WEndY"])
        direction, _, _ = _axis(start, end)
        rotation_deg = math.degrees(math.atan2(direction[1], direction[0])) - 90.0
        references = wall["info"]["tbl2_column_refs"]
        for endpoint_name, endpoint in (("start", start), ("end", end)):
            column_id = references.get(endpoint_name)
            column = columns_by_id.get(column_id)
            if column is None:
                continue
            dimensions = _parse_h_section(column["CSection"])
            if dimensions is None:
                continue
            if endpoint_name == "start":
                if wall["WShape"] == "L" and wall["WGroupID"] in secondary_widths:
                    offset = (
                        dimensions["h"] - secondary_widths[wall["WGroupID"]]
                    ) / 2.0
                    rule = "(H-SECONDARY_B)/2"
                else:
                    offset = dimensions["h"] / 2.0
                    rule = "H/2"
                center = _add(start, _scale(direction, offset))
            else:
                offset = dimensions["h"] / 2.0
                rule = "H/2"
                center = _add(end, _scale(direction, -offset))
            geometries[column_id] = {
                "tbl2_id": column_id,
                "column": column,
                "dimensions": dimensions,
                "node": endpoint,
                "center": center,
                "rotation_deg": rotation_deg,
                "offset_mm": offset,
                "offset_rule": rule,
                "outline": _h_outline(
                    center,
                    dimensions["h"],
                    dimensions["b"],
                    dimensions["tw"],
                    dimensions["tf"],
                    rotation_deg,
                ),
            }
    return geometries


def _wall_geometry(modelspace, wall, h_geometries):
    start = float(wall["WStartX"]), float(wall["WStartY"])
    end = float(wall["WEndX"]), float(wall["WEndY"])
    direction, normal, node_length = _axis(start, end)
    info = wall["info"]
    concrete_width = float(wall["WSection"])
    steel = info["steel_configuration"]

    _add_closed_polyline(
        modelspace,
        _axis_rectangle(start, end, concrete_width),
        "PEC_WALL_OUTLINE",
    )
    modelspace.add_line(start, end, dxfattribs={"layer": "PEC_NODE_AXIS"})
    for point in (start, end):
        modelspace.add_circle(point, radius=35.0, dxfattribs={"layer": "PEC_SOURCE_NODE"})

    dimension_side = 1.0 if wall["WLegRole"] == "SECONDARY" else -1.0
    _add_aligned_dimension(
        modelspace,
        start,
        end,
        dimension_side * (concrete_width / 2.0 + 420.0),
    )

    midpoint = _add(start, _scale(direction, node_length / 2.0))
    label_position = _add(midpoint, _scale(normal, concrete_width / 2.0 + 150.0))
    _add_text(
        modelspace,
        "%s %s %s | NODE-L=%s | B=%s"
        % (
            wall["WLegID"],
            wall["WLegRole"],
            steel["cross_section_form"],
            _format_number(node_length),
            _format_number(concrete_width),
        ),
        label_position,
        75.0,
    )

    if wall["WLegRole"] == "MAIN":
        references = info["tbl2_column_refs"]
        start_h = h_geometries.get(references.get("start"))
        end_h = h_geometries.get(references.get("end"))
        if start_h is None or end_h is None:
            return node_length
        start_inner = max(
            _dot(_subtract(point, start), direction)
            for point in start_h["outline"]
        )
        end_inner = min(
            _dot(_subtract(point, start), direction)
            for point in end_h["outline"]
        )
        start_inner = max(0.0, min(node_length, start_inner))
        end_inner = max(0.0, min(node_length, end_inner))
        if end_inner > start_inner:
            web_start = _add(start, _scale(direction, start_inner))
            web_end = _add(start, _scale(direction, end_inner))
            _add_closed_polyline(
                modelspace,
                _axis_rectangle(web_start, web_end, steel["web_thickness_mm"]),
                "PEC_MAIN_WEB",
            )
            stiffener = steel["internal_stiffener"]
            count = int(stiffener["count"])
            for index in range(count):
                fraction = float(index + 1) / float(count + 1)
                center = _add(
                    web_start,
                    _scale(_subtract(web_end, web_start), fraction),
                )
                _add_closed_polyline(
                    modelspace,
                    _centered_rectangle(
                        center,
                        direction,
                        normal,
                        stiffener["thickness_mm"],
                        stiffener["width_mm"],
                    ),
                    "PEC_STIFFENER",
                )
        return node_length

    flange_thickness = float(steel["flange_thickness_mm"])
    flange_center = _add(end, _scale(direction, -flange_thickness / 2.0))
    _add_closed_polyline(
        modelspace,
        _centered_rectangle(
            flange_center,
            direction,
            normal,
            flange_thickness,
            concrete_width,
        ),
        "PEC_SECONDARY_FLANGE",
    )

    web_start_distance = 0.0
    connected_h_id = info["tbl2_column_refs"].get("connected_main")
    connected_h = h_geometries.get(connected_h_id)
    if connected_h is not None:
        web_outline = _h_web_outline(connected_h)
        web_start_distance = max(
            _dot(_subtract(point, start), direction) for point in web_outline
        )
    web_end_distance = node_length - flange_thickness
    if web_end_distance > web_start_distance:
        web_start = _add(start, _scale(direction, web_start_distance))
        web_end = _add(start, _scale(direction, web_end_distance))
        _add_closed_polyline(
            modelspace,
            _axis_rectangle(web_start, web_end, steel["web_thickness_mm"]),
            "PEC_SECONDARY_WEB",
        )
    return node_length


def _draw_h_geometry(modelspace, geometry):
    dimensions = geometry["dimensions"]
    node = geometry["node"]
    center = geometry["center"]
    _add_closed_polyline(modelspace, geometry["outline"], "PEC_H_COLUMN")
    modelspace.add_circle(node, radius=50.0, dxfattribs={"layer": "PEC_SOURCE_NODE"})
    if math.dist(node, center) > 1e-9:
        modelspace.add_line(
            node,
            center,
            dxfattribs={"layer": "PEC_H_DERIVED_OFFSET"},
        )
        _add_aligned_dimension(modelspace, node, center, 115.0)
    _add_text(
        modelspace,
        "%s | tbl2.ID=%s | OFFSET=%s (%s) | Rot(calc)=%s"
        % (
            geometry["column"]["CSection"],
            geometry["tbl2_id"],
            _format_number(geometry["offset_mm"]),
            geometry["offset_rule"],
            _format_number(geometry["rotation_deg"]),
        ),
        (center[0] + dimensions["b"] / 2.0 + 80.0, center[1]),
        65.0,
    )
    return dimensions


def _add_parameter_legend(modelspace, walls, floors, h_geometries):
    all_x = [float(wall[key]) for wall in walls for key in ("WStartX", "WEndX")]
    all_y = [float(wall[key]) for wall in walls for key in ("WStartY", "WEndY")]
    left = min(all_x)
    top = max(all_y) + 1800.0
    _add_text(modelspace, "PEC WALL PARAMETER CHECK (UNIT: mm)", (left, top), 180.0)
    _add_text(
        modelspace,
        "Wall outline and NODE-L are reconstructed from YDB WStart/WEnd coordinates.",
        (left, top - 260.0),
        95.0,
    )
    _add_text(
        modelspace,
        "H center/rotation are derived from wall endpoints and section dimensions; tbl2 Ecc/Rotation are not read.",
        (left, top - 430.0),
        95.0,
    )
    floor_text = " | ".join(
        "%s=%s" % (floor["Floor"], _format_number(floor["LevelB"]))
        for floor in floors
    )
    _add_text(modelspace, "LEVELS: " + floor_text, (left, top - 600.0), 95.0)

    line_y = top - 850.0
    for wall in walls:
        start = float(wall["WStartX"]), float(wall["WStartY"])
        end = float(wall["WEndX"]), float(wall["WEndY"])
        _, _, node_length = _axis(start, end)
        info = wall["info"]
        steel = info["steel_configuration"]
        concrete_width = float(wall["WSection"])
        if wall["WLegRole"] == "MAIN":
            stiffener = steel["internal_stiffener"]
            details = (
                "web=%s | partitions=%s | stiffener=%sx%s count=%s | H-ref(start,end)=(%s,%s)"
                % (
                    _format_number(steel["web_thickness_mm"]),
                    steel["partition_count"],
                    _format_number(stiffener["width_mm"]),
                    _format_number(stiffener["thickness_mm"]),
                    stiffener["count"],
                    info["tbl2_column_refs"].get("start"),
                    info["tbl2_column_refs"].get("end"),
                )
            )
        else:
            connected_h_id = info["tbl2_column_refs"].get("connected_main")
            connected_h = h_geometries.get(connected_h_id)
            calculated_offset = None
            if connected_h is not None:
                calculated_offset = abs(
                    (connected_h["dimensions"]["h"] - concrete_width) / 2.0
                )
            details = (
                "web=%s | flange=%sx%s | Dis1(raw)=%s | H-ref=%s | H-offset(calc)=%s"
                % (
                    _format_number(steel["web_thickness_mm"]),
                    _format_number(concrete_width),
                    _format_number(steel["flange_thickness_mm"]),
                    _format_number(info["source_parameters"]["section"]["Dis1"]),
                    connected_h_id,
                    _format_number(calculated_offset),
                )
            )
        _add_text(
            modelspace,
            "%s %s/%s | NODE-L=%s | B=%s | %s"
            % (
                wall["WLegID"],
                wall["WLegRole"],
                steel["cross_section_form"],
                _format_number(node_length),
                _format_number(concrete_width),
                details,
            ),
            (left, line_y),
            82.0,
        )
        line_y -= 150.0


def export_check_dxf(source_path, output_path):
    source_path = Path(source_path).expanduser().resolve()
    output_path = Path(output_path).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError("YDB does not exist: " + str(source_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    converter = _load_converter()
    with tempfile.TemporaryDirectory(prefix="pec_dxf_") as temporary_directory:
        handoff_path = Path(temporary_directory) / "handoff.sqlite"
        converter.convert_ydb(source_path, handoff_path)
        walls, columns, floors = _load_handoff(handoff_path)

    if not walls:
        raise ValueError("No PEC walls were extracted from the YDB")

    document = ezdxf.new("R2010", setup=True)
    document.units = units.MM
    document.header["$MEASUREMENT"] = 1
    document.dimstyles.get("EZDXF").dxf.dimlfac = 1.0
    for layer_name, attributes in LAYERS.items():
        if layer_name in document.layers:
            layer = document.layers.get(layer_name)
            for key, value in attributes.items():
                setattr(layer.dxf, key, value)
        else:
            document.layers.add(layer_name, dxfattribs=attributes)

    modelspace = document.modelspace()
    columns_by_id = {int(column["ID"]): column for column in columns}
    h_geometries = _derive_h_geometries(walls, columns_by_id)
    node_lengths = {}
    for wall in walls:
        node_lengths[wall["WLegID"]] = _wall_geometry(
            modelspace, wall, h_geometries
        )
    h_sections = []
    for column_id in sorted(h_geometries):
        geometry = h_geometries[column_id]
        dimensions = _draw_h_geometry(modelspace, geometry)
        if dimensions is not None:
            h_sections.append(geometry["column"]["CSection"])
    _add_parameter_legend(modelspace, walls, floors, h_geometries)

    document.saveas(output_path)

    reloaded = ezdxf.readfile(output_path)
    auditor = reloaded.audit()
    if auditor.has_errors:
        raise RuntimeError("Generated DXF audit failed with %s errors" % len(auditor.errors))
    reloaded_modelspace = reloaded.modelspace()
    entity_counts = Counter(entity.dxftype() for entity in reloaded_modelspace)
    layer_counts = Counter(entity.dxf.layer for entity in reloaded_modelspace)
    extents = bbox.extents(reloaded_modelspace)
    return {
        "source": str(source_path),
        "output": str(output_path),
        "units": "mm",
        "wall_legs": len(walls),
        "pec_h_columns": len(h_geometries),
        "node_lengths_mm": {
            key: round(value, 6) for key, value in node_lengths.items()
        },
        "h_sections": h_sections,
        "entity_counts": dict(sorted(entity_counts.items())),
        "layer_counts": dict(sorted(layer_counts.items())),
        "extents": {
            "min": list(extents.extmin),
            "max": list(extents.extmax),
        },
        "audit": "ok",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate a measurable PEC wall parameter-check DXF from YDB"
    )
    parser.add_argument("source", help="source dtlmodel.ydb")
    parser.add_argument("-o", "--output", required=True, help="output .dxf path")
    args = parser.parse_args(argv)
    summary = export_check_dxf(args.source, args.output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
