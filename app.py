"""
app.py
------
Dashboard interactivo (Streamlit + Plotly) para el analisis de
inventario, ventas y logistica del distribuidor de autopartes.

Consume exclusivamente funciones de src/queries.py: la app no escribe
SQL propio, solo pide datos ya calculados y los renderiza.

Ejecucion:
    streamlit run app.py
"""

from io import BytesIO

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import text

from src.queries import (
    DEFAULT_DEAD_STOCK_DAYS,
    build_master_table,
    get_dead_stock,
    get_engine,
    get_reference_date,
)

# ---------------------------------------------------------------------------
# Configuracion de pagina
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Análisis de inventario de autopartes",
    page_icon="🔧",
    layout="wide",
)

STATUS_COLORS = {
    "Agotado": "#e74c3c",
    "Reordenar": "#f39c12",
    "Optimo": "#2ecc71",
    "Sobrestocado": "#3498db",
}
ABC_COLORS = {"A": "#2ecc71", "B": "#f1c40f", "C": "#e74c3c"}


# ---------------------------------------------------------------------------
# Carga de datos (cacheada)
# ---------------------------------------------------------------------------
@st.cache_resource
def load_engine():
    return get_engine()


@st.cache_data
def get_filter_options(_engine):
    """Categorias y canales disponibles, sin aplicar ningun filtro (para la sidebar)."""
    with _engine.connect() as conn:
        categories = pd.read_sql(
            text("SELECT DISTINCT category FROM products ORDER BY category"), conn
        )["category"].tolist()
        channels = pd.read_sql(
            text("SELECT DISTINCT channel FROM sales ORDER BY channel"), conn
        )["channel"].tolist()
    return categories, channels


@st.cache_data
def load_master(_engine, categories: tuple, channels: tuple) -> pd.DataFrame:
    df = build_master_table(_engine, categories=list(categories) or None, channels=list(channels) or None)
    
    # Calculamos la columna days_of_supply de forma segura y global para todo el dashboard
    df["days_of_supply"] = df.apply(
        lambda r: 0 if r["current_stock"] <= 0 or r["avg_daily_demand"] == 0 
        else round(r["current_stock"] / r["avg_daily_demand"], 1),
        axis=1,
    )
    return df


@st.cache_data
def load_dead_stock(_engine, categories: tuple, channels: tuple) -> pd.DataFrame:
    return get_dead_stock(_engine, categories=list(categories) or None, channels=list(channels) or None,
                           days_threshold=DEFAULT_DEAD_STOCK_DAYS)


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="alertas_stock")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Carga inicial + validacion de que la base ya fue construida
# ---------------------------------------------------------------------------
try:
    engine = load_engine()
except FileNotFoundError as exc:
    st.error(
        "⚠️ No se encontro la base de datos.\n\n"
        "Corre estos comandos antes de levantar el dashboard:\n\n"
        "```bash\npython generate_data.py\npython db_builder.py\n```"
    )
    st.stop()

reference_date = get_reference_date(engine)
all_categories, all_channels = get_filter_options(engine)

# ---------------------------------------------------------------------------
# Sidebar - filtros interactivos
# ---------------------------------------------------------------------------
st.sidebar.title("🔧 Filtros")
st.sidebar.caption(f"Datos actualizados al {reference_date}")

selected_categories = st.sidebar.multiselect(
    "Categoria de repuesto", options=all_categories, default=all_categories
)
selected_channels = st.sidebar.multiselect(
    "Canal de venta", options=all_channels, default=all_channels
)

if not selected_categories or not selected_channels:
    st.warning("Seleccioná al menos una categoria y un canal en la barra lateral.")
    st.stop()

master = load_master(engine, tuple(selected_categories), tuple(selected_channels))
dead_stock = load_dead_stock(engine, tuple(selected_categories), tuple(selected_channels))

# ---------------------------------------------------------------------------
# Header + KPI cards
# ---------------------------------------------------------------------------
st.title("📦 Análisis de inventario de autopartes")
st.caption("Distribuidor de repuestos automotrices · Frenos · Suspension · Motor · Electricidad · Transmision")

total_sales_usd = master["total_revenue"].sum()
critical_products = master["stock_status"].isin(["Agotado", "Reordenar"]).sum()
capital_inmovilizado = dead_stock["capital_inmovilizado_usd"].sum()

