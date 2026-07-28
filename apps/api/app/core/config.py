"""Application configuration, read from environment variables (or a .env file locally)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the API and worker processes.

    Every field has a local-development default so the app boots without a
    .env file present; production deployments should override all of them
    via real environment variables, especially jwt_secret, api_key_pepper,
    and the Twilio/Gemini credentials.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://echoflow:echoflow@localhost:5432/echoflow"
    redis_url: str = "redis://localhost:6379/0"

    # Twilio (see docs/ARCHITECTURE.md): webhook signature verification and
    # outbound SMS land in Milestone 2. Placeholder defaults let the app and
    # its tests boot without a real Twilio account; the phone number uses
    # Twilio's documented "magic" test number so it reads unambiguously as a
    # placeholder rather than a real line.
    twilio_account_sid: str = "dev-placeholder-account-sid"
    twilio_auth_token: str = "dev-placeholder-auth-token"
    twilio_phone_number: str = "+15005550006"

    # Gemini (see docs/ARCHITECTURE.md): the agent's function-calling model,
    # wired up in Milestone 3.
    gemini_api_key: str = "dev-placeholder-gemini-key"

    # Dashboard auth (see docs/TDD.md section 3.6). jwt_secret signs session
    # JWTs; api_key_pepper is the HMAC pepper used to deterministically hash
    # API keys and refresh tokens so a presented secret can be looked up by
    # exact hash match. Access tokens are short-lived and stateless; refresh
    # tokens are longer-lived, single-use, and rotated on every refresh.
    jwt_secret: str = "dev-secret-change-me-32-bytes-min"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    api_key_pepper: str = "dev-api-key-pepper-change-me"

    # Comma-separated list of origins allowed to call the API from a browser
    # (see app/main.py CORSMiddleware setup). Defaults to the web app's dev
    # origin from infra/docker-compose.yml.
    cors_allowed_origins: str = "http://localhost:3000"


settings = Settings()
