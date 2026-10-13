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
    def test_records_an_actual_subprocess_timeout(self):
        import sys
        with tempfile.TemporaryDirectory() as folder:
            result = producer.execute([sys.executable, "-c", "import time; time.sleep(30)"], folder, pathlib.Path(folder)/"log", .1)
            self.assertEqual(result["status"], "TIMEOUT"); self.assertNotEqual(result["exitCode"], 0)
    def test_bounds_jmh_file_bytes_before_parsing(self):
        with tempfile.TemporaryDirectory() as folder:
            file=pathlib.Path(folder)/"jmh.json"
            file.write_text(json.dumps(self.fixture())+" "*(1024*1024))
            with self.assertRaises(ValueError): producer.parse_jmh(file)
    def test_rejects_duplicate_jmh_metadata_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            file=pathlib.Path(folder)/"jmh.json"
            file.write_text(json.dumps(self.fixture()).replace('"threads": 1','"threads": 2, "threads": 1'))
            with self.assertRaises(ValueError): producer.parse_jmh(file)
    def test_rejects_symlinks_and_named_pipes_without_blocking(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder);file=root/"jmh.json";file.write_text(json.dumps(self.fixture()))
            (root/"link").symlink_to(file)
            with self.assertRaises(OSError): producer.parse_jmh(root/"link")
            os.mkfifo(root/"pipe")
            with self.assertRaises(ValueError): producer.parse_jmh(root/"pipe")
    def test_records_a_process_launch_error_without_inventing_an_exit_code(self):
        with tempfile.TemporaryDirectory() as folder:
            result=producer.execute([str(pathlib.Path(folder)/"missing-executable")],folder,pathlib.Path(folder)/"log",1)
            self.assertEqual(result["status"],"BENCHMARK_FAILED")
            self.assertIsNone(result["exitCode"])
            self.assertIn("FileNotFoundError",result["error"])
    def test_exit_between_timeout_and_group_kill_does_not_abort_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(producer.subprocess,"Popen") as launch,patch.object(producer.os,"killpg",side_effect=ProcessLookupError):
                launch.return_value.pid=123
                launch.return_value.wait.side_effect=[producer.subprocess.TimeoutExpired("test-only",.1),0]
                result=producer.execute(["test-only"],folder,pathlib.Path(folder)/"log",.1)
            self.assertEqual(result["status"],"TIMEOUT");self.assertEqual(result["exitCode"],0)
            self.assertIn("timeout",result["error"])
    def test_correctness_timeout_retains_timeout_status_in_uploaded_ledger(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder)
            for arm in ("base","candidate"): (root/arm/"src/main/java/dev/regresslab/demo").mkdir(parents=True)
            event={"repository":{"id":1,"owner":{"login":"test-only"},"name":"test-only"},"pull_request":{"number":1,"title":"TEST ONLY: timeout fixture","base":{"sha":"a"*40,"ref":"main"},"head":{"sha":"b"*40,"ref":"test-only"}}}
            def checkout(command,cwd,**kwargs): return ("a" if str(cwd).endswith("base") else "b")*40
            complete={"status":"COMPLETE","exitCode":0,"startedAt":producer.stamp(),"finishedAt":producer.stamp(),"elapsedMs":100,"error":None}
            timed_out={**complete,"status":"TIMEOUT","exitCode":-9}
            with patch.object(producer.subprocess,"check_output",side_effect=checkout),patch.object(producer,"environment",return_value={}),patch.object(producer,"execute",side_effect=[complete,timed_out,complete,timed_out]):
                self.assertEqual(producer.produce(root/"base",root/"candidate",root/"out",event,2,1),1)
            ledger=json.loads((root/"out/baseline/execution.json").read_text())
            self.assertEqual(ledger["builds"]["BASELINE"]["status"],"TIMEOUT")
            self.assertIn("Correctness",ledger["builds"]["BASELINE"]["error"])
    def test_failed_execution_cannot_reuse_jmh_values_from_an_earlier_invocation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder)
            for arm in ("base","candidate"): (root/arm/"src/main/java/dev/regresslab/demo").mkdir(parents=True)
            logs=root/"out/logs";logs.mkdir(parents=True)
            for arm in ("baseline","candidate"): (logs/f"{arm}-0-jmh.json").write_text(json.dumps(self.fixture()))
            event={"repository":{"id":1,"owner":{"login":"test-only"},"name":"test-only"},"pull_request":{"number":1,"title":"TEST ONLY: stale output fixture","base":{"sha":"a"*40,"ref":"main"},"head":{"sha":"b"*40,"ref":"test-only"}}}
            def checkout(command,cwd,**kwargs): return ("a" if str(cwd).endswith("base") else "b")*40
            complete={"status":"COMPLETE","exitCode":0,"startedAt":producer.stamp(),"finishedAt":producer.stamp(),"elapsedMs":1,"error":None}
            failure={**complete,"status":"BENCHMARK_FAILED","exitCode":1}
            with patch.object(producer.subprocess,"check_output",side_effect=checkout),patch.object(producer,"environment",return_value={}),patch.object(producer,"execute",side_effect=[complete]*4+[failure]*2):
                self.assertEqual(producer.produce(root/"base",root/"candidate",root/"out",event,2,1),1)
            ledger=json.loads((root/"out/baseline/execution.json").read_text())
            self.assertEqual(ledger["blocks"][0]["observations"],[])
            self.assertEqual(ledger["blocks"][1]["observations"],[])
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
