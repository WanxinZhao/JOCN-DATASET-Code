import json
import os
from pathlib import Path

import psycopg2


def _load_dotenv_if_present() -> None:
    candidates = [
        Path(__file__).resolve().parent / ".env",
        Path(__file__).resolve().parent / "utils" / ".env",
    ]

    for env_path in candidates:
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _connect():
    return psycopg2.connect(
        host=os.getenv("PHYSICAL_LAYER_PG_HOST") or os.getenv("PGHOST") or "localhost",
        port=os.getenv("PHYSICAL_LAYER_PG_PORT") or os.getenv("PGPORT") or "5432",
        database=os.getenv("PHYSICAL_LAYER_PG_DATABASE") or os.getenv("PGDATABASE") or "ecoc2026",
        user=os.getenv("PHYSICAL_LAYER_PG_USER") or os.getenv("PGUSER") or "postgres",
        password=os.getenv("PHYSICAL_LAYER_PG_PASSWORD") or os.getenv("PGPASSWORD") or "",
    )


def main() -> None:
    _load_dotenv_if_present()
    root = Path(__file__).resolve().parent
    table_name = os.getenv("PHYSICAL_LAYER_PG_TABLE") or "json_documents"
    files = [
        root / "eqpt_config_NDFF.json",
        root / "OFC_Testbed.json",
    ]

    with _connect() as conn:
        with conn.cursor() as cur:
            for path in files:
                data = json.loads(path.read_text(encoding="utf-8"))
                cur.execute(
                    f"""
                    INSERT INTO {table_name} (file_name, doc)
                    VALUES (%s, %s::jsonb)
                    """,
                    (path.name, json.dumps(data, ensure_ascii=False)),
                )

    print("JSON documents imported into PostgreSQL.")


if __name__ == "__main__":
    main()
