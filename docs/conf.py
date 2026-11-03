# Configuration file for the Sphinx documentation builder.
# https://www.sphinx-doc.org/en/master/usage/configuration.html

from importlib.metadata import version as _version

project = "pysrim2"
copyright = "2018, Chris Ostrouchov; 2026, pysrim2 contributors"
author = "Chris Ostrouchov and pysrim2 contributors"

release = _version("pysrim2")
version = ".".join(release.split(".")[:2])

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.mathjax",
    "sphinx.ext.napoleon",
    "myst_parser",
]

templates_path = ["_templates"]
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
master_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "superpowers/**", "paper/**"]
pygments_style = "sphinx"

html_theme = "furo"
html_static_path = []

htmlhelp_basename = "pysrim2doc"
latex_documents = [
    (master_doc, "pysrim2.tex", "pysrim2 Documentation", author, "manual"),
]
man_pages = [(master_doc, "pysrim2", "pysrim2 Documentation", [author], 1)]
