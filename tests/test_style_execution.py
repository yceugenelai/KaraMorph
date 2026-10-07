import math
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from app.style_execution import confirm_execution, execution_plan
from workers.style_worker import validate_candidate_lengths


class StyleExecutionTests(unittest.TestCase):
    hardware = {"cuda": True, "gpu_name": "NVIDIA test", "vram_gib": 4,
                "gpu_max_seconds": 360, "ram_available_gib": 24}

    def test_gpu_boundary_and_slowed_song(self):
        self.assertEqual(execution_plan(self.hardware, 360, 2)["device"], "cuda")
        self.assertEqual(execution_plan(self.hardware, 360.01, 2)["device"], "cpu")
        self.assertEqual(execution_plan(self.hardware, 330 / .9, 2)["device"], "cpu")

    def test_uses_detected_tier_and_cpu_without_gpu(self):
        self.assertEqual(execution_plan(self.hardware | {"gpu_max_seconds": 600}, 480, 2)["device"], "cuda")
        self.assertEqual(execution_plan(self.hardware | {"cuda": False}, 15, 2)["device"], "cpu")

    def test_estimates_count_time_but_not_duplicate_ram(self):
        one, two = [execution_plan(self.hardware, 420, n) for n in (1, 2)]
        self.assertGreater(two["cpu_minutes_low"], one["cpu_minutes_low"])
        self.assertEqual(one["cpu_ram_gib"], two["cpu_ram_gib"])
        self.assertGreater(one["generation_timeout"], 600)

    def test_invalid_duration_rejected(self):
        for value in (0, -1, math.inf, math.nan):
            with self.assertRaises(ValueError):
                execution_plan(self.hardware, value, 2)

    def test_gpu_has_no_confirmation_cpu_requires_ok(self):
        from PySide6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel) as question:
            self.assertTrue(confirm_execution(None, execution_plan(self.hardware, 60, 2)))
            question.assert_not_called()
            self.assertFalse(confirm_execution(None, execution_plan(self.hardware, 420, 2)))
            self.assertIn("RAM", question.call_args.args[2])
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Ok):
            self.assertTrue(confirm_execution(None, execution_plan(self.hardware, 420, 2)))

    def test_truncated_output_rejected_without_hiding_successful_candidate(self):
        with patch("pathlib.Path.is_file", return_value=True), patch("soundfile.info", side_effect=[Mock(duration=360), Mock(duration=420)]):
            records = [{"success": True, "output": "short.wav"}, {"success": True, "output": "full.wav"}]
            validate_candidate_lengths(records, 420)
        self.assertFalse(records[0]["success"])
        self.assertIn("incomplete", records[0]["error"])
        self.assertTrue(records[1]["success"])

    def test_consumer_cancel_does_not_enqueue_style(self):
        from app.consumer import ConsumerWindow
        ui = Mock()
        with patch("app.consumer.confirm_execution", return_value=False):
            ConsumerWindow._queue_planned_style(ui, "song", {}, {}, {})
        ui._enqueue.assert_not_called()
        ui._finish_operation.assert_called_once_with(False, "已取消風格轉換", show_error=False)

    def test_consumer_passes_cpu_consent_to_worker(self):
        from app.consumer import ConsumerWindow
        plan = execution_plan(self.hardware, 420, 2)
        request = dict(caption="jazz", tag="jazz", count=2, fixed_strength=.2, fixed_noise=.2,
                       strengths=".2", noises=".2", seeds="1234", style_label="Jazz", manifest_path="manifest.json")
        with tempfile.TemporaryDirectory() as temp:
            ui = Mock(workspace=Path(temp))
            metadata = dict(backing="backing.wav", vocal_path="vocal.wav", source_id="fx", manifest_path="manifest.json")
            with patch("app.consumer.confirm_execution", return_value=True):
                ConsumerWindow._queue_planned_style(ui, "song", metadata, request, plan)
            args = ui._enqueue.call_args.args[3]
            self.assertIn("--cpu-confirmed", args)
            self.assertEqual(args[args.index("--device")+1], "cpu")



if __name__ == "__main__":
    unittest.main()
