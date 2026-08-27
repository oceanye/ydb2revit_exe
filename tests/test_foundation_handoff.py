# coding: utf-8
import importlib.util
import io
import json
import math
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from foundation_handoff import (
    FoundationDataError,
    convert_foundation_ydb,
    read_editor_data,
    update_cap_rebar,
)
from foundation_web import _handler_factory
from http.server import ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[1]
CONVERTER_SPEC = importlib.util.spec_from_file_location(
    "foundation_cli_converter", ROOT / "ydb转换.py"
)
CONVERTER = importlib.util.module_from_spec(CONVERTER_SPEC)
CONVERTER_SPEC.loader.exec_module(CONVERTER)


def create_foundation_fixture(path):
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE DEF_dais (
                ID INTEGER PRIMARY KEY, lID INTEGER, DaisFlag INTEGER,
                nstep INTEGER, npile INTEGER
            );
            INSERT INTO DEF_dais VALUES
                (1,0,0,1,2),
                (2,1,1,1,2);

            CREATE TABLE dais_pt (
                ID INTEGER PRIMARY KEY, DaisFlag INTEGER, nstep INTEGER,
                x REAL, y REAL, z INTEGER
            );
            INSERT INTO dais_pt VALUES
                (1,0,0,-1000,-2500,0), (2,0,0,1000,-2500,0),
                (3,0,0,1000,2500,0),  (4,0,0,-1000,2500,0),
                (5,1,0,-2500,1000,0), (6,1,0,-2500,-1000,0),
                (7,1,0,2500,-1000,0), (8,1,0,2500,1000,0);

            CREATE TABLE dais_stepH (
                ID INTEGER PRIMARY KEY, DaisFlag INTEGER, nstep INTEGER, H INTEGER
            );
            INSERT INTO dais_stepH VALUES (1,0,0,2200),(2,1,0,2200);
            CREATE TABLE dais_Cir (ID INTEGER PRIMARY KEY, DaisFlag INTEGER, D REAL);

            CREATE TABLE node (
                ID INTEGER PRIMARY KEY, X REAL, Y REAL, Z REAL
            );
            INSERT INTO node VALUES (1,10000,20000,-70),(2,30000,40000,-70);

            CREATE TABLE app_dais (
                ID INTEGER PRIMARY KEY, kind INTEGER, nj INTEGER,
                ex REAL, ey REAL, ang REAL, idaispilelen REAL,
                dBotElevat REAL, isBGAbs INTEGER
            );
            INSERT INTO app_dais VALUES
                (1,0,0,100,-200,0,31,-2.5,1),
                (2,1,1,0,0,1.5707963267948966,31,-2.17,1);

            CREATE TABLE DEF_Pile (
                ID INTEGER PRIMARY KEY, B INTEGER, H INTEGER
            );
            INSERT INTO DEF_Pile VALUES (1,1000,0);

            CREATE TABLE app_Pile (
                ID INTEGER PRIMARY KEY, x REAL, y REAL, z INTEGER,
                kind INTEGER, ang REAL, DaisFlag INTEGER,
                fKn INTEGER, fKm INTEGER, fALFQ INTEGER
            );
            INSERT INTO app_Pile VALUES
                (1,0,-1500,0,1,0,0,0,0,0),
                (2,0,1500,0,1,0,0,0,0,0),
                (3,-1500,0,0,1,0,1,0,0,0),
                (4,1500,0,0,1,0,1,0,0,0);
            """
        )
        connection.commit()
    finally:
        connection.close()


class FoundationHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.source = Path(self.temp_dir.name) / "foundation.ydb"
        self.output = Path(self.temp_dir.name) / "handoff.db"
        create_foundation_fixture(self.source)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_same_dimensions_are_merged_and_instances_remain_exact(self):
        summary = convert_foundation_ydb(self.source, self.output)
        self.assertEqual("foundation", summary["mode"])
        self.assertEqual("success", summary["status"])
        self.assertIn("protected_sha256", summary)
        self.assertEqual(1, summary["cap_types"])
        self.assertEqual(2, summary["caps"])
        self.assertEqual(1, summary["pile_types"])
        self.assertEqual(4, summary["piles"])
        self.assertEqual("YDB_ONLY", summary["data_source"])

        connection = sqlite3.connect(self.output)
        connection.row_factory = sqlite3.Row
        try:
            self.assertEqual(
                [
                    "ID", "TypeKey", "Diameter", "Length", "UserTypeName",
                    "LongitudinalRebar", "StirrupRebar",
                    "DenseStirrupRebar", "DenseZoneLength", "Cover", "Notes",
                    "ExtraJson", "UpdatedAt",
                ],
                [row[1] for row in connection.execute("PRAGMA table_info(tbl5)")],
            )
            self.assertEqual(
                [
                    "ID", "TypeKey", "PolygonJson", "Thickness",
                    "PileLayoutJson", "UserTypeName", "BottomX", "BottomY",
                    "TopX", "TopY", "SideRebar", "Cover", "Notes",
                    "ExtraJson", "UpdatedAt",
                ],
                [row[1] for row in connection.execute("PRAGMA table_info(tbl6)")],
            )
            self.assertEqual(
                ["X", "Y", "BottomZ", "Rotation", "CapTypeID", "Tag", "ID", "RvtID"],
                [row[1] for row in connection.execute("PRAGMA table_info(tbl7)")],
            )

            pile_type = connection.execute("SELECT * FROM tbl5").fetchone()
            self.assertEqual(1000, pile_type["Diameter"])
            self.assertEqual(31000, pile_type["Length"])

            cap_type = connection.execute("SELECT * FROM tbl6").fetchone()
            self.assertEqual(2200, cap_type["Thickness"])
            local_polygon = json.loads(cap_type["PolygonJson"])
            pile_layout = json.loads(cap_type["PileLayoutJson"])
            self.assertEqual(4, len(local_polygon))
            self.assertEqual(2, len(pile_layout))
            signed_area = 0.5 * sum(
                local_polygon[index][0] * local_polygon[(index + 1) % len(local_polygon)][1]
                - local_polygon[(index + 1) % len(local_polygon)][0] * local_polygon[index][1]
                for index in range(len(local_polygon))
            )
            self.assertGreater(signed_area, 0)
            self.assertEqual(
                {pile_type["ID"]},
                {
                    item["pile_type_id"]
                    for item in pile_layout
                },
            )
            placement = connection.execute("SELECT * FROM tbl7 WHERE ID=1").fetchone()
            self.assertEqual(cap_type["ID"], placement["CapTypeID"])
            self.assertEqual(-2500, placement["BottomZ"])

            angle = math.radians(placement["Rotation"])
            cosine, sine = math.cos(angle), math.sin(angle)
            transform = lambda x, y: (
                round(placement["X"] + x * cosine - y * sine, 6),
                round(placement["Y"] + x * sine + y * cosine, 6),
            )
            world_polygon = {
                transform(x, y) for x, y in local_polygon
            }
            self.assertEqual(
                {(9100, 17300), (11100, 17300), (11100, 22300), (9100, 22300)},
                world_polygon,
            )
            world_piles = {
                transform(item["x"], item["y"])
                for item in pile_layout
            }
            self.assertEqual({(10100, 18300), (10100, 21300)}, world_piles)
            editor_data = read_editor_data(self.output)
            self.assertEqual(2, editor_data["cap_types"][0]["InstanceCount"])
            self.assertEqual(4, editor_data["pile_types"][0]["InstanceCount"])
        finally:
            connection.close()

    def test_rebar_survives_repeat_ydb_extraction(self):
        convert_foundation_ydb(self.source, self.output)
        data = read_editor_data(self.output)
        type_key = data["cap_types"][0]["TypeKey"]
        update_cap_rebar(
            self.output,
            type_key,
            {
                "UserTypeName": "CT-A",
                "BottomX": "C20@150",
                "BottomY": "C20@150",
                "Cover": 50,
                "ExtraJson": {"reviewed": True},
            },
        )
        convert_foundation_ydb(self.source, self.output)
        item = read_editor_data(self.output)["cap_types"][0]
        self.assertEqual("CT-A", item["UserTypeName"])
        self.assertEqual("C20@150", item["BottomX"])
        self.assertEqual(50, item["Cover"])
        self.assertEqual({"reviewed": True}, json.loads(item["ExtraJson"]))

    def test_foundation_extends_existing_intermediate_database(self):
        connection = sqlite3.connect(self.output)
        try:
            for number in range(1, 5):
                connection.execute(
                    "CREATE TABLE tbl{} (Value TEXT)".format(number)
                )
                connection.execute(
                    "INSERT INTO tbl{} VALUES (?)".format(number),
                    ("keep-{}".format(number),),
                )
            connection.execute("CREATE TABLE tbl8 (Value TEXT)")
            connection.execute("INSERT INTO tbl8 VALUES ('keep-tbl8')")
            connection.execute("CREATE TABLE CombineBeam (Value TEXT)")
            connection.execute("INSERT INTO CombineBeam VALUES ('keep-combine')")
            connection.execute(
                "CREATE TABLE handoff_meta (Key TEXT PRIMARY KEY, Value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO handoff_meta VALUES ('Upper.Keep','unchanged')"
            )
            connection.commit()
        finally:
            connection.close()

        convert_foundation_ydb(self.source, self.output)
        connection = sqlite3.connect(self.output)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertTrue(
                {
                    "tbl1", "tbl2", "tbl3", "tbl4",
                    "tbl5", "tbl6", "tbl7",
                }.issubset(tables)
            )
            self.assertIn("tbl8", tables)
            self.assertIn("CombineBeam", tables)
            for number in range(1, 5):
                value = connection.execute(
                    "SELECT Value FROM tbl{}".format(number)
                ).fetchone()[0]
                self.assertEqual("keep-{}".format(number), value)
            self.assertEqual(
                "keep-tbl8",
                connection.execute("SELECT Value FROM tbl8").fetchone()[0],
            )
            self.assertEqual(
                "keep-combine",
                connection.execute("SELECT Value FROM CombineBeam").fetchone()[0],
            )
            self.assertEqual(
                "unchanged",
                connection.execute(
                    "SELECT Value FROM handoff_meta WHERE Key='Upper.Keep'"
                ).fetchone()[0],
            )
        finally:
            connection.close()

    def test_legacy_foundation_rebar_is_read_but_preserved_outside_tbl5_to_tbl7(self):
        convert_foundation_ydb(self.source, self.output)
        type_key = read_editor_data(self.output)["cap_types"][0]["TypeKey"]
        connection = sqlite3.connect(self.output)
        try:
            connection.executescript(
                """
                DROP TABLE IF EXISTS tbl7;
                DROP TABLE IF EXISTS tbl6;
                DROP TABLE IF EXISTS tbl5;
                DROP TABLE IF EXISTS tbl8;
                CREATE TABLE FoundationCapRebar (
                    TypeKey TEXT PRIMARY KEY,
                    UserTypeName TEXT, BottomX TEXT, BottomY TEXT,
                    TopX TEXT, TopY TEXT, SideRebar TEXT, Cover REAL,
                    Notes TEXT, ExtraJson TEXT, UpdatedAt TEXT
                );
                """
            )
            connection.execute(
                "INSERT INTO FoundationCapRebar VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    type_key, "LEGACY-CT", "C20@150", "C20@150", "", "",
                    "", 50, "migrated", "{}", "2026-01-01T00:00:00+00:00",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        convert_foundation_ydb(self.source, self.output)
        item = read_editor_data(self.output)["cap_types"][0]
        self.assertEqual("LEGACY-CT", item["UserTypeName"])
        self.assertEqual("C20@150", item["BottomX"])
        connection = sqlite3.connect(self.output)
        try:
            legacy_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='FoundationCapRebar'"
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(legacy_exists)

    def test_cli_rejects_foundation_source_in_upper_mode_without_touching_output(self):
        official = Path(self.temp_dir.name) / "official.db"
        connection = sqlite3.connect(official)
        try:
            connection.execute("CREATE TABLE Sentinel(Value TEXT)")
            connection.execute("INSERT INTO Sentinel VALUES ('keep')")
            connection.commit()
        finally:
            connection.close()
        original = official.read_bytes()
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = CONVERTER.main([
                str(self.source),
                "-o", str(official),
                "--mode", "upper",
            ])

        payload = json.loads(output.getvalue().strip())
        self.assertEqual(1, exit_code)
        self.assertEqual("upper", payload["mode"])
        self.assertEqual("error", payload["status"])
        self.assertIn("source mode mismatch", payload["error"])
        self.assertEqual(original, official.read_bytes())
        self.assertEqual([], list(official.parent.glob(official.name + ".pending-*")))

    def test_cli_cancel_is_machine_readable_and_does_not_choose_a_destination(self):
        output = io.StringIO()
        with patch.object(CONVERTER, "_choose_source_file", return_value=""), redirect_stdout(output):
            exit_code = CONVERTER.main([])
        payload = json.loads(output.getvalue().strip())
        self.assertEqual(2, exit_code)
        self.assertEqual("auto", payload["mode"])
        self.assertEqual("cancelled", payload["status"])

    def test_arbitrary_polygon_grouping_ignores_start_vertex_and_rotation(self):
        first = [
            (0, 0),
            (4000, 0),
            (3500, 1200),
            (2200, 2600),
            (0, 1800),
        ]
        # Same outline, rotated 90 degrees, reversed, and started at another vertex.
        second = [(-y, x) for x, y in first]
        second = list(reversed(second[2:] + second[:2]))
        connection = sqlite3.connect(self.source)
        try:
            connection.execute("DELETE FROM dais_pt")
            rows = []
            for flag, points in ((0, first), (1, second)):
                for x, y in points:
                    rows.append((len(rows) + 1, flag, 0, x, y, 0))
            connection.executemany(
                "INSERT INTO dais_pt VALUES (?,?,?,?,?,?)",
                rows,
            )
            connection.commit()
        finally:
            connection.close()

        summary = convert_foundation_ydb(self.source, self.output)
        self.assertEqual(1, summary["cap_types"])
        connection = sqlite3.connect(self.output)
        try:
            polygon = json.loads(
                connection.execute("SELECT PolygonJson FROM tbl6").fetchone()[0]
            )
        finally:
            connection.close()
        self.assertEqual(5, len(polygon))

    def test_different_pile_layouts_produce_different_cap_types(self):
        connection = sqlite3.connect(self.source)
        try:
            connection.execute("UPDATE app_Pile SET x=1700 WHERE ID=3")
            connection.commit()
        finally:
            connection.close()
        summary = convert_foundation_ydb(self.source, self.output)
        self.assertEqual(2, summary["cap_types"])

    def test_pile_length_is_part_of_pile_and_cap_type(self):
        connection = sqlite3.connect(self.source)
        try:
            connection.execute("UPDATE app_dais SET idaispilelen=32 WHERE ID=2")
            connection.commit()
        finally:
            connection.close()
        summary = convert_foundation_ydb(self.source, self.output)
        self.assertEqual(2, summary["pile_types"])
        self.assertEqual(2, summary["cap_types"])
        connection = sqlite3.connect(self.output)
        try:
            lengths = {
                row[0] for row in connection.execute("SELECT Length FROM tbl5")
            }
        finally:
            connection.close()
        self.assertEqual({31000, 32000}, lengths)

    def test_local_web_api_reads_and_updates_without_external_dependencies(self):
        convert_foundation_ydb(self.source, self.output)
        token = "unit-test-token"
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), _handler_factory(self.output, token)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:{}".format(server.server_address[1])
        try:
            request = urllib.request.Request(
                base + "/api/data", headers={"X-Foundation-Token": token}
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
            self.assertEqual(2, data["summary"]["caps"])
            type_key = data["pile_types"][0]["TypeKey"]
            body = json.dumps({
                "UserTypeName": "P-1000",
                "LongitudinalRebar": "20C22",
                "Cover": 70,
                "ExtraJson": {},
            }).encode("utf-8")
            request = urllib.request.Request(
                base + "/api/pile/" + type_key,
                data=body,
                method="PUT",
                headers={
                    "Content-Type": "application/json",
                    "X-Foundation-Token": token,
                },
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
            self.assertTrue(result["ok"])
            item = read_editor_data(self.output)["pile_types"][0]
            self.assertEqual("P-1000", item["UserTypeName"])
            self.assertEqual("20C22", item["LongitudinalRebar"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_multistep_caps_are_rejected_explicitly(self):
        connection = sqlite3.connect(self.source)
        try:
            connection.execute("UPDATE DEF_dais SET nstep=2 WHERE ID=1")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(FoundationDataError, "only one-step"):
            convert_foundation_ydb(self.source, self.output)

    def test_circular_caps_are_rejected_explicitly(self):
        connection = sqlite3.connect(self.source)
        try:
            connection.execute("INSERT INTO dais_Cir VALUES (1,0,2000)")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(FoundationDataError, "circular"):
            convert_foundation_ydb(self.source, self.output)

    def test_inclined_piles_are_rejected_explicitly(self):
        connection = sqlite3.connect(self.source)
        try:
            connection.execute("UPDATE app_Pile SET fKn=0.1 WHERE ID=1")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(FoundationDataError, "only vertical"):
            convert_foundation_ydb(self.source, self.output)

    def test_rectangular_piles_are_rejected_explicitly(self):
        connection = sqlite3.connect(self.source)
        try:
            connection.execute("UPDATE DEF_Pile SET H=800 WHERE ID=1")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(FoundationDataError, "requires diameter and length"):
            convert_foundation_ydb(self.source, self.output)

    def test_current_project_sample_when_present(self):
        matches = list(ROOT.rglob("jccad.ydb"))
        if not matches:
            self.skipTest("current foundation sample is not present")
        summary = convert_foundation_ydb(matches[0], self.output)
        self.assertEqual(5, summary["cap_types"])
        self.assertEqual(88, summary["caps"])
        self.assertEqual(1, summary["pile_types"])
        self.assertEqual(173, summary["piles"])


if __name__ == "__main__":
    unittest.main()
