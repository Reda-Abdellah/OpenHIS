def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["service"] == "simulator"

def test_presets(client):
    r = client.get("/api/presets")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)
    assert len(r.json()) > 0

def test_jobs_empty(client):
    r = client.get("/api/jobs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
