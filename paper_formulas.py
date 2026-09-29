"""Readable equations for the supported paper-derived neuron models."""
from paper_catalog import (CHANNEL_LABELS, effective_model, get_model,
                           validate_disabled_channels)


CHANNELS = {
    "leak": """Leak current
I_L = gL(V − VL)""",
    "nan_leak": """Leak and NALCN sodium component
I_leak = gLeak(V − VL)
gLeNa = gLeak(VL − VK)/(VLeNa − VK)
I_Na,NALCN = 0.44 gLeNa(V − VNa)""",
    "na": """Fast voltage-gated NaV current
I_Na = gNa mNa∞³ hNa(V − VNa)
mNa∞ = αm/(αm + βm)
αm = 0.1(V + 33)/[1 − exp(−(V + 33)/10)]
βm = 4 exp(−(V + 53.7)/12)
dhNa/dt = 4[αh(1 − hNa) − βh hNa]
αh = 0.07 exp(−(V + 50)/10)
βh = 1/[1 + exp(−(V + 20)/10)]""",
    "unav": """Shifted UNaV current
I_UNaV = gUNaV mUNaV∞³ hUNaV(V − VNa)
mUNaV∞ = αm'/(αm' + βm')
αm' = 0.1(V + 33 + x)/[1 − exp(−(V + 33 + x)/10)]
βm' = 4 exp(−(V + 53.7 + x)/12)
dhUNaV/dt = 4[αh'(1 − hUNaV) − βh' hUNaV]
αh' = 0.07 exp(−(V + 50 + y)/10)
βh' = 1/[1 + exp(−(V + 20 + y)/10)]""",
    "kv": """Delayed-rectifier K current
I_K = gK nK⁴(V − VK)
dnK/dt = 4[αn(1 − nK) − βn nK]
αn = 0.01(V + 34)/[1 − exp(−(V + 34)/10)]
βn = 0.125 exp(−(V + 44)/25)""",
    "kna": """Sodium-dependent KNa current
I_KNa = gKNa mKNa∞(V − VK)
mKNa∞ = 1/[1 + (Ke/[Na])^Kf]""",
    "a": """A-type K current
I_A = gA mA∞³ hA(V − VK)
mA∞ = 1/[1 + exp(−(V + 50)/20)]
dhA/dt = (hA∞ − hA)/τhA
hA∞ = 1/[1 + exp((V + 80)/6)]""",
    "a_fnan": """A-type K current (FNAN activation curve)
I_A = gA mA∞³ hA(V − VK)
mA∞ = 1/[1 + exp(−(V + 44)/50)]
dhA/dt = (hA∞ − hA)/τhA
hA∞ = 1/[1 + exp((V + 80)/6)]""",
    "ks": """Slow K current
I_KS = gKS mKS(V − VK)
dmKS/dt = (mKS∞ − mKS)/τmKS
mKS∞ = 1/[1 + exp(−(V + 34)/6.5)]
τmKS = 8/[exp(−(V + 55)/30) + exp((V + 55)/30)]""",
    "ca": """Voltage-gated Ca current
I_Ca = gCa mCa∞²(V − VCa)
mCa∞ = 1/[1 + exp(−(V + 20)/9)]""",
    "kca": """Calcium-dependent KCa current
I_KCa = gKCa mKCa∞(V − VK)
mKCa∞ = 1/[1 + (Kd/[Ca])³·⁵]""",
    "nap": """Persistent NaP current
I_NaP = gNaP mNaP∞³(V − VNa)
mNaP∞ = 1/[1 + exp(−(V + 55.7)/7.7)]""",
    "ar": """Inward-rectifier K current
I_AR = gAR hAR∞(V − VK)
hAR∞ = 1/[1 + exp((V + 75)/4)]""",
    "ampa": """AMPA synaptic current
I_AMPA = gAMPA sAMPA(V − VAMPA)
f(V) = 1/[1 + exp(−(V − 20)/2)]
dsAMPA/dt = 3.48 f(V) − sAMPA/τAMPA""",
    "nmda": """NMDA synaptic current
I_NMDA = gNMDA sNMDA(V − VNMDA)
dsNMDA/dt = 0.5 xNMDA(1 − sNMDA) − sNMDA/τsNMDA
dxNMDA/dt = 3.48 f(V) − xNMDA/τxNMDA""",
    "gaba": """GABA synaptic current
I_GABA = gGABA sGABA(V − VGABA)
dsGABA/dt = f(V) − sGABA/τGABA""",
}

