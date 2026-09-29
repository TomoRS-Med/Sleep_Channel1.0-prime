"""Paper equation sets, separate from the earlier exploratory HH-like engine.

All channel kinetics below are taken from the PDF locations in paper_catalog.
This implementation is a transcription to validate against the published
traces; it is not yet a claimed reproduction of the papers.
"""
from __future__ import annotations

import json
import math
import warnings
from pathlib import Path

import numpy as np
from scipy.integrate import ODEintWarning, odeint
from scipy.signal import detrend, periodogram

from paper_catalog import FNAN_SATO, effective_model, validate_disabled_channels
from custom_equations import compile_modules, module_parameters


def _exp(x):
    return math.exp(max(-700.0, min(700.0, x)))


def _sigmoid(x):
    return 1.0 / (1.0 + _exp(-x))


def _alpha(z, factor):
    # z / (1-exp(-z/10)); analytic limit avoids a 0/0 at z=0.
    if abs(z) < 1e-5:
        return 10.0 * factor * (1.0 + z / 20.0 + z*z / 1200.0)
    return factor * z / (-math.expm1(-z / 10.0))


def _kv_rates(v):
    return _alpha(v+34.0, .01), .125*_exp(-(v+44.0)/25.0)


def _reduced_rhs(state, t, p, c, family):
    v, gate, ca = state
    mnap = _sigmoid((v+55.7)/7.7)
    mca = _sigmoid((v+20.0)/9.0)
    mkca = 1.0/(1.0+(c["Kd_uM"]/max(ca, 1e-12))**3.5)
    i_ca = p["gCa"]*mca**2*(v-c["VCa"])
    if family == "SAN":
        i_k = p["gK"]*gate**4*(v-c["VK"])
        an, bn = _kv_rates(v)
        dgate = 4.0*(an*(1.0-gate)-bn*gate)
    else:
        i_k = p["gKS"]*gate*(v-c["VK"])
        m_inf = _sigmoid((v+34.0)/6.5)
        tau = 8.0/(_exp(-(v+55.0)/30.0)+_exp((v+55.0)/30.0))
        dgate = (m_inf-gate)/tau
    intrinsic = (p["gL"]*(v-c["VL"]) + p["gNaP"]*mnap**3*(v-c["VNa"])
                 + i_k + i_ca + p["gKCa"]*mkca*(v-c["VK"]))
    return [-intrinsic/c["C"], dgate,
            -c["alphaCa"]*(10.0*c["A_mm2"]*i_ca)-ca/p["tauCa"]]


def _na_gates(v, shift_m=0.0, shift_h=0.0):
    z = v + 33.0 + shift_m
    am = _alpha(z, 0.1)
    bm = 4.0 * _exp(-(v + 53.7 + shift_m) / 12.0)
    ah = 0.07 * _exp(-(v + 50.0 + shift_h) / 10.0)
    bh = _sigmoid((v + 20.0 + shift_h) / 10.0)
    return am / (am + bm), ah, bh


def _an_rhs(state, t, p, c):
    v, hna, nk, ha, mks, ca, sa, sn, xn, sg = state
    vk, vna, vca = c["VK"], c["VNa"], c["VCa"]
    mna, ah, bh = _na_gates(v)
    an = _alpha(v + 34.0, .01)
    bn = .125 * _exp(-(v + 44.0) / 25.0)
    ma = _sigmoid((v + 50.0) / 20.0)
    ha_inf = _sigmoid(-(v + 80.0) / 6.0)
    mks_inf = _sigmoid((v + 34.0) / 6.5)
    tau_mks = 8.0 / (_exp(-(v + 55.0) / 30.0) + _exp((v + 55.0) / 30.0))
    mca = _sigmoid((v + 20.0) / 9.0)
    mkca = 1.0 / (1.0 + (c["Kd_uM"] / max(ca, 1e-12)) ** 3.5)
    mnap = _sigmoid((v + 55.7) / 7.7)
    har = _sigmoid(-(v + 75.0) / 4.0)
    intrinsic = (
        p["gL"]*(v-c["VL"]) + p["gNa"]*mna**3*hna*(v-vna)
        + p["gK"]*nk**4*(v-vk) + p["gA"]*ma**3*ha*(v-vk)
        + p["gKS"]*mks*(v-vk) + p["gCa"]*mca**2*(v-vca)
        + p["gKCa"]*mkca*(v-vk) + p["gNaP"]*mnap**3*(v-vna)
        + p["gAR"]*har*(v-vk)
    )
    i_ca = p["gCa"]*mca**2*(v-vca)
    # g_syn in µS, V in mV -> nA. Intrinsic mS/cm² * mV -> µA/cm²;
    # 10*A(mm²) converts the latter to nA.
    i_nmda = p["gNMDA"]*sn*(v-c["VNMDA"])
    syn_nA = (p["gAMPA"]*sa*(v-c["VAMPA"]) + i_nmda
              + p["gGABA"]*sg*(v-c["VGABA"]))
    density_to_nA = 10.0 * c["A_mm2"]
    f = _sigmoid((v - 20.0) / 2.0)
    return [
        -(intrinsic + syn_nA/density_to_nA)/c["C"],
        4.0*(ah*(1.0-hna)-bh*hna),
        4.0*(an*(1.0-nk)-bn*nk),
        (ha_inf-ha)/c["tau_hA"],
        (mks_inf-mks)/tau_mks,
        -c["alphaCa"]*(density_to_nA*i_ca+i_nmda)-ca/p["tauCa"],
        3.48*f-sa/c["tauAMPA"],
        .5*xn*(1.0-sn)-sn/c["tau_sNMDA"],
        3.48*f-xn/c["tau_xNMDA"],
        f-sg/c["tauGABA"],
    ]


