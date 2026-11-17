"""The painter's pre-flight guards: VRAM, RAM, and the profile override."""
from __future__ import annotations

import unittest
from unittest import mock

from murmur import fluxgen


class PlanTests(unittest.TestCase):
    def _plan(self, vram=(16000.0, 16304.0), ram=(12000.0, 32000.0), environ=None):
        with mock.patch.object(fluxgen, "_vram", return_value=vram), \
             mock.patch.object(fluxgen, "_ram", return_value=ram), \
             mock.patch.dict("os.environ", environ or {}, clear=False):
            return fluxgen._plan(object(), "cuda")

    def test_refuses_when_system_memory_is_low(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            self._plan(ram=(900.0, 32000.0))
        self.assertIn("system memory", str(caught.exception))

    def test_refuses_when_the_card_has_no_room(self) -> None:
        # With a q4 transformer available the refusal is about VRAM.
        with mock.patch.object(fluxgen, "_gguf_path", return_value=__file__):
            with self.assertRaises(SystemExit) as caught:
                self._plan(vram=(2500.0, 16304.0))
        self.assertIn("refusing", str(caught.exception))

    def test_refuses_when_there_is_no_q4_transformer_and_no_room(self) -> None:
        # Without one, the message points at the setup script instead.
        with mock.patch.object(fluxgen, "_gguf_path", return_value=None):
            with self.assertRaises(SystemExit) as caught:
                self._plan(vram=(2500.0, 16304.0))
        self.assertIn("no q4 transformer", str(caught.exception))

    def test_balanced_picks_q4_with_the_encoder_on_the_gpu(self) -> None:
        with mock.patch.object(fluxgen, "_gguf_path", return_value=__file__):
            plan = self._plan(environ={"MURMUR_FLUX_PRECISION": "", "MURMUR_FLUX_TEXT_DEVICE": ""})
        self.assertEqual("q4", plan["precision"])
        self.assertEqual("cuda", plan["text_device"])

    def test_text_device_cpu_is_honoured(self) -> None:
        with mock.patch.object(fluxgen, "_gguf_path", return_value=__file__):
            plan = self._plan(environ={"MURMUR_FLUX_TEXT_DEVICE": "cpu"})
        self.assertEqual("cpu", plan["text_device"])

    def test_cpu_profile_refuses_to_load_anything(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            self._plan(environ={"MURMUR_FLUX_PRECISION": "cpu"})
        self.assertIn("placeholder", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
