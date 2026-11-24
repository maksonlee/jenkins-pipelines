"""Exercise the Flutter connected-device script with fake adb, emulator, avdmanager, flutter and Gradle."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / "resources/flutter/connected-tests.sh").read_text(encoding="utf-8")

FAKE_ADB = r"""#!/bin/bash
state="$FAKE_STATE"
if [[ "$1" == start-server ]]; then exit 0; fi
if [[ "$1" == devices ]]; then
  echo 'List of devices attached'
  cat "$state/physical" "$state/emulators" 2>/dev/null | sed 's/$/\tdevice/'
  exit 0
fi
if [[ "$1" == -s ]]; then
  serial=$2; shift 2
  case "$*" in
    "shell getprop ro.product.model") grep "^$serial " "$state/models" | cut -d' ' -f2 ;;
    "shell getprop sys.boot_completed") grep -qx "$serial" "$state/emulators" 2>/dev/null && echo 1 ;;
    "emu kill") echo "$serial" >> "$state/killed"; sed -i "/^$serial\$/d" "$state/emulators" ;;
  esac
  exit 0
fi
exit 0
"""
FAKE_EMULATOR = r"""#!/bin/bash
port=""
while [[ $# -gt 0 ]]; do [[ "$1" == -port ]] && port=$2; shift; done
echo "emulator-$port" >> "$FAKE_STATE/emulators"
echo "emulator-$port" >> "$FAKE_STATE/started"
trap 'exit 0' TERM
while true; do sleep 0.2; done
"""
FAKE_AVDMANAGER = r"""#!/bin/bash
cat >/dev/null
echo "$ANDROID_AVD_HOME" > "$FAKE_STATE/avd_home"
"""
FAKE_FLUTTER = r"""#!/bin/bash
if [[ "$1" == test ]]; then echo "$4" >> "$FAKE_STATE/flutter_runs"; fi
"""
FAKE_GRADLEW = """#!/bin/bash
echo "$ANDROID_SERIAL" > "$FAKE_STATE/gradle_serial"
"""


class ConnectedDevicesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.workspace = self.root / "workspace"
        sdk = self.root / "android-cache/flutter-android-sdk"
        self.tools = {
            "adb": (sdk / "platform-tools/adb", FAKE_ADB),
            "emulator": (sdk / "emulator/emulator", FAKE_EMULATOR),
            "avdmanager": (sdk / "cmdline-tools/latest/bin/avdmanager", FAKE_AVDMANAGER),
            "flutter": (self.root / "flutter-cache/fvm/versions/3.47.8/bin/flutter", FAKE_FLUTTER),
        }
        for path, body in self.tools.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)
        (sdk / "system-images/android-34/default/x86_64").mkdir(parents=True)
        self.kvm = self.root / "kvm"
        self.kvm.write_text("")
        source = self.workspace / "source"
        (source / "integration_test").mkdir(parents=True)
        (source / "integration_test/app_test.dart").write_text("// test\n")
        (source / "android/app/src/androidTest").mkdir(parents=True)
        (source / "android/app/src/androidTest/Placeholder.kt").write_text("// test\n")
        gradlew = source / "android/gradlew"
        gradlew.write_text(FAKE_GRADLEW)
        gradlew.chmod(0o755)
        (source / "connected-device-minimum.txt").write_text("2\n")
        (source / "connected-device-exclude.txt").write_text("# Play Protect blocks installs\nSM-G930F\n")
        for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
            subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=ci@example.invalid", *args],
                           cwd=source, check=True, capture_output=True)

    def devices(self, physical, models=()):
        (self.state / "physical").write_text("".join(f"{serial}\n" for serial in physical))
        (self.state / "models").write_text("".join(f"{serial} {model}\n" for serial, model in models))

    def run_script(self, **overrides):
        env = {k: v for k, v in os.environ.items() if k not in ("ANDROID_SERIAL",)}
        env.update({
            "FAKE_STATE": str(self.state), "PROJECT_WORKDIR": ".", "BUILD_NUMBER": "7",
            "ANDROID_CACHE_ROOT": str(self.root / "android-cache"),
            "FLUTTER_CACHE_ROOT": str(self.root / "flutter-cache"), "FLUTTER_VERSION": "3.47.8",
            "CONNECTED_KVM_DEVICE": str(self.kvm),
        })
        env.update(overrides)
        return subprocess.run(["bash"], input=SCRIPT.encode(), cwd=self.workspace, env=env, capture_output=True, timeout=120)

    def lines(self, name):
        path = self.state / name
        return path.read_text().split() if path.exists() else []

    def leftovers(self):
        return sorted(p.name for p in self.workspace.iterdir() if p.name.startswith("connected-"))

    def test_enough_physical_devices_use_no_emulator_and_skip_foreign_ones(self):
        self.devices(["PHONE1", "PHONE2", "OLDPHONE", "emulator-5564"],
                     [("PHONE1", "SM-G9910"), ("PHONE2", "SM-S9110"), ("OLDPHONE", "SM-G930F")])
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        out = result.stdout.decode()
        self.assertIn("Ignoring emulator-5564", out)
        self.assertIn("Skipping excluded Android device OLDPHONE (SM-G930F)", out)
        self.assertEqual(self.lines("flutter_runs"), ["PHONE1", "PHONE2"])
        self.assertEqual(self.lines("gradle_serial"), ["PHONE1,PHONE2"])
        self.assertEqual(self.lines("started"), [])
        self.assertEqual(self.leftovers(), [])

    def test_missing_physical_devices_are_replaced_by_disposable_emulators(self):
        for physical, started in ((["PHONE1"], 1), ([], 2)):
            with self.subTest(physical=physical):
                for name in ("flutter_runs", "gradle_serial", "started", "killed", "emulators"):
                    (self.state / name).unlink(missing_ok=True)
                self.devices(physical + ["emulator-5564"], [("PHONE1", "SM-G9910")])
                result = self.run_script()
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                emulators = self.lines("started")
                self.assertEqual(len(emulators), started)
                self.assertTrue(all(serial.startswith("emulator-55") and serial != "emulator-5564" for serial in emulators))
                self.assertEqual(self.lines("flutter_runs"), physical + emulators)
                self.assertEqual(self.lines("gradle_serial"), [",".join(physical + emulators)])
                self.assertEqual(sorted(self.lines("killed")), sorted(emulators))
                self.assertEqual(self.leftovers(), [])
                self.assertTrue(self.lines("avd_home")[0].startswith(str(self.workspace / "connected-emulators.")))

    def test_fails_clearly_when_no_emulator_can_make_up_the_difference(self):
        self.devices(["PHONE1"], [("PHONE1", "SM-G9910")])
        result = self.run_script(CONNECTED_KVM_DEVICE=str(self.root / "missing-kvm"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Only 1 of 2 Android devices are usable, and no emulator can be started", result.stderr.decode())
        self.assertEqual(self.lines("flutter_runs"), [])
        self.assertEqual(self.leftovers(), [])

    def test_a_failing_test_still_removes_the_emulators(self):
        self.devices([])
        self.tools["flutter"][0].write_text("#!/bin/bash\n[[ $1 == test ]] && exit 9\nexit 0\n")
        result = self.run_script()
        self.assertEqual(result.returncode, 9)
        self.assertEqual(sorted(self.lines("killed")), sorted(self.lines("started")))
        self.assertEqual(len(self.lines("started")), 2)
        self.assertEqual(self.leftovers(), [])


if __name__ == "__main__":
    unittest.main()
