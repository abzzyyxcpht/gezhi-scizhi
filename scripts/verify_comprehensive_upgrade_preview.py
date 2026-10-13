from pathlib import Path
from verify_agent_polish_preview import main


if __name__ == "__main__":
    main(Path(__file__).resolve().parents[1] / "design-previews/2026-10-03-comprehensive-upgrade",
         minimum_frames=10, minimum_images=8)
