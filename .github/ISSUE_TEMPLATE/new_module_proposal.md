---
name: New module proposal
about: Propose adding a new service or integration module to OpenHIS
labels: new-module
---

## Module name

`services/<name>/`

## Profile

<!-- Which compose profile should activate this module? -->

## Problem this module solves

<!-- What clinical or operational gap does this fill? -->

## Proposed architecture

<!-- How does it fit into the existing stack? What does it publish/subscribe to? -->

### Event bus contract

**Publishes:**
- `<event.type>` — description

**Subscribes to:**
- `<event.type>` — description

### `openhis.service.json` sketch

```json
{
  "name": "<name>",
  "version": "0.1.0",
  "profile": "<profile>",
  "port": 80XX,
  "nginx_path": "<path>",
  "health_path": "/api/health",
  "bus": {
    "publishes": [],
    "subscribes": []
  },
  "depends_on": ["mpi", "integration-hub"],
  "env_required": [],
  "env_optional": []
}
```

## Dependencies

<!-- What external systems, images, or infrastructure does this require? -->

## Checklist

- [ ] I have read `docs/adding-a-module.md`
- [ ] The module fits an existing profile (or I'm proposing a new profile)
- [ ] I am willing to implement and maintain this module
