# OpenHIS — Roadmap & Positionnement

*Rédigé le 2026-06-12, à l'issue de l'audit complet du projet. Complète
les plans d'exécution existants (`docs/task_planning/`) : ce document ne
liste pas des tâches, il définit où le projet doit aller pour avoir le
plus d'impact et ce qui le différencie durablement.*

---

## 1. Où est l'impact

Le marché des hôpitaux à ressources limitées (Afrique francophone, Asie du
Sud-Est, structures privées de taille moyenne) n'a aujourd'hui que trois
options réalistes :

1. **Bahmni** — monolithe EMR-centric, intégration par Atom Feeds, fortement
   couplé ; remplacer un composant (le LIS, la facturation) est un projet en soi.
2. **Des déploiements artisanaux** OpenMRS + OpenELIS + autre, recâblés à la
   main à chaque site, sans bus d'événements ni identité patient unifiée.
3. **Des HIS propriétaires** hors budget.

OpenHIS occupe un créneau qu'aucun de ces acteurs ne tient : **la couche
plateforme** — identité (MPI), flux (FHIR R4 + Redis Streams), déploiement
(profils + OPM), exploitation (SSO, audit, health) — en laissant le domaine
clinique aux applications matures. C'est la thèse des trois principes de
`concepts.md`, et l'audit confirme qu'elle est tenue dans le code.

## 2. Ce qui différencie OpenHIS (à défendre en priorité)

| Différenciateur | Pourquoi c'est défendable | Risque si négligé |
|---|---|---|
| **Profils composables + OPM** (`opm enable laboratory`) | Personne d'autre ne fait du « Docker Compose profilé » pour l'hôpital ; le coût d'installation passe de semaines à heures | Si l'e2e CI ne reste pas vert, la promesse « une commande » meurt |
| **MPI léger + matching** | OpenEMPI est lourd, Bahmni n'en a pas ; un MPI Postgres avec matching phonétique/diacritiques couvre 90 % des besoins réels | La qualité du matching doit être mesurée (corpus de noms locaux) sinon la confiance clinique s'effondre |
| **Contrats explicites** (adapter/service/profile + V&V exécutable) | Permet à des tiers d'ajouter un module sans connaître tout le système | La dérive docs/code (constatée à l'audit) doit être tenue par la CI |

## 3. Roadmap proposée

### Horizon 1 — « Pilotable » (1–2 mois)
Objectif : un site pilote peut tourner sans qu'un développeur soit de garde.

- [x] Finir la Phase 1 du REMEDIATION_PLAN — T-01 à T-10 livrées :
  credentials externalisés, guard nginx signé (RS256), socket-proxy,
  Redis AUTH, realm templating + secrets générés par `opm init`, lockdown
  ai-controller/admin/RIS/MPI/hub/simulateur, MLLP interne par défaut.
  *(livré 2026-06-12 — validation live e2e en attente)*
- [x] Événements `lab_order.routed` / `lab_result.ready` publiés par le hub
  (OBJ 3) — condition explicite de l'ADR-0004 ; contrats de payload
  verrouillés par tests unitaires.
  *(livré 2026-06-12 — validation live e2e en attente)*
- [x] DEF-001, DEF-002, DEF-007, DEF-008 fermés dans le code (plus DEF-010
  côté publication MPI ; le consommateur hub → OpenELIS reste ouvert).
  *(livré 2026-06-12 — validation live e2e en attente)*
- [x] Sauvegardes : `make backup` / `make restore` (dump Postgres + AOF
  Redis + volumes), avec auto-test de complétude piloté par les fichiers
  compose. **Sans restauration testée, aucun pilote clinique.**
  *(livré 2026-06-12 — validation live e2e en attente)*
- [x] Observabilité minimale : `/metrics` Prometheus sur tous les services
  natifs via `openhis_sdk.metrics`, jauge `openhis_dlq_depth` et règle
  d'alerte d'exemple sur la profondeur de la DLQ (`openhis:events:dlq`).
  *(livré 2026-06-12 — validation live e2e en attente)*

