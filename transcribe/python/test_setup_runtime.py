from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("setup_runtime", Path(__file__).resolve().parents[1] / "install/setup_runtime.py")
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


class SetupRuntimeTests(unittest.TestCase):
    def gpu(self, driver="580.65.06", capability="8.6"):
        return dict(name="NVIDIA test GPU", driver=driver, capability=capability)

    def test_driver_and_cuda_capability_select_supported_cuda12_build(self):
        for maximum, driver, expected in (((12, 6), "560.28.03", "cu126"),
                                          ((12, 8), "570.26", "cu128"),
                                          ((12, 9), "575.51.03", "cu129"),
                                          ((13, 0), "580.65.06", "cu129")):
            self.assertEqual(setup.select_cuda(maximum, [self.gpu(driver)]), expected)
        # The installed toolkit (nvcc) is not involved; driver capability governs.
        self.assertEqual(setup.select_cuda((13, 0), [self.gpu()], requested="cu126"), "cu126")

    def test_old_driver_blackwell_and_windows_driver_limits(self):
        for maximum, devices in (((12, 4), [self.gpu("550.54.15")]),
                                 ((12, 6), [self.gpu("560.28.03", "12.0")]),
                                 ((13, 0), [self.gpu(capability="6.1")])):
            with self.assertRaises(ValueError):
                setup.select_cuda(maximum, devices)
        self.assertEqual(setup.select_cuda((12, 8), [self.gpu("570.65", "12.0")], wsl=True), "cu128")
        self.assertEqual(setup.select_cuda((12, 8), [self.gpu("570.26")], wsl=True), "cu126")
        with self.assertRaises(ValueError):
            setup.select_cuda((12, 6), [self.gpu("560.28.03")], requested="cu129")

    def test_gpu_probe_parses_all_devices_and_fails_closed_without_driver(self):
        results = [subprocess.CompletedProcess([], 0, stdout="CUDA Version: 13.0"),
                   subprocess.CompletedProcess([], 0, stdout="RTX 3050, 580.65.06, 8.6\nRTX 3060, 580.65.06, 8.6\n")]
        with patch.object(setup.shutil, "which", return_value="/bin/nvidia-smi"), \
                patch.object(setup.subprocess, "run", side_effect=results):
            maximum, devices = setup.detect_gpus()
        self.assertEqual(maximum, (13, 0))
        self.assertEqual(len(devices), 2)
        with patch.object(setup.shutil, "which", return_value="/bin/nvidia-smi"), \
                patch.object(setup.subprocess, "run", side_effect=subprocess.CalledProcessError(9, "nvidia-smi")):
            with self.assertRaisesRegex(ValueError, "detection failed"):
                setup.detect_gpus()

    def test_platform_gates_native_mac_cpu_and_windows_wsl(self):
        setup.check_platform("cpu", "Darwin", "arm64")
        setup.check_platform("gpu", "Linux", "x86_64")
        setup.check_platform("cpu", "Windows", "AMD64")
        setup.check_platform("gpu", "Windows", "AMD64")
        for mode, system, machine in (("gpu", "Darwin", "arm64"),
                                       ("cpu", "Darwin", "x86_64"),
                                       ("cpu", "Windows", "ARM64")):
            with self.assertRaises(ValueError):
                setup.check_platform(mode, system, machine)

    def test_dry_run_installs_nothing_and_uses_separate_requirements(self):
        with tempfile.TemporaryDirectory() as directory:
            asr = Path(directory) / "asr"
            speaker = Path(directory) / "speaker"
            output = io.StringIO()
            with patch.object(setup, "check_platform"), patch.object(setup.subprocess, "run") as run, redirect_stdout(output):
                setup.main(["--mode", "cpu", "--dry-run", "--asr-venv", str(asr), "--diarization-venv", str(speaker)])
            run.assert_not_called()
            self.assertFalse(asr.exists())
            self.assertFalse(speaker.exists())
            self.assertIn("requirements-pytorch-cpu.txt", output.getvalue())
            self.assertIn("requirements-diarization-pytorch-cpu.txt", output.getvalue())
            self.assertIn(".env was not edited", output.getvalue())

    def test_requirements_files_parse_without_fetching_any_packages(self):
        from pip._internal.network.session import PipSession
        from pip._internal.req.req_file import parse_requirements
        from packaging.requirements import Requirement
        for path in setup.INSTALL.glob("requirements*.txt"):
            with self.subTest(path=path.name):
                requirements = [Requirement(item.requirement) for item in parse_requirements(str(path), session=PipSession())]
                self.assertTrue(requirements)
                if "pytorch" in path.name:
                    pins = [req for req in requirements if req.name == "torch"]
                    self.assertTrue(pins)
                    self.assertTrue(all("2.8.0" in str(req.specifier) for req in pins))

    def test_launcher_uses_selected_environment_libraries_before_importing_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "lib/python3.11/site-packages/nvidia/cublas/lib"
            library.mkdir(parents=True)
            cuda13 = root / "lib/python3.11/site-packages/nvidia/cu13/lib"
            cuda13.mkdir(parents=True)
            worker = root / "worker.py"
            worker.write_text("raise AssertionError('must only exec, not import')")
            with patch.object(sys, "prefix", str(root)), patch.object(sys, "argv", ["start_worker.py", str(worker)]), \
                    patch("os.execve") as execute:
                runpy.run_path(str(Path(__file__).parent / "start_worker.py"), run_name="__main__")
            executable, arguments, environment = execute.call_args.args
            self.assertEqual(executable, sys.executable)
            self.assertEqual(arguments[-1], str(worker))
            self.assertTrue(environment["LD_LIBRARY_PATH"].startswith(str(library)))
            self.assertNotIn(str(cuda13), environment["LD_LIBRARY_PATH"].split(":"))
            self.assertNotIn(str(cuda13), setup.library_environment(root)["LD_LIBRARY_PATH"].split(":"))


if __name__ == "__main__":
    unittest.main()
