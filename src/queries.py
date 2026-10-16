"""
src/queries.py
---------------
Capa de analitica: ejecuta las consultas SQL complejas (CTEs, JOINs,
GROUP BY, funciones de ventana y una CTE recursiva) que responden a las
cuatro preguntas de negocio del proyecto:

    1. Rotacion de inventario y Dias de Suministro (DOS)
    2. Punto de Reorden (ROP) con Stock de Seguridad
    3. Clasificacion ABC / Pareto (80-20) por ingresos
    4. Deteccion de Dead Stock (sin ventas hace mas de N dias)

Todas las funciones publicas reciben un `Engine` de SQLAlchemy (ver
get_engine) y devuelven un `pandas.DataFrame`, listas para ser
consumidas por app.py o por un notebook de analisis.

Los filtros de categoria y canal de venta son opcionales y, cuando se
usan, se inyectan en el SQL como parametros ligados (no concatenacion
de strings), evitando SQL injection.
"""

from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

DB_PATH = Path(__file__).parent.parent / "data" / "inventory.db"

# Nivel de servicio objetivo para el stock de seguridad (95% -> Z = 1.65).
DEFAULT_SERVICE_LEVEL_Z = 1.65
DEFAULT_SALES_WINDOW_DAYS = 180
DEFAULT_DEAD_STOCK_DAYS = 60


# ---------------------------------------------------------------------------
# Conexion
# ---------------------------------------------------------------------------
def get_engine(db_path: Path = DB_PATH) -> Engine:
    """Crea (o reutiliza) el engine de SQLAlchemy hacia la base SQLite local."""
    if not Path(db_path).exists():
        raise FileNotFoundError(
            f"No se encontro la base de datos en {db_path}.\n"
            "Ejecuta primero: python generate_data.py && python db_builder.py"
        )
    return create_engine(f"sqlite:///{db_path}", echo=False)


def get_reference_date(engine: Engine) -> str:
    """Devuelve la fecha mas reciente presente en `sales` (formato YYYY-MM-DD)."""
    with engine.connect() as conn:
        max_date = conn.execute(text("SELECT MAX(date) FROM sales")).scalar()
    return max_date


# ---------------------------------------------------------------------------
# Helpers internos para armar filtros parametrizados (categoria / canal)
# ---------------------------------------------------------------------------
def _in_clause(column: str, values: Optional[Sequence[str]], prefix: str):
    """Arma un fragmento `column IN (:p0, :p1, ...)` con params ligados.

    Devuelve ("" , {}) si `values` es None o vacio (sin filtro).
    """
    if not values:
        return "", {}
    placeholders = [f":{prefix}{i}" for i in range(len(values))]
    clause = f"{column} IN ({', '.join(placeholders)})"
    params = {f"{prefix}{i}": v for i, v in enumerate(values)}
    return clause, params


def _and(*clauses: str) -> str:
    """Une clausulas no vacias con AND, para insertar despues de un WHERE base."""
    parts = [c for c in clauses if c]
    return (" AND " + " AND ".join(parts)) if parts else ""


# ---------------------------------------------------------------------------
# 1) ROTACION DE INVENTARIO + DIAS DE SUMINISTRO (DOS)
# ---------------------------------------------------------------------------
def get_rotation_and_dos(
    engine: Engine,
    categories: Optional[Sequence[str]] = None,
    channels: Optional[Sequence[str]] = None,
    window_days: int = DEFAULT_SALES_WINDOW_DAYS,
) -> pd.DataFrame:
    """Rotacion de inventario y Dias de Suministro (DOS) por producto."""
    cat_clause, cat_params = _in_clause("p.category", categories, "cat")
    chan_clause, chan_params = _in_clause("channel", channels, "chan")

    sql = f"""
        WITH sales_agg AS (
            SELECT product_id,
                   SUM(quantity_sold) AS units_sold,
                   SUM(total_usd)     AS revenue
            FROM sales
            WHERE 1=1 {_and(chan_clause)}
            GROUP BY product_id
        )
        SELECT
            p.product_id, p.product_name, p.category, p.supplier_type,
            p.lead_time_days, p.unit_cost_usd, p.unit_price_usd,
            i.current_stock, i.warehouse_zone,
            COALESCE(sa.units_sold, 0) AS units_sold,
            COALESCE(sa.revenue, 0)    AS revenue,
            ROUND(COALESCE(sa.units_sold, 0) * 1.0 / {window_days}, 3) AS avg_daily_demand,
            CASE WHEN COALESCE(sa.units_sold, 0) = 0 THEN NULL
                 ELSE ROUND(i.current_stock * 1.0 / (COALESCE(sa.units_sold, 0) * 1.0 / {window_days}), 1)
            END AS days_of_supply,
            CASE WHEN i.current_stock = 0 THEN NULL
                 ELSE ROUND(COALESCE(sa.units_sold, 0) * 1.0 / i.current_stock, 3)
            END AS inventory_turnover
        FROM products p
        JOIN inventory i ON p.product_id = i.product_id
        LEFT JOIN sales_agg sa ON p.product_id = sa.product_id
        WHERE 1=1 {_and(cat_clause)};
    """
    params = {**cat_params, **chan_params}
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


