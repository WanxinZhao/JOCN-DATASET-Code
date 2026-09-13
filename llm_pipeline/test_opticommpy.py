import json
import inspect
import numpy as np
import matplotlib.pyplot as plt

from optic.utils import parameters
from optic.models.tx import simpleWDMTx
from optic.models.devices import pdmCoherentReceiver, basicLaserModel
from optic.models.channels import manakovSSF
from optic.dsp.equalization import edc, mimoAdaptEqualizer
from optic.dsp.carrierRecovery import cpr, fourthPowerFOE
from optic.comm.metrics import calcEVM, fastBERcalc, monteCarloGMI
from optic.plot import plotPSD
from optic.dsp.core import pulseShape, firFilter


def safe_text(text):
    return str(text).encode("ascii", "backslashreplace").decode("ascii")


print("cpr =", safe_text(inspect.signature(cpr)))
print("calcEVM =", safe_text(inspect.signature(calcEVM)))
print("fastBERcalc =", safe_text(inspect.signature(fastBERcalc)))
print("monteCarloGMI =", safe_text(inspect.signature(monteCarloGMI)))
print("pdmCoherentReceiver =", safe_text(inspect.signature(pdmCoherentReceiver)))

TOPO_FILE = "OFC_Testbed.json"
EQPT_FILE = "eqpt_config_NDFF.json"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_bristol_powergate_path(topo, eqpt):
    elements = {e["uid"]: e for e in topo["elements"]}
    fiber_db = {f["type_variety"]: f for f in eqpt["Fiber"]}
    edfa_db = {e["type_variety"]: e for e in eqpt["Edfa"]}

    fiber_uids = [
        "fiber_uob_brd",
        "fiber_brd_ffd",
        "fiber_ffd_rdg",
        "fiber_rdg_pgt",
    ]
    edfa_uids = [
        "edfa_uob",
        "edfa_brd",
        "edfa_ffd",
        "edfa_rdg",
    ]

    spans = []
    for fiber_uid, edfa_uid in zip(fiber_uids, edfa_uids):
        fiber = elements[fiber_uid]
        amp = elements[edfa_uid]

        fcfg = fiber_db[fiber["type_variety"]]
        acfg = edfa_db[amp["type_variety"]]

        nf_db = 0.5 * (acfg["nf_min"] + acfg["nf_max"]) if "nf_min" in acfg else acfg.get("nf0", 8.0)

        spans.append(
            {
                "fiber_uid": fiber_uid,
                "length_km": float(fiber["params"]["length"]),
                "alpha_db_per_km": float(fiber["params"]["loss_coef"]),
                "dispersion_ps_nm_km": 16.7,
                "gamma_W_km": 1.27,
                "pmd_coef": float(fcfg["pmd_coef"]),
                "edfa_uid": edfa_uid,
                "edfa_gain_db": float(amp["operational"]["gain_target"]),
                "edfa_nf_db": float(nf_db),
            }
        )
    return spans


def get_voyager_16qam_mode(eqpt):
    voyager = next(t for t in eqpt["Transceiver"] if t["type_variety"] == "voyager")
    mode = next(
        m for m in voyager["mode"]
        if m["format"] == "16QAM" and m["baud_rate"] == 32_000_000_000.0
    )
    return mode


def ensure_scalar(x):
    x = np.real_if_close(x)
    if np.isscalar(x):
        return float(x)
    return float(np.asarray(x).reshape(-1)[0])


def print_matrix_info(name, x):
    arr = np.asarray(x)
    print(f"{name} shape: {arr.shape}, dtype: {arr.dtype}")
    try:
        flat = arr.reshape(-1)
        print(
            f"{name}: finite={np.isfinite(np.real(flat)).all() and np.isfinite(np.imag(flat)).all()}, "
            f"min|x|={np.nanmin(np.abs(flat)):.4e}, max|x|={np.nanmax(np.abs(flat)):.4e}"
        )
    except Exception as e:
        print(f"{name}: summary failed -> {e}")


