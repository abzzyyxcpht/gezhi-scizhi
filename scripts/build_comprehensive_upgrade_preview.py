"""Reuse the review shell with this upgrade's actual native/Ink evidence."""
from pathlib import Path
from build_agent_polish_preview import main


if __name__ == "__main__":
    main(Path(__file__).resolve().parents[1] / "design-previews/2026-10-03-comprehensive-upgrade",
         groups=("commands", "models", "transitions"), comprehensive=True)
