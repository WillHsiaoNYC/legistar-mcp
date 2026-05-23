from pathlib import Path
import pytest

FIXTURES = Path(__file__).parent / "fixtures"

@pytest.fixture
def bills_dir():
    return FIXTURES / "bills"

@pytest.fixture
def events_dir():
    return FIXTURES / "events"

@pytest.fixture
def people_dir():
    return FIXTURES / "people"

@pytest.fixture
def fixtures_root():
    return FIXTURES