def _nan_rhs(state, t, p, c):
    v, h, nk, na_mM = state
    vk, vna, vca = c["VK"], c["VNa"], c["VCa"]
    munav, ah, bh = _na_gates(v, p["x"], p["y"])
    an = _alpha(v + 34.0, .01)
    bn = .125 * _exp(-(v + 44.0) / 25.0)
    mca = _sigmoid((v + 20.0) / 9.0)
    g_lena = p["gLeak"]*(c["VL"]-vk)/(c["VLeNa"]-vk)
    i_na_nalcn = .44*g_lena*(v-vna)
    # The PDF labels Ke=32 as µM and the plotted/initial [Na] as mM. Its
    # representative slow oscillation requires the published *numbers* 32 and [Na] to be
    # compared directly. The unresolved unit conflict is disclosed in
    # PAPER_PROVENANCE.md; no replacement parameter is introduced here.
    mkna = 1.0/(1.0+(c["Ke_uM"]/max(na_mM, 1e-12))**c["Kf"])
    i_unav = p["gUNaV"]*munav**3*h*(v-vna)
    intrinsic = (p["gLeak"]*(v-c["VL"]) + p["gK"]*nk**4*(v-vk)
                 + i_unav + p["gKNa"]*mkna*(v-vk)
                 + p["gCa"]*mca**2*(v-vca))
    density_to_nA = 10.0*c["A_mm2"]
    return [
        -intrinsic/c["C"],
        4.0*(ah*(1.0-h)-bh*h),
        4.0*(an*(1.0-nk)-bn*nk),
        (-c["alphaNa_uM_per_nA_ms"]*density_to_nA*(i_unav+i_na_nalcn)
         - na_mM*1000.0/p["tauNa"])/1000.0,
    ]


