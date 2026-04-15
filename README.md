# GitHub Actions Library - Innogando

Reusable **workflows** and **composite actions** for Innogando's CI/CD. Centralizes automation across all repositories to ensure consistency and reduce duplication.

## Reusable Workflows

Called from consumer repos with `uses: Innogando/github-workflows/.github/workflows/<name>@v2`.

### `build-push-image-gcp.yml`

Builds a Docker image and pushes it to GCP Artifact Registry. Used by both production and develop pipelines.

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `image_name` | yes | - | Docker image name |
| `repository` | yes | - | Artifact Registry repository |
| `tag` | yes | - | Image tag (release tag for prod, `develop` for dev) |
| `also_tag_latest` | no | `false` | Also push a `:latest` tag |
| `checkout_ref` | no | `github.sha` | Git ref to checkout |
| `artifact_name` | no | `""` | GitHub artifact to download before build (e.g. Flutter web assets) |
| `artifact_path` | no | `"."` | Path to extract the downloaded artifact |
| `gcp_project_id` | no | `innogando` | GCP project ID |
| `gcp_region` | no | `europe-southwest1` | GCP region |
| `build_args` | no | `""` | Docker build-args (multiline `KEY=VALUE`) |

| Output | Description |
|--------|-------------|
| `image` | Full image reference (`registry/project/repo/name:tag`) |
| `tag` | Image tag used |

| Secret | Required | Description |
|--------|----------|-------------|
| `GCP_SA_KEY` | yes | GCP Service Account JSON key |

### `deploy-manifest-argocd.yml`

