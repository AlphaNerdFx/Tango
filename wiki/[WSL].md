# WSL Setup

Running Tango in WSL with Anki on Windows requires extra configuration because WSL2 and Windows run in separate network namespaces.

## The problem

AnkiConnect binds to `127.0.0.1:8765` by default. This means it only accepts connections from the same machine. From inside WSL2, `localhost` does not reach Windows services -- they are on different virtual networks.

## Fix 1: Change AnkiConnect bind address

In Anki, go to Tools -> Add-ons -> AnkiConnect -> Config. Change:

```json
"webBindAddress": "127.0.0.1"
```

to:

```json
"webBindAddress": "0.0.0.0"
```

Restart Anki after saving. This makes AnkiConnect listen on all network interfaces, including the one visible from WSL.

## Fix 2: Find your Windows host IP

From inside WSL, run:

```bash
ip route | grep default
```

The IP shown (e.g. `172.28.144.1`) is your Windows host as seen from WSL.

Test the connection:

```bash
curl http://172.28.144.1:8765
```

You should see `{"apiVersion": "AnkiConnect v.6"}`.

## Fix 3: Update your .env

```
ANKI_HOST=http://172.28.144.1:8765
```

Replace the IP with your own from step 2.

## Note on IP stability

The WSL gateway IP can change when Windows restarts or WSL is reset. If Tango stops connecting to Anki after a restart, re-run `ip route | grep default` and update `ANKI_HOST` in your `.env`.

A permanent fix is available on Windows 11 23H2 and later using WSL mirrored networking mode, which makes `localhost` work transparently between WSL and Windows. Enable it by adding `networkingMode=mirrored` to `%USERPROFILE%\.wslconfig` and restarting WSL.

## Auto-import limitation

The auto-import prompt at the end of a pipeline run sends the .apkg file path to AnkiConnect using the Linux path format (`/mnt/c/...`). Windows AnkiConnect cannot read this path. Import the file manually instead: File -> Import in Anki, then navigate to the `output/` folder in your project directory.