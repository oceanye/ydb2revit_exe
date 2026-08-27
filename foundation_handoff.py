# coding: utf-8
"""YJK foundation YDB extraction and editable Revit handoff storage.

Production data in this module comes exclusively from the source YDB.  A DWG
is never opened, parsed, or used as a fallback.  The supported modelling scope
is intentionally narrow and explicit:

* one-step rectangular or polygonal pile caps;
* vertical piles;
* reinforcement is user-authored in the handoff database, not read from YDB.

The extracted data extends the existing Revit handoff database as tbl5-tbl7:
pile types, cap types (including local pile layouts), and cap placements.
Geometry and active type rows are rebuilt on every extraction, while manually
entered reinforcement is restored by stable geometry hashes when the
corresponding dimensions have not changed.  Existing tbl1-tbl4 are never
modified here.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from handoff_atomic import FOUNDATION_MODE, atomic_update_database


FOUNDATION_SCHEMA_VERSION = 2
ROUND_DIGITS = 6
CAP_REBAR_FIELDS = (
    "UserTypeName",
    "BottomX",
    "BottomY",
    "TopX",
    "TopY",
    "SideRebar",
    "Cover",
    "Notes",
    "ExtraJson",
    "UpdatedAt",
)
PILE_REBAR_FIELDS = (
    "UserTypeName",
    "LongitudinalRebar",
    "StirrupRebar",
    "DenseStirrupRebar",
    "DenseZoneLength",
    "Cover",
    "Notes",
    "ExtraJson",
    "UpdatedAt",
)
REQUIRED_SOURCE_TABLES = {
    "DEF_dais",
    "app_dais",
    "dais_pt",
    "dais_stepH",
    "DEF_Pile",
    "app_Pile",
    "node",
}


class FoundationDataError(ValueError):
    """Raised when a YDB is outside the explicitly supported foundation scope."""


def _quote_identifier(name):
    return '"' + str(name).replace('"', '""') + '"'


def _utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean_number(value):
    value = round(float(value), ROUND_DIGITS)
    return 0.0 if value == 0 else value


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _source_connection(path):
    source_path = Path(path).expanduser().resolve()
    uri = source_path.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _table_names(connection):
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def is_foundation_ydb(path):
    """Return whether *path* exposes the required YJK foundation tables."""
    try:
        connection = _source_connection(path)
    except (OSError, sqlite3.Error):
        return False
    try:
        return REQUIRED_SOURCE_TABLES.issubset(_table_names(connection))
    finally:
        connection.close()


def _require_foundation_schema(connection):
    missing = sorted(REQUIRED_SOURCE_TABLES - _table_names(connection))
    if missing:
        raise FoundationDataError(
            "Not a supported foundation YDB; missing tables: " + ", ".join(missing)
        )


def _rows(connection, table_name, order="ID"):
    return list(
        connection.execute(
            "SELECT * FROM " + _quote_identifier(table_name)
            + (" ORDER BY " + _quote_identifier(order) if order else "")
        )
    )


def _polygon_area(points):
    return 0.5 * sum(
        points[index][0] * points[(index + 1) % len(points)][1]
        - points[(index + 1) % len(points)][0] * points[index][1]
        for index in range(len(points))
    )


def _clean_polygon(points, label):
    cleaned = []
    for point in points:
        candidate = (_clean_number(point[0]), _clean_number(point[1]))
        if not cleaned or candidate != cleaned[-1]:
            cleaned.append(candidate)
    if len(cleaned) > 1 and cleaned[0] == cleaned[-1]:
        cleaned.pop()
    if len(cleaned) < 3:
        raise FoundationDataError(label + " has fewer than three polygon points")
    if abs(_polygon_area(cleaned)) < 1e-6:
        raise FoundationDataError(label + " has a zero-area polygon")
    return cleaned


def _normalise_model_variant(points, pile_layout):
    origin_x, origin_y = points[0]
    edge_x = points[1][0] - origin_x
    edge_y = points[1][1] - origin_y
    edge_angle = math.atan2(edge_y, edge_x)
    edge_length = math.hypot(edge_x, edge_y)
    if edge_length <= 1e-9:
        raise FoundationDataError("polygon contains a zero-length edge")
    cosine = edge_x / edge_length
    sine = edge_y / edge_length

    def normalise_xy(x, y):
        dx = x - origin_x
        dy = y - origin_y
        return (
            _clean_number(dx * cosine + dy * sine),
            _clean_number(-dx * sine + dy * cosine),
        )

    polygon = tuple(normalise_xy(x, y) for x, y in points)
    piles = tuple(sorted(
        (
            *normalise_xy(item["x"], item["y"]),
            _clean_number(item["top_offset_z"]),
            item["pile_type_key"],
        )
        for item in pile_layout
    ))
    return polygon, piles, (origin_x, origin_y), edge_angle


def _canonical_cap_model(points, pile_layout):
    """Canonicalise a cap outline and its attached local pile arrangement."""
    best = None
    sequence = list(points)
    if _polygon_area(sequence) < 0:
        sequence.reverse()
    for start in range(len(sequence)):
        rotated = sequence[start:] + sequence[:start]
        variant = _normalise_model_variant(rotated, pile_layout)
        representation = (variant[0], variant[1])
        if best is None or representation < best[0]:
            best = (representation, variant[2], variant[3])
    polygon, piles = best[0]
    return {
        "polygon": polygon,
        "piles": piles,
        "source_origin": best[1],
        "source_axis_angle": best[2],
    }


def _stable_key(prefix, payload):
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return prefix + "-" + hashlib.sha256(encoded).hexdigest()[:16].upper()


def _transform_point(center_x, center_y, angle, point):
    cosine = math.cos(angle)
    sine = math.sin(angle)
    x, y = point
    return (
        _clean_number(center_x + x * cosine - y * sine),
        _clean_number(center_y + x * sine + y * cosine),
    )


def _source_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest().upper()


def _create_destination_schema(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS handoff_meta (
            Key TEXT PRIMARY KEY,
            Value TEXT NOT NULL
        );

        CREATE TABLE tbl5 (
            ID INTEGER PRIMARY KEY,
            TypeKey TEXT NOT NULL UNIQUE,
            Diameter REAL NOT NULL,
            Length REAL NOT NULL,
            UserTypeName TEXT NOT NULL DEFAULT '',
            LongitudinalRebar TEXT NOT NULL DEFAULT '',
            StirrupRebar TEXT NOT NULL DEFAULT '',
            DenseStirrupRebar TEXT NOT NULL DEFAULT '',
            DenseZoneLength REAL,
            Cover REAL,
            Notes TEXT NOT NULL DEFAULT '',
            ExtraJson TEXT NOT NULL DEFAULT '{}',
            UpdatedAt TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE tbl6 (
            ID INTEGER PRIMARY KEY,
            TypeKey TEXT NOT NULL,
            PolygonJson TEXT NOT NULL,
            Thickness REAL NOT NULL,
            PileLayoutJson TEXT NOT NULL,
            UserTypeName TEXT NOT NULL DEFAULT '',
            BottomX TEXT NOT NULL DEFAULT '',
            BottomY TEXT NOT NULL DEFAULT '',
            TopX TEXT NOT NULL DEFAULT '',
            TopY TEXT NOT NULL DEFAULT '',
            SideRebar TEXT NOT NULL DEFAULT '',
            Cover REAL,
            Notes TEXT NOT NULL DEFAULT '',
            ExtraJson TEXT NOT NULL DEFAULT '{}',
            UpdatedAt TEXT NOT NULL DEFAULT '',
            UNIQUE(TypeKey)
        );

        CREATE TABLE tbl7 (
            X REAL NOT NULL,
            Y REAL NOT NULL,
            BottomZ REAL NOT NULL,
            Rotation REAL NOT NULL,
            CapTypeID INTEGER NOT NULL,
            Tag INTEGER NOT NULL DEFAULT 0,
            ID INTEGER PRIMARY KEY,
            RvtID TEXT,
            FOREIGN KEY(CapTypeID) REFERENCES tbl6(ID)
        );

        CREATE INDEX idx_tbl7_type ON tbl7(CapTypeID);
        """
    )


