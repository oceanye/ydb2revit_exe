# Handoff：Python 端截面文本契约对齐（SectionTextParser）

对齐日期：2026-09-02
契约权威来源：`E:\revit-external-tool2.git`（CreateNewExtern 插件 C# 源码裸仓库，
**对本工程只读**，仅以 `git show/log` 等只读命令查阅）
`CreateNewExtern/SectionTextParser.cs`

## 1. 契约要点（自 C# 提取）

1. **PEC 标记**：截面文本**末尾**的 `@PEC` 后缀，不区分大小写；只识别最后一个，
   旧 ShapeVal 中间的 `@` 元数据不受影响；
2. **H 截面文本**：`H{h}{sep}{b}{sep}{tw}{sep}{tf}`，sep ∈ `x / X / × / *`，
   允许空白，尺寸为正数、InvariantCulture 小数；
3. **规范名（CanonicalName）**：`H` + `{h}X{b}X{tw}X{tf}`（**大写 X**，尺寸
   `0.###` 格式）+ `@PEC`，例：`H400X200X8X13@PEC`；
4. 旧 C# 输出的末尾多余 `X`（`…X@PEC`）解析时兼容去除；
5. **同尺寸的 PEC 与普通 H 型钢不得视为同一截面**（梁合并比较键
   `H|{尺寸}|PEC` vs `|STANDARD`）。

## 2. Python 端已落实（随本 handoff 提交）

| 项 | 内容 |
|---|---|
| 契约实现 | `ydb转换.py` 新增 `has_pec_suffix / remove_pec_suffix / parse_h_section_text / format_h_section`，规则逐条对齐上述契约，代码注释注明只读来源 |
| 输出规范形 | 转换器输出截面串由 `H400x200x8x13@PEC` 升级为规范形 **`H400X200X8X13@PEC`**（大写 X、0.###）。颛桥实测 12 种 PEC 截面全部输出规范形 |
| 读取统一 | `tools/export_pec_wall_check_lite.py` 的 H 截面解析改为复用转换器契约实现，杜绝两套规则漂移 |
| 回归锁定 | 新增契约测试（小写 x / × / * / 旧尾 X / 非法尺寸 / PEC 与普通不同名），36/36 通过 |
| 部署 | exe 已重打包并更新两个部署位 |

## 3. 其他文件夹需要的后续动作

| 方 | 动作 |
|---|---|
| Revit 插件端（revit-external-tool2.git） | **无需任何改动**——其解析本就忽略大小写且规范形即其 CanonicalName；梁合并比较键行为不变 |
| 任何直接读中间库的脚本/报表 | 若对 `tbl1.BSection / tbl2.CSection` 做**区分大小写的精确匹配**，请改用规范形（大写 X）或按后缀 `@PEC` 判断；推荐复用各自语言中等价的契约解析 |
| 建模/YJK 侧 | 无 |

## 4. 只读合规声明

本次对 `E:\revit-external-tool2.git` 仅执行 `git log / ls-tree / show` 等
只读命令提取契约，未做任何写入、推送或对象修改。
