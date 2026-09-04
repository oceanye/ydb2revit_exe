// PecStiffenerCommand.cs
// 依据 ydb转换数据库.db 的 WInfo v4 协议，在已生成的 PEC 主墙内补建钢板实体：
//   * 贯通腹板：厚 web_thickness_mm，沿墙轴，两端 H 内侧边缘之间，通高；
//   * 加劲板：净腹板长度等分（1 道居中 / 2 道三分点），宽 x 厚，横穿居中。
//
// 两个入口：
//   1. CreateNewExtern.PecStiffenerCommand.Execute —— 附加模块/外部工具 手动运行；
//   2. CreateNewExtern.PecStiffenerCommand.GenerateAllPlates(Document) —— 静态方法，
//      由 CombineBeam::Execute 末尾经 IL 挂接调用，实现"合并梁并生成模型"一键全生成。
//
// 稳定性设计：tbl4.RvtID 锚定已建墙；比例尺 = 墙曲线长 / 数据库节点长，对坐标系免疫；
// 重复运行先删除既有板（按名称匹配），幂等；GenerateAllPlates 全程吞错，不影响宿主命令。
using System;
using System.Collections.Generic;
using System.Data.SQLite;
using System.IO;
using System.Text.RegularExpressions;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

namespace CreateNewExtern
{
    [Transaction(TransactionMode.Manual)]
    public class PecStiffenerCommand : IExternalCommand
    {
        private const string DbPath =
            @"C:\ProgramData\Autodesk\Revit\Addins\2018\数据库\ydb转换数据库.db";
        private const double FeetPerMm = 1.0 / 304.8;

        private static readonly Regex StiffenerRegex = new Regex(
            "\"internal_stiffener\":\\{\"count\":(\\d+)," +
            "\"thickness_mm\":([0-9.eE+-]+),\"width_mm\":([0-9.eE+-]+)\\}");
        private static readonly Regex WebRegex = new Regex(
            "\"web_thickness_mm\":([0-9.eE+-]+)");
        private static readonly Regex RefsRegex = new Regex(
            "\"tbl2_column_refs\":\\{\"end\":(\\d+),\"start\":(\\d+)\\}");

        private class WallRow
        {
            public string Group; public string Leg; public string Role; public string Shape;
            public string RvtId; public double Section;
            public double StartX; public double StartY; public double EndX; public double EndY;
            public int StiffCount; public double StiffThk; public double StiffWidth;
            public double WebThk;
            public long HStart; public long HEnd;
            public double NodeLengthMm
            {
                get { return Math.Sqrt((EndX - StartX) * (EndX - StartX)
                                     + (EndY - StartY) * (EndY - StartY)); }
            }
        }

        // ------------------------------------------------------ 外部工具入口
        public Result Execute(ExternalCommandData commandData,
                              ref string message, ElementSet elements)
        {
            Document doc = commandData.Application.ActiveUIDocument.Document;
            List<string> problems = new List<string>();
            int considered, created, webs;
            GenerateCore(doc, problems, out considered, out created, out webs);
            StringBuilderReport(considered, created, webs, problems);
            return Result.Succeeded;
        }

        // ------------------------------ CombineBeam 一键生成挂接入口（静态）
        public static void GenerateAllPlates(Document doc)
        {
            if (doc == null) return;
            try
            {
                List<string> problems = new List<string>();
                int considered, created, webs;
                GenerateCore(doc, problems, out considered, out created, out webs);
                if (problems.Count > 0)
                {
                    System.Text.StringBuilder text = new System.Text.StringBuilder();
                    text.AppendLine("PEC 钢板生成提示：");
                    int shown = Math.Min(problems.Count, 10);
                    for (int i = 0; i < shown; i++) text.AppendLine("  " + problems[i]);
                    if (problems.Count > shown)
                        text.AppendLine("  … 其余 " + (problems.Count - shown) + " 条略");
                    TaskDialog.Show("PEC钢板", text.ToString());
                }
            }
            catch (Exception ex)
            {
                // 挂接入口绝不向宿主命令抛异常
                try { TaskDialog.Show("PEC钢板", "钢板生成中止（不影响本次建模）：\n"
                        + ex.GetType().Name + "\n" + ex.Message); }
                catch (Exception) { }
            }
        }

