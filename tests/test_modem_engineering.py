import tempfile
import unittest
from unittest.mock import patch

from control.app import modem_engineering as eng
from control.app import ussd


class Result:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


class AtTerminalTests(unittest.TestCase):
    modem = "/org/freedesktop/ModemManager1/Modem/2"

    def test_pin_and_sms_body_commands_are_refused(self):
        for command in ("AT+CPIN=1234", "AT+CLCK=\"SC\",1,\"1234\"",
                        "AT+CMGS=+44123", "AT+KI=001122"):
            result = eng.send_at(self.modem, command, runner=lambda *a, **k: Result())
            self.assertFalse(result["ok"], command)
            self.assertEqual(result["stage"], "validate")
            self.assertEqual(result["command"], "")

    def test_history_redacts_quoted_payloads_but_keeps_service_codes(self):
        self.assertEqual(eng.redact_at('AT+CUSD=1,"*100#",15'), 'AT+CUSD=1,"*100#",15')
        self.assertIn("<redacted>", eng.redact_at('AT+FOO="secret-body"'))
        self.assertNotIn("DEADBEEFCAFEBABE0123456789ABCD",
                         eng.redact_at("AT+CSIM=32,DEADBEEFCAFEBABE0123456789ABCD"))

    def test_send_at_uses_mmcli_command_and_records_redacted_history(self):
        calls = []

        def runner(args, **_kwargs):
            calls.append(tuple(args))
            return Result("response: +CESQ: 99,99,255,255,20,70\nOK\n")

        with tempfile.TemporaryDirectory() as temp:
            with patch.object(eng.device_state, "ROOT", temp):
                sent = eng.send_at(self.modem, "AT+CESQ", runner=runner)
                self.assertTrue(sent["ok"])
                self.assertIn("+CESQ:", sent["response"])
                stored = eng.record_history("modem-a", sent["command"], sent["response"],
                                            ok=True)
                self.assertEqual(stored[-1]["command"], "AT+CESQ")
                self.assertIn("+CESQ:", stored[-1]["response"])
        self.assertEqual(calls[0][:3], ("mmcli", "-m", self.modem))
        self.assertIn("--command=AT+CESQ", calls[0])


class ModemUssdTests(unittest.TestCase):
    modem = "/org/freedesktop/ModemManager1/Modem/3"

    def test_parses_plus_cusd_and_3gpp_xml(self):
        xml = ("<ussd-data><ussd-string>Balance $5.00</ussd-string>"
               "<error-code>0</error-code></ussd-data>")
        parsed = eng.parse_cusd(f'+CUSD: 0,"{xml}",15')
        self.assertEqual(parsed["text"], "Balance $5.00")
        self.assertEqual(parsed["error_code"], "0")
        self.assertEqual(parsed["status"], 0)
        self.assertEqual(eng.parse_cusd('+CUSD: 0,"Account 12.50 GBP",15')["text"],
                         "Account 12.50 GBP")

    def test_mm_ussd_flag_missing_falls_back_to_at_cusd(self):
        calls = []

        def runner(args, **_kwargs):
            calls.append(tuple(args))
            if any(item.startswith("--3gpp-ussd-initiate=") for item in args):
                return Result(returncode=1, stderr="error: no actions specified")
            if any(item.startswith("--command=AT+CUSD=") for item in args):
                return Result('+CUSD: 0,"Balance 8.00",15')
            return Result(returncode=1)

        result = eng.send_ussd(self.modem, "*100#", runner=runner)
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "Balance 8.00")
        self.assertEqual(result["transport"], "cellular")
        self.assertTrue(any("--command=AT+CUSD=1,\"*100#\",15" in item
                            for call in calls for item in call))

    def test_invalid_ussd_is_refused_before_mmcli(self):
        called = []
        result = eng.send_ussd(self.modem, "+441234567890",
                               runner=lambda *a, **k: called.append(a))
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "validate")
        self.assertEqual(called, [])


class RadioMetricsTests(unittest.TestCase):
    def test_parses_lte_rsrp_rsrq_sinr_band_and_channel(self):
        detail = """modem.generic.access-technologies.value[1] : lte
modem.generic.current-bands.value[1] : eutran-3
modem.generic.signal-quality.value : 77
"""
        signal = """modem.signal.lte.rsrp : -95.00
modem.signal.lte.rsrq : -8.00
modem.signal.lte.snr : 20.00
modem.signal.lte.earfcn : 1850
"""
        got = eng.parse_radio_metrics(detail, signal)
        self.assertEqual(got["rsrp"], -95.0)
        self.assertEqual(got["rsrq"], -8.0)
        self.assertEqual(got["sinr"], 20.0)
        self.assertEqual(got["access_tech"], "lte")
        self.assertEqual(got["band"], "eutran-3")
        self.assertEqual(got["channel"], 1850)

    def test_live_radio_prefers_module_snapshot_over_desired_echo(self):
        self.assertTrue(eng.radio_from_snapshot(
            {"available": True, "radio_enabled": True},
            {"cellular_radio_enabled": False}))
        self.assertFalse(eng.radio_from_snapshot(
            {"available": True, "radio_enabled": False},
            {"cellular_radio_enabled": True}))
        self.assertIsNone(eng.radio_from_snapshot({}, {}))


