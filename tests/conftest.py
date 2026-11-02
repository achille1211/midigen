import pytest
import midigen as mg


@pytest.fixture
def pop():
    return mg.build_spec("pop", key="C", mode="major", bars=16, seed=3)


@pytest.fixture(autouse=True)
def _isolated_learning_store(tmp_path, monkeypatch):
    """Every test gets its own empty learning store, so learned weights never leak into tests."""
    monkeypatch.setenv("MIDIGEN_DATA_DIR", str(tmp_path / "midigen-data"))
    monkeypatch.delenv("MIDIGEN_LEARN", raising=False)
    monkeypatch.delenv("MIDIGEN_NO_RECORD", raising=False)
    from midigen import learn
    learn._LAST.clear()
    yield
