import pandas as pd
import matplotlib.pyplot as plt

# 读入数据
csv_path = r"C:/Users/po19996/Downloads/code/ECOC2026/oopt-gnpy/data/runs/case1/scenarios.csv"
df = pd.read_csv(csv_path)

# 只保留有效结果
df = df[df["status"] == "ok"].copy()

# 为了论文展示更整齐，把 channel_pattern 转成三位字符串
df["channel_pattern_str"] = df["channel_pattern"].astype(int).astype(str).str.zfill(3)

# 排序顺序
modulations = ["QPSK", "16QAM"]
launch_powers = [-5.5, -5.0, -4.5]
channel_order = ["000", "001", "010", "011", "100", "101", "110", "111"]

# 只保留数据里实际出现的 destination，并排序
# destination_order = sorted(df["destination"].dropna().unique().tolist())
destination_order = ["bradley stoke", "froxfield", "reading", "powergate"]
destination_labels = ["Bradley Stoke", "Froxfield", "Reading", "Power Gate"]

# 统一色条范围
vmin = df["gsnr_db"].min()
vmax = df["gsnr_db"].max()
heatmap_cmap = "turbo"

fig, axes = plt.subplots(
    nrows=len(modulations),
    ncols=len(launch_powers),
    figsize=(14, 7),
    constrained_layout=True
)

# 当 axes 不是二维时做兼容
if len(modulations) == 1 and len(launch_powers) == 1:
    axes = [[axes]]
elif len(modulations) == 1:
    axes = [axes]
elif len(launch_powers) == 1:
    axes = [[ax] for ax in axes]

im = None

for i, mod in enumerate(modulations):
    for j, lp in enumerate(launch_powers):
        ax = axes[i][j]

        sub = df[
            (df["modulation"] == mod) &
            (df["launch_power_dbm"] == lp)
        ].copy()

        pivot = sub.pivot_table(
            index="destination",
            columns="channel_pattern_str",
            values="gsnr_db",
            aggfunc="mean"
        )

        # 保证行列顺序一致
        pivot = pivot.reindex(index=destination_order, columns=channel_order)

        im = ax.imshow(
            pivot.values,
            aspect="auto",
            interpolation="nearest",
            cmap=heatmap_cmap,
            vmin=vmin,
            vmax=vmax
        )

        # 子图标题
        panel_label = chr(ord('a') + i * len(launch_powers) + j)
        ax.set_title(f"({panel_label}) {mod}, {lp:.1f} dBm")

        # 坐标轴标签
        ax.set_xticks(range(len(channel_order)))
        ax.set_xticklabels(channel_order, rotation=0)
        ax.set_yticks(range(len(destination_order)))
        ax.set_yticklabels(destination_labels)

        if i == len(modulations) - 1:
            ax.set_xlabel("Channel pattern")
        if j == 0:
            ax.set_ylabel("Destination")

        # 在格子中标数值
        for y in range(len(destination_order)):
            for x in range(len(channel_order)):
                val = pivot.values[y, x]
                if pd.notna(val):
                    ax.text(
                        x, y, f"{val:.1f}",
                        ha="center", va="center",
                        fontsize=8, color="black"
                    )

# 公共 colorbar
cbar = fig.colorbar(im, ax=axes, shrink=0.95)
cbar.set_label("GSNR (dB)")

# 总标题
fig.suptitle("GSNR across routes, launch powers, and channel occupancy patterns", fontsize=14)

# 保存
output_path = r"C:/Users/po19996/Downloads/code/ECOC2026/oopt-gnpy/data/runs/case1/gsnr_heatmap_2x3.png"
plt.savefig(output_path, dpi=300, bbox_inches="tight")
plt.show()

print(f"Saved: {output_path}")
