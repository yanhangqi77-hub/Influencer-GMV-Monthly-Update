#!/usr/bin/env python3
"""Synthetic regression checks; no Feishu calls or real business data."""
import argparse
import csv
import json
import tempfile
import unittest
from pathlib import Path

from prepare_creator_gmv import build

COLUMNS = ["Order ID", "Paid Time", "Order Status", "Creator Username",
           "Content Type", "Seller SKU", "Quantity", "Order Amount", "Recipient"]
ROWS = [
    ["900000000000000001", "2026-09-01 01:00:00", "Completed", "demo_A", "Videos", "HC503", 1, 100, "Synthetic"],
    ["900000000000000002", "2026-09-10 12:00:00", "Completed", "demo_a", "LIVE", "HC509", 2, 200, "Synthetic"],
    ["900000000000000003", "2026-09-03 01:00:00", "Completed", "62", "Product cards", "HC303", 1, 50, "Synthetic"],
    ["900000000000000004", "2026-09-30 01:00:00", "Cancelled", "demo_c", "Videos", "HC503", 1, 999, "Synthetic"],
    ["900000000000000005", "2026-08-30 01:00:00", "Completed", "demo_d", "Videos", "HC503", 1, 500, "Synthetic"],
]

class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "orders.csv"
        self.write_rows(ROWS + [ROWS[0]])
    def tearDown(self):
        self.temp.cleanup()
    def write_rows(self, rows):
        with self.source.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(COLUMNS)
            writer.writerows(rows)
    def args(self, **changes):
        values = dict(input=self.source, month="latest", target="latest",
                      month_label=None, gmv_column=None, output_dir=self.root / "out",
                      manual_source=[], include_sensitive_columns=False)
        values.update(changes)
        return argparse.Namespace(**values)
    def test_latest_values_names_and_style(self):
        result = build(self.args())
        manifest = result["manifest"]
        self.assertEqual(manifest["status"], "ready")
        self.assertEqual(manifest["latest_range_label"], "260901~260910")
        self.assertEqual(manifest["totals"]["total_gmv"], 350)
        self.assertEqual(manifest["totals"]["attributed_gmv"], 300)
        self.assertEqual(manifest["totals"]["unattributed_gmv"], 50)
        self.assertEqual(manifest["row_counts"]["cleaned_orders"], 3)
        self.assertEqual(manifest["row_counts"]["exact_duplicates_removed"], 1)
        self.assertEqual(manifest["row_counts"]["attributed_creators"], 1)
        self.assertEqual(len(result["tables"]["sheets"]), 2)
        cleaned, summary = result["tables"]["sheets"]
        self.assertNotIn("Recipient", cleaned["columns"])
        self.assertEqual(cleaned["data"][0][0], "900000000000000001")
        self.assertEqual(summary["start_cell"], "A1")
        style = result["styles"]["styles"][1]
        self.assertEqual(style["freeze"], {"rows": 1, "cols": 2})
        self.assertTrue(any(s["range"] == "R1:X1" and s.get("background_color") == "#EBD8EF"
                            for s in style["cell_styles"]))
    def test_monthly_and_both_routing(self):
        monthly = build(self.args(target="monthly"))["manifest"]
        self.assertEqual(set(monthly["target_sheets"].values()),
                         {"Cleaned Orders - 2026-09", "Creator GMV Summary - 2026-09"})
        both = build(self.args(target="both", month_label="Sep"))["manifest"]
        self.assertEqual(len(both["target_sheets"]), 4)
        self.assertEqual(both["target_sheets"]["latest_summary"], "Creator GMV Summary - 260901~260910")
        self.assertEqual(both["target_sheets"]["monthly_summary"], "Creator GMV Summary - Sep")
    def test_manual_fields_join_by_account(self):
        source = self.root / "manual.json"
        source.write_text(json.dumps({"columns": ["Creator Handle", "503", "备注"],
                                      "data": [[" DEMO_A ", 12, "Synthetic note"]]}), encoding="utf-8")
        result = build(self.args(manual_source=[source]))
        summary = result["tables"]["sheets"][1]
        row = dict(zip(summary["columns"], summary["data"][0]))
        self.assertEqual(row["503"], 12)
        self.assertEqual(row["备注"], "Synthetic note")
        self.assertEqual(result["manifest"]["manual_creator_records_loaded"], 1)
    def test_duplicate_order_id_blocks(self):
        altered = list(ROWS[0])
        altered[7] = 150
        self.write_rows(ROWS + [altered])
        self.assertTrue(any("Duplicate Order IDs" in issue for issue in build(self.args())["issues"]))
    def test_unknown_channel_blocks(self):
        altered = list(ROWS[0])
        altered[4] = "unknown-channel"
        self.write_rows([altered])
        self.assertTrue(any("Unknown channels" in issue for issue in build(self.args())["issues"]))

if __name__ == "__main__":
    unittest.main(verbosity=2)
