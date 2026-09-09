"""Read-only analysis of YJK raft-region/free-pile foundation YDB files."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path


def _tables(connection):
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _inside(x, y, polygon):
    hit = False
    for index, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[(index + 1) % len(polygon)]
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        dot = (x - x1) * (x - x2) + (y - y1) * (y - y2)
        if abs(cross) <= 1e-4 and dot <= 1e-3:
            return True
        if ((y1 > y) != (y2 > y)) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def analyze(source):
    source = Path(source).expanduser().resolve()
    connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = _tables(connection)
        required = {
            "RaftSlab",
            "RaftCornerPoint",
            "app_Pile",
            "DEF_Pile",
        }
        missing = sorted(required - tables)
        if missing:
            raise ValueError("missing raft/free-pile tables: " + ", ".join(missing))

        slabs = {
            int(row["lID"]): row
            for row in connection.execute("SELECT * FROM RaftSlab ORDER BY ID")
        }
        polygons = {
            raft_id: [
                (float(point["ptx"]), float(point["pty"]))
                for point in connection.execute(
                    "SELECT ptx,pty FROM RaftCornerPoint WHERE RaftID=? ORDER BY ID",
                    (raft_id,),
                )
            ]
            for raft_id in slabs
        }
        piles = list(connection.execute("SELECT * FROM app_Pile ORDER BY ID"))
        exact_count = Counter()
        spatial_count = Counter()
        exact_by_z = Counter()
        kind_count = Counter(int(row["kind"]) for row in piles)
        pile_z_count = Counter(float(row["z"]) for row in piles)
        examples = {"boundary": [], "outside": []}

        for row in piles:
            spatial = [
                raft_id
                for raft_id, polygon in polygons.items()
                if polygon and _inside(float(row["x"]), float(row["y"]), polygon)
            ]
            exact = [
                raft_id
                for raft_id in spatial
                if abs(float(row["z"]) - float(slabs[raft_id]["BotElevat"]) * 1000) <= 0.01
            ]
            spatial_count[len(spatial)] += 1
            exact_count[len(exact)] += 1
            exact_by_z[(float(row["z"]), len(exact))] += 1
            if len(exact) > 1 and len(examples["boundary"]) < 10:
                examples["boundary"].append(
                    {"source_id": int(row["ID"]), "raft_ids": exact, "z": float(row["z"])}
                )
            if not spatial and len(examples["outside"]) < 10:
                examples["outside"].append(
                    {"source_id": int(row["ID"]), "x": float(row["x"]), "y": float(row["y"]), "z": float(row["z"])}
                )

        return {
            "source": str(source),
            "tables": sorted(tables),
            "cap_tables": {
                name: int(connection.execute(f"SELECT COUNT(*) FROM \"{name}\"").fetchone()[0])
                for name in ("DEF_dais", "app_dais", "dais_pt", "dais_stepH")
                if name in tables
            },
            "raft_regions": {
                "count": len(slabs),
                "point_count": sum(len(polygon) for polygon in polygons.values()),
                "records": [
                    {
                        "source_id": int(row["ID"]),
                        "geometry_id": raft_id,
                        "point_count": len(polygons[raft_id]),
                        "thickness_mm": float(row["thick"]),
                        "bottom_z_mm": float(row["BotElevat"]) * 1000,
                        "base_z_raw": float(row["baseZ"]),
                    }
                    for raft_id, row in slabs.items()
                ],
            },
            "piles": {
                "count": len(piles),
                "kind_counts": dict(kind_count),
                "z_counts_mm": dict(pile_z_count),
                "spatial_candidate_count": dict(spatial_count),
                "exact_bottom_elevation_match_count": dict(exact_count),
                "exact_match_by_z": {f"{z}:{n}": count for (z, n), count in exact_by_z.items()},
                "boundary_examples": examples["boundary"],
                "outside_examples": examples["outside"],
            },
            "pile_definitions": [dict(row) for row in connection.execute("SELECT * FROM DEF_Pile ORDER BY ID")],
        }
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)
    payload = analyze(args.source)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