        // ------------------------------------------------------ 核心生成逻辑
        private static void GenerateCore(Document doc, List<string> problems,
                                         out int considered, out int created, out int webs)
        {
            considered = 0; created = 0; webs = 0;
            if (!File.Exists(DbPath))
            {
                problems.Add("找不到中间数据库：" + DbPath);
                return;
            }
            List<WallRow> walls;
            Dictionary<long, double> hHeights;
            try
            {
                walls = LoadWalls();
                hHeights = LoadColumnHeights();
            }
            catch (Exception ex)
            {
                problems.Add("读取数据库失败：" + ex.Message);
                return;
            }
            Dictionary<string, double> secondaryWidth = new Dictionary<string, double>();
            foreach (WallRow row in walls)
            {
                if (row.Role == "SECONDARY" && !secondaryWidth.ContainsKey(row.Group))
                    secondaryWidth[row.Group] = row.Section;
            }

            // Revit 2018 无 IsTransacting：尝试自开事务，失败说明宿主（CombineBeam）
            // 事务仍打开，此时直接并入宿主事务创建图元。
            Transaction transaction = new Transaction(doc, "生成PEC钢板");
            bool ownTransaction = false;
            try { transaction.Start(); ownTransaction = true; }
            catch (Exception) { transaction.Dispose(); transaction = null; }
            try
            {
                DeleteExistingPlates(doc);

                foreach (WallRow row in walls)
                {
                    if (row.Role != "MAIN") continue;
                    considered++;
                    long elementId;
                    if (!long.TryParse(row.RvtId, out elementId))
                    {
                        problems.Add(row.Leg + "：RvtID 缺失（先运行建模）");
                        continue;
                    }
                    Wall wall = doc.GetElement(new ElementId((int)elementId)) as Wall;
                    if (wall == null)
                    {
                        problems.Add(row.Leg + "：RvtID=" + row.RvtId + " 的墙不存在");
                        continue;
                    }
                    LocationCurve location = wall.Location as LocationCurve;
                    Line line = location == null ? null : location.Curve as Line;
                    if (line == null)
                    {
                        problems.Add(row.Leg + "：墙定位线不是直线，跳过");
                        continue;
                    }
                    double nodeLengthMm = row.NodeLengthMm;
                    if (nodeLengthMm <= 0)
                    {
                        problems.Add(row.Leg + "：节点长度为零");
                        continue;
                    }
                    double scale = line.Length / nodeLengthMm;   // feet per mm

                    XYZ p0 = line.GetEndPoint(0);
                    XYZ p1 = line.GetEndPoint(1);
                    bool reversed = IsReversed(p0, p1, row);
                    XYZ origin = reversed ? p1 : p0;
                    XYZ dir = reversed ? (p0 - p1).Normalize() : (p1 - p0).Normalize();
                    XYZ normal = new XYZ(-dir.Y, dir.X, 0);

                    double hStart, hEnd;
                    if (!hHeights.TryGetValue(row.HStart, out hStart)
                        || !hHeights.TryGetValue(row.HEnd, out hEnd)
                        || hStart <= 0 || hEnd <= 0)
                    {
                        problems.Add(row.Leg + "：H 截面 (start=" + row.HStart
                                     + ",end=" + row.HEnd + ") 无法解析");
                        continue;
                    }
                    double secondaryB;
                    double startInner = (row.Shape == "L"
                                         && secondaryWidth.TryGetValue(row.Group, out secondaryB))
                        ? hStart - secondaryB / 2.0
                        : hStart;
                    double endInner = nodeLengthMm - hEnd;
                    startInner = Math.Max(0.0, Math.Min(nodeLengthMm, startInner));
                    endInner = Math.Max(0.0, Math.Min(nodeLengthMm, endInner));
                    if (endInner <= startInner)
                    {
                        problems.Add(row.Leg + "：净腹板长度为零（H 覆盖整墙）");
                        continue;
                    }

                    BoundingBoxXYZ box = wall.get_BoundingBox(null);
                    if (box == null)
                    {
                        problems.Add(row.Leg + "：无法取得墙高度范围");
                        continue;
                    }
                    double z0 = box.Min.Z, z1 = box.Max.Z;

                    // 贯通腹板
                    if (row.WebThk > 0)
                    {
                        double span = (endInner - startInner) * scale;
                        XYZ webCenter = origin + dir
                            * ((startInner + endInner) / 2.0 * scale);
                        GeometryObject webPlate = BuildPlateSolid(
                            doc, webCenter, normal, dir, span / 2.0,
                            row.WebThk * scale, z0, z1, problems, row.Leg + " 腹板");
                        if (webPlate != null)
                        {
                            DirectShape webShape = DirectShape.CreateElement(
                                doc, new ElementId(BuiltInCategory.OST_GenericModel));
                            webShape.SetShape(new List<GeometryObject> { webPlate });
                            webShape.Name = row.Leg + " 腹板";
                            created++;
                            webs++;
                        }
                    }

                    // 加劲板
                    if (row.StiffCount > 0 && row.StiffWidth > 0 && row.StiffThk > 0)
                    {
                        double halfWidth = row.StiffWidth * scale / 2.0;
                        double thickness = row.StiffThk * scale;
                        for (int index = 1; index <= row.StiffCount; index++)
                        {
                            double fraction = (double)index / (row.StiffCount + 1);
                            double distance = (startInner
                                + (endInner - startInner) * fraction) * scale;
                            XYZ planeCenter = origin + dir * distance
                                              - dir * (thickness / 2.0);
                            GeometryObject plate = BuildPlateSolid(
                                doc, planeCenter, dir, normal, halfWidth, thickness,
                                z0, z1, problems, row.Leg + " 加劲板" + index);
                            if (plate == null) continue;

                            DirectShape shape = DirectShape.CreateElement(
                                doc, new ElementId(BuiltInCategory.OST_GenericModel));
                            shape.SetShape(new List<GeometryObject> { plate });
                            shape.Name = row.Leg + " 加劲板" + index;
                            created++;
                        }
                    }
                }
            }
            finally
            {
                if (ownTransaction && transaction != null)
                {
                    try { transaction.Commit(); }
                    catch (Exception ex) { problems.Add("事务提交失败：" + ex.Message); }
                }
            }
        }

