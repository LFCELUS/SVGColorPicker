# svg_style_ui.py
from __future__ import annotations

import sys
import io
import os
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

from PyQt5.QtCore import Qt, QByteArray, QSize, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QWidget, QComboBox, QPushButton, QColorDialog, QFileDialog,
    QLabel, QHBoxLayout, QVBoxLayout, QScrollArea, QMessageBox,
    QLineEdit, QCheckBox, QSplitter, QSizePolicy, QFrame, QInputDialog  
)
from PyQt5.QtGui import QColor, QImage, QPainter, QPixmap, QPen   
from PyQt5.QtSvg import QSvgRenderer

import json
from datetime import datetime
# ---- EDIT THESE TO MATCH YOUR PROJECT --------------------------------------

# --- New: where to look for files ---
SVG_DIR = Path("./bin/svg")           # e.g., ./bin/svg/*.svg
THEME_DIR = Path("./bin/themes")      # e.g., ./bin/themes/*.txt

# Acceptable extensions
SVG_EXTS = {".svg"}
THEME_EXTS = {".txt", ".json"}

# These will be filled at runtime by scanning the folders above
SVG_FILES: Dict[str, str] = {}
THEME_FILES: Dict[str, str] = {}

# The groups to show color controls for (by <g id="...">)
GROUPS: List[str] = [
    "Pin",
    "MainShape",
    "SecondaryShape",
    "PowerPort",
    "CrossRef",
    "CrossRefText",
    "Wire",
    "AllOtherText",
    # add more as needed
]

# The groups to show color controls for (by <g id="...">)
GUI_GROUPS: List[str] = [
    "MainShape",
    "PowerPort",
    "SecondaryShape",
    "Pin",
    "Wire",
    "CrossRef",
    "CrossRefText",
    "AllOtherText",
    # add more as needed
]

# Default color for all pickers
DEFAULT_HEX = "#000000"
DEFAULT_WIDTH = "2"
# ---------------------------------------------------------------------------

# Uses your module from the same folder
import svg_tools  # make sure svg_tools.py is in the same directory

def _discover_files_in_dir(base: Path, exts: set[str]) -> Dict[str, str]:
    """
    Return {display_name: absolute_path} for all files with given extensions.
    display_name is filename without extension (unique within dir).
    """
    results: Dict[str, str] = {}
    if not base.exists():
        return results
    for p in sorted(base.glob("*")):
        if p.is_file() and p.suffix.lower() in exts:
            name = p.stem
            results[name] = str(p.resolve())
    return results

def _ensure_file_exists(path: str) -> bool:
    try:
        return Path(path).is_file()
    except Exception:
        return False

def _is_valid_hex(s: str) -> bool:
    if not s:
        return False
    if s[0] != "#":
        return False
    if len(s) != 7:
        return False
    # Let QColor validate more strictly (supports #RGB and #RRGGBB)
    return QColor(s).isValid()

def _color_preview_pixmap(bg: QColor | None, border: QColor | None, bwidth: int = 5, size: int = 18) -> QPixmap:
    """
    Make a small square pixmap preview.
    - bg: fill color; None -> transparent checker
    - border: stroke color; None -> neutral gray border
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)

    # Checkerboard if bg is None
    if bg is None:
        tile = 4
        for y in range(0, size, tile):
            for x in range(0, size, tile):
                c = QColor(210, 210, 210) if ((x // tile + y // tile) % 2 == 0) else QColor(245, 245, 245)
                p.fillRect(x, y, tile, tile, c)
    else:
        p.fillRect(1, 1, size - 2, size - 2, bg)

    # Border
    if border is not None:
        pen = QPen(border)
        pen.setWidth(bwidth) 
        p.setPen(pen)
        p.drawRect(0, 0, size - 1, size - 1)

    p.end()
    return pm

def _parse_theme_file_json(path: Path) -> Dict[str, Dict[str, str]]:
    """
    Read a JSON theme (schema above) and return {group: {fill, stroke, stroke-width}}.
    Missing keys → "" (like your current behavior).
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    defaults = data.get("defaults", {})
    groups = data.get("groups", {})
    out: Dict[str, Dict[str, str]] = {}

    def _norm(v: str) -> str:
        return (v or "").strip()

    for gid in GROUPS:
        g = groups.get(gid, {})
        out[gid] = {
            "fill":         _norm(g.get("fill",         defaults.get("fill", ""))),
            "stroke":       _norm(g.get("stroke",       defaults.get("stroke", ""))),
            "stroke-width": _norm(g.get("stroke-width", defaults.get("stroke-width", ""))),
        }
    return out

