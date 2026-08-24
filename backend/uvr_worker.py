from __future__ import annotations

import json
import sys
from pathlib import Path

from .saxscribe.pipeline import _separate_wind_in_process


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit("Usage: python -m backend.uvr_worker SOURCE OUTPUT_DIR DISPLAY_NAME RESULT_JSON")
    source = Path(sys.argv[1]).resolve()
    output_dir = Path(sys.argv[2]).resolve()
    display_name = sys.argv[3]
    result_path = Path(sys.argv[4]).resolve()
    target = _separate_wind_in_process(
        source,
        output_dir,
        source_display_name=display_name,
    )
    result_path.write_text(json.dumps({"path": str(target.resolve())}), encoding="utf-8")


if __name__ == "__main__":
    main()
