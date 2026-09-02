# coding: utf-8
"""YJK ydb -> Revit handoff SQLite converter.

The first columns of tbl1/tbl2/tbl3/tbl4 are a compatibility contract with the
existing Revit add-in. PEC data is deliberately compact:

* PEC beam/column H sections use ``H{h}x{b}x{tw}x{tf}@PEC``.  Ordinary Kind-2
  H columns located at a PEC main-wall endpoint are PEC end columns and remain
  independent tbl2 members.
* Every straight wall leg is one tbl4 row.
* A logical L wall is represented by two rows sharing WGroupID and carrying
  ``-L1`` / ``-L2`` leg identifiers.
* Main walls have an I steel arrangement (two independently modeled end H
  columns, an internal web and optional stiffeners); secondary walls have a T
  arrangement whose tail connects to the main H column.
* Internal web/stiffener/flange/rebar/plate data is retained as JSON in WInfo;
  only the concrete wall outline and independent tbl2 end columns are modeled.
"""

import argparse
import json
import math
import sqlite3
import struct
from pathlib import Path

from foundation_handoff import convert_foundation_ydb, is_foundation_ydb
from handoff_atomic import UPPER_MODE, atomic_update_database


PEC_WALL_KINDS = {211, 212}
PEC_MAIN_WALL_KIND = 211
PEC_SECONDARY_WALL_KIND = 212
WINFO_VERSION = 4


