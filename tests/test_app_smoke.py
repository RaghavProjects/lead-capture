def test_app_runs_all_tabs_without_exception():
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    app_path = Path(__file__).resolve().parent.parent / "app.py"
    app = AppTest.from_file(str(app_path))
    app.run(timeout=30)

    assert not app.exception, [str(e) for e in app.exception]
    # App rendered; dashboard filter widgets exist.
    assert app.title[0].value == "AI Lead Follow-Up Assistant"
    labels = [s.label for s in app.selectbox]
    assert "Stage" in labels
    assert "Priority" in labels
