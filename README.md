# Dark Factory — Stage 1 : réservation anti-collision

Service FastAPI + SQLite qui rend la **double réservation physiquement impossible**.

La table `reservations` porte une contrainte `UNIQUE(table_id, start_time)`.
Chaque transaction démarre en `BEGIN IMMEDIATE` : SQLite prend un verrou
d'écriture *avant* l'INSERT. Résultat mesurable : **20 POST simultanés = 1 succès, 19 rejets**.

- Horodatages stockés en **UTC**
- Créneaux fixes de **30 minutes**
- Idempotence via le header `X-Idempotency-Key`
- Écoute sur le **port 8000**

## Lancer en local (Windows PowerShell)

```powershell
cd D:\dark-factory-serveur
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Puis ouvrir http://127.0.0.1:8000

Dans un second terminal :

```powershell
.\.venv\Scripts\Activate.ps1
pytest test_concurrency.py -v
```

## Lancer en local (Linux / macOS)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
# autre terminal
pytest test_concurrency.py -v
```

## Docker — build une fois, exécuter sans réseau

Le conteneur est autonome (SQLite embarqué, aucune dépendance runtime externe).
Après le build, **coupez le réseau** : le service continue de répondre.

```powershell
docker build -t dark-factory-reservations .
docker run --rm --network none -p 8000:8000 dark-factory-reservations
```

`--network none` est le mode juges : aucun DNS, aucun egress.
L'UI reste disponible sur http://127.0.0.1:8000 (le mapping de port est local).

Vérification rapide sans navigateur :

```powershell
curl http://127.0.0.1:8000/health
```

## API

| Méthode | Chemin | Rôle |
|---|---|---|
| `POST` | `/reservations` | Crée une réservation |
| `GET` | `/reservations` | Liste |
| `DELETE` | `/reservations/{id}` | Annule |
| `POST` | `/stress-test` | 20 INSERT concurrents côté serveur |

Exemple :

```http
POST /reservations
X-Idempotency-Key: 8f1c2e3a-1111-2222-3333-444455556666
Content-Type: application/json

{
  "table_id": 7,
  "start_time": "2026-12-24T19:00:00Z",
  "customer_name": "Ada Lovelace",
  "party_size": 2
}
```

- `201` : créée
- `200` : rejeu de la même clé d'idempotence
- `409` : créneau déjà pris (`UNIQUE`)
- `422` : créneau hors grille 30 min UTC

## UI — bouton rouge

**🔥 Lancer le Test de Concurrence** envoie 20 `POST /reservations` en parallèle
(JavaScript `Promise.all`, clés d'idempotence distinctes, même table, même slot).
L'écran affiche en direct :

`20 requêtes envoyées. 1 succès, 19 rejets. La double réservation est physiquement impossible !`
