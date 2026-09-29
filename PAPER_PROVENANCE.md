# Model sources and validation scope

| Model | Equations | Representative values | Default search domains |
|---|---|---|---|
| Tatsuki AN | Tatsuki et al., 2016, Supplemental Procedures | Tatsuki et al., 2016, Table S1 | Intrinsic g 0.01–100 mS/cm²; synaptic g 0.002–20 µS; τCa 10–1000 ms |
| Yamada RAN | Yamada et al., 2022, STAR Methods | Yamada et al., 2022, Table S2, Figure 2A | Intrinsic g 0.01–100 mS/cm²; τCa 10–1000 ms |
| Yoshida SAN | Yoshida et al., 2018; Yamada et al., 2022, STAR Methods | Yamada et al., 2022, Table S2, Figure S2E | Intrinsic g 0.01–100 mS/cm²; τCa 10–1000 ms |
| Sato NAN / FNAN | Sato et al., 2025, STAR Methods | Sato et al., 2025, Tables S1 / S3 | Intrinsic g 0.01–100 mS/cm²; synaptic g 0.001–10 µS; τNa 1000–10000 ms; τCa 10–1000 ms; x/y −45–45 mV |

Sweep levels use geometric spacing for positive ranges and linear spacing for signed voltage shifts. A search group stores its parameter names, draw or sweep-level counts, ranges and mode. Random mode samples continuous values independently within each parameter's bounds and pairs them into one condition per draw; each parameter needs the same draw count. Sweep mode enumerates the entire Cartesian product of its levels. The default count is 100 per parameter; two parameters therefore produce 100 paired random conditions or 10,000 sweep conditions. A single control can set all counts together. Random draw indices are one-based in `summary.csv`; the seed and configuration allow exact replay. Turning off a listed channel sets its conductance to zero as an experimental knockout; the disabled names and effective zero values are saved with each run. The published representative values remain available in the model catalog.

Every model simulates for 20 s and analyzes only the final 10 s. The recorded time step is 1 ms. The automatic classifier follows Sato et al., 2025: −20 mV crossings, an FFT peak, RESTING below 2 spikes/s, AWAKE at a peak of at least 10 Hz, and a SWO candidate when spikes/s exceeds five times a positive peak below 10 Hz. Visual waveform review is required for SWO candidates. Plots are saved as vector PDFs, with Arial embedded when it is installed on the compute host.

Custom assemblies select channel currents and initial conductance values from the listed model sources. The summed current system is an exploratory hybrid, not itself a published model or a cell-type-specific fit. Custom runs use the full set of ion and gate states, run for 20 s, and analyze the last 10 s. Channel selections and value sources are saved in the output configuration; custom searches use edited bounds.

Hand-entered current modules are separate experimental definitions. Instantaneous expressions and ODE state derivatives are validated, and new numeric parameters can be searched alongside published-model parameters. Explicit additions to the Na and Ca rates are stored with the module. Their numerical output and biological interpretation require user review.

Representative traces have not been verified for numerical identity with published figures. E/I labels in the app designate research assignments; the program does not fit inhibitory-cell physiology or implement network connections.