# ---------------------------------------------------------------------------
# 2) PUNTO DE REORDEN (ROP) CON STOCK DE SEGURIDAD
# ---------------------------------------------------------------------------
def get_reorder_point(
    engine: Engine,
    categories: Optional[Sequence[str]] = None,
    channels: Optional[Sequence[str]] = None,
    window_days: int = DEFAULT_SALES_WINDOW_DAYS,
    service_level_z: float = DEFAULT_SERVICE_LEVEL_Z,
) -> pd.DataFrame:
    """Punto de Reorden (ROP) y clasificacion de estado de stock."""
    cat_clause, cat_params = _in_clause("p.category", categories, "cat")
    chan_clause, chan_params = _in_clause("ds.channel", channels, "chan")

    calendar_sql = f"""
        WITH RECURSIVE calendar(day) AS (
            SELECT date((SELECT MAX(date) FROM sales), '-{window_days} days')
            UNION ALL
            SELECT date(day, '+1 day') FROM calendar
            WHERE day < (SELECT MAX(date) FROM sales)
        ),
        daily_sales AS (
            SELECT product_id, date AS day, SUM(quantity_sold) AS qty
            FROM sales AS ds
            WHERE 1=1 {_and(chan_clause)}
            GROUP BY product_id, date
        )
        SELECT p.product_id, c.day, COALESCE(d.qty, 0) AS qty
        FROM products p
        CROSS JOIN calendar c
        LEFT JOIN daily_sales d ON d.product_id = p.product_id AND d.day = c.day
        WHERE 1=1 {_and(cat_clause)};
    """
    params = {**cat_params, **chan_params}
    with engine.connect() as conn:
        daily = pd.read_sql(text(calendar_sql), conn, params=params)
        # Se agrega alias 'p' a 'products' para solucionar el error de 'p.category'
        products = pd.read_sql(
            text(f"SELECT * FROM products p WHERE 1=1 {_and(cat_clause)}"),
            conn,
            params=cat_params,
        )
        inventory = pd.read_sql("SELECT * FROM inventory", conn)

    demand_stats = (
        daily.groupby("product_id")["qty"]
        .agg(avg_daily_demand="mean", std_daily_demand="std")
        .reset_index()
    )
    demand_stats["std_daily_demand"] = demand_stats["std_daily_demand"].fillna(0)

    df = products.merge(inventory, on="product_id").merge(demand_stats, on="product_id")

    df["safety_stock"] = np.ceil(
        service_level_z * df["std_daily_demand"] * np.sqrt(df["lead_time_days"])
    )
    df["reorder_point"] = np.ceil(
        df["avg_daily_demand"] * df["lead_time_days"] + df["safety_stock"]
    )

    def _classify(row: pd.Series) -> str:
        if row["current_stock"] <= 0:
            return "Agotado"
        if row["current_stock"] <= row["reorder_point"]:
            return "Reordenar"
        if row["avg_daily_demand"] > 0 and row["current_stock"] / row["avg_daily_demand"] > 120:
            return "Sobrestocado"
        if row["avg_daily_demand"] == 0 and row["current_stock"] > 0:
            return "Sobrestocado"
        return "Optimo"

    df["stock_status"] = df.apply(_classify, axis=1)
    return df


