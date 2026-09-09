import sqlite3

from tools.analyze_raft_free_piles import analyze
from foundation_handoff import convert_foundation_ydb, read_editor_data


def test_analyze_raft_regions_and_free_piles(tmp_path):
    source = tmp_path / "raft.ydb"
    connection = sqlite3.connect(source)
    connection.executescript(
        """
        CREATE TABLE RaftSlab (
            ID INTEGER, lID INTEGER, thick REAL, BotElevat REAL,
            baseZ REAL
        );
        CREATE TABLE RaftCornerPoint (
            ID INTEGER, RaftID INTEGER, ptx REAL, pty REAL
        );
        CREATE TABLE app_Pile (
            ID INTEGER, x REAL, y REAL, z REAL, kind INTEGER,
            DaisFlag INTEGER, idUp INTEGER, idaispilelen REAL
        );
        CREATE TABLE DEF_Pile (
            ID INTEGER, B REAL, H REAL, F1 REAL, F2 REAL,
            means INTEGER, blade_D REAL, blade_dis REAL
        );
        CREATE TABLE DEF_dais (ID INTEGER);
        CREATE TABLE app_dais (ID INTEGER);
        CREATE TABLE dais_pt (ID INTEGER);
        CREATE TABLE dais_stepH (ID INTEGER);
        INSERT INTO RaftSlab VALUES (1, 10, 500, -10.0, -7200);
        INSERT INTO RaftSlab VALUES (2, 20, 800, -9.5, -7200);
        INSERT INTO RaftCornerPoint VALUES (1, 10, 0, 0);
        INSERT INTO RaftCornerPoint VALUES (2, 10, 10, 0);
        INSERT INTO RaftCornerPoint VALUES (3, 10, 10, 10);
        INSERT INTO RaftCornerPoint VALUES (4, 10, 0, 10);
        INSERT INTO RaftCornerPoint VALUES (5, 20, 20, 20);
        INSERT INTO RaftCornerPoint VALUES (6, 20, 30, 20);
        INSERT INTO RaftCornerPoint VALUES (7, 20, 30, 30);
        INSERT INTO RaftCornerPoint VALUES (8, 20, 20, 30);
        INSERT INTO app_Pile VALUES (1, 5, 5, -10000, 0, -1, 0, 25);
        INSERT INTO app_Pile VALUES (2, 25, 25, -9500, 0, -1, 0, 25);
        INSERT INTO app_Pile VALUES (3, 40, 40, -9500, 0, -1, 0, 25);
        INSERT INTO DEF_Pile VALUES (1, 600, 0, 1400, 250, 3, 200, 500);
        """
    )
    connection.commit()
    connection.close()

    result = analyze(source)

    assert result["raft_regions"]["count"] == 2
    assert result["raft_regions"]["point_count"] == 8
    assert result["piles"]["count"] == 3
    assert result["piles"]["exact_bottom_elevation_match_count"] == {0: 1, 1: 2}
    assert result["piles"]["outside_examples"][0]["source_id"] == 3
    assert result["pile_definitions"][0]["B"] == 600


def test_convert_raft_free_piles_contract(tmp_path):
    source = tmp_path / "raft.ydb"
    connection = sqlite3.connect(source)
    connection.executescript(
        """
        CREATE TABLE RaftSlab (ID INTEGER, lID INTEGER, thick REAL, BotElevat REAL, baseZ REAL);
        CREATE TABLE RaftCornerPoint (ID INTEGER, RaftID INTEGER, ptx REAL, pty REAL);
        CREATE TABLE app_Pile (ID INTEGER, x REAL, y REAL, z REAL, kind INTEGER, DaisFlag INTEGER, idUp INTEGER, idaispilelen REAL);
        CREATE TABLE DEF_Pile (ID INTEGER, B REAL, H REAL);
        CREATE TABLE DEF_dais (ID INTEGER);
        CREATE TABLE app_dais (ID INTEGER);
        INSERT INTO RaftSlab VALUES (1, 10, 500, -10.0, -7200);
        INSERT INTO RaftCornerPoint VALUES (1, 10, 0, 0), (2, 10, 10, 0), (3, 10, 10, 10), (4, 10, 0, 10);
        INSERT INTO app_Pile VALUES (1, 5, 5, -10000, 0, -1, 0, 25), (2, 20, 20, -10000, 0, -1, 0, 25);
        INSERT INTO DEF_Pile VALUES (1, 600, 0);
        """
    )
    connection.commit()
    connection.close()
    destination = tmp_path / "handoff.db"

    summary = convert_foundation_ydb(source, destination)
    data = read_editor_data(destination)

    assert summary["scope"] == "RAFT_FREE_PILES"
    assert summary["raft_regions"] == 1
    assert summary["piles"] == 2
    assert data["meta"]["ContractVersion"] == "RAFT_FREE_PILES_V1"
    assert data["summary"] == {"raft_regions": 1, "piles": 2, "pile_types": 1}
    connection = sqlite3.connect(destination)
    try:
        assert connection.execute("SELECT COUNT(*) FROM tbl6").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM tbl7").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM tbl8").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM tbl9").fetchone()[0] == 2
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()
