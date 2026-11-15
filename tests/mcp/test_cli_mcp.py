from srim import cli


def test_mcp_without_the_sdk_says_how_to_install(monkeypatch, capsys):
    monkeypatch.setattr("srim.mcp.mcp_available", lambda: False)
    assert cli.main(["mcp"]) == 2
    err = capsys.readouterr().err
    assert "pysrim2[mcp]" in err


def test_the_parser_knows_mcp():
    args = cli.build_parser().parse_args(["mcp"])
    assert args.command == "mcp"
