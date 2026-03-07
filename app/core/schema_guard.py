from sqlalchemy import inspect
from sqlalchemy.engine import Engine

REQUIRED_SCHEMA: dict[str, set[str]] = {
    "company": {
        "company_id",
        "name",
        "domain",
        "is_active",
        "created_at",
        "updated_at",
    },
    "app_user": {
        "user_id",
        "company_id",
        "role",
        "username",
        "full_name",
        "password_hash",
        "is_active",
    },
    "auth_token": {
        "token_id",
        "user_id",
        "access_token",
        "refresh_token",
        "expires_at",
        "revoked",
    },
    "site": {
        "site_id",
        "company_id",
        "name",
        "timezone",
        "address",
        "created_at",
    },
    "device": {
        "device_id",
        "site_id",
        "device_serial_number",
        "vendor",
        "model_name",
        "ip_address",
        "mac_address",
        "api_username",
        "api_password_encrypted",
        "api_port",
        "use_https",
        "status",
        "config",
        "created_at",
    },
}


def assert_required_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    missing_tables: list[str] = []
    missing_columns: list[str] = []

    for table_name, required_columns in REQUIRED_SCHEMA.items():
        if not inspector.has_table(table_name, schema="public"):
            missing_tables.append(table_name)
            continue

        db_columns = {column["name"] for column in inspector.get_columns(table_name, schema="public")}
        for column_name in sorted(required_columns - db_columns):
            missing_columns.append(f"public.{table_name}.{column_name}")

    if not missing_tables and not missing_columns:
        return

    parts: list[str] = ["Database schema validation failed."]
    if missing_tables:
        parts.append(f"Missing tables: {', '.join(f'public.{t}' for t in missing_tables)}")
    if missing_columns:
        parts.append(f"Missing columns: {', '.join(missing_columns)}")

    parts.append(
        "Run: psql \"$DATABASE_URL\" -f files/sql_file/init.sql"
    )
    raise RuntimeError(" ".join(parts))
