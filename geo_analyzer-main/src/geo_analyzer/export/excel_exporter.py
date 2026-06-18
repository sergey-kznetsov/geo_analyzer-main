from typing import Dict
import pandas as pd
import os


def export_to_excel(result: Dict, output_path: str = "output/report.xlsx") -> None:
    """
    Экспорт результатов анализа в Excel.
    """

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    context = result["context"]
    pois = result["pois"]
    isochrones = result["isochrones"]
    metrics = result["metrics"]

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:

        # --- Контекст
        pd.DataFrame([context]).to_excel(writer, sheet_name="context", index=False)

        # --- POI
        if not pois.empty:
            df_pois = pois.drop(columns="geometry", errors="ignore")
            df_pois.to_excel(writer, sheet_name="pois", index=False)

        # --- Изохроны
        iso_df = isochrones.copy()
        iso_df["area"] = iso_df.geometry.area
        iso_df.drop(columns="geometry").to_excel(writer, sheet_name="isochrones", index=False)

        # --- Метрики
        flat_metrics = {}

        for key, value in metrics.items():
            if isinstance(value, dict):
                for k, v in value.items():
                    flat_metrics[f"{key}_{k}"] = v
            else:
                flat_metrics[key] = value

        pd.DataFrame([flat_metrics]).to_excel(writer, sheet_name="metrics", index=False)