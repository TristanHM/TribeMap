# TribeMap

A map and turn planner for [TribeNet](http://tribenet.com.au/), the play-by-email game. It runs in your web browser:
there's nothing to install.

**Open TribeMap: https://tristanhm.github.io/TribeMap/**

**Read the guide: https://tristanhm.github.io/TribeMap/guide/** (a step-by-step tutorial with pictures)

## What it does

- **Builds your map from your turn reports.** Import the results files the GM emails you (one, several, or a whole
  folder) and TribeMap draws every hex your tribes and scouts have seen: terrain, rivers, finds, special hexes and
  where your units are. It reads Clan Cartographer / Worldographer `.wxx` maps too, such as one an ally sends you.
- **Plans your turn.** Draw moves and scout routes on the map with the movement points worked out step by step, so
  you can see how far each one really gets before you send your orders. It can suggest scout routes that reach the
  most unknown ground, and exports your plan as a sheet to paste into your orders.
- **Looks back.** Replay the map as it was at any earlier turn, or show where each tribe and element has been over a
  range of turns.
- **Shares.** Export your map as a `.wxx` file for Clan Cartographer or Worldographer. Your units and plans are never
  included.

## Getting started

1. Open https://tristanhm.github.io/TribeMap/ (the first visit takes a little while to load).
2. Click **Import (.docx/.wxx)** and choose your turn results, or **Folder (.docx/.wxx)** to bring in a whole folder.
3. Your map appears. Pick next turn's plan in the **Plan** list and start drawing routes.

The [guide](https://tristanhm.github.io/TribeMap/guide/) walks through each step.

## Your data stays with you

Nothing is uploaded. TribeMap runs entirely in your browser and keeps your reports and plans there, on your own
computer. Because of that, clearing your browser's data or switching computers starts you afresh, so use
**Save data** now and then to keep a copy (and **Open data…** to load it again).

TribeMap never sends anything to the GM and never changes your orders file.

## Questions and problems

Found a bug, or something doesn't read your report properly? Open an
[issue](https://github.com/TristanHM/TribeMap/issues).

## For developers

TribeMap is written in Python and runs in the browser under [Pyodide](https://pyodide.org/). To build the site and try
it locally (Python 3.12+):

```
python webapp/build.py site
python -m http.server -d site 8000      then open http://localhost:8000/
```

## Licence

MIT: free to use, change and share, as long as the copyright notice stays (see `LICENSE`). Made by
[TristanHM](https://github.com/TristanHM). TribeMap is a fan-made tool and isn't part of TribeNet.
