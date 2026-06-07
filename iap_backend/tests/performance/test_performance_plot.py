from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import pytest


pytestmark = [pytest.mark.performance, pytest.mark.manual]

REPORT_DIR = Path("/app/tests/reports")
DEFAULT_REPORTS = [
    "併發效能測試報告_5090_8.xlsx",
    "併發效能測試報告_L20_8.xlsx",
    "併發效能測試報告_least-busy_8_1778231036.xlsx",
]
DEFAULT_LABELS = [
    "RTX 5090",
    "L20",
    "LeastBusy Routing (RTX5090+L20)",
]


def generate_performance_plot(
    report_paths: list[Path],
    labels: list[str],
    output_file: Path,
) -> Path:
    dfs = [pd.read_excel(path) for path in report_paths]
    iap_dict = {}
    iap_ae = {}

    for label, df in zip(labels, dfs):
        iap_df = df[df["API 名稱"] == "iap_retrieve_v3"].sort_values("並發人數(N)")
        iap_ae_df = df[df["API 名稱"] == "iap_ae_retrieve_v3"].sort_values("並發人數(N)")
        iap_dict[label] = iap_df["中位數_整體完工時間(秒)"].tolist()
        iap_ae[label] = iap_ae_df["中位數_整體完工時間(秒)"].tolist()

    x = iap_df["並發人數(N)"].tolist()
    fig, axes = plt.subplots(1, 2, figsize=(16, 9))
    fig.suptitle("AI Inference Routing Strategy Analysis", fontsize=28, fontweight="bold")

    for label, y in iap_dict.items():
        axes[0].plot(x, y, marker="o", linewidth=2.5, markersize=8, label=label)
        for xi, yi in zip(x, y):
            axes[0].text(xi, yi + 0.5, f"{yi:.2f}", fontsize=10)
    axes[0].set_title("Heavy Reasoning Workload\n(iap_retrieve_v3)", fontsize=20, fontweight="bold")
    axes[0].set_xlabel("Concurrent Requests", fontsize=15)
    axes[0].set_ylabel("Complete Time (s)", fontsize=15)
    axes[0].set_ylim(0, 50)
    axes[0].grid(True, alpha=0.3)
    axes[0].legend(fontsize=11, loc="upper left")

    for label, y in iap_ae.items():
        axes[1].plot(x, y, marker="o", linewidth=2.5, markersize=8, label=label)
        for xi, yi in zip(x, y):
            axes[1].text(xi, yi + 0.5, f"{yi:.2f}", fontsize=10)
    axes[1].set_title("Lightweight Throughput Workload\n(iap_ae_retrieve_v3)", fontsize=20, fontweight="bold")
    axes[1].set_xlabel("Concurrent Requests", fontsize=15)
    axes[1].set_ylabel("Complete Time (s)", fontsize=15)
    axes[1].set_ylim(0, 50)
    axes[1].grid(True, alpha=0.3)
    axes[1].legend(fontsize=11, loc="upper left")

    fig.text(
        0.5,
        0.02,
        "Environment: RTX5090 (Qwen3:8B bf16) + L20 (Qwen3:8B AWQ)  |  Unit: Seconds (s)  |  Lower is better",
        ha="center",
        fontsize=12,
        alpha=0.7,
    )
    plt.tight_layout(rect=[0, 0.05, 1, 0.95])
    output_file.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_file, dpi=600)
    plt.close(fig)
    return output_file


def test_generate_performance_plot():
    report_paths = [REPORT_DIR / filename for filename in DEFAULT_REPORTS]
    missing = [str(path) for path in report_paths if not path.exists()]
    if missing:
        pytest.skip(f"Performance report files are missing: {missing}")

    output_file = generate_performance_plot(
        report_paths=report_paths,
        labels=DEFAULT_LABELS,
        output_file=REPORT_DIR / "performance_dashboard.png",
    )

    assert output_file.exists()
