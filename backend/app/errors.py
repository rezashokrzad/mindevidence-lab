"""Application errors raised by services and turned into the standard error envelope."""


class AppError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: list[dict] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or []


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class PatientNotFound(NotFoundError):
    code = "patient_not_found"

    def __init__(self, patient_id: str):
        super().__init__(f"No synthetic patient with id {patient_id}.")


class SessionNotFound(NotFoundError):
    code = "session_not_found"

    def __init__(self):
        # Same message whether the session is missing or owned by someone else,
        # so the API never reveals that another user's session exists.
        super().__init__("Session not found.")


class UnknownPatientReference(AppError):
    status_code = 422
    code = "unknown_patient"

    def __init__(self, patient_id: str):
        super().__init__(f"patient_id {patient_id} does not match any synthetic patient.",
                         details=[{"loc": ["body", "patient_id"], "message": "unknown patient", "type": "unknown_patient"}])


class AuthenticationRequired(AppError):
    status_code = 401
    code = "authentication_required"


class CsrfFailed(AppError):
    status_code = 403
    code = "csrf_failed"


class AuthNotConfigured(AppError):
    status_code = 503
    code = "auth_not_configured"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class DocumentNotFound(NotFoundError):
    code = "document_not_found"

    def __init__(self, document_id: str):
        super().__init__(f"No document with id {document_id}.")


class ChunkNotFound(NotFoundError):
    code = "chunk_not_found"

    def __init__(self, chunk_id: str):
        super().__init__(f"No chunk with id {chunk_id}.")


class RunNotFound(NotFoundError):
    code = "ingestion_run_not_found"

    def __init__(self):
        super().__init__("Ingestion run not found.")


class IngestionInProgress(AppError):
    status_code = 409
    code = "ingestion_in_progress"


class ManifestInvalid(AppError):
    status_code = 500
    code = "manifest_invalid"


class UnknownDocument(AppError):
    status_code = 422
    code = "unknown_document"


class RetrievalUnavailable(AppError):
    status_code = 503
    code = "retrieval_unavailable"


class EmbeddingModelMismatch(AppError):
    status_code = 409
    code = "embedding_model_mismatch"


class EmbeddingBackendError(AppError):
    status_code = 502
    code = "embedding_failed"


class LLMProblem(AppError):
    """A model-service failure surfaced to the client. The code mirrors app.llm.provider error codes."""

    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class WorkflowFailed(AppError):
    status_code = 500
    code = "workflow_error"


class AuditAccessDenied(AppError):
    status_code = 404
    code = "not_found"

    def __init__(self):
        super().__init__("No such record.")
