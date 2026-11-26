from contextlib import redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

import runtime_paths


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


setup = module("windows_setup", "install/setup_runtime.py")
runner = module("windows_runner", "install/run_worker.py")


class WindowsRuntimeTests(unittest.TestCase):
    def test_python_paths_and_windows_dll_search_are_separate_from_linux(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "Lib/site-packages/torch/lib"
            library.mkdir(parents=True)
            self.assertEqual(runtime_paths.venv_python(root, "Windows"), root / "Scripts/python.exe")
            self.assertEqual(runtime_paths.venv_python(root, "Linux"), root / "bin/python")
            with patch.dict(os.environ, {"PATH": "C:/Windows", "LD_LIBRARY_PATH": "/linux-only"}), \
                    patch.object(runtime_paths.shutil, "which", return_value=None):
                environment = runtime_paths.runtime_environment(root, "Windows")
            self.assertEqual(environment["PATH"], str(library) + ";C:/Windows")
            self.assertEqual(environment["LD_LIBRARY_PATH"], "/linux-only")
            self.assertEqual(environment["PYTHONIOENCODING"], "utf-8")

    def test_dll_directory_handles_are_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory) / "Lib/site-packages/torch/lib"
            library.mkdir(parents=True)
            with patch.object(runtime_paths.platform, "system", return_value="Windows"), \
                    patch.object(runtime_paths.shutil, "which", return_value=None), \
                    patch.object(os, "add_dll_directory", create=True) as add:
                runtime_paths.activate_dll_directories(directory)
            add.assert_called_once_with(str(library))
            self.assertIn(add.return_value, runtime_paths._DLL_HANDLES)

    def test_windows_setup_dry_run_uses_scripts_paths_and_windows_driver_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with patch.object(setup.platform, "system", return_value="Windows"), \
                    patch.object(setup.platform, "machine", return_value="AMD64"), \
                    patch.object(setup, "detect_gpus", return_value=((12,8), [dict(name="GPU", driver="570.65", capability="8.6")])), \
                    patch.object(setup.subprocess, "run") as install, redirect_stdout(output):
                setup.main(["--mode", "gpu", "--dry-run", "--asr-venv", directory + "/asr", "--diarization-venv", directory + "/speaker"])
            install.assert_not_called()
            self.assertIn("Scripts/python.exe", output.getvalue())
            self.assertIn('"profile": "cu128"', output.getvalue())
            self.assertFalse((Path(directory) / "asr").exists())

    def test_verification_uses_windows_dll_names_and_waveform_diarization(self):
        for component in ("asr", "diarization"):
            for gpu in (True, False):
                for system in ("Windows", "Linux"):
                    code = setup.verification_code(component, gpu, system)
                    compile(code, "runtime-check", "exec")
        self.assertIn("cublas64_12.dll", setup.verification_code("asr", True, "Windows"))
        self.assertNotIn("libcublas.so", setup.verification_code("asr", True, "Windows"))
        windows_speaker = setup.verification_code("diarization", True, "Windows")
        self.assertIn("'waveform'", windows_speaker)
        self.assertNotIn("from torchcodec.decoders", windows_speaker)

    def test_launcher_keeps_windows_worker_in_java_owned_process(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = Path(directory) / "worker.py"
            worker.write_text("raise AssertionError('must use patched worker runner')")
            launch = runpy.run_path
            with patch.object(runtime_paths.platform, "system", return_value="Windows"), \
                    patch.dict(os.environ), patch.object(sys, "argv", ["start_worker.py", str(worker)]), \
                    patch.object(sys, "path", list(sys.path)), \
                    patch("runpy.run_path") as execute, patch("os.execve") as spawn:
                launch(str(Path(__file__).parent / "start_worker.py"), run_name="__main__")
            execute.assert_called_once_with(str(worker), run_name="__main__")
            spawn.assert_not_called()

    def test_env_loader_resolves_tools_without_executing_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            marker = Path(directory) / "never-created"
            path.write_text(f'FFMPEG_PATH=ffmpeg.exe\nFFPROBE_PATH=ffprobe.exe\nEXAMPLE_SECRET="$(touch {marker})"\n')
            with patch.object(runner.shutil, "which", side_effect=lambda name, **kwargs: str(Path(directory) / name)):
                environment = runner.worker_environment(path)
            self.assertEqual(environment["FFMPEG_PATH"], str(Path(directory) / "ffmpeg.exe"))
            self.assertEqual(environment["EXAMPLE_SECRET"], f"$(touch {marker})")
            self.assertFalse(marker.exists())

    def test_windows_template_has_all_shared_keys_without_auth_token(self):
        from dotenv import dotenv_values
        root = Path(__file__).resolve().parents[1]
        shared = dotenv_values(root / ".env.example")
        windows = dotenv_values(root / "install/env.windows.example")
        self.assertEqual(set(shared), set(windows))
        self.assertEqual(windows["HF_TOKEN"], "")
        self.assertEqual(windows["PYTHON_EXECUTABLE"], "python/venv/Scripts/python.exe")
        self.assertEqual(windows["ASR_WORK_DIR"], "./work")


if __name__ == "__main__":
    unittest.main()
