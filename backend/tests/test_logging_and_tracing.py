import logging

from fastapi.testclient import TestClient

from backend.app.logging_config import (
    StructuredFormatter,
    sanitize_sensitive_data,
)
from backend.app.services.resume_compiler import ResumeCompiler, ResumeSpec
from backend.main import app


def test_sanitize_sensitive_data():
    raw_bearer = "Authorization: Bearer secret-token-value-12345"
    assert sanitize_sensitive_data(raw_bearer) == "Authorization: Bearer [REDACTED]"

    raw_openai = "Connecting with sk-proj-12345678901234567890"
    assert "[REDACTED_OPENAI_KEY]" in sanitize_sensitive_data(raw_openai)

    raw_claude = "Connecting with sk-ant-12345678901234567890"
    assert "[REDACTED_CLAUDE_KEY]" in sanitize_sensitive_data(raw_claude)

    raw_gemini = "Key AIzaSyB1234567890123456789012345678901"
    assert "[REDACTED_GEMINI_KEY]" in sanitize_sensitive_data(raw_gemini)

    raw_query = "https://example.com/api?api_key=secretkey123&user=john"
    sanitized = sanitize_sensitive_data(raw_query)
    assert "api_key=[REDACTED]" in sanitized
    assert "user=john" in sanitized

    raw_header = "headers={'x-goog-api-key': 'supersecretkey123'}"
    assert "'x-goog-api-key': '[REDACTED]'" in sanitize_sensitive_data(raw_header)


def test_structured_formatter_output():
    formatter = StructuredFormatter(use_colors=False)
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="User auth failed with token Bearer secret-token-xyz-123456",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)

    # Check ISO timestamp format: YYYY-MM-DD
    assert " | INFO  | test.logger | " in formatted
    assert "Bearer [REDACTED]" in formatted
    assert "secret-token-xyz" not in formatted


def test_structured_formatter_with_colors():
    formatter = StructuredFormatter(use_colors=True)
    record = logging.LogRecord(
        name="test.logger",
        level=logging.WARNING,
        pathname=__file__,
        lineno=10,
        msg="Warning message",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    assert "\033[33mWARN" in formatted or "\033[33mWARNING" in formatted
    assert "Warning message" in formatted


def test_http_request_tracing_middleware(caplog):
    client = TestClient(app)
    with caplog.at_level(logging.INFO):
        resp = client.get("/api/scraper/session/status")
        assert resp.status_code == 200

    logs = [rec.message for rec in caplog.records if "careerforge.http" in rec.name]
    assert any("[GET] /api/scraper/session/status -> 200 OK" in m for m in logs)
    assert any("ms)" in m for m in logs)


def test_http_request_tracing_404(caplog):
    client = TestClient(app)
    with caplog.at_level(logging.WARNING):
        resp = client.get("/api/nonexistent-endpoint")
        assert resp.status_code == 404

    logs = [rec.message for rec in caplog.records if "careerforge.http" in rec.name]
    assert any("[GET] /api/nonexistent-endpoint -> 404 Not Found" in m for m in logs)


def test_http_request_tracing_quiet_success(caplog):
    client = TestClient(app)
    with caplog.at_level(logging.INFO):
        resp_logs = client.get("/api/system/logs?limit=50")
        assert resp_logs.status_code == 200
        resp_health = client.get("/api/health")
        assert resp_health.status_code == 200

    logs = [rec.message for rec in caplog.records if "careerforge.http" in rec.name]
    assert not any("/api/system/logs" in m for m in logs)
    assert not any("/api/health" in m for m in logs)


def test_circular_memory_log_handler_exception_formatting():
    from backend.app.logging_config import CircularMemoryLogHandler, get_runtime_logs

    handler = CircularMemoryLogHandler()
    logger = logging.getLogger("test.exception_logger")
    logger.addHandler(handler)
    logger.setLevel(logging.ERROR)

    try:
        raise ValueError("Simulated failure for traceback test")
    except ValueError:
        logger.error("An error occurred during task execution", exc_info=True)

    recent = get_runtime_logs(limit=10)
    assert any(
        "Simulated failure for traceback test" in entry["message"]
        and "Traceback (most recent call last):" in entry["message"]
        for entry in recent
    )


def test_resume_compiler_logging(caplog):
    compiler = ResumeCompiler()
    spec = ResumeSpec(
        name="Jane Doe",
        title="Software Engineer",
        sections=[],
    )
    with caplog.at_level(logging.INFO):
        buf, pages = compiler.compile_pdf(spec)

    assert pages >= 1
    logs = [rec.message for rec in caplog.records if "careerforge.compiler" in rec.name]
    assert any("Compiled PDF resume" in m and "page(s)" in m for m in logs)
