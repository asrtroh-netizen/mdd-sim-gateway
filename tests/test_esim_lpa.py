"""eSIM / LPA completeness without live hardware.

lpac and PC/SC are mocked. These pin the P2 contract: exclusive reader lock,
rebind to the new ICCID after enable/switch, and leftover-card recovery.
"""
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from control.app import esim_lifecycle, lpa, main
from control.app.identity import normalize_iccid


async def _absorb_lpa(name, idx, coro, **_kwargs):
    if hasattr(coro, "close"):
        coro.close()
    return {"ses": [], "dual": False}


class ChipAndCacheTests(unittest.TestCase):
    def test_chip_info_surfaces_certs_lpac_already_returns(self):
        certs = esim_lifecycle.chip_certificates({
            "EUICCInfo2": {
                "sasAccreditationNumber": "GSMA-SAS-UP",
                "euiccCiPKIdListForVerification": ["AA11", "BB22"],
                "euiccCiPKIdListForSigning": ["AA11"],
            }
        })
        self.assertEqual(certs["sas"], "GSMA-SAS-UP")
        self.assertEqual(certs["ci_verify"], ["AA11", "BB22"])

    def test_cache_matches_iccid_case_insensitively(self):
        cache = {
            "eid-1": {"ses": [{"profiles": [
                {"iccid": "89000000000000ABCD", "profileState": "enabled"},
                {"iccid": "89000000000000eeee", "profileState": "disabled"},
            ]}]},
        }
        hit = esim_lifecycle.cache_entry_for_iccid(cache, "89000000000000abcd")
        self.assertIs(hit, cache["eid-1"])
        self.assertTrue(esim_lifecycle.update_cached_profile(
            cache, "89000000000000eeee", state="enabled"))
        profiles = cache["eid-1"]["ses"][0]["profiles"]
        self.assertEqual(profiles[0]["profileState"], "disabled")
        self.assertEqual(profiles[1]["profileState"], "enabled")

    def test_chip_certificates_accept_lpac_typo_and_lists(self):
        certs = esim_lifecycle.chip_certificates({
            "EUICCInfo2": {
                "sasAcreditationNumber": "GSMA-SAS-UP",
                "euiccCiPKIdListForVerification": "not-a-list",
            }
        })
        self.assertEqual(certs["sas"], "GSMA-SAS-UP")
        self.assertEqual(certs["ci_verify"], [])


class ExclusiveReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_guard_returns_structured_reader_busy(self):
        with patch.object(main, "_find_running_by_reader",
                          return_value={"id": "2", "name": "SIM 2"}):
            with self.assertRaises(main.HTTPException) as raised:
                main._esim_guard_engine("AK9563 00 00")
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["code"], "reader_busy")
        self.assertEqual(raised.exception.detail["instance_id"], "2")

    async def test_chip_without_stop_refuses_a_running_line(self):
        with patch.object(main, "_esim_resolve_reader",
                          return_value=("AK9563 00 00", 0)), \
                patch.object(main, "_find_running_by_reader",
                             return_value={"id": "1"}), \
                patch.object(main.lpa, "load_all_ses", new=AsyncMock()) as load:
            with self.assertRaises(main.HTTPException) as raised:
                await main.api_esim_chip(reader="AK9563 00 00")
        self.assertEqual(raised.exception.detail["code"], "reader_busy")
        load.assert_not_awaited()

    async def test_chip_with_stop_halts_vowifi_then_loads(self):
        with patch.object(main, "_esim_resolve_reader",
                          return_value=("AK9563 00 00", 0)), \
                patch.object(main, "_esim_stop_for_lpa",
                             new=AsyncMock(return_value={"1": {"enabled": True,
                                                                "running": True}})) as stop, \
                patch.object(main, "_find_running_by_reader", return_value=None), \
                patch.object(main, "_esim_run", new=AsyncMock(side_effect=_absorb_lpa)), \
                patch.object(main, "_esim_imei_for_reader", return_value=""), \
                patch.object(main, "_esim_cache_store"):
            result = await main.api_esim_chip(reader="AK9563 00 00", stop=True)
        stop.assert_awaited_once()
        self.assertTrue(result["ok"])
        self.assertFalse(result["line_running"])

    async def test_download_refuses_a_running_line(self):
        with patch.object(main, "_esim_resolve_reader",
                          return_value=("AK9563 00 00", 0)), \
                patch.object(main, "_esim_resolve_se",
                             return_value={"id": "default", "aid": None}), \
                patch.object(main, "_find_running_by_reader",
                             return_value={"id": "2"}), \
                patch.object(main.hub, "lpa_busy", {}):
            with self.assertRaises(main.HTTPException) as raised:
                await main.api_esim_download({
                    "reader": "AK9563 00 00",
                    "activation_code": "LPA:1$smdp.example.test$MATCH",
                })
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["code"], "reader_busy")


