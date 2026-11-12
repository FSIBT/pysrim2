# Desktop app

`pysrim2 app` opens a window for pysrim2. In 0.12.0 it guides you through setting up
SRIM: it shows how SRIM runs on your machine, installs what is still missing, and checks
the result. It also edits a run setup: the target (Geometry tab), the beam (Beam tab) and the
run settings (Run settings tab). Starting runs and reading results follow later in the 0.12
series.

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

The first line is a link when it names a place: clicking it opens the setup, or the tab and the
field it names. When there are more lines, the bar says how many more, and its tooltip lists
all of them.

## Geometry

The **Geometry** tab edits the layers of the target. The table has one row per layer; the
first row is the layer the beam reaches first. Its columns are:

| Column | What it holds |
|---|---|
| Name | A name for the layer (optional; the cell is blank when there is none) |
| Elements | The elements and their amounts, for example `Si 1, O 2`; an isotope shows as `Si-28 1` |
| Density (g/cm³) | The density |
| Thickness | The thickness, shown in the layer's unit |
| Unit | The unit of the thickness: Å, nm, µm or mm (the value is stored in nm) |
| Gas | Ticked for a gas layer |

Type a number into a cell and press Enter. A comma works as a decimal point (`1,5` is 1.5).
Text that is not a number is refused and the old value stays. An empty cell is a missing value:
the status bar names it. A value the library cannot use (for example a negative thickness) is
shown in red, with the library's message as its tooltip.

**Add layer**, **Remove layer**, **Up** and **Down** change the layers; you can also drag a row
to a new place. **Elements…** opens the elements of the selected layer in a dialog, with one row
per element: the element (**Choose…**, which opens the periodic table), its amount, and its
displacement, lattice and surface energies in eV. Each new element starts with SRIM's usual
values (amount 1, displacement 25 eV, lattice 0 eV, surface 3 eV).