def _decode_sqlite_text(raw):
    """Decode YDB text from either UTF-8 or common Chinese legacy encodings."""
    for encoding in ("utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


def _quote_identifier(name):
    return '"' + str(name).replace('"', '""') + '"'


def _table_exists(connection, table_name):
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None


def _table_columns(connection, table_name):
    if not _table_exists(connection, table_name):
        return []
    return [row[1] for row in connection.execute(
        "PRAGMA table_info(" + _quote_identifier(table_name) + ")"
    )]


def _ordered_rows(connection, table_name):
    if not _table_exists(connection, table_name):
        return []
    columns = _table_columns(connection, table_name)
    order_columns = [name for name in ("No_", "ID") if name in columns]
    sql = "SELECT * FROM " + _quote_identifier(table_name)
    if order_columns:
        sql += " ORDER BY " + ", ".join(_quote_identifier(name) for name in order_columns)
    return list(connection.execute(sql))


def _value(row, name, default=None):
    if row is None:
        return default
    try:
        if name in row.keys():
            value = row[name]
            return default if value is None else value
    except AttributeError:
        pass
    return default


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_number(value):
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isfinite(number) and number.is_integer():
        return str(int(number))
    return format(number, ".12g")


def _first_positive(*values):
    for value in values:
        number = _as_float(value, 0.0)
        if number > 0:
            return number
    return None


def _section_kind(section):
    return _as_int(_value(section, "Kind"), 0)


def _legacy_section_text(section):
    """Preserve the pre-existing ShapeVal@detail representation."""
    if section is None:
        return ""
    shape_value = str(_value(section, "ShapeVal", "") or "")
    detail_names = ("b", "h", "u", "t", "d", "f")
    present = [name for name in detail_names if name in section.keys()]
    if not present:
        return shape_value
    details = ",".join(_format_number(_value(section, name, 0)) for name in detail_names)
    return shape_value + "@" + details


# GB/T 11263-2017 热轧 H 型钢常用规格（腹板厚, 翼缘厚），按 (系列, H, B) 索引。
# 用于两段式名称（如 HN400X200）在 tblSubSectionSect 打包串中的厚度还原；
# 未收录的名称仍按显式拒绝处理。颛桥样例的 6 个截面全部命中此表。
STANDARD_HOT_ROLLED_H = {
    ("HW", 100, 100): (6, 8), ("HW", 125, 125): (6.5, 9),
    ("HW", 150, 150): (7, 10), ("HW", 175, 175): (7.5, 11),
    ("HW", 200, 200): (8, 12), ("HW", 250, 250): (9, 14),
    ("HW", 300, 300): (10, 15), ("HW", 350, 350): (12, 19),
    ("HW", 400, 400): (13, 21),
    ("HM", 148, 100): (6, 9), ("HM", 194, 150): (6, 9),
    ("HM", 244, 175): (7, 11), ("HM", 294, 200): (8, 12),
    ("HM", 340, 250): (9, 14), ("HM", 390, 300): (10, 16),
    ("HM", 440, 300): (11, 18), ("HM", 482, 300): (11, 15),
    ("HM", 488, 300): (11, 18), ("HM", 582, 300): (12, 17),
    ("HM", 588, 300): (12, 20), ("HM", 594, 302): (14, 23),
    ("HN", 198, 99): (4.5, 7), ("HN", 200, 100): (5.5, 8),
    ("HN", 250, 125): (6, 9), ("HN", 300, 150): (6.5, 9),
    ("HN", 350, 175): (7, 11), ("HN", 400, 150): (8, 13),
    ("HN", 400, 200): (8, 13), ("HN", 450, 150): (9, 14),
    ("HN", 450, 200): (9, 14), ("HN", 500, 200): (10, 16),
    ("HN", 600, 200): (11, 17), ("HN", 700, 300): (13, 24),
    ("HN", 800, 300): (14, 26), ("HN", 900, 300): (16, 28),
}


def _packed_hot_rolled_h_dimensions(subsection):
    """Decode a Kind-26 packed subsection ShapeVal into (h, b, tw, tf).

    Layout per "YJK Kind26热轧H型钢与Kind209_YDB解析说明": 42 CSV integers,
    [0]=26, [1..8]=selector(2B)+name(30B) blob, [9]=b Q16, [10]=h Q16,
    [14]=custom flag, [41]=section ID.  Only the standard catalogue selector
    (39) with a cross-validated name is accepted; everything else stays
    unexplained and the caller rejects the section explicitly.
    """
    if subsection is None:
        return None
    text = str(_value(subsection, "ShapeVal", "") or "")
    try:
        fields = [int(part) for part in text.strip(",").split(",")]
    except ValueError:
        return None
    if len(fields) < 42 or fields[0] != 26 or fields[14] != 0:
        return None
    blob = struct.pack("<8I", *fields[1:9])
    if blob[0] | (blob[1] << 8) != 39:
        return None
    name = blob[2:].split(b"\x00")[0]
    try:
        name = name.decode("ascii").strip().upper()
    except UnicodeDecodeError:
        return None
    height = fields[10] / 65536.0
    width = fields[9] / 65536.0
    if fields[41] != _as_int(_value(subsection, "ID"), 0):
        return None
    parts = name.split("X")
    if len(parts) == 4 and parts[0].startswith("H") and not parts[0][1:3].isdigit():
        # 四段式自定义名称 "H400X200X8X13"：厚度直接在名称里。
        try:
            if abs(float(parts[0][1:]) - height) > 0.5:
                return None
            if abs(float(parts[1]) - width) > 0.5:
                return None
            return height, width, float(parts[2]), float(parts[3])
        except ValueError:
            return None
    if len(parts) == 2 and parts[0][:2] in ("HW", "HM", "HN"):
        # 两段式标准热轧名称 "HN400X200"：查内置国标规格表取厚度。
        try:
            named_height = float(parts[0][2:])
            named_width = float(parts[1])
        except ValueError:
            return None
        if abs(named_height - height) > 0.5 or abs(named_width - width) > 0.5:
            return None
        thickness = STANDARD_HOT_ROLLED_H.get(
            (parts[0][:2], int(round(named_height)), int(round(named_width)))
        )
        if thickness is None:
            return None
        return height, width, thickness[0], thickness[1]
    return None


def _h_dimensions(section, subsection=None):
    """Return conventional (height, flange width, web, flange) dimensions."""
    if section is None:
        return None
    kind = _section_kind(section)
    if kind == 209:
        # YJK PEC beam/column: u=tw, t=H, d=W, f=tf.  The same values may
        # also be repeated in tblSubSectionSect.
        height = _first_positive(_value(section, "t"), _value(subsection, "t"), _value(subsection, "h"))
        width = _first_positive(_value(section, "d"), _value(subsection, "d"), _value(subsection, "b"))
        web = _first_positive(_value(section, "u"), _value(subsection, "u"))
        flange = _first_positive(_value(section, "f"), _value(subsection, "f"))
    elif kind == 2:
        # Existing symmetric H/I section: b=tw, h=H, u/d=W, t/f=tf.
        height = _first_positive(_value(section, "h"))
        width = _first_positive(_value(section, "u"), _value(section, "d"))
        web = _first_positive(_value(section, "b"))
        flange = _first_positive(_value(section, "t"), _value(section, "f"))
    else:
        return None
    if None in (height, width, web, flange):
        # 主表与子表数值列都不全时，尝试子表 ShapeVal 里的 Kind-26 打包定义。
        if kind == 209:
            return _packed_hot_rolled_h_dimensions(subsection)
        return None
    return height, width, web, flange


def _h_section_name(section, subsection=None, pec=False):
    dimensions = _h_dimensions(section, subsection)
    if dimensions is None:
        raw = str(_value(section, "ShapeVal", "") or "").rstrip(",")
        base = raw or "H"
    else:
        base = "H" + "x".join(_format_number(value) for value in dimensions)
    if pec and not base.upper().endswith("@PEC"):
        base += "@PEC"
    return base


def _group_by(rows, column):
    grouped = {}
    for row in rows:
        grouped.setdefault(_value(row, column), []).append(row)
    return grouped


def _row_map(rows, column="ID"):
    return {_value(row, column): row for row in rows}


def _connection_values(properties, member_id, property_name):
    for row in properties:
        if _value(row, "ID") != member_id or _value(row, "Name", "") != property_name:
            continue
        parts = str(_value(row, "ShapeVal", "") or "").split(",")
        values = []
        for index in (0, 1):
            value = parts[index].strip() if index < len(parts) else "0"
            values.append(0 if value == "3.00" else _as_float(value, 0.0))
        return tuple(values)
    return 0, 0


def _meaningful_parameters(row, names):
    return {name: _value(row, name) for name in names}


def _json_safe(value):
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _vector_from_corner(record, corner_joint_id):
    if record["jt1_id"] == corner_joint_id:
        start = record["joint1"]
        end = record["joint2"]
    else:
        start = record["joint2"]
        end = record["joint1"]
    return (
        _as_float(_value(end, "X")) - _as_float(_value(start, "X")),
        _as_float(_value(end, "Y")) - _as_float(_value(start, "Y")),
    )


def _shared_joint(first, second):
    shared = {first["jt1_id"], first["jt2_id"]}.intersection(
        {second["jt1_id"], second["jt2_id"]}
    )
    return next(iter(shared)) if len(shared) == 1 else None


def _perpendicular_score(first, second, corner_joint_id):
    vector1 = _vector_from_corner(first, corner_joint_id)
    vector2 = _vector_from_corner(second, corner_joint_id)
    norm1 = math.hypot(*vector1)
    norm2 = math.hypot(*vector2)
    if norm1 == 0 or norm2 == 0:
        return None
    return abs(vector1[0] * vector2[0] + vector1[1] * vector2[1]) / (norm1 * norm2)


def _orient_from_corner(record, corner_joint_id):
    """For L walls, make each output line point from the corner outwards."""
    if record["jt1_id"] == corner_joint_id:
        record["output_reversed"] = False
        return
    record["start"], record["end"] = record["end"], record["start"]
    record["output_jt1_id"], record["output_jt2_id"] = (
        record["output_jt2_id"], record["output_jt1_id"]
    )
    record["output_reversed"] = True


def _main_wall_steel_configuration(section):
    stiffener_count = max(0, _as_int(_value(section, "T2"), 0))
    return {
        "cross_section_form": "I",
        "web_thickness_mm": _value(section, "H"),
        "partition_count": stiffener_count + 3,
        "internal_stiffener": {
            "count": stiffener_count,
            "width_mm": _value(section, "Dis"),
            "thickness_mm": _value(section, "Dis1"),
        },
    }


def _secondary_wall_steel_configuration(section):
    return {
        "cross_section_form": "T",
        "web_thickness_mm": _value(section, "H"),
        "flange_thickness_mm": _value(section, "Dis"),
    }


def _first_column_id(column_ids_by_node, floor_instance, floor_id, joint_id):
    ids = column_ids_by_node.get((floor_instance, floor_id, joint_id), [])
    return ids[0] if ids else None


def _build_wall_info(record, wall_h_column_ids_by_node):
    section = record["section"]
    segment = record["segment"]
    section_parameters = _meaningful_parameters(section, (
        "No_", "Mat", "Kind", "B", "H", "T2", "Dis", "Dis1",
        "colsect1", "colShapeVal1", "colsect2", "colShapeVal2",
        "Name", "StateFlag",
    ))
    segment_parameters = _meaningful_parameters(segment, (
        "No_", "Ecc", "HDiff1", "HDiff2", "HDiffB", "sloping",
        "EccDown", "offset1", "offset2", "HDiffB2", "WallJY",
        "NoSlab", "Prefix", "No", "Suffix", "StateFlag",
    ))
    floor_id = record["std_floor_id"]
    start_column_id = _first_column_id(
        wall_h_column_ids_by_node,
        record["floor_instance"],
        floor_id,
        record["output_jt1_id"],
    )
    end_column_id = _first_column_id(
        wall_h_column_ids_by_node,
        record["floor_instance"],
        floor_id,
        record["output_jt2_id"],
    )
    if record["kind"] == PEC_MAIN_WALL_KIND:
        steel_configuration = _main_wall_steel_configuration(section)
        tbl2_column_refs = {
            "start": start_column_id,
            "end": end_column_id,
        }
    else:
        steel_configuration = _secondary_wall_steel_configuration(section)
        tbl2_column_refs = {
            # Both L legs are normalized from the common corner to the outer
            # endpoint, so the secondary start resolves the main corner H.
            "connected_main": start_column_id if record["shape"] == "L" else None,
        }

    info = {
        "version": WINFO_VERSION,
        "tbl2_column_refs": tbl2_column_refs,
        "steel_configuration": steel_configuration,
        # These fields preserve source information for later Revit parameter
        # writing.  They are explicitly non-geometric: placement is derived
        # from tbl4 endpoints, WLegRole/WShape and the referenced H dimensions.
        "source_parameters": {
            "section": section_parameters,
            "segment": segment_parameters,
        },
    }
    if record.get("warning"):
        info["warning"] = record["warning"]
    return json.dumps(
        _json_safe(info), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _convert_ydb_in_place(source_path, destination_path):
    """Write upper-structure tables into an already isolated staging database."""
    source_path = Path(source_path).expanduser().resolve()
    destination_path = Path(destination_path).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError("YDB file does not exist: " + str(source_path))
    if source_path == destination_path:
        raise ValueError("Source YDB and destination database must be different files")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    # UNC 路径（\\server\share\...）经 as_uri() 会生成 file://server/...，
    # SQLite 拒绝非 localhost 的 URI authority，因此 UNC 时退回普通路径打开。
    if source_path.drive.startswith("\\\\"):
        source = sqlite3.connect(str(source_path))
    else:
        source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    source.text_factory = _decode_sqlite_text

    required_tables = ("tblFloor", "tblGrid", "tblJoint")
    missing = [name for name in required_tables if not _table_exists(source, name)]
    if missing:
        source.close()
        raise ValueError("YDB is missing required tables: " + ", ".join(missing))

    floors = _ordered_rows(source, "tblFloor")
    if not floors:
        source.close()
        raise ValueError("tblFloor contains no natural floors")

    grids = _ordered_rows(source, "tblGrid")
    joints = _ordered_rows(source, "tblJoint")
    beam_segments = _ordered_rows(source, "tblBeamSeg")
    beam_sections = _row_map(_ordered_rows(source, "tblBeamSect"))
    subsections = _row_map(_ordered_rows(source, "tblSubSectionSect"))
    column_segments = _ordered_rows(source, "tblColSeg")
    column_sections = _row_map(_ordered_rows(source, "tblColSect"))
    brace_segments = _ordered_rows(source, "tblBraceSeg")
    brace_sections = _row_map(_ordered_rows(source, "tblBraceSect"))
    wall_segments = _ordered_rows(source, "tblWallSeg")
    wall_sections = _row_map(_ordered_rows(source, "tblWallSect"))
    properties = _ordered_rows(source, "tblProperty")

    grids_by_key = {}
    grids_by_id = {}
    for row in grids:
        grids_by_key[(_value(row, "StdFlrID"), _value(row, "ID"))] = row
        grids_by_id[_value(row, "ID")] = row
    joints_by_key = {}
    joints_by_id = {}
    for row in joints:
        joints_by_key[(_value(row, "StdFlrID"), _value(row, "ID"))] = row
        joints_by_id[_value(row, "ID")] = row

    def grid_for(floor_id, grid_id):
        return grids_by_key.get((floor_id, grid_id)) or grids_by_id.get(grid_id)

    def joint_for(floor_id, joint_id):
        return joints_by_key.get((floor_id, joint_id)) or joints_by_id.get(joint_id)

    beam_segments_by_floor = _group_by(beam_segments, "StdFlrID")
    column_segments_by_floor = _group_by(column_segments, "StdFlrID")
    brace_segments_by_floor = _group_by(brace_segments, "StdFlrID")
    wall_segments_by_floor = _group_by(wall_segments, "StdFlrID")

    pec_main_wall_nodes = set()
    for segment in wall_segments:
        section = wall_sections.get(_value(segment, "SectID"))
        if _section_kind(section) != PEC_MAIN_WALL_KIND:
            continue
        standard_floor_id = _value(segment, "StdFlrID")
        grid = grid_for(standard_floor_id, _value(segment, "GridID"))
        if grid is not None:
            pec_main_wall_nodes.add((standard_floor_id, _value(grid, "Jt1ID")))
            pec_main_wall_nodes.add((standard_floor_id, _value(grid, "Jt2ID")))

    tbl1_rows = []
    # PEC sections whose four H dimensions cannot be resolved anywhere must
    # fail the conversion explicitly: emitting the raw ShapeVal with an @PEC
    # suffix would produce an unparseable section string downstream.
    unresolved_pec_sections = {}

    def _note_unresolved_pec(member_kind, section, subsection):
        if _h_dimensions(section, subsection) is None:
            key = (member_kind, _value(section, "ID"))
            unresolved_pec_sections[key] = unresolved_pec_sections.get(key, 0) + 1

    for floor in floors:
        standard_floor_id = _value(floor, "StdFlrID")
        top_z = _as_float(_value(floor, "LevelB")) + _as_float(_value(floor, "Height"))
        for segment in beam_segments_by_floor.get(standard_floor_id, []):
            grid = grid_for(standard_floor_id, _value(segment, "GridID"))
            if grid is None:
                continue
            joint1 = joint_for(standard_floor_id, _value(grid, "Jt1ID"))
            joint2 = joint_for(standard_floor_id, _value(grid, "Jt2ID"))
            if joint1 is None or joint2 is None:
                continue
            section = beam_sections.get(_value(segment, "SectID"))
            if _section_kind(section) == 209:
                _note_unresolved_pec(
                    "梁", section, subsections.get(_value(segment, "SectID"))
                )
                section_text = _h_section_name(
                    section, subsections.get(_value(segment, "SectID")), pec=True
                )
            else:
                section_text = _legacy_section_text(section)
            start_z = top_z + _as_float(_value(joint1, "HDiff")) + _as_float(_value(segment, "HDiff1"))
            end_z = top_z + _as_float(_value(joint2, "HDiff")) + _as_float(_value(segment, "HDiff2"))
            start_connection, end_connection = _connection_values(
                properties, _value(segment, "ID"), "SpBeam"
            )
            tbl1_rows.append((
                _value(joint1, "X"), _value(joint1, "Y"), start_z,
                _value(joint2, "X"), _value(joint2, "Y"), end_z,
                section_text, 0, len(tbl1_rows) + 1, None,
                start_connection, end_connection,
                _value(segment, "Ecc", 0),
                _value(segment, "Ecc2", _value(segment, "Ecc", 0)),
                _value(segment, "Rotation", 0),
            ))

        for segment in brace_segments_by_floor.get(standard_floor_id, []):
            joint1 = joint_for(standard_floor_id, _value(segment, "Jt1ID"))
            joint2 = joint_for(standard_floor_id, _value(segment, "Jt2ID"))
            if joint1 is None or joint2 is None:
                continue
            section = brace_sections.get(_value(segment, "SectID"))
            tbl1_rows.append((
                _value(joint1, "X"), _value(joint1, "Y"),
                top_z + _as_float(_value(joint1, "HDiff")) + _as_float(_value(segment, "HDiff1")),
                _value(joint2, "X"), _value(joint2, "Y"),
                top_z + _as_float(_value(joint2, "HDiff")) + _as_float(_value(segment, "HDiff2")),
                _legacy_section_text(section), 0, len(tbl1_rows) + 1, None,
                0, 0, 0, 0, 0,
            ))

    tbl2_rows = []
    wall_h_column_ids_by_node = {}
    for floor_instance, floor in enumerate(floors):
        standard_floor_id = _value(floor, "StdFlrID")
        bottom_z = _as_float(_value(floor, "LevelB"))
        top_z = bottom_z + _as_float(_value(floor, "Height"))
        for segment in column_segments_by_floor.get(standard_floor_id, []):
            joint = joint_for(standard_floor_id, _value(segment, "JtID"))
            if joint is None:
                continue
            section = column_sections.get(_value(segment, "SectID"))
            kind = _section_kind(section)
            node_key = (standard_floor_id, _value(segment, "JtID"))
            # The Kind-2 H profiles at a main-wall endpoint are the two PEC end
            # columns.  They are modeled independently as columns, while WInfo
            # also references them to preserve the wall-to-column relationship.
            is_wall_end_h = kind == 2 and node_key in pec_main_wall_nodes
            is_pec_h = kind == 209 or is_wall_end_h
            if is_pec_h:
                _note_unresolved_pec(
                    "柱", section, subsections.get(_value(segment, "SectID"))
                )
            section_text = (
                _h_section_name(section, subsections.get(_value(segment, "SectID")), pec=True)
                if is_pec_h else _legacy_section_text(section)
            )
            output_column_id = len(tbl2_rows) + 1
            # PEC wall-end H placement is a wall relationship, not a source
            # column eccentricity.  Keep the legacy columns for compatibility
            # but make them neutral; Revit derives center and rotation from the
            # referenced wall endpoint, direction and H dimensions.
            if is_wall_end_h:
                eccentric_x = 0
                eccentric_y = 0
                rotation = 0
            else:
                eccentric_x = _value(segment, "EccX", 0)
                eccentric_y = _value(segment, "EccY", 0)
                rotation = _value(segment, "Rotation", 0)
            tbl2_rows.append((
                _value(joint, "X"), _value(joint, "Y"), bottom_z,
                _value(joint, "X"), _value(joint, "Y"), top_z,
                section_text, 0, output_column_id, None,
                eccentric_x, eccentric_y, rotation,
            ))
            if is_wall_end_h:
                reference_key = (
                    floor_instance,
                    standard_floor_id,
                    _value(segment, "JtID"),
                )
                wall_h_column_ids_by_node.setdefault(reference_key, []).append(
                    output_column_id
                )

    if unresolved_pec_sections:
        source.close()
        summary = "；".join(
            "%s截面 SectID=%s（%d 根构件）" % (member_kind, sect_id, count)
            for (member_kind, sect_id), count in sorted(
                unresolved_pec_sections.items(),
                key=lambda item: (item[0][0], _as_int(item[0][1])),
            )
        )
        raise ValueError(
            "PEC 截面缺少尺寸数据，无法生成 H 截面字符串：" + summary +
            "。请在 YJK 中为这些截面补全尺寸（或重选标准截面）后重新导出 ydb。"
        )

    wall_records = []
    for floor_index, floor in enumerate(floors):
        standard_floor_id = _value(floor, "StdFlrID")
        bottom_z = _as_float(_value(floor, "LevelB"))
        for source_order, segment in enumerate(wall_segments_by_floor.get(standard_floor_id, [])):
            grid = grid_for(standard_floor_id, _value(segment, "GridID"))
            if grid is None:
                continue
            joint1 = joint_for(standard_floor_id, _value(grid, "Jt1ID"))
            joint2 = joint_for(standard_floor_id, _value(grid, "Jt2ID"))
            if joint1 is None or joint2 is None:
                continue
            section = wall_sections.get(_value(segment, "SectID"))
            kind = _section_kind(section)
            thickness = _value(section, "B", "")
            if kind == 1 or kind in PEC_WALL_KINDS:
                wall_section = _format_number(thickness)
            else:
                second = _value(section, "H")
                wall_section = _format_number(thickness)
                if second is not None:
                    wall_section += "@" + _format_number(second)
            wall_records.append({
                "floor_instance": floor_index,
                "source_order": source_order,
                "std_floor_id": standard_floor_id,
                "segment": segment,
                "section": section,
                "kind": kind,
                "joint1": joint1,
                "joint2": joint2,
                "jt1_id": _value(grid, "Jt1ID"),
                "jt2_id": _value(grid, "Jt2ID"),
                "output_jt1_id": _value(grid, "Jt1ID"),
                "output_jt2_id": _value(grid, "Jt2ID"),
                "start": [_value(joint1, "X"), _value(joint1, "Y"), bottom_z],
                "end": [_value(joint2, "X"), _value(joint2, "Y"), bottom_z],
                "wall_section": wall_section,
                "bottom_floor": str(standard_floor_id),
                "is_pec": kind in PEC_WALL_KINDS,
                "output_reversed": False,
                "shape": None,
                "group_id": None,
                "leg_id": None,
                "leg_role": None,
                "corner": None,
                "turn_sign": None,
            })

    group_counter = 0
    records_by_floor = {}
    for record in wall_records:
        records_by_floor.setdefault(record["floor_instance"], []).append(record)
    for floor_index in sorted(records_by_floor):
        pec_records = [record for record in records_by_floor[floor_index] if record["is_pec"]]
        main_records = [record for record in pec_records if record["kind"] == PEC_MAIN_WALL_KIND]
        secondary_records = [record for record in pec_records if record["kind"] == PEC_SECONDARY_WALL_KIND]
        assigned = set()
        groups = []
        for secondary in secondary_records:
            candidates = []
            for main in main_records:
                if id(main) in assigned:
                    continue
                corner_joint_id = _shared_joint(main, secondary)
                if corner_joint_id is None:
                    continue
                score = _perpendicular_score(main, secondary, corner_joint_id)
                if score is not None and score <= 0.05:
                    candidates.append((score, main["source_order"], main, corner_joint_id))
            if not candidates:
                continue
            _, _, main, corner_joint_id = min(candidates, key=lambda item: (item[0], item[1]))
            assigned.add(id(main))
            assigned.add(id(secondary))
            groups.append((min(main["source_order"], secondary["source_order"]), [main, secondary], corner_joint_id))
        for record in pec_records:
            if id(record) not in assigned:
                groups.append((record["source_order"], [record], None))
        groups.sort(key=lambda item: item[0])

        for _, members, corner_joint_id in groups:
            group_counter += 1
            group_id = "PECW" + str(group_counter).zfill(4)
            if len(members) == 2:
                main = next(item for item in members if item["kind"] == PEC_MAIN_WALL_KIND)
                secondary = next(item for item in members if item["kind"] == PEC_SECONDARY_WALL_KIND)
                corner = joint_for(main["std_floor_id"], corner_joint_id)
                _orient_from_corner(main, corner_joint_id)
                _orient_from_corner(secondary, corner_joint_id)
                main_vector = (main["end"][0] - main["start"][0], main["end"][1] - main["start"][1])
                secondary_vector = (
                    secondary["end"][0] - secondary["start"][0],
                    secondary["end"][1] - secondary["start"][1],
                )
                cross = main_vector[0] * secondary_vector[1] - main_vector[1] * secondary_vector[0]
                turn_sign = 1 if cross > 0 else -1 if cross < 0 else 0
                for leg_index, (record, role) in enumerate(
                    ((main, "MAIN"), (secondary, "SECONDARY")), start=1
                ):
                    record.update({
                        "shape": "L",
                        "group_id": group_id,
                        "leg_id": group_id + "-L" + str(leg_index),
                        "leg_role": role,
                        "corner": corner,
                        "turn_sign": turn_sign,
                    })
            else:
                record = members[0]
                record.update({
                    "shape": "I",
                    "group_id": group_id,
                    "leg_id": group_id + "-L1",
                    "leg_role": (
                        "SECONDARY"
                        if record["kind"] == PEC_SECONDARY_WALL_KIND
                        else "MAIN"
                    ),
                })
                if record["kind"] == PEC_SECONDARY_WALL_KIND:
                    record["warning"] = "Kind 212 has no perpendicular Kind 211 partner; exported as I"

    tbl4_rows = []
    for record in wall_records:
        wall_info = None
        if record["is_pec"]:
            wall_info = _build_wall_info(record, wall_h_column_ids_by_node)
        tbl4_rows.append((
            record["start"][0], record["start"][1], record["start"][2],
            record["end"][0], record["end"][1], record["end"][2],
            record["wall_section"], 0, len(tbl4_rows) + 1, None,
            record["bottom_floor"], 0,
            record["group_id"], record["leg_id"], record["leg_role"],
            record["shape"], wall_info,
        ))

    # tbl3 is the full set of level elevations: every floor bottom plus every
    # floor top, deduplicated by elevation.  In continuous single-tower models
    # each interior top equals the next floor bottom, so the output keeps the
    # legacy shape (bottoms + one RF).  In multi-tower models with a detached
    # top (e.g. a sloped-roof storey) the top would otherwise be missing and
    # the Revit side could not match a level for members ending there.
    tbl3_rows = []
    seen_elevations = set()

    def _add_level(name, elevation):
        key = round(_as_float(elevation), 6)
        if key in seen_elevations:
            return
        seen_elevations.add(key)
        tbl3_rows.append((name, elevation))

    for index, floor in enumerate(floors, start=1):
        _add_level(str(index) + "F", _value(floor, "LevelB"))
    # Claim "RF" for the last floor top first so the name always survives
    # deduplication and continuous models stay byte-identical to the old output.
    last_floor = floors[-1]
    _add_level(
        "RF",
        _as_float(_value(last_floor, "LevelB")) + _as_float(_value(last_floor, "Height")),
    )
    # Add interior tops that no floor bottom absorbs; name them RF2, RF3, ...
    intermediate_index = 1
    for floor in floors[:-1]:
        top = _as_float(_value(floor, "LevelB")) + _as_float(_value(floor, "Height"))
        previous = len(tbl3_rows)
        _add_level("RF" + str(intermediate_index + 1), top)
        if len(tbl3_rows) > previous:
            intermediate_index += 1

    destination = sqlite3.connect(str(destination_path))
    try:
        with destination:
            destination.execute("DROP TABLE IF EXISTS tbl1")
            destination.execute("""
                CREATE TABLE tbl1 (
                    BStartX REAL, BStartY REAL, BStartZ REAL,
                    BEndX REAL, BEndY REAL, BEndZ REAL,
                    BSection TEXT, Tag INTEGER DEFAULT 0, ID INTEGER, RvtID TEXT,
                    BSConn REAL, BEConn REAL, Ecc REAL, Ecc2 REAL, BRotation REAL
                )
            """)
            destination.executemany(
                "INSERT INTO tbl1 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tbl1_rows
            )

            destination.execute("DROP TABLE IF EXISTS tbl2")
            destination.execute("""
                CREATE TABLE tbl2 (
                    CStartX REAL, CStartY REAL, CStartZ REAL,
                    CEndX REAL, CEndY REAL, CEndZ REAL,
                    CSection TEXT, Tag INTEGER DEFAULT 0, ID INTEGER, RvtID TEXT,
                    EccX REAL, EccY REAL, Rotation REAL
                )
            """)
            destination.executemany(
                "INSERT INTO tbl2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", tbl2_rows
            )
            destination.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_tbl2_id ON tbl2(ID)"
            )

            destination.execute("DROP TABLE IF EXISTS tbl3")
            destination.execute("CREATE TABLE tbl3 (Floor TEXT, LevelB REAL)")
            destination.executemany("INSERT INTO tbl3 VALUES (?,?)", tbl3_rows)

            destination.execute("DROP TABLE IF EXISTS tbl4")
            destination.execute("""
                CREATE TABLE tbl4 (
                    WStartX REAL, WStartY REAL, WStartZ REAL,
                    WEndX REAL, WEndY REAL, WEndZ REAL,
                    WSection TEXT, Tag INTEGER DEFAULT 0, ID INTEGER, RvtID TEXT,
                    BottomFloor TEXT, WEConn REAL,
                    WGroupID TEXT, WLegID TEXT, WLegRole TEXT, WShape TEXT, WInfo TEXT
                )
            """)
            destination.executemany(
                "INSERT INTO tbl4 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tbl4_rows
            )
            destination.execute(
                "CREATE INDEX IF NOT EXISTS idx_tbl4_group ON tbl4(WGroupID)"
            )
            destination.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_tbl4_leg "
                "ON tbl4(WLegID) WHERE WLegID IS NOT NULL"
            )
    finally:
        destination.close()
        source.close()

    return {
        "source": str(source_path),
        "destination": str(destination_path),
        "beams_and_braces": len(tbl1_rows),
        "columns": len(tbl2_rows),
        "levels": len(tbl3_rows),
        "wall_legs": len(tbl4_rows),
        "pec_wall_groups": group_counter,
    }


