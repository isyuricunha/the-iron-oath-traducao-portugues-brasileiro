# The Iron Oath - Portuguese (Brazil) Translation

Working repository for the fan translation of The Iron Oath (GOG release,
Curious Panda Games) to Portuguese (Brazil).

## Source

Files copied from a clean GOG install (`D:\GOG Games\The Iron Oath` on the
`ysinot` machine). Game data uses Unity 2019.4.20f1 with the Mono scripting
backend. The directory layout mirrors the game install so translated files
can be dropped back in place.

## Contents

- `The Iron Oath_Data/StreamingAssets/Data/` - plain HJSON data files read
  by the game at runtime (dialogue, world sim, dungeons, items, systems,
  guild, actors). This is the primary translation surface (~119k chars of
  English prose across ~1.85k strings). Note: the files are HJSON (unquoted
  keys, trailing commas), not strict JSON.
- `The Iron Oath_Data/resources.assets` - serialized assets holding UI and
  tooltip text (~65k chars, TextMeshPro rich text).
- `The Iron Oath_Data/level*.assets` / `sharedassets*.assets` - scenes and
  shared assets with baked UI strings.
- `The Iron Oath_Data/globalgamemanagers*` - engine managers (context only).
- `The Iron Oath_Data/Managed/Assembly-CSharp*.dll` - game code with ~24k
  chars of embedded strings (mixed with developer logs).

## Deliberately excluded

- `*.resS` / `*.resource` binary blobs (900 MB video/audio bundle included)
- `StreamingAssets/Data/Worldmap` (map icons, no text)
- Engine binaries (`UnityPlayer.dll`, `MonoBleedingEdge/`), launcher files

## Localization notes

- The shipped `StreamingAssets/Data/Localization/*.properties` files are a
  dead system: 5 UI keys and a bundled JLokalize editor. Ignore them.
- The game is officially English-only. UI uses TextMeshPro with a pixel
  font (Perfect DOS VGA 437) that lacks Brazilian accent glyphs (ã, õ, Ã,
  Õ); a font replacement or atlas regeneration is required.