CHANNEL_PARAMETER = {
    "leak": "gL", "nan_leak": "gLeak", "na": "gNa", "unav": "gUNaV",
    "kv": "gK", "kna": "gKNa", "a": "gA", "a_fnan": "gA",
    "ks": "gKS", "ca": "gCa", "kca": "gKCa", "nap": "gNaP",
    "ar": "gAR", "ampa": "gAMPA", "nmda": "gNMDA", "gaba": "gGABA",
}


def _composed_equations(model_name, spec, disabled):
    channels = spec["composition"]["channels"]
    names = {"gL": "leak", "gLeak": "nan_leak", "gNa": "na",
             "gUNaV": "unav", "gNaP": "nap", "gK": "kv", "gKNa": "kna",
             "gA": "a",
             "gKS": "ks", "gAR": "ar", "gCa": "ca", "gKCa": "kca",
             "gAMPA": "ampa", "gNMDA": "nmda", "gGABA": "gaba"}
    currents = {"gL": "I_L", "gLeak": "I_leak", "gNa": "I_Na",
                "gUNaV": "I_UNaV", "gNaP": "I_NaP", "gK": "I_K",
                "gKNa": "I_KNa", "gA": "I_A", "gKS": "I_KS", "gAR": "I_AR",
                "gCa": "I_Ca", "gKCa": "I_KCa", "gAMPA": "I_AMPA",
                "gNMDA": "I_NMDA", "gGABA": "I_GABA"}
    intrinsic = [currents[key] for key in channels if key not in
                 ("gAMPA", "gNMDA", "gGABA")]
    synaptic = [currents[key] for key in channels if key in
                ("gAMPA", "gNMDA", "gGABA")]
    membrane = ("C A dV/dt = −A(" + " + ".join(intrinsic or ["0"]) + ")"
                + (" − " + " − ".join(synaptic) if synaptic else ""))
    sodium = [current for key, current in
              (("gNa", "I_Na"), ("gNaP", "I_NaP"),
               ("gUNaV", "I_UNaV"), ("gLeak", "I_Na,NALCN")) if key in channels]
    na_balance = ("d[Na]/dt = {−αNa(10A)(" + " + ".join(sodium or ["0"])
                  + ") − 1000[Na]/τNa}/1000")
    ca_currents = [term for key, term in
                   (("gCa", "I_Ca"), ("gLeak", "I_Ca,NALCN")) if key in channels]
    ca_influx = "10A(" + " + ".join(ca_currents or ["0"]) + ")"
    ca_balance = "d[Ca]/dt = −αCa(" + ca_influx + ") − [Ca]/τCa"
    if "gLeak" in channels:
        ca_balance += "\nI_Ca,NALCN = 0.25 gLeNa(V − VCa)"
    sections = ["CUSTOM CHANNEL ASSEMBLY | " + model_name + " template",
                "Membrane currents and ion balances combine published terms; "
                "this hybrid is not a published cell model.",
                "MEMBRANE POTENTIAL\n" + membrane,
                "ION CONCENTRATION\n" + na_balance + "\n" + ca_balance +
                "\nCa balance: " + spec["composition"]["calcium_source"]]
    for key in CHANNEL_LABELS:
        if key not in channels:
            continue
        module = CHANNELS[names[key]]
        if key in disabled:
            module = module.splitlines()[0] + f" — OFF\n{key} = 0; this current is zero."
        sections.append(module + "\nSource: " +
                        ("Tatsuki 2016 AN" if key == "gA" else channels[key]))
    sections.append("Units: V in mV, time in ms, A in mm². Intrinsic currents are "
                    "µA/cm²; synaptic currents are nA. The density-to-nA factor is 10A.")
    return "\n\n".join(sections)


