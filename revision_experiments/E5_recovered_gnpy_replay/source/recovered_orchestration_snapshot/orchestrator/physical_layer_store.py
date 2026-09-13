import json
import os
from pathlib import Path
from typing import Any, Dict, Tuple


def _load_dotenv_if_present() -> None:
    candidates = [
        Path(__file__).resolve().parents[1] / ".env",
        Path(__file__).resolve().parent / ".env",
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


class PhysicalLayerStore:
    """Unified loader for topology and equipment data from files or PostgreSQL."""

    def __init__(
        self,
        topology_path: str = "OFC_Testbed.json",
        equipment_path: str = "eqpt_config_NDFF.json",
        backend: str | None = None,
    ) -> None:
        self._repo_root = Path(__file__).resolve().parents[1]
        self.topology_path = self._resolve_path(topology_path)
        self.equipment_path = self._resolve_path(equipment_path)
        self.backend = self._resolve_backend(backend)
        self._cache: Tuple[Dict[str, Any], Dict[str, Any]] | None = None

    def load_topology_and_equipment(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if self._cache is None:
            if self.backend == "postgres":
                self._cache = self._load_from_postgres()
            elif self.backend == "file":
                self._cache = self._load_from_files()
            else:
                self._cache = self._load_auto()
        return self._cache

    def describe(self) -> str:
        if self.backend == "postgres":
            return (
                f"postgres://{self._postgres_user()}@{self._postgres_host()}:"
                f"{self._postgres_port()}/{self._postgres_database()} table={self._postgres_table()}"
            )
        return f"files:{self.topology_path.name},{self.equipment_path.name}"

    def backend_label(self) -> str:
        if self.backend == "postgres":
            return "PostgreSQL"
        if self.backend == "file":
            return "local files"
        return "auto"

    def _resolve_backend(self, backend: str | None) -> str:
        _load_dotenv_if_present()
        raw_backend = (backend or os.getenv("PHYSICAL_LAYER_DB_BACKEND") or "postgres").strip().lower()
        if raw_backend not in {"auto", "file", "postgres"}:
            raise ValueError(f"Unsupported physical-layer backend: {raw_backend}")
        return raw_backend

    def _resolve_path(self, value: str) -> Path:
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate
        cwd_candidate = Path.cwd() / candidate
        if cwd_candidate.exists():
            return cwd_candidate
        return self._repo_root / candidate

    def _load_auto(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if self.topology_path.exists() and self.equipment_path.exists():
            return self._load_from_files()
        return self._load_from_postgres()

    def _load_from_files(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if not self.topology_path.exists() or not self.equipment_path.exists():
            raise FileNotFoundError(
                f"Required topology files are missing: {self.topology_path} | {self.equipment_path}"
            )

        topology = json.loads(self.topology_path.read_text(encoding="utf-8"))
        equipment = json.loads(self.equipment_path.read_text(encoding="utf-8"))
        return topology, equipment

    def _load_from_postgres(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        topology = self._fetch_document(self.topology_path.name)
        equipment = self._fetch_document(self.equipment_path.name)
        return topology, equipment

    def _fetch_document(self, file_name: str) -> Dict[str, Any]:
        try:
            import psycopg2
            from psycopg2.extras import Json
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "PostgreSQL backend is enabled, but `psycopg2` is not installed."
            ) from exc

        query = f"""
            SELECT doc
            FROM {self._postgres_table()}
            WHERE file_name = %s
            ORDER BY imported_at DESC NULLS LAST, id DESC
            LIMIT 1
        """

        with psycopg2.connect(
            host=self._postgres_host(),
            port=self._postgres_port(),
            database=self._postgres_database(),
            user=self._postgres_user(),
            password=self._postgres_password(),
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (file_name,))
                row = cur.fetchone()

        if row is None:
            raise FileNotFoundError(
                f"Document `{file_name}` was not found in PostgreSQL table `{self._postgres_table()}`."
            )

        payload = row[0]
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, str):
            return json.loads(payload)
        return json.loads(Json(payload).dumps(payload))

    def _postgres_host(self) -> str:
        return os.getenv("PHYSICAL_LAYER_PG_HOST") or os.getenv("PGHOST") or "localhost"

    def _postgres_port(self) -> int:
        raw_value = os.getenv("PHYSICAL_LAYER_PG_PORT") or os.getenv("PGPORT") or "5432"
        return int(raw_value)

    def _postgres_database(self) -> str:
        return os.getenv("PHYSICAL_LAYER_PG_DATABASE") or os.getenv("PGDATABASE") or "ecoc2026"

    def _postgres_user(self) -> str:
        return os.getenv("PHYSICAL_LAYER_PG_USER") or os.getenv("PGUSER") or "postgres"

    def _postgres_password(self) -> str:
        return os.getenv("PHYSICAL_LAYER_PG_PASSWORD") or os.getenv("PGPASSWORD") or ""

    def _postgres_table(self) -> str:
        return os.getenv("PHYSICAL_LAYER_PG_TABLE") or "json_documents"
