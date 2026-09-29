"""Paper values and model-specific search domains.

The model names describe equation sets, not demonstrated cell identities.
The inhibitory-cell role is an experimental assignment of published equations.
"""
from __future__ import annotations

import copy
import math

AN_FIXED = {
    "C": 1.0, "A_mm2": 0.02, "VL": -60.95, "VNa": 55.0, "VK": -100.0,
    "VCa": 120.0, "Kd_uM": 30.0, "tau_hA": 15.0, "alphaCa": 0.5,
    "VAMPA": 0.0, "VNMDA": 0.0, "VGABA": -70.0,
    "tauAMPA": 2.0, "tau_sNMDA": 100.0, "tau_xNMDA": 2.0,
    "tauGABA": 10.0,
}
AN_TATSUKI = {
    "gL": 0.03573, "gNa": 12.2438, "gK": 2.61868, "gA": 1.79259,
    "gKS": 0.0350135, "gNaP": 0.0717984, "gAR": 0.0166454,
    "gCa": 0.0256867, "gKCa": 2.34906, "gAMPA": 0.513425,
    "gNMDA": 0.00434132, "gGABA": 0.00252916, "tauCa": 121.403,
}
AN_RANGES = {
    **{k: (0.01, 100.0, "log", "mS/cm²") for k in
       ("gL", "gNa", "gK", "gA", "gKS", "gNaP", "gAR", "gCa", "gKCa")},
    **{k: (0.002, 20.0, "log", "µS") for k in ("gAMPA", "gNMDA", "gGABA")},
    "tauCa": (10.0, 1000.0, "log", "ms"),
}
NAN_FIXED = {
    "C": 1.0, "A_mm2": 0.02, "VL": -60.95, "VLeNa": 0.0,
    "VNa": 55.0, "VK": -100.0, "VCa": 120.0,
    "Ke_uM": 32.0, "Kf": 3.0, "alphaNa_uM_per_nA_ms": 1.0,
}
NAN_SATO = {
    "gK": 48.19198701, "gUNaV": 6.104226316, "gKNa": 9.657438734,
    "gLeak": 0.062345227, "gCa": 0.391216425,
    "tauNa": 6638.79306935, "x": 28.21858435, "y": -7.96971366,
}
NAN_RANGES = {
    **{k: (0.01, 100.0, "log", "mS/cm²") for k in
       ("gK", "gUNaV", "gKNa", "gLeak", "gCa")},
    "tauNa": (1000.0, 10000.0, "log", "ms"),
    "x": (-45.0, 45.0, "uniform", "mV"),
    "y": (-45.0, 45.0, "uniform", "mV"),
}
FNAN_SATO = {
    "gK": 72.12222201, "gUNaV": 0.304654151, "gKNa": 10.06806462,
    "gLeak": 0.040563611, "gCa": 0.294154229,
    "x": 18.4297867, "y": 34.85857952,
    "gNa": 1.422098676, "gA": 0.01332761, "gKS": 0.239625682,
    "gKCa": 0.205446971, "gNaP": 3.071575267, "gAR": 0.020469817,
    "gAMPA": 0.023553782, "gNMDA": 0.04138171, "gGABA": 0.0,
    "tauCa": 70.63624625, "tauNa": 3352.688071,
}
FNAN_FIXED = {**AN_FIXED, **NAN_FIXED}
FNAN_RANGES = {
    **{k: (0.01, 100.0, "log", "mS/cm²") for k in
       ("gK", "gUNaV", "gKNa", "gLeak", "gCa", "gNa", "gA", "gKS",
        "gKCa", "gNaP", "gAR")},
    "x": (-45.0, 45.0, "uniform", "mV"),
    "y": (-45.0, 45.0, "uniform", "mV"),
    **{k: (0.001, 10.0, "log", "µS") for k in
       ("gAMPA", "gNMDA", "gGABA")},
    "tauCa": (10.0, 1000.0, "log", "ms"),
    "tauNa": (1000.0, 10000.0, "log", "ms"),
}

