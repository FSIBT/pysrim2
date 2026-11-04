from .paths import srim_dir

# Kept for backward compatibility; new code should call ``srim.paths.srim_dir()``
# (``TRIM.run`` and ``SR.run`` already do so when no directory is given).
DEFAULT_SRIM_DIRECTORY = str(srim_dir())
