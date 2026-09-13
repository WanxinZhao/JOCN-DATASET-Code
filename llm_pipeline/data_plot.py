# import json
# import os
# import matplotlib.pyplot as plt

# files = [
#     ("C:/Users/po19996/Downloads/code/ECOC2026/oopt-gnpy/data/runs/20260419_154014/artifacts/iter-1-scenario-4/rx_frontend_constellation_data.json", "RX Frontend"),
#     ("C:/Users/po19996/Downloads/code/ECOC2026/oopt-gnpy/data/runs/20260419_154014/artifacts/iter-1-scenario-4/rx_after_edc_constellation_data.json", "After EDC"),
#     ("C:/Users/po19996/Downloads/code/ECOC2026/oopt-gnpy/data/runs/20260419_154014/artifacts/iter-1-scenario-4/rx_after_equalizer_constellation_data.json", "After Equalizer"),
#     ("C:/Users/po19996/Downloads/code/ECOC2026/oopt-gnpy/data/runs/20260419_154014/artifacts/iter-1-scenario-4/rx_after_cpr_constellation_data.json", "After CPR"),
# ]

# def load_constellation(json_file, pol="pol_x"):
#     with open(json_file, "r", encoding="utf-8") as f:
#         data = json.load(f)

#     i_data = data["polarizations"][pol]["i"]
#     q_data = data["polarizations"][pol]["q"]
#     n = min(len(i_data), len(q_data))
#     return i_data[:n], q_data[:n]

# output_dir = r"C:/Users/po19996/Downloads/code/ECOC2026/oopt-gnpy/data/runs/20260419_154014/artifacts/iter-1-scenario-4/constellation_figures"
# os.makedirs(output_dir, exist_ok=True)

# for polarization in ["pol_x", "pol_y"]:
#     for file_name, title in files:
#         i_data, q_data = load_constellation(file_name, pol=polarization)

#         plt.figure(figsize=(6, 6))
#         plt.scatter(i_data, q_data, s=8, alpha=0.5)
#         plt.title(f"{title} ({polarization})")
#         plt.xlabel("In-Phase (I)")
#         plt.ylabel("Quadrature (Q)")
#         plt.axhline(0, linewidth=0.8)
#         plt.axvline(0, linewidth=0.8)
#         plt.grid(True, alpha=0.3)
#         plt.gca().set_aspect("equal", adjustable="box")
#         plt.tight_layout()

#         safe_title = title.lower().replace(" ", "_")
#         save_path = os.path.join(output_dir, f"{safe_title}_{polarization}.png")

#         plt.savefig(save_path, dpi=300, bbox_inches="tight")
#         plt.close()

#         print(f"Saved: {save_path}")


#############################################################################################################

# import json
# import os
# import matplotlib.pyplot as plt

# files = [
#     ("C:/Users/po19996/Downloads/code/ECOC2026/oopt-gnpy/data/runs/20260419_154014/artifacts/iter-1-scenario-4/rx_frontend_constellation_data.json", "RX Frontend"),
#     ("C:/Users/po19996/Downloads/code/ECOC2026/oopt-gnpy/data/runs/20260419_154014/artifacts/iter-1-scenario-4/rx_after_edc_constellation_data.json", "After EDC"),
#     ("C:/Users/po19996/Downloads/code/ECOC2026/oopt-gnpy/data/runs/20260419_154014/artifacts/iter-1-scenario-4/rx_after_equalizer_constellation_data.json", "After Equalizer"),
#     ("C:/Users/po19996/Downloads/code/ECOC2026/oopt-gnpy/data/runs/20260419_154014/artifacts/iter-1-scenario-4/rx_after_cpr_constellation_data.json", "After CPR"),
# ]

# def load_constellation(json_file, pol="pol_x"):
#     with open(json_file, "r", encoding="utf-8") as f:
#         data = json.load(f)

#     i_data = data["polarizations"][pol]["i"]
#     q_data = data["polarizations"][pol]["q"]
#     n = min(len(i_data), len(q_data))
#     return i_data[:n], q_data[:n]

# polarization = "pol_x"   # 改成 "pol_y" 可画另一组

# output_dir = r"C:/Users/po19996/Downloads/code/ECOC2026/oopt-gnpy/data/runs/20260419_154014/artifacts/iter-1-scenario-4/constellation_figures"
# os.makedirs(output_dir, exist_ok=True)

# fig, axes = plt.subplots(2, 2, figsize=(10, 10))
# axes = axes.flatten()

