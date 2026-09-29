# Linux server and Windows browser

## Start the app on the Linux server

In the extracted folder on the Linux server:

```bash
cd ~/Sleep_Channel1.0-prime
bash run_linux.sh
```

If dependencies are missing, run `bash install_linux.sh`. It uses the existing `channel-prime` micromamba environment if present, or Python 3.11+ with `venv`; it does not require `sudo` or Tkinter. For environments without either, install micromamba in your home directory and rerun the installer. To install without starting immediately: `bash install_linux.sh --no-launch`.

Keep this terminal open. It prints a full URL such as `http://127.0.0.1:8501/?token=...`; copy that exact URL, including the token. Results are stored on the server in `~/ChannelCircuitLab/WebRuns/`. You can use `bash run_linux.sh --port 8502` if 8501 is occupied.

## Open it from Windows

Open a **second Windows Terminal** window, in PowerShell or Command Prompt, and run:

```text
ssh -N -L 8501:127.0.0.1:8501 your_user@your_server
```

Replace `your_user` with your Linux login and `your_server` with its hostname or IP address. Leave the SSH window open. Open the **full URL printed by the server** in your Windows browser; the address before the token is `http://127.0.0.1:8501/`. Use the same port number on both sides if you changed it. Do not use `ssh -X` or `ssh -Y`: the browser UI uses the ordinary SSH connection.

The token is required to access the app. The web server binds to `127.0.0.1` on Linux and does not expose a public listening port. Close the SSH tunnel to disconnect; press Ctrl+C in the Linux terminal to stop the app. After a restart, use the newly printed token URL.

For an existing micromamba environment, `run_linux.sh` uses `~/.local/share/micromamba/envs/channel-prime/bin/python` directly, so `conda run` is unnecessary. You can also launch explicitly with that Python path: `~/.local/share/micromamba/envs/channel-prime/bin/python web_app.py`.
