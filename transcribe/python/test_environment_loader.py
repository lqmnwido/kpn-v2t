import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = os.environ.get("POWERSHELL_TEST_EXECUTABLE") or shutil.which("pwsh")


class EnvironmentLoaderTests(unittest.TestCase):
    def configuration(self, folder):
        marker = folder / "must-not-execute"
        value = f"literal $(touch {marker}); 'quoted'\nsecond line\n"
        path = folder / "test.env"
        path.write_text("\n".join(f"{key}={json.dumps(item)}" for key, item in {
            "FFMPEG_PATH": sys.executable, "FFPROBE_PATH": sys.executable,
            "LOADER_TEST_VALUE": value,
        }.items()) + "\n")
        return path, marker, value

    def test_bash_loads_parent_environment_without_starting_maven_or_executing_values(self):
        with tempfile.TemporaryDirectory(prefix="env loader ") as directory:
            folder = Path(directory)
            config, marker, value = self.configuration(folder)
            capture = folder / "result.json"
            shell = '''source "$1" --env-file "$2" --python-executable "$3" || exit 1
"$3" -c 'import json,os,sys; open(sys.argv[1],"w").write(json.dumps({"value":os.environ["LOADER_TEST_VALUE"],"tool":os.environ["FFMPEG_PATH"]}))' "$4"
'''
            result = subprocess.run(["bash", "-c", shell, "loader-test", str(ROOT / "install/load_env.sh"),
                                     str(config), sys.executable, str(capture)],
                                    cwd=folder, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            captured = json.loads(capture.read_text())
            self.assertEqual(captured["value"], value)
            self.assertEqual(captured["tool"], str(Path(sys.executable).resolve()))
            self.assertFalse(marker.exists())
            self.assertNotIn("literal", result.stdout)

    def test_bash_rejects_child_execution_and_invalid_names_without_applying_exports(self):
        result = subprocess.run(["bash", str(ROOT / "install/load_env.sh")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            config, _, _ = self.configuration(folder)
            with config.open("a") as file:
                file.write("BAD-NAME=value\n")
            result = subprocess.run(["bash", "-c", 'source "$1" --env-file "$2" --python-executable "$3"',
                                     "test", str(ROOT / "install/load_env.sh"), str(config), sys.executable],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid variable name", result.stderr)
            self.assertNotIn("literal", result.stdout)

    @unittest.skipUnless(POWERSHELL, "PowerShell not available")
    def test_powershell_loads_current_process_without_starting_maven(self):
        with tempfile.TemporaryDirectory(prefix="ps env loader ") as directory:
            folder = Path(directory)
            config, marker, value = self.configuration(folder)
            capture = folder / "result.json"
            script = folder / "check.ps1"
            script.write_text('''param($Loader, $ConfigFile, $Python, $Capture)
$ErrorActionPreference = "Stop"
& $Loader -EnvFile $ConfigFile -PythonExecutable $Python
@{value=$env:LOADER_TEST_VALUE; tool=$env:FFMPEG_PATH} | ConvertTo-Json | Set-Content -Encoding utf8 $Capture
''')
            result = subprocess.run([POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(script),
                                     str(ROOT / "install/load_env.ps1"), str(config), sys.executable, str(capture)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(capture.read_text(encoding="utf-8-sig"))["value"], value)
            self.assertFalse(marker.exists())
            self.assertNotIn("literal", result.stdout)


if __name__ == "__main__":
    unittest.main()
