from __future__ import annotations

from pathlib import Path

import pytest

from engine.shared.account_paths import (
    account_cache_dir,
    account_documents_dir,
    account_graph_dir,
    account_output_dir,
    account_qa_output_path,
    account_root,
    normalize_account_id,
)


def test_normalize_account_id() -> None:
    assert normalize_account_id("admin") == "admin"
    assert normalize_account_id("user_01") == "user_01"
    assert normalize_account_id("team-alpha") == "team-alpha"


def test_normalize_account_id_rejects_empty() -> None:
    with pytest.raises(RuntimeError):
        normalize_account_id("   ")


def test_normalize_account_id_rejects_invalid_format() -> None:
    bad_values = [
        "Admin",      # uppercase
        "a",          # too short
        "-admin",     # invalid start
        "admin/",     # invalid char
        "alice smith" # whitespace
    ]
    for value in bad_values:
        with pytest.raises(RuntimeError):
            normalize_account_id(value)


def test_account_paths_are_scoped() -> None:
    base = Path("/tmp/work")
    root_a = account_root(base_dir=base, account_id="alpha")
    root_b = account_root(base_dir=base, account_id="beta")
    assert root_a != root_b
    docs = account_documents_dir(base_dir=base, account_id="alpha")
    output = account_output_dir(base_dir=base, account_id="alpha")
    cache = account_cache_dir(base_dir=base, account_id="alpha")
    graph = account_graph_dir(base_dir=base, account_id="alpha")
    qa_out = account_qa_output_path(base_dir=base, account_id="alpha")

    assert docs.parts[-3:] == ("accounts", "alpha", "documents")
    assert output.parts[-3:] == ("accounts", "alpha", "output")
    assert cache.parts[-3:] == ("accounts", "alpha", "cache")
    assert graph.parts[-3:] == ("accounts", "alpha", "graph_db")
    assert qa_out.parts[-4:] == ("accounts", "alpha", "output", "output_curriculum_qa.json")
