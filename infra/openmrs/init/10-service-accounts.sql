-- Machine service-account users (D-01 / DEF-011).
--
-- The oauth2login module's OAuth2ServiceAccountFilter authenticates
-- Authorization: Bearer <JWT> requests (signature verified against
-- oauth2.properties keysUrl) but REQUIRES a pre-existing OpenMRS user
-- whose username matches the token's preferred_username claim — for a
-- Keycloak client-credentials token that is "service-account-<client>".
-- No password: like the built-in `daemon` user, these accounts can only
-- authenticate via a verified JWT.
--
-- Idempotent: applied by the openmrs-init one-shot container on every
-- `up`; re-running is a no-op.

-- integration-hub-sa — the FHIR sync worker + event push adapters
INSERT INTO person (gender, creator, date_created, uuid)
SELECT '', 1, NOW(), 'c3d4e5f6-0000-4000-8000-000000000101'
WHERE NOT EXISTS (
  SELECT 1 FROM person WHERE uuid = 'c3d4e5f6-0000-4000-8000-000000000101');

SET @pid := (SELECT person_id FROM person
             WHERE uuid = 'c3d4e5f6-0000-4000-8000-000000000101');

INSERT INTO person_name (preferred, person_id, given_name, family_name,
                         creator, date_created, uuid)
SELECT 1, @pid, 'Integration', 'Hub', 1, NOW(),
       'c3d4e5f6-0000-4000-8000-000000000102'
WHERE NOT EXISTS (
  SELECT 1 FROM person_name
  WHERE uuid = 'c3d4e5f6-0000-4000-8000-000000000102');

INSERT INTO users (system_id, username, password, salt, creator,
                   date_created, person_id, retired, uuid)
SELECT 'integration-hub-sa', 'service-account-integration-hub-sa',
       NULL, NULL, 1, NOW(), @pid, 0,
       'c3d4e5f6-0000-4000-8000-000000000103'
WHERE NOT EXISTS (
  SELECT 1 FROM users WHERE username = 'service-account-integration-hub-sa');

SET @uid := (SELECT user_id FROM users
             WHERE username = 'service-account-integration-hub-sa');

-- Full API privileges: the account is machine-only, bearer-only, and the
-- FHIR routes it uses are subnet-restricted at nginx.
INSERT INTO user_role (user_id, role)
SELECT @uid, 'System Developer'
WHERE NOT EXISTS (
  SELECT 1 FROM user_role WHERE user_id = @uid AND role = 'System Developer');
