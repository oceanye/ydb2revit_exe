# coding: utf-8
import importlib.util
import json
import sqlite3
import struct
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ydb_converter", ROOT / "ydb转换.py")
CONVERTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONVERTER)


def build_packed_hot_rolled_value(section_id, name, height, width):
    """Build a Kind-26 packed subsection ShapeVal exactly like YJK 8.1 does."""
    blob = struct.pack("<H", 39) + name.encode("ascii").ljust(30, b"\x00")
    fields = [26]
    fields.extend(struct.unpack("<8I", blob))
    fields.append(int(width * 65536))    # [9]  翼缘宽 Q16
    fields.append(int(height * 65536))   # [10] 总高 Q16
    fields.extend([0, 0, 0])             # [11..13]
    fields.append(0)                     # [14] 自定义标志：标准热轧
    fields.append(327680)                # [15] 材料 Q16 = 5
    fields.extend([0] * (41 - len(fields)))
    fields.append(section_id)            # [41] 尾部截面 ID
    return ",".join(str(value) for value in fields) + ","


def build_packed_short_value(name, height, width):
    """Build the 11-field short Kind-26 packed form (handoff 2026-09-03)."""
    blob = struct.pack("<H", 39) + name.encode("ascii").ljust(10, b"\x00")
    blob = blob[:12]
    fields = [26]
    fields.extend(struct.unpack("<3I", blob))   # [1..3] 选择器+名称 12 字节
    fields.extend([0, 0, 0, 0, 0])              # [4..8]
    fields.append(int(width * 65536))           # [9]
    fields.append(int(height * 65536))          # [10]
    return ",".join(str(value) for value in fields) + ","