def to_2pol_matrix(x):
    arr = np.asarray(x)

    if arr.ndim == 1:
        return arr.reshape(-1, 1)

    if arr.ndim == 2:
        if arr.shape[1] in (1, 2):
            return arr
        if arr.shape[0] in (1, 2):
            return arr.T
        return arr

    arr = np.squeeze(arr)

    if arr.ndim == 1:
        return arr.reshape(-1, 1)
    if arr.ndim == 2:
        if arr.shape[1] in (1, 2):
            return arr
        if arr.shape[0] in (1, 2):
            return arr.T

    return arr.reshape(arr.shape[0], -1)


def normalize_per_pol(x):
    x = to_2pol_matrix(x).astype(np.complex128)
    y = x.copy()
    for k in range(y.shape[1]):
        p = np.mean(np.abs(y[:, k]) ** 2)
        if p > 0:
            y[:, k] /= np.sqrt(p)
    return y


def normalize_for_plot(x):
    return normalize_per_pol(x)


def phase_align_to_reference(rx, tx):
    rx = to_2pol_matrix(rx).astype(np.complex128)
    tx = to_2pol_matrix(tx).astype(np.complex128)

    n = min(rx.shape[0], tx.shape[0])
    rx = rx[:n, :].copy()
    tx = tx[:n, :]

    n_pol = min(rx.shape[1], tx.shape[1])
    for k in range(n_pol):
        denom = np.vdot(rx[:, k], rx[:, k])
        if np.abs(denom) < 1e-20:
            continue
        rot = np.vdot(tx[:, k], rx[:, k]) / denom
        if np.abs(rot) > 0:
            rx[:, k] *= rot / np.abs(rot)
    return rx


def save_constellation_manual(x, filename, title, max_points=20000):
    x = to_2pol_matrix(x)

    max_cols = min(2, x.shape[1])
    fig, axes = plt.subplots(1, max_cols, figsize=(6 * max_cols, 6), squeeze=False)
    labels = ["Pol-X", "Pol-Y"]

    for i in range(max_cols):
        ax = axes[0, i]
        xi = np.asarray(x[:, i]).reshape(-1)
        xi = xi[np.isfinite(np.real(xi)) & np.isfinite(np.imag(xi))]
        if len(xi) == 0:
            ax.text(0.5, 0.5, "No valid samples to plot", ha="center", va="center")
        else:
            if len(xi) > max_points:
                idx = np.linspace(0, len(xi) - 1, max_points).astype(int)
                xi = xi[idx]
            ax.scatter(np.real(xi), np.imag(xi), s=4, alpha=0.35)
        ax.set_title(labels[i])
        ax.set_xlabel("In-Phase")
        ax.set_ylabel("Quadrature")
        ax.grid(True)
        ax.axis("equal")

    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(filename, dpi=150, bbox_inches="tight")
    plt.close(fig)


def save_processing_triptych(stage_map, filename, title, max_points=12000):
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), squeeze=False)
    stage_names = list(stage_map.keys())

    for i, name in enumerate(stage_names[:3]):
        ax = axes[0, i]
        x = to_2pol_matrix(stage_map[name])
        xi = np.asarray(x[:, 0]).reshape(-1)
        xi = xi[np.isfinite(np.real(xi)) & np.isfinite(np.imag(xi))]

        if len(xi) > max_points:
            idx = np.linspace(0, len(xi) - 1, max_points).astype(int)
            xi = xi[idx]

        ax.scatter(np.real(xi), np.imag(xi), s=4, alpha=0.35)
        ax.set_title(name)
        ax.set_xlabel("In-Phase")
        ax.set_ylabel("Quadrature")
        ax.grid(True)
        ax.axis("equal")

    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(filename, dpi=150, bbox_inches="tight")
    plt.close(fig)


def call_pdm_receiver(sig, sigLO, theta_sig, paramPD):
    return pdmCoherentReceiver(sig, sigLO, theta_sig, paramPD)


def apply_frequency_shift(sig, Fs, freq_hz):
    if abs(freq_hz) < 1e-12:
        return sig
    t = np.arange(len(sig)) / Fs
    return sig * np.exp(1j * 2 * np.pi * freq_hz * t)


def power_dbm_to_mw(p_dbm):
    return 10 ** (p_dbm / 10.0)


