# 🔧 Autoparts Inventory Analytics

Proyecto de **Data Engineering + BI** que simula la gestión de inventario, ventas y logística de un **distribuidor de repuestos automotrices** (Frenos, Suspensión, Motor, Electricidad y Transmisión).

Genera datos sintéticos, los modela en una base de datos relacional local, calcula métricas clásicas de supply chain / retail analytics con SQL avanzado, y las expone en un **dashboard interactivo con Streamlit**.

> 100% gratuito y local: no requiere licencias, servicios cloud ni credenciales. Todo corre con Python + SQLite.

---

## 📌 Objetivo del proyecto

Proyecto pensado como **pieza de portfolio** para vacantes Trainee / Junior de Analista de Datos y BI, mostrando:

- Generación y modelado de datos (Faker, NumPy, Pandas)
- Diseño de base de datos relacional (SQLAlchemy + SQLite)
- SQL avanzado: CTEs, CTE recursivas, `JOIN`, `GROUP BY`, funciones de ventana (`OVER`)
- Métricas de negocio reales de un distribuidor: rotación de inventario, punto de reorden, clasificación ABC, dead stock
- Visualización interactiva con Streamlit + Plotly
- Buenas prácticas: código modular, PEP 8, funciones documentadas, control de errores

---

## 🏗️ Arquitectura

```mermaid
flowchart LR
    A["generate_data.py<br/>Faker + NumPy"] -->|CSV| B[("data/raw/*.csv")]
    B --> C["db_builder.py<br/>SQLAlchemy"]
    C --> D[("data/inventory.db<br/>SQLite")]
    D --> E["src/queries.py<br/>SQL: CTE, JOIN, Window Functions"]
    E --> F["app.py<br/>Streamlit + Plotly"]
    F --> G(["Dashboard interactivo"])
```

1. **`generate_data.py`** crea 150 productos, su inventario y 5.000 ventas de los últimos 180 días.
2. **`db_builder.py`** carga esos CSV a una base **SQLite** local (`data/inventory.db`) vía **SQLAlchemy**, con índices sobre las columnas más consultadas.
3. **`src/queries.py`** contiene toda la lógica analítica: cada métrica de negocio es una función Python que devuelve un `DataFrame`, resultado de una consulta SQL parametrizada (sin concatenar strings, para evitar SQL injection).
4. **`app.py`** consume esas funciones y las renderiza en un dashboard interactivo, sin escribir SQL propio.

---

## 🗂️ Estructura del repositorio

```
autoparts-inventory-analytics/
├── app.py                  # Dashboard Streamlit
├── generate_data.py         # Generador de datos sintéticos (Faker + NumPy)
├── db_builder.py            # CSV -> SQLite (SQLAlchemy)
├── requirements.txt
├── .gitignore
├── README.md
├── src/
│   ├── __init__.py
│   └── queries.py           # Consultas SQL complejas (capa analítica)
└── data/
    ├── raw/                 # CSVs generados (products, inventory, sales)
    └── inventory.db          # Base SQLite generada (no se versiona)
```

---

## 🧬 Modelo de datos (Diagrama ER)

```mermaid
erDiagram
    PRODUCTS ||--|| INVENTORY : "tiene stock en"
    PRODUCTS ||--o{ SALES : "se vende en"

    PRODUCTS {
        string product_id PK
        string sku
        string oem_code
        string product_name
        string category
        string supplier_type
        float  unit_cost_usd
        float  unit_price_usd
        int    lead_time_days
    }
    INVENTORY {
        string product_id PK_FK
        int    current_stock
        string warehouse_zone
    }
    SALES {
        string order_id PK
        string date
        string product_id FK
        int    quantity_sold
        string channel
        float  total_usd
    }
```

### Diccionario de datos

**`products.csv`** (150 filas — catálogo maestro)

| Columna | Tipo | Descripción |
|---|---|---|
| `product_id` | string | Identificador único (`P0001`...`P0150`) |
| `sku` | string | Código interno de depósito |
| `oem_code` | string | Código de referencia del fabricante original |
| `product_name` | string | Nombre comercial (categoría + marca) |
| `category` | string | Frenos / Suspensión / Motor / Electricidad / Transmisión |
| `supplier_type` | string | Nacional o Importado (define el `lead_time_days`) |
| `unit_cost_usd` | float | Costo de compra unitario |
| `unit_price_usd` | float | Precio de venta unitario |
| `lead_time_days` | int | Días de reposición del proveedor (2-7 nacional, 15-45 importado) |

**`inventory.csv`** (150 filas — 1 por producto)

| Columna | Tipo | Descripción |
|---|---|---|
| `product_id` | string | FK a `products` |
| `current_stock` | int | Unidades actualmente en depósito |
| `warehouse_zone` | string | Zona física de almacenamiento (`Zona-A`...`Zona-E`, una por categoría) |

**`sales.csv`** (5.000 filas — últimos 180 días)

