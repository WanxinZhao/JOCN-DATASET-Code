import math
from typing import Any, Dict


def run_opticommpy(sim_config: Dict[str, Any]) -> Dict[str, Any]:
    """Run OptiCommPy with the real backend only."""
    import numpy as np
    from optic.comm.metrics import GNmodel_OSNR, theoryBER
    from optic.models.channels import manakovSSF
    from optic.models.tx import simpleWDMTx
    from optic.utils import parameters

    modulation = str(sim_config.get("modulation", "16QAM")).strip().upper()
    if modulation == "QPSK":
        modulation_order = 4
        const_type = "psk"
    elif modulation == "16QAM":
        modulation_order = 16
        const_type = "qam"
    elif modulation == "64QAM":
        modulation_order = 64
        const_type = "qam"
    else:
        raise ValueError(f"Unsupported modulation format: {modulation}")

    launch_power_dbm = float(sim_config.get("launch_power", 0.0))
    span_length_km = float(sim_config.get("span_length", 80.0))
    baud_rate_hz = float(sim_config.get("baud_rate", 32.0)) * 1e9
    channel_spacing_hz = float(sim_config.get("channel_spacing", 50.0)) * 1e9
    channel_count = int(sim_config.get("channels", 1))
    bits_per_symbol = int(math.log2(modulation_order))
    requested_n_bits = int(sim_config.get("n_bits", 2048))
    min_n_bits = max(2048, bits_per_symbol * 256)
    rounded_n_bits = ((max(requested_n_bits, min_n_bits) + bits_per_symbol - 1) // bits_per_symbol) * bits_per_symbol
    n_bits = rounded_n_bits

    tx_param = parameters()
    tx_param.M = modulation_order
    tx_param.constType = const_type
    tx_param.Rs = baud_rate_hz
    tx_param.SpS = int(sim_config.get("samples_per_symbol", 4))
    tx_param.nBits = n_bits
    tx_param.pulseType = sim_config.get("pulse_type", "rrc")
    tx_param.pulseRollOff = float(sim_config.get("roll_off", 0.15))
    tx_param.powerPerChannel = launch_power_dbm
    tx_param.nChannels = channel_count
    tx_param.nPolModes = int(sim_config.get("n_pol_modes", 2))
    tx_param.wdmGridSpacing = channel_spacing_hz
    tx_param.prgsBar = False

    tx_signal, _, tx_param = simpleWDMTx(tx_param)

    channel_param = parameters()
    channel_param.Ltotal = span_length_km
    channel_param.Lspan = float(sim_config.get("fiber_span_length", min(80.0, span_length_km)))
    channel_param.hz = float(sim_config.get("ssfm_step_km", 1.0))
    channel_param.alpha = float(sim_config.get("loss_coef_db_per_km", 0.2))
    channel_param.D = float(sim_config.get("dispersion_ps_per_nm_per_km", 16.0))
    channel_param.gamma = float(sim_config.get("gamma_per_w_per_km", 1.3))
    channel_param.Fc = float(sim_config.get("carrier_frequency_hz", 193.1e12))
    channel_param.Fs = tx_param.Rs * tx_param.SpS
    channel_param.amp = sim_config.get("amplifier", "edfa")
    channel_param.NF = float(sim_config.get("noise_figure_db", 4.5))
    channel_param.prgsBar = False
    channel_param.nlprMethod = bool(sim_config.get("nonlinear_phase_rotation_adaptive", False))

    rx_signal = manakovSSF(tx_signal, channel_param)
    if isinstance(rx_signal, tuple):
        rx_signal = rx_signal[0]

    osnr_linear, nli_power, ase_power = GNmodel_OSNR(
        tx_param.Rs,
        channel_count,
        channel_spacing_hz,
        np.array([launch_power_dbm]),
        channel_param,
    )

    osnr_db = 10.0 * math.log10(float(osnr_linear[0]))
    snr_db = osnr_db - 10.0 * math.log10(tx_param.Rs / 12.5e9)
    ebn0_db = snr_db - 10.0 * math.log10(math.log2(modulation_order))
    ber = float(theoryBER(modulation_order, ebn0_db, const_type))
    tx_power_w = float(np.mean(np.abs(tx_signal) ** 2))
    rx_power_w = float(np.mean(np.abs(rx_signal) ** 2))

    metrics = {
        "gn_model_osnr_db": round(osnr_db, 3),
        "effective_snr_db": round(snr_db, 3),
        "theory_ber": ber,
        "tx_power_dbm": round(10.0 * math.log10(tx_power_w / 1e-3), 3),
        "rx_power_dbm": round(10.0 * math.log10(rx_power_w / 1e-3), 3),
        "ase_power_w": float(ase_power[0]),
        "nli_power_w": float(nli_power[0]),
        "channels_simulated": channel_count,
        "transmitter_type": sim_config.get("transmitter_type"),
        "channel_pattern": sim_config.get("channel_pattern"),
    }
    return {
        "engine": "opticommpy",
        "backend": "real",
        "status": "ok",
        "metrics": metrics,
    }
