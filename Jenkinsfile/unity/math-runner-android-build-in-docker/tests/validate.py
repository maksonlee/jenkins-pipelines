import pathlib
import re
import subprocess
import sys

path = pathlib.Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
blocks = re.findall(r"sh '''(.*?)'''", source, re.S)
if len(blocks) != 8:
    raise SystemExit(f"Expected 8 shell blocks, got {len(blocks)}")
compiled = 0
for index, block in enumerate(blocks, 1):
    result = subprocess.run(["bash", "-n"], input=block.encode(), capture_output=True)
    if result.returncode:
        raise SystemExit(f"Shell block {index}: {result.stderr.decode(errors='replace')}")
    for code in re.findall(r"<<'?PY'?\n(.*?)\nPY", block, re.S):
        compile(code, f"shell-{index}-python", "exec")
        compiled += 1
if compiled != 5:
    raise SystemExit(f"Expected 5 Python blocks, got {compiled}")
print(f"Embedded Bash: {len(blocks)}/{len(blocks)}; Python compile: {compiled}/{compiled}")
if len(sys.argv) > 2:
    bootstrap = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
    result = subprocess.run(["bash", "-n"], input=bootstrap.encode(), capture_output=True)
    if result.returncode:
        raise SystemExit(f"Bootstrap Bash: {result.stderr.decode(errors='replace')}")
    scripts = re.findall(r"<<'PY'\n(.*?)\nPY", bootstrap, re.S)
    if len(scripts) != 4:
        raise SystemExit(f"Expected 4 bootstrap Python blocks, got {len(scripts)}")
    for index, code in enumerate(scripts, 1):
        compile(code, f"bootstrap-python-{index}", "exec")
    print(f"Bootstrap Bash: 1/1; embedded Python compile: {len(scripts)}/{len(scripts)}")
    root = path.parent
    paths = [root / name for name in
             ("Jenkinsfile", "README.md", "bootstrap-unity-personal.sh",
              "job-config.disabled.xml")]
    paths += sorted((root / "tests").glob("*.py"))
    for candidate in paths:
        data = candidate.read_bytes()
        if (not data.endswith(b"\n") or b"\r" in data or
                any(line.rstrip(b" \t") != line for line in data.splitlines())):
            raise SystemExit(f"Candidate format failed: {candidate}")
    print(f"Candidate LF/newline/trailing whitespace: {len(paths)}/{len(paths)}")
