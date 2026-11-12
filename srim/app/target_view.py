"""The to-scale cross-section of the target, beside the Geometry tab's layer table.

The layers are drawn left to right across the target, the beam comes in from the left at the
incidence angle (from the surface normal). A layer that is drawn wider than to scale has a dashed
outline and a "≠" mark; the selected layer has a thick outline in the highlight colour. A click
on a layer emits :attr:`TargetView.layer_clicked` with its row, and :meth:`TargetView.set_selected`
shows the row the table selects. The drawing repaints when the document changes.

The widths and the tooltips come from :mod:`srim.app.drawing`, which has the rules.
"""

import math

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from srim.app.document import DocumentModel
from srim.app.drawing import beam_angle, layer_boxes, layer_color, layer_tooltip

#: space left of the target for the beam and its arrow, in pixels
BEAM_MARGIN = 56
#: space right of the target
EDGE_MARGIN = 10
#: space above the layers for the "≠" marks
TOP_MARGIN = 22
#: space below the layers
BOTTOM_MARGIN = 10
#: the arrow's length, and the wings of its head
ARROW_LENGTH = BEAM_MARGIN - 8
HEAD_LENGTH = 7
HEAD_ANGLE_DEG = 25.0


class TargetView(QWidget):
    """The cross-section of ``document``'s target; ``selected`` is the highlighted row (or -1)."""

    layer_clicked = Signal(int)

    def __init__(self, document: DocumentModel, parent=None):
        super().__init__(parent)
        self._document = document
        self._selected = -1
        self.setMinimumSize(180, 120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        document.changed.connect(self.update)

    # --- geometry ---------------------------------------------------------------------

    @property
    def selected(self) -> int:
        """The row the table selects, or -1."""
        return self._selected

    def _layers(self) -> list:
        return self._document.doc.get("target") or []

    def _boxes(self):
        thicknesses = [layer.get("thickness_nm") for layer in self._layers()]
        return layer_boxes(thicknesses, self.target_span()[1])

    def target_span(self) -> tuple[int, int]:
        """Where the layers are, in widget pixels: the left edge and the width."""
        return BEAM_MARGIN, max(0, self.width() - BEAM_MARGIN - EDGE_MARGIN)

    def layer_at(self, x: int) -> int:
        """The row of the layer under widget column ``x``, or -1 outside the layers."""
        left, _ = self.target_span()
        for row, box in enumerate(self._boxes()):
            if left + box.left <= x < left + box.left + box.width:
                return row
        return -1

    def tooltip_at(self, x: int) -> str:
        """The tooltip of the layer under column ``x``, or "" outside the layers."""
        row = self.layer_at(x)
        if row < 0:
            return ""
        return layer_tooltip(self._layers()[row], self._boxes()[row].to_scale)

    # --- selection ----------------------------------------------------------------------

    def set_selected(self, row: int) -> None:
        """Highlight the layer at ``row``; -1 highlights none."""
        self._selected = int(row)
        self.update()

    # --- events -------------------------------------------------------------------------

    def mousePressEvent(self, event):  # noqa: N802 (Qt's name)
        row = self.layer_at(int(event.position().x()))
        if row >= 0:
            self.layer_clicked.emit(row)
        super().mousePressEvent(event)

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip:
            text = self.tooltip_at(int(event.pos().x()))
            if text:
                QToolTip.showText(event.globalPos(), text, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    def paintEvent(self, _event):  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        palette = self.palette()
        ink = palette.color(QPalette.ColorRole.WindowText)
        highlight = palette.color(QPalette.ColorRole.Highlight)
        left, width = self.target_span()
        top = TOP_MARGIN
        bottom = max(top, self.height() - BOTTOM_MARGIN)
        height = bottom - top

        painter.setPen(QPen(ink, 2))
        painter.drawLine(QPointF(left, top), QPointF(left, bottom))  # the surface
        self._paint_beam(painter, ink, left, (top + bottom) / 2)

        layers = self._layers()
        if not layers:
            painter.setPen(QPen(ink))
            painter.drawText(
                QRectF(left, top, width, height),
                Qt.AlignmentFlag.AlignCenter,
                "No layers yet",
            )
            return
        for row, (layer, box) in enumerate(zip(layers, self._boxes())):
            rect = QRectF(left + box.left, top, box.width, height)
            painter.fillRect(rect, QColor(layer_color(layer)))
            if not box.to_scale:
                mark = QRectF(rect.left(), 0, rect.width(), TOP_MARGIN)
                painter.setPen(QPen(ink))
                painter.drawText(mark, Qt.AlignmentFlag.AlignCenter, "≠")
            if row == self._selected:
                pen = QPen(highlight, 3)
                painter.setPen(pen)
                painter.drawRect(rect.adjusted(1.5, 1.5, -1.5, -1.5))
            else:
                pen = QPen(ink, 1)
                if not box.to_scale:
                    pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.drawRect(rect)

    def _paint_beam(self, painter: QPainter, ink: QColor, left: float, middle: float) -> None:
        """The beam arrow: its head on the surface, its tail ``ARROW_LENGTH`` back along the beam."""
        angle = beam_angle(
            (self._document.doc.get("ion") or {}).get("angle_deg"), self._document.base_dir
        )
        angle = max(-90.0, min(90.0, angle))
        radians = math.radians(angle)
        dx, dy = math.cos(radians), math.sin(radians)  # the direction into the target
        head = QPointF(left, middle)
        tail = QPointF(left - ARROW_LENGTH * dx, middle - ARROW_LENGTH * dy)
        painter.setPen(QPen(ink, 2))
        painter.drawLine(tail, head)
        back_x, back_y = -dx, -dy  # the head's wings point back along the beam
        for sign in (1.0, -1.0):
            turn = math.radians(sign * HEAD_ANGLE_DEG)
            wing_x = back_x * math.cos(turn) - back_y * math.sin(turn)
            wing_y = back_x * math.sin(turn) + back_y * math.cos(turn)
            wing = QPointF(head.x() + HEAD_LENGTH * wing_x, head.y() + HEAD_LENGTH * wing_y)
            painter.drawLine(head, wing)
