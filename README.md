# 订单清洗与达人 GMV 更新 Skill

用于将 TikTok Shop 原始订单清洗为订单明细和达人 GMV 汇总，再由 Codex 通过已授权的飞书工具写入指定工作表。

## 日常指令

| 你说的话 | 只更新这组工作表 |
| --- | --- |
| 更新达人 2026 年 9 月 GMV | `Cleaned Orders - 2026-09`、`Creator GMV Summary - 2026-09` |
| 更新最新 GMV / 更新达人最新 GMV | `Cleaned Orders - YYMMDD~YYMMDD`、`Creator GMV Summary - YYMMDD~YYMMDD` |
| 更新最新 GMV，先给预览，不写飞书 | 只生成本地结果，待明确确认后写入 |

“最新”沿用同一对工作表的 sheet_id，改名后覆盖，不为每个日期范围新建表；月度与最新两组不会互相顺带更新。

## 当前统计口径

- 以 Paid Time 选择月份；最新模式默认选文件中最新付款月份，再按该月清洗后有效订单的最早、最晚付款日命名。多月文件不会自动汇总所有月份。
- 完整快照重算覆盖；增量订单需先确定完整合并来源。
- 默认 GMV 为 Order Amount；排除 cancel/unpaid，删除完全重复行，其他重复订单号拦截。
- Creator Username 空白或 62 归入 Unattributed，不参与达人排名及份额分母。
- 输出商品件数、Videos/LIVE/Product cards 渠道 GMV 与订单数、达人排名及占比。
- 佣金等七个人工字段按达人账号保留，不能按行号继承。
- 表头位于第 1 行；汇总表冻结首行和 Rank、Creator Handle 两列。
- 佣金区采用浅紫色表头、白色数据区。
- 维护现有“佣金情况汇总”的公式引用：滚动表改名或数据范围增长后，检查并按需修复引用。
- 默认排除买家姓名、地址、电话等履约敏感字段。

## 版本范围与边界

Python 脚本只生成本地数据与样式 JSON，不直接登录或写入飞书。工作表改名、覆盖、冻结、佣金关联检查及回读核验由 Codex 按 Skill 指令调用飞书工具完成。

本版包含现有佣金关联的维护规则，不是一个脱离 Codex 后可自行运行的定时同步程序。“没有寄样但出单”的跨表匹配与高亮曾单独执行，但尚未封装进此 Skill，也不会随每次更新 GMV 自动重算。不要把旧高亮名单当作持续有效的寄样判断；需要时另行要求重新核对。

## 文件说明

- `SKILL.md`：触发方式、指令路由与完整执行流程。
- `references/business-rules.md`：清洗、归因、SKU、GMV、人工字段和对账口径。
- `references/feishu-workflow.md`：目标定位、写入、改名、格式和核验规则。
- `scripts/prepare_creator_gmv.py`：本地清洗与生成脚本。
- `scripts/test_prepare_creator_gmv.py`：仅使用虚构数据的回归测试。
- `agents/openai.yaml`：Skill 展示与自动触发配置。
- `requirements.txt`：经此次运行验证的 Python 核心依赖。

## 使用环境

需要支持 Skill 的 Codex、Python 3.10+、pandas，以及已安装并授权的 lark-cli。依赖飞书电子表格能力 `lark-sheets`；按需使用 `lark-shared`、`lark-drive` 和 `spreadsheets`。本包不包含这些外部能力，也不包含任何访问凭据。

```text
python -m pip install -r requirements.txt
python scripts/test_prepare_creator_gmv.py
```

核心脚本直接读取 XLSX，也支持 CSV/TSV；旧式 XLS 额外需要 xlrd。未将 XLS 纳入此次验证。

在 Codex 中安装整个 `lark-creator-gmv-monthly` 文件夹后，附上订单文件并使用上述自然语言指令。飞书访问仍取决于当前账号授权和目标表权限。

## 本地预览示例

在 Skill 目录中运行：

```text
python scripts/prepare_creator_gmv.py --input orders.xlsx --month latest --target latest --output-dir ./preview
python scripts/prepare_creator_gmv.py --input orders.xlsx --month 2026-09 --target monthly --output-dir ./preview-monthly
```

输出 `manifest.json`、`lark-table-payload.json`、`lark-styles-payload.json`。只有 manifest 状态为 ready 且无 issues 才可进入飞书写入；本地脚本通过不代表飞书已经更新。

`--manual-source` 需要包含 `columns` 与 `data` 的表格 JSON（可在外层对象中嵌套），不是任意飞书读取响应。使用 annotated_csv 或 cells 格式的读取结果时，应先按实际行号、列标转换；若原表存在人工字段而脚本载入记录为 0，必须停止核查。

```json
{
  "columns": ["Creator Handle", "合作类型", "503", "303", "509", "Ad", "持续时间", "备注"],
  "data": [["demo_creator", "", 12, "", "", "4", "2026/09/30", "示例"]]
}
```

## 本次验证

- 两个版本均通过 Skill 结构校验。
- 5 项虚构数据测试通过：最新范围命名及数值样式、月度/最新路由、按账号保留人工字段、重复订单号拦截、未知渠道拦截。
- 核心生成脚本已用用户本次原始 XLSX 重新运行；清洗统计与先前验证结果一致。真实订单及本地验证输出未纳入压缩包。
- 此次验证没有再次写入飞书；外部依赖、权限和实际写入仍需在使用时按流程核验。
