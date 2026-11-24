"""Exercise the foreground entrypoint in isolated roots without network calls."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class StartupTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".cache").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / ".cache")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ("config.py", "start.sh"):
            shutil.copy(ROOT / name, self.root / name)
        self.log = self.root / "calls.jsonl"
        self.wrapper = self.root / "fake-python"
        self.wrapper.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "kind = None\n"
            "if args[:2] == ['-m', 'bot.update']: kind = 'bootstrap'\n"
            "elif args[:1] == ['bot.py']: kind = 'bot'\n"
            "if kind:\n"
            "    record = {'kind': kind, 'pid': os.getpid(), 'cwd': os.getcwd(),\n"
            "              'env': {k: os.environ.get(k) for k in\n"
            "              ('PYTHON', 'PYTHONUNBUFFERED', 'BOT_TOKEN', 'DB_PATH', 'DB',\n"
            "               'DUMP', 'OUT', 'WORK', 'DOWNLOAD_DIR', 'LITERAL')}}\n"
            "    with open(os.environ['CALL_LOG'], 'a') as out:\n"
            "        out.write(json.dumps(record) + '\\n')\n"
            "    sys.exit(int(os.environ.get('FAKE_' + kind.upper() + '_EXIT', '0')))\n"
            "if args[:2] == ['-m', 'pip']:\n"
            "    with open(os.environ['CALL_LOG'], 'a') as out:\n"
            "        out.write(json.dumps({'kind': 'pip', 'args': args,\n"
            "                                   'python': os.environ.get('PYTHON')}) + '\\n')\n"
            "    if os.environ.get('FAKE_INSTALL_EXIT', '0') != '0':\n"
            "        sys.exit(int(os.environ['FAKE_INSTALL_EXIT']))\n"
            "    if not os.environ.get('FAKE_STILL_MISSING'):\n"
            "        pathlib.Path(os.environ['CALL_LOG'] + '.installed').touch()\n"
            "    sys.exit(0)\n"
            "if '-c' in args and 'importlib.metadata' in args[args.index('-c') + 1]:\n"
            "    with open(os.environ['CALL_LOG'], 'a') as out:\n"
            "        out.write(json.dumps({'kind': 'probe', 'args': args}) + '\\n')\n"
            "    if not pathlib.Path(os.environ['CALL_LOG'] + '.installed').exists():\n"
            "        print(os.environ.get('FAKE_MISSING', ''))\n"
            "    sys.exit(int(os.environ.get('FAKE_PROBE_EXIT', '0')))\n"
            "os.execv(os.environ['REAL_PY'], [os.environ['REAL_PY'], *args])\n",
            encoding="utf-8")
        self.wrapper.chmod(0o755)

    def run_start(self, overrides=None, args=()):
        env = {"PATH": os.environ["PATH"], "PYTHON": str(self.wrapper),
               "REAL_PY": sys.executable, "CALL_LOG": str(self.log), "INSTALL_DEPS": "0"}
        env.update(overrides or {})
        process = subprocess.Popen(["bash", str(self.root / "start.sh"), *args],
                                   cwd=self.root.parent, env=env, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            self.fail("foreground startup timed out")
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return process.returncode, stdout, stderr, calls, process.pid

    def test_malformed_dotenv_aborts_before_bootstrap_or_bot_without_secret_leak(self):
        (self.root / ".env").write_text('BOT_TOKEN="secret-unclosed\n', encoding="utf-8")
        status, stdout, stderr, calls, _ = self.run_start()
        self.assertNotEqual(status, 0)
        self.assertEqual(calls, [])
        self.assertIn(".env line 1", stderr)
        self.assertNotIn("secret-unclosed", stdout + stderr)

    def test_dependencies_are_checked_before_a_missing_database_is_bootstrapped(self):
        status, _, stderr, calls, _ = self.run_start({"INSTALL_DEPS": "1"})
        self.assertEqual(status, 0, stderr)
        self.assertEqual([call["kind"] for call in calls], ["probe", "bootstrap", "bot"])

    def test_dependency_probe_failure_aborts_before_network_or_bot(self):
        status, _, _, calls, _ = self.run_start({"INSTALL_DEPS": "1", "FAKE_PROBE_EXIT": "17"})
        self.assertEqual(status, 17)
        self.assertEqual([call["kind"] for call in calls], ["probe"])

    def test_child_uses_unbuffered_selected_python_canonical_paths_and_safe_dotenv(self):
        marker = self.root / "not-executed"
        (self.root / ".env").write_text(
            "BOT_TOKEN=file-token\nDB_PATH=data/current.db\nDB=legacy.db\n"
            "DUMP=new-dump\nOUT=old-dump\nWORK=scratch\nDOWNLOAD_DIR=downloads-alt\n"
            f"LITERAL=$(touch {marker})\n", encoding="utf-8")
        status, _, stderr, calls, pid = self.run_start({"BOT_TOKEN": "process-token"})
        self.assertEqual(status, 0, stderr)
        self.assertEqual([call["kind"] for call in calls], ["bootstrap", "bot"])
        self.assertEqual(calls[-1]["pid"], pid, "shell and launcher must exec, not detach")
        for call in calls:
            env = call["env"]
            self.assertEqual(call["cwd"], str(self.root))
            self.assertEqual(env["PYTHON"], str(self.wrapper))
            self.assertEqual(env["PYTHONUNBUFFERED"], "1")
            self.assertEqual(env["BOT_TOKEN"], "process-token")
            self.assertEqual(env["DB_PATH"], str(self.root / "data/current.db"))
            self.assertEqual(env["DB"], env["DB_PATH"])
            self.assertEqual(env["DUMP"], str(self.root / "new-dump"))
            self.assertEqual(env["OUT"], env["DUMP"])
            self.assertEqual(env["WORK"], str(self.root / "scratch"))
            self.assertEqual(env["DOWNLOAD_DIR"], str(self.root / "downloads-alt"))
            self.assertEqual(env["LITERAL"], f"$(touch {marker})")
        self.assertFalse(marker.exists())

    def test_invalid_runtime_values_abort_before_dependency_install_or_bootstrap(self):
        for key, value in (("POLL_SEC", "nan"), ("DB_UPDATE_TIME", "25:00"),
                           ("TELEGRAM_API", "private-api")):
            with self.subTest(key=key):
                status, stdout, stderr, calls, _ = self.run_start({key: value, "INSTALL_DEPS": "1"})
                self.assertNotEqual(status, 0)
                self.assertEqual(calls, [])
                self.assertIn(key, stderr)
                self.assertNotIn(value, stdout + stderr)

    def test_entrypoint_rejects_process_management_flags(self):
        for argument in ("start", "stop", "run", "--help"):
            with self.subTest(argument=argument):
                status, _, stderr, calls, _ = self.run_start(args=(argument,))
                self.assertEqual(status, 2)
                self.assertEqual(calls, [])
                self.assertIn("no arguments", stderr)

    def test_browsing_does_not_require_mtproto_dependencies(self):
        status, _, stderr, calls, _ = self.run_start({"INSTALL_DEPS": "1", "BOOTSTRAP": "0"})
        self.assertEqual(status, 0, stderr)
        self.assertEqual(calls[0]["args"][-1], "requests")
        self.assertNotIn("tgcrypto", calls[0]["args"])
        self.assertNotIn("kurigram:pyrogram:pyrofork", calls[0]["args"])

    def test_configured_mtproto_adds_optional_client_dependencies(self):
        status, _, stderr, calls, _ = self.run_start({"INSTALL_DEPS": "1", "BOOTSTRAP": "0",
            "TELEGRAM_API": "123", "TELEGRAM_HASH": "ab" * 16})
        self.assertEqual(status, 0, stderr)
        self.assertIn("kurigram:pyrogram:pyrofork", calls[0]["args"])
        self.assertIn("tgcrypto", calls[0]["args"])

    def test_installed_dependencies_are_rechecked_before_bootstrap(self):
        status, _, stderr, calls, _ = self.run_start({"INSTALL_DEPS": "1", "FAKE_MISSING": "requests"})
        self.assertEqual(status, 0, stderr)
        self.assertEqual([call["kind"] for call in calls], ["probe", "pip", "probe", "bootstrap", "bot"])
        self.assertIn("requests", calls[1]["args"])
        self.assertEqual(calls[1]["python"], str(self.wrapper))

    def test_failed_install_or_still_missing_deps_aborts_before_bootstrap(self):
        for extra in ({"FAKE_INSTALL_EXIT": "19"}, {"FAKE_STILL_MISSING": "1"}):
            with self.subTest(overrides=extra):
                self.log.unlink(missing_ok=True)
                status, _, stderr, calls, _ = self.run_start({"INSTALL_DEPS": "1", "FAKE_MISSING": "requests", **extra})
                self.assertNotEqual(status, 0)
                self.assertNotIn("bootstrap", [call["kind"] for call in calls])
                self.assertNotIn("bot", [call["kind"] for call in calls])
                self.assertTrue(stderr)

    def test_dotenv_can_disable_bootstrap_and_dependency_installation(self):
        (self.root / ".env").write_text("BOOTSTRAP=0\nINSTALL_DEPS=0\nDB_UPDATE_TIME=\n", encoding="utf-8")
        status, stdout, stderr, calls, _ = self.run_start()
        self.assertEqual(status, 0, stderr)
        self.assertEqual([call["kind"] for call in calls], ["bot"])
        self.assertIn("BOOTSTRAP=0", stdout)

    def test_existing_database_in_dotenv_skips_bootstrap(self):
        (self.root / "custom db").touch()
        (self.root / ".env").write_text('DB_PATH="custom db"\n', encoding="utf-8")
        status, stdout, stderr, calls, _ = self.run_start()
        self.assertEqual(status, 0, stderr)
        self.assertEqual([call["kind"] for call in calls], ["bot"])
        self.assertIn("custom db already exists", stdout)

    def test_bootstrap_failure_never_starts_bot(self):
        status, _, stderr, calls, _ = self.run_start({"FAKE_BOOTSTRAP_EXIT": "23"})
        self.assertEqual(status, 23)
        self.assertEqual([call["kind"] for call in calls], ["bootstrap"])
        self.assertIn("exit 23", stderr)

    def test_foreground_bot_exit_code_is_propagated(self):
        status, _, _, calls, pid = self.run_start({"BOOTSTRAP": "0", "FAKE_BOT_EXIT": "29"})
        self.assertEqual(status, 29)
        self.assertEqual(calls[-1]["pid"], pid)

    def test_parser_subprocess_failure_is_not_swallowed(self):
        (self.root / "config.py").write_text('raise RuntimeError("broken parser")\n', encoding="utf-8")
        status, _, stderr, calls, _ = self.run_start()
        self.assertNotEqual(status, 0)
        self.assertEqual(calls, [])
        self.assertIn("broken parser", stderr)

    def test_shell_and_embedded_python_syntax(self):
        result = subprocess.run(["bash", "-n", str(self.root / "start.sh")],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        script = (self.root / "start.sh").read_text()
        compile(script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0], "start.sh python", "exec")


if __name__ == "__main__":
    unittest.main()
