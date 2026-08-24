import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DEMO = ROOT / "docs" / "demo"

names = sys.argv[1:] or ["v1-neural-baseline", "v2-generative", "v3-longform"]
shutil.rmtree(DEMO, ignore_errors=True)
DEMO.mkdir(parents=True)
for name in names:
    timeline = json.loads((ROOT / "build" / name / "timeline.json").read_text())
    shutil.copy(ROOT / "build" / name / "episode.mp3", DEMO / (name + ".mp3"))
    (DEMO / (name + ".json")).write_text(json.dumps({
        "variant": name,
        "engine": timeline["settings"]["engine"],
        "voices": timeline["settings"]["voices"],
        "loudness": timeline["settings"]["loudness"],
        "total_seconds": timeline["total_seconds"],
        "turns": [[t["start_seconds"], t["seconds"]] for t in timeline["turns"]],
    }))
(DEMO / "index.json").write_text(json.dumps(names))
print("-> %s" % DEMO.relative_to(ROOT))
