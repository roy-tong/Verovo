import json
import multiprocessing
import unittest
from unittest import mock

from buyer_ledger import close as close_mod
from buyer_ledger.close import CloseError, ingest, init_case
from tests.test_close_period_import import CloseCaseTestBase


def _paused_signoff(case_dir, started, release, queue):
    from buyer_ledger import close_revision

    def hook(stage, info):
        if stage == "signoff:signoff-written":
            started.set()
            if not release.wait(30):
                raise RuntimeError("signoff test timed out")

    close_revision._CRASH_HOOK = hook
    try:
        record = close_revision.signoff(case_dir, reviewer="roy", revision_id="r2")
        queue.put(("ok", record["state"]))
    except Exception as exc:
        queue.put(("error", type(exc).__name__, str(exc)))


def _repository_api():
    from buyer_ledger.case_repository import CaseRepository
    return CaseRepository


def _period_api():
    from buyer_ledger.period_case import PeriodCase
    return PeriodCase


def _api():
    return _repository_api(), _period_api()


class CaseRepositoryTests(CloseCaseTestBase):
    def test_repository_api_exists(self):
        try:
            CaseRepository, PeriodCase = _api()
        except ModuleNotFoundError as exc:
            self.fail("case repository API is missing: %s" % exc)
        self.assertTrue(callable(CaseRepository))
        self.assertTrue(callable(PeriodCase))

    def test_missing_state_returns_none_but_corrupt_state_fails_loudly(self):
        CaseRepository = _repository_api()
        self.write_bundle()
        init_case(self.case, job_path=self.bundle / "period-job.json")
        repo = CaseRepository(self.case)
        self.assertIsNone(repo.read_json("review"))
        (self.case / "review.json").write_text("{bad", encoding="utf-8")
        with self.assertRaises(CloseError) as raised:
            repo.read_json("review")
        self.assertEqual("ARTIFACT_PARTIAL", raised.exception.code)
        self.assertIn("review.json", str(raised.exception))

    def test_legacy_close_writes_use_repository_boundary(self):
        self.write_bundle()
        from buyer_ledger.case_repository import CaseRepository
        from buyer_ledger.close import confirm_mapping
        original = CaseRepository.atomic_write_bytes
        targets = []

        def record(repo, target, data):
            targets.append(repo.case_dir / target if isinstance(target, str) else target)
            return original(repo, target, data)

        with mock.patch.object(CaseRepository, "atomic_write_bytes", record):
            init_case(self.case, job_path=self.bundle / "period-job.json")
            ingest(self.case, bundle=self.bundle)
            confirm_mapping(self.case, reviewer="finance-reviewer")
        expected = {
            self.case / "period-job.json",
            self.case / "intake" / "scan.json",
            self.case / "intake" / "mapping-draft.json",
            self.case / "intake" / "mapping-confirmed.json",
            self.case / "canonical" / "rule-map.json",
            self.case / "canonical" / "manifest.json",
        }
        self.assertTrue(expected.issubset(set(targets)), targets)

    def test_atomic_write_replaces_bytes_without_changing_case_root(self):
        CaseRepository = _repository_api()
        self.write_bundle()
        init_case(self.case, job_path=self.bundle / "period-job.json")
        repo = CaseRepository(self.case)
        target = self.case / "review.json"
        repo.atomic_write_bytes(target, b'{"state":"reviewed"}\n')
        self.assertEqual(b'{"state":"reviewed"}\n', target.read_bytes())
        self.assertEqual({"state": "reviewed"}, repo.read_json("review"))
        self.assertFalse(list(self.case.glob("*.tmp-*")))


