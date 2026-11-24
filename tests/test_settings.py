"""Runtime settings contract, without importing the bot or optional clients."""
import dataclasses
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import config


class SettingsTests(unittest.TestCase):
    def test_settings_have_validated_frozen_defaults(self):
        factory = getattr(config, "Settings", None)
        self.assertIsNotNone(factory, "config must expose Settings")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = factory.from_env({}, root=root)
            self.assertEqual(settings.bot_token, "")
            self.assertEqual(settings.db_path, root / "archive.db")
            self.assertEqual(settings.dump_path, root / "haramain")
            self.assertEqual(settings.work_path, root / "work")
            self.assertEqual(settings.download_dir, root / "downloads")
            self.assertEqual(settings.update_time, "03:00")
            self.assertEqual(settings.update_max_age_hours, 24)
            self.assertEqual(settings.update_retry_seconds, 300)
            self.assertEqual(settings.update_timeout_seconds, 7200)
            self.assertEqual(settings.poll_sec, 3)
            self.assertEqual(settings.longpoll_sec, 25)
            self.assertEqual(settings.poll_backoff_max_sec, 300)
            self.assertEqual(settings.queue_size, 10)
            with self.assertRaises(dataclasses.FrozenInstanceError):
                settings.poll_sec = 1

    def test_paths_use_root_and_prefer_current_names_to_legacy_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {"DB_PATH": "data/../data/current.db", "DB": "legacy.db",
                   "DUMP": "dump", "OUT": "old-dump", "WORK": "scratch",
                   "DOWNLOAD_DIR": str(root / "absolute-downloads")}
            settings = config.Settings.from_env(env, root=root)
            self.assertEqual(settings.db_path, root / "data/current.db")
            self.assertEqual(settings.dump_path, root / "dump")
            self.assertEqual(settings.work_path, root / "scratch")
            self.assertEqual(settings.download_dir, root / "absolute-downloads")
            legacy = config.Settings.from_env({"DB": "old.db", "OUT": "old"}, root=root)
            self.assertEqual(legacy.db_path, root / "old.db")
            self.assertEqual(legacy.dump_path, root / "old")
            blank = config.Settings.from_env({"DB_PATH": "", "DB": "old.db", "DUMP": ""}, root=root)
            self.assertEqual(blank.db_path, root / "old.db")
            self.assertEqual(blank.dump_path, root / "haramain")
            self.assertEqual(env["DB_PATH"], "data/../data/current.db")

    def test_schedule_defaults_only_when_absent_and_validates_hhmm(self):
        self.assertEqual(config.Settings.from_env({}).update_time, "03:00")
        for value in ("", "  ", "00:00", "23:59", " 09:30 "):
            with self.subTest(valid=value):
                self.assertEqual(config.Settings.from_env({"DB_UPDATE_TIME": value}).update_time,
                                 value.strip())
        for value in ("3:00", "24:00", "03:60", "03:00:00", "secret-time", "０３:００"):
            with self.subTest(invalid=value):
                with self.assertRaisesRegex(ValueError, "DB_UPDATE_TIME") as raised:
                    config.Settings.from_env({"DB_UPDATE_TIME": value})
                self.assertNotIn(value, str(raised.exception))

    def test_numeric_overrides_are_parsed_and_reject_nonfinite_or_out_of_range_values(self):
        fields = {
            "DB_UPDATE_MAX_AGE_HOURS": ("update_max_age_hours", "0", 0.0),
            "DB_UPDATE_RETRY_SEC": ("update_retry_seconds", "1.5", 1.5),
            "DB_UPDATE_TIMEOUT_SEC": ("update_timeout_seconds", "60.5", 60.5),
            "POLL_SEC": ("poll_sec", "0.25", 0.25),
            "LONGPOLL_SEC": ("longpoll_sec", "30", 30),
            "POLL_BACKOFF_MAX_SEC": ("poll_backoff_max_sec", "400.5", 400.5),
            "LEECH_QUEUE_SIZE": ("queue_size", "2", 2),
        }
        for key, (attribute, value, expected) in fields.items():
            with self.subTest(key=key):
                settings = config.Settings.from_env({key: value})
                self.assertEqual(getattr(settings, attribute), expected)
            invalid = ("", "not-a-number", "nan", "inf", "-inf", "-1")
            if key != "DB_UPDATE_MAX_AGE_HOURS":
                invalid += ("0",)
            if key in ("LONGPOLL_SEC", "LEECH_QUEUE_SIZE"):
                invalid += ("1.5", "2.0", "True")
            for value in invalid:
                with self.subTest(key=key, invalid=value):
                    with self.assertRaisesRegex(ValueError, key) as raised:
                        config.Settings.from_env({key: value})
                    self.assertNotIn("not-a-number", str(raised.exception))

    def test_process_environment_is_used_only_when_mapping_is_omitted(self):
        with patch.dict(os.environ, {"BOT_TOKEN": "sensitive-token", "POLL_SEC": "7"}, clear=True):
            self.assertEqual(config.Settings.from_env().poll_sec, 7)
            self.assertEqual(config.Settings.from_env({}).poll_sec, 3)
            self.assertNotIn("sensitive-token", repr(config.Settings.from_env()))

    def test_mtproto_credentials_are_optional_paired_validated_and_secret_safe(self):
        self.assertIsNone(getattr(config.Settings.from_env({}), "telegram_api", "missing"))
        valid_hash = "a1" * 16
        settings = config.Settings.from_env({"BOT_TOKEN": "secret-token", "TELEGRAM_API": "123",
                                             "TELEGRAM_HASH": valid_hash,
                                             "USER_SESSION_STRING": "secret-session"})
        self.assertEqual(settings.telegram_api, 123)
        self.assertEqual(settings.telegram_hash, valid_hash)
        self.assertEqual(settings.user_session_string, "secret-session")
        for secret in ("secret-token", valid_hash, "secret-session"):
            self.assertNotIn(secret, repr(settings))
        for env in ({"TELEGRAM_API": "1"}, {"TELEGRAM_HASH": valid_hash},
                    {"TELEGRAM_API": "secret-api", "TELEGRAM_HASH": valid_hash},
                    {"TELEGRAM_API": "0", "TELEGRAM_HASH": valid_hash},
                    {"TELEGRAM_API": "1.5", "TELEGRAM_HASH": valid_hash},
                    {"TELEGRAM_API": "1", "TELEGRAM_HASH": "secret-hash"}):
            with self.subTest(keys=tuple(env)):
                with self.assertRaisesRegex(ValueError, "TELEGRAM_") as raised:
                    config.Settings.from_env(env)
                for secret in ("secret-api", "secret-hash", valid_hash):
                    self.assertNotIn(secret, str(raised.exception))
        blank = config.Settings.from_env({"TELEGRAM_API": "", "TELEGRAM_HASH": ""})
        self.assertIsNone(blank.telegram_api)