def _column_names(connection, table_name):
    return {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(" + _quote_identifier(table_name) + ")"
        )
    }


def _snapshot_rebar(connection, table_name, fields):
    """Read editable values before rebuilding the active type tables."""
    if table_name not in _table_names(connection):
        return {}
    required = {"TypeKey", *fields}
    missing = sorted(required - _column_names(connection, table_name))
    if missing:
        return {}
    columns = ("TypeKey",) + tuple(fields)
    sql = "SELECT {} FROM {}".format(
        ",".join(_quote_identifier(column) for column in columns),
        _quote_identifier(table_name),
    )
    return {
        row[0]: dict(zip(fields, row[1:]))
        for row in connection.execute(sql)
    }


def _existing_rebar(connection, table_names, fields):
    values = {}
    for table_name in table_names:
        values.update(_snapshot_rebar(connection, table_name, fields))
    return values


def _drop_foundation_contract_tables(connection):
    for table_name in ("tbl7", "tbl6", "tbl5"):
        connection.execute("DROP TABLE IF EXISTS " + _quote_identifier(table_name))


def _restored_rebar(records, type_key, fields, nullable_fields=()):
    saved = records.get(type_key, {})
    nullable_fields = set(nullable_fields)
    result = {}
    for field in fields:
        default = None if field in nullable_fields else ("{}" if field == "ExtraJson" else "")
        value = saved.get(field, default)
        result[field] = default if value is None and field not in nullable_fields else value
    return result


