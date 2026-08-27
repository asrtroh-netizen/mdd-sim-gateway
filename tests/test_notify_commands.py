import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from control.app import config, notify_commands, notify_push


class NotifyCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = patch.multiple(
            config,
            DATA_DIR=self.temp.name,
            CONFIG_PATH=str(Path(self.temp.name) / "config.yaml"),
        )
        self.paths.start()
        config.save_local({"allow_telegram_commands": True, "max_sim_lines": 8})
        config.upsert_instance({
            "id": "1", "name": "Home", "msisdn": "+447700900001",
        })

    def tearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    def test_parse_command_splits_sms_body(self):
        verb, args = notify_commands.parse_command("/sms Home +447700900099 hello there")
        self.assertEqual(verb, "sms")
        self.assertEqual(args, ["Home", "+447700900099", "hello there"])
        self.assertEqual(notify_commands.parse_command("/status@MyBot")[0], "status")

    def test_resolve_line_matches_id_name_and_number(self):
        self.assertEqual(notify_commands.resolve_line("1")[0]["id"], "1")
        self.assertEqual(notify_commands.resolve_line("home")[0]["id"], "1")
        self.assertEqual(notify_commands.resolve_line("+447700900001")[0]["id"], "1")
        self.assertEqual(notify_commands.resolve_line("missing"), [])

    def test_commands_stay_off_without_the_flag(self):
        config.save_local({"allow_telegram_commands": False})
        settings = config.get_settings()
        settings["telegram"] = {"bot_token": "t", "chat_id": "99",
                                "commands": {"enabled": True}}
        self.assertFalse(notify_commands.commands_enabled(settings))
        reply, action = notify_commands.handle_update({
            "message": {"chat": {"id": 99}, "date": int(time.time()), "text": "/lines"},
        }, settings)
        self.assertIsNone(reply)
        self.assertIsNone(action)

    def test_foreign_chat_is_ignored_and_local_lines_can_sms(self):
        settings = config.get_settings()
        settings["telegram"] = {"bot_token": "t", "chat_id": "42",
                                "commands": {"enabled": True}}
        reply, action = notify_commands.handle_update({
            "message": {"chat": {"id": 99}, "date": int(time.time()), "text": "/lines"},
        }, settings)
        self.assertIsNone(action)
        reply, action = notify_commands.handle_update({
            "message": {"chat": {"id": 42}, "date": int(time.time()),
                        "text": "/sms Home +447700900099 hi"},
        }, settings)
        self.assertEqual(action["op"], "sms")
        self.assertEqual(action["iid"], "1")
        self.assertEqual(action["to"], "+447700900099")
        reply, action = notify_commands.dispatch_text("/sms 9 +447700900099 hi")
        self.assertIsNone(action)
        self.assertIn("没有", reply)

    def test_reply_to_notification_texts_that_peer(self):
        notify_push.remember_reply_target(17, {
            "event": notify_push.EV_INCOMING_SMS, "instance": "1", "from": "+447700900088",
        })
        reply, action = notify_commands.dispatch_text(
            "on my way", reply_target=notify_push.reply_target(17))
        self.assertEqual(action, {"op": "sms", "iid": "1", "to": "+447700900088",
                                  "text": "on my way"})
        self.assertTrue(reply)


if __name__ == "__main__":
    unittest.main()