# # 可选：统一坐标范围
# all_i, all_q = [], []
# for file_name, _ in files:
#     i_data, q_data = load_constellation(file_name, pol=polarization)
#     all_i.extend(i_data)
#     all_q.extend(q_data)

# x_min, x_max = min(all_i), max(all_i)
# y_min, y_max = min(all_q), max(all_q)

# pad_x = 0.05 * (x_max - x_min)
# pad_y = 0.05 * (y_max - y_min)

# for ax, (file_name, title) in zip(axes, files):
#     i_data, q_data = load_constellation(file_name, pol=polarization)

#     ax.scatter(i_data, q_data, s=8, alpha=0.5)
#     ax.set_title(f"{title} ({polarization})")
#     ax.set_xlabel("In-Phase (I)")
#     ax.set_ylabel("Quadrature (Q)")
#     ax.axhline(0, linewidth=0.8)
#     ax.axvline(0, linewidth=0.8)
#     ax.grid(True, alpha=0.3)
#     ax.set_aspect("equal", adjustable="box")
#     ax.set_xlim(x_min - pad_x, x_max + pad_x)
#     ax.set_ylim(y_min - pad_y, y_max + pad_y)

# plt.tight_layout()

# save_path = os.path.join(output_dir, f"constellation_2x2_{polarization}.png")
# plt.savefig(save_path, dpi=300, bbox_inches="tight")
# plt.close()

# print(f"Saved: {save_path}")

#####################################################################################################

import json
import os
import matplotlib.pyplot as plt

files = [
    ("C:/Users/po19996/Downloads/code/ECOC2026/oopt-gnpy/data/runs/20260419_154014/artifacts/iter-1-scenario-4/rx_frontend_constellation_data.json", "RX Frontend"),
    ("C:/Users/po19996/Downloads/code/ECOC2026/oopt-gnpy/data/runs/20260419_154014/artifacts/iter-1-scenario-4/rx_after_edc_constellation_data.json", "After EDC"),
    ("C:/Users/po19996/Downloads/code/ECOC2026/oopt-gnpy/data/runs/20260419_154014/artifacts/iter-1-scenario-4/rx_after_equalizer_constellation_data.json", "After Equalizer"),
    ("C:/Users/po19996/Downloads/code/ECOC2026/oopt-gnpy/data/runs/20260419_154014/artifacts/iter-1-scenario-4/rx_after_cpr_constellation_data.json", "After CPR"),
]

def load_constellation(json_file, pol="pol_x"):
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    i_data = data["polarizations"][pol]["i"]
    q_data = data["polarizations"][pol]["q"]
    n = min(len(i_data), len(q_data))
    return i_data[:n], q_data[:n]

output_dir = r"C:/Users/po19996/Downloads/code/ECOC2026/oopt-gnpy/data/runs/20260419_154014/artifacts/iter-1-scenario-4/constellation_figures"
os.makedirs(output_dir, exist_ok=True)

for polarization in ["pol_x", "pol_y"]:
    fig, axes = plt.subplots(2, 2, figsize=(10, 10))
    axes = axes.flatten()

    all_i, all_q = [], []
    for file_name, _ in files:
        i_data, q_data = load_constellation(file_name, pol=polarization)
        all_i.extend(i_data)
        all_q.extend(q_data)

    x_min, x_max = min(all_i), max(all_i)
    y_min, y_max = min(all_q), max(all_q)

    pad_x = 0.05 * (x_max - x_min)
    pad_y = 0.05 * (y_max - y_min)

    for ax, (file_name, title) in zip(axes, files):
        i_data, q_data = load_constellation(file_name, pol=polarization)

        ax.scatter(i_data, q_data, s=8, alpha=0.5)
        ax.set_title(f"{title} ({polarization})")
        ax.set_xlabel("In-Phase (I)")
        ax.set_ylabel("Quadrature (Q)")
        ax.axhline(0, linewidth=0.8)
        ax.axvline(0, linewidth=0.8)
        ax.grid(True, alpha=0.3)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlim(x_min - pad_x, x_max + pad_x)
        ax.set_ylim(y_min - pad_y, y_max + pad_y)

    plt.tight_layout()

    save_path = os.path.join(output_dir, f"constellation_2x2_{polarization}.png")
    plt.savefig(save_path, dpi=300, bbox_inches="tight")
    plt.close()

    print(f"Saved: {save_path}")