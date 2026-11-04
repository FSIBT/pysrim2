Welcome to pysrim2's documentation!
===================================

.. admonition:: pysrim2 is a continuation of pysrim

   pysrim2 is a maintained continuation of
   `pysrim <https://gitlab.com/costrouc/pysrim>`_ by Chris Ostrouchov.
   See :doc:`changes-from-pysrim`.

.. image:: ../examples/images/length-heatmap-log-cropped.png

``pysrim2`` is a Python package that wraps and extends SRIM, a popular
tool for simulating ions traveling through a material. It addresses
several pain points of SRIM: compatibility with all operating systems,
automation and crash recovery of SRIM calculations, parsing of all
output files, and publication quality plots.

The import name is ``srim``. See :doc:`installation` for getting
``pysrim2`` and SRIM installed. Next follow the simple :doc:`tutorial`
to get started. For a more advanced example see the `SiC ion damage
production jupyter notebook
<https://github.com/FSIBT/pysrim2/blob/main/examples/notebooks/SiC.ipynb>`_.
Use :ref:`modindex` to search the API documentation.

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   installation
   container
   cli
   agents
   tutorial
   benchmarks
   changes-from-pysrim
   changelog

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