def _fnan_rhs(state, t, p, c, composition=None):
    """Sato's full current set, optionally composing published channel modules."""
    v, h_unav, nk, na_mM, hna, ha, mks, ca, sa, sn, xn, sg = state
    vk, vna, vca = c["VK"], c["VNa"], c["VCa"]
    munav, ah_u, bh_u = _na_gates(v, p["x"], p["y"])
    mna, ah, bh = _na_gates(v)
    an = _alpha(v + 34.0, .01)
    bn = .125*_exp(-(v + 44.0)/25.0)
    if composition is not None:
        ma = _sigmoid((v + 50.0)/20.0)
    else:
        ma = _sigmoid((v + 44.0)/50.0)  # Sato Eq. 41.
    ha_inf = _sigmoid(-(v + 80.0)/6.0)
    mks_inf = _sigmoid((v + 34.0)/6.5)
    tau_mks = 8.0/(_exp(-(v + 55.0)/30.0) + _exp((v + 55.0)/30.0))
    mkca = 1.0/(1.0+(c["Kd_uM"]/max(ca, 1e-12))**3.5)
    mkna = 1.0/(1.0+(c["Ke_uM"]/max(na_mM, 1e-12))**c["Kf"])
    mca = _sigmoid((v + 20.0)/9.0)
    mnap = _sigmoid((v + 55.7)/7.7)
    har = _sigmoid(-(v + 75.0)/4.0)
    i_unav = p["gUNaV"]*munav**3*h_unav*(v-vna)
    i_na = p["gNa"]*mna**3*hna*(v-vna)
    i_nap = p["gNaP"]*mnap**3*(v-vna)
    i_ca = p["gCa"]*mca**2*(v-vca)
    g_lena = p["gLeak"]*(c["VL"]-vk)/(c["VLeNa"]-vk)
    i_na_nalcn = .44*g_lena*(v-vna)
    i_ca_nalcn = .25*g_lena*(v-vca)
    intrinsic = (
        p.get("gL", 0.0)*(v-c["VL"]) + p["gLeak"]*(v-c["VL"]) + p["gK"]*nk**4*(v-vk)
        + i_unav + p["gKNa"]*mkna*(v-vk) + i_ca + i_na
        + p["gA"]*ma**3*ha*(v-vk) + p["gKS"]*mks*(v-vk)
        + p["gKCa"]*mkca*(v-vk) + i_nap + p["gAR"]*har*(v-vk)
    )
    i_nmda = p["gNMDA"]*sn*(v-c["VNMDA"])
    syn_nA = (p["gAMPA"]*sa*(v-c["VAMPA"]) + i_nmda
              + p["gGABA"]*sg*(v-c["VGABA"]))
    density_to_nA = 10.0*c["A_mm2"]
    f = _sigmoid((v-20.0)/2.0)
    ca_influx = density_to_nA*(i_ca+i_ca_nalcn)
    return [
        -(intrinsic+syn_nA/density_to_nA)/c["C"],
        4.0*(ah_u*(1.0-h_unav)-bh_u*h_unav),
        4.0*(an*(1.0-nk)-bn*nk),
        (-c["alphaNa_uM_per_nA_ms"]*density_to_nA*
         (i_na+i_nap+i_unav+i_na_nalcn)-na_mM*1000.0/p["tauNa"])/1000.0,
        4.0*(ah*(1.0-hna)-bh*hna),
        (ha_inf-ha)/c["tau_hA"],
        (mks_inf-mks)/tau_mks,
        -c["alphaCa"]*ca_influx-ca/p["tauCa"],
        3.48*f-sa/c["tauAMPA"],
        .5*xn*(1.0-sn)-sn/c["tau_sNMDA"],
        3.48*f-xn/c["tau_xNMDA"],
        f-sg/c["tauGABA"],
    ]