class NativeProfileSwitchTests(unittest.IsolatedAsyncioTestCase):
    async def test_enable_stops_vowifi_rebinds_new_iccid_and_does_not_start_old_line(self):
        lines = {
            "2": {"id": "2", "enabled": True, "iccid": "89000000000000aaaa",
                  "provisioning_state": "ready", "imsi": "234151234567890",
                  "mcc": "234", "mnc": "15", "smsc": "+447700900111",
                  "imei": "123456789012345"},
        }
        card = SimpleNamespace(
            iccid="89000000000000BBBB", imsi="234151234567890", mcc="234",
            mnc="15", mnc_len=2, pin_enabled=False, pin_tries=3, smsc="+447700900111",
            carrier_identity={})
        started = []

        def get_instance(iid):
            return dict(lines[str(iid)])

        def upsert(value):
            iid = str(value["id"])
            lines[iid].update(value)
            return dict(lines[iid])

        with ExitStack() as stack:
            stack.enter_context(patch.object(main, "_esim_resolve_reader",
                                             return_value=("AK9563 00 00", 0)))
            stack.enter_context(patch.object(main, "_esim_switch_identity",
                                             return_value=("reader:AK9563 00 00", "")))
            stack.enter_context(patch.object(main, "_find_running_by_reader",
                                             return_value={"id": "2", "enabled": True}))
            stack.enter_context(patch.object(main.cfg, "get_instance",
                                             side_effect=get_instance))
            stack.enter_context(patch.object(main.cfg, "upsert_instance",
                                             side_effect=upsert))
            stack.enter_context(patch.object(
                main.cfg, "list_instances",
                side_effect=lambda: [dict(line) for line in lines.values()]))
            stack.enter_context(patch.object(main.engine, "is_running", return_value=True))
            stop = stack.enter_context(patch.object(main.engine, "stop"))
            stack.enter_context(patch.object(
                main, "api_instance_start",
                new=AsyncMock(side_effect=lambda iid: started.append(iid))))
            stack.enter_context(patch.object(main.hub, "drop_ami", new=AsyncMock()))
            stack.enter_context(patch.object(main.hub, "reset_health"))
            stack.enter_context(patch.object(
                main.hub, "cards",
                {"AK9563 00 00": {"name": "AK9563 00 00", "matched": "2",
                                  "iccid": "89000000000000aaaa"}}))
            stack.enter_context(patch.object(
                main.hub, "cards_list",
                return_value=[{"name": "AK9563 00 00", "matched": "2"}]))
            stack.enter_context(patch.object(main.hub, "broadcast", new=AsyncMock()))
            stack.enter_context(patch.object(main.egress, "publish"))
            stack.enter_context(patch.object(main, "_esim_resolve_se",
                                             return_value={"id": "default", "aid": None}))
            stack.enter_context(patch.object(main, "_esim_run",
                                             new=AsyncMock(side_effect=_absorb_lpa)))
            stack.enter_context(patch.object(main, "_esim_cache_update_profile"))
            stack.enter_context(patch.object(main.sim, "read_card", return_value=card))
            stack.enter_context(patch.object(
                main.cfg, "card_auto_create_suppressed", return_value=False))
            stack.enter_context(patch.object(
                main, "_hardware_imei_for_card",
                return_value=("123456789012345", "reader-1", "reader")))
            stack.enter_context(patch.object(main, "_carrier_identity_update",
                                             return_value={}))
            result = await main.api_esim_enable(
                "89000000000000BBBB", {"reader": "AK9563 00 00"})

        stop.assert_called_once_with("2")
        self.assertEqual(started, [])
        self.assertEqual(normalize_iccid(lines["2"]["iccid"]),
                         normalize_iccid("89000000000000BBBB"))
        self.assertFalse(lines["2"]["enabled"])
        self.assertEqual(result["instance_id"], "2")
        self.assertNotEqual(normalize_iccid(result["card"]["iccid"]),
                            normalize_iccid("89000000000000aaaa"))

    async def test_leftover_foreign_card_is_card_mismatch_not_old_line_start(self):
        with patch.object(main, "_esim_refresh_card",
                          new=AsyncMock(return_value={
                              "name": "AK9563 00 00", "index": 0,
                              "iccid": "89000000000000OLD1"})):
            with self.assertRaises(main.HTTPException) as raised:
                await main._esim_bind_after_switch(
                    "AK9563 00 00", 0, "89000000000000NEW2")
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["code"], "card_mismatch")
        self.assertIn("OLD1", raised.exception.detail["card_iccid"])

    async def test_unread_card_after_switch_is_fail_closed(self):
        with patch.object(main, "_esim_refresh_card",
                          new=AsyncMock(return_value={
                              "name": "AK9563 00 00", "index": 0, "iccid": ""})):
            with self.assertRaises(main.HTTPException) as raised:
                await main._esim_bind_after_switch(
                    "AK9563 00 00", 0, "89000000000000NEW2")
        self.assertEqual(raised.exception.detail["code"], "card_mismatch")

    async def test_modem_recover_rebinds_without_starting_the_old_line(self):
        started = []
        bound = {
            "card": {"iccid": "89000000000000NEW2"},
            "instance_id": "2",
            "draft": False,
            "missing": [],
        }
        with patch.object(main, "_esim_restart_modem_bridge",
                          new=AsyncMock(return_value={"state": "channels_ready"})), \
                patch.object(main, "_esim_refresh_modem_readers",
                             new=AsyncMock(return_value=({"index": 0}, ["reader"]))), \
                patch.object(main, "_esim_bind_after_switch",
                             new=AsyncMock(return_value=bound)), \
                patch.object(main, "api_instance_start",
                             new=AsyncMock(side_effect=lambda iid: started.append(iid))):
            result = await main._esim_recover_profile_switch(
                "reader", "modem-1", "89000000000000NEW2")
        self.assertEqual(started, [])
        self.assertEqual(result["instance_id"], "2")
        self.assertFalse(result.get("draft"))


class LpaChipNormalizeTests(unittest.IsolatedAsyncioTestCase):
    async def test_chip_info_passes_certs_through(self):
        payload = {
            "eidValue": "89000000000000000000000000000000",
            "EuiccConfiguredAddresses": {"defaultDpAddress": "lpa.example.test"},
            "EUICCInfo2": {
                "extCardResource": {"freeNonVolatileMemory": 12},
                "sasAccreditationNumber": "GSMA-SAS-UP",
                "euiccCiPKIdListForVerification": ["CI1"],
            },
        }
        with patch.object(lpa, "run_lpac",
                          new=AsyncMock(return_value=lpa.LpaResult(data=payload))):
            chip = await lpa.chip_info("AK9563 00 00")
        self.assertEqual(chip["eid"], "89000000000000000000000000000000")
        self.assertEqual(chip["certs"]["sas"], "GSMA-SAS-UP")
        self.assertEqual(chip["certs"]["ci_verify"], ["CI1"])


if __name__ == "__main__":
    unittest.main()