def _extract_source_model(connection):
    _require_foundation_schema(connection)

    definitions = {int(row["lID"]): row for row in _rows(connection, "DEF_dais")}
    if not definitions:
        raise FoundationDataError("foundation YDB contains no pile-cap definitions")

    circle_rows = _rows(connection, "dais_Cir") if "dais_Cir" in _table_names(connection) else []
    if circle_rows:
        raise FoundationDataError("circular pile caps are outside the supported scope")

    points_by_flag_step = defaultdict(list)
    for row in _rows(connection, "dais_pt"):
        points_by_flag_step[(int(row["DaisFlag"]), int(row["nstep"]))].append(
            (float(row["x"]), float(row["y"]))
        )

    heights_by_flag_step = {}
    for row in _rows(connection, "dais_stepH"):
        key = (int(row["DaisFlag"]), int(row["nstep"]))
        heights_by_flag_step[key] = float(row["H"])

    cap_definitions = {}
    for kind, row in definitions.items():
        nstep = int(row["nstep"])
        if nstep != 1:
            raise FoundationDataError(
                "pile-cap definition kind {} has {} steps; only one-step caps are supported".format(
                    kind, nstep
                )
            )
        flag = int(row["DaisFlag"])
        if flag != kind:
            raise FoundationDataError(
                "pile-cap kind {} has inconsistent DaisFlag {}".format(kind, flag)
            )
        polygon = _clean_polygon(
            points_by_flag_step.get((flag, 0), []),
            "pile-cap definition kind {}".format(kind),
        )
        if (flag, 0) not in heights_by_flag_step:
            raise FoundationDataError(
                "pile-cap definition kind {} has no height".format(kind)
            )
        height = float(heights_by_flag_step[(flag, 0)])
        if height <= 0:
            raise FoundationDataError(
                "pile-cap definition kind {} has a non-positive height".format(kind)
            )
        cap_definitions[kind] = {
            "kind": kind,
            "flag": flag,
            "polygon": polygon,
            "height": _clean_number(height),
            "declared_pile_count": int(row["npile"]),
        }

    pile_definitions = {int(row["ID"]): row for row in _rows(connection, "DEF_Pile")}
    pile_templates = defaultdict(list)
    for row in _rows(connection, "app_Pile"):
        flag = int(row["DaisFlag"])
        if flag < 0:
            raise FoundationDataError(
                "standalone piles are present; this extraction currently requires cap-associated piles"
            )
        if any(abs(float(row[name] or 0)) > 1e-9 for name in ("fKn", "fKm", "fALFQ")):
            raise FoundationDataError(
                "inclined pile parameters are present; only vertical piles are supported"
            )
        pile_templates[flag].append(row)

    for definition in cap_definitions.values():
        actual_count = len(pile_templates.get(definition["flag"], []))
        if actual_count != definition["declared_pile_count"]:
            raise FoundationDataError(
                "pile-cap kind {} declares {} piles but has {} pile template rows".format(
                    definition["kind"], definition["declared_pile_count"], actual_count
                )
            )

    nodes = {int(row["ID"]): row for row in _rows(connection, "node")}
    cap_apps = _rows(connection, "app_dais")
    pile_types = {}
    cap_types = {}
    cap_placements = []
    pile_instance_count = 0

    for placement_id, application in enumerate(cap_apps, 1):
        kind = int(application["kind"])
        if kind not in cap_definitions:
            raise FoundationDataError(
                "pile-cap application {} references unknown kind {}".format(
                    placement_id, kind
                )
            )
        if int(application["isBGAbs"]) != 1:
            raise FoundationDataError(
                "pile-cap application {} uses relative bottom elevation; only absolute elevation is supported".format(
                    placement_id
                )
            )
        node_id = int(application["nj"]) + 1
        if node_id not in nodes:
            raise FoundationDataError(
                "pile-cap application {} references missing node {}".format(
                    placement_id, node_id
                )
            )
        definition = cap_definitions[kind]
        node = nodes[node_id]
        center_x = float(node["X"]) + float(application["ex"] or 0)
        center_y = float(node["Y"]) + float(application["ey"] or 0)
        angle = float(application["ang"] or 0)
        bottom_z = float(application["dBotElevat"]) * 1000.0
        pile_length = float(application["idaispilelen"] or 0)
        templates = pile_templates[definition["flag"]]
        if templates and pile_length <= 0:
            raise FoundationDataError(
                "pile-cap application {} has no positive pile length".format(placement_id)
            )

        raw_layout = []
        for template in templates:
            pile_definition_id = int(template["kind"])
            if pile_definition_id not in pile_definitions:
                raise FoundationDataError(
                    "pile template {} references unknown pile definition {}".format(
                        template["ID"], pile_definition_id
                    )
                )
            pile_definition = pile_definitions[pile_definition_id]
            section_b = float(pile_definition["B"] or 0)
            section_h = float(pile_definition["H"] or 0)
            if section_b <= 0:
                raise FoundationDataError(
                    "pile definition {} has no positive section size".format(pile_definition_id)
                )
            if section_h > 0:
                raise FoundationDataError(
                    "pile definition {} is rectangular; the handoff pile type requires diameter and length".format(
                        pile_definition_id
                    )
                )
            diameter = _clean_number(section_b)
            length = _clean_number(pile_length * 1000.0)
            type_payload = {
                "diameter": diameter,
                "length": length,
            }
            pile_type_key = _stable_key("PILE", type_payload)
            pile_types.setdefault(pile_type_key, type_payload)
            pile_instance_count += 1
            raw_layout.append({
                "x": _clean_number(float(template["x"] or 0)),
                "y": _clean_number(float(template["y"] or 0)),
                "top_offset_z": _clean_number(float(template["z"] or 0)),
                "pile_type_key": pile_type_key,
            })

        model = _canonical_cap_model(definition["polygon"], raw_layout)
        type_payload = {
            "polygon": model["polygon"],
            "thickness": definition["height"],
            "piles": model["piles"],
        }
        cap_type_key = _stable_key("CAP", type_payload)
        cap_types.setdefault(cap_type_key, {
            "polygon": model["polygon"],
            "thickness": definition["height"],
            "pile_layout": model["piles"],
        })

        origin_x, origin_y = _transform_point(
            center_x,
            center_y,
            angle,
            model["source_origin"],
        )
        cap_placements.append({
            "id": placement_id,
            "cap_type_key": cap_type_key,
            "x": origin_x,
            "y": origin_y,
            "bottom_z": _clean_number(bottom_z),
            "rotation": _clean_number(
                math.degrees(angle + model["source_axis_angle"]) % 360.0
            ),
        })

    return pile_types, cap_types, cap_placements, pile_instance_count