        // ------------------------------------------- 删除旧板（幂等，可重复运行）
        private static void DeleteExistingPlates(Document doc)
        {
            FilteredElementCollector collector =
                new FilteredElementCollector(doc).OfClass(typeof(DirectShape));
            List<ElementId> doomed = new List<ElementId>();
            foreach (Element element in collector)
            {
                string name = element.Name == null ? "" : element.Name;
                if (name.IndexOf("加劲板") >= 0 || name.EndsWith("腹板"))
                    doomed.Add(element.Id);
            }
            if (doomed.Count > 0) doc.Delete(doomed);
        }

        private static bool IsReversed(XYZ p0, XYZ p1, WallRow row)
        {
            XYZ dataStart = new XYZ(row.StartX * FeetPerMm, row.StartY * FeetPerMm, 0);
            XYZ dataEnd = new XYZ(row.EndX * FeetPerMm, row.EndY * FeetPerMm, 0);
            double toStart = p0.DistanceTo(dataStart);
            double toEnd = p0.DistanceTo(dataEnd);
            if (toStart > 1.0 && toEnd > 1.0) return false;  // 坐标系被变换：按对称假设
            return toStart > toEnd;
        }

        private static XYZ At(XYZ center, XYZ normal, double alongNormal, double z)
        {
            // Z 直接取绝对标高，避免叠加墙定位线自身的基点高程。
            return new XYZ(center.X + normal.X * alongNormal,
                           center.Y + normal.Y * alongNormal, z);
        }

        // ------------------------------------------- 扫掠生成一块板（腹板/加劲板）
        private static GeometryObject BuildPlateSolid(Document doc, XYZ planeCenter,
                                                      XYZ dir, XYZ normal, double halfWidth,
                                                      double thickness, double z0, double z1,
                                                      List<string> problems, string label)
        {
            try
            {
                Line path = Line.CreateBound(planeCenter,
                                             planeCenter + dir * thickness);
                CurveLoop pathLoop = new CurveLoop();
                pathLoop.Append(path);
                CurveLoop loop = new CurveLoop();
                loop.Append(Line.CreateBound(At(planeCenter, normal, -halfWidth, z0),
                                             At(planeCenter, normal, halfWidth, z0)));
                loop.Append(Line.CreateBound(At(planeCenter, normal, halfWidth, z0),
                                             At(planeCenter, normal, halfWidth, z1)));
                loop.Append(Line.CreateBound(At(planeCenter, normal, halfWidth, z1),
                                             At(planeCenter, normal, -halfWidth, z1)));
                loop.Append(Line.CreateBound(At(planeCenter, normal, -halfWidth, z1),
                                             At(planeCenter, normal, -halfWidth, z0)));
                List<CurveLoop> loops = new List<CurveLoop>();
                loops.Add(loop);
                Solid plate = GeometryCreationUtilities.CreateSweptGeometry(
                    pathLoop, 0, 0.0, loops);
                if (plate == null || plate.Volume <= 0)
                {
                    problems.Add(label + "：扫掠结果无效");
                    return null;
                }
                return plate;
            }
            catch (Exception ex)
            {
                problems.Add(label + "：" + ex.GetType().Name + " " + ex.Message);
                return null;
            }
        }

