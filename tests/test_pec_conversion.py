# coding: utf-8
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ydb_converter", ROOT / "ydb转换.py")
CONVERTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONVERTER)


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
                (311,1,10,301,1,0,122,0), (312,2,10,301,2,0,-35,0),
                (313,3,10,301,4,0,122,0), (314,4,10,301,5,0,-122,0);

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

    def test_pec_beam_and_columns_use_h_suffix(self):
        with closing(sqlite3.connect(self.pec_output)) as connection:
            beam_sections = [row[0] for row in connection.execute("SELECT BSection FROM tbl1")]
            column_sections = [row[0] for row in connection.execute("SELECT CSection FROM tbl2")]
            column_columns = [row[1] for row in connection.execute("PRAGMA table_info(tbl2)")]
        self.assertEqual(["H400x150x10x20@PEC"], beam_sections)
        self.assertEqual(["H244x175x8x12@PEC"] * 4, column_sections)
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
        self.assertEqual(3, len(infos))
        self.assertEqual(211, infos[0]["section_parameters"]["Kind"])
        self.assertEqual(6.0, infos[0]["section_parameters"]["Dis1"])
        self.assertEqual(212, infos[1]["section_parameters"]["Kind"])
        self.assertEqual(150.0, infos[1]["section_parameters"]["Dis1"])
        self.assertEqual("H244x175x8x12", infos[0]["boundary_h"]["start"][0]["section"])
        self.assertFalse(infos[0]["modeling"]["create_wall_internal_h"])

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


if __name__ == "__main__":
    unittest.main()
