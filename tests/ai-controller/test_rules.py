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


def test_rule_trigger_filter_defaults_to_empty(client):
    client.post("/api/pipelines", json={"id": "tf-pipe1", "name": "TF1", "docker_image": "x/y:1"})
    r = client.post("/api/rules", json={
        "pipeline_id": "tf-pipe1", "name": "No Filter Rule"
    })
    assert r.status_code == 201
    assert r.json()["trigger_filter"] == "{}"


def test_rule_trigger_filter_stored_correctly(client):
    client.post("/api/pipelines", json={"id": "tf-pipe2", "name": "TF2", "docker_image": "x/y:1"})
    f = '{"test_code": "CBC"}'
    r = client.post("/api/rules", json={
        "pipeline_id": "tf-pipe2", "name": "CBC Filter",
        "trigger_filter": f
    })
    assert r.status_code == 201
    assert r.json()["trigger_filter"] == f


def test_rule_trigger_filter_patchable(client):
    client.post("/api/pipelines", json={"id": "tf-pipe3", "name": "TF3", "docker_image": "x/y:1"})
    rule = client.post("/api/rules", json={
        "pipeline_id": "tf-pipe3", "name": "Patch Filter"
    }).json()
    r = client.patch(f"/api/rules/{rule['id']}", json={"trigger_filter": '{"panel": "HBA1C"}'})
    assert r.status_code == 200