| Columna | Tipo | Descripción |
|---|---|---|
| `order_id` | string | Identificador de la orden de venta |
| `date` | date | Fecha de la venta (`YYYY-MM-DD`) |
| `product_id` | string | FK a `products` |
| `quantity_sold` | int | Unidades vendidas en la orden |
| `channel` | string | Mostrador / E-Commerce / Taller Mecánico |
| `total_usd` | float | Importe total de la línea de venta |

---

## 📐 Fórmulas y lógica de negocio

Todas están implementadas en `src/queries.py`.

### 1. Rotación de inventario y Días de Suministro (DOS)

$$\text{Demanda diaria promedio} = \frac{\text{Unidades vendidas (180 días)}}{180}$$

$$\text{Días de Suministro (DOS)} = \frac{\text{Stock actual}}{\text{Demanda diaria promedio}}$$

$$\text{Rotación de inventario} = \frac{\text{Unidades vendidas (180 días)}}{\text{Stock actual}}$$

### 2. Punto de Reorden (ROP) con Stock de Seguridad

Modelo clásico de inventario con demanda variable (nivel de servicio 95%, Z = 1.65):

$$\text{Stock de Seguridad} = Z \times \sigma_{\text{demanda diaria}} \times \sqrt{\text{Lead Time (días)}}$$

$$\text{Punto de Reorden (ROP)} = (\text{Demanda diaria promedio} \times \text{Lead Time}) + \text{Stock de Seguridad}$$

> `σ_demanda diaria` (desvío estándar de la demanda diaria) se calcula sobre una serie temporal **completa** de 180 días por producto (incluyendo días en cero), construida con una **CTE recursiva** que genera el calendario y lo cruza con las ventas reales.

**Estados de stock** derivados del ROP:

| Estado | Condición |
|---|---|
| 🔴 Agotado | `current_stock == 0` |
| 🟠 Reordenar | `current_stock <= reorder_point` |
| 🔵 Sobrestocado | cobertura > 120 días de venta al ritmo actual |
| 🟢 Óptimo | resto de los casos |

### 3. Clasificación ABC / Pareto (regla 80/20)

Productos ordenados por ingreso generado, con ingreso acumulado calculado mediante función de ventana (`SUM(...) OVER (ORDER BY ... ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)`):

| Clase | % acumulado de ingresos |
|---|---|
| A | hasta el 80% |
| B | entre 80% y 95% |
| C | el resto (cola larga) |

### 4. Dead Stock

Productos con `current_stock > 0` cuya última venta ocurrió hace **más de 60 días** (o que nunca vendieron):

$$\text{Capital Inmovilizado} = \text{Stock actual} \times \text{Costo unitario}$$

---

## 🖥️ Dashboard (Streamlit)

- **KPI Cards**: Ventas Totales (USD), Productos Críticos (Agotado + Reordenar), Capital Inmovilizado.
- **Filtros en sidebar**: Categoría de repuesto y Canal de venta — recalculan las consultas SQL en vivo (no son un filtro visual sobre datos ya cargados).
- **Gráfico 1**: Distribución ABC de productos (Plotly, donut chart).
- **Gráfico 2**: Alertas de stock (Agotado / Reordenar / Sobrestocado) + tabla interactiva exportable a **CSV y Excel**.
- **Gráfico 3**: Top 10 repuestos con mayor riesgo de quiebre de stock.
- Detalle expandible de **Dead Stock**.

📸 *Sugerencia de capturas para el README final:* vista general del dashboard, gráfico ABC, tabla de alertas con el botón de exportación, y el gráfico de Top 10 en riesgo.

---

## 🚀 Cómo correrlo

```bash
# 1. Clonar el repo y crear entorno virtual
git clone https://github.com/<tu-usuario>/autoparts-inventory-analytics.git
cd autoparts-inventory-analytics
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Generar los datos sintéticos
python generate_data.py

# 4. Construir la base de datos SQLite
python db_builder.py

# 5. (Opcional) probar las consultas por consola
python -m src.queries

# 6. Levantar el dashboard
streamlit run app.py
```

El dashboard queda disponible en `http://localhost:8501`.

---

## 🛠️ Stack tecnológico

| Capa | Tecnología |
|---|---|
| Generación de datos | Python, Faker, NumPy |
| Procesamiento | Pandas |
| Base de datos | SQLite + SQLAlchemy |
| Analítica | SQL (CTEs, CTE recursiva, window functions) |
| Visualización | Streamlit, Plotly |
| Exportación | openpyxl (Excel) |

---

## 🔭 Posibles mejoras futuras

- Agregar una tabla `suppliers` normalizada (hoy `supplier_type` vive en `products`).
- Versionar snapshots diarios de inventario para calcular rotación con stock promedio real (hoy se aproxima con el stock actual).
- Simular estacionalidad por categoría (ej. mayor demanda de frenos/batería en invierno).
- Migrar la capa de persistencia a PostgreSQL manteniendo la misma interfaz de `src/queries.py`.
- Tests automatizados (pytest) sobre las funciones de `src/queries.py`.

---

## 📄 Licencia

Proyecto de uso educativo / portfolio. Podés reutilizarlo libremente citando la fuente.
