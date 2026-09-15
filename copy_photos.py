"""Copie les photos et vidéos d'une carte SD vers un disque dur en les
rangeant par date de prise de vue : YYYY/YYYY-MM/YYYY-MM-DD/pic (ou /video).

Utilisation : lancer ce script (double-clic ou `python copy_photos.py`),
choisir le dossier source (carte SD) et le dossier de destination (disque
dur), puis cliquer sur "Copier". Les fichiers déjà copiés (contenu
identique) sont ignorés ; en cas de nom identique mais contenu différent,
un suffixe numérique est ajouté.
"""

from __future__ import annotations

import filecmp
import hashlib
import os
import queue
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, NamedTuple, Optional

PHOTO_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".heic", ".heif",
    ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".orf", ".rw2",
    ".raf", ".dng", ".pef", ".raw",
}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mts", ".m2ts", ".3gp", ".mkv", ".wmv"}

# DateTimeOriginal, DateTimeDigitized, DateTime (dans cet ordre de préférence)
EXIF_DATETIME_TAGS = (36867, 36868, 306)
EXIF_DATE_FORMAT = "%Y:%m:%d %H:%M:%S"


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
    return (
        dest_root
        / f"{date.year:04d}"
        / f"{date.year:04d}-{date.month:02d}"
        / f"{date.year:04d}-{date.month:02d}-{date.day:02d}"
        / kind
    )


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
) -> CopyStats:
    stats = CopyStats()
    files = list(iter_media_files(source))
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

    class App(tk.Tk):
        def __init__(self) -> None:
            super().__init__()
            self.title("Copie photos SD -> disque dur")
            self.geometry("640x480")

            self.source_var = tk.StringVar()
            self.dest_var = tk.StringVar()
            self.delete_var = tk.BooleanVar(value=False)
            self._queue: "queue.Queue" = queue.Queue()
            self._stop_requested = False

            self._build_widgets()
            self.after(100, self._poll_queue)

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
            self.start_btn = tk.Button(btn_frm, text="Copier", command=self._start)
            self.start_btn.pack(side="left")
            self.stop_btn = tk.Button(btn_frm, text="Arrêter", command=self._stop, state="disabled")
            self.stop_btn.pack(side="left", padx=8)

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

        def _start(self) -> None:
            source = Path(self.source_var.get().strip())
            dest = Path(self.dest_var.get().strip())
            if not source.is_dir():
                messagebox.showerror("Erreur", "Choisis un dossier source valide (carte SD).")
                return
            if not dest.is_dir():
                messagebox.showerror("Erreur", "Choisis un dossier de destination valide.")
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
                target=self._run_copy, args=(source, dest, delete_source), daemon=True
            ).start()

        def _stop(self) -> None:
            self._stop_requested = True
            self.stop_btn.config(state="disabled")

        def _run_copy(self, source: Path, dest: Path, delete_source: bool) -> None:
            self._log(f"Analyse de {source} ...")
            stats = copy_media(
                source,
                dest,
                log=self._log,
                progress=self._progress,
                should_stop=self._should_stop,
                delete_source=delete_source,
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