# Reduced AN equations are the published zero-conductance SAN/RAN limits.
# Only channels present in the respective reduced equation are user-searchable.
SAN_YOSHIDA = {
    "gL": 0.076208, "gNaP": 0.697291, "gK": 19.258326,
    "gCa": 0.084111, "gKCa": 14.093700, "tauCa": 709.874820,
}
RAN_YAMADA = {
    "gL": 1.406030, "gNaP": 6.636438, "gKS": 19.138263,
    "gCa": 1.914677, "gKCa": 0.296167, "tauCa": 884.719189,
}
REDUCED_RANGES = {
    key: AN_RANGES[key] for key in
    ("gL", "gNaP", "gK", "gKS", "gCa", "gKCa", "tauCa")
}

MODELS = {
    "Tatsuki 2016 AN": {
        "equations": "Tatsuki et al., 2016, Supplemental Procedures Eqs. 1–13",
        "baseline": "Tatsuki et al., 2016, Supplemental Table S1",
        "search": "Tatsuki et al., 2016, Supplemental Procedures, Parameter search",
        "family": "AN", "duration_ms": 20000.0, "analysis_from_ms": 10000.0,
        "bifurcation": (0.001, 10.0),
        "fixed": AN_FIXED, "parameters": AN_TATSUKI, "ranges": AN_RANGES,
    },
    "Sato 2025 NAN": {
        "equations": "Sato et al., 2025, STAR Methods Eqs. 8, 12–31, 64–95",
        "baseline": "Sato et al., 2025, Table S1",
        "search": "Sato et al., 2025, STAR Methods, Parameter search",
        "family": "NAN", "duration_ms": 20000.0, "analysis_from_ms": 10000.0,
        "bifurcation": (0.01, 100.0),
        "fixed": NAN_FIXED, "parameters": NAN_SATO, "ranges": NAN_RANGES,
    },
    "Sato 2025 FNAN": {
        "equations": "Sato et al., 2025, STAR Methods Eqs. 11–63, 64–98",
        "baseline": "Sato et al., 2025, Table S3 (Figure S5)",
        "search": "Sato et al., 2025, STAR Methods, Parameter search",
        "family": "FNAN", "duration_ms": 20000.0, "analysis_from_ms": 10000.0,
        "bifurcation": (0.01, 100.0),
        "fixed": FNAN_FIXED, "parameters": FNAN_SATO, "ranges": FNAN_RANGES,
    },
    "Yoshida 2018 SAN": {
        "equations": "Yoshida et al., 2018, SAN; Yamada et al., 2022, STAR Methods",
        "baseline": "Yamada et al., 2022, Table S2 (Figure S2E)",
        "search": "Yamada et al., 2022, STAR Methods, Parameter search",
        "family": "SAN", "duration_ms": 20000.0, "analysis_from_ms": 10000.0,
        "bifurcation": (0.001, 10.0), "fixed": AN_FIXED,
        "parameters": SAN_YOSHIDA,
        "ranges": {k: REDUCED_RANGES[k] for k in SAN_YOSHIDA},
    },
    "Yamada 2022 RAN": {
        "equations": "Yamada et al., 2022, STAR Methods, RAN model",
        "baseline": "Yamada et al., 2022, Table S2 (Figure 2A)",
        "search": "Yamada et al., 2022, STAR Methods, Parameter search",
        "family": "RAN", "duration_ms": 20000.0, "analysis_from_ms": 10000.0,
        "bifurcation": (0.001, 10.0), "fixed": AN_FIXED,
        "parameters": RAN_YAMADA,
        "ranges": {k: REDUCED_RANGES[k] for k in RAN_YAMADA},
    },
}

# The E/I labels are experimental assignments, not cell-type specific fits.
CELL_ROLE_MODELS = {
    "E": ("Tatsuki 2016 AN", "Sato 2025 NAN", "Sato 2025 FNAN",
          "Yoshida 2018 SAN"),
    "I": ("Tatsuki 2016 AN", "Yamada 2022 RAN"),
}

CHANNEL_LABELS = {
    "gL": "Leak", "gLeak": "Leak", "gNa": "NaV", "gUNaV": "UNaV",
    "gNaP": "Persistent NaP", "gK": "K (Kv)", "gKNa": "KNa",
    "gA": "A-type K", "gKS": "Slow K", "gAR": "Rectifier K",
    "gCa": "Voltage-gated Ca", "gKCa": "KCa",
    "gAMPA": "AMPA receptor", "gNMDA": "NMDA receptor",
    "gGABA": "GABA receptor",
}


def channel_sources(key):
    """Published equation sets that contain this conductance."""
    if key not in CHANNEL_LABELS:
        raise ValueError("Unknown channel: " + str(key))
    return tuple(name for name, spec in MODELS.items() if key in spec["parameters"])


