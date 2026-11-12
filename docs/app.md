# Desktop app

`pysrim2 app` opens a window for pysrim2. In 0.12.0 it guides you through setting up
SRIM: it shows how SRIM runs on your machine, installs what is still missing, and checks
the result. Editing a calculation, starting runs and reading results follow later in the
0.12 series; until then their tabs only say so.

The app is optional. The library (`import srim`) and the command line never import Qt, so
they work without it.

## Install

The app needs the `app` extra (PySide6, the Qt bindings, and matplotlib). PySide6 is a download of over 100 MB (about 340 MB installed), so it is an extra rather than part of the default install, and the container image stays small. Install it into the pysrim2 you already use:

```shell
pysrim2 install app
```

`pysrim2 install app` runs the install that matches how pysrim2 was installed. A
`uv tool` install is reinstalled with the extras it has plus `app`; a pip install gets
`pysrim2[app]` at the same version. You can also install the extra directly:

```shell
uv tool install "pysrim2[app]"
pip install "pysrim2[app]"
```

In a source checkout (an editable install) the command does not run anything; it prints
`uv sync --extra app` for you to run in the checkout.

The app needs Python 3.14 or older for now: PySide6 does not support Python 3.15 yet. On
Python 3.15 the `app` extra installs nothing, and `pysrim2 install app` and `pysrim2 app`
say so. Use Python 3.14 for the app, for example `uv tool install --python 3.14 "pysrim2[app]"`.

Installing the app needs no administrator rights.

## Start

```shell
pysrim2 app
```

If the app is not installed, the command says so and asks you to run `pysrim2 install app`.

## Setup

The setup opens by itself on the first check if no route is chosen yet or a check is not
ready. A route saved on another machine, or one this platform does not offer, counts as no
route until you choose one; the app does not change the settings file at startup. You can
reopen it at any time from the **Setup** menu (**Run setup…**), or by
clicking the setup link in the status bar.

The setup has four pages: **Welcome to pysrim2**, **Choose how SRIM runs**, **Install what
is missing** and **Check the installation**.

SRIM runs in one of three routes:

| Route | What it does | Offered on |
|---|---|---|
| Native TRIM.exe | Runs SRIM's own `TRIM.exe` (Windows only) | Windows |
| Wine (pysrim2's own wine prefix) | Runs `TRIM.exe` under wine, in pysrim2's private prefix | Linux only |
| Container (Podman or Docker) | Runs SRIM inside the pysrim2 container image | Every platform |

The route that is recommended on your machine is marked "(recommended)" and selected
first. A route you chose before is preselected instead. On macOS only the container route is
offered, with a note that native SRIM is not supported there.

On the install page each step is listed as installed, to install, or needs admin rights.
**Install** runs the steps that need no administrator rights. **Check again** plans the
steps once more and checks them again.

The app never runs a command that needs administrator rights (for example the package
installs or `regsvr32` on Windows). It shows those commands in a box instead: use
**Copy commands**, run them in a terminal, then press **Check again**. The install and the
check are the same as in the command line (see {doc}`cli`), so `pysrim2 install` in a
terminal can also finish what the app left.

**Finish** saves the route. **Cancel** changes nothing. Back, Cancel and closing the
wizard are unavailable while an install runs.

## Status bar

The status bar at the bottom of the window says what to do next, one line at a time. It lists,
in this order:

- *Choose how SRIM runs: open the setup* until a route is chosen, and a line for each check
  that is not ready, in the form `<check>: <detail>. Fix: <what to run>`.
- What the run setup still needs (for example *Choose the ion: pick an element on the Beam
  tab*), then the problems the library finds in it. A note that does not stop a run comes
  last.
- *The run setup is complete* when nothing is listed.

When there are more lines, the bar says how many more. A line with a link opens the setup, or
the tab and field it names, when you click it.

## Open and save a setup

The window edits one run setup at a time, kept in a `run.yaml` file (the same file the library
reads with `RunSpec.from_yaml`):

- **File > New setup** (Ctrl+N) starts an empty setup.
- **File > Open setup…** (Ctrl+O) reads a `run.yaml`.
- **File > Save setup** (Ctrl+S) writes the setup to the file it came from; a new setup is saved
  with **Save setup as…** (Ctrl+Shift+S), which asks for a file.

Saving refuses an incomplete setup and says what is missing first. A histogram file that the
setup uses from outside its own folder is copied to a `beam/` folder next to the saved file,
and the file refers to the copy, so the setup still opens when its folder is moved. A file that
cannot be read or written gives a message, and the setup stays as it was.

**Edit > Undo** and **Redo** (Ctrl+Z, Ctrl+Shift+Z on Windows and Linux) go back and forward
through the edits since the setup was opened or created; opening another file starts a new
history, so Undo never returns to the setup that was open before.

New, Open and Quit ask **Discard the changes to this setup?** when the setup has edits that are
not saved. Choosing No keeps the setup and the window open.

## Themes

**View > Theme** chooses **System** (the default: follow the operating system), **Light** or
**Dark**. The choice is saved and applies at once.

## Menus and tabs

- **File > New setup**, **Open setup…**, **Save setup**, **Save setup as…** and **Quit** (Ctrl+Q)
- **Edit > Undo** and **Redo**
- **Setup > Run setup…**
- **View > Theme > System, Light, Dark**
- **Help > Documentation** (opens this documentation) and **About pysrim2**

The window has five tabs: **Geometry**, **Beam**, **Run settings**, **Progress** and
**Results**. In 0.12.0 each of them only says that it arrives in a later release.

## Settings file

The app's choices are stored in `<data dir>/app/settings.json`, where `<data dir>` is the
pysrim2 data directory (see {doc}`installation`). It holds the `route` (`native`, `wine`,
`container`, or `null` before a choice) and the `theme` (`system`, `light` or `dark`). A
missing or broken file gives the defaults, so the app always starts. You can delete the file
to start from the defaults again.