class OperatorSelectTests(unittest.TestCase):
    modem = "/org/freedesktop/ModemManager1/Modem/4"

    def test_parses_mmcli_scan_listing(self):
        text = """Found 2 networks:
	23415 - Vodafone UK (lte, available)
	23410 - O2 (lte, current)
"""
        rows = eng.parse_operator_scan(text)
        self.assertEqual([row["plmn"] for row in rows], ["23415", "23410"])
        self.assertEqual(rows[0]["name"], "Vodafone UK")
        self.assertEqual(rows[1]["availability"], "current")

    def test_parses_at_cops_list(self):
        rows = eng.parse_operator_scan(
            '+COPS: (2,"EE","EE","23430",7),(1,"Vodafone","Voda","23415",7)')
        self.assertEqual(rows[0]["plmn"], "23430")
        self.assertEqual(rows[0]["availability"], "current")
        self.assertEqual(rows[1]["plmn"], "23415")

    def test_manual_select_reads_back_current_operator(self):
        calls = []

        def runner(args, **_kwargs):
            calls.append(tuple(args))
            if any(item.startswith("--3gpp-register-in-operator=") for item in args):
                return Result()
            if args == ["mmcli", "-m", self.modem, "--output-keyvalue"]:
                return Result("modem.3gpp.operator-name : EE\n"
                              "modem.3gpp.operator-code : 23430\n")
            if any(item.startswith("--command=AT+COPS?") for item in args):
                return Result("response: +COPS: 1,2,\"23430\",7")
            return Result(returncode=1)

        result = eng.select_operator(self.modem, mode="manual", plmn="23430",
                                     runner=runner)
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "manual")
        self.assertEqual(result["plmn"], "23430")
        self.assertEqual(result["selection"], "manual")
        self.assertEqual(result["name"], "EE")

    def test_missing_scan_flag_falls_back_to_at_cops(self):
        def runner(args, **_kwargs):
            if "--3gpp-scan" in args:
                return Result(returncode=1, stderr="error: no actions specified")
            if any(item.startswith("--command=AT+COPS=?") for item in args):
                return Result('+COPS: (1,"EE","EE","23430",7)')
            return Result(returncode=1)

        result = eng.scan_operators(self.modem, runner=runner)
        self.assertTrue(result["ok"])
        self.assertEqual(result["operators"][0]["plmn"], "23430")


class UsbNetAndRestartTests(unittest.TestCase):
    modem = "/org/freedesktop/ModemManager1/Modem/5"

    def test_usbnet_write_is_not_ok_until_readback_matches(self):
        def runner(args, **_kwargs):
            command = next((item for item in args if item.startswith("--command=")), "")
            if command == '--command=AT+QCFG="usbnet",1':
                return Result("OK")
            if command == '--command=AT+QCFG="usbnet"':
                return Result('+QCFG: "usbnet",0')
            return Result(returncode=1)

        result = eng.set_usbnet(self.modem, "ecm", runner=runner)
        self.assertFalse(result["ok"])
        self.assertEqual(result["requested"]["name"], "ecm")
        self.assertEqual(result["actual"]["name"], "qmi")

    def test_usbnet_write_ok_when_module_reports_the_new_mode(self):
        def runner(args, **_kwargs):
            command = next((item for item in args if item.startswith("--command=")), "")
            if command.startswith("--command=AT+QCFG="):
                return Result('+QCFG: "usbnet",2')
            return Result(returncode=1)

        result = eng.set_usbnet(self.modem, "mbim", runner=runner)
        self.assertTrue(result["ok"])
        self.assertEqual(result["actual"]["code"], 2)

    def test_restart_waits_for_live_radio_state(self):
        ticks = {"n": 0}

        def runner(args, **_kwargs):
            if "--reset" in args:
                return Result()
            if args[-1:] == ["--output-keyvalue"] or "--output-keyvalue" in args:
                ticks["n"] += 1
                if ticks["n"] < 2:
                    return Result(returncode=1, stderr="couldn't find modem")
                return Result("modem.generic.state : registered\n"
                              "modem.generic.power-state : on\n")
            if any(item.startswith("--command=AT+QCFG=") for item in args):
                return Result('+QCFG: "usbnet",1')
            return Result(returncode=1)

        result = eng.restart_modem(self.modem, runner=runner, sleeper=lambda _s: None)
        self.assertTrue(result["ok"])
        self.assertTrue(result["radio_enabled"])
        self.assertEqual(result["state"], "registered")
        self.assertEqual(result["usbnet"]["name"], "ecm")

    def test_cfun_four_is_radio_off(self):
        self.assertFalse(eng.parse_cfun("+CFUN: 4"))
        self.assertTrue(eng.parse_cfun("+CFUN: 1"))
        self.assertIsNone(eng.parse_cfun("OK"))


class SharedUssdParserTests(unittest.TestCase):
    def test_modem_xml_reuses_the_ims_parser(self):
        payload = "<ussd-data><ussd-string>Thank you</ussd-string></ussd-data>"
        self.assertEqual(ussd.parse(payload)["text"], "Thank you")
        self.assertEqual(eng.parse_cusd(payload)["text"], "Thank you")


if __name__ == "__main__":
    unittest.main()
