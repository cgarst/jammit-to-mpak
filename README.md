# jammit-to-mpak

Converts a Jammit song folder (`.jcf`, or a zip of one) into a [Music Pack](https://github.com/cgarst/MusicPackSpec)
(`.mpak`) file: the notation as PDF, the audio as FLAC, and the cursor data as sync points, so a reader can follow the
score along with the recording.

```
pip install -e .
jcf2mpak "Dream_Theater_Under_a_Glass_Moon_guitar.jcf.zip"          # writes "Dream Theater - Under a Glass Moon.mpak"
jcf2mpak song_folder -o out.mpak --systems-per-page 7
```

Needs Python 3.10+, Pillow, and `ffmpeg`/`ffprobe` on the PATH (Jammit's audio is AIFF-C IMA ADPCM, which ffmpeg decodes).

## What comes out

| Jammit | Music Pack |
|---|---|
| `<id>_jcfn_NN`, `<id>_jcft_NN` (PNG strips) | one PDF score for each notation and tab set, built from the strips |
| `nowline.nodes` | a `pdf` sync file for each score: every beat gets a page and an `x`, `y` position |
| `beats.plist` | the recording's beat file (`beats/<recording>.json`), with downbeats |
| `sections.plist` | `sections` in the beat file, named from the section codes |
| `<id>_jcfx` (audio) | FLAC; an instrument track becomes a `stem`, "Band" the `backing` recording, with `without` listing the stems it lacks |
| `info.plist` | `song`: title, artist, album, genre, publisher (`publishedBy`), composer (`writtenBy`), year (`copyright`) |
| `tuning.plist` | `instruments` (tuning) on the score |

"Band" is the first recording, so it is the default. The stems and the sync use its timeline (`syncFrom`).

The run prints what it carried, what it left out (cover art, amp settings, `skillLevel`, `sku`, and so on, none of
which has a place in the specification), and any warnings.

## How the cursor data works

`nowline.nodes` (not documented anywhere; read from real files and checked against the rendered notation) holds, for each
notation track, one record for each beat of `beats.plist`: a packed `(system << 16) | bar` and the pixel `x` in the
strip. The strips are one tall image cut into systems of `scoreSystemInterval` pixels. This tool lays `--systems-per-page`
systems on each PDF page with no margins, so a beat's page, `x` and `y` follow exactly. See `src/jammit_to_mpak/nowline.py`.

Section codes were read off the titles printed in the notation of real songs; unknown codes are written as
`Section <code>` with a warning (`src/jammit_to_mpak/sections.py`).

## Limits

- One song folder is one instrument group (guitar, bass, drums, keys or vocals). Folders of the same song are not merged.
- The click and the user track are not files in the folder, so they are not written.
- Audio offsets that other tools hard-code for a few songs are not applied.

## Tests

```
pip install -e ".[test]"
MPAK_SPEC_DIR=/path/to/MusicPakSpec pytest
```

The tests build a tiny synthetic song folder; the repository holds no real song data. With `MPAK_SPEC_DIR` set (and
`node`), they also run the specification's reference validator on the output.

## License

Mozilla Public License 2.0; see [LICENSE](LICENSE).