def convert_ydb(source_path, destination_path):
    """Atomically rebuild tbl1-tbl4 while preserving every other database object."""
    source_path = Path(source_path).expanduser().resolve()
    destination_path = Path(destination_path).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError("YDB file does not exist: " + str(source_path))
    if source_path == destination_path:
        raise ValueError("Source YDB and destination database must be different files")
    return atomic_update_database(
        destination_path,
        UPPER_MODE,
        lambda pending_path: _convert_ydb_in_place(source_path, pending_path),
    )


def resolve_source_mode(source_path, requested_mode="auto"):
    """Resolve and validate the caller's expected source kind before any DB write."""
    if requested_mode not in ("auto", "upper", "foundation"):
        raise ValueError("unknown conversion mode: " + str(requested_mode))
    detected_mode = "foundation" if is_foundation_ydb(source_path) else "upper"
    if requested_mode != "auto" and requested_mode != detected_mode:
        raise ValueError(
            "source mode mismatch: expected {}, detected {}".format(
                requested_mode, detected_mode
            )
        )
    return detected_mode


def convert_auto_ydb(source_path, destination_path, mode="auto"):
    """Route a validated source without allowing upper/foundation scope mixing."""
    resolved_mode = resolve_source_mode(source_path, mode)
    if resolved_mode == "foundation":
        return convert_foundation_ydb(source_path, destination_path)
    return convert_ydb(source_path, destination_path)


