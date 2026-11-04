.. _installation:

============
Installation
============

Install ``pysrim2`` with `uv <https://docs.astral.sh/uv/>`_ or pip. Python
3.11 or newer is required. The import name is ``srim``.

If the original ``pysrim`` is installed, remove it first, because both
packages provide the ``srim`` module::

   pip uninstall pysrim && pip install pysrim2

.. admonition:: SRIM licence

   SRIM © SRIM.com (James F. Ziegler) — free for non-commercial use only;
   see ``LEGAL.rtf``. pysrim2 never ships SRIM; the installer downloads it
   and keeps Ziegler's ``LEGAL.rtf`` next to every copy. Commercial users
   must get SRIM from `srim.org <http://www.srim.org/>`_ themselves. You do
   not need SRIM if you only analyse existing output files.

Quick start
-----------

.. code-block:: bash

   uv tool install pysrim2       # the pysrim2 command; or: pip install pysrim2
   pysrim2 install --dry-run     # show every step and the exact commands, change nothing
   pysrim2 install               # do it
   pysrim2 doctor                # check the result

With no component names, ``pysrim2 install`` picks the right set for your
platform (see below) and also installs the :doc:`agent skill <agents>`.
Steps that need administrator rights ask for confirmation first (or run
with ``--yes``); ``--dry-run`` never changes anything. See :doc:`cli` for
all options and exit codes. Use ``uv add pysrim2`` instead if you only
want the library in a project.

Windows (native)
----------------

``pysrim2 install`` runs two steps: ``srim`` downloads SRIM-2013 into
``%LOCALAPPDATA%\pysrim2\srim``, and ``vb5`` puts the Visual Basic 5 runtime
and the OCX controls TRIM needs next to it. Registering the OCX controls
(``regsvr32``) needs administrator rights, so you get an admin prompt
(or pass ``--yes`` from an elevated terminal).

Linux (native, through wine)
----------------------------

The default set is ``wine``, ``srim`` and ``skill``. The ``wine`` step is two
parts:

1. *wine packages.* It installs wine with 32-bit support, ``xvfb-run``,
   ``cabextract``, ``unzip`` and ``curl`` through your package manager
   (``apt``, ``dnf``, ``zypper`` or ``pacman``; Arch is untested). This
   needs ``sudo`` and asks first. On other distributions install them
   yourself, see the `WineHQ download page
   <https://www.winehq.org/download>`_.
2. *private wine prefix.* A 32-bit prefix under ``~/.local/share/pysrim2/wine``
   with the VB5 runtime and OCX controls. pysrim2 never touches ``~/.wine``
   and ignores ``$WINEPREFIX``; the prefix variable is only set in the
   environment of the child processes it starts.

SRIM runs headless through ``xvfb-run`` on a 1024x768x24 virtual screen,
so no display is needed.

macOS
-----

Native SRIM is not supported on macOS. Use the :doc:`container`; running
``pysrim2 install`` installs Podman (never Docker Desktop), starts a Podman
machine and pulls the image.

Where things live
-----------------

pysrim2 keeps its files in one data directory: ``%LOCALAPPDATA%\pysrim2`` on
Windows, ``~/Library/Application Support/pysrim2`` on macOS and
``${XDG_DATA_HOME:-~/.local/share}/pysrim2`` elsewhere. These environment
variables override the defaults:

``PYSRIM2_HOME``
   The data directory.

``PYSRIM2_SRIM_DIR``
   The directory holding ``TRIM.exe``. If unset, pysrim2 uses
   ``<data directory>/srim``, then an existing ``/tmp/srim``.

``PYSRIM2_WINEPREFIX``
   The private wine prefix (default ``<data directory>/wine``).

``TRIM().run()`` and ``SR().run()`` use this SRIM directory when you do not
pass one.

Installing SRIM by hand
-----------------------

If you prefer, extract SRIM yourself into any directory that contains
``TRIM.exe`` and point ``PYSRIM2_SRIM_DIR`` (or the ``srim_directory``
argument of ``run()``) at it. pysrim2 never writes into such a directory.
The archive pysrim2 installs is the
`srim-2013 release <https://github.com/FSIBT/pysrim2/releases/tag/srim-2013>`_
(``srim.2013.zip``, SHA-256
``f425c4715d5a849c1f521a8fc14aff58d41066fa4ca773546690a220ff3acecf``, with
``LEGAL.rtf`` and ``SRIM-NOTICE.txt`` as separate assets). A checksum mismatch
is a hard error and nothing is installed. On Linux you still need the wine
packages and prefix: ``pysrim2 install wine``.

Troubleshooting
---------------

Start with ``pysrim2 doctor``: every failing check has a ``fix:`` line.

**The install seems stuck.** Long sub-steps print a progress line when they
start (creating the wine prefix, winetricks, the VB5 runtime, the SRIM
download); the last line tells you where it is. Wine setup is bounded:
a ``wineserver`` that does not exit within 60 s is stopped. Press Ctrl-C to
abort; the private prefix's wine processes are stopped.

**An old winetricks.** pysrim2 always downloads and uses its own pinned,
checksum-verified winetricks and ignores any winetricks on your ``PATH``
(old copies do not know the OCX verbs it needs).

**Mono, Gecko and controller drivers.** They are disabled for the private
prefix (the Wine Mono/Gecko prompts and the USB, controller and Bluetooth
drivers can hang or crash headless setups). SRIM does not use them.

**"X connection to :N broken" and Mesa/EGL warnings.** Harmless; pysrim2
filters them from ``TRIM.run()`` and ``SR.run()`` output.

**A run fails.** ``SRIMProcessError`` carries SRIM's stderr; ``SRIMRunError``
means SRIM wrote no result (old output is deleted before each run, so a
crash never returns a previous run's data).
