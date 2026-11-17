# jammit-to-mpak

Converts a Jammit song folder (`.jcf`, or a zip of one) into a [Music Pack](https://github.com/cgarst/MusicPackSpec)
(`.mpak`) file: the notation as PDF, the audio as FLAC (or AAC in an `.m4a`, with `--format m4a`), and the cursor data as sync
points, so a reader can follow the score along with the recording.

[MajesticTab](https://github.com/cgarst/majestictab) is a compatible Music Pack reader.

```
pip install -e .
jcf2mpak "Dream_Theater_Under_a_Glass_Moon_guitar.jcf.zip"          # writes "Dream Theater - Under a Glass Moon.mpak"
jcf2mpak song_folder -o out.mpak --systems-per-page 7
jcf2mpak song_folder --format m4a                                   # smaller AAC audio instead of FLAC
jcf2mpak ~/Jammit/songs -o ~/mpaks                                  # a folder of songs: converts every .zip and .jcf in it
```

Needs Python 3.10+, Pillow, and `ffmpeg`/`ffprobe` on the PATH (Jammit's audio is AIFF-C IMA ADPCM, which ffmpeg decodes).

## What comes out

| Jammit | Music Pack |
|---|---|
| `<id>_jcfn_NN`, `<id>_jcft_NN` (PNG strips) | one PDF score for each notation and tab set, built from the strips |
| `nowline.nodes` | a `pdf` sync file for each score: every beat gets a page and an `x`, `y` position |
| `beats.plist` | the recording's beat file (`beats/<recording>.json`), with downbeats |
| `sections.plist` | `sections` in the beat file, named from the section codes |
| `<id>_jcfx` (audio) | FLAC or M4A (see [Audio format](#audio-format---format)); see [Two modes](#two-modes-default-and---stem) for which tracks the pack gets |
| `info.plist` | `song`: title, artist, album, genre, publisher (`publishedBy`), composer (`writtenBy`), year (`copyright`) |
| `tuning.plist` | `instruments` (tuning) on the score |

In the default mode, "Band" is the first recording, so it is the one a reader plays first. The stems and the sync use its timeline (`syncFrom`).

The run prints what it carried, what it left out (cover art, amp settings, `skillLevel`, `sku`, and so on, none of
which has a place in the specification), and any warnings.

## Audio format (`--format`)

| Value | Result |
|---|---|
| `flac` (default) | Lossless FLAC. The tool writes 4096-sample blocks. Blocks under 256 samples are valid, but macOS WebKit can't decode them, so Safari and the MajesticTab desktop app would fail to play the audio. |
| `m4a` | AAC at 192 kbit/s in an MP4 container, with the index at the start of the file. The files are smaller, but the encoding is lossy. |

Jammit's audio is already lossy, so FLAC doesn't restore quality; it only avoids a second lossy encode. `--format` applies to every recording, including the `--stem` mix. `mpak-join` reads both formats.

## Two modes: default and `--stem`

Jammit sells a song as one zip for each instrument group it released: some of guitar, bass, drums, keys and vocals. Most
songs have all five, but some have fewer. Each zip holds the song with that group's part removed ("Band", the backing
track) and the group's own recording (a stem).

```
jcf2mpak Another_Day_guitar.jcf.zip                           # default: one group
jcf2mpak Another_Day_guitar.jcf.zip --stem                    # --stem: every group (the song's other zips are looked up beside it)
jcf2mpak ~/Jammit/songs --stem -o ~/mpaks                     # a folder: every song whose zips are all in it
```

| | Default | `--stem` |
|---|---|---|
| Input | the one zip or folder you give | every zip the song has, matched by artist and title in `info.plist` |
| Audio tracks | the group's stem or stems, plus a `Band` track (the `backing` recording, with `without` listing the stems it lacks) | a stem for each of the song's groups, and no `Band` track |
| What a reader can do | play along with the group muted, using `Band` | play all stems together as the full mix, or leave out any of them (for example no guitar, or no vocals) |
| Score and cursor | the group's own | from the first zip in the order guitar, bass, drums, keys, vocals |
| Output name | `<artist> - <title>.mpak` | `<artist> - <title> (stems).mpak` |

The tool knows which groups a song has from a built-in copy of the Jammit catalog (`src/jammit_to_mpak/catalog.py`). For a song
not in the catalog it expects all five. If you lack any expected zip, `--stem` refuses the song. In a folder of songs, it
skips that song and says what is missing.

### The `Other` stem

Some sound belongs to no group, such as percussion, strings or effects. With `--stem`, the tool checks whether the
stems leave out part of the first zip's full mix (its `Band` plus its stems). If they do, it renders the difference
(`Band - other stems`) as an `Other` stem (`instrument: "other"`), so all stems together still sum to the full mix.

The tool adds `Other` when the leftover is within 20 dB of the mix. Jammit's audio is lossy, so a leftover a few tens of
dB below the mix is only the codec's noise, and the tool ignores it.

## How the cursor data works

`nowline.nodes` holds, for each notation track, one record for each beat of `beats.plist`: a packed
`(system << 16) | bar` and the pixel `x` in the strip. The strips are one tall image cut into systems of `scoreSystemInterval` pixels. This tool lays `--systems-per-page`
systems on each PDF page with no margins, so a beat's page, `x` and `y` follow exactly. See `src/jammit_to_mpak/nowline.py`.

Section codes were read off the titles printed in the notation of real songs; unknown codes are written as
`Section <code>` with a warning (`src/jammit_to_mpak/sections.py`).

## Joining the parts of a long song (`mpak-join`)

```
mpak-join "X (Pt. I) (stems).mpak" "X (Pt. II, III) (stems).mpak" "X (Pt. IV) (stems).mpak" -o "X (stems).mpak"
```

Glues packs end to end, in the order given. Scores are appended page after page; each part's beats, sections and sync
points move by the length (and pages) of the parts before it; every audio track is the parts' audio joined without a gap
(decoded and re-encoded once, each part trimmed or padded to the timeline recording's length so the stems stay aligned).
A track some part lacks is silent for that part. Scores, tracks and sync files are not added or dropped. A score has one
tuning, so where the parts differ the one with the most strings is written. The title defaults to the first part's
without `(Pt. ...)`; `--title` overrides it.

## Limits

- One song folder is one instrument group (guitar, bass, drums, keys or vocals). Folders of the same song are merged only with `--stem`.
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