**Material…** fills the selected layer from a menu. Its first part is the common materials
(by name; each one's formula and density come from `srim/data/compounds.yaml`); its second part,
**My materials**, is what you saved before. A material replaces the layer's name, density, gas
flag and elements. **Save as material…** keeps the selected layer's density, gas flag and
elements under a name you choose; saving a name that exists replaces that material. My materials
are stored in `materials.json` in pysrim2's data directory, in the `app` folder (`<data
dir>/app/materials.json`, see {doc}`installation`). A file that cannot be read counts as no
materials.

The periodic table has one button per element, Z = 1 to 92 (SRIM's range). An element's menu
has **Natural** first, which is SRIM's own mass for the element, the same as a scripted run gets
when it names no mass. The other entries are the element's natural isotopes, most abundant first,
and choosing one sets that mass for the ion or the element. Only an isotope is stored with a mass;
natural elements are stored without one.

Beside the table the **target** is drawn to scale. Each layer's width follows its thickness,
the beam comes in from the left at its angle, and a click on a layer selects its row (and a row
selects its layer). A thin layer next to a thick one is still drawn a few pixels wide so that it
stays visible and clickable. Such a layer is drawn wider than to scale: it has a dashed outline,
a "≠" mark and a tooltip that says so. A layer with no usable thickness is drawn the same way. When
there are more layers than fit at that width, each gets an equal width and none is to scale. The
picture is only a guide: the numbers in the table are the setup.

## Beam

The **Beam** tab sets the ion, the number of ions and the beam quantities. The **Ion** button
opens the periodic table, with the isotopes as on the Geometry tab. **Number of ions** is a whole
number of at least 1.

Each beam quantity has its own editor: **Energy** (keV), **Angle** (degrees), **Azimuth**
(degrees) and the position **Position y**, **Position z** and **Depth** (nm). Energy and angle
have no **Not set** choice, but an energy that is missing is named by the status bar. Azimuth and
the three positions are **Not set** by default, which leaves them out of the setup. Setting one
position coordinate creates the position, with the other two at 0.0. **Not set** on a coordinate
removes the whole position when the other two are 0.0 (or Not set); otherwise that coordinate is
stored as 0.0.

Each quantity has a **Kind**:

- **Fixed value** is one number.
- **Gaussian** (mean, sigma), **Uniform** (low, high), **Triangular** (low, mode, high) and
  **Log-normal** (mu, sigma) are the distributions. The parameters are those of the
  {doc}`beam` guide.
- **Python function** takes a reference written as `python:<module>:<name>`, for example
  `python:my_beams:density`. The module must be importable by its name from the folder you run
  from, so put it next to your script. A function in `__main__`, a lambda and a function made
  inside another function are refused, because a worker process cannot import them.
- **Histogram file** is read with **Load…**. The file's bars are shown in a preview, and **OK**
  uses it (**Cancel** keeps the setup as it was). A file that is broken (one row, decreasing
  edges, values that are not numbers) is refused with the library's message.

A distribution (every kind but a fixed value) also has **Limit to**, with optional low and high
bounds: values outside the range are not sampled.

Under each editor the density of that quantity is drawn: a smooth curve for a distribution, and a
step line for a histogram or a uniform distribution. A binned beam also marks its bin edges with
dashed lines. A value the library refuses is not drawn. The tab scrolls, so all six plots can be
reached in a small window.

A setup with a distribution, an azimuth or a position is a **distributed** beam, and the tab then
shows the **Beam mode**. A single energy and angle is one SRIM run, and the tab says so instead.
The modes are:

- **Automatic** (the default) is exact for calculations 1 and 2, and binned for the others. The
  line under the choice says which one it picked and why.
- **Exact (TRIM.DAT)** samples every ion from the distributions and runs SRIM once, with the
  sampled ions in SRIM's `TRIM.DAT` file. One SRIM run holds up to 99 999 ions; a larger exact
  beam is split into several runs by `srim.beam.run_beam` (see {doc}`beam`).
- **Binned** splits the beam into bins of energy and angle and runs SRIM once per bin. **Energy
  bins** and **Angle bins** set the number of bins (**Automatic** is 10 and 6, up to 2000). The
  count can be edited only for a distributed energy or angle, and only when it is binned. A
  setup whose bins are written as edges keeps them, and the count shows without being editable.

For the meaning of the modes and of the sampling, see {doc}`beam`. For the file itself, see
{doc}`run-spec`.

**Histogram files and saving.** A histogram file is read from the folder of the setup when it is
relative to it. When you save, a histogram file that lies outside the folder of the saved
`run.yaml` is copied into a `beam/` folder next to it, and the setup refers to the copy (a
second file with the same name becomes `name-2.txt`, and so on). The setup then opens when its
folder moves. A histogram file that cannot be found stops the save with a message.

## Run settings

The **Run settings** tab sets the calculation and the options that SRIM reads from `TRIM.IN`.
Each control stores one item in the setup, and the library writes it to the `TRIM.IN` line
named here:

| Control | Setup item | `TRIM.IN` line |
|---|---|---|
| Calculation | `calculation` (1 Quick, 2 Full cascades, 3 Monolayer) | cascade options |
| Random seed | `seed` | cascade options |
| Description | `settings.description` (no double quote) | target |
| Ranges, Backscattered ions, Transmitted ions, Sputtered atoms | `settings.ranges`, `backscattered`, `transmit`, `sputtered` (0 or 1) | disk files |
| Collisions | `settings.collisions` (0, 1 or 2) | disk files |
| EXYZ.txt from | `settings.exyz` (eV; Off is 0) | disk files |
| Bragg correction | `settings.bragg_correction` (default 1.0) | ion |
| Autosave every | `settings.autosave` | ion |

The number of ions is on the Beam tab (`number_ions`); it is written on the ion line of `TRIM.IN`.

A setting that is set back to its default is removed from the setup, and so is the `settings`
mapping when it becomes empty, so the setup holds only what differs from SRIM's defaults. A
`run.yaml` written by `TRIM.to_spec()` always lists `description` and `ranges`, even at their
defaults. The `TRIM.IN` file is the same either way: the library writes every setting to it.
Settings the tab does not show (for example `plot_mode`) and a calculation 4 to 7 from a file
are kept as they are. A calculation 4 to 7 is shown as "SRIM calculation N" in the menu.

## Undo, open and save

The window edits one run setup at a time, kept in a `run.yaml` file (the same file the library
reads with `RunSpec.from_yaml`):

- **File > New setup** (Ctrl+N) starts an empty setup.
- **File > Open setup…** (Ctrl+O) reads a `run.yaml`.
- **File > Save setup** (Ctrl+S) writes the setup to the file it came from; a new setup is saved
  with **Save setup as…** (Ctrl+Shift+S), which asks for a file.

A setup opened and saved without edits keeps the same contents. Anything the editors do not offer
(for example calculations 4 to 7, a `fixed` beam shape, or `settings.plot_mode`) is kept as it
is; the Beam tab shows a value it does not offer as YAML, and it stays until you choose another
kind.

Saving refuses an incomplete setup and says what is missing first, and a setup the library refuses
is saved only once it is fixed. A file that cannot be read or written gives a message, and the
setup stays as it was.

**Edit > Undo** and **Redo** (the standard keys of your platform) go back and forward through
the edits since the setup was opened or created (the last 200). Each change to a field is
one edit, so a typed number is one step once you press Enter or leave the field. Opening another
file starts a new history, so Undo never returns to the setup that was open before.

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
**Results**. The first three edit the run setup. In 0.12.0 **Progress** and **Results** only
say that they arrive in a later release.

## Settings file

The app's choices are stored in `<data dir>/app/settings.json`, where `<data dir>` is the
pysrim2 data directory (see {doc}`installation`). It holds the `route` (`native`, `wine`,
`container`, or `null` before a choice) and the `theme` (`system`, `light` or `dark`). A
missing or broken file gives the defaults, so the app always starts. You can delete the file
to start from the defaults again.
