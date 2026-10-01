from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager, patches
import numpy as np
import pandas as pd

INK = "#111111"
LOW = "#D14900"
OTHER = "#245E91"
OPPORTUNITY = "#007C6C"
PRACTICAL = "#245E91"
OUTCOME = "#D58A00"


def setup_style() -> None:
    for candidate in (Path("C:/Windows/Fonts/malgun.ttf"), Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf")):
        if candidate.exists():
            font_manager.fontManager.addfont(str(candidate))
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(candidate)).get_name()
            break
    plt.rcParams.update(
        {
            "axes.unicode_minus": False,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "text.color": INK,
            "axes.labelcolor": INK,
            "xtick.color": INK,
            "ytick.color": INK,
            "axes.edgecolor": INK,
            "figure.dpi": 160,
            "savefig.dpi": 240,
        }
    )


def finish_axis(ax: plt.Axes) -> None:
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_linewidth(1.1)
    ax.tick_params(axis="both", labelsize=10.5, width=1.0, length=3)


def save(fig: plt.Figure, output_dir: Path, filename: str, source: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.text(0.012, 0.012, source, ha="left", va="bottom", fontsize=7.0, color="#444444")
    fig.savefig(output_dir / filename, bbox_inches="tight", pad_inches=0.06, facecolor="white")
    plt.close(fig)


def culture_nuri(descriptive_dir: Path, output_dir: Path) -> None:
    fields = ["도서", "영화", "공연", "음악", "전시"]
    frame = pd.read_csv(descriptive_dir / "01_culture_nuri_field_usage.csv").set_index("분야").loc[fields]
    values = frame["비중_퍼센트"].to_numpy()
    fig, ax = plt.subplots(figsize=(5.8, 3.55))
    y = np.arange(len(fields))
    ax.barh(y, values, color=[OTHER, OTHER, LOW, LOW, LOW], height=0.58)
    ax.set_yticks(y, fields)
    ax.invert_yaxis()
    ax.set_xlabel("전체 이용건수 대비 비중(%)", fontsize=11.5, weight="bold")
    ax.set_title("서울시 문화누리카드 이용건수(분야별) 비중", loc="left", fontsize=14.2, weight="bold")
    ax.set_xlim(0, values.max() * 1.18)
    for index, value in enumerate(values):
        label = f"{value:.1f}%" if value >= 1 else f"{value:.2f}%"
        ax.text(value + values.max() * 0.012, index, label, va="center", fontsize=11.0, weight="bold")
    finish_axis(ax)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    fig.tight_layout(rect=(0, 0.07, 1, 1), pad=0.35)
    save(fig, output_dir, "01_culture_nuri_field_share.png", "출처: 2025년 서울시 문화누리카드 발급·이용 현황 재구성.")


def _two_bar(summary: pd.DataFrame, column: str, title: str, ylabel: str, output_dir: Path, filename: str) -> None:
    order = ["저소득 집단", "그 외 소득집단"]
    data = summary.set_index("소득집단").loc[order]
    values = data[column].to_numpy()
    fig, ax = plt.subplots(figsize=(4.5, 3.25))
    bars = ax.bar([0, 1], values, color=[LOW, OTHER], width=0.56)
    ax.set_xticks([0, 1], order)
    ax.set_ylabel(ylabel, fontsize=11.5, weight="bold")
    ax.set_title(title, loc="left", fontsize=14.0, weight="bold")
    ax.set_ylim(0, values.max() * 1.3)
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, value + values.max() * 0.035, f"{value:.1f}%", ha="center", fontsize=11.5, weight="bold")
    finish_axis(ax)
    fig.tight_layout(rect=(0, 0.07, 1, 1), pad=0.35)
    save(fig, output_dir, filename, "출처: 문화체육관광부, 국민문화예술활동조사 2023~2025 원자료 재분석.")


def income_stage_figures(descriptive_dir: Path, output_dir: Path) -> None:
    summary = pd.read_csv(descriptive_dir / "03_income_stage_summary.csv")
    _two_bar(summary, "가중_최근관람률_퍼센트", "소득집단별 최근 1년 기초예술 관람률", "최근 1년 관람률(%)", output_dir, "02_recent_attendance_by_income.png")
    _two_bar(summary, "가중_관람의향률_퍼센트", "소득집단별 기초예술 관람의향률", "관람의향률(%)", output_dir, "03_intention_by_income.png")
    _two_bar(summary, "의향자중_가중_최근관람률_퍼센트", "관람의향자 중 최근 1년 관람률", "의향자 중 최근 관람률(%)", output_dir, "04_attendance_among_intenders.png")


