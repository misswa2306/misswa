# RESTLESS STUDIO — Site statique

Fichiers:
- `index.html` — page principale (déjà stylée et fonctionnelle localement).

Comment tester localement:

1. Ouvrir `mix-site/index.html` dans votre navigateur (double-clic).

Optionnel — servir localement via un serveur (recommandé pour certaines fonctionnalités):

- Avec Python 3 (dans le dossier `mix-site`):

```bash
python -m http.server 8000
```

Puis ouvrir `http://localhost:8000`.

- Avec `npx serve`:

```bash
npx serve .
```

Remarques:
- Le formulaire est simulé côté client; il n'envoie pas de requête serveur. Si vous voulez un back-end (email, stockage), dites-moi quel type et je l'ajoute.
 
Contact exemple dans le formulaire: +1 514 123 4567 (format nord-américain).