def convert_single_209_beam(main_dims, subsection=None, sect_id=903):
    """Convert a one-beam YDB with a configurable Kind-209 section.

    main_dims = (t, d, u, f) written to tblBeamSect; subsection is
    ("packed", shapeval_text) or ("numeric", (b, h, u, t, d, f)) or None.
    Returns (result dict, list of tbl1 BSection values).
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        source = Path(temp_dir) / "pec209.ydb"
        destination = Path(temp_dir) / "out.db"
        with closing(sqlite3.connect(str(source))) as connection:
            connection.executescript("""
                CREATE TABLE tblFloor (
                    ID INTEGER, No_ INTEGER, Name TEXT, StdFlrID INTEGER,
                    LevelB REAL, Height REAL
                );
                INSERT INTO tblFloor VALUES (1,1,'',10,0,3300);
                CREATE TABLE tblJoint (
                    ID INTEGER, No_ INTEGER, StdFlrID INTEGER,
                    X REAL, Y REAL, HDiff REAL
                );
                INSERT INTO tblJoint VALUES (1,1,10,0,0,0), (2,2,10,0,6000,0);
                CREATE TABLE tblGrid (
                    ID INTEGER, No_ INTEGER, StdFlrID INTEGER,
                    Jt1ID INTEGER, Jt2ID INTEGER
                );
                INSERT INTO tblGrid VALUES (11,1,10,1,2);
                CREATE TABLE tblBeamSect (
                    ID INTEGER, No_ INTEGER, Mat INTEGER, Kind INTEGER,
                    ShapeVal TEXT, b REAL, h REAL, u REAL, t REAL, d REAL, f REAL
                );
                CREATE TABLE tblBeamSeg (
                    ID INTEGER, No_ INTEGER, StdFlrID INTEGER, SectID INTEGER,
                    GridID INTEGER, HDiff1 REAL, HDiff2 REAL
                );
                INSERT INTO tblBeamSeg VALUES (503,1,10,%d,11,0,0);
                CREATE TABLE tblSubSectionSect (
                    ID INTEGER, Kind INTEGER, No_ INTEGER, SubKind INTEGER,
                    ShapeVal TEXT, b REAL, h REAL, u REAL, t REAL, d REAL, f REAL
                );
            """ % sect_id)
            t, d, u, f = main_dims
            connection.execute(
                "INSERT INTO tblBeamSect VALUES (?,?,0,209,?,0,0,?,?,?,?)",
                (sect_id, sect_id, "209,%d," % sect_id, u, t, d, f),
            )
            if subsection is not None:
                if subsection[0] == "packed":
                    connection.execute(
                        "INSERT INTO tblSubSectionSect VALUES (?,12,1,26,?,0,0,0,0,0,0)",
                        (sect_id, subsection[1]),
                    )
                else:
                    b, h_, u_, t_, d_, f_ = subsection[1]
                    connection.execute(
                        "INSERT INTO tblSubSectionSect VALUES (?,12,1,13,'',?,?,?,?,?,?)",
                        (sect_id, b, h_, u_, t_, d_, f_),
                    )
            connection.commit()
        result = CONVERTER.convert_ydb(str(source), str(destination))
        with closing(sqlite3.connect(str(destination))) as connection:
            sections = [
                row[0] for row in connection.execute("SELECT BSection FROM tbl1")
            ]
        return result, sections


def create_anonymous_pec_fixture(path):
    """Create the smallest YDB-shaped database needed for a public regression test."""
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript("""
            CREATE TABLE tblFloor (
                ID INTEGER, No_ INTEGER, Name TEXT, StdFlrID INTEGER,
                LevelB REAL, Height REAL
            );
            INSERT INTO tblFloor VALUES (1,1,'',10,0,3300);

            CREATE TABLE tblJoint (
                ID INTEGER, No_ INTEGER, StdFlrID INTEGER,
                X REAL, Y REAL, HDiff REAL
            );
            INSERT INTO tblJoint VALUES
                (1,1,10,0,0,0), (2,2,10,0,1000,0),
                (3,3,10,500,1000,0), (4,4,10,2000,0,0),
                (5,5,10,2000,1000,0);

            CREATE TABLE tblGrid (
                ID INTEGER, No_ INTEGER, StdFlrID INTEGER,
                Jt1ID INTEGER, Jt2ID INTEGER
            );
            INSERT INTO tblGrid VALUES
                (11,1,10,1,2), (12,2,10,2,3),
                (13,3,10,4,5), (14,4,10,3,4);

            CREATE TABLE tblBeamSect (
                ID INTEGER, No_ INTEGER, Mat INTEGER, Kind INTEGER,
                ShapeVal TEXT, b REAL, h REAL, u REAL, t REAL, d REAL, f REAL
            );
            INSERT INTO tblBeamSect VALUES
                (201,1,0,209,'209,201,',0,0,10,400,150,20);
            CREATE TABLE tblSubSectionSect (
                ID INTEGER, Kind INTEGER, No_ INTEGER, SubKind INTEGER,
                ShapeVal TEXT, b REAL, h REAL, u REAL, t REAL, d REAL, f REAL
            );
            INSERT INTO tblSubSectionSect VALUES
                (201,12,1,13,'',150,400,10,400,150,20);
            CREATE TABLE tblBeamSeg (
                ID INTEGER, No_ INTEGER, StdFlrID INTEGER, SectID INTEGER,
                GridID INTEGER, Ecc REAL, Ecc2 REAL,
                HDiff1 REAL, HDiff2 REAL, Rotation REAL
            );
            INSERT INTO tblBeamSeg VALUES (202,1,10,201,14,0,0,0,0,0);

            CREATE TABLE tblColSect (
                ID INTEGER, No_ INTEGER, Mat INTEGER, Kind INTEGER,
                ShapeVal TEXT, b REAL, h REAL, u REAL, t REAL, d REAL, f REAL
            );
            INSERT INTO tblColSect VALUES
                (301,1,5,2,'2,8,244,175,12,175,12,5,301,',8,244,175,12,175,12);
            CREATE TABLE tblColSeg (
                ID INTEGER, No_ INTEGER, StdFlrID INTEGER, SectID INTEGER,
                JtID INTEGER, EccX REAL, EccY REAL, Rotation REAL
            );
            INSERT INTO tblColSeg VALUES
                (311,1,10,301,1,0,122,10), (312,2,10,301,2,0,-35,20),
                (313,3,10,301,4,0,122,30), (314,4,10,301,5,0,-122,40);

            CREATE TABLE tblWallSect (
                ID INTEGER, No_ INTEGER, Mat INTEGER, Kind INTEGER,
                B REAL, H REAL, T2 REAL, Dis REAL, Dis1 REAL,
                colsect1 TEXT, colShapeVal1 TEXT,
                colsect2 TEXT, colShapeVal2 TEXT,
                Name TEXT, StateFlag INTEGER
            );
            INSERT INTO tblWallSect VALUES
                (101,1,6,211,175,6,1,175,6,'','','','','',0),
                (102,2,6,212,175,8,0,20,150,'','','','','',0);
            CREATE TABLE tblWallSeg (
                ID INTEGER, No_ INTEGER, StdFlrID INTEGER, SectID INTEGER,
                GridID INTEGER, Ecc REAL, HDiff1 REAL, HDiff2 REAL,
                HDiffB REAL, sloping INTEGER, EccDown REAL,
                offset1 REAL, offset2 REAL, HDiffB2 REAL, WallJY TEXT,
                NoSlab INTEGER, Prefix TEXT, No TEXT, Suffix TEXT,
                StateFlag INTEGER
            );
            INSERT INTO tblWallSeg VALUES
                (111,1,10,101,11,0,0,0,0,0,0,0,0,0,'0',0,'','','',0),
                (112,2,10,102,12,0,0,0,0,0,0,0,0,0,'0',0,'','','',0),
                (113,3,10,101,13,0,0,0,0,0,0,0,0,0,'0',0,'','','',0);
        """)
        connection.commit()


class PecConversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.pec_source = Path(cls.temp_dir.name) / "anonymous_pec.ydb"
        cls.pec_output = Path(cls.temp_dir.name) / "pec.db"
        cls.normal_output = Path(cls.temp_dir.name) / "normal.db"
        create_anonymous_pec_fixture(cls.pec_source)
        CONVERTER.convert_ydb(
            cls.pec_source,
            cls.pec_output,
        )
        CONVERTER.convert_ydb(
            ROOT / "施工图-墙" / "dtlmodelsw.ydb",
            cls.normal_output,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def test_existing_column_positions_are_unchanged(self):
        expected = [
            "WStartX", "WStartY", "WStartZ", "WEndX", "WEndY", "WEndZ",
            "WSection", "Tag", "ID", "RvtID", "BottomFloor", "WEConn",
        ]
        with closing(sqlite3.connect(self.pec_output)) as connection:
            columns = [row[1] for row in connection.execute("PRAGMA table_info(tbl4)")]
        self.assertEqual(expected, columns[:12])
        self.assertEqual(
            ["WGroupID", "WLegID", "WLegRole", "WShape", "WInfo"],
            columns[12:],
        )

    def test_pec_beam_and_main_wall_end_columns_use_h_suffix(self):
        with closing(sqlite3.connect(self.pec_output)) as connection:
            beam_sections = [row[0] for row in connection.execute("SELECT BSection FROM tbl1")]
            column_sections = [row[0] for row in connection.execute("SELECT CSection FROM tbl2")]
            column_columns = [row[1] for row in connection.execute("PRAGMA table_info(tbl2)")]
        self.assertEqual(["H400X150X10X20@PEC"], beam_sections)
        self.assertEqual(["H244X175X8X12@PEC"] * 4, column_sections)
        self.assertFalse(any(name.startswith("Ydb") for name in column_columns))

    def test_l_and_i_wall_groups_are_traceable(self):
        with closing(sqlite3.connect(self.pec_output)) as connection:
            rows = connection.execute(
                "SELECT WStartX,WStartY,WEndX,WEndY,WGroupID,WLegID,WLegRole,WShape,WInfo "
                "FROM tbl4 ORDER BY ID"
            ).fetchall()
        self.assertEqual(3, len(rows))
        l_rows = [row for row in rows if row[7] == "L"]
        i_rows = [row for row in rows if row[7] == "I"]
        self.assertEqual(2, len(l_rows))
        self.assertEqual(1, len(i_rows))
        self.assertEqual({"PECW0001"}, {row[4] for row in l_rows})
        self.assertEqual({"PECW0001-L1", "PECW0001-L2"}, {row[5] for row in l_rows})
        self.assertEqual({"MAIN", "SECONDARY"}, {row[6] for row in l_rows})
        self.assertEqual((l_rows[0][0], l_rows[0][1]), (l_rows[1][0], l_rows[1][1]))
        self.assertEqual("PECW0002-L1", i_rows[0][5])
        self.assertEqual("MAIN", i_rows[0][6])

    def test_wall_info_is_valid_and_complete_for_current_sample(self):
        with closing(sqlite3.connect(self.pec_output)) as connection:
            infos = [json.loads(row[0]) for row in connection.execute(
                "SELECT WInfo FROM tbl4 WHERE WInfo IS NOT NULL ORDER BY ID"
            )]
            tbl2_ids = {
                row[0] for row in connection.execute("SELECT ID FROM tbl2")
            }
            index_names = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
        self.assertEqual(3, len(infos))
        self.assertEqual(211, infos[0]["source_parameters"]["section"]["Kind"])
        self.assertEqual(6.0, infos[0]["source_parameters"]["section"]["Dis1"])
        self.assertEqual(212, infos[1]["source_parameters"]["section"]["Kind"])
        self.assertEqual(150.0, infos[1]["source_parameters"]["section"]["Dis1"])
        self.assertEqual(4, infos[0]["version"])
        self.assertEqual({"start": 2, "end": 1}, infos[0]["tbl2_column_refs"])
        self.assertEqual({"connected_main": 2}, infos[1]["tbl2_column_refs"])
        self.assertEqual({"start": 3, "end": 4}, infos[2]["tbl2_column_refs"])
        referenced_ids = {
            value
            for info in infos
            for value in info["tbl2_column_refs"].values()
            if value is not None
        }
        self.assertTrue(referenced_ids.issubset(tbl2_ids))
        self.assertTrue(
            {"idx_tbl2_id", "idx_tbl4_group", "idx_tbl4_leg"}.issubset(index_names)
        )
        main_steel = infos[0]["steel_configuration"]
        self.assertEqual("I", main_steel["cross_section_form"])
        self.assertEqual(6.0, main_steel["web_thickness_mm"])
        self.assertEqual(4, main_steel["partition_count"])
        self.assertEqual(1, main_steel["internal_stiffener"]["count"])
        self.assertEqual(175.0, main_steel["internal_stiffener"]["width_mm"])
        self.assertEqual(6.0, main_steel["internal_stiffener"]["thickness_mm"])
        secondary_steel = infos[1]["steel_configuration"]
        self.assertEqual("T", secondary_steel["cross_section_form"])
        self.assertEqual(8.0, secondary_steel["web_thickness_mm"])
        self.assertEqual(20.0, secondary_steel["flange_thickness_mm"])
        self.assertNotIn("flange", secondary_steel)
        for removed_key in ("layout", "concrete_outer", "boundary_h", "modeling"):
            self.assertNotIn(removed_key, infos[0])
        self.assertNotIn("tail_connection", secondary_steel)

    def test_main_wall_partition_mapping_covers_three_four_and_five(self):
        for source_t2, partitions, stiffeners in ((0, 3, 0), (1, 4, 1), (2, 5, 2)):
            section = {"H": 6, "T2": source_t2, "Dis": 175, "Dis1": 6}
            config = CONVERTER._main_wall_steel_configuration(section)
            self.assertEqual(partitions, config["partition_count"])
            self.assertEqual(stiffeners, config["internal_stiffener"]["count"])

    def test_secondary_minimal_parameter_mapping(self):
        section = {"B": 300, "H": 12, "Dis": 14, "Dis1": 150}
        config = CONVERTER._secondary_wall_steel_configuration(section)
        self.assertEqual({
            "cross_section_form": "T",
            "web_thickness_mm": 12,
            "flange_thickness_mm": 14,
        }, config)

    def test_unpaired_secondary_has_no_false_h_reference(self):
        source = Path(self.temp_dir.name) / "unpaired_secondary.ydb"
        output = Path(self.temp_dir.name) / "unpaired_secondary.db"
        with closing(sqlite3.connect(self.pec_source)) as original, closing(
            sqlite3.connect(source)
        ) as connection:
            original.backup(connection)
            connection.execute("DELETE FROM tblWallSeg WHERE SectID=101")
            connection.commit()
        CONVERTER.convert_ydb(source, output)
        with closing(sqlite3.connect(output)) as connection:
            raw_info = connection.execute(
                "SELECT WInfo FROM tbl4 WHERE WLegRole='SECONDARY'"
            ).fetchone()[0]
        info = json.loads(raw_info)
        self.assertEqual({"connected_main": None}, info["tbl2_column_refs"])
        self.assertIn("warning", info)

    def test_wall_end_h_placement_values_are_not_read_from_ydb(self):
        with closing(sqlite3.connect(self.pec_output)) as connection:
            placement_values = connection.execute(
                "SELECT EccX,EccY,Rotation FROM tbl2 ORDER BY ID"
            ).fetchall()
        self.assertEqual([(0.0, 0.0, 0.0)] * 4, placement_values)

    def test_tbl2_references_follow_each_natural_floor_instance(self):
        source = Path(self.temp_dir.name) / "reused_standard_floor.ydb"
        output = Path(self.temp_dir.name) / "reused_standard_floor.db"
        with closing(sqlite3.connect(self.pec_source)) as original, closing(
            sqlite3.connect(source)
        ) as connection:
            original.backup(connection)
            connection.execute(
                "INSERT INTO tblFloor VALUES (2,2,'',10,3300,3300)"
            )
            connection.commit()
        CONVERTER.convert_ydb(source, output)
        with closing(sqlite3.connect(output)) as connection:
            rows = connection.execute(
                "SELECT WStartZ,WInfo FROM tbl4 WHERE WInfo IS NOT NULL ORDER BY ID"
            ).fetchall()
        first_floor_refs = {
            value
            for z_value, raw_info in rows
            if z_value == 0
            for value in json.loads(raw_info)["tbl2_column_refs"].values()
            if value is not None
        }
        second_floor_refs = {
            value
            for z_value, raw_info in rows
            if z_value == 3300
            for value in json.loads(raw_info)["tbl2_column_refs"].values()
            if value is not None
        }
        self.assertEqual({1, 2, 3, 4}, first_floor_refs)
        self.assertEqual({5, 6, 7, 8}, second_floor_refs)

    def test_true_standalone_pec_column_still_uses_h_suffix(self):
        source = Path(self.temp_dir.name) / "standalone_pec_column.ydb"
        output = Path(self.temp_dir.name) / "standalone_pec_column.db"
        with closing(sqlite3.connect(self.pec_source)) as original, closing(
            sqlite3.connect(source)
        ) as connection:
            original.backup(connection)
            connection.execute(
                "INSERT INTO tblJoint VALUES (6,6,10,3000,3000,0)"
            )
            connection.execute(
                "INSERT INTO tblColSect VALUES "
                "(302,2,0,209,'209,302,',0,0,8,400,200,16)"
            )
            connection.execute(
                "INSERT INTO tblSubSectionSect VALUES "
                "(302,12,2,13,'',200,400,8,400,200,16)"
            )
            connection.execute(
                "INSERT INTO tblColSeg VALUES (315,5,10,302,6,7,8,9)"
            )
            connection.commit()
        CONVERTER.convert_ydb(source, output)
        with closing(sqlite3.connect(output)) as connection:
            rows = connection.execute(
                "SELECT CSection,EccX,EccY,Rotation FROM tbl2 ORDER BY ID"
            ).fetchall()
            sections = [row[0] for row in rows]
        self.assertEqual(["H244X175X8X12@PEC"] * 4, sections[:4])
        self.assertEqual("H400X200X8X16@PEC", sections[4])
        self.assertEqual((7.0, 8.0, 9.0), rows[4][1:])

    def test_kind2_column_at_secondary_outer_end_is_not_a_main_end_column(self):
        source = Path(self.temp_dir.name) / "secondary_outer_column.ydb"
        output = Path(self.temp_dir.name) / "secondary_outer_column.db"
        with closing(sqlite3.connect(self.pec_source)) as original, closing(
            sqlite3.connect(source)
        ) as connection:
            original.backup(connection)
            connection.execute(
                "INSERT INTO tblColSeg VALUES (315,5,10,301,3,0,0,0)"
            )
            connection.commit()
        CONVERTER.convert_ydb(source, output)
        with closing(sqlite3.connect(output)) as connection:
            section = connection.execute(
                "SELECT CSection FROM tbl2 ORDER BY ID DESC LIMIT 1"
            ).fetchone()[0]
        self.assertFalse(section.endswith("@PEC"))

    def test_legacy_wall_sample_without_dis1_still_converts(self):
        with closing(sqlite3.connect(self.normal_output)) as connection:
            wall_count = connection.execute("SELECT count(*) FROM tbl4").fetchone()[0]
            pec_count = connection.execute(
                "SELECT count(*) FROM tbl4 WHERE WShape IS NOT NULL OR WInfo IS NOT NULL"
            ).fetchone()[0]
            subsection_exists = connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='tblSubSectionSect'"
            ).fetchone()[0]
        self.assertEqual(189, wall_count)
        self.assertEqual(0, pec_count)
        self.assertEqual(0, subsection_exists)

    @staticmethod
    def _convert_floors_fixture(floors):
        """Convert a floors-only YDB and return the tbl3 rows (name, elevation)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "floors.ydb"
            destination = Path(temp_dir) / "out.db"
            with closing(sqlite3.connect(str(source))) as connection:
                connection.executescript("""
                    CREATE TABLE tblFloor (
                        ID INTEGER, No_ INTEGER, Name TEXT, StdFlrID INTEGER,
                        LevelB REAL, Height REAL
                    );
                    CREATE TABLE tblJoint (
                        ID INTEGER, No_ INTEGER, StdFlrID INTEGER,
                        X REAL, Y REAL, HDiff REAL
                    );
                    CREATE TABLE tblGrid (
                        ID INTEGER, No_ INTEGER, StdFlrID INTEGER,
                        Jt1ID INTEGER, Jt2ID INTEGER
                    );
                """)
                connection.executemany(
                    "INSERT INTO tblFloor VALUES (?,?,'',?, ?, ?)",
                    [(i, i, i, level_b, height)
                     for i, (level_b, height) in enumerate(floors, start=1)],
                )
                connection.commit()
            CONVERTER.convert_ydb(str(source), str(destination))
            with closing(sqlite3.connect(str(destination))) as connection:
                return [
                    (name, round(elevation, 6))
                    for name, elevation in connection.execute(
                        "SELECT Floor,LevelB FROM tbl3"
                    )
                ]

    def test_tbl3_includes_detached_intermediate_tops(self):
        # Multi-tower shape from handoff-Python端-tbl3标高集合多塔缺口.md:
        # an absorbed top, a detached (sloped-roof) top, a duplicated bottom
        # and a tower restart must all appear in the level set exactly once.
        rows = self._convert_floors_fixture([
            (0.0, 3000.0),     # top 3000 absorbed by the next bottom
            (3000.0, 2295.0),  # top 5295 detached -> RF2
            (5100.0, 2800.0),  # tower restart; top 7900 detached -> RF3
            (5100.0, 1000.0),  # duplicated bottom deduplicated; last top -> RF
        ])
        self.assertEqual(
            [("1F", 0.0), ("2F", 3000.0), ("3F", 5100.0),
             ("RF", 6100.0), ("RF2", 5295.0), ("RF3", 7900.0)],
            rows,
        )
        names = [name for name, _ in rows]
        self.assertEqual(len(names), len(set(names)), "level names must be unique")
        self.assertEqual(1, names.count("RF"))

    def test_tbl3_continuous_model_output_unchanged(self):
        # In a continuous single-tower model every interior top equals the next
        # bottom, so tbl3 must stay identical to the legacy bottoms+RF output.
        rows = self._convert_floors_fixture([
            (0.0, 3000.0),
            (3000.0, 3300.0),
            (6300.0, 3600.0),
        ])
        self.assertEqual(
            [("1F", 0.0), ("2F", 3000.0), ("3F", 6300.0), ("RF", 9900.0)],
            rows,
        )

    def test_dimensionless_pec_section_fails_explicitly(self):
        # A Kind-209 section with no dimensions anywhere must abort the
        # conversion naming the section, never emit an unparseable @PEC string.
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "dimless.ydb"
            destination = Path(temp_dir) / "out.db"
            with closing(sqlite3.connect(str(source))) as connection:
                connection.executescript("""
                    CREATE TABLE tblFloor (
                        ID INTEGER, No_ INTEGER, Name TEXT, StdFlrID INTEGER,
                        LevelB REAL, Height REAL
                    );
                    INSERT INTO tblFloor VALUES (1,1,'',10,0,3300);
                    CREATE TABLE tblJoint (
                        ID INTEGER, No_ INTEGER, StdFlrID INTEGER,
                        X REAL, Y REAL, HDiff REAL
                    );
                    INSERT INTO tblJoint VALUES (1,1,10,0,0,0), (2,2,10,0,6000,0);
                    CREATE TABLE tblGrid (
                        ID INTEGER, No_ INTEGER, StdFlrID INTEGER,
                        Jt1ID INTEGER, Jt2ID INTEGER
                    );
                    INSERT INTO tblGrid VALUES (11,1,10,1,2);
                    CREATE TABLE tblBeamSect (
                        ID INTEGER, No_ INTEGER, Mat INTEGER, Kind INTEGER,
                        ShapeVal TEXT, b REAL, h REAL, u REAL, t REAL, d REAL, f REAL
                    );
                    INSERT INTO tblBeamSect VALUES (901,1,0,209,'209,901,',
                        0,0,0,0,0,0);
                    CREATE TABLE tblBeamSeg (
                        ID INTEGER, No_ INTEGER, StdFlrID INTEGER, SectID INTEGER,
                        GridID INTEGER, HDiff1 REAL, HDiff2 REAL
                    );
                    INSERT INTO tblBeamSeg VALUES (501,1,10,901,11,0,0);
                    CREATE TABLE tblSubSectionSect (
                        ID INTEGER, Kind INTEGER, No_ INTEGER, SubKind INTEGER,
                        ShapeVal TEXT, b REAL, h REAL, u REAL, t REAL, d REAL, f REAL
                    );
                """)
                connection.commit()
            with self.assertRaises(ValueError) as caught:
                CONVERTER.convert_ydb(str(source), str(destination))
            message = str(caught.exception)
            self.assertIn("901", message)
            self.assertIn("PEC", message)
            self.assertFalse(Path(destination).exists())

    def test_packed_hot_rolled_subsection_decodes(self):
        # 颛桥实测形态：209 主表尺寸全零，尺寸藏在子表 ShapeVal 的 Kind-26
        # 打包串里（选择器 39 + 名称 HN400X200 + Q16 H/B），厚度取国标规格表。
        packed = build_packed_hot_rolled_value(902, "HN400X200", 400, 200)
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "packed.ydb"
            destination = Path(temp_dir) / "out.db"
            with closing(sqlite3.connect(str(source))) as connection:
                connection.executescript("""
                    CREATE TABLE tblFloor (
                        ID INTEGER, No_ INTEGER, Name TEXT, StdFlrID INTEGER,
                        LevelB REAL, Height REAL
                    );
                    INSERT INTO tblFloor VALUES (1,1,'',10,0,3300);
                    CREATE TABLE tblJoint (
                        ID INTEGER, No_ INTEGER, StdFlrID INTEGER,
                        X REAL, Y REAL, HDiff REAL
                    );
                    INSERT INTO tblJoint VALUES (1,1,10,0,0,0), (2,2,10,0,6000,0);
                    CREATE TABLE tblGrid (
                        ID INTEGER, No_ INTEGER, StdFlrID INTEGER,
                        Jt1ID INTEGER, Jt2ID INTEGER
                    );
                    INSERT INTO tblGrid VALUES (11,1,10,1,2);
                    CREATE TABLE tblBeamSect (
                        ID INTEGER, No_ INTEGER, Mat INTEGER, Kind INTEGER,
                        ShapeVal TEXT, b REAL, h REAL, u REAL, t REAL, d REAL, f REAL
                    );
                    INSERT INTO tblBeamSect VALUES (902,1,0,209,'209,902,',
                        0,0,0,0,0,0);
                    CREATE TABLE tblBeamSeg (
                        ID INTEGER, No_ INTEGER, StdFlrID INTEGER, SectID INTEGER,
                        GridID INTEGER, HDiff1 REAL, HDiff2 REAL
                    );
                    INSERT INTO tblBeamSeg VALUES (502,1,10,902,11,0,0);
                    CREATE TABLE tblSubSectionSect (
                        ID INTEGER, Kind INTEGER, No_ INTEGER, SubKind INTEGER,
                        ShapeVal TEXT, b REAL, h REAL, u REAL, t REAL, d REAL, f REAL
                    );
                """)
                connection.execute(
                    "INSERT INTO tblSubSectionSect VALUES (902,12,1,26,?,0,0,0,0,0,0)",
                    (packed,),
                )
                connection.commit()
            CONVERTER.convert_ydb(str(source), str(destination))
            with closing(sqlite3.connect(str(destination))) as connection:
                sections = [
                    row[0] for row in connection.execute(
                        "SELECT BSection FROM tbl1"
                    )
                ]
        self.assertEqual(["H400X200X8X13@PEC"], sections)

    def test_subsection_overrides_stale_main_values(self):
        # 1117 类矛盾数据（handoff 2026-09-03）：主表 t/d/u/f 是陈旧值
        # H350x150x6x11，子表 Kind-26 打包串为 HW200X200（YJK 界面所据）。
        # 优先级规则：子表解码成功且校验通过 → 覆盖主表。
        packed = build_packed_hot_rolled_value(903, "HW200X200", 200, 200)
        result, sections = convert_single_209_beam(
            (350.0, 150.0, 6.0, 11.0), ("packed", packed)
        )
        self.assertEqual(["H200X200X8X12@PEC"], sections)
        self.assertEqual([], result["warnings"])

    def test_packed_short_format_decodes(self):
        # 短格式 11 字段子串（handoff §2.1）：[1..3]=选择器+名称 12 字节，
        # [9]/[10]=b/h Q16；无 [14] 自定义标志与 [41] 尾部 ID 校验。
        short = build_packed_short_value("HW200X200", 200, 200)
        self.assertEqual(11, len(short.strip(",").split(",")))
        result, sections = convert_single_209_beam(
            (0.0, 0.0, 0.0, 0.0), ("packed", short)
        )
        self.assertEqual(["H200X200X8X12@PEC"], sections)
        self.assertEqual([], result["warnings"])

    def test_main_value_fallback_is_named_in_warnings(self):
        # 无子表定义的 209 截面（如颛桥 12489 等 5 个）：仍取主表数值转换，
        # 但必须在 result["warnings"] 中逐个点名，不静默。
        result, sections = convert_single_209_beam((350.0, 150.0, 10.0, 16.0))
        self.assertEqual(["H350X150X10X16@PEC"], sections)
        self.assertEqual(1, len(result["warnings"]))
        self.assertIn("SectID=903", result["warnings"][0])

    def test_section_text_contract_matches_csharp_parser(self):
        # 契约权威来源：CreateNewExtern/SectionTextParser.cs（只读参考仓库
        # E:\revit-external-tool2.git）。此处锁定 Python 端与之一致的行为。
        parse = CONVERTER.parse_h_section_text
        self.assertEqual(
            (400, 200, 8, 13, True),
            parse("H400X200X8X13@PEC"),
        )
        self.assertEqual(                      # 小写 x 同样接受（C# 忽略大小写）
            parse("H400x200x8x13@PEC"),
            parse("H400X200X8X13@PEC"),
        )
        self.assertEqual(                      # × 与 * 分隔符
            parse("H400×200×8×13@PEC"),
            parse("H400*200*8*13@PEC"),
        )
        self.assertEqual(                      # 旧 C# 输出的末尾多余 X
            parse("H400X200X8X13X@PEC"),
            (400, 200, 8, 13, True),
        )
        self.assertEqual(
            (400, 200, 8, 13, False),
            parse("H400X200X8X13"),
        )
        self.assertIsNone(parse("209,33506@PEC"))
        self.assertIsNone(parse("H400X200X0X13@PEC"))   # 尺寸必须为正
        self.assertTrue(CONVERTER.has_pec_suffix("h400x200x8x13@pec"))
        self.assertFalse(CONVERTER.has_pec_suffix("H400X200X8X13"))
        self.assertEqual("209,33506", CONVERTER.remove_pec_suffix("209,33506@PEC"))
        # 规范输出：大写 X、0.### 数字格式
        self.assertEqual(
            "H300X150X6.5X9@PEC",
            CONVERTER.format_h_section(300, 150, 6.5, 9, pec=True),
        )
        self.assertEqual(
            "H400X200X8X13",
            CONVERTER.format_h_section(400.0, 200.0, 8.0, 13.0, pec=False),
        )
        # 同尺寸 PEC 与普通 H 名称不同（梁合并不得视为同一截面）
        self.assertNotEqual(
            CONVERTER.format_h_section(400, 200, 8, 13, pec=True),
            CONVERTER.format_h_section(400, 200, 8, 13, pec=False),
        )


if __name__ == "__main__":
    unittest.main()