def equations_for(model_name, disabled_channels=(), composition=None):
    """Return only the currents and gates active in the selected model."""
    spec = effective_model(model_name, composition)
    disabled = set(validate_disabled_channels(model_name, disabled_channels, composition))
    if composition is not None:
        return _composed_equations(model_name, spec, disabled)
    family = spec["family"]
    if family == "AN":
        membrane = ("C A dV/dt = −A(I_L + I_Na + I_K + I_A + I_KS + I_Ca + "
                    "I_KCa + I_NaP + I_AR) − I_AMPA − I_NMDA − I_GABA")
        ions = "d[Ca]/dt = −αCa(10A I_Ca + I_NMDA) − [Ca]/τCa"
        active = ("leak", "na", "kv", "a", "ks", "ca", "kca", "nap", "ar",
                  "ampa", "nmda", "gaba")
    elif family in ("SAN", "RAN"):
        potassium = "I_K" if family == "SAN" else "I_KS"
        membrane = f"C dV/dt = −(I_L + I_NaP + {potassium} + I_Ca + I_KCa)"
        ions = "d[Ca]/dt = −αCa(10A I_Ca) − [Ca]/τCa"
        active = ("leak", "nap", "kv" if family == "SAN" else "ks", "ca", "kca")
    elif family == "NAN":
        membrane = "C dV/dt = −(I_leak + I_K + I_UNaV + I_KNa + I_Ca)"
        ions = ("d[Na]/dt = {−αNa(10A)(I_UNaV + I_Na,NALCN) "
                "− 1000[Na]/τNa}/1000\n[Ca] is not a dynamic state in NAN.")
        active = ("nan_leak", "unav", "kv", "kna", "ca")
    elif family == "FNAN":
        membrane = ("C A dV/dt = −A(I_leak + I_K + I_UNaV + I_KNa + I_Ca + "
                    "I_Na + I_A + I_KS + I_KCa + I_NaP + I_AR) "
                    "− I_AMPA − I_NMDA − I_GABA")
        ions = ("d[Na]/dt = {−αNa(10A)(I_Na + I_NaP + I_UNaV + I_Na,NALCN) "
                "− 1000[Na]/τNa}/1000\n"
                "d[Ca]/dt = −αCa(10A)(I_Ca + I_Ca,NALCN) − [Ca]/τCa\n"
                "I_Ca,NALCN = 0.25 gLeNa(V − VCa)")
        active = ("nan_leak", "unav", "kv", "kna", "ca", "na", "a_fnan",
                  "ks", "kca", "nap", "ar", "ampa", "nmda", "gaba")
    else:
        raise ValueError("No equations for " + model_name)
    channel_sections = []
    for name in active:
        key = CHANNEL_PARAMETER[name]
        if key in disabled:
            title = CHANNELS[name].splitlines()[0]
            channel_sections.append(f"{title} — OFF\n{key} = 0; this current is zero.")
        else:
            channel_sections.append(CHANNELS[name])
    sections = [f"{model_name}  |  {spec['equations']}",
                "MEMBRANE POTENTIAL\n" + membrane,
                "ION CONCENTRATION\n" + ions,
                *channel_sections,
                "Units: V in mV, time in ms, A in mm². Intrinsic currents are "
                "µA/cm²; synaptic currents are nA. The density-to-nA factor is 10A. "
                "At removable 0/0 points, activation rates use their analytic limits."]
    return "\n\n".join(sections)
