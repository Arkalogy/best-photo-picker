# Changelog

All notable changes to Best Photo Picker are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/) and
this project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.2] — 2026-10-24

### Fixed
- **Faces are grouped far more accurately, with each face model.** All
  three face models shared one grouping distance (0.80); on 365 hand-labelled
  family photos (889 faces of 74 people) it put most people together with
  someone else on SFace, and nearly everyone into two groups on dlib. Each
  model now has its own measured default (SFace 0.70, dlib 0.40, the
  optional research-only model 0.75), and a value picked in Regroup is
  kept for that model only, so a
  model switch never inherits a number on another model's scale. Of the
  face pairs grouped together, the share that are one person went from
  14% to 84% (SFace), 2% to 92% (dlib) and 77% to 94% (research-only); a
  person may be split over a few groups, which Merge fixes. dlib has no
  setting that is both clean and complete on family photos, so a library
  grouped with dlib now shows a note in Settings suggesting SFace.
- **Analysis groups faces the way Regroup does.** New faces joined the
  group whose average face was nearest, a looser test than Regroup's
  (near the group's faces on average), so analysis mixed people that
  Regroup kept apart, and much more so when photos arrived in small
  batches. Both now use the same rule.
- **Two faces of one photo grouped together are no longer deleted.** After
  grouping, every face of a photo that shared a group with another face of
  that photo was deleted, meant for one face detected twice. When two
  people were grouped by mistake, one of them lost that face for good (on
  dlib, more than half of all faces). Overlapping boxes are still dropped
  as a double detection; any other face now gets a group of its own.
- **Corrections no longer loosen grouping or outlive their groups.**
  Merges could raise the learned distance past the point where strangers
  merge; corrections now only make grouping stricter. They are cleared
  when the groups are rebuilt (Regroup, Retry, a model switch), since they
  name groups by numbers that then belong to other people.
- **Re-cluster and switching the face model work in the Mac app.** The
  app's build left out the part of a scientific library that groups faces
  (it is loaded indirectly, which the packager cannot see), so in the Mac
  app Re-cluster failed and a face-model switch removed the faces and then
  could not group the new ones. The build now includes it, refuses to build
  without it, and a test keeps every indirectly loaded module in the build.
- **A photo where the face model finds no face is not marked unreadable.**
  When SFace found no face, a second try with dlib ran; where dlib is not
  installed (the Mac app) or its license is not accepted, that try failed
  and marked the photo unreadable, which also kept people's names from
  being placed after a model switch. A photo without a face found by SFace
  now simply has no face, and an SFace library no longer gets dlib's
  vectors, which are on another scale.
- **People keep their names when clusters are renumbered.** Re-clustering
  (a new threshold) and switching the face model both give clusters new
  numbers. The names were moved one at a time while the new numbers
  reused the old ones, so two people who swapped numbers lost both
  names, and a name could land on a different person. Albums now move
  in one order-independent pass, and a name whose cluster has no match
  is kept, with its manual tags, as a person without faces rather than
  left on a stranger or deleted; the activity feed says so. Two people
  with equally strong evidence are not guessed; a person kept this way
  is placed later only by photos where they are the only face. A
  re-clustering renumbers the faces and moves the names in one database
  transaction, so one that is interrupted or cannot move a name changes
  nothing.
- **A face-model switch keeps every name, not just the first chunk's.**
  The switch re-extracts faces in batches of 250 photos; only the first
  batch knew which photos each named person had, so people who appeared
  later lost their names. The names are now parked in the same database
  transaction as the wipe and placed once, from the complete evidence,
  when the last batch is done (people show as "Person N" until then), so
  a group photo seen early cannot leave a name on the wrong person. While
  a switch is still placing names,
  every face edit (re-cluster, merge, split, dismiss, tags, face boxes)
  and a person rename answer 409 (`blocker: face_switch`) until the
  analysis has run to its last batch; a name that still matched no
  cluster is kept, with its manual tags, as a person without faces (not
  shown until the next refresh finds their photos), and the activity feed
  says so.
- **People found in different batches of one face run no longer merge.**
  Face extraction works in batches of 250 photos; each batch numbered
  its new clusters without knowing the clusters earlier batches had
  made, so a person first seen in a later batch could take another
  person's number. Each batch now clusters its new faces against the
  whole library.
- **A model switched off for analysis shows as "Off" and can be turned
  back on.** Libraries set up on 0.1.x could carry a stored "skip pets /
  content filter / CLIP" switch from the old Settings list; analysis
  honoured it, the model picker showed the model as ready, and no screen
  could flip it back. The picker now shows "Off — skipped in analysis"
  for such a model and offers "Use in analysis" / "Skip in analysis" in
  its row menu. The install confirms now name the packages they will
  install, and that package list is owner-only like the rest of the
  install state.
- **An import survives being interrupted.** The database rows were
  written only after the whole copy, and quitting the desktop app kills
  the server after three seconds, so an import stopped late left
  thousands of copied files with no record, and the next import copied
  them all again under numbered names. Rows are now written as the copy
  goes, a file an interrupted import had copied is recorded instead of
  copied again, Cancel stops the copy between files and keeps what is
  done, and half-copied `.tmp` files are cleaned up.
- **A camera RAW with saved edits is shown edited.** The full-size view
  and the thumbnail of a RAW file showed the plain conversion; the edits
  step never ran for that format. Edits now apply on top of the
  conversion, as for every other format (the editor still gets the
  original), and saving an edit no longer throws the slow RAW conversion
  away.
- **Import takes camera RAW photos and videos.** The app's Import picked
  up only JPEG, PNG and HEIC files: a folder of RAW photos or videos
  imported nothing ("0 found"), with no setting to change it. Import,
  scan and analysis now take every supported photo, RAW and video format
  by default; `scan_extensions` in the config file still narrows it,
  now for `bpp analyze` too (it used to ignore the file's list). The short
  motion clip an iPhone Live Photo exports next to each photo
  (`IMG_4214.HEIC` + `IMG_4214.MOV`) is skipped like the other Live Photo
  sidecars unless "Import Live Photo motion sidecars" is on.
- **A camera RAW exports as JPEG, PNG or at a smaller size.** Those export
  choices opened a RAW with the plain image reader, which can't decode
  it, so the photo failed the export. They now use the RAW reader, as the
  default export did; a RAW comes out as JPEG unless PNG is chosen. With
  metadata kept, the RAW's date, camera and location go along (a maker
  note too large for a JPEG is left out), and a smaller export decodes the
  RAW at half size.
- **A resized HEIC export no longer keeps its location.** Exporting a HEIC
  in its original format at a smaller size kept the owner's name, GPS
  location and other metadata although metadata removal was on, and the
  export reported it clean. Removal now drops all of it for every format,
  and a resized HEIC comes out as JPEG, as the metadata-removal copy
  already made it (writing HEIC could also crash the app).
- **Exports keep the colour profile and, when asked, all the metadata.**
  Converted or resized JPEG and WebP exports lost the photo's colour
  profile (wide-colour phone photos came out dull). With metadata kept,
  converted photos lost their XMP (captions, credits) and resized TIFFs
  lost their EXIF. With metadata removal on, the full-size copy and the
  gallery thumbnails kept a photo's embedded comment, and a CMYK photo
  couldn't be exported as PNG. Every exported image now keeps its colour
  profile (unless its pixels change colour space), and keeps or drops the
  EXIF, XMP and comment together, as chosen, PNG included. A resized
  export in the original format now uses the chosen quality (it was
  always 75).
- **RAW photos stay marked as RAW.** Scoring a photo blanked the RAW mark
  the import gave it, so imported RAW photos were missing from the RAW
  filter, and the library statistics counted no plain photos. A photo's
  RAW and video marks now always follow its file type.

## [0.2.1] — 2026-10-23

### Fixed
- **The Mac app starts.** The 0.2.0 Mac build did not: macOS refused to
  let its signed server load the Python runtime it unpacks (library
  validation under the hardened runtime), so the app never reached its
  server. The app now carries the entitlements a PyInstaller build
  needs, and the release pipeline runs the signed server from the
  installer before anything is uploaded. Install 0.2.1 over 0.2.0.
  The Python package was not affected.

## [0.2.0] — 2026-10-23

Licensing, accuracy and hardening release. **Schema version: 44** (unchanged).

### Security
- **LAN sharing.** The server checks the request's `Host` before anything
  else, so a page on another site can't reach it through DNS rebinding.
  Pairing a phone needs the share link's token and is rate-limited; a
  paired device is recognised by its cookie. Local file paths are removed
  from every API response a paired device receives, and export progress
  is visible to the owner only. Progress streams re-check that the
  device is still trusted while they run.
- **The app page is locked to the window that opened it.** Any program
  on the computer could read the session token off the page at
  `127.0.0.1:5001`. `bpp serve`, `bpp web` and `bpp demo` now print a
  link with a one-time key and open it for you; without the key the page
  only says the app is open elsewhere. The Mac app handles the key
  itself. For scripts, Docker and opening the page by hand, pass
  `--open-loopback` (or set `BPP_OPEN_LOOPBACK=1`) to get the old open
  page; the key never appears in the server log. The Mac app now loads
  the page from `127.0.0.1` rather than `localhost`, so its window
  settings (theme, the update-check switch, filters) reset once on this
  upgrade.
- A paired device sees `@ext/…` instead of the real folder for photos
  that live outside the library, and a plain "storage unreachable"
  instead of the error text that named the library path, whatever the
  folder is called (a folder named like one of the app's URLs, such as
  `/static` or `/shared`, is hidden the same way).
- The Mac app hands its one-time key only to a server that proves it
  is the app's own, after a restart as well as at launch; a port taken
  by another process while the server was down gets nothing. At launch
  it reclaims the port only from a leftover server whose window is
  gone, never from another running copy's.
- `--debug` on `bpp serve`, `bpp web` and `bpp demo` turns on verbose
  logging only; it never enables the Werkzeug debugger.
- Archives you analyze are unpacked with Python's `data` filter on
  3.11.4 and later (it was 3.12+ only), so a crafted `.tar` can't write
  outside its folder. A camera RAW that declares more than 200 million
  pixels is refused like an oversized JPEG instead of being decoded.
- A pairing request nobody approves expires 24 hours after the phone
  first asked; a model download with no pinned checksum is refused and
  the reason shown.
- "Open folder" refuses application bundles, even ones renamed to hide
  their suffix.
- **Export with "remove metadata" really removes it.** HEIC and camera
  RAW files are converted to JPEG for the strip, files whose metadata
  can't be removed are reported as failed instead of copied with it,
  and the summary says how many files (videos, linked files) kept
  theirs.

### Added
- **Plugins work like the built-ins.** A plugin detector's or scorer's
  on/off switch appears in Settings → ML Models and is honoured by the
  run; a plugin face embedder can be picked; a plugin scorer's weight
  can be adjusted per album; a smart album type can set its own icon,
  tooltip and sidebar position; `register_post_cluster_hook` callbacks
  fire for pet, near-duplicate and manual face clustering; a plugin can
  register how its model bundle downloads (`register_catalog_loader`).
  The plugin guide (`docs/plugins.md`) covers each of these.
- Keyboard access to People, Pets, Groups, Memories and On-this-day
  cards; the model pickers announce themselves as dialogs and return
  focus when closed.
- Tag, calendar and duplicate-group lists accept `limit` / `offset`.
- Plugins create their smart-album rows with
  `bpp.db.smart_albums.ensure_smart_album`, which honours a user's
  dismissal.

### Changed
- A minor rather than a patch bump: this release drops the `--seed` flag and
  locks the page behind a boot key by default, both of which change how
  scripts call it.
- **Large libraries.** Face clustering of more than 8,000 faces runs in
  blocks with bounded memory; the face detector is built once per
  thread; new faces are matched to known people in one pass. The People
  list, Groups, the Top Rated refresh and Live Photo tagging at startup
  no longer read every embedding or every photo. Cache cleanup, the hash
  backfill, thumbnail generation and the duplicate pass do less work per
  photo.
- **SCRFD face detector is now opt-in.** Earlier releases downloaded it by
  default and labelled it MIT. InsightFace's terms say its trained models are
  for non-commercial research only, so SCRFD now follows the same path as
  other restricted models: it downloads and runs only after you accept it in
  Settings → Models, and with a "Commercial" use context it stays blocked
  unless you state you hold separate rights. YuNet is the default face
  detector. In our testing on 400 photos, faces were found in the same photos
  as before; a few small or distant faces were missed.
- **NudeNet content filter no longer installs NudeNet's code.** BPP runs the
  downloaded model with its own code (identical scores in our 150-photo
  comparison),
  so the `[nudity]` extra is gone and the Mac app no longer bundles NudeNet.
  The model weights still download only after you accept them, and are now
  correctly labelled AGPL-3.0 (they were labelled GPL-3.0).
- **dlib face weights are now treated as restricted.** Their landmark model was
  trained on a dataset whose creators ask that it not be used commercially, so
  it gets the same click-through and "Commercial" rules as other research
  models, and the Mac app no longer includes dlib.
- **Model acceptance text updated.** It now says models come from third-party
  hosts rather than "directly from the upstream source". Restricted models you
  accepted before show "Re-accept needed" in Settings → Models and stay off
  until you accept again.
- **Mac app:** the DMG itself is now notarized, not only the app inside it.
  Each desktop release also attaches an inventory of the app's Python side
  (packages, their native libraries, the Python runtime and the PyInstaller
  bootloader) and the exact HEIC/RAW library sources; NOTICE.txt adds a
  written offer for the source of any bundled GPL/LGPL component.

### Fixed
- **Every file an export writes is a new file of its own.** A link
  planted in the export folder at the name of a photo's temporary file,
  a report, the manifest, the gallery, a thumbnail or the zip bundle no
  longer redirects the write into whatever it pointed at: the file is
  created exclusively and the planted entry is replaced.
- Importing a folder scores the new photos with the stored model
  switches and detection thresholds, as Analyze does, instead of the
  scorer's defaults. The "minimum face area" setting now reaches the
  scorer on both paths; it was read as its default before.
- Cancelling an export stops it after the photo being written; the
  photos already exported stay, the manifest, report, gallery and zip
  bundle are skipped, and the progress stream ends with `cancelled`
  rather than `done`.
- BPP Picks keeps the "Max photos per month" cap when it relaxes the
  per-day cap to reach the requested count.
- Permanent delete renames each selected file to a hidden sibling
  before anything is removed and deletes only recycle-bin rows, so a
  crash at any point loses nothing: a photo whose row survived
  (restored in another tab, or the delete never reached the database)
  gets its file back, never over another file, and a file put at the
  same path later is never mistaken for the deleted one. A file the OS refuses to remove is retried at the next start (a
  few times, then logged by name) and listed under `files_failed`.
- BPP Picks recomputes its duplicate pass when a photo's average hash
  changes, not only its perceptual hash or score.
- The test suite never resolves the owner's default library folder.
- **Library stays intact through storage blips and renames.** A short
  NAS outage no longer marks the whole library missing; renaming the
  open library keeps the session on it; a failed final write at the end
  of an analysis is reported instead of the run claiming success;
  resuming an interrupted face extraction runs in the background
  (shown in `/api/v1/health`) instead of blocking startup.
- Re-analysing a photo keeps its checksum, import batch and original
  file name; exported copies are upright (camera orientation applied)
  and keep their metadata unless you chose to strip it; the automatic
  purge of long-deleted photos is journaled, so a crash halfway through
  finishes on the next start instead of leaving half the files.
- Right-click menus built at runtime ran none of their items; they work
  again and return keyboard focus. A video trim can no longer overwrite
  another file, and a failed trim leaves no stray file. Exported
  galleries and CSV files escape photo names.
- The People list loads in pages, the duplicate review loads every
  group, and the Tags and Calendar views page their lists, so large
  libraries open them without a stall.
- While an interrupted face extraction resumes after a restart, Analyze,
  Import and face edits wait ("Face extraction is resuming after a
  restart") instead of racing it; a failed resume and a refused
  missing-file scan show in `/api/v1/health`; an analysis helper that
  refuses a job shows its own reason instead of "crashed; see
  server.log".
- Folders named `data` or `cache` inside an import source are scanned
  again; only bpp's own folders at the library root are skipped. The
  automatic restore after a corrupt start brings the backup's newest
  rows along.
- Recluster refuses past the face-embedding cap, as merge and restore
  do, instead of risking an out-of-memory stop.
- Mac app: the Report Issue and Get the latest version buttons on its
  error screens work; the phone pairing page's Open library button works.
- Late responses no longer overwrite newer state: a slow face list
  can't show the previous photo's faces, a slow face-outline save can't
  end a newer edit, and the import finishes correctly even when nobody
  is watching its progress.
- Keyboard: person menu shortcuts no longer also trigger lightbox
  actions, the calendar leaves inputs and dialogs alone, and "E" on a
  card menu no longer opens Export.
- Failures that used to be silent now say what failed and why: face
  recluster, renaming a person during review, import and analysis stream
  errors, and MediaPipe inference errors (logged).
- `/api/v1/health` reports install state, download jobs and the hash
  backfill.
- The command-line `select` uses the same duplicate detection as the
  app, including plugin strategies.
- Docker: the health check always failed (it hit an endpoint that needs a
  token), and the container could not save downloaded models. Models and model
  acceptances now live on the `/data` volume.
- SCRFD ignored `BPP_CACHE_DIR` / `BPP_MODELS_DIR`.
- Mac app: MediaPipe face detection and the last-resort face detector failed
  to load inside the app, its helper processes crashed on start, and the
  in-app "install face recognition" offer could never work there. The
  detectors now load, the helpers start, and the offer is hidden in the app.
- Fresh `pip install "bppicker[faces]"`: analysis could stop partway while
  still reporting success, because newer setuptools no longer includes a
  module the dlib face models need. The `[faces]` extra now pins
  `setuptools<81`, and bpp carries on without dlib if it still can't load.
- The BlazeFace full-range face detector never ran on fresh installs because
  its runtime (`ai-edge-litert`) wasn't declared; it is now part of `[faces]`
  (except on Intel Macs, which have no build of it).
- Settings → Models labelled the default SFace and YuNet models "Restricted";
  they only ask you to acknowledge attribution terms, and now say
  "Attribution".
- Three Settings sliders showed the wrong starting values (duplicate
  sensitivity, burst gap, across dates); they now match the real defaults.
- License links in the model dialogs that returned 404.
- Videos are scored with the face confidence and minimum face size from
  Settings, and face grouping runs only the detectors switched on there,
  as photo scoring already did. The scoring helper's loss is noticed
  within a second rather than at its five-minute message timeout.
- Switching sharing off ends a paired phone's open progress streams.
  After a crash, the batch-rename recovery leaves alone the renames
  the database had already recorded. A failed near-duplicate backfill
  at startup shows in `/api/v1/health`.
- `bpp db restore-backup` works on exFAT and SMB volumes (its lock no
  longer needs hard links). Album config is validated on create as on
  update; a video trim with a bad time range is a 400, not a 500, and
  updates the row however the path was spelled; renaming the folder of
  an unmounted library is refused instead of renaming the entry only.
- Removed the `--seed` flag, the recompute `seed` parameter and the
  "Selection RNG seed" setting: selection was always deterministic, so
  none of them did anything.
- Final review sweep. Mac app: Restart Server and Quit now stop the
  server itself (a bare kill left it holding the port, so a restart
  could never succeed); the error screens' buttons work on the loaded
  page; a stale "update required" verdict is cleared once the server is
  healthy. Web page: every reload, including Cmd+R and the overlay's
  Reload, first checks that the listener on the port is this app's
  server and refuses to load anyone else's page; the reconnect overlay
  clears on a locked server; the first-run wizard's library choice no
  longer leaves the tab refused on its next edit. Data: exporting a
  photo onto itself is refused; a folder that once received symlink
  exports accepts later exports; "Restore All" and friends work past
  32,000 photos; a serving lock is removed only by the server that
  holds it; a batch of photos finishing during a hash backfill is
  hashed too; clearing the library waits with one deadline and refuses
  while background work is still finishing. Performance: the preview
  grid shows its first page at once and the server reads one page per
  request; the global duplicate pass uses a band index instead of
  comparing every representative. Trimming a video refuses playlists
  the same way decoding does. Failed batch actions no longer show a
  success message.

### Documentation
- Corrected statements that didn't match the code or the repo settings,
  including:
  - the network calls list, which now covers the model-registry check that
    runs whenever bpp starts (turn it off with `BPP_DISABLE_REMOTE_REGISTRY=1`);
  - registry signing: one trusted key, not two;
  - that code-owner review is not enforced;
  - the one model file that ships in the package (BlazeFace, Apache-2.0);
  - which download hosts are third-party re-hosts;
  - test counts and Docker status;
  - plugin loading;
  - the API reference.
- Correction to the 0.1.0 notes below: the network list there omits the
  model-registry check, and NudeNet is AGPL-3.0, not GPL-3.0.

## [0.1.2] — 2026-10-13

Patch release. **Schema version: 44** (unchanged).

### Fixed
- **BPP Picks could show too few photos on first use.** Right after the app
  opened, switching to an album or person and turning on BPP Picks could show
  a stale, too-small set (e.g. 1 photo instead of 13) until a refresh. A slow
  library-wide recompute was overwriting the album's selection; only the newest
  result for the view on screen is applied now. (#29)

### Changed
- README: demo GIF, a clear note that the first run downloads ~650 MB of models
  once, plus "How it's built" and "Known limits" sections. (#30)
- The macOS app is now built on GitHub's macos-15 runner (macos-14 is being
  retired). No change to the app itself. (#31)

## [0.1.1] — 2026-09-04

Patch release fixing a broken-on-fresh-install bug. **Schema version: 44.**

### Fixed
- **Fresh `pip install` was broken by OpenCV 5.** `opencv-python-headless`
  was unpinned (`>=4.8`), so new installs pulled OpenCV 5.0, which removed
  the bundled Haar cascade data the face-detection fallback loads. On a
  fresh install this made the fallback raise, and the analyze worker
  (which catches per-photo errors) then silently skipped every photo that
  reached the fallback — i.e. any photo with no face detected by the
  primary detectors. Now pinned to `>=4.8,<5`. (#21)
- **Duplicate "Person" smart albums under concurrent refresh.** Two
  smart-album refreshes running at once (e.g. a face action and a
  background clustering pass) could each create the same person album.
  Added a `UNIQUE(album_type, rule_json)` constraint (schema v44, with a
  self-healing migration that de-dupes existing albums) plus conflict-safe
  album creation. (#21)

## [0.1.0] — 2026-06-23

First public release. **Schema version: 34.**

### Added

- Manual Face-draw flow for missed faces, with identity stickiness and duplicate guards.
- Activity-tab Copy-log button.
- End-to-end fixture sentinel guard for mutating Playwright helpers.
- Photo-deletion lifecycle hook registry for plugins.

### Changed

- Vectorized CLIP semantic deduplication to cut large-library Pick-count recompute latency.
- Sidebar folders and per-album filters persist across refresh and album switches.
- Toolbar Pick count commits on blur, Enter, and arrow-step changes instead of every keystroke.
- Recently Deleted and Hidden photo APIs are paginated.
- Storage health checks pause while the tab is hidden.
- Recompute reports elapsed time in stats and user-visible toasts for noticeable runs.

### Fixed

- Boot restore waits for the album list before reopening the saved album.
- Update checker surfaces real network/GitHub errors instead of reporting falsely up to date.
- Face, batch-rename, compare, pet, logging, parser, and IO hardening from the pre-release audit pass.
- Release-gate fixes: Activity-log filtering, Prettier formatting, filter persistence, face-purge counts, deletion-hook affected IDs, CLIP-dedup peak memory, cv2 decode-size cap, and download-chokepoint test isolation.

### Highlights

- **Local-first photo curation.** Score every photo on sharpness,
  lighting, faces, and composition, then pick the best ones to
  share. No cloud account, no telemetry — your photo bytes never
  leave your machine for analysis, scoring, or selection. (See the
  README → Privacy section for the full list of network calls bpp
  does make: model downloads on first analyze, OpenStreetMap tiles
  when you open the Map view, default-on update check against
  GitHub Releases.)
- **Flask web UI** with a vanilla-JS single-page app, plus an
  optional native macOS desktop wrapper via Tauri v2.

### Features

- **Scoring pipeline** — blur (Laplacian + LoG), exposure, faces
  (SCRFD + BlazeFace + dlib + Haar fallback with confidence-weighted
  NMS), composition (rule-of-thirds + leading lines + saliency).
- **Deduplication** — dual perceptual hashing (dHash + aHash) plus
  optional CLIP-embedding semantic similarity.
- **Face clustering** — group photos by who's in them, name people,
  boost selections by named person. When a face is mis-localized
  (e.g. a busy pattern triggers a phantom detection while the real
  face goes unboxed), click the wrong bbox in the lightbox and drag
  it onto the real face — the embedding is re-extracted and the
  person is re-matched automatically.
- **Pet detection** (cats / dogs) via YOLOv11n.
- **Near-duplicate clustering** — burst shots and visually similar
  photos grouped by perceptual hash distance (hamming ≤ 8, 60-second
  time window). The Duplicates smart album lets you step through groups
  and keep the best.
- **Live Photo sidecar filtering** — iPhone `_N` sidecar files are
  detected and excluded from all user-facing counts and albums so
  library sizes reflect real photos, not metadata duplicates.
- **Smart albums** — manual + 16 auto-curated types (person, time,
  score, duplicates, pets, screenshots, documents, edited photos,
  recents, hidden, deleted, "needs review", and more).
- **Library management** — import via copy or symlink, batch
  rename, soft delete with auto-purge after 30 days, restore from
  backup.
- **Editing** — non-destructive crops, rotation, exposure / contrast
  / saturation adjustments, plus optional AI inpainting (LaMa) for
  removing objects.
- **Export** — original / hardlink / symlink / re-encode at chosen
  quality, with optional XMP sidecar and JSON manifest.
- **Search & navigation** — full-text tag search, calendar view,
  map view (geo-tagged photos on OpenStreetMap tiles), "On This
  Day," memories.

### LAN sharing & security

- **Optional LAN sharing** — toggle in Settings → Share. The owner
  approves each new device via QR-code pairing; trusted devices
  reconnect silently across restarts.
- **Per-boot session token** for the local Tauri / browser, plus a
  persistent share token for paired LAN devices. All API endpoints
  are gated.
- Loopback-only bind by default; flips to all-interfaces only when
  the share toggle is on at startup.
- Token redaction in logs (active + rotated + in-memory ring
  buffer), including bare-token assignments in subprocess
  tracebacks and home-directory paths in PII-sensitive log lines.
- HTTP-only by default — designed for trusted LAN. For untrusted
  networks, run behind a TLS-terminating reverse proxy and set
  `BPP_TRUSTED_PROXIES`.

### ML & data integrity

- All bundled and downloaded model weights are SHA-256 pinned and
  verified on cache hit.
- **Atomic backups** before every mutating schema migration, with
  WAL/SHM siblings, integrity verification of the copy, and
  timestamped quarantine of corrupt copies. Restore CLI refuses to
  overwrite the live DB from a corrupt source and refuses to
  proceed when a stranded quarantine sibling is present.
- Forward-only schema migrations; the recovery path is
  `bpp db restore-backup`.

### Extensibility

- Public registries for face detectors, face embedders, scoring
  fields, smart-album types, background workers, and config
  schema.
- Optional plugin auto-loading via setuptools entry-points
  (`bpp.plugins`). **Off by default** — opt in with
  `BPP_ENABLE_PLUGINS=1`. See `docs/plugins.md` for the trust
  contract and authoring guide.

### Optional features (opt-in extras)

- `bppicker[heic]` — HEIC / HEIF support.
- `bppicker[faces]` — face recognition (dlib + scipy + mediapipe).
- `bppicker[nudity]` — NSFW detection (NudeNet, GPL-3.0).
- `bppicker[raw]` — camera RAW formats (rawpy / LibRaw).
- `bppicker[inpaint]` — LaMa AI object removal (heavy: pulls torch).
- `bppicker[web]` — Flask SPA backend (most users want this).

### Network calls

External calls are limited to documented features:

- Model downloads from public hosts (HuggingFace, GitHub releases,
  Azure CDN, OpenStreetMap tile servers) on first use.
- Update check against GitHub Releases (owner-only, default-on,
  256 KB response cap, can be disabled in Settings → App).
- LAN-share traffic to paired devices, never traversing the
  internet.

### Platform scope (unchanged through 0.2.0)

- macOS-first. The Flask backend runs on Linux too; the Tauri
  desktop wrapper currently builds only the macOS bundle.
- Python 3.11 only (CI tests this version; 3.12+ is unvalidated).

[Unreleased]: https://github.com/Arkalogy/best-photo-picker/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/Arkalogy/best-photo-picker/releases/tag/v0.2.2
[0.2.1]: https://github.com/Arkalogy/best-photo-picker/releases/tag/v0.2.1
[0.2.0]: https://github.com/Arkalogy/best-photo-picker/releases/tag/v0.2.0
[0.1.2]: https://github.com/Arkalogy/best-photo-picker/releases/tag/v0.1.2
[0.1.1]: https://github.com/Arkalogy/best-photo-picker/releases/tag/v0.1.1
[0.1.0]: https://github.com/Arkalogy/best-photo-picker/releases/tag/v0.1.0
