# copy-photos

Script Windows (Python) qui copie les photos et vidéos d'une carte SD vers
un disque dur en les rangeant par date de prise de vue :

```
<destination>/YYYY/YYYY-MM/YYYY-MM-DD/pic/    -> photos
<destination>/YYYY/YYYY-MM/YYYY-MM-DD/video/  -> vidéos
```

La date utilisée est la date EXIF `DateTimeOriginal` de la photo quand
elle est disponible (cas des JPEG), sinon la date de modification du
fichier (cas des RAW non lus par Pillow, et de toutes les vidéos).

Les fichiers déjà copiés (contenu identique) sont détectés et ignorés au
second passage. En cas de fichier de même nom mais de contenu différent,
un suffixe est ajouté (`IMG_0001_2.jpg`).

## Installation

1. Installer [Python 3](https://www.python.org/downloads/) (cocher "Add
   Python to PATH" pendant l'installation).
2. Ouvrir une invite de commandes dans ce dossier et installer la seule
   dépendance :

   ```
   pip install -r requirements.txt
   ```

## Utilisation

Double-cliquer sur `copy_photos.py`, ou lancer :

```
python copy_photos.py
```

Une fenêtre s'ouvre : choisir le dossier de la carte SD, le dossier de
destination sur le disque dur, puis cliquer sur "Copier". La progression
et le journal des fichiers copiés/ignorés s'affichent dans la fenêtre.

## Créer un .exe autonome (optionnel)

Pour éviter d'avoir Python à installer sur chaque machine :

```
pip install pyinstaller
pyinstaller --onefile --windowed --name copy_photos copy_photos.py
```

L'exécutable est généré dans `dist\copy_photos.exe`.

## Tests

```
pip install pytest
pytest
```
