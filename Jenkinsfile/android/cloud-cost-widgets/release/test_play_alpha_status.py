import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import play_alpha_status as alpha_status


class AlphaStatusTest(unittest.TestCase):
    def test_preflight_reads_each_guarded_track_once(self):
        statuses = {
            'alpha': {'result': 'pending_release', 'http_status': 200},
            'internal': {'result': 'ok', 'http_status': 200},
            'wear:internal': {'result': 'ok', 'http_status': 200},
        }
        # Three reads per run: Play limits this lifecycle read to about a dozen within a few minutes.
        self.assertEqual(alpha_status.TRACK_NAMES, tuple(statuses))
        for failure, expected in (
            (None, 'checked'),
            ({'result': 'forbidden', 'http_status': 403, 'quota': 'listing_releases_quota_exceeded'}, 'quota_limited'),
            ({'result': 'forbidden', 'http_status': 403}, 'failed'),
        ):
            with self.subTest(expected=expected):
                tracks = statuses if failure is None else statuses | {'wear:internal': failure}
                with patch.object(alpha_status, 'get_access_token', return_value=('token', 200)), patch.object(
                    alpha_status, 'read_live_track', side_effect=lambda opener, token, track, **kwargs: tracks[track],
                ) as read_track:
                    report = alpha_status.read_status('synthetic-account')
                self.assertEqual(report['result'], expected)
                self.assertEqual(report['tracks'], tracks)
                self.assertEqual(datetime.fromisoformat(report['observed_at']).tzinfo, timezone.utc)
                self.assertEqual([entry.args[2] for entry in read_track.call_args_list], list(statuses))


if __name__ == '__main__':
    unittest.main()
