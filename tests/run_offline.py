"""Run the offline tests without pytest: python tests/run_offline.py"""
import importlib
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
failed = 0
for mod in ("tests.test_offline", "tests.test_health"):
    m = importlib.import_module(mod)
    for name in sorted(n for n in dir(m) if n.startswith("test_")):
        try:
            getattr(m, name)()
            print(f"ok    {mod}.{name}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {mod}.{name}")
            traceback.print_exc()
print(f"\n{'all passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
