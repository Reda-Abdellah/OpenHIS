# Démarrage rapide — d'un VPS 8 Go nu à un hôpital qui tourne en ~30 minutes

Ce guide part d'un serveur Ubuntu vierge et aboutit à une plateforme OpenHIS
fonctionnelle : portail, dossier patient (OpenMRS), laboratoire (OpenELIS),
index patient maître, passerelle HL7 et tableau de bord d'administration.

Budget temps indicatif :

| Étape | Durée |
|---|---|
| Prérequis système (Docker + Python 3.11) | ~5 min |
| Clonage + installation de la CLI `opm` | ~3 min |
| Assistant `opm init` (secrets, profils, rendu infra) | ~2 min |
| Premier `make up` (téléchargement des images) | ~15–20 min |
| Vérification (`make health`, `make e2e`) | ~2 min |

---

## 1. Prérequis

| Composant | Version minimale |
|---|---|
| Ubuntu (ou équivalent) | 22.04 |
| Docker Engine | 24.x |
| Plugin Docker Compose | 2.20 |
| Python | 3.11 (+ paquet `python3.11-venv`) |
| RAM disponible | 8 Go (voir le tableau des profils ci-dessous) |
| Disque disponible | 15 Go |

```bash
# Python 3.11 + venv (Ubuntu/Debian)
sudo apt install python3.11 python3.11-venv
```

