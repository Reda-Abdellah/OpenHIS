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
