"""Regression tests for the filters that decide what you see. Run: python -m unittest -v"""
import json
import os
import tempfile
import unittest
from unittest import mock

import more_ats

from fetch_jobs import (is_dev_job, is_location_allowed, load_tracking, residence_terms, score_job, workplace,
                        write_json)

FRANCE = residence_terms("france")
COMPANY = {"dev_departments": ["engineering"]}


def job(title="Backend Engineer", location="Remote", department="Engineering", **extra):
    return {"title": title, "location": location, "department": department, "remote": True, **extra}


class Locations(unittest.TestCase):
    def test_residence_france(self):
        for loc in ("Remote", "Remote, EMEA", "Paris", "Remote, France; Remote, Germany", "Remote; India; France"):
            self.assertTrue(is_location_allowed(loc, FRANCE), loc)
        for loc in ("Germany (Remote)", "London, UK", "Remote, US", "Paris (Hybrid)", "Remote - in-office"):
            self.assertFalse(is_location_allowed(loc, FRANCE), loc)

    def test_no_location_is_kept(self):
        self.assertTrue(is_location_allowed("", FRANCE))


class DevJobs(unittest.TestCase):
    def test_international_is_not_an_internship(self):
        self.assertTrue(is_dev_job(job("International Payments Engineer"), COMPANY, FRANCE))
        self.assertFalse(is_dev_job(job("Software Engineer Intern"), COMPANY, FRANCE))

    def test_accept_locations_is_per_company(self):
        england = job(location="England")
        self.assertFalse(is_dev_job(england, COMPANY, FRANCE))
        self.assertTrue(is_dev_job(england, dict(COMPANY, accept_locations=["england"]), FRANCE))

    def test_department_and_title_filters(self):
        self.assertFalse(is_dev_job(job(department="Sales"), COMPANY, FRANCE))
        self.assertFalse(is_dev_job(job("Sales Engineer"), COMPANY, FRANCE))
        self.assertFalse(is_dev_job(job("Product Designer"), COMPANY, FRANCE))


class Workplace(unittest.TestCase):
    def test_board_field_wins_over_remote_flag(self):
        self.assertEqual(workplace({"workplace": "hybrid", "remote": True}), "hybrid")
        self.assertEqual(workplace({"remote": True}), "remote")
        self.assertEqual(workplace({"location": "Paris"}), "on-site")


class Score(unittest.TestCase):
    def test_title_boost_and_avoid(self):
        j = score_job({"title": "Senior Python Engineer", "text": "Python and Docker", "url": "u"},
                      ["python", "docker"], ["senior"], ["intern"])
        self.assertEqual(j["match_score"], 2 + 2 + 3)  # 2 text hits, +2 python in title, +3 boost


class Files(unittest.TestCase):
    def test_damaged_tracking_file_is_never_read_as_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "applications.json")
            write_json(path, {"u": {"status": "seen"}})
            self.assertEqual(load_tracking(path), {"u": {"status": "seen"}})
            with open(path, "w") as f:
                f.write('{"u": ')
            with self.assertRaises(SystemExit):
                load_tracking(path)
            self.assertEqual(load_tracking(os.path.join(d, "missing.json")), {})


class Descriptions(unittest.TestCase):
    LISTING = {"results": [{"title": "Backend Engineer", "shortcode": "AB1", "location": {}}]}

    def fetch(self, detail_answers):
        calls = iter(detail_answers)

        def fake(url, data=None):
            if "/api/v3/" in url:
                return self.LISTING
            answer = next(calls)
            if isinstance(answer, Exception):
                raise answer
            return answer

        with mock.patch.object(more_ats, "get_json", fake):
            return more_ats.fetch_workable({"name": "Acme", "slug": "acme"})[0]

    def test_one_failed_request_is_retried(self):
        job = self.fetch([OSError("boom"), {"description": "Python", "requirements": ""}])
        self.assertFalse(job["no_description"])
        self.assertIn("Python", job["text"])

    def test_two_failures_flag_the_posting(self):
        self.assertTrue(self.fetch([OSError("boom"), OSError("boom")])["no_description"])


if __name__ == "__main__":
    unittest.main()
