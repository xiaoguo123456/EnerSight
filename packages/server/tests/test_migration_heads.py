"""验证真实迁移链，防止 create_all 测试掩盖合并后的多 head。"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("initial", [None, "c291b8d4f230", "c4f1a2b7d9e0", "c5a092e1d830"])
def test_空库及旧分支可升级到唯一头(tmp_path, initial):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1
    database = tmp_path / "migration.sqlite"
    env = {**os.environ, "ENERSIGHT_DATABASE_URL": f"sqlite+aiosqlite:///{database}"}

    def upgrade(target):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", target],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    if initial:
        upgrade(initial)
    upgrade("head")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchall() == [(heads[0],)]
        assert {"last_detected_at", "clear_since", "last_clear_check_at"} <= {
            row[1] for row in db.execute("PRAGMA table_info(alerts)")
        }
        assert "provenance" in {row[1] for row in db.execute("PRAGMA table_info(catalog_plants)")}
