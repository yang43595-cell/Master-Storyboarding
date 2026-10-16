"""Compare all restored source files to the exact v78 manifest before applying patches."""
import hashlib
import json
from pathlib import Path
import sys

parent = Path(sys.argv[1]).resolve()  # Directory containing site-source/.
manifest = Path(__file__).resolve().parents[1] / "v78-source-manifest.json"
files = json.loads(manifest.read_text())["sourceFiles"]
failed = []
for item in files:
    path = parent / item["path"]
    if not path.is_file():
        failed.append(item["path"])
        continue
    raw = path.read_bytes()
    if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
        failed.append(item["path"])
print(json.dumps({"files": len(files), "mismatches": failed}))
sys.exit(bool(failed))
