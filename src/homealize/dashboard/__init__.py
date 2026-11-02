"""Веб-дашборд на Streamlit (спека §7.2): `uv run homealize dashboard`."""

from pathlib import Path

APP_PATH = Path(__file__).with_name("app.py")  # файл, который исполняет `streamlit run`
CONFIG_ENV = "HOMEALIZE_CONFIG"  # путь к settings.yaml для страницы дашборда
