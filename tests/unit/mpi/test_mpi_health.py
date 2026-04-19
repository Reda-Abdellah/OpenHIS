def test_health_returns_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["service"] == "mpi"
    assert "master_patients" in data
    assert "cross_references" in data
    assert "pending_matches" in data
    assert data["version"] == "1.0.0"


def test_health_counts_reflect_seeded_data(client, db):
    """Health endpoint reports active masters, total xrefs, and pending matches."""
    import uuid
    pid_a, pid_b = str(uuid.uuid4()), str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            "INSERT INTO master_patients(id,mrn,firstname,lastname,status)"
            " VALUES(?,?,?,?,?)", (pid_a, "H-A", "A", "A", "active"))
        conn.execute(
            "INSERT INTO master_patients(id,mrn,firstname,lastname,status)"
            " VALUES(?,?,?,?,?)", (pid_b, "H-B", "B", "B", "active"))
        conn.execute(
            "INSERT INTO cross_references(master_id,system,system_id,mrn)"
            " VALUES(?,?,?,?)", (pid_a, "openmrs", "h-omrs-a", "H-A"))
        conn.execute(
            "INSERT INTO match_candidates(master_id_a,master_id_b,score)"
            " VALUES(?,?,?)",
            (min(pid_a, pid_b), max(pid_a, pid_b), 0.85))

    body = client.get("/api/health").json()
    assert body["master_patients"] == 2
    assert body["cross_references"] == 1
    assert body["pending_matches"] == 1


def test_auth_config_exposes_oidc_settings(client, monkeypatch):
    monkeypatch.setenv("KEYCLOAK_PUBLIC_URL", "https://kc.example.com")
    monkeypatch.setenv("KEYCLOAK_REALM", "openhis")
    monkeypatch.setenv("KEYCLOAK_SPA_CLIENT_ID", "openhis-admin-spa")
    body = client.get("/api/auth/config").json()
    assert body["realm"] == "openhis"
    assert body["client_id"] == "openhis-admin-spa"
    # keycloak_url is read at request time so the override applies
    assert body["keycloak_url"] == "https://kc.example.com"