        private static List<WallRow> LoadWalls()
        {
            List<WallRow> result = new List<WallRow>();
            using (SQLiteConnection connection = new SQLiteConnection(
                "Data Source=" + DbPath + ";Read Only=True;"))
            {
                connection.Open();
                using (SQLiteCommand command = connection.CreateCommand())
                {
                    command.CommandText =
                        "SELECT WGroupID,WLegID,WLegRole,WShape,WSection," +
                        "WStartX,WStartY,WEndX,WEndY,WInfo,RvtID FROM tbl4 " +
                        "WHERE WInfo IS NOT NULL";
                    using (SQLiteDataReader reader = command.ExecuteReader())
                    {
                        while (reader.Read())
                        {
                            WallRow row = new WallRow();
                            row.Group = reader.IsDBNull(0) ? "" : reader.GetString(0);
                            row.Leg = reader.IsDBNull(1) ? "" : reader.GetString(1);
                            row.Role = reader.IsDBNull(2) ? "" : reader.GetString(2);
                            row.Shape = reader.IsDBNull(3) ? "" : reader.GetString(3);
                            row.Section = ParseDouble(reader, 4);
                            row.StartX = ParseDouble(reader, 5);
                            row.StartY = ParseDouble(reader, 6);
                            row.EndX = ParseDouble(reader, 7);
                            row.EndY = ParseDouble(reader, 8);
                            string info = reader.IsDBNull(9) ? "" : reader.GetString(9);
                            row.RvtId = reader.IsDBNull(10) ? "" : reader.GetString(10);
                            Match stiffener = StiffenerRegex.Match(info);
                            if (stiffener.Success)
                            {
                                int.TryParse(stiffener.Groups[1].Value, out row.StiffCount);
                                row.StiffThk = ParseText(stiffener.Groups[2].Value);
                                row.StiffWidth = ParseText(stiffener.Groups[3].Value);
                            }
                            Match web = WebRegex.Match(info);
                            if (web.Success) row.WebThk = ParseText(web.Groups[1].Value);
                            Match refs = RefsRegex.Match(info);
                            if (refs.Success)
                            {
                                long.TryParse(refs.Groups[2].Value, out row.HStart);
                                long.TryParse(refs.Groups[1].Value, out row.HEnd);
                            }
                            result.Add(row);
                        }
                    }
                }
            }
            return result;
        }

        private static Dictionary<long, double> LoadColumnHeights()
        {
            Dictionary<long, double> result = new Dictionary<long, double>();
            using (SQLiteConnection connection = new SQLiteConnection(
                "Data Source=" + DbPath + ";Read Only=True;"))
            {
                connection.Open();
                using (SQLiteCommand command = connection.CreateCommand())
                {
                    command.CommandText = "SELECT ID,CSection FROM tbl2";
                    using (SQLiteDataReader reader = command.ExecuteReader())
                    {
                        while (reader.Read())
                        {
                            long id = reader.GetInt64(0);
                            string section = reader.IsDBNull(1) ? "" : reader.GetString(1);
                            // "H600x300x7x22@PEC" -> 高度 600
                            if (!section.StartsWith("H", StringComparison.OrdinalIgnoreCase))
                                continue;
                            string[] parts = section.Substring(1).Split('x', '@');
                            double height;
                            if (parts.Length >= 4 && double.TryParse(parts[0], out height))
                                result[id] = height;
                        }
                    }
                }
            }
            return result;
        }

        private static double ParseDouble(SQLiteDataReader reader, int index)
        {
            if (reader.IsDBNull(index)) return 0.0;
            Type code = reader.GetFieldType(index);
            if (code == typeof(double)) return reader.GetDouble(index);
            if (code == typeof(long)) return reader.GetInt64(index);
            return ParseText(reader.GetString(index));
        }

        private static double ParseText(string text)
        {
            double value;
            return double.TryParse(text, out value) ? value : 0.0;
        }

        private static void StringBuilderReport(int considered, int created,
                                                int webs, List<string> problems)
        {
            System.Text.StringBuilder text = new System.Text.StringBuilder();
            text.AppendLine("处理 MAIN 墙肢：" + considered + " 个");
            text.AppendLine("生成钢板：" + created + " 块（含腹板 " + webs + " 块）");
            if (problems.Count > 0)
            {
                text.AppendLine("");
                text.AppendLine("提示（" + problems.Count + " 条）：");
                int shown = Math.Min(problems.Count, 10);
                for (int i = 0; i < shown; i++) text.AppendLine("  " + problems[i]);
                if (problems.Count > shown)
                    text.AppendLine("  … 其余 " + (problems.Count - shown) + " 条略");
            }
            TaskDialog.Show("PEC钢板", text.ToString());
        }
    }
}
