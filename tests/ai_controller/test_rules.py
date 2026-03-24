def test_list_rules(client):
    r = client.get("/api/rules")
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_create_rule(client):
    client.post("/api/pipelines", json={"id": "r-pipe", "name": "R",
                                         "docker_image": "x/y:1"})
    r = client.post("/api/rules", json={
        "pipeline_id": "r-pipe", "name": "CT Auto-trigger",
        "modality": "CT", "auto_trigger": True, "enabled": True
    })
    assert r.status_code in (200, 201)
    assert r.json()["modality"] == "CT"

def test_update_rule(client):
    client.post("/api/pipelines", json={"id": "r2-pipe", "name": "R2",
                                         "docker_image": "x/y:1"})
    rule = client.post("/api/rules", json={
        "pipeline_id": "r2-pipe", "name": "XR Rule", "modality": "CR"
    }).json()
    r = client.patch(f"/api/rules/{rule['id']}", json={"enabled": False})
    assert r.status_code == 200

def test_delete_rule(client):
    client.post("/api/pipelines", json={"id": "r3-pipe", "name": "R3",
                                         "docker_image": "x/y:1"})
    rule = client.post("/api/rules", json={
        "pipeline_id": "r3-pipe", "name": "Del Rule", "modality": "MR"
    }).json()
    r = client.delete(f"/api/rules/{rule['id']}")
    assert r.status_code == 204
