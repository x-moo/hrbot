# Imaams public-data test fixtures

These are checked-in, offline snapshots of real public source data; they are not
constructed from the application's `RECITERS` catalog.

## Homepage source snapshot

- Public source: `http://www.haramain.info/` (the captured page was reached via
  `https://haramainrecordings.blogspot.com/`, which redirected to this URL).
- Fixture: `haramain-info-reciter-section.html` (4,914 bytes).
- Exact snapshot scope: the original HTML fragment from the heading
  `IMAAMS QU'RAN RECITATIONS` through the first following `</ul>`.
- Raw fetched page used to make the fragment: `.cache/imaams-research/35d1a470a0a8.body`
  (2,503,652 bytes), SHA-256 `3101811da038c34d0c4f9a82511ab3ea3890ab73a9a8de8a4e9918699bdcb0a2`.
- Checked-in fragment SHA-256: `00cbb35b49ddbaf54fa400c179b42d5c9a08c1a8847f1eebd1a8e8262586cc1c`.
- Extraction method: Python `HTMLParser` reads `<a>` elements whose `href` starts
  with `https://archive.org/details/`, whitespace-normalizes their visible text,
  URL-decodes the identifier, counts all links, and deduplicates by identifier
  in source order (last occurrence retained). The real section contains 39
  links and 38 unique identifiers. The test compares all deduplicated entries
  to the application catalog.

## Internet Archive metadata snapshot

- Public item: `https://archive.org/metadata/HaramainQuraafi` (identifier
  `HaramainQuraafi`).
- Fixture: `archive-metadata-quraafi.json` (11,070 bytes).
- Raw fetched JSON: `.cache/imaams-research/archive-metadata-quraafi.json`
  (55,584 bytes), SHA-256 `985918c614ba5e28d9d103cbca1d3616cacc38fc177cbdc007de84d9116fbbf5`.
- Extraction method: retain the real item's public descriptive fields
  (`identifier`, `mediatype`, `creator`, `description`, `title`, `publicdate`,
  `addeddate`) and the original audio file records only (`source == "original"`
  with an `.mp3`, `.m4a`, `.ogg`, or `.wav` filename). Keep the catalog fields
  `name`, `source`, `title`, `length`, `size`, and `track` when present; omit
  irrelevant file metadata and derivative artwork/spectrogram entries. This
  preserves all 62 matching original audio records from the downloaded payload.
- Checked-in compact fixture SHA-256:
  `1bd3b53f5d20714c07416be17da311ed92af4fd782dda74c66896c6b24067721`.

The `IMAAMS_SOURCE_SNAPSHOT` and `IMAAMS_ARCHIVE_METADATA` environment variables
may point tests at alternate snapshots. Without overrides, tests always use the
checked-in fixtures and do not skip when cache files are missing.
