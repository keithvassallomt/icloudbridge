"""The passwords CSV import routes must hand the engine a Path."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from icloudbridge.api.routes import passwords as passwords_routes


@pytest.fixture
def config(tmp_path):
    return SimpleNamespace(general=SimpleNamespace(data_dir=tmp_path))


@pytest.mark.parametrize("kind", ["apple", "bitwarden"])
async def test_csv_import_routes_pass_a_path(config, kind):
    # Both routes passed the temp file's name as a str; parsing it failed
    # with "'str' object has no attribute 'exists'".
    seen = []

    async def importer(csv_path):
        seen.append(csv_path)
        return {"new": 0}

    engine = SimpleNamespace(**{f"import_{kind}_csv": importer})
    upload = SimpleNamespace(read=_async_value(b"Title,URL,Username,Password\n"))
    route = getattr(passwords_routes, f"import_{kind}_csv")

    await route(file=upload, engine=engine, config=config)

    [csv_path] = seen
    assert hasattr(csv_path, "exists")


def _async_value(value):
    async def read():
        return value

    return read