def _write_theme_file_json(path: Path, color_map: Dict[str, Dict[str, str]], name: str):
    """
    Write the current theme to JSON using the proposed schema.
    """
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    payload = {
        "version": 1,
        "name": name,
        "meta": {"updated_at": now},
        "groups": {gid: {
            "fill": color_map.get(gid, {}).get("fill", ""),
            "stroke": color_map.get(gid, {}).get("stroke", ""),
            "stroke-width": color_map.get(gid, {}).get("stroke-width", ""),
        } for gid in GROUPS}
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

# =========================
# SVG -> Image preview widget
# =========================

class SvgImagePreview(QLabel):
    """
    A QLabel-based preview that rasterizes SVG bytes into a QImage via QSvgRenderer.
    - Call set_svg_bytes(data) whenever the SVG changes.
    - It re-renders at the current widget size (fit) with smooth scaling.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(520, 520)
        self.setAlignment(Qt.AlignCenter)
        self._svg_bytes: Optional[bytes] = None
        self._natural_size: Optional[QSize] = None  # from width/height/viewBox
        self._last_pixmap: Optional[QPixmap] = None
        # small border to distinguish the area visually (optional)
        self.setStyleSheet("border: 1px solid rgba(0,0,0,0.15); border-radius: 6px;")
        self.checkered = False

    def set_svg_bytes(self, data: bytes):
        self._svg_bytes = data
        self._natural_size = self._probe_size(data)
        self._rerender()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._svg_bytes:
            self._rerender()

    def setCheckered(self, checked: bool):
        self.checkered = checked

    def _probe_size(self, data: bytes) -> Optional[QSize]:
        """Try to infer a natural size from the SVG (width/height or viewBox)."""
        try:
            root = svg_tools.ET.fromstring(data)
            # width/height attributes can be like "800", "800px", "100%" etc.
            def _parse_len(v: str) -> Optional[float]:
                v = v.strip()
                if v.endswith("%"):
                    return None
                for suffix in ("px", "pt", "mm", "cm", "in"):
                    if v.endswith(suffix):
                        v = v[: -len(suffix)]
                        break
                try:
                    return float(v)
                except Exception:
                    return None

            w_attr = root.get("width")
            h_attr = root.get("height")
            if w_attr and h_attr:
                w = _parse_len(w_attr)
                h = _parse_len(h_attr)
                if w and h and w > 0 and h > 0:
                    return QSize(int(w), int(h))

            vb = root.get("viewBox")
            if vb:
                parts = vb.replace(",", " ").split()
                if len(parts) == 4:
                    try:
                        _, _, vw, vh = map(float, parts)
                        if vw > 0 and vh > 0:
                            return QSize(int(vw), int(vh))
                    except Exception:
                        pass
        except Exception:
            pass
        return None

    def _paint_checker(self, painter: QPainter, w: int, h: int, tile: int = 10):
        c1 = QColor(210, 210, 210)
        c2 = QColor(245, 245, 245)
        y = 0
        while y < h:
            x = 0
            row = (y // tile) % 2
            while x < w:
                col = (x // tile) % 2
                painter.fillRect(x, y, tile, tile, c1 if (row + col) % 2 == 0 else c2)
                x += tile
            y += tile

    def _rerender(self):
        if not self._svg_bytes:
            self.clear()
            return

        # Decide target size: fit inside label size, preserve aspect ratio
        target_w = max(1, self.width() - 8)   # some padding
        target_h = max(1, self.height() - 8)

        # If we know a natural size, use it to compute aspect ratio
        if self._natural_size and self._natural_size.width() > 0 and self._natural_size.height() > 0:
            nat_w, nat_h = self._natural_size.width(), self._natural_size.height()
            nat_ratio = nat_w / nat_h
            avail_ratio = target_w / target_h
            if nat_ratio >= avail_ratio:
                render_w = target_w
                render_h = int(target_w / nat_ratio)
            else:
                render_h = target_h
                render_w = int(target_h * nat_ratio)
        else:
            # Fallback: just use the available area
            render_w, render_h = target_w, target_h

        # Create image with transparency and render
        image = QImage(render_w, render_h, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, True)

        if self.checkered:
            # Checkerboard background
            self._paint_checker(painter, render_w, render_h, tile=10)

        renderer = QSvgRenderer(QByteArray(self._svg_bytes))
        renderer.render(painter)
        painter.end()

        self._last_pixmap = QPixmap.fromImage(image)
        self.setPixmap(self._last_pixmap)

# =========================
# UI pieces
# =========================

class GroupRow(QWidget):
    """
    A row with small previews and controls:
    [F-preview] [S-preview] [GroupID]  Fill: [text][Pick][None □]   Stroke: [text][Pick][None □]
    Enforces that each color has a value unless 'None' is checked.
    """
    changed = pyqtSignal()

    def __init__(self, group_id: str, parent=None):
        super().__init__(parent)
        self.group_id = group_id

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        # Previews
        self.fill_preview = QLabel()
        self.fill_preview.setFixedSize(30, 30)
        #self.stroke_preview = QLabel()
        #self.stroke_preview.setFixedSize(20, 20)

        # Group label
        self.lbl = QLabel(group_id)
        self.lbl.setMinimumWidth(100)
        self.lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        # Fill
        self.fill_edit = QLineEdit()
        self.fill_edit.setPlaceholderText("#RRGGBB")
        self.fill_edit.setMaximumWidth(60)
        #self.fill_btn = QPushButton("Pick")
        #self.fill_btn.clicked.connect(self.pick_fill)
        self.fill_none = QCheckBox("None")
        self.fill_none.toggled.connect(self.on_fill_none_toggled)

        # Stroke
        self.stroke_edit = QLineEdit()
        self.stroke_edit.setPlaceholderText("#RRGGBB")
        self.stroke_edit.setMaximumWidth(60)
        #self.stroke_btn = QPushButton("Pick")
        #self.stroke_btn.clicked.connect(self.pick_stroke)
        self.stroke_none = QCheckBox("None")
        self.stroke_none.toggled.connect(self.on_stroke_none_toggled)

        # Stroke Width
        self.stroke_w_edit = QLineEdit()
        self.stroke_w_edit.setPlaceholderText("1")
        self.stroke_w_edit.setMaximumWidth(60)
        self.stroke_w_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        # Default values
        self.fill_edit.setText(DEFAULT_HEX)
        self.stroke_edit.setText(DEFAULT_HEX)
        self.stroke_w_edit.setText(DEFAULT_WIDTH)

        # Live preview updates
        self.fill_edit.textChanged.connect(self.update_preview)
        self.stroke_edit.textChanged.connect(self.update_preview)
        self.stroke_w_edit.textChanged.connect(self.update_preview)

        # Layout
        layout.addWidget(self.fill_preview)
        #layout.addWidget(self.stroke_preview)
        layout.addWidget(self.lbl, 1)
        
        line_fill = QHBoxLayout()
        line_fill.setSpacing(5)

        MIN_WIDTH = 40

        line_fill_label = QLabel("Fill:")
        line_fill_label.setMinimumWidth(MIN_WIDTH)
        line_fill.addWidget(line_fill_label)
        line_fill.addWidget(self.fill_edit, 2,alignment=Qt.AlignLeft)
        #layout.addWidget(self.fill_btn)
        #line_fill.addSpacing(5)
        line_fill.addWidget(self.fill_none,alignment=Qt.AlignLeft)

        line_stroke = QHBoxLayout()
        line_stroke.setSpacing(5)
        
        line_stroke_label = QLabel("Stroke:")
        line_stroke_label.setMinimumWidth(MIN_WIDTH)
        line_stroke.addWidget(line_stroke_label)
        line_stroke.addWidget(self.stroke_edit, 2,alignment=Qt.AlignLeft)
        #layout.addWidget(self.stroke_btn)
        #line_stroke.addSpacing(5)
        line_stroke.addWidget(self.stroke_none,alignment=Qt.AlignLeft)

        line_stroke_w = QHBoxLayout()
        line_stroke_w.setSpacing(5)

        line_stroke_w_label = QLabel("Width:")
        line_stroke_w_label.setMinimumWidth(MIN_WIDTH)
        line_stroke_w.addWidget(line_stroke_w_label)
        line_stroke_w.addWidget(self.stroke_w_edit, 2,alignment=Qt.AlignLeft)
        line_stroke_w.addWidget(QLabel("px"), 2, alignment=Qt.AlignLeft)

        line_fields = QVBoxLayout()
        line_fields.setSpacing(5)
        line_fields.addLayout(line_fill)
        line_fields.addLayout(line_stroke)
        line_fields.addLayout(line_stroke_w)

        layout.addLayout(line_fields)

        self.update_preview()

    # ----- Behavior -----

    def _set_color_from_dialog(self, line_edit: QLineEdit, none_checkbox: QCheckBox):
        # If 'None' was enabled, turn it off when picking
        if none_checkbox.isChecked():
            none_checkbox.setChecked(False)

        initial = QColor(line_edit.text().strip() or DEFAULT_HEX)
        if not initial.isValid():
            initial = QColor(DEFAULT_HEX)
        color = QColorDialog.getColor(initial, self, "Pick a color")
        if color.isValid():
            line_edit.setText(color.name())

    def pick_fill(self):
        self._set_color_from_dialog(self.fill_edit, self.fill_none)

    def pick_stroke(self):
        self._set_color_from_dialog(self.stroke_edit, self.stroke_none)

    def on_fill_none_toggled(self, checked: bool):
        self.fill_edit.setEnabled(not checked)
        #self.fill_btn.setEnabled(not checked)
        if checked:
            # Clear to emphasize it's 'none'
            self.fill_edit.setText("")
        else:
            if not self.fill_edit.text().strip():
                self.fill_edit.setText(DEFAULT_HEX)
        self.update_preview()

    def on_stroke_none_toggled(self, checked: bool):
        self.stroke_edit.setEnabled(not checked)
        #self.stroke_btn.setEnabled(not checked)
        if checked:
            self.stroke_edit.setText("")
        else:
            if not self.stroke_edit.text().strip():
                self.stroke_edit.setText(DEFAULT_HEX)
        self.update_preview()

    def set_values(self, fill: str | None, stroke: str | None, stroke_width: str | None):
        # Interpret "none" (case-insensitive) or empty as None
        if fill is None or fill.lower() == "none" or fill == "":
            self.fill_none.setChecked(True)
            self.fill_edit.setText("")
        else:
            self.fill_none.setChecked(False)
            self.fill_edit.setText(fill)

        if stroke is None or stroke.lower() == "none" or stroke == "":
            self.stroke_none.setChecked(True)
            self.stroke_edit.setText("")
        else:
            self.stroke_none.setChecked(False)
            self.stroke_edit.setText(stroke)

        if stroke_width is None or stroke_width.lower() == "none" or stroke_width == "":
            self.stroke_w_edit.setText("1")
        else:
            self.stroke_w_edit.setText(stroke_width)

        # Ensure enabled states match
        self.fill_edit.setEnabled(not self.fill_none.isChecked())
        #self.fill_btn.setEnabled(not self.fill_none.isChecked())
        self.stroke_edit.setEnabled(not self.stroke_none.isChecked())
        #self.stroke_btn.setEnabled(not self.stroke_none.isChecked())

        self.update_preview()

    def values(self) -> Dict[str, str]:
        # Return final values; 'none' if checkbox checked
        fill = "none" if self.fill_none.isChecked() else self.fill_edit.text().strip()
        stroke = "none" if self.stroke_none.isChecked() else self.stroke_edit.text().strip()
        stroke_width = self.stroke_w_edit.text().strip()
        return {"fill": fill, "stroke": stroke, "stroke-width": stroke_width}

    def validate(self) -> tuple[bool, str | None]:
        # Must have value unless 'None' is checked
        if not self.fill_none.isChecked():
            txt = self.fill_edit.text().strip()
            if not _is_valid_hex(txt):
                return False, f"{self.group_id}: Fill must be a valid hex color (e.g., #000000)."
        if not self.stroke_none.isChecked():
            txt = self.stroke_edit.text().strip()
            if not _is_valid_hex(txt):
                return False, f"{self.group_id}: Stroke must be a valid hex color (e.g., #000000)."
        return True, None

    def update_preview(self):
        # For previews: if 'none' -> bg/border None; else use the given colors

        fill_q = None if self.fill_none.isChecked() else QColor(self.fill_edit.text().strip() or DEFAULT_HEX)
        if fill_q is not None and not fill_q.isValid() and not _is_valid_hex(self.fill_edit.text().strip()):
            fill_q = QColor(DEFAULT_HEX)

        stroke_q = None if self.stroke_none.isChecked() else QColor(self.stroke_edit.text().strip() or DEFAULT_HEX)
        if stroke_q is not None and not stroke_q.isValid() and not _is_valid_hex(self.fill_edit.text().strip()):
            stroke_q = QColor(DEFAULT_HEX)

        self.fill_preview.setPixmap(_color_preview_pixmap(fill_q, stroke_q))

        self.changed.emit()

class SvgStylerApp(QWidget):
    """
    Main window:
      - Left: SVG->Image preview
      - Right: controls (file selector, group color rows, Apply, Save All to ZIP)
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Schematic Color Picker")
        self.resize(1100, 700)

        # Discover files from the configured folders
        global SVG_FILES, THEME_FILES
        SVG_FILES = _discover_files_in_dir(SVG_DIR, SVG_EXTS)
        THEME_FILES = _discover_files_in_dir(THEME_DIR, THEME_EXTS)

        # In-memory state
        self.trees: Dict[str, svg_tools.ET.ElementTree] = {}
        self.color_maps: Dict[str, Dict[str, Dict[str, str]]] = {
            title: {gid: {"fill": DEFAULT_HEX, "stroke": DEFAULT_HEX, "stroke-width": DEFAULT_WIDTH} for gid in GROUPS}
            for title in THEME_FILES
        }

        # Try load theme file and merge into state
        for theme in THEME_FILES:
            theme_path = THEME_FILES.get(theme)
            if theme_path and Path(theme_path).exists():
                parsed = _parse_theme_file_json(Path(theme_path))
                # merge only known GROUPS/keys to keep it safe
                for gid in GROUPS:
                    vals = self.color_maps[theme].setdefault(gid, {"fill": DEFAULT_HEX, "stroke": DEFAULT_HEX, "stroke-width": DEFAULT_WIDTH})
                    if gid in parsed:
                        vals["fill"] = parsed[gid].get("fill", vals["fill"])
                        vals["stroke"] = parsed[gid].get("stroke", vals["stroke"])
                        vals["stroke-width"] = parsed[gid].get("stroke-width", vals["stroke-width"])

        # ----- UI LAYOUT -----

        # Use a splitter so the preview gets extra space
        split = QSplitter(Qt.Horizontal, self)
        #split.setStyleSheet("background: rgba(220,220,220,1)")

        # ---------- Left panel (Preview) ----------
        left_panel = QWidget()
        left = QVBoxLayout(left_panel)
        left.setContentsMargins(20, 20, 20, 20)
        left.setSpacing(5)
        left_panel.setObjectName("LeftPannel")
        left_panel.setStyleSheet("""
        #LeftPannel {
            background: rgba(255,255,255,0.1);
        }
        """)

        self.file_title = QLabel("Select a File")
        self.file_title.setStyleSheet("font-weight: 600; font-size: 15px;")
        self.file_title.setContentsMargins(0, 0, 0, 8)
        self.theme_title = QLabel("Select a Theme")
        self.theme_title.setStyleSheet("font-weight: 600; font-size: 15px;")
        self.theme_title.setContentsMargins(0, 8, 0, 0)

        self.checkered_check = QCheckBox("Checkered")
        #self.checkered_check.setMinimumWidth(MIN_BUTTON_WIDTH)
        self.checkered_check.toggled.connect(self.on_checkered_toggled)

        self.preview = SvgImagePreview()
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview.setMinimumSize(320, 320)  # smaller minimum so it can go really compact if needed
        self.preview.setStyleSheet("border: 1px solid rgba(0,0,0,0.15); border-radius: 6px; background: rgba(255,255,255,1)")

        left.addWidget(self.file_title)
        left.addWidget(self.theme_title)
        left.addSpacing(10)
        left.addWidget(self.preview, 1)
        left.addWidget(self.checkered_check)

        # ---------- Right panel (Controls) ----------
        right_panel = QWidget()
        right = QVBoxLayout(right_panel)
        right.setContentsMargins(10, 10, 20, 10)
        right.setSpacing(10)
        right_panel.setObjectName("RightPannel")
        right_panel.setStyleSheet("""
        #RightPannel {
            border: 1px solid rgba(0,0,0,1); 
            border-radius: 6px;
            background: rgba(90,90,90,0.1);
        }
        """)

        MIN_WIDTH = 55

        # File chooser
        file_row = QHBoxLayout()
        file_label = QLabel("Schematic:")
        file_label.setMinimumWidth(MIN_WIDTH)
        file_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        file_row.addWidget(file_label)
        self.file_combo = QComboBox()
        for title in SVG_FILES:
            self.file_combo.addItem(title)
        self.file_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        file_row.addWidget(self.file_combo, 1)
        
        # Theme chooser
        theme_row = QHBoxLayout()
        theme_label = QLabel("Theme:")
        theme_label.setMinimumWidth(MIN_WIDTH)
        theme_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        theme_row.addWidget(theme_label)
        self.theme_combo = QComboBox()
        for title in THEME_FILES:
            self.theme_combo.addItem(title)
        self.theme_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.theme_combo.currentTextChanged.connect(self.on_theme_changed)
        theme_row.addWidget(self.theme_combo, 1)
        
        # Group rows in a scroll area
        self.rows_container = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(6, 6, 6, 6)
        self.rows_layout.setSpacing(0)

        self.group_rows: Dict[str, GroupRow] = {}
        for gid in GUI_GROUPS:
            row = GroupRow(gid)
            row.changed.connect(self.on_apply_theme)  # <- triggers full preview refresh
            self.group_rows[gid] = row
            line = QFrame()
            line.setFrameShape(QFrame.HLine)
            line.setFrameShadow(QFrame.Sunken)
            self.rows_layout.addWidget(line)
            self.rows_layout.addWidget(row)
            line = QFrame()
            line.setFrameShape(QFrame.HLine)
            line.setFrameShadow(QFrame.Sunken)
            self.rows_layout.addWidget(line)

        self.rows_layout.addStretch(1)

        self.rows_container.setObjectName("RowsPannel")
        self.rows_container.setStyleSheet("""
        #RowsPannel {
            background: rgba(255,255,255,0.8);
        }
        """)
        scroll = QScrollArea()
        scroll.setWidget(self.rows_container)
        scroll.setWidgetResizable(True)

        # Buttons
        MIN_BUTTON_WIDTH = 80
        
        self.discard_btn = QPushButton("Reset")
        self.discard_btn.setMinimumWidth(MIN_BUTTON_WIDTH)
        self.discard_btn.clicked.connect(self.on_theme_changed)
        self.new_btn = QPushButton("Save as..")
        self.new_btn.setMinimumWidth(MIN_BUTTON_WIDTH)
        self.new_btn.clicked.connect(self.new_theme)
        self.save_btn = QPushButton("Save")
        self.save_btn.setMinimumWidth(MIN_BUTTON_WIDTH)
        self.save_btn.clicked.connect(self.save_theme)
        self.del_btn = QPushButton("Delete")
        self.del_btn.setMinimumWidth(MIN_BUTTON_WIDTH)
        self.del_btn.clicked.connect(self.delete_theme)
        self.export_btn = QPushButton("Export")
        self.export_btn.clicked.connect(self.export)

        tools_btn_row = QHBoxLayout()
        #tools_btn_row.setSpacing(20)
        tools_btn_row.addStretch(1)
        tools_btn_row.addWidget(self.discard_btn)
        tools_btn_row.addWidget(self.export_btn)
        tools_btn_row.setAlignment(Qt.AlignJustify)
        

        theme_btn_row = QHBoxLayout()
        theme_btn_row.setSpacing(5)
        theme_btn_row.addStretch(1)
        theme_btn_row.addWidget(self.del_btn)
        theme_btn_row.addSpacing(30)
        theme_btn_row.addWidget(self.save_btn)
        theme_btn_row.addWidget(self.new_btn)
        theme_btn_row.setAlignment(Qt.AlignRight)

        right.addLayout(file_row)
        right.addLayout(theme_row)
        right.addLayout(theme_btn_row)
        right.addWidget(scroll, 1)
        right.addLayout(tools_btn_row)

        # Put panels into the splitter
        split.addWidget(left_panel)
        split.addWidget(right_panel)

        # Tell the splitter to give extra space to the left
        split.setStretchFactor(0, 1)  # left grows
        split.setStretchFactor(1, 0)  # right stays minimal

        # Keep the controls panel minimal: don't let it expand horizontally
        right_panel.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)

        # Create a tiny layout to host the splitter as the only child
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(split)

        # Load initial file
        self.file_combo.setCurrentIndex(0)
        file = self.file_combo.currentText()
        self.load_tree_if_needed(file)
        # Only refresh rows from theme if there is at least one theme selected
        if self.theme_combo.count() > 0:
            theme = self.theme_combo.currentText()
            if theme in self.color_maps:          # extra safety
                self.refresh_rows_from_state(theme)
                self.reload_theme(theme)

        self.file_combo.currentTextChanged.connect(self.on_file_changed)

    # ---------- State helpers ----------

    def _connect_row_signals(self):
        for row in self.group_rows.values():
            # avoid duplicate connections
            try:
                row.changed.disconnect(self.on_apply_theme)
            except TypeError:
                pass
            row.changed.connect(self.on_apply_theme)

    def _disconnect_row_signals(self):
        for row in self.group_rows.values():
            try:
                row.changed.disconnect(self.on_apply_theme)
            except TypeError:
                pass

    def load_tree_if_needed(self, title: str):
        path = SVG_FILES[title]
        if title in self.trees:
            return
        if not _ensure_file_exists(path):
            QMessageBox.warning(self, "Missing file", f"SVG file not found:\n{path}")
            minimal_svg = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"></svg>'
            tree = svg_tools.ET.ElementTree(svg_tools.ET.fromstring(minimal_svg))
            self.trees[title] = tree
            return
        self.trees[title] = svg_tools.parse_svg(path)

    def current_title(self) -> str:
        return self.file_combo.currentText()
    
    def current_theme(self) -> str:
        return self.theme_combo.currentText()

    def refresh_rows_from_state(self, title: str):
        cmap = self.color_maps[title]
        for gid, row in self.group_rows.items():
            vals = cmap.get(gid, {"fill": DEFAULT_HEX, "stroke": DEFAULT_HEX, "stroke-width": DEFAULT_WIDTH})
            row.set_values(vals.get("fill"), vals.get("stroke"), vals.get("stroke-width"))

    def collect_rows_into_state(self, title: str):
        for gid, row in self.group_rows.items():
            try:
                self.color_maps[title][gid] = row.values()
            except:
                self.color_maps[title] = {gid: row.values()}

    def _normalize_prop_key(self, k: str) -> str:
        # Treat stroke_width and stroke-width as the same
        k = (k or "").strip().lower()
        return "stroke-width" if k in ("stroke_width", "stroke-width") else k

    def _theme_from_memory(self, theme_name: str) -> dict:
        """
        Return a normalized copy of the theme in memory:
        {group: {'fill':..., 'stroke':..., 'stroke-width':...}}
        """
        mem = self.color_maps.get(theme_name, {})
        out = {}
        for gid in GROUPS:  # or just: for gid in mem
            vals = dict(mem.get(gid, {}))
            # normalize keys
            nvals = {}
            for k, v in vals.items():
                nvals[self._normalize_prop_key(k)] = (v or "").strip()
            # ensure keys exist
            nvals.setdefault("fill", "")
            nvals.setdefault("stroke", "")
            nvals.setdefault("stroke-width", "")
            out[gid] = nvals
        return out

    def _theme_from_file(self, theme_name: str) -> dict:
        """
        Load and normalize the theme file, if present.
        """
        path = THEME_FILES.get(theme_name)
        file_map = {}
        if path:
            try:
                parsed = _parse_theme_file_json(Path(path))
                for gid, vals in parsed.items():
                    nvals = {}
                    for k, v in (vals or {}).items():
                        nvals[self._normalize_prop_key(k)] = (v or "").strip()
                    nvals.setdefault("fill", "")
                    nvals.setdefault("stroke", "")
                    nvals.setdefault("stroke-width", "")
                    file_map[gid] = nvals
            except Exception:
                pass
        # make sure all groups exist in map
        for gid in GROUPS:
            file_map.setdefault(gid, {"fill": "", "stroke": "", "stroke-width": ""})
        return file_map

    def theme_diff_vs_file(self, theme_name: str) -> tuple[bool, dict]:
        """
        Compare current in-memory theme vs on-disk file.
        Returns (has_differences, diff_dict)
        diff_dict format: { group: { prop: (file_value, memory_value), ... }, ... }
        """
        mem = self._theme_from_memory(theme_name)
        fil = self._theme_from_file(theme_name)
        diff = {}
        for gid in sorted(set(mem.keys()) | set(fil.keys())):
            gdiff = {}
            for prop in ("fill", "stroke", "stroke-width"):
                mv = mem.get(gid, {}).get(prop, "")
                fv = fil.get(gid, {}).get(prop, "")
                if mv != fv:
                    gdiff[prop] = (fv, mv)
            if gdiff:
                diff[gid] = gdiff
        return (len(diff) > 0, diff)

    def has_unsaved_changes_any_theme(self) -> tuple[bool, str]:
        """
        True if any theme differs from its file on disk.
        """
        for theme_name in self.color_maps.keys():
            changed, _ = self.theme_diff_vs_file(theme_name)
            if changed:
                return True, theme_name
        return False, None

    # ---------- Validation ----------
    def _validate_all_rows(self) -> tuple[bool, str | None]:
        for gid, row in self.group_rows.items():
            ok, msg = row.validate()
            if not ok:
                return False, msg
        return True, None

    # ---------- Actions ----------

    def on_checkered_toggled(self, checked: bool):
        self.preview.setCheckered(checked)
        self.update_preview()
   
    def on_file_changed(self, title: str):
        self.load_tree_if_needed(title)
        #self.refresh_rows_from_state(title)
        self.update_preview()

    def on_theme_changed(self, theme: str):

        changed, theme_name = self.has_unsaved_changes_any_theme()
        if changed:
            # Ask for confirmation first
            resp = QMessageBox.question(
                self,
                "Unsaved Changes",
                "All the unsaved changes will be lost."
                "Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                try:
                    self.theme_combo.currentTextChanged.disconnect(self.on_theme_changed)
                except TypeError:
                    pass
                self.theme_combo.setCurrentText(theme_name)
                self.theme_combo.currentTextChanged.connect(self.on_theme_changed)
                return
            self.reload_theme(theme_name)
        self.reload_theme(theme)

    def reload_theme(self, theme: str):

        # Load theme from file
        theme_path = THEME_FILES.get(theme)
        if theme_path and Path(theme_path).exists():
            parsed = _parse_theme_file_json(Path(theme_path))
            # merge only known GROUPS/keys to keep it safe
            for gid in GROUPS:
                vals = self.color_maps[theme].setdefault(gid, {"fill": DEFAULT_HEX, "stroke": DEFAULT_HEX, "stroke-width": DEFAULT_WIDTH})
                if gid in parsed:
                    vals["fill"] = parsed[gid].get("fill", vals["fill"])
                    vals["stroke"] = parsed[gid].get("stroke", vals["stroke"])
                    vals["stroke-width"] = parsed[gid].get("stroke-width", vals["stroke-width"])

        self._disconnect_row_signals()
        self.refresh_rows_from_state(theme)
        self._connect_row_signals()
        self.update_preview()

    def on_apply_theme(self):
        if self.theme_combo.count() == 0:
            QMessageBox.warning(self, "No Themes Available", 'You need to create a color Theme first. Choose your colors and click on "New Theme"')
            return
        theme = self.current_theme()

        # Validate first: every picker must have value unless 'None' is checked
        ok, msg = self._validate_all_rows()
        if not ok:
            #QMessageBox.warning(self, "Invalid color", "Please fix color inputs.")
            return

        self.collect_rows_into_state(theme)
        self.update_preview()

    def save_theme(self):
        theme = self.current_theme()

        # Validate first: every picker must have value unless 'None' is checked
        ok, msg = self._validate_all_rows()
        if not ok:
            QMessageBox.warning(self, "Invalid color", msg or "Please fix color inputs.")
            return

        self.collect_rows_into_state(theme)
        self.update_preview()

        # Determine the theme's path. If it came from discovery, reuse. Otherwise create.
        theme_path = THEME_FILES.get(theme)
        if not theme_path:
            # If the name isn't mapped (e.g., user typed or future feature), save under THEME_DIR
            # new theme → default to JSON
            theme_path = str((THEME_DIR / f"{theme}.json").resolve())
            THEME_FILES[theme] = theme_path

        try:
            _write_theme_file_json(Path(theme_path), self.color_maps[theme], name=theme)
            QMessageBox.information(self, "Saved", f"Theme saved to:\n{theme_path}")
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    def delete_theme(self):
        # No themes available
        if self.theme_combo.count() == 0:
            QMessageBox.information(self, "Delete Theme", "There are no themes to delete.")
            return

        theme = self.current_theme()
        if not theme:
            QMessageBox.information(self, "Delete Theme", "Please select a theme to delete.")
            return

        # Confirm deletion
        resp = QMessageBox.question(
            self,
            "Delete Theme",
            f"Are you sure you want to delete the theme '{theme}'?\n\n"
            "This will remove its file from disk and from the list.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return

        # Try to delete the file on disk (if we know its path)
        err = None
        path_str = THEME_FILES.get(theme)
        if path_str:
            try:
                p = Path(path_str)
                if p.exists():
                    p.unlink()
            except Exception as e:
                err = str(e)

        # Remove from in-memory structures and UI
        self.color_maps.pop(theme, None)
        THEME_FILES.pop(theme, None)

        idx = self.theme_combo.findText(theme)
        if idx >= 0:
            self.theme_combo.removeItem(idx)

        # If we still have themes, switch to the current one and refresh;
        # otherwise reset rows to defaults and update preview.
        if self.theme_combo.count() > 0:
            new_theme = self.theme_combo.currentText()
            # reconnect/refresh just like on_theme_changed does
            self._disconnect_row_signals()
            self.refresh_rows_from_state(new_theme)
            self._connect_row_signals()
            self.update_preview()
        else:
            # No themes left: put rows back to defaults and redraw
            for gid, row in self.group_rows.items():
                row.set_values(DEFAULT_HEX, DEFAULT_HEX, DEFAULT_WIDTH)
            self.update_preview()

        # Notify user
        if err:
            QMessageBox.warning(
                self,
                "Deleted with warning",
                f"Theme '{theme}' was removed from the app, but its file "
                f"could not be deleted:\n{err}"
            )
        else:
            QMessageBox.information(self, "Theme Deleted", f"Theme '{theme}' was deleted.")

    def new_theme(self):
        # Start from current theme as a template
        src_theme = self.current_theme()

        # Validate first: every picker must have value unless 'None' is checked
        ok, msg = self._validate_all_rows()
        if not ok:
            QMessageBox.warning(self, "Invalid color", msg or "Please fix color inputs.")
            return

        # Ask for a new name; loop until unique or user cancels
        suggested = (src_theme + "_copy") if src_theme else "NewTheme"
        while True:
            name, accepted = QInputDialog.getText(
                self, "New Theme", "Enter a new theme name:", QLineEdit.Normal, suggested
            )
            if not accepted:
                return  # user cancelled
            name = name.strip()

            # Basic validation
            if not name:
                QMessageBox.warning(self, "Invalid name", "Theme name cannot be empty.")
                suggested = "NewTheme"
                continue
            if any(ch in name for ch in r'\/:*?"<>|'):
                QMessageBox.warning(
                    self, "Invalid name",
                    r"Theme name cannot contain \ / : * ? \" < > |"
                )
                suggested = name
                continue

            # Check for duplicate against in-memory map and on-disk file
            path_obj = (THEME_DIR / f"{name}.json")
            if name in THEME_FILES or path_obj.exists():
                QMessageBox.warning(
                    self, "Name already in use",
                    f"A theme named '{name}' already exists.\nChoose another name or Cancel."
                )
                suggested = name
                continue

            # Name is OK
            break


        # Create entry: copy values from source theme
        self.collect_rows_into_state(name)
        new_path = str(path_obj.resolve())
        THEME_FILES[name] = new_path

        # Add to combo and switch to it
        self.theme_combo.addItem(name)
        try:
            self.theme_combo.currentTextChanged.disconnect(self.on_theme_changed)
        except TypeError:
            pass
        self.theme_combo.setCurrentText(name)
        self.theme_combo.currentTextChanged.connect(self.on_theme_changed)

        # Persist to disk
        try:
            _write_theme_file_json(path_obj, self.color_maps[name],name=name)
            QMessageBox.information(self, "Theme created", f"New theme saved to:\n{new_path}")
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

        self.update_preview()
   
    def discard_changes(self):
        self.on_theme_changed(self.current_theme())
    
    def update_preview(self):

        file = self.current_title()
        style_map = {}
        theme = ""

        if self.theme_combo.count() > 0:    
            theme = self.current_theme()

            for gid, kv in self.color_maps[theme].items():
                fill = kv.get("fill", "").strip()
                stroke = kv.get("stroke", "").strip()
                stroke_w = kv.get("stroke-width", "1").strip()
                
                if fill or stroke or stroke_w:
                    style_map[gid] = {"fill": fill, "stroke": stroke, "stroke-width": stroke_w}

        try:
            if style_map:
                self.trees[file] = svg_tools.bulk_update_group_styles(self.trees[file], style_map)
            self.render_preview(file, theme)
        except Exception as e:
            QMessageBox.critical(self, "Apply failed", str(e))

    def _tree_to_bytes(self, title: str) -> bytes:
        tree = self.trees.get(title)
        if not tree:
            return b""
        return svg_tools.ET.tostring(tree.getroot(), encoding="utf-8", xml_declaration=True)

    def render_preview(self, title: str, theme: str):
        data = self._tree_to_bytes(title)
        if data:
            self.preview.set_svg_bytes(data)
            self.file_title.setText(f"Schematic: {title}")
            self.theme_title.setText(f"Theme: {theme}")

    def export(self):
        """
        Export into:
        <base>/theme configs/<theme>.txt
        <base>/<svg_title>/<svg_title>__<theme>.svg  (styles applied + label)
        """

        # Warn only if current theme differs from disk
        changed, _ = self.theme_diff_vs_file(self.current_theme())
        if changed:
            resp = QMessageBox.question(
                self,
                "Unsaved Changes",
                "All the unsaved changes will be lost. Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return

        # Pick base folder
        base_dir = QFileDialog.getExistingDirectory(
            self, "Choose export folder", str(Path.home())
        )
        if not base_dir:
            return
        base_dir = Path(base_dir)

        # Small helper to keep filenames safe
        def _safe(name: str) -> str:
            bad = r'\/:*?"<>|'
            out = "".join("_" if ch in bad else ch for ch in name.strip())
            return out.replace(" ", "_")

        try:
            # 1) Write all theme config files once
            cfg_dir = base_dir / "theme configs"
            cfg_dir.mkdir(parents=True, exist_ok=True)
            for theme_name in sorted(self.color_maps.keys()):
                outp = cfg_dir / f"{theme_name}.json"
                _write_theme_file_json(outp, self.color_maps[theme_name], name=theme_name)

            # 2) For each SVG: make a folder, then emit one file per theme
            for svg_title, svg_path in SVG_FILES.items():
                svg_folder = base_dir / _safe(svg_title)
                svg_folder.mkdir(parents=True, exist_ok=True)

                if not _ensure_file_exists(svg_path):
                    (svg_folder / f"{_safe(svg_title)}__MISSING.svg").write_text(
                        f"<!-- Missing source: {svg_path} -->\n", encoding="utf-8"
                    )
                    continue

                # For each theme, load fresh, apply styles, label, write
                for theme_name in sorted(self.color_maps.keys()):
                    tree = svg_tools.parse_svg(svg_path)

                    # Build style_map compatible with svg_tools
                    style_map = {}
                    for gid, kv in self.color_maps[theme_name].items():
                        fill = kv.get("fill", "").strip()
                        stroke = kv.get("stroke", "").strip()
                        stroke_w = kv.get("stroke-width", "").strip()

                        entry = {}
                        if fill != "": entry["fill"] = fill
                        if stroke != "": entry["stroke"] = stroke
                        if stroke_w != "":
                            entry["stroke-width"] = stroke_w
                            entry["stroke_width"] = stroke_w  # keep compatibility
                        if entry:
                            style_map[gid] = entry

                    if style_map:
                        tree = svg_tools.bulk_update_group_styles(tree, style_map)

                    # Add “Theme: <name>” label in top-left
                    svg_tools.add_top_left_label(
                        tree,
                        label_text=f"Theme: {theme_name}",
                        x=8, y=16, font_size="12",
                        fill="#000000", font_family="sans-serif",
                        element_id="__theme_label__"
                    )

                    data = svg_tools.ET.tostring(
                        tree.getroot(), encoding="utf-8", xml_declaration=True
                    )
                    out_name = f"{_safe(svg_title)}__{_safe(theme_name)}.svg"
                    (svg_folder / out_name).write_bytes(data)

            # Refresh preview (non-destructive)
            self.reload_theme(self.current_theme())
            QMessageBox.information(self, "Export complete", f"Exported to:\n{base_dir}")

        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))

def main():
    app = QApplication(sys.argv)
    w = SvgStylerApp()
    w.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()