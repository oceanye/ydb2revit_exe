# YDB → Revit 数据转换工作包

本仓库把 YJK 的 SQLite/YDB 数据转换为 Revit 插件使用的统一中间数据库。上部结构写入 `tbl1`–`tbl4`，基础写入 `tbl5`–`tbl7`；两类提取通过临时数据库、完整性检查、范围哈希和原子替换共享同一个目标文件。

## 数据范围

- `tbl1`：梁与斜撑。保留原有前 15 列，尾部扩展 `BZOffset`、`BZOffset2`、`BIsArc`。
- `tbl2`：柱。`EccX`、`EccY` 单位为 mm，`Rotation` 单位为 degree；普通柱与独立 PEC 柱按源值原样传递。
- `tbl3`：标高。
- `tbl4`：墙肢与 PEC 墙关系。`WInfo` 当前为 v4，`WTopZ/WTopZ2` 是两端真实顶标高。
- `tbl5`–`tbl7`：桩、承台型号和承台布置。
- `handoff_meta`：分别以 `Upper.`、`Foundation.` 为前缀保存契约版本、源文件摘要和能力标记；任一提取器都不能改写另一方前缀。

柱偏心与旋转、墙端 PEC 例外、柱顶标高以及插件端验收要求见 [Revit 端 handoff](handoff_doc/handoff-Revit端-柱偏心旋转与近期接口联动-20260907.md)。

## 开发与验证

环境基线为 Python 3.12。安装固定版本的开发依赖后，可直接运行测试：

```powershell
python -m pip install -r requirements-dev.txt
pytest -q
python -m py_compile ydb转换.py handoff_atomic.py foundation_handoff.py foundation_web.py
```

构建单文件控制台程序：

```powershell
python -m PyInstaller --clean --noconfirm ydb转换.spec
```

输出为 `dist\ydb转换.exe`。仓库内的 GitHub Actions 在 Windows 上执行回归测试、语法编译和同一条 PyInstaller 构建命令。

## 命令行

```powershell
python ydb转换.py <source.ydb> -o <destination.db>
python ydb转换.py <source.ydb> --mode upper -o <destination.db>
python ydb转换.py <foundation.ydb> --mode foundation -o <destination.db>
```

不指定参数时进入原有交互式选择流程。源数据库只读打开；本地路径使用 SQLite `mode=ro`，UNC 路径使用原生路径并启用 `PRAGMA query_only=ON`。目标数据库只在暂存副本通过校验后替换。

## Revit 参考代码

`revit_addin/PecStiffenerCommand.cs` 是可交给插件仓库集成的参考实现。本仓库没有 CreateNewExtern 的完整工程文件，也不会生成或部署正式插件 DLL。发布时应把 handoff 文件和该参考代码一并交给 Revit 开发，在其工程中编译、联调并按验收矩阵确认。
