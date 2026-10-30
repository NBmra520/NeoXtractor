"""Dialog that wires the Wwise event hash index onto the opened package."""

from __future__ import annotations

import os

from PySide6 import QtWidgets

from core.logger import get_logger
from core.utils import get_application_path
from id5mapping.wwise import (
    WwiseEventIndex,
    build_hash_index,
    bundled_index_path,
    merge_index,
    mount_archive_banks,
    save_index,
)
from id5mapping.wwise.event_index import _thy_pair_from_thy
from id5mapping.wwise.thy import guess_thy_path


class WwiseMappingWindow(QtWidgets.QDialog):
    """Pick the event hash index / .thy and mount the banks of the package.

    The index only maps `FNV-1(event name) -> name`. Which events exist, which
    media they use and where those media live is read from the banks inside the
    loaded package and from the local `.thy` + `.idx`.
    """

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        idx_path: str | None = None,
        archive_file=None,
    ):
        super().__init__(parent)

        self.setWindowTitle("Wwise Event Mapping")
        self.resize(760, 300)

        self.index: WwiseEventIndex | None = None
        self.idx_path = idx_path
        self.archive_file = archive_file

        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()

        bundled = bundled_index_path()
        self.index_path = QtWidgets.QLineEdit(bundled or "")
        self.index_path.setPlaceholderText(
            "Bundled hash index (id5mapping/wwise/wwise_event_hash_index.json)"
        )
        form.addRow("Event hash index", self._with_browse(self.index_path, "Open event hash index"))

        self.thy_base = QtWidgets.QLineEdit()
        self.thy_base.setPlaceholderText(
            "base name table, optional (e.g. <root>/thd/wwise.thy)"
        )
        form.addRow("Name table (base)", self._with_browse(self.thy_base, "Open .thy"))

        self.thy_hot = QtWidgets.QLineEdit()
        self.thy_hot.setPlaceholderText(
            "hot-update name table, optional (e.g. <root>/Documents/thd/wwise.thy)"
        )
        form.addRow("Name table (hot update)", self._with_browse(self.thy_hot, "Open .thy"))

        guessed = guess_thy_path(idx_path or "")
        if guessed:
            pair = _thy_pair_from_thy(guessed)
            self.thy_base.setText(pair[0] if pair else guessed)
            if len(pair) > 1:
                self.thy_hot.setText(pair[-1])

        layout.addLayout(form)

        self.status = QtWidgets.QLabel("No mapping built.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        build_button = buttons.addButton("Reload", QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)
        build_button.clicked.connect(self.build_mapping)
        events_button = buttons.addButton("Rebuild index...", QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)
        events_button.clicked.connect(self.rebuild_index)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if bundled:
            self.build_mapping()

    def _with_browse(self, line_edit: QtWidgets.QLineEdit, title: str) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(line_edit)

        button = QtWidgets.QPushButton("Browse...")

        def browse():
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, title, "", "Supported Files (*.py *.json *.gz *.thy);;All Files (*)"
            )
            if path:
                line_edit.setText(path)

        button.clicked.connect(browse)
        row.addWidget(button)
        return container

    def build_mapping(self) -> bool:
        """Load the hash index, mount the package banks and report coverage."""
        index = WwiseEventIndex()
        try:
            path = self.index_path.text().strip()
            if path:
                index.load_hash_index(path)
            elif not index.load_bundled_index():
                self._error(
                    "Bundled event hash index not found. Select an index file or use Rebuild index."
                )
                return False

            base_thy = self.thy_base.text().strip()
            hot_thy = self.thy_hot.text().strip()
            if base_thy or hot_thy:
                index.load_thy_files([p for p in (base_thy, hot_thy) if p])
            else:
                index.autodetect_thy(self.idx_path)

            mounted = 0
            if self.archive_file is not None:
                mounted = mount_archive_banks(self.archive_file, index)
        except Exception as error:  # noqa: BLE001
            get_logger().exception("Wwise mapping failed")
            self._error(f"Failed to build mapping: {error}")
            return False

        self.index = index
        self.status.setText(
            f"Event name hashes {len(index.name_by_hash)}; events mounted from this package {mounted}, "
            f"media short IDs {len(index.short_id_events)}, "
            f"package entries mapped {len(index.md5_events)}, "
            f"name table keys {index.thy_key_count}."
            + ("" if index.thy_path else " Warning: wwise.thy not found, external wem cannot be resolved.")
        )
        return True

    def rebuild_index(self) -> None:
        """Recompute the event hash index from an event name list."""
        events_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select the event name list", "", "Event List (*.py *.json);;All Files (*)"
        )
        if not events_path:
            return

        default_out = os.path.join(
            get_application_path(), "id5mapping", "wwise", "wwise_event_hash_index.json"
        )
        out_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save event hash index", default_out, "JSON (*.json *.json.gz)"
        )
        if not out_path:
            return

        try:
            index = build_hash_index(events_path)
            index = merge_index(index, out_path)
            save_index(index, out_path)
        except Exception as error:  # noqa: BLE001
            get_logger().exception("Wwise hash index rebuild failed")
            self._error(f"Failed to rebuild index: {error}")
            return

        self.index_path.setText(out_path)
        self.status.setText(
            f"Hash index written to {out_path}: {index['stats']['events']} events."
        )
        self.build_mapping()

    def apply(self) -> None:
        if self.index is None and not self.build_mapping():
            return
        self.accept()

    def _error(self, message: str) -> None:
        QtWidgets.QMessageBox.critical(self, "Wwise Event Mapping", message)
        self.status.setText(message)
