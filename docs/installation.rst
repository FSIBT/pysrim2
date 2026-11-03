.. _installation:

============
Installation
============

Install ``pysrim2`` with `uv <https://docs.astral.sh/uv/>`_ or pip. Python
3.11 or newer is required. The import name is ``srim``.

.. code-block:: bash

   uv add pysrim2
   # or
   pip install pysrim2

If the original ``pysrim`` is installed, remove it first, because both
packages provide the ``srim`` module::

   pip uninstall pysrim && pip install pysrim2

SRIM
----

SRIM itself is not included. Download it from `srim.org
<http://www.srim.org/>`_. SRIM is copyright (c) James F. Ziegler and is free
for non-commercial use only. You do not need SRIM if you are only analysing
existing output files.

Windows
-------

Extract the SRIM files into a directory and pass that directory to
``trim.run()``. Check that ``TRIM.exe`` is in the directory.

Linux and macOS
---------------

Install `wine <https://www.winehq.org/>`_ (for example ``apt install wine``
on Debian-based systems, or see `this post
<https://www.davidbaumgold.com/tutorials/wine-mac/>`_ for macOS), then extract
SRIM into a directory. SRIM is `compatible
<https://appdb.winehq.org/objectManager.php?sClass=version&iId=13202>`_ with
wine. If ``xvfb-run`` is available, ``pysrim2`` runs SRIM through it so no
display is needed. An installer for SRIM under wine is planned.

A container image with SRIM under wine is also planned.