# ---------------------------------------------------------------------------
# 3) CLASIFICACION ABC / PARETO (80-20) POR INGRESOS
# ---------------------------------------------------------------------------
def get_abc_classification(
    engine: Engine,
    categories: Optional[Sequence[str]] = None,
    channels: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """Clasificacion ABC (regla 80/20) por ingresos generados."""
    cat_clause, cat_params = _in_clause("p.category", categories, "cat")
    chan_clause, chan_params = _in_clause("s.channel", channels, "chan")

    sql = f"""
        WITH revenue AS (
            SELECT p.product_id, p.product_name, p.category,
                   COALESCE(SUM(s.total_usd), 0) AS total_revenue
            FROM products p
            LEFT JOIN sales s
                   ON p.product_id = s.product_id {_and(chan_clause)}
            WHERE 1=1 {_and(cat_clause)}
            GROUP BY p.product_id, p.product_name, p.category
        ),
        ranked AS (
            SELECT *,
                   SUM(total_revenue) OVER (
                       ORDER BY total_revenue DESC
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS cumulative_revenue,
                   SUM(total_revenue) OVER () AS grand_total
            FROM revenue
        )
        SELECT
            product_id, product_name, category, total_revenue,
            ROUND(100.0 * cumulative_revenue / NULLIF(grand_total, 0), 2) AS cumulative_pct,
            CASE
                WHEN 100.0 * cumulative_revenue / NULLIF(grand_total, 0) <= 80 THEN 'A'
                WHEN 100.0 * cumulative_revenue / NULLIF(grand_total, 0) <= 95 THEN 'B'
                ELSE 'C'
            END AS abc_class
        FROM ranked
        ORDER BY total_revenue DESC;
    """
    params = {**cat_params, **chan_params}
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


# ---------------------------------------------------------------------------
# 4) DEAD STOCK (SIN VENTAS EN +N DIAS)
# ---------------------------------------------------------------------------
def get_dead_stock(
    engine: Engine,
    categories: Optional[Sequence[str]] = None,
    channels: Optional[Sequence[str]] = None,
    days_threshold: int = DEFAULT_DEAD_STOCK_DAYS,
) -> pd.DataFrame:
    """Productos con stock > 0 y sin ventas en los ultimos `days_threshold` dias."""
    cat_clause, cat_params = _in_clause("p.category", categories, "cat")
    chan_clause, chan_params = _in_clause("channel", channels, "chan")

    sql = f"""
        WITH last_sale AS (
            SELECT product_id, MAX(date) AS last_sale_date
            FROM sales
            WHERE 1=1 {_and(chan_clause)}
            GROUP BY product_id
        )
        SELECT
            p.product_id, p.product_name, p.category, p.supplier_type,
            i.current_stock, i.warehouse_zone, p.unit_cost_usd,
            ls.last_sale_date,
            CAST(
                julianday((SELECT MAX(date) FROM sales)) - julianday(ls.last_sale_date)
                AS INTEGER
            ) AS days_since_last_sale,
            ROUND(i.current_stock * p.unit_cost_usd, 2) AS capital_inmovilizado_usd
        FROM products p
        JOIN inventory i ON p.product_id = i.product_id
        LEFT JOIN last_sale ls ON p.product_id = ls.product_id
        WHERE i.current_stock > 0 {_and(cat_clause)}
          AND (
                ls.last_sale_date IS NULL
                OR julianday((SELECT MAX(date) FROM sales)) - julianday(ls.last_sale_date) > {days_threshold}
              )
        ORDER BY capital_inmovilizado_usd DESC;
    """
    params = {**cat_params, **chan_params}
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


# ---------------------------------------------------------------------------
# Vista maestra: combina rotacion/ROP + clase ABC en una sola tabla
# ---------------------------------------------------------------------------
def build_master_table(
    engine: Engine,
    categories: Optional[Sequence[str]] = None,
    channels: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """Une ROP/estado de stock con la clase ABC en una unica tabla por producto."""
    rop = get_reorder_point(engine, categories=categories, channels=channels)
    abc = get_abc_classification(engine, categories=categories, channels=channels)
    master = rop.merge(
        abc[["product_id", "total_revenue", "cumulative_pct", "abc_class"]],
        on="product_id",
        how="left",
    )
    return master


# ---------------------------------------------------------------------------
# Demo por consola
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    engine = get_engine()
    print("Fecha de referencia (max fecha en sales):", get_reference_date(engine))

    print("\n--- Rotacion / DOS (top 5 por rotacion) ---")
    rot = get_rotation_and_dos(engine)
    print(rot.sort_values("inventory_turnover", ascending=False).head(5)[
        ["product_id", "product_name", "current_stock", "units_sold", "inventory_turnover", "days_of_supply"]
    ])

    print("\n--- ROP / Estado de stock ---")
    rop = get_reorder_point(engine)
    print(rop["stock_status"].value_counts())

    print("\n--- Clasificacion ABC ---")
    abc = get_abc_classification(engine)
    print(abc["abc_class"].value_counts())

    print("\n--- Dead Stock (top 5 por capital inmovilizado) ---")
    dead = get_dead_stock(engine)
    print(dead.head(5)[["product_id", "product_name", "current_stock", "days_since_last_sale", "capital_inmovilizado_usd"]])
    print(f"\nCapital total inmovilizado en dead stock: USD {dead['capital_inmovilizado_usd'].sum():,.2f}")