def power_mw_to_dbm(p_mw):
    return 10 * np.log10(max(p_mw, 1e-30))


def build_expected_wdm_grid(n_channels, spacing_hz):
    if n_channels <= 0:
        raise ValueError("n_channels must be positive")

    if n_channels % 2 == 1:
        k = np.arange(-(n_channels // 2), n_channels // 2 + 1, dtype=float)
    else:
        k = np.arange(-n_channels / 2 + 0.5, n_channels / 2, 1.0, dtype=float)

    return k * spacing_hz


def choose_channel_offset_for_case(n_channels, spacing_hz, even_side="lower"):
    if n_channels % 2 == 1:
        return 0.0
    if even_side.lower() in ("lower", "left", "negative", "-"):
        return -spacing_hz / 2.0
    if even_side.lower() in ("upper", "right", "positive", "+"):
        return +spacing_hz / 2.0
    raise ValueError("even_side must be 'lower' or 'upper'")


def choose_channel_by_target_offset(freq_grid_hz, target_offset_hz):
    freq_grid_hz = np.asarray(freq_grid_hz).reshape(-1)
    return int(np.argmin(np.abs(freq_grid_hz - target_offset_hz)))


def summarize_wdm_selection(tx, raw_freq_grid_hz, rx_freq_grid_hz, ch_index, target_offset_hz):
    pch_mw = power_dbm_to_mw(tx.powerPerChannel)
    ptot_mw = tx.nChannels * pch_mw

    print("\n=== WDM channel selection summary ===")
    print(f"nChannels                  : {tx.nChannels}")
    print(f"powerPerChannel            : {tx.powerPerChannel:.4f} dBm")
    print(f"total launch power         : {power_mw_to_dbm(ptot_mw):.4f} dBm")
    print(f"raw freqGrid (GHz)         : {np.round(np.asarray(raw_freq_grid_hz).reshape(-1) / 1e9, 6)}")
    print(f"rx freqGrid used (GHz)     : {np.round(np.asarray(rx_freq_grid_hz).reshape(-1) / 1e9, 6)}")
    print(f"target channel offset (GHz): {target_offset_hz / 1e9:.6f}")
    print(f"selected RF tune index     : {ch_index}")
    print(f"selected RF tune offset    : {rx_freq_grid_hz[ch_index] / 1e9:.6f} GHz")


def build_rrc_matched_filter(tx):
    p = parameters()
    p.pulseType = "rrc"
    p.SpS = tx.SpS
    p.N = tx.nFilterTaps
    p.alpha = tx.pulseRollOff
    p.Ts = 1 / tx.Rs
    return pulseShape(p)


def align_lengths(a, b):
    a = np.asarray(a)
    b = np.asarray(b)
    n = min(len(a), len(b))
    return a[:n], b[:n]


def sample_with_offset(x, sps, offset):
    x = to_2pol_matrix(x)
    if x.shape[0] <= offset:
        return x
    return x[offset::sps, :]


def score_offset(rx_s, tx_s):
    rx = to_2pol_matrix(rx_s)
    tx = to_2pol_matrix(tx_s)
    if rx.shape[0] < 100:
        return -np.inf

    rx_power = np.sum(np.abs(rx) ** 2, axis=1)
    tx_power = np.sum(np.abs(tx) ** 2, axis=1)

    n = min(len(rx_power), len(tx_power))
    rx_power = rx_power[:n]
    tx_power = tx_power[:n]

    rx_power = rx_power - np.mean(rx_power)
    tx_power = tx_power - np.mean(tx_power)
    denom = (np.linalg.norm(rx_power) * np.linalg.norm(tx_power)) + 1e-12
    return float(np.abs(np.vdot(rx_power, tx_power)) / denom)


def find_best_channel_and_offset(sig_mf, symbTx_all, sps):
    symbTx_all = np.asarray(symbTx_all)
    if symbTx_all.ndim != 3:
        raise RuntimeError(f"symbTx_all should be 3D, got {symbTx_all.shape}")

    best = {
        "score": -np.inf,
        "offset": 0,
        "tx_channel_index": 0,
        "rx_symb": None,
        "tx_symb": None,
    }

    n_candidates = symbTx_all.shape[2]
    for ch in range(n_candidates):
        tx_ref = symbTx_all[:, :, ch]
        for off in range(sps):
            cand = sample_with_offset(sig_mf, sps, off)
            sc = score_offset(cand, tx_ref)
            print(f"search ref_ch={ch}, offset={off}, score={sc}")
            if sc > best["score"]:
                best["score"] = sc
                best["offset"] = off
                best["tx_channel_index"] = ch
                best["rx_symb"] = cand
                best["tx_symb"] = tx_ref

    return best


def align_symbol_delay(rx_symb, tx_symb, max_lag=256):
    """
    整数符号级延迟搜索
    best_lag > 0: rx 相对 tx 晚了 best_lag 个符号
    """
    rx = to_2pol_matrix(rx_symb)
    tx = to_2pol_matrix(tx_symb)

    best_score = -np.inf
    best_lag = 0
    best_rx = None
    best_tx = None

    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            rx_seg = rx[lag:, :]
            tx_seg = tx[:len(rx_seg), :]
        else:
            tx_seg = tx[-lag:, :]
            rx_seg = rx[:len(tx_seg), :]

        n = min(len(rx_seg), len(tx_seg))
        if n < 1000:
            continue

        rx_use = rx_seg[:n, :]
        tx_use = tx_seg[:n, :]

        rx_pow = np.sum(np.abs(rx_use) ** 2, axis=1)
        tx_pow = np.sum(np.abs(tx_use) ** 2, axis=1)

        rx_pow = rx_pow - np.mean(rx_pow)
        tx_pow = tx_pow - np.mean(tx_pow)

        denom = np.linalg.norm(rx_pow) * np.linalg.norm(tx_pow) + 1e-12
        sc = float(np.abs(np.vdot(rx_pow, tx_pow)) / denom)

        if sc > best_score:
            best_score = sc
            best_lag = lag
            best_rx = rx_use
            best_tx = tx_use

    return best_rx, best_tx, best_lag, best_score


def compute_stage_metrics(rx, tx, M=16, const_type="qam"):
    rx = to_2pol_matrix(rx)
    tx = to_2pol_matrix(tx)
    rx_pol, tx_pol = align_lengths(rx[:, 0], tx[:, 0])

    ber, ser, snr = fastBERcalc(rx_pol, tx_pol, M, const_type)
    evm = calcEVM(rx_pol, M, const_type, symbTx=tx_pol)
    gmi, ngmi = monteCarloGMI(rx_pol, tx_pol, M, const_type)

    return {
        "BER": ensure_scalar(ber),
        "SER": ensure_scalar(ser),
        "SNR_dB": ensure_scalar(snr),
        "EVM": ensure_scalar(evm),
        "GMI": ensure_scalar(gmi),
        "NGMI": ensure_scalar(ngmi),
    }


def print_stage_metrics(name, metrics):
    print(f"\n=== {name} ===")
    for k, v in metrics.items():
        print(f"{k}: {v}")


def run_mimo_chain(rx_symb, tx_symb, M):
    rx_in = normalize_per_pol(rx_symb)
    tx_ref = normalize_per_pol(tx_symb)

    eq = parameters()
    eq.nTaps = 17
    eq.SpS = 1
    eq.numIter = 8
    eq.storeCoeff = False
    eq.M = M
    eq.constType = "qam"
    eq.prgsBar = False
    eq.returnResults = True
    eq.alg = ["nlms", "nlms"]
    eq.mu = [1e-3, 2e-4]
    eq.L = [4000, max(1, len(tx_ref) - 4000)]

    out_eq, H, err_sq, _ = mimoAdaptEqualizer(rx_in, eq, tx_ref)
    out_eq = normalize_per_pol(out_eq)
    return out_eq, tx_ref, H, err_sq, eq


def main():
    topo = load_json(TOPO_FILE)
    eqpt = load_json(EQPT_FILE)

    spans = build_bristol_powergate_path(topo, eqpt)
    mode = get_voyager_16qam_mode(eqpt)
    si = eqpt["SI"][0]
    total_distance = sum(s["length_km"] for s in spans)

    print("=== Path: Bristol -> Powergate ===")
    for i, s in enumerate(spans, start=1):
        print(
            f"Span {i}: {s['fiber_uid']}, "
            f"L={s['length_km']} km, "
            f"alpha={s['alpha_db_per_km']} dB/km, "
            f"D={s['dispersion_ps_nm_km']} ps/nm/km, "
            f"gamma={s['gamma_W_km']} 1/W/km, "
            f"EDFA gain={s['edfa_gain_db']} dB, NF={s['edfa_nf_db']} dB"
        )
    print(f"Total distance = {total_distance:.1f} km\n")

    tx = parameters()
    tx.M = 16
    tx.constType = "qam"
    tx.Rs = float(mode["baud_rate"])
    tx.SpS = 8
    tx.nBits = 2**16
    tx.pulseType = "rrc"
    tx.nFilterTaps = 256
    tx.pulseRollOff = float(mode["roll_off"])

    # ===== 用户可改 =====
    tx.nChannels = 4
    even_side = "lower"   # "lower" -> -25 GHz, "upper" -> +25 GHz
    # ===================

    totalLaunchPower_dBm = -0.23
    tx.powerPerChannel = totalLaunchPower_dBm - 10 * np.log10(tx.nChannels)

    tx.Fc = float(si["f_min"])
    tx.laserLinewidth = 100e3
    tx.wdmGridSpacing = float(mode["min_spacing"])
    tx.nPolModes = 2
    tx.seed = 123
    tx.prgsBar = False

    print("=== TX setup ===")
    print(f"Modulation      : 16QAM")
    print(f"Channels        : {tx.nChannels}")
    print(f"Launch/ch       : {tx.powerPerChannel:.4f} dBm")
    print(f"Rs              : {tx.Rs/1e9:.1f} GBd")
    print(f"Spacing         : {tx.wdmGridSpacing/1e9:.1f} GHz")
    print(f"Total launch    : {power_mw_to_dbm(tx.nChannels * power_dbm_to_mw(tx.powerPerChannel)):.4f} dBm")
    print()

    sigTxWDM, symbTx_all, paramTx = simpleWDMTx(tx)
    Fs = tx.SpS * tx.Rs

    print_matrix_info("sigTxWDM", sigTxWDM)
    print_matrix_info("symbTx_all", symbTx_all)

    plt.figure()
    plotPSD(sigTxWDM, Fs=Fs, Fc=tx.Fc)
    plt.savefig("tx_psd.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved tx_psd.png")

    raw_freqGrid = np.asarray(getattr(paramTx, "wdmFreqGrid", [])).reshape(-1)
    print("raw paramTx.wdmFreqGrid (GHz) =", raw_freqGrid / 1e9 if raw_freqGrid.size else raw_freqGrid)
    print("symbTx_all.shape =", np.asarray(symbTx_all).shape)
    print("len(raw_freqGrid) =", len(raw_freqGrid))

    if np.asarray(symbTx_all).ndim != 3:
        raise RuntimeError(f"symbTx_all should be 3D [Ns, Npol, Nch], got shape {np.asarray(symbTx_all).shape}")

    n_symb_channels = int(np.asarray(symbTx_all).shape[2])
    if n_symb_channels != tx.nChannels:
        raise RuntimeError(
            f"symbTx_all channel count mismatch: got {n_symb_channels}, expected {tx.nChannels}"
        )

    rx_freqGrid = build_expected_wdm_grid(tx.nChannels, tx.wdmGridSpacing)
    targetChannelOffsetHz = choose_channel_offset_for_case(
        tx.nChannels, tx.wdmGridSpacing, even_side=even_side
    )
    rfTuneIndex = choose_channel_by_target_offset(rx_freqGrid, targetChannelOffsetHz)

    summarize_wdm_selection(tx, raw_freqGrid, rx_freqGrid, rfTuneIndex, targetChannelOffsetHz)

    tx_ref_preview = symbTx_all[:, :, min(rfTuneIndex, symbTx_all.shape[2] - 1)]
    save_constellation_manual(
        tx_ref_preview,
        "tx_center_constellation_reference.png",
        "TX Candidate Symbol Reference"
    )
    print("Saved tx_center_constellation_reference.png")

    sig = sigTxWDM
    for i, span in enumerate(spans, start=1):
        ch = parameters()
        ch.Ltotal = span["length_km"]
        ch.Lspan = span["length_km"]
        ch.hz = 0.1
        ch.alpha = span["alpha_db_per_km"]
        ch.D = span["dispersion_ps_nm_km"]
        ch.gamma = span["gamma_W_km"]
        ch.Fc = tx.Fc
        ch.Fs = Fs
        ch.NF = span["edfa_nf_db"]
        ch.G = span["edfa_gain_db"]
        ch.amp = "edfa"
        ch.prgsBar = False

        print(
            f"Running physical span {i}/{len(spans)}: "
            f"{span['fiber_uid']} ({span['length_km']:.1f} km)"
        )
        sig = manakovSSF(sig, ch)

    print_matrix_info("sig_after_link", sig)

    plt.figure()
    plotPSD(sig, Fs=Fs, Fc=tx.Fc)
    plt.savefig("rx_psd.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved rx_psd.png")

    FO = 64e6

    paramLO = parameters()
    paramLO.P = 10
    paramLO.lw = 100e3
    paramLO.RIN_var = 0
    paramLO.Ns = len(sig)
    paramLO.Fs = Fs
    paramLO.seed = 789
    paramLO.freqShift = 0.0

    sigLO = basicLaserModel(paramLO)

    lo_shift_hz = -(rx_freqGrid[rfTuneIndex] + FO)
    sigLO = apply_frequency_shift(sigLO, Fs, lo_shift_hz)

    print(f"LO manual freqShift used = {lo_shift_hz/1e9:.6f} GHz")
    print_matrix_info("sigLO", sigLO)

    paramPD = parameters()
    paramPD.B = tx.Rs
    paramPD.Fs = Fs
    paramPD.ideal = True
    paramPD.seed = 1011

    theta_sig = np.pi / 3
    sigRx = call_pdm_receiver(sig, sigLO, theta_sig, paramPD)
    print_matrix_info("sigRx_frontend", sigRx)

    rx_frontend_plot = normalize_for_plot(sample_with_offset(sigRx, tx.SpS, tx.SpS // 2))
    save_constellation_manual(
        rx_frontend_plot,
        "rx_frontend_constellation.png",
        "RX Frontend IQ Cloud (before EDC)"
    )
    print("Saved rx_frontend_constellation.png")

    edcParam = parameters()
    edcParam.L = total_distance
    edcParam.D = 16.7
    edcParam.Fc = tx.Fc - rx_freqGrid[rfTuneIndex]
    edcParam.Fs = Fs
    edcParam.Rs = tx.Rs

    print(f"EDC center Fc used = {edcParam.Fc/1e12:.6f} THz")

    sigEdc = edc(sigRx, edcParam)
    print_matrix_info("sigEdc", sigEdc)

    try:
        h = build_rrc_matched_filter(tx)
        sigMf = firFilter(h, sigEdc)
        print("Matched filter applied.")
    except Exception as e:
        print(f"Matched filter failed, using sigEdc directly: {e}")
        sigMf = sigEdc

    print_matrix_info("sigMf", sigMf)

    search = find_best_channel_and_offset(sigMf, symbTx_all, tx.SpS)
    best_offset = int(search["offset"])
    best_score = float(search["score"])
    tx_channel_index = int(search["tx_channel_index"])
    rx_mf_symb = search["rx_symb"]
    symbTx = search["tx_symb"]

    print("\n=== Auto channel/reference search result ===")
    print(f"matched_tx_channel_index = {tx_channel_index}")
    print(f"best_offset              = {best_offset}")
    print(f"best_score               = {best_score}")

    rx_mf_symb, symbTx, best_lag, lag_score = align_symbol_delay(
        rx_mf_symb, symbTx, max_lag=256
    )

    print("\n=== Symbol delay alignment ===")
    print(f"best_symbol_lag = {best_lag}")
    print(f"lag_score       = {lag_score}")

    print_matrix_info("symbTx_matched_aligned", symbTx)
    print_matrix_info("rx_mf_symb_best_aligned", rx_mf_symb)

    rx_edc_plot = phase_align_to_reference(
        normalize_for_plot(rx_mf_symb),
        normalize_for_plot(symbTx),
    )
    save_constellation_manual(
        rx_edc_plot,
        "rx_after_edc_constellation.png",
        f"RX After EDC + MF (matched ref ch={tx_channel_index}, offset={best_offset}, lag={best_lag})"
    )
    print("Saved rx_after_edc_constellation.png")

    tx_ref = normalize_per_pol(symbTx)
    stage_metrics_pre_eq = compute_stage_metrics(normalize_per_pol(rx_mf_symb), tx_ref, tx.M, "qam")
    print_stage_metrics("Metrics Before Equalizer", stage_metrics_pre_eq)

    rx_foe_out, fo_est = fourthPowerFOE(normalize_per_pol(rx_mf_symb), tx.Rs)
    rx_foe_out = normalize_per_pol(rx_foe_out)
    print(f"Estimated FO after FOE = {fo_est} Hz")

    stage_metrics_post_foe = compute_stage_metrics(rx_foe_out, tx_ref, tx.M, "qam")
    print_stage_metrics("Metrics After FOE", stage_metrics_post_foe)

    rx_eq_out, tx_ref, _, _, eq_param = run_mimo_chain(rx_foe_out, symbTx, tx.M)
    print_matrix_info("rx_eq_out", rx_eq_out)

    rx_eq_plot = phase_align_to_reference(
        normalize_for_plot(rx_eq_out),
        tx_ref,
    )
    save_constellation_manual(
        rx_eq_plot,
        "rx_after_equalizer_constellation.png",
        "RX After MIMO Equalizer"
    )
    print("Saved rx_after_equalizer_constellation.png")

    stage_metrics_post_eq = compute_stage_metrics(normalize_per_pol(rx_eq_out), tx_ref, tx.M, "qam")
    print_stage_metrics("Metrics After Equalizer", stage_metrics_post_eq)

    cpr_out = None
    stage_metrics_post_cpr = None
    try:
        ph = parameters()
        ph.alg = "bps"
        ph.N = 64
        ph.B = 64
        ph.M = 16
        ph.constType = "qam"

        cpr_result = cpr(rx_eq_out, param=ph, symbTx=tx_ref)
        cpr_out = cpr_result[0] if isinstance(cpr_result, tuple) else cpr_result
        cpr_out = normalize_per_pol(cpr_out)

        print_matrix_info("cpr_out", cpr_out)

        save_constellation_manual(
            phase_align_to_reference(normalize_for_plot(cpr_out), tx_ref),
            "rx_after_cpr_constellation.png",
            "RX After CPR"
        )
        print("Saved rx_after_cpr_constellation.png")

        stage_metrics_post_cpr = compute_stage_metrics(cpr_out, tx_ref, tx.M, "qam")
        print_stage_metrics("Metrics After CPR", stage_metrics_post_cpr)

        save_processing_triptych(
            {
                "After FOE": phase_align_to_reference(normalize_for_plot(rx_foe_out), tx_ref),
                "After EQ": phase_align_to_reference(normalize_for_plot(rx_eq_out), tx_ref),
                "After CPR": phase_align_to_reference(normalize_for_plot(cpr_out), tx_ref),
            },
            "rx_eye.png",
            "RX DSP Progression (Pol-X)"
        )
        print("Saved rx_eye.png")

    except Exception as e:
        print(f"CPR failed: {e}")

    try:
        metric_rx = cpr_out if cpr_out is not None else rx_eq_out
        metric_rx = to_2pol_matrix(metric_rx)
        metric_tx = to_2pol_matrix(tx_ref)

        rx_pol = metric_rx[:, 0]
        tx_pol = metric_tx[:, 0]
        rx_pol, tx_pol = align_lengths(rx_pol, tx_pol)

        BER, SER, SNR = fastBERcalc(rx_pol, tx_pol, 16, "qam")
        EVM = calcEVM(rx_pol, 16, "qam", symbTx=tx_pol)
        GMI, NGMI = monteCarloGMI(rx_pol, tx_pol, 16, "qam")

        final_metrics = {
            "BER": ensure_scalar(BER),
            "SER": ensure_scalar(SER),
            "SNR_dB": ensure_scalar(SNR),
            "EVM": ensure_scalar(EVM),
            "GMI": ensure_scalar(GMI),
            "NGMI": ensure_scalar(NGMI),
        }

        metrics = {
            "final_metrics": final_metrics,
            "stage_metrics": {
                "before_equalizer": stage_metrics_pre_eq,
                "after_foe": stage_metrics_post_foe,
                "after_equalizer": stage_metrics_post_eq,
                "after_cpr": stage_metrics_post_cpr if cpr_out is not None else None,
            },
            "timing_recovery": {
                "best_offset": best_offset,
                "blind_score": best_score,
                "best_symbol_lag": int(best_lag),
                "lag_score": float(lag_score),
            },
            "frequency_recovery": {
                "configured_frequency_offset_hz": float(FO),
                "estimated_frequency_offset_hz": float(fo_est),
                "rf_tuned_channel_offset_hz": float(rx_freqGrid[rfTuneIndex]),
                "target_channel_offset_hz": float(targetChannelOffsetHz),
                "lo_freq_shift_used_hz": float(lo_shift_hz),
                "edc_center_fc_hz": float(edcParam.Fc),
            },
            "reference_matching": {
                "matched_tx_channel_index": tx_channel_index,
            },
            "physical_settings": {
                "theta_sig_rad": float(theta_sig),
                "laser_linewidth_hz": float(tx.laserLinewidth),
                "equalizer_taps": int(eq_param.nTaps),
                "equalizer_num_iter": int(eq_param.numIter),
            },
        }

        print("\n=== Metrics (Pol-X, auto-matched reference) ===")
        for k, v in final_metrics.items():
            print(f"{k}: {v}")
        print(f"matched_tx_channel_index: {tx_channel_index}")
        print(f"best_offset: {best_offset}")
        print(f"best_score: {best_score}")
        print(f"best_symbol_lag: {best_lag}")
        print(f"lag_score: {lag_score}")
        print(f"FO_Hz: {metrics['frequency_recovery']['configured_frequency_offset_hz']}")
        print(f"FO_estimated_Hz: {metrics['frequency_recovery']['estimated_frequency_offset_hz']}")
        print(f"LO_shift_used_Hz: {metrics['frequency_recovery']['lo_freq_shift_used_hz']}")
        print(f"EDC_center_Fc_Hz: {metrics['frequency_recovery']['edc_center_fc_hz']}")

        with open("metrics.json", "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2)
        print("Saved metrics.json")

    except Exception as e:
        print(f"Metrics failed: {e}")

    summary = {
        "path": "Bristol -> Powergate",
        "modulation": "16QAM",
        "channels": int(tx.nChannels),
        "launch_power_dbm_per_channel": float(tx.powerPerChannel),
        "total_launch_power_dbm": float(
            power_mw_to_dbm(tx.nChannels * power_dbm_to_mw(tx.powerPerChannel))
        ),
        "symbol_rate_baud": tx.Rs,
        "spacing_hz": tx.wdmGridSpacing,
        "total_distance_km": total_distance,
        "rf_tuned_channel_index": int(rfTuneIndex),
        "rf_tuned_channel_offset_hz": float(rx_freqGrid[rfTuneIndex]),
        "target_channel_offset_hz": float(targetChannelOffsetHz),
        "matched_tx_channel_index": int(tx_channel_index),
        "receiver_chain": [
            "pdmCoherentReceiver",
            "edc",
            "matched_filter",
            "auto_reference_search",
            "symbol_delay_alignment",
            "timing_recovery",
            "fourthPowerFOE",
            "mimoAdaptEqualizer",
            "cpr",
            "metrics",
        ],
    }

    with open("run_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("\nDone.")
    print("Generated: metrics.json, run_summary.json, PSD/constellation figures")


if __name__ == "__main__":
    main()