def _convert_foundation_ydb_in_place(source_path, destination_path):
    """Write foundation tables into an already isolated staging database."""
    source_path = Path(source_path).expanduser().resolve()
    destination_path = Path(destination_path).expanduser().resolve()
    if source_path == destination_path:
        raise ValueError("source YDB and destination database must be different files")
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    source = _source_connection(source_path)
    try:
        pile_types, cap_types, placements, pile_count = _extract_source_model(source)
    finally:
        source.close()

    cap_type_ids = {type_key: index for index, type_key in enumerate(sorted(cap_types), 1)}
    pile_type_ids = {type_key: index for index, type_key in enumerate(sorted(pile_types), 1)}

    destination = sqlite3.connect(str(destination_path))
    try:
        destination.execute("PRAGMA foreign_keys=ON")
        with destination:
            cap_rebar = _existing_rebar(
                destination,
                ("FoundationCapRebar", "tbl7", "tbl6"),
                CAP_REBAR_FIELDS,
            )
            pile_rebar = _existing_rebar(
                destination,
                ("FoundationPileRebar", "tbl8", "tbl5"),
                PILE_REBAR_FIELDS,
            )
            _drop_foundation_contract_tables(destination)
            _create_destination_schema(destination)

            for type_key in sorted(pile_types):
                item = pile_types[type_key]
                row = {
                    "ID": pile_type_ids[type_key],
                    "TypeKey": type_key,
                    "Diameter": item["diameter"],
                    "Length": item["length"],
                }
                row.update(
                    _restored_rebar(
                        pile_rebar,
                        type_key,
                        PILE_REBAR_FIELDS,
                        nullable_fields=("DenseZoneLength", "Cover"),
                    )
                )
                destination.execute(
                    """
                    INSERT INTO tbl5(
                        ID,TypeKey,Diameter,Length,UserTypeName,
                        LongitudinalRebar,StirrupRebar,DenseStirrupRebar,
                        DenseZoneLength,Cover,Notes,ExtraJson,UpdatedAt
                    ) VALUES (
                        :ID,:TypeKey,:Diameter,:Length,:UserTypeName,
                        :LongitudinalRebar,:StirrupRebar,
                        :DenseStirrupRebar,:DenseZoneLength,:Cover,:Notes,
                        :ExtraJson,:UpdatedAt
                    )
                    """,
                    row,
                )

            for type_key in sorted(cap_types):
                item = cap_types[type_key]
                pile_layout = [
                    {
                        "x": pile[0],
                        "y": pile[1],
                        "top_offset_z": pile[2],
                        "pile_type_id": pile_type_ids[pile[3]],
                    }
                    for pile in item["pile_layout"]
                ]
                row = {
                    "ID": cap_type_ids[type_key],
                    "TypeKey": type_key,
                    "PolygonJson": _json(item["polygon"]),
                    "Thickness": item["thickness"],
                    "PileLayoutJson": _json(pile_layout),
                }
                row.update(
                    _restored_rebar(
                        cap_rebar,
                        type_key,
                        CAP_REBAR_FIELDS,
                        nullable_fields=("Cover",),
                    )
                )
                destination.execute(
                    """
                    INSERT INTO tbl6(
                        ID,TypeKey,PolygonJson,Thickness,PileLayoutJson,
                        UserTypeName,BottomX,BottomY,TopX,TopY,SideRebar,
                        Cover,Notes,ExtraJson,UpdatedAt
                    ) VALUES (
                        :ID,:TypeKey,:PolygonJson,:Thickness,:PileLayoutJson,
                        :UserTypeName,:BottomX,:BottomY,:TopX,:TopY,:SideRebar,
                        :Cover,:Notes,:ExtraJson,:UpdatedAt
                    )
                    """,
                    row,
                )

            for item in placements:
                destination.execute(
                    "INSERT INTO tbl7 VALUES (?,?,?,?,?,?,?,?)",
                    (
                        item["x"],
                        item["y"],
                        item["bottom_z"],
                        item["rotation"],
                        cap_type_ids[item["cap_type_key"]],
                        0,
                        item["id"],
                        None,
                    ),
                )

            metadata = {
                "Foundation.SchemaVersion": str(FOUNDATION_SCHEMA_VERSION),
                "Foundation.DataSource": "YDB_ONLY",
                "Foundation.SourceFile": str(source_path),
                "Foundation.SourceSHA256": _source_sha256(source_path),
                "Foundation.ExtractedAt": _utc_now(),
                "Foundation.Scope": "ONE_STEP_POLYGON_CAPS_VERTICAL_PILES",
                "Foundation.ContractTables": "tbl5,tbl6,tbl7",
                "Foundation.ContractVersion": "PILE_TYPE_CAP_TYPE_CAP_PLACEMENT",
            }
            destination.executemany(
                "INSERT OR REPLACE INTO handoff_meta(Key,Value) VALUES (?,?)",
                sorted(metadata.items()),
            )
    finally:
        destination.close()

    return {
        "source": str(source_path),
        "destination": str(destination_path),
        "data_source": "YDB_ONLY",
        "cap_types": len(cap_types),
        "caps": len(placements),
        "pile_types": len(pile_types),
        "piles": pile_count,
    }


