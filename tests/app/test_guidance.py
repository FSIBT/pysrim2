from srim.app.guidance import Guidance, setup_guidance


def test_guidance_lists_failing_checks_in_order():
    checks = [
        {"name": "srim-dir", "ok": False, "detail": "/x", "fix": "run: pysrim2 install srim"},
        {"name": "python", "ok": True, "detail": "3.13", "fix": ""},
        {"name": "trim-exe", "ok": False, "detail": "no", "fix": "run: pysrim2 install srim"},
    ]
    assert setup_guidance(checks, "wine") == [
        Guidance("srim-dir: /x. Fix: run: pysrim2 install srim", "setup"),
        Guidance("trim-exe: no. Fix: run: pysrim2 install srim", "setup"),
    ]


def test_all_ok_gives_no_guidance():
    checks = [{"name": "python", "ok": True, "detail": "3.13", "fix": ""}]
    assert setup_guidance(checks, "native") == []


def test_no_route_asks_to_choose_one():
    guidance = setup_guidance([], None)
    assert guidance == [Guidance("Choose how SRIM runs: open the setup", "setup")]
    assert guidance[0].target == "setup"