def _default_destination():
    network_path = Path(r"Y:\数字化课题\数据库\ydb转换数据库.db")
    if network_path.exists():
        return network_path
    return Path(r"C:\ProgramData\Autodesk\Revit\Addins\2018\数据库\ydb转换数据库.db")


def _choose_source_file():
    """Open the native Windows file dialog without a Tcl/Tk dependency."""
    import ctypes
    from ctypes import wintypes

    class OpenFileNameW(ctypes.Structure):
        _fields_ = [
            ("lStructSize", wintypes.DWORD),
            ("hwndOwner", wintypes.HWND),
            ("hInstance", wintypes.HINSTANCE),
            ("lpstrFilter", wintypes.LPCWSTR),
            ("lpstrCustomFilter", wintypes.LPWSTR),
            ("nMaxCustFilter", wintypes.DWORD),
            ("nFilterIndex", wintypes.DWORD),
            ("lpstrFile", wintypes.LPWSTR),
            ("nMaxFile", wintypes.DWORD),
            ("lpstrFileTitle", wintypes.LPWSTR),
            ("nMaxFileTitle", wintypes.DWORD),
            ("lpstrInitialDir", wintypes.LPCWSTR),
            ("lpstrTitle", wintypes.LPCWSTR),
            ("Flags", wintypes.DWORD),
            ("nFileOffset", wintypes.WORD),
            ("nFileExtension", wintypes.WORD),
            ("lpstrDefExt", wintypes.LPCWSTR),
            ("lCustData", wintypes.LPARAM),
            ("lpfnHook", ctypes.c_void_p),
            ("lpTemplateName", wintypes.LPCWSTR),
            ("pvReserved", ctypes.c_void_p),
            ("dwReserved", wintypes.DWORD),
            ("FlagsEx", wintypes.DWORD),
        ]

    file_buffer = ctypes.create_unicode_buffer(32768)
    dialog = OpenFileNameW()
    dialog.lStructSize = ctypes.sizeof(OpenFileNameW)
    dialog.lpstrFilter = "YJK database (*.ydb)\0*.ydb\0All files (*.*)\0*.*\0\0"
    dialog.nFilterIndex = 1
    dialog.lpstrFile = ctypes.cast(file_buffer, wintypes.LPWSTR)
    dialog.nMaxFile = len(file_buffer)
    dialog.lpstrTitle = "选择 YJK YDB 文件"
    dialog.lpstrDefExt = "ydb"
    dialog.Flags = 0x00080000 | 0x00001000 | 0x00000800 | 0x00000008

    if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(dialog)):
        return file_buffer.value
    error_code = ctypes.windll.comdlg32.CommDlgExtendedError()
    if error_code:
        raise OSError("Windows file dialog failed with code " + hex(error_code))
    return ""


