# Branching & Promotion Strategy

## Pipeline Enforced: `(feat|fix|refactor|...) -> dev -> main`

1. **Feature / Fix Branches (`feat/*`, `fix/*`, `refactor/*`, `chore/*`, `test/*`, `docs/*`, `perf/*`, `ci/*`)**:
   - Todo desenvolvimento deve ser feito a partir de uma branch com prefixo padronizado criada a partir de `dev`.
   - Pull Requests devem obrigatoriamente apontar para a branch `dev`.
   - PRs que apontarem diretamente para `main` serão rejeitados automaticamente pelo CI.

2. **Integration (`dev`)**:
   - Branch de homologação contínua onde todos os testes e builds de CI devem passar.
   - Pushes diretos para `dev` são bloqueados pelo hook local `pre-push`. Mudanças entram apenas via Pull Request.

3. **Production (`main`)**:
   - Código promovido apenas a partir de `dev` via Pull Request aprovado.
   - Pushes diretos para `main` são bloqueados pelo hook local `pre-push` e monitorados pelo CI.

## Configuração de Git Hooks Locais

O repositório inclui hooks locais na pasta `.githooks/` para prevenir acidentes de push direto:
```bash
git config core.hooksPath .githooks
```
*(Essa configuração também é executada automaticamente ao rodar `start.bat`).*
