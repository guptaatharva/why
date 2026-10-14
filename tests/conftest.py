import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "demo" / "packages" / "dist"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Tests never call a model or Tavily."""
    monkeypatch.setenv("WHY_OFFLINE", "1")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)


@pytest.fixture(scope="session", autouse=True)
def chronokit_installed():
    """The demo repo depends on chronokit 1.4.0 (fictional, built from demo/packages)."""
    try:
        import importlib.metadata as m

        if m.version("chronokit") == "1.4.0":
            return
    except Exception:  # noqa: BLE001
        pass
    if not DIST.exists() or not list(DIST.glob("chronokit-1.4.0*.whl")):
        for v in ("1.4.0", "2.0.0"):
            subprocess.run(["uv", "build", "-q", "--wheel", "--out-dir", str(DIST),
                            str(ROOT / "demo" / "packages" / f"chronokit-{v}")], check=True)
    subprocess.run(["uv", "pip", "install", "-q", "--python", sys.executable, "--find-links", str(DIST),
                    "chronokit==1.4.0"], check=True)


@pytest.fixture
def demo_repo(tmp_path) -> Path:
    dest = tmp_path / "larkspur"
    subprocess.run([sys.executable, str(ROOT / "demo" / "build_demo.py"), str(dest)], check=True,
                   capture_output=True, env={**os.environ})
    return dest


@pytest.fixture
def store(demo_repo):
    from why.store import Store

    st = Store(demo_repo)
    st.init()
    return st
