# Desktop app

`pysrim2 app` opens a window for pysrim2. In 0.12.0 it guides you through setting up
SRIM: it shows how SRIM runs on your machine, installs what is still missing, and checks
the result. Editing a calculation, starting runs and reading results follow later in the
0.12 series; until then their tabs only say so.

The app is optional. The library (`import srim`) and the command line never import Qt, so
they work without it.

## Install

The app needs the `app` extra (PySide6, the Qt bindings, and matplotlib). PySide6 is about
100 MB, so it is an extra rather than part of the default install, and the container image
stays small. Install it into the pysrim2 you already use:

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

Installing the app needs no administrator rights.

## Start

```shell
pysrim2 app
```

If the app is not installed, the command says so and asks you to run `pysrim2 install app`.

## Setup

The setup opens by itself the first time the checks find something missing after the app
starts. You can reopen it at any time from the **Setup** menu (**Run setup…**), or by
clicking the setup link in the status bar.

The setup has four pages: **Welcome to pysrim2**, **Choose how SRIM runs**, **Install what
is missing** and **Check the installation**.

SRIM runs in one of three routes:

| Route | What it does | Offered on |
|---|---|---|
| Native TRIM.exe | Runs SRIM's own `TRIM.exe` (Windows only) | Windows |
| Wine (pysrim2's own wine prefix) | Runs `TRIM.exe` under wine, in pysrim2's private prefix | Linux and macOS |
| Container (Podman or Docker) | Runs SRIM inside the pysrim2 container image | Every platform |

The route that is recommended on your machine is marked "(recommended)" and selected
first. A route you chose before is preselected instead.

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

The status bar at the bottom of the window says what to do next, one line at a time:

- *Choose how SRIM runs: open the setup* until a route is chosen.
- A line for each check that is not ready, in the form `<check>: <detail>. Fix: <what to
  run>`. When there are more, the bar says how many more.
- *Ready to set up a run* when every check passes.

A line with a link opens the setup when you click it.

## Themes

**View > Theme** chooses **System** (the default: follow the operating system), **Light** or
**Dark**. The choice is saved and applies at once.

## Menus and tabs

- **File > Quit** (Ctrl+Q)
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
