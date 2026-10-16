"""
db_builder.py
-------------
Construye una base de datos local y 100% gratuita (SQLite) a partir de
los CSV generados por generate_data.py, usando SQLAlchemy como capa de
acceso a datos.

Crea data/inventory.db con tres tablas:
    - products
    - inventory
    - sales

y agrega indices sobre product_id para que los JOIN de src/queries.py
sean eficientes incluso si el dataset crece.

Ejecucion:
    python db_builder.py
"""

from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

BASE_DIR = Path(__file__).parent
RAW_DIR = BASE_DIR / "data" / "raw"
DB_PATH = BASE_DIR / "data" / "inventory.db"

# echo=False evita loguear cada sentencia SQL en consola durante la carga masiva
ENGINE = create_engine(f"sqlite:///{DB_PATH}", echo=False)

TABLES = {
    "products": RAW_DIR / "products.csv",
    "inventory": RAW_DIR / "inventory.csv",
    "sales": RAW_DIR / "sales.csv",
}


def validate_raw_files() -> None:
    """Falla rapido y con un mensaje claro si falta correr generate_data.py."""
    missing = [str(p) for p in TABLES.values() if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "Faltan archivos CSV: " + ", ".join(missing) +
            "\nEjecuta primero: python generate_data.py"
        )


def load_csvs_to_sqlite() -> None:
    """Lee cada CSV con pandas y lo escribe como tabla SQL (reemplaza si existe)."""
    for table_name, csv_path in TABLES.items():
        df = pd.read_csv(csv_path)
        df.to_sql(table_name, con=ENGINE, if_exists="replace", index=False)
        print(f"  -> tabla '{table_name}' cargada ({len(df)} filas)")


def create_indexes() -> None:
    """Crea indices sobre las claves foraneas/joins mas usados por queries.py."""
    statements = [
        "CREATE INDEX IF NOT EXISTS idx_inventory_product_id ON inventory(product_id);",
        "CREATE INDEX IF NOT EXISTS idx_sales_product_id ON sales(product_id);",
        "CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(date);",
        "CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);",
    ]
    with ENGINE.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    print("  -> indices creados sobre product_id / date / category")


def sanity_check() -> None:
    """Imprime un conteo de filas por tabla para verificar que la carga fue correcta."""
    with ENGINE.connect() as conn:
        for table_name in TABLES:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
            print(f"  -> {table_name}: {count} filas en la base")


def main() -> None:
    validate_raw_files()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    print("Cargando CSVs en SQLite...")
    load_csvs_to_sqlite()

    print("Creando indices...")
    create_indexes()

    print("Verificando integridad...")
    sanity_check()

    print(f"\nBase de datos lista en: {DB_PATH.resolve()}")


if __name__ == "__main__":
    main()
