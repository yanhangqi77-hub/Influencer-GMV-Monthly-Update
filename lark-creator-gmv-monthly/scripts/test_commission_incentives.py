"""Synthetic unit and pipeline tests. No network or real commission changes."""
import argparse
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from commission_incentives import (POLICY_PATH, START, append_note, clean_generated_note,
                                   conditional_formats, evaluate, net_units, rate, apply_incentives)
from prepare_creator_gmv import build, load_manual_sources

POLICY = json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def stats(n, uncertain=None):
    """n units attributed to a single product (503); other products are zero."""
    return {"known_net_units": n, "products": {"503": n, "303": 0, "509": 0}, "uncertain_rows": uncertain or []}


class CommissionTests(unittest.TestCase):
    def evaluate(self, n, organic=10, ad=3, role="普通／开放合作", context=None):
        return evaluate({"Creator Handle": "demo", "503": organic, "Ad": ad, "合作类型": role},
                        stats(n), "2026-09", POLICY, context or {})

    def test_every_standard_boundary(self):
        for n, organic, ad in [(9,None,None),(10,12,4),(29,12,4),(30,13,5),(79,13,5),
                                (80,14,6),(99,14,6),(100,15,7),(499,15,7),(500,18,8),(501,18,8)]:
            with self.subTest(n=n):
                result = self.evaluate(n)
                self.assertEqual(result["can_raise"], organic is not None)
                if organic is not None:
                    self.assertEqual(result["tiers"]["503"]["organic_percent"], organic)
                    self.assertEqual(result["tiers"]["503"]["ad_percent"], ad)

    def test_premium_100_not_500(self):
        self.assertFalse(self.evaluate(99, 15, 5, "优质达人定向邀请")["can_raise"])
        result = self.evaluate(100, 15, 5, "优质达人定向邀请")
        self.assertEqual(result["tiers"]["503"]["organic_percent"], 18)
        self.assertEqual(result["tiers"]["503"]["ad_percent"], 8)

    def test_never_downgrade_other_dimension(self):
        result = self.evaluate(30, 12, 8)
        self.assertTrue(result["can_raise"])
        self.assertIn("503自然13%", result["message"])
        self.assertNotIn("广告5%", result["message"])
        result = self.evaluate(500, 18, 5)
        self.assertIn("涨佣金为广告8%", result["message"])

    def test_no_raise_when_already_at_or_above_tier(self):
        self.assertFalse(self.evaluate(80,14,6)["can_raise"])
        self.assertFalse(self.evaluate(30,15,7)["can_raise"])

    def test_per_product_comparison(self):
        result = evaluate({"Creator Handle":"demo", "503":12,"303":15,"509":13,"Ad":5,"合作类型":"普通／开放合作"},
                          {"known_net_units": 240, "products": {"503":80,"303":80,"509":80}, "uncertain_rows": []},
                          "2026-09", POLICY, {})
        self.assertIn("503自然14%",result["message"])
        self.assertIn("509自然14%",result["message"])
        self.assertNotIn("303自然",result["message"])

    def test_per_product_tier_not_total(self):
        # 503 alone qualifies; 509's few units must not drag 509 up via the total.
        result = evaluate({"Creator Handle":"demo", "503":12,"509":12,"Ad":4,"合作类型":"普通／开放合作"},
                          {"known_net_units": 34, "products": {"503":30,"303":0,"509":4}, "uncertain_rows": []},
                          "2026-09", POLICY, {})
        self.assertIn("503自然13%",result["message"])
        self.assertNotIn("509自然",result["message"])

    def test_legacy_special_and_excluded(self):
        for role in ["核心／特殊达人", "历史高佣", "单独协商"]:
            self.assertFalse(self.evaluate(1000,20,8,role)["can_raise"])
        self.assertEqual(self.evaluate(1000,25,8)["status"],"protected_review")
        self.assertEqual(self.evaluate(1000,10,3,context={"demo":{"eligible":False}})["status"],"excluded")

    def test_missing_and_ambiguous_rates_need_review(self):
        for organic,ad in [(None,None),(12,None),(12,"12 / 5 / 5")]:
            self.assertEqual(self.evaluate(100,organic,ad)["status"],"needs_review")
        self.assertEqual(self.evaluate(100,15,5,role="")["status"],"needs_review")

    def test_rate_formats(self):
        for value in [12,"12","12%",0.12,"12％"]:
            self.assertEqual(rate(value),12)
        for value in ["12/5", "abc12", "-5", "105%"]:
            with self.assertRaises(ValueError): rate(value)

    def test_unknown_role_at_15_does_not_guess_premium(self):
        self.assertEqual(self.evaluate(100,15,5,role="机构")["status"],"needs_review")
        self.assertEqual(self.evaluate(100,15,5,role="standard")["tiers"]["503"]["ad_percent"],7)

    def test_policy_not_applied_before_august(self):
        frame=pd.DataFrame([{"__creator_key__":"demo","__source_row__":2,"Quantity":500,
            "Order Status":"Shipped","Sku Quantity of return":0,"Order Refund Amount":0}])
        rows=[{"__creator_key__":"demo","Creator Handle":"demo","503":10,"Ad":3,"备注":"人工备注"}]
        result=apply_incentives(frame,{"quantity":"Quantity","status":"Order Status"},rows,"2026-07")
        self.assertEqual(result["creators"][0]["status"],"policy_not_applicable")
        self.assertEqual(rows[0]["备注"],"人工备注")

    def test_default_local_context_is_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            policy=root/"commission-policy.json"
            policy.write_text(json.dumps(POLICY),encoding="utf-8")
            (root/"creator-context.local.json").write_text(json.dumps({"creators":{"demo":{"eligible":False}}}),encoding="utf-8")
            frame=pd.DataFrame([{"__creator_key__":"demo","__source_row__":2,"Quantity":500,
                "Order Status":"Shipped","Sku Quantity of return":0,"Order Refund Amount":0}])
            rows=[{"__creator_key__":"demo","Creator Handle":"demo","503":10,"Ad":3}]
            with patch("commission_incentives.POLICY_PATH",policy):
                result=apply_incentives(frame,{"quantity":"Quantity","status":"Order Status"},rows,"2026-09")
            self.assertEqual(result["creators"][0]["status"],"excluded")

    def test_raw_invalid_quantity_not_normalized_into_qualification(self):
        frame=pd.DataFrame([{"__creator_key__":"demo","__source_row__":2,"Quantity":500,
            "__incentive_quantity_raw__":"abc500","Order Status":"Shipped",
            "Sku Quantity of return":0,"Order Refund Amount":0}])
        self.assertTrue(net_units(frame,{"quantity":"Quantity","status":"Order Status"})[0]["demo"]["uncertain_rows"])

    def test_note_is_idempotent_preserves_manual(self):
        manual="人工备注\n涨佣金为20%（人工记录）"
        once=append_note(manual,"涨佣金为503自然13%（待审核）")
        self.assertEqual(once,append_note(once,"涨佣金为503自然13%（待审核）"))
        self.assertEqual(clean_generated_note(once),manual)
        self.assertEqual(append_note(once,""),manual)

    def test_uncertain_refund_never_highlights(self):
        result=evaluate({"Creator Handle":"demo","503":10,"Ad":3},stats(500,[{"source_row":2}]),"2026-09",POLICY,{})
        self.assertIsNone(result["net_units"])
        self.assertFalse(result["can_raise"])

    def net(self,q=5,returned=0,refund=0,amount=500,status="Shipped",aftersale=""):
        df=pd.DataFrame([{"__creator_key__":"demo","__source_row__":2,"Quantity":q,
                          "Sku Quantity of return":returned,"Order Refund Amount":refund,
                          "Order Amount":amount,"Order Status":status,"Cancelation/Return Type":aftersale}])
        return net_units(df,{"quantity":"Quantity","status":"Order Status"})[0]["demo"]

    def test_return_and_refund_not_double_deducted(self):
        self.assertEqual(self.net(returned=2,refund=200)["known_net_units"],3)
    def test_full_refund_zero_even_with_partial_return_count(self):
        self.assertEqual(self.net(returned=2,refund=500)["known_net_units"],0)
    def test_single_unit_refund_zero(self):
        self.assertEqual(self.net(q=1,refund=20)["known_net_units"],0)
    def test_partial_amount_multi_unit_ambiguous(self):
        self.assertTrue(self.net(refund=100)["uncertain_rows"])
    def test_invalid_returns_block_incentive(self):
        for returned in [-1,6,1.5,"abc2"]:
            self.assertTrue(self.net(returned=returned)["uncertain_rows"])
    def test_aftersale_without_numbers_ambiguous(self):
        self.assertTrue(self.net(aftersale="Return/Refund")["uncertain_rows"])
    def test_missing_refund_columns_not_silently_zero(self):
        df=pd.DataFrame([{"__creator_key__":"demo","__source_row__":2,"Quantity":100,"Order Status":"Shipped"}])
        self.assertTrue(net_units(df,{"quantity":"Quantity","status":"Order Status"})[0]["demo"]["uncertain_rows"])
    def test_highlight_owned_marker_and_no_sample_color_override(self):
        rule=conditional_formats(["Creator GMV Summary - 260901~260910"],46)["rules"][0]
        self.assertEqual(rule["ranges"],["A2:A46","X2:X46"])
        self.assertIn(START+" 涨佣金为",rule["properties"]["attrs"][0]["formula"][0])

    def test_pipeline_net_units_month_filter_and_manual_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/"orders.csv"; manual=root/"manual.json"
            headers=["Order ID","Paid Time","Order Status","Creator Username","Content Type","Seller SKU","Quantity","Order Amount","Sku Quantity of return","Order Refund Amount"]
            rows=[["a","2026-09-01","Shipped","demo","Videos","503",30,2000,0,0],
                  ["b","2026-09-10","To ship","demo","LIVE","509",5,500,0,0],
                  ["c","2026-09-11","Cancelled","demo","Videos","503",99,9900,0,0],
                  ["d","2026-09-11","Unpaid","demo","Videos","503",99,9900,0,0],
                  ["e","2026-08-30","Shipped","demo","Videos","503",99,9900,0,0]]
            with source.open("w",newline="",encoding="utf-8") as stream:
                writer=csv.writer(stream);writer.writerow(headers);writer.writerows(rows+[rows[0]])
            manual.write_text(json.dumps({"annotated_csv":"[row=1] Creator Handle,503,Ad,备注\n[row=2] demo,12,4,保留人工备注", "has_more":False}),encoding="utf-8")
            args=argparse.Namespace(input=source,month="latest",target="latest",month_label=None,gmv_column=None,
                output_dir=root/"out",manual_source=[manual],include_sensitive_columns=False,creator_context=None)
            result=build(args)
            self.assertEqual(result["manifest"]["totals"]["total_gmv"],2500)
            self.assertEqual(result["manifest"]["totals"]["total_orders"],2)
            evaluation=result["incentives"]["creators"][0]
            self.assertEqual(evaluation["net_units"],{"503":30,"303":0,"509":5})
            self.assertTrue(evaluation["can_raise"])
            summary=result["tables"]["sheets"][1]
            row=dict(zip(summary["columns"],summary["data"][0]))
            self.assertEqual(row["503"],"12")
            self.assertEqual(row["Ad"],"4")
            self.assertEqual(clean_generated_note(row["备注"]),"保留人工备注")
            self.assertIn("涨佣金为503自然13%／广告5%",row["备注"])
            manual.write_text(json.dumps(summary,ensure_ascii=False),encoding="utf-8")
            self.assertEqual(result["tables"],build(args)["tables"])

    def test_truncated_or_unsupported_manual_source_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"manual.json"
            path.write_text(json.dumps({"annotated_csv":"", "has_more":True}),encoding="utf-8")
            with self.assertRaises(ValueError):load_manual_sources([path],[])
            path.write_text(json.dumps({"cells":[]}),encoding="utf-8")
            issues=[];load_manual_sources([path],issues)
            self.assertTrue(issues)


if __name__=="__main__":
    unittest.main(verbosity=2)
