"""Tests for IndexCheckLevel + IndexOutOfDateChecker."""
import os
import time
import tempfile
from orchard.validation.freshness import (
    IndexCheckLevel, IndexOutOfDateChecker, SymbolLocation,
    freshness_for, GraphFreshness,
)
from orchard.graph.db import get_connection, init_schema


class TestIndexCheckLevel:
    def test_enum_values(self):
        assert IndexCheckLevel.DELETED_FILES.value == "deleted_files"
        assert IndexCheckLevel.MODIFIED_FILES.value == "modified_files"
        assert IndexCheckLevel.IN_MEMORY_MODIFIED_FILES.value == "in_memory_modified_files"

    def test_default_level(self):
        assert IndexCheckLevel.default() == IndexCheckLevel.MODIFIED_FILES


class TestIndexOutOfDateChecker:
    @staticmethod
    def make_temp_file():
        fd, path = tempfile.mkstemp(suffix=".swift")
        os.close(fd)
        return path

    def test_up_to_date_fresh_file(self):
        path = self.make_temp_file()
        try:
            checker = IndexOutOfDateChecker(IndexCheckLevel.MODIFIED_FILES)
            loc = SymbolLocation(path=path, timestamp=time.time() + 3600)
            assert checker.is_up_to_date(loc)
        finally:
            os.unlink(path)

    def test_out_of_date_modified_file(self):
        path = self.make_temp_file()
        try:
            time.sleep(0.01)
            checker = IndexOutOfDateChecker(IndexCheckLevel.MODIFIED_FILES)
            loc = SymbolLocation(path=path, timestamp=time.time() - 3600)
            assert not checker.is_up_to_date(loc)
        finally:
            os.unlink(path)

    def test_deleted_file_not_up_to_date(self):
        checker = IndexOutOfDateChecker(IndexCheckLevel.DELETED_FILES)
        loc = SymbolLocation(path="/nonexistent/path.swift", timestamp=time.time())
        assert not checker.is_up_to_date(loc)

    def test_modtime_cache_reuse(self):
        path = self.make_temp_file()
        try:
            checker = IndexOutOfDateChecker(IndexCheckLevel.MODIFIED_FILES)
            loc = SymbolLocation(path=path, timestamp=time.time() + 3600)
            assert checker.is_up_to_date(loc)
            assert checker.is_up_to_date(loc)  # cache hit
            assert path in checker._mod_time_cache
        finally:
            os.unlink(path)

    def test_deleted_files_level_ignores_mtime(self):
        path = self.make_temp_file()
        try:
            checker = IndexOutOfDateChecker(IndexCheckLevel.DELETED_FILES)
            loc = SymbolLocation(path=path, timestamp=time.time() - 99999)
            assert checker.is_up_to_date(loc)  # file exists → OK
        finally:
            os.unlink(path)


def test_freshness_for_backward_compatible():
    """freshness_for() must still work with existing callers."""
    conn = get_connection(":memory:")
    init_schema(conn)
    status, msg = freshness_for(conn, "", {})
    assert isinstance(status, GraphFreshness)
    assert isinstance(msg, str)
