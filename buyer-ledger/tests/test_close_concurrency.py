"""V-07 冻结：同一账期的修订/签核必须跨进程互斥。

先于修复编写。V-06 的 journal 解决的是"单进程崩溃后能恢复"，
但两个进程同时 revise/signoff 仍可能抢同一个 rN 或写坏签核三件套。

冻结的契约：

1. 两个独立进程同时 ``revise``：只有一个真正生成新版本，另一个得到明确的
   并发冲突（不是静默丢一个版本，也不是写坏索引）。
2. 两个独立进程同时 ``signoff``：只有一个成功，另一个明确冲突；
   签核文件与索引最终一致。
3. 并发失败不能留下 journal 或半成品目录。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUYER = REPO / "buyer-ledger"

BILLS = ("Charge Reference,Total Amount,Currency,Quantity,Unit Price,Rate Plan,Task,Service Date\n"
         "ch-1,20.00,USD,1000,0.020,api-request,t1,2026-10-05\n")
USAGE = ('{"record id":"u1","run":"r1","charge reference":"ch-1","total cost":"20.00",'
         '"currency":"USD","quantity":"1000","unit":"request","rate plan":"api-request",'
         '"financialized":true}\n')
RULES = {"schema_version": "1.0", "rules": [
    {"rule_id": "intake-bills", "kind": "intake", "format": "bills-csv",
     "version": "1", "confirmed_by": "Finance",
     "confirmed_at": "2026-10-01T00:00:00Z", "source_ref": "rules#intake-bills"},
    {"rule_id": "intake-usage", "kind": "intake", "format": "usage-jsonl",
     "version": "1", "confirmed_by": "Finance",
     "confirmed_at": "2026-10-01T00:00:00Z", "source_ref": "rules#intake-usage"},
    {"rule_id": "api-request", "kind": "pricing", "method": "per_unit",
     "unit_price": "0.020", "currency": "USD", "unit": "request",
     "version": "2026-10", "valid_from": "2026-10-01", "valid_to": "2026-10-31",
     "confirmed_by": "Procurement", "confirmed_at": "2026-10-01T00:00:00Z",
     "source_ref": "rules#api-request"}]}


def _run(args, cwd):
    return subprocess.run([sys.executable, "-m", "buyer_ledger", *args],
                          cwd=str(cwd), capture_output=True, text=True)


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.case = base / "case"
        bundle = base / "bundle"
        bundle.mkdir(parents=True)
        (bundle / "bills.csv").write_text(BILLS, encoding="utf-8")
        (bundle / "usage.jsonl").write_text(USAGE, encoding="utf-8")
        (bundle / "rules.json").write_text(json.dumps(RULES), encoding="utf-8")
        job = {"schema": "verovo.period-job/1", "job_id": "close-2026-10-a",
               "buyer": "buyer-1", "vendor": "vendor-1", "account": "acct-1",
               "buyer_signers": ["roy"],
               "period": {"from": "2026-10-01", "to": "2026-11-01",
                          "to_exclusive": True, "timezone": "UTC"},
               "currency": "USD",
               "sources": [{"role": "bills", "path": "bills.csv", "control_domain": "vendor"},
                           {"role": "usage", "path": "usage.jsonl",
                            "control_domain": "buyer_or_vendor_pending_review"}],
               "rule_map": "rules.json", "task_economics_requested": False,
               "approved_by": None}
        (bundle / "period-job.json").write_text(json.dumps(job), encoding="utf-8")
        self.bundle = bundle
        for args in (["close", "init", str(self.case), "--from-job",
                      str(bundle / "period-job.json")],
                     ["close", "ingest", str(self.case), "--bundle", str(bundle)],
                     ["close", "mapping", str(self.case), "--confirm", "--review-by", "roy",
                      "--control-domain", "usage=vendor"]):
            out = _run(args, BUYER)
            self.assertEqual(0, out.returncode, out.stderr + out.stdout)
        out = _run(["close", "calculate", str(self.case)], BUYER)
        self.assertEqual(0, out.returncode, out.stderr + out.stdout)
        out = _run(["close", "report", str(self.case)], BUYER)
        self.assertEqual(0, out.returncode, out.stderr + out.stdout)

    def _spawn_pair(self, args):
        procs = [subprocess.Popen([sys.executable, "-m", "buyer_ledger", *args],
                                  cwd=str(BUYER), stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
                 for _ in range(2)]
        return [(p.wait(), p.stdout.read(), p.stderr.read()) for p in procs]

    def test_concurrent_signoff_has_exactly_one_winner(self):
        code = ("import sys; sys.path.insert(0, %r);"
                "from buyer_ledger import close_revision as r;"
                "r.signoff(%r, reviewer='roy')" % (str(BUYER), str(self.case)))
        procs = [subprocess.Popen([sys.executable, "-c", code], cwd=str(BUYER),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True) for _ in range(2)]
        results = [(p.wait(), p.stderr.read()) for p in procs]
        ok = [r for r in results if r[0] == 0]
        bad = [r for r in results if r[0] != 0]
        self.assertEqual(1, len(ok), results)
        self.assertEqual(1, len(bad), results)
        # 输家有两种合法的显式失败：抢锁时对方还在写（并发冲突），或抢到锁时
        # 对方已签核完（already signed）。两者都拒绝写入；只有静默成功或静默
        # 覆盖才是缺陷。定时器决定落在哪一支，所以断言两种文案都接受，
        # 抢锁分支的文案由下面 test_signoff_while_lock_held 确定性覆盖。
        message = bad[0][1] + ""
        self.assertTrue("并发" in message or "already signed" in message,
                        "另一个进程必须得到显式的并发/已签核冲突，实际：%r"
                        % message)

    def test_signoff_rejects_signer_outside_acceptance_signers(self):
        from buyer_ledger import close_revision

        path = self.case / "canonical/manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["buyer"]["acceptance_signers"] = ["roy"]
        path.write_text(json.dumps(manifest), encoding="utf-8")
        from buyer_ledger import close_report
        close_report.calculate(self.case)
        close_report.build_report(self.case)
        with self.assertRaises(Exception) as caught:
            close_revision.signoff(self.case, reviewer="unauthorized-signer")
        self.assertIn("acceptance_signers", str(caught.exception))
        self.assertEqual("AUTH_REQUIRED", caught.exception.code)
        self.assertFalse((self.case / "signoff.json").exists())
        with self.assertRaises(Exception) as caught:
            close_revision.signoff(self.case, reviewer="unauthorized roy")
        self.assertEqual("AUTH_REQUIRED", caught.exception.code)
        self.assertFalse((self.case / "signoff.json").exists())

    def test_signoff_while_lock_held_reports_concurrency(self):
        """锁被占有时必须立刻报并发冲突，而不是排队、等待或静默重试。"""
        try:
            import fcntl
        except ImportError:  # Windows 走 O_EXCL 兜底，这里不覆盖
            self.skipTest("fcntl 不可用")
        lock_path = self.case / ".close.lock"
        with open(lock_path, "a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                code = ("import sys; sys.path.insert(0, %r);"
                        "from buyer_ledger import close_revision as r;"
                        "r.signoff(%r, reviewer='roy')" % (str(BUYER), str(self.case)))
                proc = subprocess.run([sys.executable, "-c", code], cwd=str(BUYER),
                                      capture_output=True, text=True)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("并发", proc.stderr + proc.stdout)
        self.assertFalse((self.case / ".close.journal").exists(),
                         "被拒的签核不能留下 journal")

    def test_revision_review_uses_the_root_case_lock(self):
        import shutil
        from buyer_ledger import close_revision
        revised = Path(self.tmp.name) / "revised"
        shutil.copytree(self.bundle, revised)
        (revised / "bills.csv").write_text(BILLS.replace("20.00", "21.00"),
                                           encoding="utf-8")
        close_revision.revise(self.case, bundle=revised, reviewer="roy")
        target = self.case / "revisions" / "r2"
        try:
            import fcntl
        except ImportError:
            self.skipTest("fcntl 不可用")
        with open(self.case / ".close.lock", "a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                code = ("import sys; sys.path.insert(0, %r);"
                        "from buyer_ledger import close_report as r;"
                        "r.record_review(%r, reviewer='roy')" % (str(BUYER), str(target)))
                proc = subprocess.run([sys.executable, "-c", code], cwd=str(BUYER),
                                      capture_output=True, text=True)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        self.assertNotEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("并发", proc.stderr + proc.stdout)

    def test_concurrent_revise_keeps_versions_consistent(self):
        code = ("import sys; sys.path.insert(0, %r);"
                "from buyer_ledger import close_revision as r;"
                "r.revise(%r, bundle=%r, reviewer='roy', reason='并发测试')"
                % (str(BUYER), str(self.case), str(self.bundle)))
        procs = [subprocess.Popen([sys.executable, "-c", code], cwd=str(BUYER),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True) for _ in range(2)]
        results = [(p.wait(), p.stderr.read()) for p in procs]
        codes = [c for c, _ in results]
        self.assertEqual(1, codes.count(0), results)
        index = json.loads((self.case / "revisions" / "index.json")
                           .read_text(encoding="utf-8"))
        revisions = index.get("revisions") or []
        on_disk = sorted(p.name for p in (self.case / "revisions").iterdir()
                         if p.is_dir() and not p.name.startswith("."))
        self.assertEqual(sorted(r["revision_id"] for r in revisions), on_disk,
                         "索引与磁盘上的版本必须一致，不能有孤儿")
        self.assertEqual([], [p.name for p in (self.case / "revisions").iterdir()
                              if p.name.startswith(".staging")])


if __name__ == "__main__":
    unittest.main()
