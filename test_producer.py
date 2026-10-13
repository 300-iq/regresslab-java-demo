import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch
import run_benchmarks as producer

class ProducerTests(unittest.TestCase):
    def fixture(self):
        return [{"benchmark": name, "jmhVersion": "1.37", "mode": "avgt", "threads": 1, "forks": 1,
                 "warmupIterations": 3, "measurementIterations": 5, "warmupTime": "1 s", "measurementTime": "1 s",
                 "params": {"events": "10000"}, "primaryMetric": {"scoreUnit": "ns/op", "rawData": [[1,2,3,4,5]]}}
                for name in producer.CASES]
    def test_alternates_order_and_changes_initial_arm(self):
        self.assertEqual(producer.order(2), ["BASELINE", "CANDIDATE", "CANDIDATE", "BASELINE", "BASELINE", "CANDIDATE"])
        self.assertEqual(producer.order(3), ["CANDIDATE", "BASELINE", "BASELINE", "CANDIDATE", "CANDIDATE", "BASELINE"])
    def test_keeps_actual_fork_iterations_and_rejects_bad_jmh(self):
        with tempfile.TemporaryDirectory() as folder:
            file = pathlib.Path(folder) / "jmh.json"
            file.write_text(json.dumps(self.fixture()))
            self.assertEqual(producer.parse_jmh(file)[0]["values"], [1,2,3,4,5])
            for field, value in [("warmupIterations", 0), ("forks", 2), ("mode", "thrpt")]:
                raw = self.fixture(); raw[0][field] = value; file.write_text(json.dumps(raw))
                with self.assertRaises(ValueError): producer.parse_jmh(file)
            raw = self.fixture(); raw[0]["primaryMetric"]["rawData"][0][0] = float("nan"); file.write_text(json.dumps(raw))
            with self.assertRaises(ValueError): producer.parse_jmh(file)
    def test_does_not_pass_tokens_or_actions_control_files_to_pr_code(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN":"test-only", "GITHUB_ENV":"test-only", "ACTIONS_RUNTIME_TOKEN":"test-only", "DATABASE_PASSWORD":"test-only"}):
            env = producer.clean_environment()
            self.assertNotIn("GITHUB_TOKEN", env); self.assertNotIn("GITHUB_ENV", env)
            self.assertNotIn("ACTIONS_RUNTIME_TOKEN", env); self.assertNotIn("DATABASE_PASSWORD", env)
    def test_kills_process_group_on_timeout_and_records_it(self):
        import sys
        with tempfile.TemporaryDirectory() as folder:
            result = producer.execute([sys.executable, "-c", "import time; time.sleep(30)"], folder, pathlib.Path(folder)/"log", .1)
            self.assertEqual(result["status"], "TIMEOUT"); self.assertNotEqual(result["exitCode"], 0)
    def test_publishes_failed_runs_and_build_failure_ledger_without_fake_samples(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder)
            for arm in ("base", "candidate"):
                (root/arm/"src/main/java/dev/regresslab/demo").mkdir(parents=True)
            event={"repository":{"id":1,"owner":{"login":"test-only"},"name":"test-only"},"pull_request":{"number":1,"title":"TEST ONLY: failure fixture","base":{"sha":"a"*40,"ref":"main"},"head":{"sha":"b"*40,"ref":"test-only"}}}
            def checkout(command, cwd, **kwargs): return ("a" if str(cwd).endswith("base") else "b")*40
            failed={"status":"TIMEOUT","exitCode":-9,"startedAt":producer.stamp(),"finishedAt":producer.stamp(),"elapsedMs":100,"error":None}
            with patch.object(producer.subprocess,"check_output",side_effect=checkout), patch.object(producer,"environment",return_value={}), patch.object(producer,"execute",return_value=failed):
                code=producer.produce(root/"base",root/"candidate",root/"out",event,2,1)
            self.assertEqual(code,1)
            run=json.loads((root/"out/baseline/run.json").read_text()); ledger=json.loads((root/"out/baseline/execution.json").read_text())
            self.assertEqual(run["run"]["status"],"FAILED"); self.assertEqual(run["benchmarks"],[])
            self.assertEqual(ledger["builds"]["BASELINE"]["status"],"TIMEOUT")
            self.assertTrue(all(b["status"]=="NOT_RUN" and b["observations"]==[] for b in ledger["blocks"]))
if __name__ == "__main__": unittest.main()