class PeriodCaseProjectionTests(CloseCaseTestBase):
    def test_period_case_matches_legacy_projection_and_writes_nothing(self):
        _, PeriodCase = _api()
        self.write_bundle()
        init_case(self.case, job_path=self.bundle / "period-job.json")
        ingest(self.case, bundle=self.bundle)
        expected = close_mod.build_case(self.case)
        before = {path: path.read_bytes() for path in self.case.rglob("*") if path.is_file()}
        actual = PeriodCase(self.case).project()
        after = {path: path.read_bytes() for path in self.case.rglob("*") if path.is_file()}
        self.assertEqual(expected, actual)
        self.assertEqual(before, after)
        self.assertEqual("verovo.buyer-close-case/1", actual["schema"])
        self.assertEqual(json.loads(json.dumps(actual)), actual)

    def test_projection_rejects_changes_during_snapshot_read(self):
        from buyer_ledger import period_case
        self.write_bundle()
        init_case(self.case, job_path=self.bundle / "period-job.json")
        ingest(self.case, bundle=self.bundle)
        original = period_case._project_case

        def change_during_read(repository):
            payload = original(repository)
            repository.atomic_write_bytes("review.json", b'{"state":"reviewed"}\n')
            return payload

        with mock.patch.object(period_case, "_project_case", change_during_read):
            with self.assertRaises(CloseError) as raised:
                period_case.PeriodCase(self.case).project()
        self.assertEqual("STALE_SOURCE", raised.exception.code)

    def test_projection_rejects_signoff_from_another_revision(self):
        from buyer_ledger import close_revision
        from tests.test_close_concurrency import ConcurrencyTests
        fixture = ConcurrencyTests(methodName="test_concurrent_signoff_has_exactly_one_winner")
        fixture.setUp()
        try:
            close_revision.signoff(fixture.case, reviewer="roy")
            report_path = fixture.case / "report" / "report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["revision_id"] = "r2"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(CloseError) as raised:
                close_mod.build_case(fixture.case)
            self.assertEqual("STALE_SOURCE", raised.exception.code)
        finally:
            fixture.doCleanups()

    def test_projection_rejects_signoff_from_another_report_fingerprint(self):
        from buyer_ledger import close_revision
        from tests.test_close_concurrency import ConcurrencyTests
        fixture = ConcurrencyTests(methodName="test_concurrent_signoff_has_exactly_one_winner")
        fixture.setUp()
        try:
            close_revision.signoff(fixture.case, reviewer="roy")
            report_path = fixture.case / "report" / "report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["replay"]["output_snapshot_fingerprint"] = "different-report"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(CloseError) as raised:
                close_mod.build_case(fixture.case)
            self.assertEqual("STALE_SOURCE", raised.exception.code)
        finally:
            fixture.doCleanups()

    def test_projection_accepts_signoff_bound_to_current_report(self):
        from buyer_ledger import close_revision
        from tests.test_close_concurrency import ConcurrencyTests
        fixture = ConcurrencyTests(methodName="test_concurrent_signoff_has_exactly_one_winner")
        fixture.setUp()
        try:
            close_revision.signoff(fixture.case, reviewer="roy")
            projected = close_mod.build_case(fixture.case)
            self.assertEqual("delivered", projected["c01_status"]["task_state"])
            self.assertEqual("r1", projected["review_flow"]["signoff_revision"])
            self.assertEqual("signed_with_open_items", projected["review_flow"]["signoff_state"])
        finally:
            fixture.doCleanups()

    def test_projection_refuses_uncommitted_signoff_transaction(self):
        from buyer_ledger import close_revision
        from tests.test_close_concurrency import ConcurrencyTests
        fixture = ConcurrencyTests(methodName="test_concurrent_signoff_has_exactly_one_winner")
        fixture.setUp()
        context = multiprocessing.get_context("spawn")
        started = context.Event()
        release = context.Event()
        queue = context.Queue()
        process = context.Process(
            target=_paused_signoff, args=(str(fixture.case), started, release, queue),
            daemon=True)
        try:
            usage = fixture.bundle / "usage.jsonl"
            usage.write_text(
                usage.read_text(encoding="utf-8").replace('"quantity":"1000"', '"quantity":"1001"'),
                encoding="utf-8")
            close_revision.revise(
                fixture.case, bundle=fixture.bundle, reviewer="roy", reason="new usage export")
            process.start()
            self.assertTrue(started.wait(30))
            with self.assertRaises(CloseError) as raised:
                close_mod.build_case(fixture.case / "revisions" / "r2")
            self.assertEqual("REVIEW_REQUIRED", raised.exception.code)
        finally:
            release.set()
            process.join(30)
            if process.is_alive():
                process.terminate()
                process.join(5)
            fixture.doCleanups()
        self.assertEqual(0, process.exitcode)
        self.assertEqual(("ok", "signed_with_open_items"), queue.get(timeout=5))


if __name__ == "__main__":
    unittest.main()
