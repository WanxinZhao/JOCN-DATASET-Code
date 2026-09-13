# E4：GNPy 唯一性、配置正确性与独立复现审计

## 覆盖与唯一性

- 公开表有 8,192 条执行记录、6,144 个唯一配置和 2,048 条重复记录。
- 期望配置 6,144 个，缺失 0、额外 0；其中 6,120 个状态为 ok、24 个为 no_signal。
- 因而配置空间覆盖完整，但必须去重后发布或明确区分执行历史与唯一数据集。

## 仿真警告

对 8,160 个存在结果文件的场景扫描 stderr：

| 警告 | 发生次数 | 受影响场景 |
|---|---:|---:|
| EDFA below minimum gain | 65,280 | 8,160 |
| ROADM target power unmet | 12,240 | 6,120 |
| missing type_variety / PMD / PDL / PMD coefficient | 各 8,160 | 各 8,160 |

前两项属于需要在论文中披露并解释的配置级警告，不能把 `status=ok` 等同为无警告或物理配置完全有效。

## 调制格式敏感性

在 3,060 对其他条件相同的有效配置中，QPSK 与 16QAM 的平均绝对 GSNR 差为 0.4779 dB，最大 0.8080 dB，仅 1.67% 完全相等。公开输入同时改变了频谱槽宽（QPSK 37.5 GHz、16QAM 50 GHz），所以该差异应表述为“调制格式与频谱间隔的联合配置效应”，不能归因于调制格式单因素。

## 独立复现

- 使用公开拓扑、设备配置和独立生成的 96 个分层 spectrum 输入，在 GNPy 2.14.2 下尝试复现。
- 96/96 在传播计算前失败：`target_extended_gain=30`、`max_loss=0` 超出 YANG 范围，且 teraflex 下 QPSK/16QAM mode 重复。
- 用 GNPy 2.13.0 再做单例兼容性检查，得到完全相同的失败。
- 公开仓库未锁定原始 GNPy commit，因此目前不能从所给输入独立重生论文数值。

这不是“仿真结果为 0”，而是公开复现包存在版本/模式不兼容。该阻断随后由 E5 继续处理：固定官方 GNPy v2.12 commit 后，96/96 个分层样本在 CLI 输出精度内重现归档数值；修正设备 schema 并改用 GNPy 2.14.2 后，96/96 可执行，但相对归档的 GSNR 平均/最大差异为 0.6281/1.4345 dB，且配置级警告仍存在。详见 `07_E5_恢复源码复现实验结果.md`。

详细表见 `E4_gnpy_audit/`，尤其是 `execution_and_coverage_summary.csv`、`published_warning_summary.csv`、`independent_reproduction_96_cases.csv` 和 `gnpy_version_compatibility.csv`。
