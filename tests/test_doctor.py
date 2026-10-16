from server import doctor
from server.doctor import FAIL, OK, frontend_problem, print_report, run_checks


def test_frontend_problem(settings, tmp_path):
    assert "npm run build" in frontend_problem(settings)
    (settings.web_dist / "host").mkdir(parents=True)
    (settings.web_dist / "host" / "index.html").write_text("<html>")
    assert frontend_problem(settings) is None


def test_checks_run_and_report(settings, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "lan_ip", lambda: "192.168.1.50")
    monkeypatch.setattr(doctor, "_windows_network_categories", lambda: ["Private"])
    monkeypatch.setattr(doctor, "_python_firewall_profiles", lambda: {"Private"})
    settings.port = 0  # any free port
    checks = {c.title: c for c in run_checks(settings)}
    assert checks["Página web"].level == FAIL
    assert any(c.level == OK and "192.168.1.50" in c.detail for c in checks.values())
    assert print_report(settings) == 1
    assert "problema(s)" in capsys.readouterr().out
