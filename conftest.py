"""Public version only: skip tests that exercise withheld modules through other files.

Whole test files of withheld modules are not published; these are single tests
living in otherwise-public files (see README, "What is not published").
"""
import pytest

WITHHELD_TESTS = {
    "tests/test_kaspi_wrapper_archive_sync.py::test_radar_skips_archived_products",
    "tests/test_thread_pool_isolation.py::RadarUsesItsOwnPool",
}


def pytest_collection_modifyitems(config, items):
    skip = pytest.mark.skip(reason="exercises a module withheld from the public version")
    for item in items:
        nodeid = item.nodeid.replace("\\", "/")
        if any(nodeid == t or nodeid.startswith(t + "::") for t in WITHHELD_TESTS):
            item.add_marker(skip)
