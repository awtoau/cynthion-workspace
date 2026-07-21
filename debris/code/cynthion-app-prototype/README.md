# cynthion-app Prototype Archive

This directory preserves the human-authored parts of an older Flutter app
prototype after removing generated files, platform boilerplate, IDE metadata,
and build output from the archived `debris/cynthion-app` snapshot.

Preserved here:
- `app/lib/` — older app source tree with a distinct UI/state model
- `app/pubspec.yaml` — dependency set for that prototype
- `app/test/` — matching widget test
- `app/analysis_options.yaml` — matching analyzer configuration

Not preserved from the old snapshot:
- `build/`, `.dart_tool/` — regenerable artifacts
- Android/iOS/Linux platform scaffolding and IDE files that were either duplicate or boilerplate
- exact duplicates of current app files such as the top-level app README

Use this archive as historical reference for the older app prototype, not as current source of truth.