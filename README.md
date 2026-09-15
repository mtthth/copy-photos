# copy-photos

Script Windows (Python) qui copie les photos et vidéos d'une carte SD vers
un disque dur en les rangeant par date de prise de vue :

```
<destination>/YYYY/YYYY-MM/YYYY-MM-DD/         -> photos
<destination>/YYYY/YYYY-MM/YYYY-MM-DD/video/   -> vidéos
```

La date utilisée est la date EXIF `DateTimeOriginal` de la photo quand
elle est disponible (cas des JPEG), sinon la date de modification du
fichier (cas des RAW non lus par Pillow, et de toutes les vidéos).

Les fichiers déjà copiés (contenu identique) sont détectés et ignorés au
second passage. En cas de fichier de même nom mais de contenu différent,
un suffixe est ajouté (`IMG_0001_2.jpg`).

Option "Supprimer les fichiers de la carte SD après copie" : après chaque
copie (ou pour un fichier déjà présent à l'identique), le fichier source
et le fichier de destination sont comparés par hash SHA-256 ; le fichier
n'est supprimé de la source que si les deux hashs correspondent. En cas de
désaccord, la source est conservée et une erreur est journalisée. Cette
option est décochée par défaut et demande une confirmation avant de
lancer la copie, la suppression étant irréversible.

Les dossiers source/destination et l'état de cette case sont mémorisés
automatiquement dans `%APPDATA%\copy_photos\config.json` (ou
`~/.config/copy_photos/config.json` hors Windows) et repris au lancement
suivant.

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

Le bouton "Aperçu..." ouvre une fenêtre avec une grille de miniatures de
toutes les photos et vidéos trouvées sur la carte SD (triées par date),
chacune avec une case "Inclure" cochée par défaut. Décocher les fichiers à
ne pas copier, valider, puis cliquer sur "Copier" : seuls les fichiers
encore cochés sont copiés. Cet aperçu n'est pris en compte que s'il a été
généré pour le dossier source actuellement sélectionné ; changer de
dossier source l'invalide.

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
