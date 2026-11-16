from srim.mcp.approval import ApprovalCodes, action_hash


def test_a_code_works_once_for_its_action():
    codes = ApprovalCodes()
    action = action_hash("start_run", {"workers": 2}, {"ion": {"symbol": "N"}}, 2)
    code = codes.issue(action)
    assert codes.redeem(code, action) is True
    assert codes.redeem(code, action) is False


def test_a_code_does_not_match_other_arguments_or_a_changed_setup():
    codes = ApprovalCodes()
    a = action_hash("start_run", {"workers": 2}, {"n": 1}, 2)
    code = codes.issue(a)
    assert codes.redeem(code, action_hash("start_run", {"workers": 3}, {"n": 1}, 3)) is False
    assert codes.redeem(code, action_hash("start_run", {"workers": 2}, {"n": 2}, 2)) is False
    assert codes.redeem(code, a) is True


def test_a_code_expires():
    now = [0.0]
    codes = ApprovalCodes(ttl=300, clock=lambda: now[0])
    a = action_hash("delete_run", {"folder": "x"}, None, None)
    code = codes.issue(a)
    now[0] = 301
    assert codes.redeem(code, a) is False


def test_the_code_itself_is_not_part_of_the_action():
    assert action_hash("t", {"x": 1, "confirm_code": "ABC"}, None, None) == action_hash(
        "t", {"x": 1}, None, None
    )


def test_a_code_is_six_unambiguous_characters_and_unique():
    codes = ApprovalCodes()
    action = action_hash("t", {}, None, None)
    issued = [codes.issue(action) for _ in range(200)]
    assert len(set(issued)) == 200
    for code in issued:
        assert len(code) == 6
        assert not set(code) & set("01IOLS5")  # no look-alike characters


def test_the_code_is_not_case_sensitive_and_ignores_spaces():
    codes = ApprovalCodes()
    action = action_hash("t", {}, None, None)
    code = codes.issue(action)
    assert codes.redeem(f" {code.lower()} ", action) is True


def test_an_unknown_code_is_refused():
    codes = ApprovalCodes()
    assert codes.redeem("ZZZZZZ", action_hash("t", {}, None, None)) is False


def test_the_hash_is_stable_and_independent_of_key_order():
    a = action_hash("t", {"a": 1, "b": [1, 2]}, {"x": 1, "y": 2}, 3)
    b = action_hash("t", {"b": [1, 2], "a": 1}, {"y": 2, "x": 1}, 3)
    assert a == b
    assert len(a) == 64  # SHA-256 in hex
