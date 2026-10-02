"""Streamlit AppTest 로 실제 화면 스크립트를 실행해 본다."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from lineage.extract import main

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    main(["--ddl-dir", str(ROOT / "ddl"), "--db", str(db_path)])
    monkeypatch.setenv("LINEAGE_DB", str(db_path))
    monkeypatch.chdir(ROOT)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    return at


def test_app_renders_and_switches_selection(app):
    app.run()
    assert not app.exception
    assert [m.value for m in app.metric] == ["17", "20", "8"]

    app.selectbox[0].select("dw.fact_sales").run()
    assert not app.exception
    text = " ".join(m.value for m in app.markdown)
    assert "stg.orders" in text and "mart.v_daily_sales" in text  # 상위·하위 모두
    assert "usp_load_fact_sales - SP" in " ".join(c.value for c in app.caption)

    app.radio[0].set_value("downstream").run()
    text = " ".join(m.value for m in app.markdown)
    assert "stg.orders" not in text and "mart.v_top_categories" in text


def test_app_without_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAGE_DB", str(tmp_path / "missing.db"))
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    at.run()
    assert "메타 DB" in at.error[0].value
