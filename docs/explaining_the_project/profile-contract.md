# OpenHIS Profile Contract

A **profile** is a Docker Compose overlay that adds a cohesive set of services to the base stack. Profiles are selected at deploy time; unselected profiles add zero overhead.

---

## 1. File location

```
compose/profiles/<profile-name>.yml
```

The profile name must be lowercase alphanumeric + hyphens and must match exactly what operators pass to `opm enable <name>` or set in `OPENHIS_PROFILES`.

---

## 2. Required `x-openhis` metadata block

Every profile file must begin with:

```yaml
x-openhis:
  profile:       my-profile              # machine name
  display_name:  "My Profile"            # shown in admin UI
  description:   "One-line description"
  requires:      []                      # other profiles that must be active
  integrates_with: [emr, mpi]           # profiles this one talks to
  ram_mb:        1024                    # estimated RAM in MB
  nginx_routes:
    - { path: /my-app/, upstream: "my-app:8080" }
```

`nginx_routes` is read by `nginx_gen.py`. For services with complex nginx requirements (WebSocket, special headers, redirect rewriting), add a named block to `infra/nginx/nginx.conf.j2` instead and gate it with `{% if 'my-profile' in active_profiles %}`.

---

## 3. Service definitions

Use the same conventions as `compose/base.yml`:

- All services join `openhis-net`.
- Use `restart: unless-stopped`.
- Define a `healthcheck` for every service.
- Inject `ROOT_PATH`, `REDIS_URL`, and any other required env vars.
- Mount volumes with named volumes, not bind mounts (except for config files).

```yaml
services:
  my-app:
    build: ../../services/my-app
    environment:
      ROOT_PATH: /my-app
      REDIS_URL: redis://redis:6379
    networks: [openhis-net]
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8020/api/health"]
      interval: 20s
      timeout: 5s
      retries: 3
      start_period: 15s
```

---

## 4. Volumes

Declare named volumes at the **top level** of the profile file so Docker Compose merges them correctly when multiple compose files are combined:

```yaml
volumes:
  my-app-data:
```

Do not shadow volumes declared in `compose/base.yml`.

---

## 5. Dependency declarations

If your services depend on base-stack services (postgres, redis, keycloak), express runtime dependencies with `depends_on` using `condition: service_healthy`:

```yaml
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
```

Do not use `depends_on` to reference services from _other_ profiles — they may not be present. Use the bus for cross-profile communication.

---

## 6. RAM estimate

Add the profile's RAM estimate to `platform/profile_engine.py` (`_RAM_MB` dict) and to the `x-openhis.ram_mb` field. Estimates should reflect peak usage, not idle.

---

## 7. Profile enable/disable lifecycle

| Step | What happens |
|------|-------------|
| `opm enable my-profile` | Updates `.env`, regenerates nginx.conf, starts profile containers |
| `opm disable my-profile` | Stops containers, updates `.env`, regenerates nginx.conf |
| nginx reload | Routes to disabled services return 502 — this is expected |
| Data volumes | Preserved on disable; removed only with `--remove-volumes` |

---

## 8. Checklist before merging a new profile

- [ ] `compose/profiles/<name>.yml` exists with `x-openhis` block
- [ ] All services have a `healthcheck`
- [ ] `nginx_routes` or a named block in `nginx.conf.j2` covers all web UIs
- [ ] RAM estimate added to `profile_engine.py`
- [ ] At least one service has an `openhis.service.json` manifest
- [ ] `opm enable <name>` runs without errors on a clean stack
- [ ] Admin dashboard shows services as healthy within 2 minutes of enable