def convert_foundation_ydb(source_path, destination_path):
    """Atomically rebuild tbl5-tbl7 while preserving every other database object."""
    source_path = Path(source_path).expanduser().resolve()
    destination_path = Path(destination_path).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError("foundation YDB does not exist: " + str(source_path))
    if source_path == destination_path:
        raise ValueError("source YDB and destination database must be different files")
    return atomic_update_database(
        destination_path,
        FOUNDATION_MODE,
        lambda pending_path: _convert_foundation_ydb_in_place(
            source_path, pending_path
        ),
    )


def _dict_rows(connection, sql, parameters=()):
    connection.row_factory = sqlite3.Row
    return [dict(row) for row in connection.execute(sql, parameters)]


def read_editor_data(database_path):
    """Return current type geometry and supplemental reinforcement as JSON data."""
    connection = sqlite3.connect(str(Path(database_path).expanduser().resolve()))
    try:
        connection.row_factory = sqlite3.Row
        tables = _table_names(connection)
        required = {"tbl5", "tbl6", "tbl7", "handoff_meta"}
        if not required.issubset(tables):
            raise FoundationDataError("handoff database has no extracted foundation data")
        meta = {
            row["Key"].removeprefix("Foundation."): row["Value"]
            for row in connection.execute(
                "SELECT Key,Value FROM handoff_meta WHERE Key LIKE 'Foundation.%'"
            )
        }
        cap_types = _dict_rows(
            connection,
            "SELECT * FROM tbl6 ORDER BY ID",
        )
        pile_types = _dict_rows(
            connection,
            "SELECT * FROM tbl5 ORDER BY ID",
        )
        placement_counts = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT CapTypeID,COUNT(*) FROM tbl7 GROUP BY CapTypeID"
            )
        }
        pile_counts = defaultdict(int)
        for cap_type in cap_types:
            polygon = json.loads(cap_type["PolygonJson"])
            layout = json.loads(cap_type["PileLayoutJson"])
            instance_count = placement_counts.get(cap_type["ID"], 0)
            cap_type["VertexCount"] = len(polygon)
            cap_type["PileCount"] = len(layout)
            cap_type["InstanceCount"] = instance_count
            for pile in layout:
                pile_counts[int(pile["pile_type_id"])] += instance_count
        for pile_type in pile_types:
            pile_type["InstanceCount"] = pile_counts.get(pile_type["ID"], 0)
        return {
            "meta": meta,
            "summary": {
                "cap_types": len(cap_types),
                "caps": connection.execute("SELECT COUNT(*) FROM tbl7").fetchone()[0],
                "pile_types": len(pile_types),
                "piles": sum(pile_counts.values()),
            },
            "cap_types": cap_types,
            "pile_types": pile_types,
        }
    finally:
        connection.close()


