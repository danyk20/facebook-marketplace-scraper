import pytest
from playwright.sync_api import Error as PlaywrightError

from fb_scraper import __version__, cli
from fb_scraper.browser import LoginFailedError
from fb_scraper.scraper import LoginRequiredError, MarketplaceConsentRequiredError, ScrapeResult


def _fake_result(**overrides):
    defaults = dict(
        query="Tesla Model S",
        country="ch",
        total_elements=1,
        listings=[{"listing_id": "1"}],
        rows=[{"listing_id": "1", "price": "1 CHF"}],
    )
    defaults.update(overrides)
    return ScrapeResult(**defaults)


def test_build_arg_parser_requires_query():
    parser = cli.build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_slug_helper():
    assert cli._slug("Tesla Model S") == "tesla_model_s"
    assert cli._slug("iPhone 15!!") == "iphone_15"
    assert cli._slug("   ") == "listings"


def test_version_flag_prints_version_and_exits_0(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.build_arg_parser().parse_args(["--version"])
    assert exc_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_main_default_output_filename_is_query_slug(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "scrape", lambda *a, **kw: _fake_result())
    rc = cli.main(["--query", "Tesla Model S"])
    assert rc == 0
    assert (tmp_path / "tesla_model_s.csv").exists()
    assert (tmp_path / "tesla_model_s.json").exists()


def test_main_custom_output_filename(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "scrape", lambda *a, **kw: _fake_result())
    cli.main(["--query", "Tesla Model S", "--out", "custom"])
    assert (tmp_path / "custom.csv").exists()
    assert (tmp_path / "custom.json").exists()


def test_main_passes_all_flags_through_to_scrape(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    captured = {}

    def _fake_scrape(query, **kwargs):
        captured["query"] = query
        captured.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(cli, "scrape", _fake_scrape)
    cli.main(
        [
            "--query",
            "Tesla Model S",
            "--country",
            "ch",
            "--no-detail",
            "--all-countries",
            "--headed",
            "--delay",
            "1.5",
            "--price-from",
            "1000",
            "--price-to",
            "2000",
            "--mileage-from",
            "0",
            "--mileage-to",
            "50000",
            "--year-from",
            "2018",
            "--year-to",
            "2020",
            "--condition",
            "new,used_like_new",
        ]
    )

    assert captured["query"] == "Tesla Model S"
    assert captured["country"] == "ch"
    assert captured["detail"] is False
    assert captured["local_only"] is False
    assert captured["headless"] is False
    assert captured["delay"] == 1.5
    assert captured["min_price"] == 1000 and captured["max_price"] == 2000
    assert captured["min_mileage"] == 0 and captured["max_mileage"] == 50000
    assert captured["min_year"] == 2018 and captured["max_year"] == 2020
    assert captured["condition"] == ["new", "used_like_new"]


def test_main_passes_email_and_password_through_to_scrape(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FB_EMAIL", raising=False)
    monkeypatch.delenv("FB_PASSWORD", raising=False)
    captured = {}

    def _fake_scrape(query, **kwargs):
        captured.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(cli, "scrape", _fake_scrape)
    cli.main(["--query", "Tesla", "--email", "test@example.com", "--password", "fake-password-123"])

    assert captured["email"] == "test@example.com"
    assert captured["password"] == "fake-password-123"


def test_main_falls_back_to_env_vars_for_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FB_EMAIL", "env@example.com")
    monkeypatch.setenv("FB_PASSWORD", "env-fake-password")
    captured = {}

    def _fake_scrape(query, **kwargs):
        captured.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(cli, "scrape", _fake_scrape)
    cli.main(["--query", "Tesla"])

    assert captured["email"] == "env@example.com"
    assert captured["password"] == "env-fake-password"


def test_main_explicit_email_flag_overrides_env_var(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FB_EMAIL", "env@example.com")
    captured = {}

    def _fake_scrape(query, **kwargs):
        captured.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(cli, "scrape", _fake_scrape)
    cli.main(["--query", "Tesla", "--email", "flag@example.com"])

    assert captured["email"] == "flag@example.com"


def test_main_password_dash_prompts_via_getpass(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FB_PASSWORD", raising=False)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "typed-fake-password")
    captured = {}

    def _fake_scrape(query, **kwargs):
        captured.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(cli, "scrape", _fake_scrape)
    cli.main(["--query", "Tesla", "--password", "-"])

    assert captured["password"] == "typed-fake-password"


def test_run_cli_login_failed_exits_1(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "scrape",
        lambda *a, **kw: (_ for _ in ()).throw(LoginFailedError("checkpoint hit")),
    )
    rc = cli.run_cli(["--query", "Tesla"])
    assert rc == 1
    assert "checkpoint hit" in capsys.readouterr().err


def test_run_cli_value_error_exits_1(monkeypatch, capsys):
    monkeypatch.setattr(cli, "scrape", lambda *a, **kw: (_ for _ in ()).throw(ValueError("bad range")))
    rc = cli.run_cli(["--query", "Tesla"])
    assert rc == 1
    assert "bad range" in capsys.readouterr().err


def test_run_cli_login_required_exits_1(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "scrape",
        lambda *a, **kw: (_ for _ in ()).throw(LoginRequiredError("please log in")),
    )
    rc = cli.run_cli(["--query", "Tesla"])
    assert rc == 1
    assert "please log in" in capsys.readouterr().err


def test_run_cli_consent_required_exits_1(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "scrape",
        lambda *a, **kw: (_ for _ in ()).throw(MarketplaceConsentRequiredError("accept the consent screen")),
    )
    rc = cli.run_cli(["--query", "Tesla"])
    assert rc == 1
    assert "accept the consent screen" in capsys.readouterr().err


def test_run_cli_playwright_error_exits_1(monkeypatch, capsys):
    monkeypatch.setattr(cli, "scrape", lambda *a, **kw: (_ for _ in ()).throw(PlaywrightError("net down")))
    rc = cli.run_cli(["--query", "Tesla"])
    assert rc == 1
    assert "net down" in capsys.readouterr().err


def test_run_cli_keyboard_interrupt_exits_130(monkeypatch):
    monkeypatch.setattr(cli, "scrape", lambda *a, **kw: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert cli.run_cli(["--query", "Tesla"]) == 130


def test_run_cli_success_returns_0(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "scrape", lambda *a, **kw: _fake_result())
    assert cli.run_cli(["--query", "Tesla"]) == 0
