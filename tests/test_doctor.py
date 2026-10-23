from titan import doctor


def test_shipped_config_has_real_operator_values(cfg):
    assert doctor.check_operator(cfg)[0] == "ok"


def test_placeholders_are_caught(cfg):
    cfg["operator"]["sender_phone"] = doctor.PLACEHOLDER_PHONE
    cfg["outreach"]["example_emails"] = ["Hi Maria, Rodrigo here with Titan Realty"]
    status, detail, fix = doctor.check_operator(cfg)
    assert status == "fail"
    assert "sender_phone" in detail and "example_emails" in detail and "config.yaml" in fix


def test_blank_credentials_are_skips_not_failures(cfg):
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
