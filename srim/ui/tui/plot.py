"""The data of the plot view: the series of one result against the scanned parameters.

The plot shows an extracted value (``y``) against a scanned parameter (``x``, the first one by
default), with one series per value of a second parameter (``hue``, the second one by
default). A point is drawn once it has a value (a finished point, or a precision point with
an estimate); its error bar is ``<y>_err`` when the scan has one. Text values of ``x`` are
drawn at their positions, labelled by :attr:`PlotData.categories`.

:func:`build` is pure (it reads a :class:`~srim.ui.tui.state.ScanState`), and :func:`draw`
takes any object with the plotext figure's methods, so neither needs a terminal.
"""

from __future__ import annotations

from dataclasses import dataclass

from .state import is_number

#: series colours, in order (plotext colour names)
PALETTE = ("cyan", "yellow", "magenta", "green", "red", "blue", "white")
#: the message while no point has a value yet
WAITING = "waiting for the first result"


@dataclass(frozen=True)
class Series:
    """One hue value: ``x``, ``y`` and ``err`` (None where there is no error) of its points."""

    label: str
    x: tuple
    y: tuple
    err: tuple


@dataclass(frozen=True)
class PlotData:
    """What to draw: the series, the axis names, the text labels of a categorical x axis
    (``None`` for a numeric one) and a message to show instead of the plot."""

    series: tuple
    x_label: str
    y_label: str
    categories: tuple | None = None
    message: str = ""


def check_plot(param_names, plot):
    """Normalise and check a ``plot`` argument: ``(x, y, hue)`` or ``"X,Y[,HUE]"``.

    ``None`` (or an empty part) for ``x``, ``y`` or the hue means the default. ``hue=""``
    (written ``X,Y,`` on the command line) means no hue. ``x`` must be ``index`` or a scanned
    parameter, and ``hue`` one too; ``y`` is checked against the extracted columns once a
    result is known (see :func:`build`). Raises ValueError otherwise.
    """
    if plot is None:
        return None, None, None
    parts = plot.split(",") if isinstance(plot, str) else list(plot)
    if not 2 <= len(parts) <= 3:
        raise ValueError(f"plot takes X,Y or X,Y,HUE, not {plot!r}")
    parts = [part.strip() if isinstance(part, str) else part for part in parts]
    parts += [None] * (3 - len(parts))
    x, y, hue = parts
    x = x or None
    y = y or None
    if x is not None and x != "index" and x not in param_names:
        raise ValueError(f"x {x!r} is not a scanned parameter (have: {_names(param_names)})")
    if hue and hue not in param_names:
        raise ValueError(f"hue {hue!r} is not a scanned parameter (have: {_names(param_names)})")
    return x, y, (hue if hue is not None else None)


def _names(names) -> str:
    return ", ".join(["index", *names]) if names else "index"


def choose_axes(state, plot=None):
    """``(x, y, hue)`` of the view: ``plot`` (see :func:`check_plot`) with the defaults. The
    defaults are the first scanned parameter for x, the second for the hue, and the first
    extracted value seen for y (None until a value is known). ``hue`` is None for no hue."""
    x, y, hue = check_plot(state.param_names, plot)
    names = state.param_names
    x = x or (names[0] if names else "index")
    if hue is None:
        hue = names[1] if len(names) > 1 else None
    elif hue == "":
        hue = None
    y = y or (state.value_names[0] if state.value_names else None)
    return x, y, hue


def build(state, x, y, hue) -> PlotData:
    """The data for ``x`` against ``y``, one series per value of ``hue`` (see the module)."""
    x_label = x or "index"
    y_label = y or ""
    if y is None or not state.value_names:
        return PlotData((), x_label, y_label, message=WAITING)
    if y not in state.value_names:
        known = ", ".join(state.value_names)
        message = f"{y!r} is not an extracted column (have: {known})"
        return PlotData((), x_label, y_label, message=message)

    def x_of(row):
        return row.index if x_label == "index" else row.params.get(x)

    rows = list(state.points.values())
    raw = [x_of(row) for row in rows]
    categories = None
    if all(is_number(value) for value in raw):
        position = {row.index: float(x_of(row)) for row in rows}
    else:
        categories = tuple(dict.fromkeys(str(value) for value in raw))
        position = {row.index: float(categories.index(str(x_of(row)))) for row in rows}

    groups: dict[str, list] = {}
    for row in rows:
        value = row.values.get(y)
        if not is_number(value):
            continue
        err = row.values.get(f"{y}_err")
        label = "" if hue is None else str(row.params.get(hue))
        groups.setdefault(label, []).append(
            (position[row.index], float(value), float(err) if is_number(err) else None)
        )
    series = []
    for label, points in groups.items():
        points.sort(key=lambda point: point[0])
        series.append(
            Series(
                label,
                tuple(p[0] for p in points),
                tuple(p[1] for p in points),
                tuple(p[2] for p in points),
            )
        )
    if not series:
        return PlotData((), x_label, y_label, categories, WAITING)
    return PlotData(tuple(series), x_label, y_label, categories)


def draw(plt, data: PlotData) -> None:
    """Draw ``data`` on a plotext figure ``plt``: a marker per point, one colour per series,
    and error bars as vertical segments (plotext has none)."""
    plt.clear_figure()
    if not data.series:
        plt.title(data.message or WAITING)
        return
    for index, series in enumerate(data.series):
        colour = PALETTE[index % len(PALETTE)]
        plt.scatter(list(series.x), list(series.y), label=series.label or None, color=colour)
        for x, y, err in zip(series.x, series.y, series.err, strict=True):
            if err is not None and err > 0:
                plt.plot([x, x], [y - err, y + err], color=colour)
    if data.categories is not None:
        plt.xticks(list(range(len(data.categories))), list(data.categories))
    plt.xlabel(data.x_label)
    plt.ylabel(data.y_label)
    plt.title(f"{data.y_label} by {data.x_label}")
