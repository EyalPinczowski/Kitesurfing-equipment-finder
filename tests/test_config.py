from kitefinder.config import PROJECT_ROOT, load_settings, parse_env_file


def test_parse_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text(
        "# comment\n"
        "GEMINI_API_KEY=abc123\n"
        "export TELEGRAM_BOT_TOKEN='tok:en'\n"
        'TELEGRAM_CHAT_ID="42"\n'
        "garbage line\n"
        "\n"
        "EMPTY=\n",
        encoding="utf-8",
    )
    assert parse_env_file(f) == {
        "GEMINI_API_KEY": "abc123",
        "TELEGRAM_BOT_TOKEN": "tok:en",
        "TELEGRAM_CHAT_ID": "42",
        "EMPTY": "",
    }


def test_missing_env_file(tmp_path):
    assert parse_env_file(tmp_path / "nope") == {}


def test_load_settings_env_overrides_file(tmp_path):
    (tmp_path / ".env").write_text("GEMINI_API_KEY=file\nFB_COOKIES_PATH=secrets/c.json\n")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sources.yaml").write_text("facebook:\n  groups: [g1]\n")
    s = load_settings(tmp_path, env={"GEMINI_API_KEY": "env"})
    assert s.gemini_api_key == "env"
    assert s.fb_cookies_path == tmp_path / "secrets" / "c.json"
    assert s.data_dir == tmp_path / "data"
    assert s.db_path == tmp_path / "data" / "kitefinder.db"
    assert s.media_dir == tmp_path / "data" / "media"
    assert s.sources["facebook"]["groups"] == ["g1"]


def test_load_settings_defaults_without_files(tmp_path):
    s = load_settings(tmp_path, env={"KITEFINDER_DATA_DIR": str(tmp_path / "abs")})
    assert s.data_dir == tmp_path / "abs"
    assert s.fb_cookies_path is None
    assert s.sources == {}
    assert s.telegram_bot_token == ""


def test_repo_sources_yaml_is_valid():
    s = load_settings(PROJECT_ROOT, env={})
    assert s.sources["location"]["country"] == "IL"
    assert "קייט" in s.sources["facebook"]["marketplace_queries"]
    assert s.sources["schedule"]["lookback_days"] > 0
