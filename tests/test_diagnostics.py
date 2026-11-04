from fb_scraper import diagnostics


def test_new_debug_dir_names_by_time_and_label(debug_dir):
    path = diagnostics.new_debug_dir("Open the location dialog (tried twice)")
    assert path.parent == debug_dir and path.is_dir()
    assert path.name.endswith("_open-the-location-dialog-tried-twice")
    assert diagnostics.new_debug_dir("Open the location dialog (tried twice)") != path, "same second, new folder"


def test_new_debug_dir_keeps_only_the_newest(debug_dir, monkeypatch):
    monkeypatch.setattr(diagnostics, "KEEP_DEBUG_DIRS", 3)
    debug_dir.mkdir()
    for name in ("2026-01-01_00-00-00_a", "2026-01-02_00-00-00_b", "2026-01-03_00-00-00_c"):
        (debug_dir / name).mkdir()
    newest = diagnostics.new_debug_dir("d")
    assert sorted(p.name for p in debug_dir.iterdir()) == [
        "2026-01-02_00-00-00_b",
        "2026-01-03_00-00-00_c",
        newest.name,
    ]


def test_save_page_snapshot(mock_page, debug_dir):
    mock_page.set_content(
        '<div role="dialog" aria-label="Change location"><input></div>'
        '<div role="dialog"><p>Log in</p><p></p><p>to continue</p></div>'
        '<div role="dialog" hidden>Hidden one</div>'
    )
    path = diagnostics.save_page_snapshot(mock_page, "test step")
    assert {p.name for p in path.iterdir()} == {"page.png", "page.html", "page.txt"}
    assert "Change location" in (path / "page.html").read_text()
    summary = (path / "page.txt").read_text()
    assert "url: about:blank" in summary
    assert "  - Change location\n  - Log in / to continue\n" in summary
    assert "Hidden one" not in summary


def test_save_page_snapshot_survives_a_closed_page(mock_context_factory, debug_dir):
    page = mock_context_factory().new_page()
    page.close()
    path = diagnostics.save_page_snapshot(page, "closed")
    assert path is not None and (path / "page.txt").exists()


def test_attach_and_read_debug_dir(tmp_path):
    exc = RuntimeError("x")
    assert diagnostics.debug_dir_of(exc) is None
    diagnostics.attach_debug_dir(exc, tmp_path)
    assert diagnostics.debug_dir_of(exc) == tmp_path
    diagnostics.attach_debug_dir(exc, None)
    assert diagnostics.debug_dir_of(exc) == tmp_path