kpi1, kpi2, kpi3 = st.columns(3)
kpi1.metric("💰 Ventas Totales (USD)", f"US$ {total_sales_usd:,.0f}")
kpi2.metric("🚨 Productos Criticos", f"{critical_products}", help="Productos en estado Agotado o Reordenar")
kpi3.metric("🧊 Capital Inmovilizado (USD)", f"US$ {capital_inmovilizado:,.0f}",
            help=f"Stock valorizado de productos sin ventas hace mas de {DEFAULT_DEAD_STOCK_DAYS} dias")

st.divider()

# ---------------------------------------------------------------------------
# Grafico 1 - Distribucion ABC (Pareto 80/20)
# ---------------------------------------------------------------------------
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("📊 Distribucion ABC de productos")
    abc_counts = master["abc_class"].value_counts().reindex(["A", "B", "C"]).fillna(0).reset_index()
    abc_counts.columns = ["abc_class", "cantidad_productos"]
    abc_revenue = master.groupby("abc_class")["total_revenue"].sum().reindex(["A", "B", "C"]).fillna(0)

    fig_abc = px.pie(
        abc_counts, values="cantidad_productos", names="abc_class",
        color="abc_class", color_discrete_map=ABC_COLORS, hole=0.45,
        title="Cantidad de productos por clase",
    )
    fig_abc.update_traces(textinfo="percent+label")
    st.plotly_chart(fig_abc, use_container_width=True)
    if total_sales_usd > 0:
        class_a_products = int(abc_counts.loc[abc_counts.abc_class == "A", "cantidad_productos"].iloc[0])
        st.caption(
            f"Clase A concentra US$ {abc_revenue['A']:,.0f} "
            f"({abc_revenue['A'] / total_sales_usd:.0%} de los ingresos) "
            f"con solo {class_a_products} productos."
        )