def simulate(model_name, parameters=None, record_ms=1.0, fixed_overrides=None,
             progress=None, stop=None, disabled_channels=(), composition=None,
             custom_modules=None):
    spec = effective_model(model_name, composition)
    p = dict(spec["parameters"])
    p.update(module_parameters(custom_modules))
    c = dict(spec["fixed"])
    if parameters:
        if not set(parameters) <= set(p):
            raise ValueError("Only selected model and custom parameters can be changed")
        p.update(parameters)
    disabled = validate_disabled_channels(model_name, disabled_channels, composition)
    for key in disabled:
        p[key] = 0.0
    if fixed_overrides:
        if not set(fixed_overrides) <= set(c):
            raise ValueError("Only PDF-defined fixed constants can be changed")
        c.update(fixed_overrides)
    for key, value in c.items():
        if not math.isfinite(value):
            raise ValueError("Invalid fixed value for " + key)
        if key in ("C", "A_mm2", "Kd_uM", "Ke_uM", "Kf", "tau_hA",
                   "alphaCa", "alphaNa_uM_per_nA_ms", "tauAMPA",
                   "tau_sNMDA", "tau_xNMDA", "tauGABA") and value <= 0:
            raise ValueError("Fixed value must be positive: " + key)
    for key, value in p.items():
        if not math.isfinite(value) or (key in spec["parameters"] and
                                         key not in ("x", "y") and value < 0):
            raise ValueError("Invalid value for " + key)
    if p.get("tauCa", 1) <= 0 or p.get("tauNa", 1) <= 0:
        raise ValueError("Time constants must be positive")
    if not 0 < record_ms <= 10.0:
        raise ValueError("record_ms must be in (0,10] ms")
    t = np.arange(0.0, spec["duration_ms"]+record_ms/2, record_ms)
    if spec["family"] == "AN":
        y0 = [-45.0, .045, .54, .045, .34, 1.0, .01, .01, .01, .01]
        rhs = _an_rhs
        state_names = ("voltage_mV", "hNa", "nK", "hA", "mKS", "Ca_uM",
                       "sAMPA", "sNMDA", "xNMDA", "sGABA")
    elif spec["family"] == "NAN":
        y0 = [-45.0, .045, .54, 1.0]
        rhs = _nan_rhs
        state_names = ("voltage_mV", "hUNaV", "nK", "Na_mM")
    elif spec["family"] == "FNAN":
        # The PDF specifies V, Na, hNa/hUNaV, nK; the remaining initial
        # values follow the supplied Tatsuki AN supplemental procedures.
        y0 = [-45.0, .045, .54, 1.0, .045, .045, .34, 1.0,
              .01, .01, .01, .01]
        rhs = _fnan_rhs
        state_names = ("voltage_mV", "hUNaV", "nK", "Na_mM", "hNa",
                       "hA", "mKS", "Ca_uM", "sAMPA", "sNMDA", "xNMDA", "sGABA")
    elif spec["family"] == "COMPOSED":
        # Keep the published full-model states; absent currents have g=0.
        y0 = [-45.0, .045, .54, 1.0, .045, .045, .34, 1.0,
              .01, .01, .01, .01]
        state_names = ("voltage_mV", "hUNaV", "nK", "Na_mM", "hNa",
                       "hA", "mKS", "Ca_uM", "sAMPA", "sNMDA", "xNMDA", "sGABA")
        calc_p = {key: 0.0 for key in FNAN_SATO if key.startswith("g")}
        calc_p.update({"gL": 0.0, "x": 0.0, "y": 0.0})
        calc_p.update(p)
        rhs = lambda state, time, params, const: _fnan_rhs(
            state, time, params, const, spec["composition"])
        p_for_ode = calc_p
    elif spec["family"] in ("SAN", "RAN"):
        y0 = [-45.0, .54 if spec["family"] == "SAN" else .34, 1.0]
        rhs = lambda state, t, params, const: _reduced_rhs(
            state, t, params, const, spec["family"])
        state_names = ("voltage_mV", "nK" if spec["family"] == "SAN" else "mKS", "Ca_uM")
    else:
        raise ValueError("Unsupported PDF family: " + spec["family"])
    if spec["family"] != "COMPOSED":
        p_for_ode = p
    modules = compile_modules(custom_modules, spec["parameters"], c, state_names)
    if modules:
        base_rhs = rhs
        base_count = len(y0)
        offsets = []
        for module in modules:
            offsets.append(len(y0))
            y0.extend(module.initial)
            state_names += tuple(f"{module.name}.{key}" for key in module.states)
        na_index = state_names.index("Na_mM") if "Na_mM" in state_names else None
        ca_index = state_names.index("Ca_uM") if "Ca_uM" in state_names else None

        def rhs(state, time, params, const):
            derivatives = list(base_rhs(state[:base_count], time, params, const))
            context = {**const, **p, "V": state[0], "t": time}
            if na_index is not None:
                context["Na"] = state[na_index]
            if ca_index is not None:
                context["Ca"] = state[ca_index]
            for module, start in zip(modules, offsets):
                current, na_rate, ca_rate, gates = module.evaluate(
                    state[start:start+len(module.states)], context)
                derivatives[0] -= current / const["C"]
                if na_index is not None:
                    derivatives[na_index] += na_rate
                if ca_index is not None:
                    derivatives[ca_index] += ca_rate
                derivatives.extend(gates)
            return derivatives
    with warnings.catch_warnings():
        warnings.simplefilter("error", ODEintWarning)
        if progress is None:
            states, info = odeint(rhs, y0, t, args=(p_for_ode, c), rtol=1e-7,
                                  atol=1e-9, mxstep=5000, full_output=True)
        else:
            # Integrate in consecutive 500 ms blocks to report real simulated
            # time, rather than merely animating an unknown-duration task.
            block_size = max(1, int(round(500.0/record_ms)))
            chunks = []
            last = np.asarray(y0, dtype=float)
            for start in range(0, len(t)-1, block_size):
                if stop is not None and stop.is_set():
                    raise InterruptedError("Simulation stopped")
                end = min(start+block_size, len(t)-1)
                part, info = odeint(rhs, last, t[start:end+1], args=(p_for_ode, c),
                                    rtol=1e-7, atol=1e-9, mxstep=5000, full_output=True)
                if "successful" not in info["message"].lower() or not np.all(np.isfinite(part)):
                    raise FloatingPointError("ODE solver did not converge")
                chunks.append(part if start == 0 else part[1:])
                last = part[-1]
                progress(end, len(t)-1)
            states = np.concatenate(chunks)
    if "successful" not in info["message"].lower() or not np.all(np.isfinite(states)):
        raise FloatingPointError("ODE solver did not converge")
    return {"model": model_name, "parameters": p, "fixed": c,
            "composition": spec.get("composition"),
            "custom_modules": custom_modules or [],
            "model_display": ("Custom channels (" + model_name + " template)"
                              if composition is not None else model_name) +
                             (" + " + ", ".join(m.name for m in modules) if modules else ""),
            "disabled_channels": disabled, "time_ms": t,
            "states": states, "state_names": state_names,
            "analysis_from_ms": spec["analysis_from_ms"]}


