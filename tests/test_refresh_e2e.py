"""Real HTTP -> pipeline processes -> SQLite, including retry and rollback."""

import os
import shutil
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from archive.repository import Archive
from bot import scheduler, update

ROOT = Path(__file__).resolve().parents[1]


class RefreshEndToEndTests(unittest.TestCase):
    def test_real_pipeline_retries_and_existing_reader_sees_new_data(self):
        state = {"version": "first", "offline": False, "requests": []}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                state["requests"].append(self.path)
                if state["offline"]:
                    self.send_error(503)
                    return
                post = (
                    f"http://127.0.0.1:{self.server.server_port}/2026/11/fajr-24th.html"
                )
                if self.path == "/sitemap.xml":
                    content = f"<urlset><url><loc>{post}</loc></url></urlset>"
                elif self.path.startswith("/feeds/posts/default?"):
                    content = (
                        f"<rss><channel><item><link>{post}</link>"
                        f"<title>Fajr</title><description><![CDATA["
                        f'<a href="https://media.example/{state["version"]}.mp3">audio</a>'
                        "]]></description></item></channel></rss>"
                    )
                else:
                    self.send_error(404)
                    return
                data = content.encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
                root = Path(directory)
                shutil.copytree(
                    ROOT / "pipeline",
                    root / "pipeline",
                    ignore=shutil.ignore_patterns("__pycache__"),
                )
                shutil.copytree(
                    ROOT / "archive",
                    root / "archive",
                    ignore=shutil.ignore_patterns("__pycache__"),
                )
                shutil.copy(ROOT / "config.py", root / "config.py")
                env = {
                    "PATH": os.environ["PATH"],
                    "SITE": f"http://127.0.0.1:{server.server_port}",
                    "DB_PATH": str(root / "data/archive.db"),
                    "WORK": str(root / "work"),
                    "OUT": str(root / "dump"),
                    "DB_UPDATE_TIMEOUT_SEC": "30",
                    "DB_UPDATE_TIME": "03:00",
                }
                with (
                    patch.object(update, "ROOT", root),
                    patch.dict(os.environ, env, clear=True),
                ):
                    update.run_update()
                    reader = Archive(root / "data/archive.db")
                    try:
                        self.assertTrue(
                            reader.latest(1)[0]["url"].endswith("first.mp3")
                        )
                        original_built = update.db_built_at()
                        state["offline"] = True
                        now = [datetime(2026, 1, 2, 4, tzinfo=timezone.utc)]
                        worker = scheduler.DailyWorker(
                            "03:00",
                            update.run_update,
                            startup_callback=update.run_update,
                            clock=lambda: now[0],
                            retry_seconds=1,
                            logger=lambda _: None,
                        )
                        worker.step()
                        self.assertEqual(worker.failures, 1)
                        self.assertEqual(update.read_status()["state"], "failed")
                        self.assertTrue(
                            reader.latest(1)[0]["url"].endswith("first.mp3")
                        )
                        self.assertEqual(update.db_built_at(), original_built)
                        state.update(offline=False, version="second")
                        now[0] += timedelta(seconds=1)
                        worker.step()
                        self.assertEqual(worker.failures, 0)
                        self.assertTrue(
                            reader.latest(1)[0]["url"].endswith("second.mp3")
                        )
                        self.assertEqual(tuple(reader.stats()[0])[:2], (1, 1))
                        status = update.read_status()
                        self.assertEqual(status["state"], "succeeded")
                        self.assertEqual(status["stage"], "complete")
                        self.assertIn("last_success", status)
                    finally:
                        reader.db.close()
                self.assertGreaterEqual(len(state["requests"]), 5)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_timeout_stops_pipeline_group_and_releases_lock(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
            root = Path(directory)
            (root / "pipeline").mkdir()
            (root / "pipeline/run.sh").write_text("sleep 20\n")
            with (
                patch.object(update, "ROOT", root),
                patch.dict(
                    os.environ,
                    {"PATH": os.environ["PATH"], "DB_UPDATE_TIMEOUT_SEC": "0.1"},
                    clear=True,
                ),
            ):
                with self.assertRaisesRegex(update.UpdateError, "timed out"):
                    update.run_update()
                self.assertEqual(update.read_status()["state"], "failed")
                with update.update_lock():
                    pass

    def test_timeout_kills_grandchild_that_ignores_term(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
            root = Path(directory)
            (root / "pipeline").mkdir()
            # The grandchild holds the inherited lock and ignores TERM. Killing
            # only its shell parent would leave every subsequent update locked.
            (root / "pipeline/stuck.py").write_text(
                "import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(20)\n"
            )
            (root / "pipeline/run.sh").write_text('"$PYTHON" stuck.py &\nwait\n')
            with (
                patch.object(update, "ROOT", root),
                patch.dict(
                    os.environ,
                    {"PATH": os.environ["PATH"], "DB_UPDATE_TIMEOUT_SEC": "0.3"},
                    clear=True,
                ),
            ):
                with self.assertRaises(update.UpdateError):
                    update.run_update()
                with update.update_lock():
                    pass

    def test_post_import_validation_rejects_missing_database(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
            root = Path(directory)
            (root / "pipeline").mkdir()
            (root / "archive").mkdir()
            (root / "pipeline/run.sh").write_text("exit 0\n")
            # A zero exit code is not sufficient proof of a committed catalog.
            (root / "archive/import_dump.py").write_text("pass\n")
            with (
                patch.object(update, "ROOT", root),
                patch.dict(os.environ, {"PATH": os.environ["PATH"]}, clear=True),
            ):
                with self.assertRaisesRegex(update.UpdateError, "verification"):
                    update.run_update()
                self.assertEqual(update.read_status()["state"], "failed")


if __name__ == "__main__":
    unittest.main()