### Horizon 2 — « Crédible » (3–6 mois)
Objectif : une DSI peut défendre le choix OpenHIS devant un comité.

- [x] **Lectures inter-services médiées par le hub** : la surface auditée
  `/api/context/*` (rôle machine `internal-sync`) remplace tout accès
  FHIR direct d'un service natif à OpenELIS/OpenMRS — la règle de
  l'adaptateur est désormais structurelle.
  *(livré 2026-07-09 — validation live e2e en attente)*
- Conformité : [x] CapabilityStatement FHIR (`GET /fhir/metadata` du hub)
  *(livré 2026-06-12 — validation live e2e en attente)* ; restent la
  politique de rétention et l'export d'audit unifié (OBJ 5) — exigences de
  tout appel d'offres public.
- [x] Benchmark MPI sur corpus de noms réels (diacritiques,
  translittérations arabes/françaises) ; précision/rappel publiés dans
  `docs/benchmarks/mpi-matching.md` avec planchers de régression en CI.
  *(livré 2026-06-12 — validation live e2e en attente)*
- [x] OPM empaqueté PyPI-ready (`openhis-opm`, commande `opm`) +
  quickstart français « VPS 8 Go → hôpital qui tourne » dans
  `docs/quickstart.md` — reste à filmer.
  *(livré 2026-06-12 — validation live e2e en attente)*

### Horizon 3 — « Référence » (6–18 mois)
Objectif : OpenHIS devient l'option par défaut de sa niche.

- **HIE-ready** : MPI exposé en IHE PIX/PDQ FHIR, support OpenHIM/SHR pour
  s'insérer dans les architectures nationales (OpenHIE) — c'est le canal
  d'adoption des ministères et des bailleurs (Digital Square global goods).
  La fondation est posée : la façade FHIR R4 du MPI (recherche Patient
  façon PDQm + requête de cross-référence `$ihe-pix` façon PIXm) est
  livrée (2026-06-12).
- Multi-site : un OpenHIS central fédérant des sites périphériques via le
  même bus (lab hub-and-spoke, télé-radiologie sur Orthanc peering) —
  note de conception : [docs/design/multi-site.md](design/multi-site.md).
- Marketplace de profils : le contrat de profil est déjà assez formel pour
  accueillir des profils tiers (pharmacie, vaccination, SMIR) — publier le
  SDK de profil et 2 profils d'exemple externes — note de conception :
  [docs/design/profile-marketplace.md](design/profile-marketplace.md).
- Migration optionnelle du bus vers Kafka au-delà de 100 k évts/jour
  (limite actée ADR-0001) — uniquement si un déploiement réel l'exige —
  note de conception :
  [docs/design/kafka-migration.md](design/kafka-migration.md).

## 4. Ce qu'il ne faut PAS faire

- **Ne pas réécrire de domaine clinique** (formulaire EMR custom, module de
  facturation maison) : c'est la mort de la thèse du projet.
- **Ne pas multiplier les services natifs** avant que les 8 existants soient
  exemplaires (le contrat de service est la vitrine).
- **Ne pas viser la certification (HIPAA/CE) prématurément** : viser d'abord
  les déploiements où l'exigence est la souveraineté des données et le coût,
  pas la certification.

## 5. Mesures de succès

| Mesure | Horizon 1 | Horizon 2 | Horizon 3 |
|---|---|---|---|
| Temps d'installation (VPS nu → stack saine) | < 1 h | < 30 min | < 30 min multi-profil |
| Suite e2e | verte en CI | verte multi-profils | verte multi-site |
| Défauts ouverts (DEF-NNN) | 0 | 0 | 0 |
| Sites pilotes réels | 1 | 3 | 10+ / 1 intégration HIE nationale |
| Contributeurs externes | — | 2+ | profil tiers publié |
