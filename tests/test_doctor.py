from titan import doctor


def test_shipped_config_has_real_operator_values(cfg):
    assert doctor.check_operator(cfg)[0] == "ok"


def test_placeholders_are_caught(cfg):
    cfg["operator"]["sender_phone"] = doctor.PLACEHOLDER_PHONE
    cfg["outreach"]["example_emails"] = ["Hi Maria, Rodrigo here with Titan Realty"]
    status, detail, fix = doctor.check_operator(cfg)
    assert status == "fail"
    assert "sender_phone" in detail and "example_emails" in detail and "config.yaml" in fix


def test_blank_credentials_are_skips_not_failures(cfg, monkeypatch):
    # offline: a cloud proxy that injects the Apify credential would otherwise make this "ok"
    from titan.ingest.apify import ApifyClient
    monkeypatch.setattr(ApifyClient, "auth_ok", lambda self: (False, "no token"))
    assert doctor.check_apify(cfg)[0] == "skip"
    assert doctor.check_anthropic(cfg)[0] == "skip"
    assert doctor.check_gmail(cfg)[0] == "skip"
    assert doctor.check_twilio(cfg)[0] == "skip"
    assert doctor.check_webhook(cfg)[0] == "skip"


def test_draft_mode_is_ok_and_auto_mode_warns(cfg, monkeypatch):
    assert doctor.check_mode(cfg)[0] == "ok"
    monkeypatch.setenv("TITAN_MODE", "FULL_AUTO")
    status, detail, _ = doctor.check_mode(cfg)
    assert status == "warn" and "WILL be sent" in detail


def test_run_isolates_crashing_checks_and_renders(cfg):
    def boom(_cfg):
        raise RuntimeError("kaboom")
    rows = doctor.run(cfg, [("good", lambda c: ("ok", "fine", "")), ("bad", boom)])
    assert [r["status"] for r in rows] == ["ok", "fail"]
    assert "kaboom" in rows[1]["detail"]
    text = doctor.render(rows)
    assert "✓ good" in text and "✗ bad" in text and "1 ok · 0 warn · 1 fail" in text
    # secrets never appear in the output
    assert "sk-" not in text and "apify_api" not in text


def test_doctor_flags_missing_cron_token_and_asana(cfg, monkeypatch):
    from titan.doctor import check_asana, check_claude_code_cron
    monkeypatch.setitem(cfg.secrets, "CLAUDE_CODE_OAUTH_TOKEN", "")
    st, detail, fix = check_claude_code_cron(cfg)
    assert st in ("warn", "skip") and ("claude-login" in fix or st == "skip")
    monkeypatch.setitem(cfg.secrets, "CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-xyz")
    assert check_claude_code_cron(cfg)[0] in ("ok", "skip")
    monkeypatch.setitem(cfg.secrets, "ASANA_TOKEN", "")
    assert check_asana(cfg)[0] in ("fail", "skip")
