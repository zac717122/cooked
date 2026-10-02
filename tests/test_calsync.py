import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import calsync  # noqa: E402

NOW = 1790000000  # 2026-09 左右

MOODLE_ICS = """BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Moodle Pty Ltd//NONSGML Moodle Version 2024//EN\r
BEGIN:VEVENT\r
UID:123@moodle.ncku.edu.tw\r
SUMMARY:HW1 \\, 微積分作業\r
DESCRIPTION:請上傳 PDF 這是一段很長很長很長很長很長很長很長很長很長很長很長很長的\r
  說明文字\r
CATEGORIES:A1-101\r
DTSTART:{start}\r
DTEND:{start}\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:old@moodle.ncku.edu.tw\r
SUMMARY:很久以前的作業\r
CATEGORIES:A1-101\r
DTSTART:20200101T000000Z\r
END:VEVENT\r
END:VCALENDAR\r
"""


class CalsyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ics = os.path.join(self.tmp.name, "moodle.ics")
        with open(self.ics, "w", encoding="utf-8") as f:
            f.write(MOODLE_ICS.replace("{start}", calsync.utc_stamp(NOW + 86400)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_moodle_ical_prefix_filter_and_alarm(self):
        cfg = {"alarm_minutes_before": [60],
               "sources": [{"type": "moodle_ical", "url": self.ics}]}
        events, _, errors = calsync.collect(cfg, now=NOW)
        self.assertEqual(errors, [])
        self.assertEqual(len(events), 1)  # 舊事件被過濾
        self.assertEqual(events[0].summary, "[A1-101] HW1 , 微積分作業")
        self.assertTrue(events[0].has_alarm())
        out = calsync.render(events, [])
        self.assertTrue(all(len(l.encode()) <= 75 for l in out.split("\r\n")))
        # 輸出可以被自己再讀回來（round trip）
        again = calsync.parse_blocks(calsync.unfold(out), "VEVENT")
        self.assertEqual(calsync.unescape(calsync.prop(again[0], "SUMMARY")),
                         "[A1-101] HW1 , 微積分作業")

    def test_dedupe_and_bad_source(self):
        cfg = {"sources": [{"type": "moodle_ical", "url": self.ics},
                           {"type": "ics", "url": self.ics, "prefix": "X"},
                           {"type": "ics", "url": os.path.join(self.tmp.name, "nope.ics")},
                           {"type": "ics", "url": ""}]}
        events, _, errors = calsync.collect(cfg, now=NOW)
        self.assertEqual(len(events), 1)
        self.assertEqual(len(errors), 2)

    def test_env_expansion(self):
        with mock.patch.dict(os.environ, {"MY_URL": "https://x/y.ics"}):
            self.assertEqual(calsync.expand_env({"a": ["${MY_URL}", "${MISSING}"]}),
                             {"a": ["https://x/y.ics", ""]})

    def test_moodle_api(self):
        api = {"events": [{"id": 9, "name": "Lab 3 is due", "timesort": NOW + 3600,
                           "url": "https://moodle.ncku.edu.tw/mod/assign/view.php?id=1",
                           "course": {"shortname": "DS", "fullname": "資料結構"}}]}
        with mock.patch.object(calsync, "fetch", return_value=json.dumps(api)) as f:
            events, _ = calsync.load_moodle_api_source(
                {"base_url": "https://moodle.ncku.edu.tw", "token": "t"}, now=NOW)
        self.assertEqual(f.call_args.kwargs["data"]["wsfunction"],
                         "core_calendar_get_action_events_by_timesort")
        self.assertEqual(events[0].summary, "[DS] Lab 3 is due")
        self.assertEqual(events[0].uid, "moodle-9@moodle.ncku.edu.tw")

    def test_moodle_api_error(self):
        err = {"exception": "x", "message": "Invalid token"}
        with mock.patch.object(calsync, "fetch", return_value=json.dumps(err)):
            with self.assertRaisesRegex(RuntimeError, "Invalid token"):
                calsync.load_moodle_api_source({"base_url": "https://m", "token": "t"})

    def test_main_writes_file(self):
        cfg_path = os.path.join(self.tmp.name, "c.json")
        out = os.path.join(self.tmp.name, "out", "cal.ics")
        with open(cfg_path, "w") as f:
            json.dump({"sources": [{"type": "moodle_ical", "url": self.ics}]}, f)
        with mock.patch.object(calsync.time, "time", return_value=NOW):
            self.assertEqual(calsync.main(["-c", cfg_path, "-o", out]), 0)
        with open(out, encoding="utf-8") as f:
            self.assertIn("BEGIN:VCALENDAR", f.read())


if __name__ == "__main__":
    unittest.main()
