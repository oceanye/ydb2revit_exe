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
import re
import sqlite3
import struct
from pathlib import Path

from foundation_handoff import convert_foundation_ydb, is_foundation_ydb
from handoff_atomic import UPPER_MODE, atomic_update_database


PEC_WALL_KINDS = {211, 212}
PEC_MAIN_WALL_KIND = 211
PEC_SECONDARY_WALL_KIND = 212
WINFO_VERSION = 4

# ---------------------------------------------------------------------------
# 截面文本契约：与 CreateNewExtern/SectionTextParser.cs 保持一致。
# 权威来源为 E:\revit-external-tool2.git（插件源码仓库，只读参考）：
#   * PEC 标记 = 末尾 "@PEC" 后缀，不区分大小写，仅识别最后一个；
#   * H 截面文本 = H{h}{sep}{b}{sep}{tw}{sep}{tf}，sep ∈ x/X/×/*，允许空白；
#   * 尺寸为正数，最多 3 位小数（"0.###"），规范名用大写 X 分隔；
#   * 同尺寸的 PEC 与普通 H 型钢不得视为同一截面。
# ---------------------------------------------------------------------------
PEC_SUFFIX = "@PEC"
_H_SECTION_TEXT_PATTERN = re.compile(
    r"^H\s*(\d+(?:\.\d+)?)\s*[xX\u00d7*]\s*(\d+(?:\.\d+)?)"
    r"\s*[xX\u00d7*]\s*(\d+(?:\.\d+)?)\s*[xX\u00d7*]\s*(\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)


def has_pec_suffix(section_text):
    """截面文本是否带 PEC 标记（仅末尾 @PEC，不区分大小写）。"""
    text = str(section_text or "").rstrip()
    return text.lower().endswith(PEC_SUFFIX.lower())


def remove_pec_suffix(section_text):
    """只移除末尾一个 @PEC；其余 @ 元数据保持原样（旧 ShapeVal 兼容）。"""
    text = str(section_text or "").strip()
    if has_pec_suffix(text):
        return text[: -len(PEC_SUFFIX)].rstrip()
    return text


def parse_h_section_text(section_text):
    """按 SectionTextParser 契约解析 H 截面文本。

    返回 (h, b, tw, tf, is_pec)，无法解析返回 None。兼容旧 C# 输出的
    末尾多余 "X"（"...X@PEC"）。
    """
    if section_text is None:
        return None
    is_pec = has_pec_suffix(section_text)
    core = remove_pec_suffix(section_text)
    if is_pec and core.upper().endswith("X"):
        core = core[:-1].rstrip()
    match = _H_SECTION_TEXT_PATTERN.match(core.strip())
    if match is None:
        return None
    values = [float(part) for part in match.groups()]
    if any(value <= 0 for value in values):
        return None
    return values[0], values[1], values[2], values[3], is_pec


def format_h_section(height, width, web, flange, pec):
    """输出规范截面名：H{h}X{b}X{tw}X{tf}（0.###，大写 X）+ @PEC。"""
    def dimension(value):
        text = format(float(value), ".3f").rstrip("0").rstrip(".")
        return text or "0"

    name = "H" + "X".join(
        dimension(value) for value in (height, width, web, flange)
    )
    return name + PEC_SUFFIX if pec else name


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

    Two layouts share the same head (per "YJK Kind26热轧H型钢与Kind209_YDB解析说明"
    and the plugin-side handoff "Kind26短格式解码与1117矛盾截面修正"):

    * long  (>=42 CSV integers): [1..8]=32B selector+name blob, [9]=b Q16,
      [10]=h Q16, [14]=custom flag (must be 0), [41]=tail ID (must match);
    * short (>=11 CSV integers): [1..3]=12B selector+name blob, [9]=b Q16,
      [10]=h Q16 — no custom-flag/tail-ID columns to check.

    Only the standard catalogue selector (39) with a cross-validated name is
    accepted; everything else stays unexplained so the caller can fall back
    to main-table values (with a warning) or reject explicitly.
    """
    if subsection is None:
        return None
    text = str(_value(subsection, "ShapeVal", "") or "")
    try:
        fields = [int(part) for part in text.strip(",").split(",")]
    except ValueError:
        return None
    is_long = len(fields) >= 42
    if len(fields) < 11 or fields[0] != 26:
        return None
    if is_long:
        if fields[14] != 0:      # 自定义截面不在国标表
            return None
        if fields[41] != _as_int(_value(subsection, "ID"), 0):
            return None
    blob = struct.pack("<8I" if is_long else "<3I",
                       *(fields[1:9] if is_long else fields[1:4]))
    if blob[0] | (blob[1] << 8) != 39:
        return None
    name = blob[2:].split(b"\x00")[0]
    try:
        name = name.decode("ascii").strip().upper()
    except UnicodeDecodeError:
        return None
    height = fields[10] / 65536.0
    width = fields[9] / 65536.0
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
        # 优先级修订（handoff：Kind26短格式解码与1117矛盾截面修正）：子表
        # Kind-26 打包定义（长/短格式）解码成功且校验通过时优先于主表
        # t/d/u/f——主表数值在该类数据上可能是编辑残留的陈旧值（如 1117：
        # 主表 H350x150x6x11 vs 子表 HW200X200，YJK 界面显示后者）。
        packed = _packed_hot_rolled_h_dimensions(subsection)
        if packed is not None:
            return packed
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
        return None
    return height, width, web, flange


def _h_section_name(section, subsection=None, pec=False):
    dimensions = _h_dimensions(section, subsection)
    if dimensions is None:
        # 尺寸不可解析的 PEC 截面会在装配后统一显式拒绝，此兜底不会落库。
        raw = str(_value(section, "ShapeVal", "") or "").rstrip(",")
        base = raw or "H"
        if pec and not has_pec_suffix(base):
            base += PEC_SUFFIX
        return base
    return format_h_section(*dimensions, pec=pec)


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

    def _detect_arc_beam_chains():
        """几何识别弧梁弦线链，返回应标记 BIsArc=1 的梁段 ID 集合。

        ydb 无弧元数据（无弧表、tblGrid.idCen 全哨兵、梁段无标志字段），
        YJK 导出时把弧梁离散为弦线段；判定 = 同层首尾相连 ≥3 段、且链上
        每个节点转角在 0.03°~15°（真折梁的构造转角通常更大，共线续接
        则为 0°）。阈值以颛桥实测校准（17 链 / 60 段），依据
        handoff-Python端-弧梁标记列BIsArc.md 的判定权授约定。
        """
        segments = {}
        adjacency = {}
        for segment in beam_segments:
            grid = grid_for(_value(segment, "StdFlrID"), _value(segment, "GridID"))
            if grid is None:
                continue
            node_a, node_b = _value(grid, "Jt1ID"), _value(grid, "Jt2ID")
            key = _value(segment, "ID")
            floor_id = _value(segment, "StdFlrID")
            segments[key] = (floor_id, node_a, node_b)
            for node in (node_a, node_b):
                adjacency.setdefault((floor_id, node), []).append(key)

        coordinates = {}
        def node_xy(floor_id, node):
            cache_key = (floor_id, node)
            if cache_key not in coordinates:
                joint = joint_for(floor_id, node)
                coordinates[cache_key] = (
                    None if joint is None
                    else (_as_float(_value(joint, "X")), _as_float(_value(joint, "Y")))
                )
            return coordinates[cache_key]

        def turn_and_sign(a, m, b):
            pa, pm, pb = node_xy(*a), node_xy(*m), node_xy(*b)
            if pa is None or pm is None or pb is None:
                return None, 0
            v1 = (pm[0] - pa[0], pm[1] - pa[1])
            v2 = (pb[0] - pm[0], pb[1] - pm[1])
            n1, n2 = math.hypot(*v1), math.hypot(*v2)
            if n1 < 1.0 or n2 < 1.0:
                return None, 0
            cosine = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
            cross = v1[0] * v2[1] - v1[1] * v2[0]
            return math.degrees(math.acos(cosine)), (1 if cross > 0 else -1 if cross < 0 else 0)

        marked = set()
        visited = set()
        for segment_id, (floor_id, node_a, node_b) in segments.items():
            if segment_id in visited:
                continue
            for start_node, next_node in ((node_a, node_b), (node_b, node_a)):
                if segment_id in visited:
                    break
                chain = [segment_id]
                visited.add(segment_id)
                previous, current = start_node, next_node
                chain_sign = 0
                chain_turn_total = 0.0
                while True:
                    successor = None
                    for candidate in adjacency.get((floor_id, current), []):
                        if candidate in visited or candidate == chain[-1]:
                            continue
                        other_floor, ca, cb = segments[candidate]
                        other_node = cb if ca == current else ca
                        angle, sign = turn_and_sign(
                            (floor_id, previous), (floor_id, current),
                            (floor_id, other_node),
                        )
                        if angle is None or not 0.1 <= angle <= 40.0:
                            continue
                        # 单调转向：真弧各节点转角同方向；直梁坐标抖动方向随机。
                        if chain_sign and sign and sign != chain_sign:
                            continue
                        successor = (candidate, other_node, angle, sign)
                        break
                    if successor is None:
                        break
                    candidate, other_node, angle, sign = successor
                    visited.add(candidate)
                    chain.append(candidate)
                    chain_turn_total += angle
                    if sign:
                        chain_sign = sign
                    previous, current = current, other_node
                # 累计转角下限：整链弯曲不足的近直链（漂移）不标。
                if len(chain) >= 3 and chain_turn_total >= 1.5:
                    marked.update(chain)
                    break
        return marked

    arc_beam_segment_ids = _detect_arc_beam_chains()

    tbl1_rows = []
    # PEC sections whose four H dimensions cannot be resolved anywhere must
    # fail the conversion explicitly: emitting the raw ShapeVal with an @PEC
    # suffix would produce an unparseable section string downstream.
    unresolved_pec_sections = {}
    # PEC sections that fell back to main-table t/d/u/f because no usable
    # subsection exists (neither packed Kind-26 nor numeric columns): each
    # must be named in the conversion warnings, never silently (handoff
    # "Kind26短格式解码与1117矛盾截面修正" §2.3).
    main_value_fallback_sections = {}

    def _note_unresolved_pec(member_kind, section, subsection):
        if _h_dimensions(section, subsection) is None:
            key = (member_kind, _value(section, "ID"))
            unresolved_pec_sections[key] = unresolved_pec_sections.get(key, 0) + 1

    def _note_main_value_fallback(member_kind, section, subsection):
        if _section_kind(section) != 209:
            return
        if _h_dimensions(section, subsection) is None:
            return  # 无尺寸路径由显式拒绝负责
        if _packed_hot_rolled_h_dimensions(subsection) is not None:
            return  # 子表打包定义为准（优先级规则）
        if subsection is not None and any(
            _first_positive(_value(subsection, name)) is not None
            for name in ("t", "d", "u", "f", "h", "b")
        ):
            return  # 子表数值列有效——规范 §4.2 的正常形态
        key = (member_kind, _value(section, "ID"))
        main_value_fallback_sections[key] = main_value_fallback_sections.get(key, 0) + 1

    for floor in floors:
        standard_floor_id = _value(floor, "StdFlrID")
        bottom_z = _as_float(_value(floor, "LevelB"))
        top_z = bottom_z + _as_float(_value(floor, "Height"))
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
                _note_main_value_fallback(
                    "梁", section, subsections.get(_value(segment, "SectID"))
                )
                section_text = _h_section_name(
                    section, subsections.get(_value(segment, "SectID")), pec=True
                )
            else:
                section_text = _legacy_section_text(section)
            start_z = top_z + _as_float(_value(joint1, "HDiff")) + _as_float(_value(segment, "HDiff1"))
            end_z = top_z + _as_float(_value(joint2, "HDiff")) + _as_float(_value(segment, "HDiff2"))
            # 偏移继承契约：挂"偏移绝对值最小"的层标高（本层底或本层顶，
            # 平手取层顶）；基准由起端 Z 判定，两端偏移同基准。
            beam_ref_z = (
                top_z if abs(start_z - top_z) <= abs(start_z - bottom_z)
                else bottom_z
            )
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
                start_z - beam_ref_z, end_z - beam_ref_z,
                1 if _value(segment, "ID") in arc_beam_segment_ids else 0,
            ))

        for segment in brace_segments_by_floor.get(standard_floor_id, []):
            joint1 = joint_for(standard_floor_id, _value(segment, "Jt1ID"))
            joint2 = joint_for(standard_floor_id, _value(segment, "Jt2ID"))
            if joint1 is None or joint2 is None:
                continue
            section = brace_sections.get(_value(segment, "SectID"))
            brace_start_z = top_z + _as_float(_value(joint1, "HDiff")) + _as_float(_value(segment, "HDiff1"))
            brace_end_z = top_z + _as_float(_value(joint2, "HDiff")) + _as_float(_value(segment, "HDiff2"))
            brace_ref_z = (
                top_z if abs(brace_start_z - top_z) <= abs(brace_start_z - bottom_z)
                else bottom_z
            )
            tbl1_rows.append((
                _value(joint1, "X"), _value(joint1, "Y"), brace_start_z,
                _value(joint2, "X"), _value(joint2, "Y"), brace_end_z,
                _legacy_section_text(section), 0, len(tbl1_rows) + 1, None,
                0, 0, 0, 0, 0,
                brace_start_z - brace_ref_z, brace_end_z - brace_ref_z, 0,
            ))

    tbl2_rows = []
    adjusted_column_tops = []
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
                if kind == 209:
                    _note_main_value_fallback(
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
            # 柱顶 = 层顶 + 该柱节点 HDiff（节点标高差；柱顶随梁平齐的
            # 数据机制——梁 Z 公式本就含节点分量，柱与其同构）。
            column_top_z = top_z + _as_float(_value(joint, "HDiff"))
            if abs(column_top_z - top_z) > 1e-6:
                adjusted_column_tops.append((_value(segment, "ID"), column_top_z))
            tbl2_rows.append((
                _value(joint, "X"), _value(joint, "Y"), bottom_z,
                _value(joint, "X"), _value(joint, "Y"), column_top_z,
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
        floor_top_z = bottom_z + _as_float(_value(floor, "Height"))
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
                # 墙顶 = 层顶 + 墙段 HDiff + 端节点 HDiff（与梁 Z 公式同构；
                # 实测两种来源互斥出现，叠加即完整，如弧形边缘墙仅节点带值）。
                "top_start_z": floor_top_z + _as_float(_value(segment, "HDiff1"))
                               + _as_float(_value(joint1, "HDiff")),
                "top_end_z": floor_top_z + _as_float(_value(segment, "HDiff2"))
                             + _as_float(_value(joint2, "HDiff")),
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
            record["top_start_z"], record["top_end_z"],
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
                    BSConn REAL, BEConn REAL, Ecc REAL, Ecc2 REAL, BRotation REAL,
                    BZOffset REAL, BZOffset2 REAL, BIsArc INTEGER DEFAULT 0
                )
            """)
            destination.executemany(
                "INSERT INTO tbl1 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tbl1_rows
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
                    WGroupID TEXT, WLegID TEXT, WLegRole TEXT, WShape TEXT, WInfo TEXT,
                    WTopZ REAL, WTopZ2 REAL
                )
            """)
            destination.executemany(
                "INSERT INTO tbl4 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tbl4_rows
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

    warnings = []
    if adjusted_column_tops:
        samples = "、".join(
            "SegID=%s->%.0f" % (seg_id, top_z) for seg_id, top_z in adjusted_column_tops[:3]
        )
        warnings.append(
            "柱顶随节点标高下调 %d 根（示例 %s%s）" % (
                len(adjusted_column_tops), samples,
                " 等" if len(adjusted_column_tops) > 3 else ""))
    if main_value_fallback_sections:
        warnings = [
            "%s截面 SectID=%s 无子表定义，截面取主表数值（%d 根构件）" % (
                member_kind, sect_id, count)
            for (member_kind, sect_id), count in sorted(
                main_value_fallback_sections.items(),
                key=lambda item: (item[0][0], _as_int(item[0][1])),
            )
        ]

    return {
        "source": str(source_path),
        "destination": str(destination_path),
        "beams_and_braces": len(tbl1_rows),
        "columns": len(tbl2_rows),
        "levels": len(tbl3_rows),
        "wall_legs": len(tbl4_rows),
        "pec_wall_groups": group_counter,
        "arc_beam_segments": len(arc_beam_segment_ids),
        "column_tops_adjusted": len(adjusted_column_tops),
        "warnings": warnings,
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
