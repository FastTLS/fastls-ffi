"""Checks that performance reports cannot hide failures or mix hosts."""
import json
import hashlib
import math
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"benchmarks"))
from common import Histogram, cases
from report import summaries, render
from run import snapshot, fastls_input
from worker import Client, require_certificate_error
import psutil


class BenchmarkContract(unittest.TestCase):
    def test_installed_native_is_verified_without_sibling_checkouts(self):
        with tempfile.TemporaryDirectory() as directory:
            package=Path(directory)/"fastls"
            native=package/"_lib";native.mkdir(parents=True)
            filename="fastls.dll" if os.name=="nt" else "libfastls.dylib" if sys.platform=="darwin" else "libfastls.so"
            library=native/filename;library.write_bytes(b"released native library")
            info=dict(package_version="0.1.4",library_sha256=hashlib.sha256(library.read_bytes()).hexdigest(),sources={"fastls":"release-sha"})
            (native/"build-info.json").write_text(json.dumps(info))
            class Distribution:
                version="0.1.4"
                def locate_file(self,name):
                    return Path(directory)/name
            with patch("run.importlib.metadata.distribution",return_value=Distribution()), patch("run.revision",side_effect=AssertionError("must not use sibling source")):
                result=fastls_input("installed")
                self.assertEqual(result["python_path"],str(package.parent.resolve()))
                self.assertEqual(result["sources"],{"fastls":"release-sha"})
                library.write_bytes(b"modified library")
                with self.assertRaisesRegex(RuntimeError,"SHA256"):
                    fastls_input("installed")
                library.write_bytes(b"released native library")
                info["package_version"]="wrong-version"
                (native/"build-info.json").write_text(json.dumps(info))
                with self.assertRaisesRegex(RuntimeError,"version mismatch"):
                    fastls_input("installed")

    def test_wrong_ca_requires_a_certificate_failure(self):
        require_certificate_error(Exception("x509: certificate signed by unknown authority"))
        require_certificate_error(Exception("CERTIFICATE_VERIFY_FAILED"))
        for error in (TimeoutError("request timed out"),ConnectionError("connection refused")):
            with self.assertRaisesRegex(AssertionError,"different reason"):
                require_certificate_error(error)

    def test_sampler_includes_actual_interpreter_behind_launcher(self):
        code="import json,os,psutil,sys; data=bytearray(b'x'*(16*1024*1024)); print(json.dumps({'pid':os.getpid(),'rss':psutil.Process().memory_info().rss}),flush=True);sys.stdin.readline()"
        process=subprocess.Popen([sys.executable,"-c",code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        try:
            actual=json.loads(process.stdout.readline())
            sampled=snapshot(psutil.Process(process.pid))
            self.assertIn(actual["pid"],{p["pid"] for p in sampled["processes"]})
            self.assertGreaterEqual(sampled["rss_bytes"],actual["rss"]-1024*1024)
            self.assertEqual(sampled["threads"],sum(p["threads"] for p in sampled["processes"]))
        finally:
            process.stdin.close();process.wait(timeout=5);process.stdout.close()

    def test_quantiles_are_bounded_and_do_not_retain_samples(self):
        h=Histogram()
        values=[.0001,.001,.01,.1,1.0]*10000
        for value in values: h.add(value)
        for fraction,expected in ((.5,.01),(.95,1.0)):
            actual=h.percentile(fraction)
            self.assertGreaterEqual(actual,expected)
            self.assertLessEqual(actual,expected*1.021+.000001)
        self.assertEqual(len(h.buckets),5)
        self.assertIsNone(Histogram().percentile(.5))

    def test_reports_preserve_errors_and_do_not_combine_hosts(self):
        good=dict(case=dict(name="small"),client="fastls",valid=True,metrics=dict(rps=10,failed=0),resources={})
        failed=dict(case=dict(name="small"),client="fastls",valid=False,metrics=dict(rps=10000,failed=1),resources={},error="failure")
        result=summaries([dict(environment_id="a",runs=[good,failed]),dict(environment_id="b",runs=[good])])
        self.assertEqual(len(result),2)
        first=result[0]
        self.assertNotIn("rps",first)
        self.assertEqual((first["valid_rounds"],first["rounds"],first["failed"]),(1,2,1))
        self.assertEqual(first["errors"],["failure"])
        self.assertEqual(result[1]["rps"]["median"],10)
        warm_failure=dict(good,valid=False,metrics=dict(rps=10,failed=0,warmup=dict(failed=3)))
        warming=summaries([dict(environment_id="c",runs=[warm_failure])])[0]
        self.assertEqual((warming["failed"],warming["warmup_failed"]),(0,3))
        self.assertNotIn("rps",warming)
        changed=summaries([dict(environment_id="a",runs=[good],source_changed_during_run=True)])[0]
        self.assertEqual(changed["valid_rounds"],0)
        self.assertNotIn("rps",changed)

    def test_protocol_applicability_and_sharding_preserve_comparisons(self):
        plan=cases("full")
        self.assertEqual(len(plan),len({c["name"] for c in plan}))
        self.assertTrue(all("requests" not in c["clients"] for c in plan if c["protocol"]=="h2"))
        self.assertTrue(all(c["size"]==64*1048576 for c in plan if c["workload"] in ("download","upload","slow","gzip")))
        shards=[plan[i::6] for i in range(6)]
        self.assertEqual(sum(map(len,shards)),len(plan))
        self.assertTrue(all(c["concurrency"]<=128 for c in plan))
        self.assertTrue(all(c["clients"] and set(c["clients"]) <= {"requests", "fastls", "fastls-poll"} for c in plan))
        self.assertTrue(all(c["clients"] == ["requests", "fastls"] for c in plan if c["protocol"] == "h1" and c["mode"] == "sync"))
        for name in ("notifications-small-paired","notifications-idle-paired"):
            paired=next(c for c in plan if c["name"]==name)
            self.assertEqual(paired["clients"],["fastls","fastls-poll"])

    def test_unsupported_client_is_rejected_before_loading_sdk(self):
        with self.assertRaisesRegex(ValueError, "unsupported benchmark client"):
            Client({"client": "unknown"})

    def test_report_is_offline_and_escapes_embedded_json(self):
        report=dict(environment_id="<script>alert(1)</script>",profile="smoke",runs=[])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"report.html";render([report],path)
            text=path.read_text(encoding="utf-8")
            self.assertNotIn(report["environment_id"],text)
            self.assertNotIn("fetch(",text)
            self.assertIn("\\u003cscript",text)


if __name__=="__main__": unittest.main()
