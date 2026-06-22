from dotenv import load_dotenv


def load_project_env() -> None:
    """Load secret .env values, then visible local overrides."""
    load_dotenv(".env")
    load_dotenv("env.local", override=True)
