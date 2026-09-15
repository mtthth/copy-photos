"""Copie les photos et vidéos d'une carte SD vers un disque dur en les
rangeant par date de prise de vue : YYYY/YYYY-MM/YYYY-MM-DD/ (photos),
YYYY/YYYY-MM/YYYY-MM-DD/video/ (vidéos).

Utilisation : lancer ce script (double-clic ou `python copy_photos.py`),
choisir le dossier source (carte SD) et le dossier de destination (disque
dur), puis cliquer sur "Copier". Les fichiers déjà copiés (contenu
identique) sont ignorés ; en cas de nom identique mais contenu différent,
un suffixe numérique est ajouté.
"""

from __future__ import annotations

import filecmp
import hashlib
import json
import os
import queue
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, NamedTuple, Optional

CONFIG_DIR_NAME = "copy_photos"
CONFIG_FILE_NAME = "config.json"

PHOTO_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".heic", ".heif",
    ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".orf", ".rw2",
    ".raf", ".dng", ".pef", ".raw",
}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mts", ".m2ts", ".3gp", ".mkv", ".wmv"}

# DateTimeOriginal, DateTimeDigitized, DateTime (dans cet ordre de préférence)
EXIF_DATETIME_TAGS = (36867, 36868, 306)
EXIF_DATE_FORMAT = "%Y:%m:%d %H:%M:%S"


def config_path() -> Path:
    """Emplacement du fichier mémorisant les derniers réglages utilisés."""
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / CONFIG_DIR_NAME / CONFIG_FILE_NAME
    return Path.home() / ".config" / CONFIG_DIR_NAME / CONFIG_FILE_NAME


def load_config(path: Optional[Path] = None) -> dict[str, Any]:
    try:
        with open(path or config_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(config: dict[str, Any], path: Optional[Path] = None) -> None:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def file_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime)


def get_photo_date(path: Path) -> datetime:
    """Date de prise de vue EXIF si disponible, sinon date de modification du fichier.

    Les formats RAW ne sont en général pas lisibles par Pillow : on retombe
    alors silencieusement sur la date du fichier.
    """
    try:
        from PIL import ExifTags, Image

        with Image.open(path) as img:
            exif = img.getexif()
            if exif:
                candidates = dict(exif)
                try:
                    candidates.update(exif.get_ifd(ExifTags.IFD.Exif))
                except Exception:
                    pass
                for tag in EXIF_DATETIME_TAGS:
                    raw = candidates.get(tag)
                    if raw:
                        try:
                            return datetime.strptime(raw, EXIF_DATE_FORMAT)
                        except ValueError:
                            continue
    except Exception:
        pass
    return file_mtime(path)


def get_video_date(path: Path) -> datetime:
    """Pas de lecture de métadonnées vidéo : on se base sur la date de modification."""
    return file_mtime(path)


def classify(path: Path) -> Optional[str]:
    ext = path.suffix.lower()
    if ext in PHOTO_EXTENSIONS:
        return "pic"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return None


def get_media_date(path: Path, kind: str) -> datetime:
    return get_photo_date(path) if kind == "pic" else get_video_date(path)


def destination_dir(dest_root: Path, date: datetime, kind: str) -> Path:
    day_dir = (
        dest_root
        / f"{date.year:04d}"
        / f"{date.year:04d}-{date.month:02d}"
        / f"{date.year:04d}-{date.month:02d}-{date.day:02d}"
    )
    return day_dir / "video" if kind == "video" else day_dir


class ResolvedDestination(NamedTuple):
    path: Path
    already_present: bool


