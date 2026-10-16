"""
generate_data.py
-----------------
Genera datos sinteticos y realistas para un distribuidor de repuestos
automotrices (autopartes) y los guarda como CSV en data/raw/.

Se generan tres entidades:
    - products.csv    -> catalogo de 150 productos (5 categorias)
    - inventory.csv   -> stock actual por producto y zona de deposito
    - sales.csv        -> 5000 ordenes de venta en los ultimos 180 dias

Enfoque de diseno:
    * Faker se usa para generar identificadores "tipo texto" (SKU, codigo
      OEM) que deben parecer reales pero son arbitrarios.
    * NumPy se usa para las distribuciones estadisticas del negocio:
      popularidad de productos (Pareto 80/20), variabilidad de demanda
      diaria y perfiles de stock (agotado / bajo / normal / sobrestock).
    * El inventario se genera DESPUES de las ventas y en funcion de la
      demanda real de cada producto, para que las metricas de rotacion,
      dias de suministro y punto de reorden que calcula src/queries.py
      tengan sentido de negocio (no son numeros puramente aleatorios).

Ejecucion:
    python generate_data.py
"""

import random
import string
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

# ---------------------------------------------------------------------------
# Configuracion global / semillas para reproducibilidad
# ---------------------------------------------------------------------------
SEED = 42
random.seed(SEED)
np.random.seed(SEED)
fake = Faker("es_AR")
Faker.seed(SEED)
rng = np.random.default_rng(SEED)

OUTPUT_DIR = Path(__file__).parent / "data" / "raw"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

N_PRODUCTS = 150
N_SALES = 5000
SALES_WINDOW_DAYS = 180
REFERENCE_DATE = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

# Categorias de negocio requeridas por el catalogo
CATEGORIES = ["Frenos", "Suspension", "Motor", "Electricidad", "Transmision"]

# Nombres base de producto por categoria (vocabulario real de autopartes)
BASE_NAMES = {
    "Frenos": [
        "Pastilla de freno", "Disco de freno", "Cilindro maestro",
        "Kit de freno", "Caliper de freno", "Tambor de freno", "Cinta de freno",
    ],
    "Suspension": [
        "Amortiguador", "Resorte helicoidal", "Rotula de suspension",
        "Buje de suspension", "Terminal de direccion", "Barra estabilizadora",
    ],
    "Motor": [
        "Filtro de aceite", "Bomba de agua", "Correa de distribucion",
        "Junta de culata", "Bujia", "Filtro de aire", "Kit de distribucion",
    ],
    "Electricidad": [
        "Bateria", "Alternador", "Motor de arranque", "Bobina de encendido",
        "Sensor de oxigeno", "Regulador de voltaje",
    ],
    "Transmision": [
        "Kit de embrague", "Disco de embrague", "Caja de cambios",
        "Cardan", "Homocinetica", "Aceite de transmision",
    ],
}

# Marcas de repuestos aftermarket reales, usadas solo como dato categorico
BRANDS = [
    "Bosch", "Sadar", "Fric Rot", "Corven", "Valeo", "NGK",
    "Magneti Marelli", "Varta", "SKF", "Fremax",
]

WAREHOUSE_ZONE_BY_CATEGORY = {
    "Frenos": "Zona-A", "Suspension": "Zona-B", "Motor": "Zona-C",
    "Electricidad": "Zona-D", "Transmision": "Zona-E",
}

SALES_CHANNELS = ["Mostrador", "E-Commerce", "Taller Mecanico"]
CHANNEL_WEIGHTS = [0.45, 0.30, 0.25]  # el mostrador sigue siendo el canal dominante


# ---------------------------------------------------------------------------
# 1. PRODUCTS
# ---------------------------------------------------------------------------
def generate_products() -> pd.DataFrame:
    """Genera el catalogo de productos con costo, precio y lead time.

    lead_time_days depende de supplier_type: los proveedores nacionales
    reponen en 2-7 dias; los importados (repuestos que llegan por barco
    o aduana) demoran entre 15 y 45 dias.
    """
    rows = []
    for i in range(1, N_PRODUCTS + 1):
        product_id = f"P{i:04d}"
        category = random.choice(CATEGORIES)
        base_name = random.choice(BASE_NAMES[category])
        brand = random.choice(BRANDS)
        product_name = f"{base_name} {brand}"

        # Faker genera codigos alfanumericos con formato realista
        sku = fake.bothify(text="???-####", letters=string.ascii_uppercase)
        oem_code = fake.bothify(text="??########", letters=string.ascii_uppercase).upper()

        supplier_type = random.choices(["Nacional", "Importado"], weights=[0.55, 0.45])[0]
        lead_time_days = (
            int(rng.integers(2, 8)) if supplier_type == "Nacional" else int(rng.integers(15, 46))
        )

        unit_cost_usd = round(float(rng.uniform(3, 250)), 2)
        margin = rng.uniform(1.25, 1.9)  # margen comercial tipico del canal repuestos
        unit_price_usd = round(unit_cost_usd * margin, 2)

        rows.append(
            [product_id, sku, oem_code, product_name, category, supplier_type,
             unit_cost_usd, unit_price_usd, lead_time_days]
        )

    return pd.DataFrame(
        rows,
        columns=[
            "product_id", "sku", "oem_code", "product_name", "category",
            "supplier_type", "unit_cost_usd", "unit_price_usd", "lead_time_days",
        ],
    )


