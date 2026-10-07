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
- Unity assets in this build ship WITHOUT type trees for custom
  MonoBehaviours and TMP components, so only the LocalizationText TextAsset
  is handled in the binary pipeline; the rest of the serialized text is
  deferred to a later phase.

## Translation toolkit

Modular EN -> PT-BR pipeline built from the `original-freeinference.md`
master prompt. Structure:

    translate_project.py          CLI entry point
    localization/                 package (config, provider, pipeline...)
      formats/hjson_format.py     StreamingAssets HJSON handler
      formats/textasset_xml_format.py   Unity LocalizationText asset
    tests/                        fake-provider and round-trip tests
    game-profile.json             evidence-backed game profile
    requirements.txt              hjson, UnityPy

Setup (Linux/macOS or Windows with py -3.11):

    python3 -m pip install -r requirements.txt
    cp .env.example .env          # add FREEINFERENCE_API_KEY (freeinference.org)

Commands:

    python3 translate_project.py --dry-run        # unit census, no calls
    python3 translate_project.py --validate-only  # byte round-trip gate
    python3 translate_project.py --self-test      # offline checks
    python3 translate_project.py                  # real run (needs API key)
    python3 translate_project.py --review         # review PTBR output
    python3 translate_project.py --remake         # full retranslation

The provider profile is strict: host freeinference.org, model
deepseek-v4-flash, reasoning effort max by default, streaming on,
3 concurrent requests of up to 40 units. Accepted checkpoints live in
`.checkpoints/` (gitignored) and resume by default (`--no-resume` for a
fresh generation). Output lands in `PTBR/` mirroring the install layout;
`--input`/`--output` override the roots. Never commits the API key: it
resolves from the process environment first, then `.env`.

The HJSON handler edits values by byte span and is proven lossless on the
whole corpus (validate-only returns byte-identical rebuilt files).
