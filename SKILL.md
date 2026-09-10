---
name: lark-creator-gmv-monthly
description: "清洗 TikTok Shop 原始订单表并统计达人 GMV；按用户指令分别更新飞书中的指定月份表或最新表。当用户提供订单 Excel/CSV 并要求更新某月达人 GMV 或达人最新 GMV 时使用。"
metadata:
  requires:
    bins: ["lark-cli"]
---

# 达人 GMV 月度更新

将原始订单清洗、汇总为达人 GMV 数据，并同步到飞书电子表格 **Creator GMV Summary**。

## 指令路由与固定输出

严格按用户用语选择写入范围，不得顺带更新另一组：

- 用户说“更新达人 X 月 GMV”时，只更新：
  - `Cleaned Orders - YYYY-MM`
  - `Creator GMV Summary - YYYY-MM`
- 用户说“更新达人最新 GMV”时，只更新：
  - `Cleaned Orders - 最新`
  - `Creator GMV Summary - 最新`
- 只有用户明确要求同时更新月度与最新 GMV 时，才更新四个工作表。
- 用户只说“更新达人 GMV”且无法判断模式时，先询问要更新指定月份还是最新数据。

月份默认使用 `YYYY-MM`，避免跨年份重名。若用户明确指定其他月份标签，可用该标签替代。

默认目标表：`https://my.feishu.cn/sheets/LEq1sWnjPh07MJt6yPxcniEqnrg`。若用户提供新的表格链接，以用户提供的链接为准。

## 使用前必读

1. 读取 `references/business-rules.md`，严格执行数据口径。
2. 读取 `references/feishu-workflow.md`，遵循飞书写入、人工字段保护和核验步骤。
3. 使用 `spreadsheets` skill 读取 Excel/CSV；使用 `lark-sheets` skill 更新飞书表格。
4. 只有按标题查找目标表时才使用 `lark-drive`；认证或权限问题使用 `lark-shared`。

## 默认边界

- 输入默认是截至导出时点的完整订单快照；当月尚未结束也可作为月累计快照更新。同月重复执行采用重算覆盖，不追加。
- 如果用户说明文件只包含新增订单，或文件显然是增量而不是完整导出，先确认合并来源，不能直接覆盖月表。
- 月份由 `Paid Time` 判定；用户未指定月份时，选择文件中最新的有效付款月份。
- GMV 默认取 `Order Amount`，即订单实付金额。
- `Creator Handle` 为空或值为 `62` 时归为 `Unattributed`。
- 未识别渠道会中止；未识别 SKU 归到 `Other Qty` 并写入审计信息。
- 飞书清洗表默认排除姓名、电话、地址、邮编、买家留言、税号等敏感履约字段。
- 人工维护列必须按规范化后的 `Creator Handle` 继承，禁止按行号继承。

## 执行流程

### 1. 检查输入和目标

- 确认输入文件存在且可读。
- 确认目标飞书表可访问，并只读取本次目标工作表的当前状态；可只读其他汇总表作为人工字段回填来源。
- 读取目标汇总表中的人工维护列，保存为后续合并源。

### 2. 生成待写入数据

通过工作区依赖提供的 Python 运行：

```powershell
& <bundled-python> scripts/prepare_creator_gmv.py `
  --input <订单文件> `
  --month <YYYY-MM|latest> `
  --target <monthly|latest> `
  --output-dir <临时输出目录> `
  --manual-source <飞书汇总表读取结果.json>
```

可选参数：

- `--month-label <标签>`：覆盖工作表名称中的月份标签。
- `--target monthly`：只生成两个指定月份工作表的写入包。
- `--target latest`：只生成两个“最新”工作表的写入包。
- `--target both`：仅在用户明确要求两组都更新时使用。
- `--gmv-column <列名>`：用户明确要求改变 GMV 口径时使用。
- `--include-sensitive-columns`：只有用户明确要求写入履约敏感字段时使用。
- 可重复传入 `--manual-source`，后传入的同名达人字段优先。

脚本只读取源文件并生成本地 JSON，不直接修改飞书。生成：

- `lark-table-payload.json`
- `lark-styles-payload.json`
- `manifest.json`

### 3. 审核清洗结果

读取 `manifest.json`，至少确认：

- 选择的月份与用户要求一致；
- 原始行数、月份内行数、状态排除数、精确重复行数、清洗后订单数合理；
- 汇总 GMV 等于清洗明细 GMV；
- 各渠道 GMV 之和等于总 GMV；
- 没有重复订单号、未知渠道或订单号精度风险；
- 未识别 SKU 已列出并确认可接受。

出现 `issues` 时停止，不得写入飞书。

### 4. 更新飞书

严格按 `references/feishu-workflow.md` 执行：

1. 月度模式只写并核验 `Cleaned Orders - YYYY-MM` 与 `Creator GMV Summary - YYYY-MM`。
2. 最新模式只写并核验 `Cleaned Orders - 最新` 与 `Creator GMV Summary - 最新`。
3. 用户明确要求两组都更新时，先写月度并回读核验，再写最新并回读核验。

若旧表占用范围大于新数据范围，需要清除旧尾部数据；这属于高风险清除操作，必须先 dry-run 并取得用户明确确认后再执行。

## 完成标准

只有同时满足以下条件才能报告成功：

- 本次指令对应的两个目标工作表均存在且更新完成；若明确要求两组，则核验四个工作表；
- 同一目标组内的明细行数、汇总 GMV、渠道 GMV 对账一致；
- 汇总 GMV 与清洗订单 GMV 对账一致；
- 人工维护列按达人正确保留；
- 已从飞书回读关键区域并完成核验；
- 向用户说明月份、订单数、达人 GMV、未归因 GMV及任何异常 SKU。
