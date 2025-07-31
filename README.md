# GitHub Workflows – Innogando

This repository contains reusable GitHub Actions workflows for Innogando's projects. It serves as a centralized library of CI/CD automation scripts, aiming to standardize and simplify development processes across all repositories within the organization.

## Purpose

- ✅ Promote reuse of workflows across multiple projects.
- 🚀 Streamline CI/CD pipelines and improve deployment efficiency.
- 🛠️ Reduce duplication and ease maintenance of automation logic.

## Structure

- `.github/workflows/` – Contains reusable workflows or examples.
- `templates/` – Optional directory for composite actions or reusable job definitions.
- `docs/` – Documentation for workflow usage (if needed).

## Usage

You can reference these workflows in other repositories using the `uses` keyword:

```yaml
jobs:
  cd:
    uses: Innogando/github-workflows/docker-deploy@v1
```

Make sure to replace the path and filename with the specific workflow you want to use.

## Contribution

If you need a new workflow or want to update an existing one:

1. Create a new branch.  
2. Add or modify the workflow file.  
3. Open a pull request with a clear description of the changes.

## License

MIT License