def resolve_destination(src: Path, dest_dir: Path) -> ResolvedDestination:
    """Détermine où copier `src` dans `dest_dir`.

    `already_present` est vrai si un fichier identique existe déjà à
    destination (`path` pointe alors sur ce fichier existant, à ne pas
    recopier). Sinon `path` est le chemin où copier, avec un suffixe
    numérique (_2, _3, ...) si un fichier de même nom mais de contenu
    différent existe déjà.
    """
    target = dest_dir / src.name
    n = 1
    while target.exists():
        if filecmp.cmp(src, target, shallow=False):
            return ResolvedDestination(target, already_present=True)
        n += 1
        target = dest_dir / f"{src.stem}_{n}{src.suffix}"
    return ResolvedDestination(target, already_present=False)


def sha256sum(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_identical(a: Path, b: Path) -> bool:
    """Compare deux fichiers par hash SHA-256 (vérification avant suppression)."""
    return sha256sum(a) == sha256sum(b)


def iter_media_files(source: Path) -> Iterator[tuple[Path, str]]:
    for root, _dirs, files in os.walk(source):
        for name in files:
            path = Path(root) / name
            kind = classify(path)
            if kind:
                yield path, kind


@dataclass
class PreviewItem:
    path: Path
    kind: str
    date: datetime


def list_preview_items(source: Path) -> list[PreviewItem]:
    """Liste les photos/vidéos de `source` avec leur date calculée, triées
    chronologiquement, pour un aperçu avant copie."""
    items = [
        PreviewItem(path=path, kind=kind, date=get_media_date(path, kind))
        for path, kind in iter_media_files(source)
    ]
    items.sort(key=lambda item: (item.date, item.path.name))
    return items


def make_thumbnail(path: Path, size: tuple[int, int] = (120, 120)) -> Optional[Any]:
    """Miniature Pillow pour une photo, ou None si non générable (RAW/HEIC
    non lus par Pillow, fichier corrompu, ...)."""
    try:
        from PIL import Image

        with Image.open(path) as img:
            img.draft("RGB", size)
            img = img.convert("RGB")
            img.thumbnail(size)
            img.load()
            return img.copy()
    except Exception:
        return None


@dataclass
class CopyStats:
    total: int = 0
    copied: int = 0
    skipped_identical: int = 0
    deleted: int = 0
    errors: int = 0


def copy_media(
    source: Path,
    dest_root: Path,
    log: Callable[[str], None] = lambda msg: None,
    progress: Callable[[int, int], None] = lambda done, total: None,
    should_stop: Callable[[], bool] = lambda: False,
    delete_source: bool = False,
    exclude: Optional[set[Path]] = None,
) -> CopyStats:
    stats = CopyStats()
    files = [
        (path, kind)
        for path, kind in iter_media_files(source)
        if not exclude or path not in exclude
    ]
    stats.total = len(files)
    for i, (path, kind) in enumerate(files, start=1):
        if should_stop():
            log("Arrêt demandé.")
            break
        try:
            date = get_media_date(path, kind)
            target_dir = destination_dir(dest_root, date, kind)
            target_dir.mkdir(parents=True, exist_ok=True)
            resolved = resolve_destination(path, target_dir)
            if resolved.already_present:
                stats.skipped_identical += 1
                log(f"Déjà copié : {path.name}")
            else:
                shutil.copy2(path, resolved.path)
                stats.copied += 1
                log(f"Copié : {path.name} -> {resolved.path.relative_to(dest_root)}")

            if delete_source:
                if verify_identical(path, resolved.path):
                    path.unlink()
                    stats.deleted += 1
                    log(f"Source supprimée (hash vérifié) : {path.name}")
                else:
                    stats.errors += 1
                    log(f"ERREUR : hash différent après copie, source conservée : {path.name}")
        except Exception as exc:
            stats.errors += 1
            log(f"Erreur sur {path.name} : {exc}")
        progress(i, stats.total)
    return stats


def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk

    class ScrollableFrame(tk.Frame):
        """Frame défilable verticalement, utilisée pour la grille de miniatures."""

        def __init__(self, master: tk.Widget) -> None:
            super().__init__(master)
            canvas = tk.Canvas(self, highlightthickness=0)
            scrollbar = tk.Scrollbar(self, orient="vertical", command=canvas.yview)
            self.inner = tk.Frame(canvas)

            self.inner.bind(
                "<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all"))
            )
            canvas.create_window((0, 0), window=self.inner, anchor="nw")
            canvas.configure(yscrollcommand=scrollbar.set)

            canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

    class PreviewWindow(tk.Toplevel):
        """Fenêtre d'aperçu : grille de miniatures avec case à cocher par
        fichier pour choisir ce qui sera effectivement copié.

        La grille s'affiche immédiatement avec des icônes provisoires ; les
        miniatures sont générées dans un thread d'arrière-plan (lecture et
        redimensionnement d'image, potentiellement lents pour du RAW) puis
        appliquées au fur et à mesure, sans jamais bloquer l'interface.
        """

        COLUMNS = 4
        POLL_INTERVAL_MS = 50

        def __init__(
            self,
            master: tk.Widget,
            items: list,
            selection: dict,
        ) -> None:
            super().__init__(master)
            self.title(f"Aperçu avant copie ({len(items)} fichier(s))")
            self.geometry("820x600")
            self.selection = selection
            self._images: dict[int, Any] = {}
            self._thumb_labels: dict[int, tk.Label] = {}
            self._thumb_queue: "queue.Queue[tuple[int, Any]]" = queue.Queue()
            self._closed = False

            top = tk.Frame(self)
            top.pack(fill="x", padx=8, pady=4)
            tk.Button(top, text="Tout cocher", command=lambda: self._set_all(True)).pack(
                side="left"
            )
            tk.Button(
                top, text="Tout décocher", command=lambda: self._set_all(False)
            ).pack(side="left", padx=8)
            tk.Button(top, text="Valider la sélection", command=self.destroy).pack(
                side="right"
            )

            scrollable = ScrollableFrame(self)
            scrollable.pack(fill="both", expand=True, padx=8, pady=4)

            for i, item in enumerate(items):
                cell = tk.Frame(scrollable.inner, borderwidth=1, relief="groove")
                cell.grid(row=i // self.COLUMNS, column=i % self.COLUMNS, padx=4, pady=4)

                if item.kind == "pic":
                    thumb_label = tk.Label(cell, text="...", font=("", 32), width=6, height=3)
                    thumb_label.pack()
                    self._thumb_labels[i] = thumb_label
                else:
                    tk.Label(cell, text="🎞", font=("", 32), width=6, height=3).pack()

                tk.Label(cell, text=item.path.name, wraplength=140).pack()
                tk.Label(cell, text=item.date.strftime("%Y-%m-%d %H:%M")).pack()
                var = self.selection.setdefault(item.path, tk.BooleanVar(value=True))
                tk.Checkbutton(cell, text="Inclure", variable=var).pack()

            threading.Thread(
                target=self._load_thumbnails, args=(items,), daemon=True
            ).start()
            self.after(self.POLL_INTERVAL_MS, self._poll_thumbnails)

        def _load_thumbnails(self, items: list) -> None:
            """Exécuté dans un thread d'arrière-plan : ne touche à aucun
            widget Tk, se contente de préparer des images Pillow."""
            for i, item in enumerate(items):
                if self._closed:
                    return
                if item.kind == "pic":
                    self._thumb_queue.put((i, make_thumbnail(item.path)))

        def _poll_thumbnails(self) -> None:
            if self._closed:
                return
            try:
                while True:
                    i, thumb = self._thumb_queue.get_nowait()
                    label = self._thumb_labels.get(i)
                    if label is None:
                        continue
                    if thumb is None:
                        label.config(text="?")
                        continue
                    from PIL import ImageTk

                    photo = ImageTk.PhotoImage(thumb)
                    self._images[i] = photo
                    label.config(image=photo, text="")
            except queue.Empty:
                pass
            self.after(self.POLL_INTERVAL_MS, self._poll_thumbnails)

        def destroy(self) -> None:
            self._closed = True
            super().destroy()

        def _set_all(self, value: bool) -> None:
            for var in self.selection.values():
                var.set(value)

    class App(tk.Tk):
        def __init__(self) -> None:
            super().__init__()
            self.title("Copie photos SD -> disque dur")
            self.geometry("640x480")

            config = load_config()
            self.source_var = tk.StringVar(value=config.get("source", ""))
            self.dest_var = tk.StringVar(value=config.get("dest", ""))
            self.delete_var = tk.BooleanVar(value=config.get("delete_source", False))
            self._queue: "queue.Queue" = queue.Queue()
            self._stop_requested = False
            self._preview_source: Optional[Path] = None
            self._preview_selection: dict = {}

            self._build_widgets()
            self.source_var.trace_add("write", self._save_config)
            self.dest_var.trace_add("write", self._save_config)
            self.delete_var.trace_add("write", self._save_config)
            self.source_var.trace_add("write", self._clear_preview)
            self.after(100, self._poll_queue)

        def _clear_preview(self, *_args: object) -> None:
            self._preview_source = None
            self._preview_selection = {}

        def _save_config(self, *_args: object) -> None:
            save_config(
                {
                    "source": self.source_var.get(),
                    "dest": self.dest_var.get(),
                    "delete_source": self.delete_var.get(),
                }
            )

        def _build_widgets(self) -> None:
            pad = {"padx": 8, "pady": 4}

            frm = tk.Frame(self)
            frm.pack(fill="x", **pad)
            tk.Label(frm, text="Carte SD (source) :").grid(row=0, column=0, sticky="w")
            tk.Entry(frm, textvariable=self.source_var, width=60).grid(row=0, column=1, sticky="we")
            tk.Button(frm, text="Parcourir...", command=self._pick_source).grid(row=0, column=2)

            tk.Label(frm, text="Disque dur (destination) :").grid(row=1, column=0, sticky="w")
            tk.Entry(frm, textvariable=self.dest_var, width=60).grid(row=1, column=1, sticky="we")
            tk.Button(frm, text="Parcourir...", command=self._pick_dest).grid(row=1, column=2)
            frm.columnconfigure(1, weight=1)

            tk.Checkbutton(
                self,
                text="Supprimer les fichiers de la carte SD après copie (une fois le hash vérifié identique)",
                variable=self.delete_var,
            ).pack(fill="x", padx=8, anchor="w")

            btn_frm = tk.Frame(self)
            btn_frm.pack(fill="x", **pad)
            self.preview_btn = tk.Button(btn_frm, text="Aperçu...", command=self._preview)
            self.preview_btn.pack(side="left")
            self.start_btn = tk.Button(btn_frm, text="Copier", command=self._start)
            self.start_btn.pack(side="left", padx=8)
            self.stop_btn = tk.Button(btn_frm, text="Arrêter", command=self._stop, state="disabled")
            self.stop_btn.pack(side="left")

            self.progress = ttk.Progressbar(self, mode="determinate")
            self.progress.pack(fill="x", **pad)

            self.log_widget = scrolledtext.ScrolledText(self, state="disabled")
            self.log_widget.pack(fill="both", expand=True, **pad)

        def _pick_source(self) -> None:
            path = filedialog.askdirectory(title="Choisir la carte SD")
            if path:
                self.source_var.set(path)

        def _pick_dest(self) -> None:
            path = filedialog.askdirectory(title="Choisir le dossier de destination")
            if path:
                self.dest_var.set(path)

        def _log(self, msg: str) -> None:
            self._queue.put(("log", msg))

        def _progress(self, done: int, total: int) -> None:
            self._queue.put(("progress", (done, total)))

        def _should_stop(self) -> bool:
            return self._stop_requested

        def _preview(self) -> None:
            source = Path(self.source_var.get().strip())
            if not source.is_dir():
                messagebox.showerror("Erreur", "Choisis un dossier source valide (carte SD).")
                return
            self.preview_btn.config(state="disabled")
            self._log(f"Analyse de {source} pour l'aperçu...")
            threading.Thread(target=self._build_preview, args=(source,), daemon=True).start()

        def _build_preview(self, source: Path) -> None:
            items = list_preview_items(source)
            self._queue.put(("preview_ready", (source, items)))

        def _start(self) -> None:
            source = Path(self.source_var.get().strip())
            dest = Path(self.dest_var.get().strip())
            if not source.is_dir():
                messagebox.showerror("Erreur", "Choisis un dossier source valide (carte SD).")
                return
            if not dest.is_dir():
                messagebox.showerror("Erreur", "Choisis un dossier de destination valide.")
                return

            exclude = None
            if self._preview_source == source and self._preview_selection:
                exclude = {
                    path for path, var in self._preview_selection.items() if not var.get()
                }
                if len(exclude) == len(self._preview_selection):
                    messagebox.showerror(
                        "Erreur", "Aucun fichier sélectionné dans l'aperçu."
                    )
                    return

            delete_source = self.delete_var.get()
            if delete_source:
                confirmed = messagebox.askyesno(
                    "Confirmer la suppression",
                    "Chaque fichier sera supprimé de la carte SD juste après sa copie, "
                    "une fois vérifié identique par hash SHA-256. Cette suppression est "
                    "irréversible. Continuer ?",
                )
                if not confirmed:
                    return

            self._stop_requested = False
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
            self.progress.config(value=0, maximum=100)
            self.log_widget.config(state="normal")
            self.log_widget.delete("1.0", "end")
            self.log_widget.config(state="disabled")

            threading.Thread(
                target=self._run_copy, args=(source, dest, delete_source, exclude), daemon=True
            ).start()

        def _stop(self) -> None:
            self._stop_requested = True
            self.stop_btn.config(state="disabled")

        def _run_copy(
            self, source: Path, dest: Path, delete_source: bool, exclude: Optional[set]
        ) -> None:
            self._log(f"Analyse de {source} ...")
            stats = copy_media(
                source,
                dest,
                log=self._log,
                progress=self._progress,
                should_stop=self._should_stop,
                delete_source=delete_source,
                exclude=exclude,
            )
            self._queue.put(("done", stats))

        def _poll_queue(self) -> None:
            try:
                while True:
                    kind, payload = self._queue.get_nowait()
                    if kind == "log":
                        self.log_widget.config(state="normal")
                        self.log_widget.insert("end", payload + "\n")
                        self.log_widget.see("end")
                        self.log_widget.config(state="disabled")
                    elif kind == "progress":
                        done, total = payload
                        self.progress.config(maximum=max(total, 1), value=done)
                    elif kind == "preview_ready":
                        source, items = payload
                        self.preview_btn.config(state="normal")
                        if not items:
                            messagebox.showinfo(
                                "Aperçu", "Aucune photo ou vidéo trouvée sur la source."
                            )
                        else:
                            self._preview_source = source
                            self._preview_selection = {
                                item.path: tk.BooleanVar(value=True) for item in items
                            }
                            self._log(f"Aperçu : {len(items)} fichier(s) trouvé(s).")
                            PreviewWindow(self, items, self._preview_selection)
                    elif kind == "done":
                        stats: CopyStats = payload
                        self._log(
                            f"Terminé : {stats.copied} copié(s), "
                            f"{stats.skipped_identical} déjà présent(s), "
                            f"{stats.deleted} supprimé(s) de la source, "
                            f"{stats.errors} erreur(s) sur {stats.total} fichier(s)."
                        )
                        self.start_btn.config(state="normal")
                        self.stop_btn.config(state="disabled")
            except queue.Empty:
                pass
            self.after(100, self._poll_queue)

    App().mainloop()


if __name__ == "__main__":
    run_gui()