# ---------------------------------------------------------------------------
# Grafico 3 (se ubica junto al 1 para mejor layout) - Top 10 riesgo de quiebre
# ---------------------------------------------------------------------------
with col2:
    st.subheader("🔥 Top 10 riesgo de quiebre de stock")
    risk = master[master["stock_status"].isin(["Agotado", "Reordenar"])].copy()
    
    # Cálculo seguro usando current_stock y avg_daily_demand
    risk["dias_cobertura"] = risk.apply(
        lambda r: 0 if r["current_stock"] <= 0 or r["avg_daily_demand"] == 0 
        else round(r["current_stock"] / r["avg_daily_demand"], 1),
        axis=1,
    )
    
    risk = risk.sort_values(["stock_status", "dias_cobertura"], ascending=[False, True]).head(10)

    if risk.empty:
        st.info("No hay productos en riesgo de quiebre con los filtros actuales. ✅")
    else:
        # Columna auxiliar para evitar barras de longitud 0 en productos agotados
        risk["dias_plot"] = risk["dias_cobertura"].apply(lambda x: max(x, 1.5))
        
        # Etiqueta de texto personalizada para cada barra
        risk["texto_etiqueta"] = risk.apply(
            lambda r: "AGOTADO (0 días)" if r["current_stock"] <= 0 else f"{r['dias_cobertura']} días", 
            axis=1
        )

        fig_risk = px.bar(
            risk.sort_values("dias_cobertura", ascending=True),
            x="dias_plot", 
            y="product_name", 
            orientation="h",
            color="dias_cobertura",
            color_continuous_scale=["#D90429", "#F77F00"],  # Gradiente estricto de rojo a naranja
            labels={"dias_plot": "Días de cobertura restantes", "product_name": ""},
            title="Menor cobertura de stock = Mayor urgencia de reposición",
            text="texto_etiqueta"
        )
        fig_risk.update_layout(
            yaxis={"categoryorder": "total ascending"},
            coloraxis_showscale=False,
            margin=dict(t=40, b=20, l=20, r=40)
        )
        fig_risk.update_traces(textposition="outside")
        st.plotly_chart(fig_risk, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# Grafico 2 - Alertas de stock + tabla exportable
# ---------------------------------------------------------------------------
st.subheader("🚦 Alertas de Stock y Estado Operativo")

status_counts = master["stock_status"].value_counts().reindex(
    ["Agotado", "Reordenar", "Optimo", "Sobrestocado"]
).fillna(0).reset_index()
status_counts.columns = ["stock_status", "cantidad"]

fig_status = px.bar(
    status_counts, x="stock_status", y="cantidad", color="stock_status",
    color_discrete_map=STATUS_COLORS, text="cantidad",
    labels={"stock_status": "Estado de stock", "cantidad": "Cantidad de productos"},
)
fig_status.update_traces(textposition="outside")
fig_status.update_layout(showlegend=False, margin=dict(t=20, b=20, l=20, r=20))
st.plotly_chart(fig_status, use_container_width=True)

st.markdown("### 📋 Detalle de Productos que Requieren Atención")

alert_cols = [
    "product_id", "product_name", "category", "supplier_type", "current_stock",
    "reorder_point", "safety_stock", "days_of_supply", "lead_time_days", "stock_status",
]
alerts_table = master[master["stock_status"] != "Optimo"][alert_cols].sort_values(
    "stock_status"
).reset_index(drop=True)

# Tabla interactiva mejorada con column_config para nombres amigables y formatos limpios
st.dataframe(
    alerts_table,
    use_container_width=True,
    hide_index=True,
    column_config={
        "product_id": st.column_config.TextColumn("ID", width="small"),
        "product_name": st.column_config.TextColumn("Repuesto / Producto", width="medium"),
        "category": st.column_config.TextColumn("Categoría", width="small"),
        "supplier_type": st.column_config.TextColumn("Proveedor", width="small"),
        "current_stock": st.column_config.NumberColumn("Stock Actual", format="%d un."),
        "reorder_point": st.column_config.NumberColumn("Punto Reorden", format="%d un."),
        "safety_stock": st.column_config.NumberColumn("Stock Seg.", format="%d un."),
        "days_of_supply": st.column_config.NumberColumn("Días Cobertura", format="%.1f días"),
        "lead_time_days": st.column_config.NumberColumn("Tiempo de entrega", format="%d días"),
        "stock_status": st.column_config.TextColumn("Estado Crítico", width="small"),
    }
)

# Botones de descarga organizados y con ancho adaptable
dl1, dl2, _ = st.columns([1, 1, 2])
with dl1:
    st.download_button(
        "⬇️ Descargar CSV",
        data=alerts_table.to_csv(index=False).encode("utf-8"),
        file_name="alertas_stock.csv",
        mime="text/csv",
        use_container_width=True,
    )
with dl2:
    st.download_button(
        "⬇️ Descargar Excel",
        data=to_excel_bytes(alerts_table),
        file_name="alertas_stock.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

st.divider()

# ---------------------------------------------------------------------------
# Dead stock detallado
# ---------------------------------------------------------------------------
with st.expander(f"🧊 Ver detalle de Dead Stock (sin ventas hace +{DEFAULT_DEAD_STOCK_DAYS} días)"):
    if dead_stock.empty:
        st.info("No hay productos en dead stock con los filtros actuales. ✅")
    else:
        # Sumatoria métrica rápida para dar contexto financiero al expandir
        total_immobilized = dead_stock["capital_inmovilizado_usd"].sum()
        st.markdown(f"**Capital total inmovilizado en esta selección:** :red[**USD {total_immobilized:,.2f}**]")
        
        ds_display = dead_stock[
            ["product_id", "product_name", "category", "current_stock",
             "last_sale_date", "days_since_last_sale", "capital_inmovilizado_usd"]
        ].copy()

        st.dataframe(
            ds_display,
            use_container_width=True,
            hide_index=True,
            column_config={
                "product_id": st.column_config.TextColumn("ID", width="small"),
                "product_name": st.column_config.TextColumn("Repuesto / Producto", width="medium"),
                "category": st.column_config.TextColumn("Categoría", width="small"),
                "current_stock": st.column_config.NumberColumn("Stock Inmovilizado", format="%d un."),
                "last_sale_date": st.column_config.TextColumn("Última Venta", width="small"),
                "days_since_last_sale": st.column_config.NumberColumn("Días Inactivo", format="%d días"),
                "capital_inmovilizado_usd": st.column_config.NumberColumn("Capital (USD)", format="USD $%,.2f"),
            }
        )

        # Botones de descarga para el reporte de Dead Stock
        ds1, ds2, _ = st.columns([1, 1, 2])
        with ds1:
            st.download_button(
                "⬇️ Descargar Dead Stock (CSV)",
                data=ds_display.to_csv(index=False).encode("utf-8"),
                file_name="dead_stock_report.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with ds2:
            st.download_button(
                "⬇️ Descargar Dead Stock (Excel)",
                data=to_excel_bytes(ds_display),
                file_name="dead_stock_report.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

st.caption(
    "Fuente: base local SQLite (data/inventory.db) generada con datos sintéticos. "
    "Proyecto con fines demostrativos / portfolio de Analista de Datos - BI."
)
