# Branching & Promotion Strategy

- **Feature branches (`feat/*`)**: Develop features here; open PRs targeting `dev`.
- **Integration (`dev`)**: Staging branch where all CI checks must pass before promotion.
- **Production (`main`)**: Merged only from `dev` via approved PR. Direct pushes to `main` are restricted.
