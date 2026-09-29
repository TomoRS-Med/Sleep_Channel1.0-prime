# Channel Circuit Lab 1.0 Prime (Linux browser edition)

Run the calculation engine on a Linux computer and use its interface in a browser on your Windows computer. A Linux desktop, X11, `DISPLAY`, and `sudo` are unnecessary. [LINUX_SETUP.md](LINUX_SETUP.md) gives the exact commands. The server listens only on the Linux computer's loopback interface and creates a new access token on every launch.

The browser interface includes representative traces; E/I model selection; per-parameter values, bounds and distributions in both the values and search tabs; conductance switches; published-channel assemblies; custom algebraic and ODE current modules; paired random draws and Cartesian sweeps; CPU process selection; progress and stop controls; equations; CSV output; and previewable or downloadable PDF traces. Results are stored on the compute host and remain available after restarting the browser app. The existing `paper_app.py` is retained as an optional local Tk desktop interface.

It includes the Tatsuki AN, Sato NAN/FNAN, Yoshida SAN, and Yamada RAN equation sets. E/I is a research assignment, not a cell-specific fit. The current release simulates individual cells rather than an E–I network. Custom modules and assemblies are exploratory definitions. No AI service is needed to run the app.

Every model runs for 20 seconds and classifies only the last 10 seconds. Default search counts are 100 per parameter; for two parameters this gives 100 paired random conditions or 10,000 sweep conditions. **Apply to all** changes the counts together. Random seeds are generated and saved automatically. Search jobs can use multiple CPU processes. Saved plots are vector PDF; Arial is used when installed on the compute host, with a fallback font otherwise.

The equation and parameter sources are listed in [PAPER_PROVENANCE.md](PAPER_PROVENANCE.md).