class EnvironmentTests(unittest.TestCase):
    def test_env_parser_reads_data_not_shell_and_preserves_process_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "must-not-exist"
            (root / ".env").write_text(
                '# comment\nexport BOT_TOKEN="file-token"\n'
                "SINGLE='a # b' # trailing comment\n"
                'DOUBLE="hello \\"there\\"" # trailing comment\n'
                "PLAIN=two  spaces # comment\n"
                "EMPTY=\nDUP=first\nDUP=second\n"
                f"LITERAL=$(touch {marker})\n", encoding="utf-8")
            with patch.dict(os.environ, {"BOT_TOKEN": "process-token"}, clear=True):
                self.assertTrue(config.load_env(root))
                self.assertEqual(os.environ["BOT_TOKEN"], "process-token")
                self.assertEqual(os.environ["SINGLE"], "a # b")
                self.assertEqual(os.environ["DOUBLE"], 'hello "there"')
                self.assertEqual(os.environ["PLAIN"], "two  spaces")
                self.assertEqual(os.environ["EMPTY"], "")
                self.assertEqual(os.environ["DUP"], "first")
                self.assertEqual(os.environ["LITERAL"], f"$(touch {marker})")
            self.assertFalse(marker.exists())

    def test_invalid_env_fails_without_partial_load_or_secret_in_error(self):
        for line in ("NO_EQUALS", "BAD KEY=private-value", 'TOKEN="private-value',
                     "TOKEN='private-value' trailing", "TOKEN=private-value\0bad"):
            with self.subTest(case=line.split("=", 1)[0]), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / ".env").write_text("FIRST=valid\n" + line + "\n", encoding="utf-8")
                with patch.dict(os.environ, {}, clear=True):
                    with self.assertRaisesRegex(ValueError, r"\.env line 2") as raised:
                        config.load_env(root)
                    self.assertNotIn("private-value", str(raised.exception))
                    self.assertNotIn("FIRST", os.environ)

    def test_load_settings_loads_dotenv_but_explicit_mapping_is_isolated(self):
        helper = getattr(config, "load_settings", None)
        self.assertIsNotNone(helper, "config must expose load_settings")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("BOT_TOKEN=file\nPOLL_SEC=8\n", encoding="utf-8")
            with patch.dict(os.environ, {"BOT_TOKEN": "process"}, clear=True):
                settings = helper(root=root)
                self.assertEqual(settings.bot_token, "process")
                self.assertEqual(settings.poll_sec, 8)
                self.assertEqual(config.Settings.from_env({}, root=root).poll_sec, 3)


if __name__ == "__main__":
    unittest.main()
