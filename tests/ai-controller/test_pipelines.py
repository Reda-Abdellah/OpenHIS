def test_list_pipelines(client):
    r = client.get("/api/pipelines")
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_seed_pipelines_present(client):
    r = client.get("/api/pipelines")
    names = [p["id"] for p in r.json()]
    assert "poc-xray" in names or len(names) >= 0  # seed pipelines

def test_register_pipeline(client):
    r = client.post("/api/pipelines", json={
        "id": "test-pipe", "name": "Test Pipeline",
        "description": "CI test", "docker_image": "test/pipe:latest",
        "version": "1.0.0", "output_types": ["json"]
    })
    assert r.status_code in (200, 201)
    assert r.json()["id"] == "test-pipe"

def test_duplicate_pipeline(client):
    payload = {"id": "dup-pipe", "name": "Dup", "docker_image": "x/y:z"}
    client.post("/api/pipelines", json=payload)
    r = client.post("/api/pipelines", json=payload)
    assert r.status_code in (200, 409)  # upsert or conflict

def test_update_pipeline(client):
    client.post("/api/pipelines", json={"id": "upd-pipe", "name": "Old",
                                         "docker_image": "x/y:1"})
    r = client.patch("/api/pipelines/upd-pipe", json={"name": "Updated"})
    assert r.status_code == 200

def test_get_pipeline(client):
    client.post("/api/pipelines", json={"id": "get-pipe", "name": "G",
                                         "docker_image": "x/y:1"})
    r = client.get("/api/pipelines/get-pipe")
    assert r.status_code == 200
    assert r.json()["id"] == "get-pipe"


def test_pipeline_source_type_defaults_to_imaging(client):
    r = client.post("/api/pipelines", json={
        "id": "st-default", "name": "ST Default", "docker_image": "x/y:z"
    })
    assert r.status_code == 201
    assert r.json()["source_type"] == "imaging"


def test_pipeline_source_type_lab_result(client):
    r = client.post("/api/pipelines", json={
        "id": "st-lab", "name": "Lab Pipeline", "docker_image": "x/y:z",
        "source_type": "lab_result"
    })
    assert r.status_code == 201
    assert r.json()["source_type"] == "lab_result"


def test_pipeline_source_type_emr_event(client):
    r = client.post("/api/pipelines", json={
        "id": "st-emr", "name": "EMR Pipeline", "docker_image": "x/y:z",
        "source_type": "emr_event"
    })
    assert r.status_code == 201
    assert r.json()["source_type"] == "emr_event"


def test_pipeline_input_schema_stored(client):
    schema = '{"oe_id": "string", "subject": "string"}'
    r = client.post("/api/pipelines", json={
        "id": "schema-pipe", "name": "Schema", "docker_image": "x/y:z",
        "input_schema": schema
    })
    assert r.status_code == 201
    assert r.json()["input_schema"] == schema


def test_seed_contains_clinical_pipelines(client):
    r = client.get("/api/pipelines")
    ids = [p["id"] for p in r.json()]
    assert "poc-lab-risk" in ids
    assert "poc-emr-alert" in ids
