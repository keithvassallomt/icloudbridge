# iCloudBridge

A small window into Apple's walled garden. Keep your Apple Notes, Reminders, Passwords and Photos in sync with other services. Mainly targeted towards Nextcloud (reminders, notes, photos) and Bitwarden/Vaultwarden (passwords) - however, can easily be used for any other services too.

> iCloudBridge is in no way affiliated with or endorsed by Apple, Inc.

## Features

- 🗒️ Apple Notes can be synced with a folder containing Markdown files. If using Nextcloud, this can then be synced to your instance and used via the Nextcloud Notes app. Supports images, URLs, attachments, folders and checkboxes (TODO items).

- 📋 Apple Reminders can be synced with a CalDAV server, such as the one provided by Nextcloud. Supports due dates, notes, folders, recurring reminders & completion status.

- 📸 Apple Photos update. This one is quite specific to Nextcloud. When the Nextcloud app is installed on iOS, it can automatically upload photos to Nextcloud - however - it is a one-way sync. Photos added on Nextcloud are not synced back to your Apple Photos library. iCloudBridge fixes this by downloading new photos from Nextcloud and adding them to Apple Photos.

- 🔐 Apple Passwords can be synced with Bitwarden or Vaultwarden, or optionally Nextcloud passwords. Supports all common fields and TOTP codes. This is a manual sync, but iCloudBridge tries to make it as easy as possible.

## Limitations

- iCloudBridge requires a macOS machine to run on. The machine does not need to be always on, but the syncs will only run when it is powered on (duh!). A macOS VM is also possible, but not officially supported.

- Apple Notes: Whilst TODO items are supported, there is a limitation that when a todo item is checked (i.e. marked as completed), iCloudBridge cannot directly update the note to reflect this (this is currently not possible - believe me, I've tried). Instead, iCloudBridge will prepend a ✅ to the start of the line. This is not ideal, but is the best that can be done for now. Sub-folders are also only partially supported at this time, but this may be improved in future.

- Apple Photos: Only new photos added to Nextcloud are synced to Apple Photos. Deletions or modifications are not synced.

- Apple Passwords: At this time, only manual syncs are supported. No automatic or scheduled syncs. Passkeys are also not synced at this time. It is also recommended to use Bitwarden or Vaultwarden, as Nextcloud Passwords does not support TOTP. 

## Installation

Grab the latest release, then double-click!

## Usage

### WebUI
iCloudBridge features a web-based GUI. After launching the app, click on the menubar icon and select "Open WebUI". For documentation on how to use the WebUI, see the [User Guide](docs/user.md).

### Command Line
iCloudBridge can also be run from the command line. For documentation on how to use the command line interface, see the [Usage](docs/USAGE.md) guide.

## Development & Contribution
iCloudBridge is a one-man show - I basically built this to scratch my own itch. However, if you find it useful and would like to contribute, please feel free to open issues or pull requests on GitHub. Here's how to get started with development:

1. Clone the repository

        git clone https://github.com/keithvassallomt/icloudbridge.git

2. Install prerequisites: macOS 13+, Xcode Command Line Tools (`xcode-select --install`), Python 3.11 with [Poetry](https://python-poetry.org/), Node.js 18+ (with `npm`), and [`just`](https://github.com/casey/just). Homebrew users can run `brew install python@3.11 node just` followed by `pipx install poetry`.

3. Install backend dependencies with Poetry:

        cd icloudbridge
        poetry install

   Optional but handy: `poetry shell` drops you into the virtualenv for ad‑hoc commands.

4. Install frontend dependencies once:

        npm --prefix frontend install

5. Run the backend development server (FastAPI + reload) from the project root:

        poetry run dev-server

   Uvicorn listens on `http://localhost:8000` and exposes both `/api/*` and `/api/ws`.

6. In another terminal, serve the React WebUI with hot reload:

        npm --prefix frontend run dev

   Vite proxies `/api` calls to the dev backend, so browsing http://localhost:3000 mirrors the packaged UI.

7. Build the macOS app bundle without a DMG (quick iteration):

        just build

   This runs `scripts/build_release.py --skip-dmg` and leaves `build/Release/iCloudBridge.app` ready to drag/install.

8. Produce the full signed bundle + compressed DMG for distribution:

        just release

   You can forward flags to the build script via `just release "--skip-dmg"` or `just release "--backend-app /path/to/custom.app"`.

9. Before sending a PR, run the quality gates:

        poetry run ruff check .
        poetry run pytest

   Linting/tests run quickly and keep the Briefcase build happy.

## Acknowledgements

iCloudBridge uses some existing open-source code created by the fantastic community. In particular:

- [Apple Cloud Notes Parser](https://github.com/threeplanetssoftware/apple_cloud_notes_parser) - a fantastic project which decodes as much as possible of Apple's arcane Notes format.
- [/u/z1ts](https://www.reddit.com/user/z1ts/) on Reddit, who created a fantastic sample Apple Shortcut for how to add rich content to Apple Notes (see their [Reddit Post](https://www.reddit.com/r/shortcuts/comments/1h54bkh/notes_checkbox_lists_rtf_md_pics_links_etc/)). 

## Disclaimer

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

