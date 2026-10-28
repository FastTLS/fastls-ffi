"""The regression gate must reject incomplete controls before ranking a candidate."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))
from regression import ARMS, aggregate, compare, evaluate, schedule
from regression import candidate_input, sha
from unittest.mock import patch
import json,tempfile


class FormalCandidate(unittest.TestCase):
    def test_formal_artifact_requires_exact_graph_source_and_manifest_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'dist').mkdir();library=root/'dist'/('fastls.dll' if sys.platform=='win32' else 'libfastls.dylib' if sys.platform=='darwin' else 'libfastls.so')
            library.write_bytes(b'candidate fixture');(root/'go.sum').write_text('verified sums\n')
            (root/'go.mod').write_text('\n'.join('github.com/FastTLS/'+name+' v0.1.1' for name in ('fastls','ftransport','imitate')))
            graph=[dict(Path='github.com/FastTLS/'+name,Version='v0.1.1',Sum='h1:sum',GoModSum='h1:mod',Origin=dict(Hash='b'*40,Ref='refs/tags/v0.1.1')) for name in ('fastls','ftransport','imitate')]
            info=dict(local_dependencies=False,library_sha256=sha(library),module_graph=graph,module_files={p:sha(root/p) for p in ('go.mod','go.sum')},
                sources={'fastls-ffi':'a'*40,**{n:'b'*40 for n in ('fastls','ftransport','imitate')}})
            (root/'dist/module-inputs.json').write_text(json.dumps(graph));(root/'dist/build-info.json').write_text(json.dumps(info))
            with patch('regression.revision',return_value=dict(commit='a'*40,dirty_files=[])):
                self.assertEqual(candidate_input(root)['sources'],info['sources'])
                (root/'go.sum').write_text('changed sums')
                with self.assertRaisesRegex(RuntimeError,'files changed'):candidate_input(root)

POLICY = dict(eligibility=dict(minimum_rounds_per_variant=5, maximum_control_throughput_drift_from_recorded_baseline_fraction=.2),
              candidate_vs_fresh_fastls_control=dict(minimum_median_rps_ratio=.85, minimum_median_mib_per_second_ratio=.85,
                 maximum_median_p95_latency_ratio_when_available=1.2, maximum_cpu_seconds_per_successful_business_byte_ratio=1.25))
CASE = dict(name="small-h1", size=1024, protocol="h1", workload="small")


def row(arm, number):
    return dict(variant=arm, round=number, case=CASE, valid=True, exit_code=0,
                metrics=dict(success=100, successful_bytes=102400, failed=0, errors={}, contract_errors=[],
                             warmup=dict(success=10, failed=0, errors={}), rps=100, mib_per_second=100 / 1024,
                             p95_seconds=.01, server=dict(connections={"1": dict(protocol="HTTP/1.1")}),
                             native_final=dict(open_requests=0, open_sessions=0, workers=0, notifiers=0), tls_wrong_ca_rejected=True),
                resources=dict(cpu_seconds=.5, peak_rss_bytes=50 * 1048576))


class RegressionGate(unittest.TestCase):
    def setUp(self):
        self.rows = [row(arm, number) for number in range(1, 6) for arm in ARMS]
        self.history = {CASE["name"]: [row("history", number) for number in range(1, 6)]}

    def evaluate(self, integrity=True):
        return evaluate(self.rows, self.history, [CASE], POLICY, integrity)

    def test_identical_arms_pass_and_every_case_is_interleaved(self):
        self.assertEqual(self.evaluate()["status"], "passed")
        plan = schedule([CASE, dict(CASE, name="other")])
        self.assertEqual(len(plan), 30)
        for index in range(0, len(plan), 3):
            self.assertEqual({r["variant"] for r in plan[index:index+3]}, set(ARMS))
        self.assertGreater(len({tuple(p["variant"] for p in plan[i:i+3]) for i in range(0, len(plan), 3)}), 2)

    def test_failed_or_missing_round_cannot_be_dropped(self):
        self.rows.pop()
        self.assertEqual(self.evaluate()["status"], "ineligible")
        self.setUp()
        self.rows[0]["metrics"]["warmup"]["failed"] = 1
        result = self.evaluate()
        self.assertEqual(result["cases"][0]["candidate"]["status"], "ineligible")
        self.assertTrue(any("warmup" in reason for reason in result["cases"][0]["eligibility_failures"]))

    def test_historical_drift_invalidates_an_otherwise_good_candidate(self):
        for r in self.history[CASE["name"]]:
            r["metrics"]["mib_per_second"] *= .5
        result = self.evaluate()
        self.assertEqual(result["status"], "ineligible")
        self.assertEqual(result["cases"][0]["candidate"]["diagnostic_status"], "passed")

    def test_aa_failure_is_not_asserted_to_be_a_candidate_regression(self):
        for r in self.rows:
            if r["variant"] == "control-b":
                r["resources"]["cpu_seconds"] *= 2
        self.assertEqual(self.evaluate()["status"], "ineligible")

    def test_real_candidate_regression_fails_with_eligible_controls(self):
        for r in self.rows:
            if r["variant"] == "candidate":
                r["metrics"]["rps"] *= .84
        result = self.evaluate()
        self.assertTrue(result["cases"][0]["eligible"])
        self.assertEqual(result["status"], "failed")

    def test_missing_metric_or_integrity_mismatch_never_passes(self):
        del self.rows[0]["resources"]["cpu_seconds"]
        self.assertEqual(self.evaluate()["status"], "ineligible")
        self.setUp()
        self.assertEqual(self.evaluate(False)["status"], "ineligible")

    def test_zero_cpu_is_unmeasurable_and_p95_requires_twenty_samples(self):
        stats = aggregate(self.history[CASE["name"]])
        empty = dict(stats, cpu_seconds_per_byte=0)
        self.assertEqual(compare(stats, empty, POLICY)["status"], "unevaluated")
        self.rows[0]["metrics"]["p95_seconds"] = None
        self.assertIsNone(aggregate(self.rows[:1]))
        self.rows[0]["metrics"].update(success=1, successful_bytes=1024)
        self.assertFalse(aggregate(self.rows[:1])["p95_applicable"])

    def test_protocol_and_upload_size_mismatch_invalidate_the_run(self):
        self.rows[0]["metrics"]["server"]["connections"]["1"]["protocol"] = "HTTP/2.0"
        self.assertEqual(self.evaluate()["status"], "ineligible")
        self.setUp()
        self.rows[0]["metrics"]["successful_bytes"] -= 1
        self.assertEqual(self.evaluate()["status"], "ineligible")


if __name__ == "__main__":
    unittest.main(verbosity=2)
