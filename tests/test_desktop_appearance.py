"""Appearance persistence does not share or mutate device/bridge settings."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import desktop_appearance as appearance
from lan_service import METHODS
from web_service import WebService


class AppearanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "local" / "appearance.json"

    def test_round_trip_and_corrupt_preferences_default_to_original_light(self):
        self.assertEqual(appearance.load_preference(self.path), "light")
        for value in appearance.CHOICES:
            appearance.save_preference(self.path, value)
            self.assertEqual(appearance.load_preference(self.path), value)
            self.assertEqual(json.loads(self.path.read_text()), {"appearance": value})
        for raw in ('{', '[]', '{"appearance": ["dark"]}', '{"appearance":"other"}'):
            self.path.write_text(raw)
            self.assertEqual(appearance.load_preference(self.path), "light")

    def test_invalid_or_failed_save_preserves_the_previous_preference(self):
        appearance.save_preference(self.path, "dark")
        for value in (None, "other", {}, ["dark"]):
            with self.assertRaises(ValueError):
                appearance.save_preference(self.path, value)
        with mock.patch.object(appearance.os, "replace", side_effect=OSError("read only")):
            with self.assertRaises(OSError):
                appearance.save_preference(self.path, "light")
        self.assertEqual(appearance.load_preference(self.path), "dark")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_desktop_save_is_separate_from_business_state_and_survives_a_new_service(self):
        state = Path(self.directory.name) / "state.json"
        service = WebService(state_path=state, appearance_path=self.path)
        try:
            before = service.initial()
            self.assertTrue(service.set_appearance("dark")["ok"])
            self.assertEqual(service.initial(), before)
            self.assertFalse(state.exists())
            self.assertFalse(service._dirty)
            self.assertFalse(service.poll()["data"]["running"])
        finally:
            service._shutdown()
        self.assertEqual(appearance.load_preference(self.path), "dark")
        service = WebService(state_path=state, appearance_path=self.path)
        try:
            self.assertEqual(service.initial(), before)
            self.assertFalse(service.set_appearance("bad")["ok"])
        finally:
            service._shutdown()

    def test_lan_cannot_save_the_supervisors_appearance(self):
        self.assertNotIn("set_appearance", METHODS)
        service = WebService(state_path=Path(self.directory.name) / "state.json")
        try:
            self.assertFalse(service.set_appearance("dark")["ok"])
            self.assertFalse(self.path.exists())
        finally:
            service._shutdown()
