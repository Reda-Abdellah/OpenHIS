# Design Note — Third-Party Profile Distribution ("Profile Marketplace")

> **Status: Design note — not an ADR.** This sketches how third parties
> could ship OpenHIS profiles without forking the repo. No registry, no
> `opm install` command exists yet. Promote to an ADR once a distribution
> mechanism is actually chosen.

- Date: 2026-06-12
- Relates to: [profile contract](../explaining_the_project/profile-contract.md),
  [service contract](../explaining_the_project/service-contract.md),
  [adding a module](../explaining_the_project/adding-a-module.md)

## Problem

Profiles are how OpenHIS grows — `emr`, `laboratory`, `imaging`, `erp`,
`analytics` all live in [`compose/profiles/`](../../compose/profiles/)
inside this repo. A hospital that wants a niche integration (a national
insurance gateway, a local pharmacy system) must today fork the repo and
maintain the profile in-tree. The question: what would it take for a third
party to publish a profile that an operator installs with one command?

## What the profile contract already guarantees

The [profile contract](../explaining_the_project/profile-contract.md) is
deliberately machine-readable, which does most of the marketplace's work:

- **The `x-openhis` block** at the top of every
  `compose/profiles/<name>.yml` declares `profile`, `display_name`,
  `description`, `requires`, `integrates_with`, `ram_mb` and
  `nginx_routes`. It is parsed today by
  [`platform/profile_engine.py`](../../platform/profile_engine.py)
  (`load_profile_meta`) — a third-party profile is discoverable by the
  same code path the moment its YAML lands in `compose/profiles/`.
- **nginx routes are data, not code.**
  [`platform/nginx_gen.py`](../../platform/nginx_gen.py) (`build_context`)
  turns `nginx_routes` entries into upstream + location blocks for any
  profile that is not one of the built-in named ones — third-party web UIs
  get routed with zero template edits.
- **RAM estimates** let `opm init`/`enable` warn before an operator starts
  a stack the host cannot hold. Caveat: `estimate_ram_mb` in
  `profile_engine.py` currently uses a hardcoded cost dict with a 256 MB
  default for unknown profiles — a marketplace must make it read
  `x-openhis.ram_mb` instead, otherwise every third-party profile "costs"
  256 MB regardless of truth.
- **Isolation rules**: profiles may not `depends_on` services from other
  profiles (bus-only cross-profile communication) and may not shadow base
  volumes — both are exactly the properties that make a profile safe to
  drop into a stack the author has never seen.

## What a marketplace adds

1. **Manifest schema versioning.** `x-openhis` has no version field;
   adding keys silently is fine in-tree but breaks independently-released
   profiles. Add `x-openhis.schema_version: 1`, validated by
   `profile_engine.load_profile_meta` with a clear "this profile needs a
   newer opm" error.
2. **A distribution unit.** A profile is more than one YAML: the
   `laboratory` profile, for example, needs `infra/openelis/` content
   alongside [`compose/profiles/laboratory.yml`](../../compose/profiles/laboratory.yml).
   The unit is therefore a directory: `profile.yml` + optional
   `infra/<name>/` + optional `services/<name>/` build context. Installation = copying those
   into the operator's checkout under the same paths.
3. **`opm install <ref>`.** A new command in
   [`platform/opm.py`](../../platform/opm.py) next to `enable`/`disable`/
   `add-service`: fetch the distribution unit from a registry, validate the
   `x-openhis` block, refuse name collisions with existing profiles, copy
   files into place, then print the `opm enable <name>` next step. Install
   must **not** auto-enable.
4. **Signature verification.** The unit is a tarball signed by the
   publisher (minisign or cosign — pick one, small surface). `opm install`
   verifies before unpacking; `--insecure-skip-verify` exists but shouts.
   Compose files run arbitrary containers with host volume mounts — an
   unsigned profile is remote code execution by construction, so this is
   the one non-negotiable piece.
5. **A CI conformance suite the profile must pass** before the registry
   lists it. Almost all of it already exists in-tree:
   - *Auth conformance*: boot each native service in the profile with
     **real JWT enforcement** using the
     [`tests/auth/harness.py`](../../tests/auth/harness.py) machinery
     (`isolated_service`, `make_token`, `make_foreign_token`) and assert
     deny-by-default — the same checks
     [`tests/auth/test_every_service_rejects_no_token.py`](../../tests/auth/test_every_service_rejects_no_token.py)
     runs against first-party services. The profile author ships a
     `ServiceSpec`-shaped declaration (protected path, granted roles,
     public paths) and the harness does the rest.
   - *Service contract checks*: every service has `openhis.service.json`
     (name/port/bus topics/`env.required`), a `Dockerfile`, a
     `healthcheck` in the compose YAML, and declares required env vars —
     all statically checkable, mirroring the
     [service contract](../explaining_the_project/service-contract.md)
     and the merge checklist already in the profile contract (§8).
   - *Compose hygiene*: `x-openhis` block present and schema-valid, all
     services join `openhis-net`, named volumes only, no `depends_on`
     across profiles. The repo already has precedent for tests that parse
     compose files (the backup-completeness unit test), so this is a
     pytest module, not new infrastructure.

## Minimal v1

Skip the registry, signing service and web UI entirely:

- **A curated git repository** (`openhis-profiles`) where each top-level
  directory is one distribution unit, reviewed by maintainers — review
  *is* the v1 trust model.
- **`opm install --from-git <url> <profile-name>`**: shallow-clone, run
  the local validation subset (x-openhis schema, collision check, compose
  hygiene), copy files, done. Signature verification and a real registry
  index come later and slot behind the same command.
- The conformance suite runs in the curated repo's CI, reusing this
  repo's `tests/auth` harness as a pinned dependency.

## Open questions

- How do third-party services get Keycloak clients/roles provisioned?
  (Today realm content is templated in `infra/keycloak/openhis-realm.json.j2`
  — probably an `x-openhis.keycloak_clients` declaration applied via the
  admin API at enable time.)
- Upgrade story: `opm install` over an existing version — overwrite,
  or versioned side-by-side with migration hooks?
- Do marketplace profiles get bus topic namespacing (e.g. mandatory
  `vendor.` prefix) to avoid colliding with first-party event types?
