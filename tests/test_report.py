from goblinmode import report


def test_build_report_shape(monkeypatch):
    monkeypatch.setattr(report, "latest_log_files", lambda limit=1: [])
    rep = report.build_report(game="Wow.exe", user_note="stutters in cities")
    assert rep["schema"] == "gmp.report.v1"
    assert rep["game"] == "Wow.exe"
    assert rep["user_note"] == "stutters in cities"
    assert "preflight_summary" in rep and "system" in rep


def test_markdown_and_prompt_render(monkeypatch):
    monkeypatch.setattr(report, "latest_log_files", lambda limit=1: [])
    rep = report.build_report(game="Wow.exe")
    md = report.as_markdown(rep)
    assert "## Goblin Mode Pro report" in md
    assert "### Pre-flight" in md
    prompt = report.as_llm_prompt(rep)
    assert "```json" in prompt and "gmp.report.v1" in prompt


def test_report_includes_log_findings(monkeypatch, tmp_path):
    logf = tmp_path / "WoW-x.log"
    logf.write_text("VK_ERROR_DEVICE_LOST\nesync: up to 512 handles\n")
    monkeypatch.setattr(report, "latest_log_files", lambda limit=1: [logf])
    rep = report.build_report(game="Wow.exe")
    ids = {f["rule_id"] for f in rep["log_findings"]}
    assert "device_lost" in ids and "esync_fd" in ids
    assert "GPU device lost" in report.as_markdown(rep)


def test_github_url_is_prefilled(monkeypatch):
    monkeypatch.setattr(report, "latest_log_files", lambda limit=1: [])
    rep = report.build_report(game="Wow.exe")
    url = report.github_issue_url(rep, repo="acme/gmp")
    assert url.startswith("https://github.com/acme/gmp/issues/new?")
    assert "title=" in url and "body=" in url
