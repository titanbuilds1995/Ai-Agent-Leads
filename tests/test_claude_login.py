"""Cron's Claude login (Rodrigo, 2026-10-26: 'this needs to be a root fix'): the failure names the cause and the fix,
one alarm card per day lands in Needs Rodrigo, and `claude-login` saves the token into .env cleanly."""
from titan.cli import save_env_value
from titan.health import alert_claude_login
from titan.llm import login_problem


class FakeAsana:
    def __init__(self):
        self.created = []

    def create_task(self, project, section, name, notes):
        self.created.append((project, section, name, notes))
        return {"gid": "1"}


def test_login_problem_names_missing_token(cfg, monkeypatch):
    monkeypatch.setitem(cfg.secrets, "CLAUDE_CODE_OAUTH_TOKEN", "")
    msg = login_problem(cfg, "Not logged in · Please run /login")
    assert "not saved in .env" in msg and "make claude-login" in msg
    monkeypatch.setitem(cfg.secrets, "CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-x")
    assert "even with the token" in login_problem(cfg, "Not logged in · Please run /login")
    assert "refused" in login_problem(cfg, "Invalid API key · Fix external API key")
    assert login_problem(cfg, "no structured output") is None and login_problem(cfg, None) is None


def test_alarm_card_once_per_day(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "root", tmp_path)
    a = FakeAsana()
    assert alert_claude_login(cfg, a, "token missing") is True
    assert alert_claude_login(cfg, a, "token missing") is False
    assert len(a.created) == 1
    project, section, name, notes = a.created[0]
    assert project == "1219837397052431" and section == "1219837590904714" and "make claude-login" in notes


def test_save_env_value_replaces_old_line(tmp_path):
    env = tmp_path / ".env"
    env.write_text("APIFY_TOKEN=a\nCLAUDE_CODE_OAUTH_TOKEN=old\nexport CLAUDE_CODE_OAUTH_TOKEN=older\nASANA_TOKEN=b\n")
    save_env_value(env, "CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-new")
    lines = env.read_text().splitlines()
    assert lines == ["APIFY_TOKEN=a", "ASANA_TOKEN=b", "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-new"]
