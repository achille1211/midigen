import pytest
import midigen as mg


@pytest.fixture
def pop():
    return mg.build_spec("pop", key="C", mode="major", bars=16, seed=3)