def _normalise_text(value):
    return "" if value is None else str(value).strip()


def _normalise_optional_number(value, field_name):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise FoundationDataError(field_name + " must be a number")
    if number < 0:
        raise FoundationDataError(field_name + " cannot be negative")
    return number


def _normalise_extra_json(value):
    if value in (None, ""):
        return "{}"
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise FoundationDataError("ExtraJson is not valid JSON: " + str(error))
    else:
        parsed = value
    if not isinstance(parsed, dict):
        raise FoundationDataError("ExtraJson must contain a JSON object")
    return _json(parsed)


def update_cap_rebar(database_path, type_key, payload):
    values = {
        "UserTypeName": _normalise_text(payload.get("UserTypeName")),
        "BottomX": _normalise_text(payload.get("BottomX")),
        "BottomY": _normalise_text(payload.get("BottomY")),
        "TopX": _normalise_text(payload.get("TopX")),
        "TopY": _normalise_text(payload.get("TopY")),
        "SideRebar": _normalise_text(payload.get("SideRebar")),
        "Cover": _normalise_optional_number(payload.get("Cover"), "Cover"),
        "Notes": _normalise_text(payload.get("Notes")),
        "ExtraJson": _normalise_extra_json(payload.get("ExtraJson")),
        "UpdatedAt": _utc_now(),
    }
    connection = sqlite3.connect(str(Path(database_path).expanduser().resolve()))
    try:
        if connection.execute(
            "SELECT 1 FROM tbl6 WHERE TypeKey=?", (type_key,)
        ).fetchone() is None:
            raise FoundationDataError("unknown pile-cap TypeKey: " + type_key)
        with connection:
            connection.execute(
                """
                UPDATE tbl6 SET
                    UserTypeName=?, BottomX=?, BottomY=?, TopX=?, TopY=?,
                    SideRebar=?, Cover=?, Notes=?, ExtraJson=?, UpdatedAt=?
                WHERE TypeKey=?
                """,
                tuple(values.values()) + (type_key,),
            )
    finally:
        connection.close()
    return values