def compose_model(base_model, composition):
    """Build a new, explicitly experimental sum of published current modules."""
    base = get_model(base_model)
    if not isinstance(composition, dict) or not isinstance(composition.get("channels"), dict):
        raise ValueError("A composed model needs a channel-to-source selection")
    requested = composition["channels"]
    if not requested or not set(requested) <= set(CHANNEL_LABELS):
        raise ValueError("Select at least one published channel module")
    calcium_source = "Sato 2025 FNAN"
    if composition.get("calcium_source", calcium_source) != calcium_source:
        raise ValueError("The composed Ca balance must use Sato 2025 FNAN")
    channels = {key: requested[key] for key in CHANNEL_LABELS if key in requested}
    for key, source in channels.items():
        if source not in channel_sources(key):
            raise ValueError(f"{key} is not defined in {source}")
    parameters = {key: MODELS[source]["parameters"][key]
                  for key, source in channels.items()}
    ranges = {key: MODELS[source]["ranges"][key]
              for key, source in channels.items()}
    if "gUNaV" in channels:
        source = MODELS[channels["gUNaV"]]
        for key in ("x", "y"):
            parameters[key], ranges[key] = source["parameters"][key], source["ranges"][key]
    ca_source = (base_model if "tauCa" in base["parameters"] else
                 next((channels[k] for k in ("gCa", "gKCa")
                       if k in channels and "tauCa" in MODELS[channels[k]]["parameters"]),
                      calcium_source))
    na_source = (base_model if "tauNa" in base["parameters"] else
                 next((channels[k] for k in ("gUNaV", "gKNa", "gLeak")
                       if k in channels and "tauNa" in MODELS[channels[k]]["parameters"]),
                      "Sato 2025 FNAN"))
    for key, source in (("tauCa", ca_source), ("tauNa", na_source)):
        parameters[key] = MODELS[source]["parameters"][key]
        ranges[key] = MODELS[source]["ranges"][key]
    sources = tuple(dict.fromkeys((base_model, *channels.values(),
                                   *(('Tatsuki 2016 AN',) if 'gA' in channels else ()),
                                   calcium_source, ca_source, na_source)))
    return {
        "family": "COMPOSED", "duration_ms": 20000.0,
        "analysis_from_ms": 10000.0, "bifurcation": base["bifurcation"],
        "fixed": {**FNAN_FIXED, **base["fixed"]},
        "parameters": parameters, "ranges": ranges,
        "composition": {"channels": channels, "calcium_source": calcium_source},
        "equations": "Published channel equations from " + ", ".join(sources),
        "baseline": "Per-channel representative values from the selected source models",
        "search": "Per-channel default ranges from the selected source models",
    }


def effective_model(model_name, composition=None):
    return get_model(model_name) if composition is None else compose_model(model_name, composition)


def available_channels(model_name, composition=None):
    return {key: CHANNEL_LABELS[key] for key in effective_model(model_name, composition)["parameters"]
            if key in CHANNEL_LABELS}


def validate_disabled_channels(model_name, disabled_channels, composition=None):
    disabled = set(disabled_channels or ())
    if not disabled <= set(available_channels(model_name, composition)):
        raise ValueError("A disabled channel must be a conductance in the selected model")
    return tuple(sorted(disabled))


def validate_assignment(cell_role, model_name):
    if cell_role not in CELL_ROLE_MODELS or model_name not in CELL_ROLE_MODELS[cell_role]:
        raise ValueError("Select an allowed published equation set for E or I")


def get_model(name):
    if name not in MODELS:
        raise ValueError("Unknown paper model: " + str(name))
    return copy.deepcopy(MODELS[name])


def validate_ranges(model, ranges, groups):
    allowed = set(model["ranges"])
    if set(ranges) != allowed:
        raise ValueError("The range table must contain exactly the selected model parameters")
    for key, (lo, hi, distribution, _) in ranges.items():
        if (not math.isfinite(lo) or not math.isfinite(hi) or not lo < hi
                or distribution not in ("log", "uniform", "neglog")
                or distribution == "log" and lo <= 0
                or distribution == "neglog" and hi >= 0):
            raise ValueError("Invalid search range for " + key)
    if not groups or any(not group or not set(group) <= allowed for group in groups):
        raise ValueError("Select at least one valid parameter combination")