def _print_machine_result(payload):
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Convert a YJK ydb file to the Revit handoff database")
    parser.add_argument("source", nargs="?", help="input .ydb file; omit to use the file chooser")
    parser.add_argument("-o", "--output", help="destination SQLite database")
    parser.add_argument(
        "--mode",
        choices=("auto", "upper", "foundation"),
        default="auto",
        help="expected source scope; use upper/foundation for caller-side safety",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="open the local reinforcement editor after extracting a foundation YDB",
    )
    parser.add_argument("--host", default="127.0.0.1", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=8765, help="local web editor port")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser automatically")
    args = parser.parse_args(argv)

    source_path = args.source or _choose_source_file()
    if not source_path:
        _print_machine_result({
            "mode": args.mode,
            "status": "cancelled",
            "error": "no YDB file selected",
        })
        return 2
    destination_path = args.output or str(_default_destination())
    resolved_mode = None
    try:
        resolved_mode = resolve_source_mode(source_path, args.mode)
        if args.web and resolved_mode != "foundation":
            raise ValueError("--web is only available for a foundation YDB")
        summary = convert_auto_ydb(
            source_path,
            destination_path,
            mode=resolved_mode,
        )
    except Exception as error:
        _print_machine_result({
            "mode": resolved_mode or args.mode,
            "status": "error",
            "source": str(Path(source_path).expanduser()),
            "destination": str(Path(destination_path).expanduser()),
            "error_type": type(error).__name__,
            "error": str(error),
        })
        return 1

    _print_machine_result(summary)
    if args.web:
        from foundation_web import serve_foundation_editor
        serve_foundation_editor(
            destination_path,
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
