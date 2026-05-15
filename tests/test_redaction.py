from hermes_progress_tail.redaction import redact_text, sanitize, simplify_path


def test_redacts_secret_keys_recursively():
    data = {
        "api_key": "sk-thisshouldnotshow",
        "nested": {"Authorization": "Bearer abc.def.ghi"},
        "safe": "visible",
    }

    redacted = sanitize(data)

    assert redacted["api_key"] == "[redacted]"
    assert redacted["nested"]["Authorization"] == "[redacted]"
    assert redacted["safe"] == "visible"


def test_redacts_env_assignments_and_private_keys_from_text():
    text = "EXAMPLE_TOKEN=tok-secret -----BEGIN PRIVATE KEY----- abc -----END PRIVATE KEY-----"

    redacted = redact_text(text)

    assert "tok-secret" not in redacted
    assert "PRIVATE KEY" not in redacted
    assert "[redacted_env]" in redacted
    assert "[redacted_private_key]" in redacted


def test_redacts_jwt_and_long_opaque_blobs():
    blob = "a" * 90
    text = f"token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature blob {blob}"

    redacted = redact_text(text)

    assert "eyJhbGci" not in redacted
    assert blob not in redacted
    assert "[redacted_jwt]" in redacted
    assert "[redacted_blob]" in redacted


def test_redaction_preserves_long_filename_components():
    filename = "a1b2c3d4e5f67890" * 6 + ".css"
    text = f"📖 read_file: ~/Works/HMX/.../views/{filename}:1384+26"

    redacted = redact_text(text)

    assert redacted == text
    assert "[redacted_blob]" not in redacted


def test_simplify_path_keeps_ordinary_paths_visible(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    project_file = tmp_path / "Projects" / "app" / "src" / "components" / "Button.vue"
    home_file = tmp_path / "Downloads" / "report.pdf"

    assert simplify_path(str(project_file)) == "src/components/Button.vue"
    assert simplify_path(str(home_file)) == "~/Downloads/report.pdf"


def test_simplify_path_handles_wsl_windows_user_paths():
    assert simplify_path("/mnt/c/Users/Zhafron/Downloads/foo.pdf") == "~/Downloads/foo.pdf"


def test_simplify_path_redacts_secret_like_components_without_hiding_file_context():
    path = "/home/zhafron/Projects/app/API_KEY=supersecret1234567890/file.py"

    simplified = simplify_path(path)

    assert simplified == "[redacted_env]/file.py"
    assert "supersecret" not in simplified


def test_redacts_quoted_env_sk_dash_tokens_and_secret_flags():
    text = "EXAMPLE_TOKEN=tok-test-value curl --password hunter2 --token abcdef123456"

    redacted = redact_text(text)

    assert "sk-quotedsecret" not in redacted
    assert "hunter2" not in redacted
    assert "abcdef123456" not in redacted
    assert "[redacted_env]" in redacted
    assert "--password [redacted]" in redacted
    assert "--token [redacted]" in redacted


def test_redacts_cookie_and_authorization_header_variants():
    text = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz Cookie: sessionid=supersecretvalue"

    redacted = redact_text(text)

    assert "abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "supersecretvalue" not in redacted
    assert "Authorization: Bearer [redacted]" in redacted
    assert "Cookie: [redacted]" in redacted


def test_redacts_common_secret_header_variants():
    text = "X-API-Key: key123456789 Api-Key: keyabcdefghij X-Auth-Token: tok123456789 X-Amz-Security-Token: amz123456789"

    redacted = redact_text(text)

    assert "key123456789" not in redacted
    assert "keyabcdefghij" not in redacted
    assert "tok123456789" not in redacted
    assert "amz123456789" not in redacted
    assert "X-API-Key: [redacted]" in redacted
    assert "Api-Key: [redacted]" in redacted
    assert "X-Auth-Token: [redacted]" in redacted
    assert "X-Amz-Security-Token: [redacted]" in redacted
