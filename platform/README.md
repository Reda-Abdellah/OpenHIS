# openhis-opm — OpenHIS Platform Manager

`opm` is the deployment CLI for [OpenHIS](https://github.com/Reda-Abdellah/OpenHIS),
an open-source, profile-driven Health Information Platform that orchestrates
best-of-breed clinical systems (OpenMRS, OpenELIS, Odoo, Orthanc, OHIF) over a
FHIR R4 + Redis Streams integration spine.

## Install

```bash
pip install openhis-opm        # from PyPI (when published)
# or, inside an OpenHIS checkout:
pip install -e platform
```

## Usage

`opm` operates on an OpenHIS source checkout (it resolves `compose/`, `.env`
and `infra/` relative to the repository root):

```bash
opm --version            # print the CLI version
opm init                 # first-run wizard: profiles, secrets, .env, infra render
opm enable emr laboratory
opm disable erp
opm status               # active profiles + live service health
opm up / opm down        # start / stop the active stack
opm upgrade emr          # rolling upgrade, one service at a time
opm nginx --reload       # regenerate nginx.conf from active profiles
opm add-service my-svc --port 8020 --profile analytics
```

Full documentation: the
[quickstart guide](https://github.com/Reda-Abdellah/OpenHIS/blob/master/docs/quickstart.md)
and the repository `docs/` tree.

## License

Apache-2.0 — see the repository `LICENSE.md`.
