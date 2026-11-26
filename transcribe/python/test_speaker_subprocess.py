from pathlib import Path
import sys
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from diarize_recording import validate_device
from speaker_subprocess import run_speaker_command, speaker_environment


class SpeakerSubprocessTests(unittest.TestCase):
    def test_speaker_cuda_libraries_take_precedence_over_asr_libraries(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            libraries = prefix / "lib/python3.12/site-packages/nvidia/cudnn/lib"
            libraries.mkdir(parents=True)
            with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/asr/old-cudnn"}):
                environment = speaker_environment(prefix / "bin/python")
                self.assertEqual(environment["LD_LIBRARY_PATH"], str(libraries) + ":/asr/old-cudnn")
                self.assertEqual(os.environ["LD_LIBRARY_PATH"], "/asr/old-cudnn")

    def test_failed_child_exposes_traceback_and_preserves_log(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "window.log"
            with self.assertRaisesRegex(RuntimeError, "Torch not compiled with CUDA enabled"):
                run_speaker_command([sys.executable, "-c",
                                     "raise AssertionError('Torch not compiled with CUDA enabled')"],
                                    log, 10, lambda: None)
            self.assertIn("Traceback", log.read_text())

    def test_successful_child_output_is_logged(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "window.log"
            run_speaker_command([sys.executable, "-c", "print('completed')"], log, 10, lambda: None)
            self.assertEqual(log.read_text().strip(), "completed")

    def test_cpu_only_runtime_rejects_cuda_before_model_loading(self):
        runtime = SimpleNamespace(version=SimpleNamespace(cuda=None), __version__="2.8.0+cpu",
                                  cuda=MagicMock())
        with self.assertRaisesRegex(RuntimeError, "CPU-only"):
            validate_device(runtime, "cuda")
        runtime.cuda.is_available.assert_not_called()

    def test_cpu_device_does_not_require_cuda(self):
        validate_device(SimpleNamespace(), "cpu")

    def test_cuda_build_with_no_accessible_gpu_reports_driver_error(self):
        runtime = SimpleNamespace(version=SimpleNamespace(cuda="12.6"), cuda=MagicMock())
        runtime.cuda.is_available.return_value = False
        with self.assertRaisesRegex(RuntimeError, "driver"):
            validate_device(runtime, "cuda:0")


if __name__ == "__main__":
    unittest.main()
