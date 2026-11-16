"""PyInstaller entry point (kept outside the package for clean imports)."""
import os
import sys

if os.environ.get("MURMUR_ENGINE_ONLY") == "1" or "--engine" in sys.argv:
    from murmur.engine_api import main as engine_main

    raise SystemExit(engine_main())

from murmur.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