Updates a Kubernetes manifest file in Git so ArgoCD syncs the new image. **Use only for production deploys.** For develop environments, use ArgoCD Image Updater instead (see [Deploy Strategy](#deploy-strategy)).

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `manifest_file` | yes | - | Path to manifest (e.g. `manifest/overlays/prod/kustomization.yaml`) |
| `image_tag` | yes | - | New image tag |
| `sed_expression` | yes | - | sed expression with `IMAGE_TAG` as placeholder |
| `target_branch` | no | `main` | Branch where the manifest lives |
| `commit_message` | no | `deploy: IMAGE_TAG [skip ci]` | Commit message (`IMAGE_TAG` replaced) |

| Secret | Required | Description |
|--------|----------|-------------|
| `GH_PAT` | yes | GitHub PAT with repo write access |

### `release-calver.yml`

Creates a CalVer tag and GitHub Release when a PR is merged to main. Used by API projects (rumi-api, cowtrol-api, airflow).

Caller must trigger on `pull_request: types: [closed], branches: [main]`.

| Secret | Required | Description |
|--------|----------|-------------|
| `GH_PAT` | yes | GitHub PAT for tag creation and release |

### `release-semver-pubspec.yml`

Extracts the version from `pubspec.yaml`, creates a `vX.Y.Z` tag and GitHub Release. Used by Flutter apps (rumi-app, cowtrol).

Caller must trigger on `pull_request: types: [closed], branches: [main]`.

| Secret | Required | Description |
|--------|----------|-------------|
| `GH_PAT` | yes | GitHub PAT for tag creation and release |

### `conventional-commits.yml`

Validates that PR commits follow the [Conventional Commits](https://www.conventionalcommits.org/) spec.

No inputs or secrets required.

---

## Composite Actions

Called with `uses: Innogando/github-workflows/<action-name>@v2`.

### `python-linter`

Lints Python code with isort + black.

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `src_paths` | no | `./src` | Paths to lint (space-separated) |
| `python_version` | no | `3.13` | Python version |

### `flutter-linter`

Lints Flutter/Dart code with `dart format` and `flutter analyze`.

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `flutter_version` | no | `3.24.5` | Flutter SDK version |
| `skip_analysis` | no | `false` | Skip `flutter analyze` |

### `flutter-web-build`

Sets up Flutter and builds for web.

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `flutter_version` | yes | - | Flutter SDK version |
| `base_href` | no | `/` | Base href for web build |
| `build_args` | no | `""` | Extra args for `flutter build web` |

### `gcp-docker-auth`

Authenticates to GCP and configures Docker for Artifact Registry.

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `gcp_sa_key` | yes | - | GCP Service Account JSON key |
| `artifact_registry` | no | `europe-southwest1-docker.pkg.dev` | AR hostname |

### `check-build-number-flutter`

Validates that the build number in `pubspec.yaml` was incremented by exactly 1 compared to main.

No inputs required.

### `android-app-build`

Builds a Flutter Android APK and sends it via Telegram.

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `project_name` | yes | - | Project name (shown in Telegram message) |
| `git_crypt_key` | no | - | Base64-encoded git-crypt key |
| `telegram_token` | yes | - | Telegram bot token |
| `telegram_chat_id` | yes | - | Telegram chat ID |
| `flutter_version` | no | `3.24.5` | Flutter SDK version |

### `sync-branches`

Syncs one Git branch into another automatically.

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `source_branch` | yes | - | Branch to sync from |
| `target_branch` | yes | - | Branch to sync to |
| `gh_token` | yes | - | GitHub token |

### Legacy Actions

`docker-deploy` and `web-app-deploy` are kept for SSH-based server deployments (non-Kubernetes). See their `action.yml` for inputs.

---

## Deploy Strategy

### Production (immutable tags)

CI builds and pushes the image with a release tag, then commits the new tag to the manifest file in Git. ArgoCD detects the Git change and syncs.

```
CI: build + push (tag: 2025.6.15-1)
  -> Commit manifest (newTag in Git)
    -> ArgoCD sync
```

### Develop (mutable tags)

CI only builds and pushes the image with a fixed tag like `develop`. **ArgoCD Image Updater** (installed in the cluster) detects the new digest and triggers a sync automatically. No Git commit needed.

```
CI: build + push (tag: develop)
  -> Artifact Registry (new digest)
    -> ArgoCD Image Updater (detects digest change)
      -> ArgoCD sync
```

Required ArgoCD Application annotations for dev environments:

```yaml
metadata:
  annotations:
    argocd-image-updater.argoproj.io/image-list: app=europe-southwest1-docker.pkg.dev/innogando/<repo>/<image>
    argocd-image-updater.argoproj.io/app.update-strategy: digest
    argocd-image-updater.argoproj.io/app.force-update: "true"
    argocd-image-updater.argoproj.io/write-back-method: argocd
```

---

## Usage Examples

### Python API - Production CD

```yaml
name: CD - Build & Deploy
on:
  release:
    types: [published]
  workflow_dispatch:
    inputs:
      release_tag:
        required: true
        type: string

jobs:
  build:
    uses: Innogando/github-workflows/.github/workflows/build-push-image-gcp.yml@v2
    with:
      image_name: my-api
      repository: my-api
      tag: ${{ github.event.release.tag_name || inputs.release_tag }}
      checkout_ref: ${{ github.event.release.tag_name || inputs.release_tag }}
      also_tag_latest: true
    secrets:
      GCP_SA_KEY: ${{ secrets.GCP_SA_KEY }}

  deploy:
    needs: build
    uses: Innogando/github-workflows/.github/workflows/deploy-manifest-argocd.yml@v2
    with:
      manifest_file: manifest/overlays/prod/kustomization.yaml
      image_tag: ${{ needs.build.outputs.tag }}
      sed_expression: 's|newTag:.*|newTag: "IMAGE_TAG"|g'
    secrets:
      GH_PAT: ${{ secrets.GH_PAT }}
```

### Python API - Develop (no commit, ArgoCD Image Updater)

```yaml
name: Build Dev Image
on:
  push:
    branches: [develop]
    paths: [src/**, requirements.txt, Dockerfile]

jobs:
  build:
    uses: Innogando/github-workflows/.github/workflows/build-push-image-gcp.yml@v2
    with:
      image_name: my-api
      repository: my-api
      tag: develop
      checkout_ref: develop
    secrets:
      GCP_SA_KEY: ${{ secrets.GCP_SA_KEY }}
```

### Flutter Web - Production CD

```yaml
name: CD - Build & Deploy
on:
  release:
    types: [published]

jobs:
  build-web:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
        with:
          ref: ${{ github.event.release.tag_name }}
      - uses: Innogando/github-workflows/flutter-web-build@v2
        with:
          flutter_version: "3.41.2"
      - uses: actions/upload-artifact@v7
        with:
          name: web-build
          path: build/web
          retention-days: 1

  build-push:
    needs: build-web
    uses: Innogando/github-workflows/.github/workflows/build-push-image-gcp.yml@v2
    with:
      image_name: my-app
      repository: my-app
      tag: ${{ github.event.release.tag_name }}
      checkout_ref: ${{ github.event.release.tag_name }}
      artifact_name: web-build
      artifact_path: build/web
      also_tag_latest: true
    secrets:
      GCP_SA_KEY: ${{ secrets.GCP_SA_KEY }}

  deploy:
    needs: build-push
    uses: Innogando/github-workflows/.github/workflows/deploy-manifest-argocd.yml@v2
    with:
      manifest_file: manifest/overlays/prod/kustomization.yaml
      image_tag: ${{ needs.build-push.outputs.tag }}
      sed_expression: 's|newTag: ".*"|newTag: "IMAGE_TAG"|g'
    secrets:
      GH_PAT: ${{ secrets.GH_PAT }}
```

### Release (CalVer for APIs)

```yaml
name: Release
on:
  pull_request:
    types: [closed]
    branches: [main]

jobs:
  release:
    uses: Innogando/github-workflows/.github/workflows/release-calver.yml@v2
    secrets:
      GH_PAT: ${{ secrets.GH_PAT }}
```

### Release (SemVer for Flutter apps)

```yaml
name: Release
on:
  pull_request:
    types: [closed]
    branches: [main]

jobs:
  release:
    uses: Innogando/github-workflows/.github/workflows/release-semver-pubspec.yml@v2
    secrets:
      GH_PAT: ${{ secrets.GH_PAT }}
```

### Conventional Commits

```yaml
name: Conventional Commits
on:
  pull_request:
    branches: [main]

jobs:
  validate:
    uses: Innogando/github-workflows/.github/workflows/conventional-commits.yml@v2
```

### CI - Python Lint

```yaml
name: CI
on:
  pull_request:
    branches: [main, develop]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: Innogando/github-workflows/python-linter@v2
```

### CI - Flutter Lint

```yaml
name: CI
on:
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: Innogando/github-workflows/flutter-linter@v2
        with:
          flutter_version: "3.41.2"

  check-build-number:
    runs-on: ubuntu-latest
    steps:
      - uses: Innogando/github-workflows/check-build-number-flutter@v2
```

---

## Versioning

- **`@v1`**: Legacy tag pointing to the previous structure. Existing consumers are not broken.
- **`@v2`**: Current version with reusable workflows and improved composite actions.

All composite actions maintain backward compatibility: new inputs have defaults that match v1 behavior.

## License

MIT License - See [LICENSE](LICENSE) file.