# ---------------------------------------------------------------------------
# 2. SALES
# ---------------------------------------------------------------------------
def generate_sales(products: pd.DataFrame) -> pd.DataFrame:
    """Genera 5000 ordenes de venta en los ultimos SALES_WINDOW_DAYS dias.

    La popularidad de los productos sigue una distribucion de Pareto
    (unos pocos productos concentran la mayor parte de las ventas), lo
    que habilita una clasificacion ABC realista mas adelante.

    Ademas se fuerzan dos grupos de control para poder demostrar reglas
    de negocio en el dashboard:
        - dead_candidates: productos que SI vendieron, pero no en los
          ultimos 60 dias (candidatos a "dead stock").
        - zero_demand: productos que nunca vendieron en la ventana.
    """
    product_ids = products["product_id"].tolist()
    price_map = dict(zip(products["product_id"], products["unit_price_usd"]))
    start_date = REFERENCE_DATE - timedelta(days=SALES_WINDOW_DAYS)

    weights = rng.pareto(a=2.0, size=len(product_ids)) + 0.1
    weights = weights / weights.sum()

    dead_candidates = set(random.sample(product_ids, 18))
    zero_demand = set(random.sample([p for p in product_ids if p not in dead_candidates], 7))

    rows = []
    order_num = 1
    while order_num <= N_SALES:
        product_id = np.random.choice(product_ids, p=weights)
        if product_id in zero_demand:
            continue  # este producto nunca aparece en ventas

        if product_id in dead_candidates:
            day_offset = int(rng.integers(0, SALES_WINDOW_DAYS - 65))  # sin ventas en los ult. 60+ dias
        else:
            day_offset = int(rng.integers(0, SALES_WINDOW_DAYS))

        sale_date = start_date + timedelta(days=day_offset)
        quantity_sold = int(rng.integers(1, 6))
        channel = random.choices(SALES_CHANNELS, weights=CHANNEL_WEIGHTS)[0]
        total_usd = round(price_map[product_id] * quantity_sold, 2)

        rows.append(
            [f"O{order_num:05d}", sale_date.strftime("%Y-%m-%d"), product_id,
             quantity_sold, channel, total_usd]
        )
        order_num += 1

    sales = pd.DataFrame(
        rows, columns=["order_id", "date", "product_id", "quantity_sold", "channel", "total_usd"]
    )
    # se exponen los sets de control para que generate_inventory los reutilice
    sales.attrs["dead_candidates"] = dead_candidates
    sales.attrs["zero_demand"] = zero_demand
    return sales


# ---------------------------------------------------------------------------
# 3. INVENTORY
# ---------------------------------------------------------------------------
def generate_inventory(products: pd.DataFrame, sales: pd.DataFrame) -> pd.DataFrame:
    """Genera el stock actual en funcion de la demanda historica real.

    Cada producto recibe un "perfil de stock" (agotado / bajo / normal /
    sobrestock) que determina cuantos "dias de suministro" objetivo tiene
    su stock actual. Esto produce una mezcla realista de alertas para el
    dashboard, en lugar de stock puramente al azar.
    """
    product_ids = products["product_id"].tolist()
    category_map = dict(zip(products["product_id"], products["category"]))

    demand = sales.groupby("product_id")["quantity_sold"].sum().reindex(product_ids, fill_value=0)
    avg_daily_demand = demand / SALES_WINDOW_DAYS

    dead_candidates = sales.attrs.get("dead_candidates", set())
    zero_demand = sales.attrs.get("zero_demand", set())

    # Mezcla de perfiles: 8% agotado, 17% bajo, 50% normal, 25% sobrestock
    profiles = rng.choice(
        ["agotado", "bajo", "normal", "alto"], size=len(product_ids), p=[0.08, 0.17, 0.50, 0.25]
    )

    rows = []
    for product_id, profile in zip(product_ids, profiles):
        daily = avg_daily_demand[product_id]
        zone = WAREHOUSE_ZONE_BY_CATEGORY[category_map[product_id]]

        if product_id in zero_demand or product_id in dead_candidates:
            # dead stock: mercaderia inmovilizada pese a no tener rotacion reciente
            stock = int(rng.integers(15, 120))
        elif profile == "agotado":
            stock = 0
        elif profile == "bajo":
            target_dos = rng.uniform(3, 15)
            stock = max(0, int(round(daily * target_dos + rng.normal(0, 2))))
        elif profile == "normal":
            target_dos = rng.uniform(25, 70)
            stock = max(0, int(round(daily * target_dos + rng.normal(0, 5))))
        else:  # "alto" -> sobrestock
            target_dos = rng.uniform(120, 300)
            if daily > 0:
                stock = max(1, int(round(daily * target_dos + rng.normal(0, 10))))
            else:
                stock = int(rng.integers(80, 250))

        rows.append([product_id, stock, zone])

    return pd.DataFrame(rows, columns=["product_id", "current_stock", "warehouse_zone"])


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> None:
    print("Generando catalogo de productos...")
    products = generate_products()

    print("Generando historico de ventas (180 dias)...")
    sales = generate_sales(products)

    print("Generando inventario en funcion de la demanda...")
    inventory = generate_inventory(products, sales)

    products.to_csv(OUTPUT_DIR / "products.csv", index=False)
    inventory.to_csv(OUTPUT_DIR / "inventory.csv", index=False)
    sales.to_csv(OUTPUT_DIR / "sales.csv", index=False)

    print(f"OK - products.csv  -> {len(products)} filas")
    print(f"OK - inventory.csv -> {len(inventory)} filas")
    print(f"OK - sales.csv     -> {len(sales)} filas")
    print(f"Archivos guardados en: {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