Pour Docker, suivez la documentation officielle
(<https://docs.docker.com/engine/install/>) et vérifiez :

```bash
docker --version            # >= 24.x
docker compose version      # >= 2.20
```

---

## 2. Cloner et installer la CLI

```bash
# 1. Cloner le dépôt
git clone https://github.com/Reda-Abdellah/OpenHIS.git
cd OpenHIS

# 2. Environnement virtuel Python 3.11
python3.11 -m venv venv_openhis
source venv_openhis/bin/activate

# 3. Installer les dépendances de la CLI, le SDK et la CLI elle-même
pip install -r platform/requirements.txt
pip install -e libs/openhis_sdk
pip install -e platform

# 4. Vérifier l'installation
opm --version
opm --help
```

> `opm` est désormais disponible comme commande installée. L'invocation
> `python platform/opm.py …` reste équivalente.

---

## 3. Initialiser la plateforme — `opm init`

L'assistant de premier lancement fait tout le travail de configuration :

- choix des profils à activer (avec estimation RAM) ;
- génération de **secrets forts** pour chaque mot de passe et secret client
  Keycloak (rien ne reste à la valeur sentinelle `CHANGE_ME_BEFORE_DEPLOY`) ;
- écriture du fichier `.env` ;
- rendu de `infra/nginx/nginx.conf` et des gabarits d'infrastructure
  porteurs de secrets (realm Keycloak, …) ;
- validation du `.env` écrit sur disque (valeurs vides, faibles ou
  sentinelles ⇒ échec explicite).

```bash
# Mode interactif (recommandé)
opm init

# Mode non interactif : tous les secrets sont auto-générés puis validés
opm init --non-interactive
```

Pour fournir certains secrets vous-même en mode non interactif, utilisez les
variables d'environnement `OPENHIS_POSTGRES_PASS`, `OPENHIS_ADMIN_PASS`,
`OPENHIS_KEYCLOAK_PASS`, `OPENHIS_KEYCLOAK_SECRET` (ou les drapeaux
`--postgres-pass`, `--admin-pass`, `--keycloak-pass`, `--keycloak-secret`).

> **Alternative manuelle** : `cp .env.example .env` puis édition à la main.
> Dans ce cas vous DEVEZ remplacer toutes les valeurs
> `CHANGE_ME_BEFORE_DEPLOY` vous-même — `opm init` est la voie sûre.

Les secrets générés ne sont jamais affichés ; ils sont consultables dans le
`.env` (par exemple `grep ADMIN_PASS .env` pour le mot de passe du tableau
de bord d'administration).

---

## 4. Choisir les profils selon votre RAM

Les profils sont des surcouches Docker Compose : un profil non activé ne
consomme rien.

| Profil | Services ajoutés | RAM estimée |
|---|---|---|
| `base` (toujours actif) | postgres, redis, nginx, keycloak, mpi, integration-hub, hl7, admin | ~512 Mo |
| `emr` | OpenMRS O3 (dossier patient + API FHIR R4) | +2 Go |
| `laboratory` | OpenELIS Global 2 (laboratoire + API FHIR R4) | +1 Go |
| `erp` | Odoo (pharmacie, achats, facturation) | +1 Go |
| `imaging` | Orthanc PACS + visionneuse OHIF + RIS + IA | +1,5 Go |
| `analytics` | Tableau de bord analytique + portail patient | +256 Mo |

**Sur un VPS 8 Go**, la combinaison recommandée est :

```
OPENHIS_PROFILES=emr,laboratory,analytics
```

soit ~3,75 Go pour les services, le reste pour l'OS, Docker et la marge de
fonctionnement. Ajouter `imaging` (+1,5 Go) ou `erp` (+1 Go) sature le
budget : prévoyez 16 Go pour la pile complète.

`opm init` écrit votre choix dans `.env` ; vous pouvez le modifier à tout
moment avec `opm enable` / `opm disable`.

---

## 5. Démarrer et vérifier

```bash
# Démarrer (les profils actifs sont lus depuis .env)
make up
# ou en forçant les profils pour cette invocation :
OPENHIS_PROFILES=emr,laboratory,analytics make up
```

Le premier démarrage télécharge les images (~15–20 min selon le réseau).
Ensuite :

```bash
make ps          # conteneurs et état
make health      # statut healthy/unhealthy de chaque service
```

Attendez que les services affichent `(healthy)` — OpenMRS est le plus long
(plusieurs minutes au premier lancement, le temps d'initialiser sa base).

### URLs par défaut

Tout passe par nginx sur le **port 80**.

| Service | URL |
|---|---|
| Portail | `http://localhost/` |
| Tableau de bord admin | `http://localhost/admin/` |
| Keycloak | `http://localhost/keycloak/` |
| MPI (index patient) | `http://localhost/mpi/` |
| Integration Hub (docs API) | `http://localhost/integration-hub/docs` |
| Passerelle HL7 | `http://localhost/hl7/` |
| OpenMRS O3 (profil `emr`) | `http://localhost/openmrs/spa/` |
| OpenELIS (profil `laboratory`) | `http://localhost/OpenELIS-Global/` |
| Analytics (profil `analytics`) | `http://localhost/analytics/` |
| Portail patient (profil `analytics`) | `http://localhost/patient-portal/` |

Identifiants : ceux de votre `.env` (`ADMIN_USER` / `ADMIN_PASS` pour le
tableau de bord ; `KEYCLOAK_ADMIN` / `KEYCLOAK_ADMIN_PASSWORD` pour
Keycloak).

> Le port MLLP **2575** (HL7 v2) n'est **pas** publié sur l'hôte par défaut :
> les flux HL7 restent internes au réseau Docker. Pour l'exposer (pare-feu
> restreint ou proxy TLS obligatoire), ajoutez la surcouche opt-in
> `compose/overrides/mllp-public.yml` — lisez d'abord l'avertissement de
> sécurité en tête de ce fichier.

### Validation de bout en bout

Les scénarios V&V (identité patient, flux labo, RBAC, plan admin, HL7, …)
s'exécutent contre la pile vivante en ~13 secondes :

```bash
pip install pytest httpx     # une seule fois, dans le venv
make e2e                     # ou : pytest tests/e2e --e2e -v
```

Lisez la ligne de synthèse `N passed, M xfailed, K skipped` : des `xfailed`
sont des défauts connus et tracés — seul un `FAILED` est une régression.

---

## 6. Exploitation au quotidien

```bash
opm status                       # profils actifs + santé des services
opm enable imaging               # activer un profil (régénère nginx, démarre)
opm disable erp                  # désactiver un profil
opm upgrade emr                  # mise à niveau glissante, service par service

make logs-service SVC=admin      # suivre les journaux d'un service
make restart SVC=mpi             # redémarrer un service
make down                        # arrêter la pile (conserve les volumes)
```

### Sauvegardes

```bash
make backup                              # dump à chaud : bases + volumes nommés
make backup ARGS="--dry-run"             # afficher le plan sans rien exécuter
make backup ARGS="--cold --keycloak-export"   # arrêt complet + export du realm
make restore BACKUP=backups/<horodatage>      # restauration (confirme avant d'écraser)
```

Les sauvegardes atterrissent dans `./backups/<horodatage-UTC>/` avec un
manifeste sha256 — détails dans `scripts/README.md`.

---

## 7. Passage en production

La pile par défaut est une pile de **développement** (Keycloak en
`start-dev`, HTTP en clair). Avant toute donnée patient réelle :

1. **Appliquer la surcouche production** — Keycloak en mode `start` + TLS,
   suppression des ports de débogage :

   ```bash
   docker compose -f compose/base.yml \
     -f compose/overrides/production.yml up -d
   ```

   Elle exige `KEYCLOAK_HOSTNAME` dans l'environnement et un certificat TLS
   monté dans Keycloak.

2. **Certificats TLS** — pour tester la surcouche en local, générez un
   certificat auto-signé :

   ```bash
   bash scripts/gen_dev_certs.sh    # écrit infra/ssl/tls.{crt,key}
   ```

   ⚠ Auto-signé = développement uniquement. En production, utilisez des
   certificats émis par une AC.

3. **Dérouler la checklist de sécurité** —
   [`docs/explaining_the_project/security.md`](explaining_the_project/security.md) :
   secrets, durcissement Keycloak, MLLP/HL7, Redis, TLS/réseau, sauvegardes.

---

## En cas de problème

- `make health` montre un service `unhealthy` → `make logs-service SVC=<nom>`.
- `.env` incomplet ou secrets faibles → relancez `opm init` (il revalide le
  fichier écrit) .
- Référence complète des commandes, URLs et API : [`README.md`](../README.md).