def classify(result):
    """Sato et al., 2025 candidate classifier; SWO needs manual review."""
    active = result["time_ms"] >= result["analysis_from_ms"]
    v = result["states"][active, 0]
    t = result["time_ms"][active]
    duration_s = (t[-1]-t[0])/1000.0
    if len(v) < 3 or duration_s <= 0 or np.any(~np.isfinite(v)):
        return {"label": "ELSE", "peak_hz": None, "spikes_per_s": None}
    crossings = np.count_nonzero((v[:-1]+20.0)*(v[1:]+20.0) < 0)
    spikes_per_s = .5*crossings/duration_s
    if np.mean(v > -20.0) > .95:
        label, peak = "ELSE", None
    else:
        waveform = detrend(v)
        if np.max(np.abs(waveform)) < 1e-9:
            peak = 0.0
        else:
            f, power = periodogram(waveform/np.std(waveform),
                                   fs=1000.0/np.median(np.diff(t)))
            peak = float(f[1:][np.argmax(power[1:])])
        if spikes_per_s < 2.0 or peak == 0.0:
            label = "RESTING"
        elif peak >= 10.0:
            label = "AWAKE"
        elif spikes_per_s > 5.0*peak:
            label = "SWO_candidate"
        else:
            label = "SWO_few_spikes"
    return {"label": label, "peak_hz": peak,
            "spikes_per_s": round(float(spikes_per_s), 4),
            "min_mV": round(float(np.min(v)), 4),
            "max_mV": round(float(np.max(v)), 4),
            "manual_review_required": label == "SWO_candidate"}


def save_result(result, directory):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import font_manager
    from matplotlib.backends.backend_pdf import FigureCanvasPdf
    from matplotlib.figure import Figure

    # macOS includes Arial. Embed the chosen TrueType font in vector PDFs.
    installed = {font.name for font in font_manager.fontManager.ttflist}
    matplotlib.rcParams["font.family"] = "Arial" if "Arial" in installed else "DejaVu Sans"
    matplotlib.rcParams["pdf.fonttype"] = 42

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    metric = classify(result)
    spec = effective_model(result["model"], result.get("composition"))
    payload = {"model": result["model"],
               "model_display": result.get("model_display", result["model"]),
               "composition": result.get("composition"),
               "custom_modules": result.get("custom_modules", []),
               "cell_role": result.get("cell_role", "unassigned"),
               "cell_role_note": "The E/I role is a research assignment, not a cell-type-specific fit",
               "parameters": result["parameters"],
               "disabled_channels": list(result.get("disabled_channels", ())),
               "fixed": result["fixed"], "published_fixed": spec["fixed"],
               "sources": {k: spec[k] for k in
               ("equations", "baseline", "search")},
               "metrics": metric}
    (directory/"config_metrics.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    np.savez_compressed(directory/"states.npz", time_ms=result["time_ms"],
                        states=result["states"], state_names=np.array(result["state_names"]))
    fig = Figure(figsize=(10, 5.5), dpi=120)
    ax = fig.subplots(2, 1, sharex=True)
    t = result["time_ms"]/1000.0
    ax[0].plot(t, result["states"][:, 0], color="#1767ad", lw=.7)
    ax[0].set_ylabel("V (mV)")
    ion_name = "Na_mM" if "Na_mM" in result["state_names"] else "Ca_uM"
    ax[1].plot(t, result["states"][:, result["state_names"].index(ion_name)],
               color="#b34b8b", lw=1)
    ax[1].set_ylabel("Ca (µM)" if ion_name == "Ca_uM" else "Na (mM)")
    if spec["family"] in ("FNAN", "COMPOSED"):
        ax_ca = ax[1].twinx()
        ax_ca.plot(t, result["states"][:, result["state_names"].index("Ca_uM")],
                   color="#3b9564", lw=.8, alpha=.75)
        ax_ca.set_ylabel("Ca (µM)")
    ax[1].set_xlabel("Time (s)")
    for a in ax:
        a.axvspan(0, result["analysis_from_ms"]/1000.0, color="#dce3ea", alpha=.45)
    role = result.get("cell_role")
    off = result.get("disabled_channels", ())
    fig.suptitle((role + " | " if role else "") + result.get("model_display", result["model"])
                 + " | " + metric["label"]
                 + (" | OFF: " + ", ".join(off) if off else ""))
    fig.tight_layout()
    # Use the PDF canvas directly; dynamic backend lookup can omit this module
    # from a PyInstaller bundle even when desktop Python can save PDFs.
    FigureCanvasPdf(fig).print_pdf(directory/"trace.pdf")
    return metric
