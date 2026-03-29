"""
Smoke test configuration.
Skip all smoke tests unless --smoke flag is passed or OPENHIS_SMOKE=1 is set.
"""
import os
import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--smoke", action="store_true", default=False, help="Run smoke tests against a live stack")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "smoke: marks tests as smoke tests (require live Docker stack)")


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    if not config.getoption("--smoke") and not os.getenv("OPENHIS_SMOKE"):
        skip = pytest.mark.skip(reason="smoke tests require --smoke flag or OPENHIS_SMOKE=1")
        for item in items:
            if "smoke" in item.keywords:
                item.add_marker(skip)