def update_pile_rebar(database_path, type_key, payload):
    values = {
        "UserTypeName": _normalise_text(payload.get("UserTypeName")),
        "LongitudinalRebar": _normalise_text(payload.get("LongitudinalRebar")),
        "StirrupRebar": _normalise_text(payload.get("StirrupRebar")),
        "DenseStirrupRebar": _normalise_text(payload.get("DenseStirrupRebar")),
        "DenseZoneLength": _normalise_optional_number(
            payload.get("DenseZoneLength"), "DenseZoneLength"
        ),
        "Cover": _normalise_optional_number(payload.get("Cover"), "Cover"),
        "Notes": _normalise_text(payload.get("Notes")),
        "ExtraJson": _normalise_extra_json(payload.get("ExtraJson")),
        "UpdatedAt": _utc_now(),
    }
    connection = sqlite3.connect(str(Path(database_path).expanduser().resolve()))
    try:
        if connection.execute(
            "SELECT 1 FROM tbl5 WHERE TypeKey=?", (type_key,)
        ).fetchone() is None:
            raise FoundationDataError("unknown pile TypeKey: " + type_key)
        with connection:
            connection.execute(
                """
                UPDATE tbl5 SET
                    UserTypeName=?, LongitudinalRebar=?, StirrupRebar=?,
                    DenseStirrupRebar=?, DenseZoneLength=?, Cover=?, Notes=?,
                    ExtraJson=?, UpdatedAt=?
                WHERE TypeKey=?
                """,
                tuple(values.values()) + (type_key,),
            )
    finally:
        connection.close()
    return values
