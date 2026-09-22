# SUNNY — The Growing Sunflower Lamp

Direction track: **Physical Design**

## Short description
A sunflower-inspired lamp that grows with your stories. SUNNY connects private memories and customizable exteriors with a traceable path from digital design to a physical object, making a personal gift that can evolve over time.

## What it does
Creators save memories privately, prepare text or image references, generate or import an exterior draft, preview and adapt its geometry, and review immutable manufacturing versions. Fabrication records attach photos and measurements to the exact version used. A simulated device flow demonstrates NFC binding, outfit changes, and lighting controls. Creators can share a selected design version for remixing while retaining private memories and references.

## Implementation
Python, SQLite, JavaScript/WebGL, Tripo API adapter, OpenSCAD CAD, STL assets, and ESP32/PlatformIO firmware. Local parametric generation works without an API key. The Tripo adapter includes submission limits, input confirmation, task recovery, and source/version provenance.

## Evidence and limits
This submission describes a software MVP and physical-design prototype. Current repository evidence covers automated software tests and browser flows. Hardware assembly, real NFC, thermal/optical tests, and real paid Tripo outputs remain unverified. Synthetic geometry and simulated devices are labeled accordingly. No partner tool track is claimed solely on the basis of an unverified integration.

## Review the project
- Source: https://github.com/RSXLX/sunny-the-growing-sunflower-lamp
- Runnable demo: local startup instructions in the repository README.
- Screenshots: evidence/2026-09-22/fabrication-regression/ and geometry-browser/.
- Detailed progress: docs/mvp-v3/05-软件开发进度.md.

## Submission status
Prepared for submission; this file is not proof of acceptance or successful submission. The official form must confirm receipt. Process video and any mandatory visual board must be supplied if requested by the form.