def conceptual_model(output_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.4, 3.25))
    ax.set_xlim(0, 12.4)
    ax.set_ylim(0, 5)
    ax.axis("off")
    ax.set_title("기초예술 관람의향의 분석 모형", loc="left", fontsize=14.2, weight="bold")

    def box(x, y, width, height, title, detail, color):
        shape = patches.FancyBboxPatch((x, y), width, height, boxstyle="round,pad=0.03,rounding_size=0.1", facecolor=color, edgecolor=INK, linewidth=1.6)
        ax.add_patch(shape)
        ax.text(x + width / 2, y + height * 0.64, title, ha="center", va="center", fontsize=13.0, weight="bold")
        if detail:
            ax.text(x + width / 2, y + height * 0.30, f"({detail})", ha="center", va="center", fontsize=8.8, weight="bold")

    box(0.15, 2.95, 4.15, 1.35, "문화 의식 형성 기회", "문화 자본·문화 교육·최근 문화 경험", "#00A991")
    box(0.15, 0.65, 4.15, 1.35, "현실적 여건", "경제·시간·건강·사회관계·지역 규모", "#4C82B8")
    box(6.15, 1.85, 2.55, 1.25, "기초예술 관람의향", "", "#E3A21A")
    box(10.25, 1.85, 1.9, 1.25, "관람 결정", "", "#F2C14E")
    ax.annotate("", xy=(6.15, 2.48), xytext=(4.3, 3.62), arrowprops={"arrowstyle": "->", "lw": 2.2, "color": INK})
    ax.annotate("", xy=(6.15, 2.48), xytext=(4.3, 1.32), arrowprops={"arrowstyle": "->", "lw": 2.2, "color": INK})
    ax.annotate("", xy=(10.25, 2.48), xytext=(8.7, 2.48), arrowprops={"arrowstyle": "->", "lw": 1.5, "linestyle": (0, (3, 3)), "color": INK})
    fig.tight_layout(rect=(0, 0.07, 1, 1), pad=0.2)
    save(fig, output_dir, "05_intention_analysis_model.png", "출처: 연구진 구성.")


def _contribution(source: Path, value_column: str, title: str, xlabel: str, output_dir: Path, filename: str) -> None:
    frame = pd.read_csv(source).sort_values(value_column)
    colors = frame["범주"].map({
        "문화 자본": OPPORTUNITY,
        "최근 문화 경험": OPPORTUNITY,
        "경제적 여건": PRACTICAL,
        "시간적 여건": PRACTICAL,
        "신체·정신적 여건": PRACTICAL,
        "사회적 관계": PRACTICAL,
        "지역 규모": PRACTICAL,
    })
    values = frame[value_column].to_numpy()
    fig, ax = plt.subplots(figsize=(6.1, 4.1))
    y = np.arange(len(frame))
    ax.barh(y, values, color=colors, height=0.62)
    ax.set_yticks(y, frame["범주"])
    ax.set_xlabel(xlabel, fontsize=11.5, weight="bold")
    ax.set_title(title, loc="left", fontsize=14.0, weight="bold")
    ax.axvline(0, color=INK, linewidth=1.0)
    ax.set_xlim(min(-2.0, values.min() * 1.5), values.max() * 1.18)
    for index, value in enumerate(values):
        ax.text(value + values.max() * 0.012 if value >= 0 else 0.7, index, f"{value:.1f}%", va="center", fontsize=10.7, weight="bold")
    finish_axis(ax)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    fig.tight_layout(rect=(0, 0.07, 1, 1), pad=0.35)
    save(fig, output_dir, filename, "출처: 문화체육관광부, 국민문화예술활동조사 2023~2025 원자료 재분석.")


def model_figures(model_dir: Path, output_dir: Path) -> None:
    _contribution(model_dir / "09_mean_intention_category_contribution.csv", "네장르_동일가중평균_기여율_퍼센트", "기초예술 관람의향 설명 기여율", "관람의향 설명 기여율(%)", output_dir, "06_intention_category_contribution.png")
    _contribution(model_dir / "10_mean_gap_category_contribution.csv", "네장르_동일가중평균_구성비_퍼센트", "소득집단 간 기초예술 관람의향 격차 기여율", "관람의향 격차 기여율(%)", output_dir, "07_income_gap_category_contribution.png")


def hypothesis_figure(bootstrap_dir: Path, output_dir: Path) -> None:
    source = bootstrap_dir / "03_opportunity_practical_contrast.csv"
    if not source.exists():
        return
    frame = pd.read_csv(source)
    row = frame.loc[
        frame["검정사양"].eq("주 분석: 시간·비용·건강·지역")
        & frame["분석단위"].eq("네 장르 동일가중 평균")
    ].iloc[0]
    values = [row["문화의식형성기회_점추정_퍼센트포인트"], row["현실적여건_점추정_퍼센트포인트"]]
    fig, ax = plt.subplots(figsize=(5.6, 3.35))
    bars = ax.bar([0, 1], values, color=[OPPORTUNITY, PRACTICAL], width=0.56)
    ax.set_xticks([0, 1], ["문화 의식 형성 기회", "현실적 여건"])
    ax.set_ylabel("관람의향 격차 기여량(%p)", fontsize=11.5, weight="bold")
    ax.set_title("관람의향 격차 기여량 비교", loc="left", fontsize=14.0, weight="bold")
    ax.set_ylim(0, max(values) * 1.3)
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, value + max(values) * 0.04, f"{value:.2f}%p", ha="center", fontsize=11.5, weight="bold")
    finish_axis(ax)
    fig.tight_layout(rect=(0, 0.07, 1, 1), pad=0.35)
    save(fig, output_dir, "08_opportunity_vs_practical_gap_contribution.png", "출처: 문화체육관광부, 국민문화예술활동조사 2023~2025 원자료 재분석.")


def build_all(descriptive_dir: Path, model_dir: Path, bootstrap_dir: Path, output_dir: Path) -> None:
    setup_style()
    culture_nuri(descriptive_dir, output_dir)
    income_stage_figures(descriptive_dir, output_dir)
    conceptual_model(output_dir)
    model_figures(model_dir, output_dir)
    hypothesis_figure(bootstrap_dir, output_dir)
