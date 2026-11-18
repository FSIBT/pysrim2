pysrim2 runs SRIM/TRIM ion implantation calculations. These tools set up a run, start it, follow it and read its results. The full guide is the resource pysrim2://skill.

Two modes, one tool set. Every result has "mode": "app" when the pysrim2 window is open (the user sees the setup and the results there) or "headless" when it is not (results come back as numbers, and plots are saved and returned as images). Say which applies when you report a result.

The usual flow: status (is SRIM ready?), get_setup (the setup and what it still needs), lookup_element and find_material for symbols, isotopes and materials, set_section or set_values to change the setup, estimate, save_setup (a run needs a saved file), start_run, run_status until it is finished, then summary, available_plots and plot or table for the results. A value that the library refuses comes back as a tool error and the setup does not change. Never guess a density or a composition: look it up with find_material or ask the user.

Ask first. Some actions need the user's OK: a run estimated over 30 minutes, a run that wants more than half the CPU cores, replacing a file this session did not create, discarding unsaved changes, deleting a run and installer steps. The server asks the user through the client when the client can answer. When it cannot, the call returns "needs the user's OK" with a confirm_code. Then ask the user in the chat, and only when they agree, repeat the same call with that confirm_code. A code works once, for those exact arguments, for a few minutes. Never make up a code.

Undo and redo need the pysrim2 app. Admin installer steps are never run here: install_plan lists their commands for the user to run in a terminal.
