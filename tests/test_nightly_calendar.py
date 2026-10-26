from datetime import datetime,timezone
import unittest
from tools.nightly_consolidation import interval


class CalendarTests(unittest.TestCase):
    def test_utc_evening_uses_new_shanghai_day(self):
        start,end=interval(datetime(2026,10,18,20,30,tzinfo=timezone.utc))
        self.assertEqual(start.isoformat(),'2026-10-17T16:00:00+00:00')
        self.assertEqual(end.isoformat(),'2026-10-18T16:00:00+00:00')

    def test_leap_day_and_new_year(self):
        for now,expected in [(datetime(2024,3,1,0,tzinfo=timezone.utc),'2024-02-28T16:00:00+00:00'),
                             (datetime(2027,1,1,0,tzinfo=timezone.utc),'2026-12-30T16:00:00+00:00')]:
            start,end=interval(now)
            self.assertEqual(start.isoformat(),expected);self.assertEqual((end-start).total_seconds(),86400)
