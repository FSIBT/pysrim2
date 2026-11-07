# Documentation gallery

The pictures in the documentation gallery are built by `make.py` from small scripts
in this folder. The committed images are in `images/`.

## Entries

Every `*.py` file here, except `make.py` and names that start with `_`, is an entry.
An entry module defines:

- `TITLE`: one line, shown by `--list`
- `NEEDS_SRIM`: `True` if the entry runs SRIM (it is skipped without SRIM)
- `OUTPUT`: the file name it writes, `.png` or `.svg` (for example `"heatmap.png"`)
- `make(outdir)`: writes `outdir / OUTPUT` and returns that path. It writes nothing
  else into `outdir`; temporary SRIM runs go in a temporary directory.

The entry scripts are shown to users as example code, so they are kept short.

## Rebuilding the pictures

Run from the repository root:

```bash
uv run --extra plot --extra tui python docs/gallery/make.py          # all entries
uv run --extra plot --extra tui python docs/gallery/make.py --list   # list the entries
uv run --extra plot --extra tui python docs/gallery/make.py heatmap  # one or more names
```

Entries that need SRIM are skipped with a message when SRIM is not installed. Install
it with `pysrim2 install` (on Linux, under wine) first. `--out DIR` writes the pictures
somewhere else than `images/`.

Each committed image must stay under 300 KB. After rebuilding, look at the changed
images and commit them with the change that caused them.
