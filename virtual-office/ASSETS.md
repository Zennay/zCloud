# Assets and licences

**No external image, model, texture or font file is used.** The city, sky, materials, furniture, people, slides and icons are generated in code at load time.

The previous Wikimedia skyline photograph (CC BY-SA 4.0) has been removed from the repository, the deploy workflow and the server; its attribution is therefore no longer needed.

## Third-party code
- **three.js r170** and the add-ons used for post-processing (`EffectComposer`, `GTAOPass`, `UnrealBloomPass`, `OutputPass`, `RoundedBoxGeometry`) are vendored under `vendor/`. MIT licence, text in `vendor/LICENSE-three.txt`. Served from the Office server itself (importmap, allow-listed paths), never from a CDN.
- The desktop app depends on Electron, `ssh2` and a Whisper runtime; see `desktop/package.json` and their own licences.
