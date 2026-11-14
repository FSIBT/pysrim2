"""A stand-in for the PDF export command (test_notebook_pdf.py runs it in place of marimo or nbconvert).

Usage: ``fake_pdf_export.py MODE OUT``. ``write`` writes OUT; ``fail`` prints playwright's
missing-browser text and exits 1; ``boom`` prints one line and exits 1; ``hang`` sleeps (the
export must be killed); ``nothing`` exits 0 without writing a file.
"""

import sys
import time

mode, out = sys.argv[1], sys.argv[2]
if mode == "write":
    with open(out, "wb") as handle:
        handle.write(b"%PDF-1.4 fake\n")
elif mode == "fail":
    print("Error: Executable doesn't exist at /fake/chromium-headless-shell", file=sys.stderr)
    sys.exit(1)
elif mode == "boom":
    print("boom: the export broke", flush=True)
    sys.exit(1)
elif mode == "hang":
    time.sleep(60)
