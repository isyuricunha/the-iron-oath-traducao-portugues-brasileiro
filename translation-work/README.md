# Workspace de Engenharia e Tradução (`translation-work/`)

Esta pasta reúne toda a infraestrutura de extração, verificação, sincronização, construção de saída (`PTBR/`), testes e publicação automatizada da tradução PT-BR de *The Iron Oath*.

## 1. Matriz de plataformas e runtimes

| Componente | Windows (desenvolvimento) | Ubuntu Server 24.04 LTS ARM64/aarch64 (tradução / manutenção) |
| :--- | :--- | :--- |
| **Python >= 3.10** (biblioteca padrão apenas) | **Verificado no ambiente** (`3.14.8`) | **Esperado compatível; não verificado nesta máquina** (pendente do primeiro `doctor` no servidor) |
| **Biblioteca Python externa (`UnityPy`, `hjson`)** | Não requerida pelo toolkit em produção | **Zero dependências PyPI** (parsers Unity v21 e HJSON 100% em stdlib Python) |
| **`.NET 8.0+ Runtime` (`dotnet`) + `DllStringPatch.dll` + `Mono.Cecil.dll`** | **Verificado no ambiente** (`.NET 8 / 10` em `PATH` + binários em `vendor/DllStringPatch/`) | **Esperado compatível via `dotnet-runtime-8.0` ARM64; não verificado nesta máquina** (execução real pendente do primeiro `doctor` após `git clone`) |
| **Comandos leves (`status`, `resume`, `apply`, `verify-output`, `package`, `gc`, `recover`, `rollback`)** | **Verificado no ambiente** (não invocando `dotnet`) | **Esperado compatível** (puro Python) |

> **Observação importante:** instalar o pacote `.NET 8 Runtime` no Ubuntu ARM64 por si só **não** comprova compatibilidade antes do teste real. O único passo de validação em campo necessário no servidor ARM64 é executar o preflight `doctor` uma vez após o `git clone` / `git pull`. **Não** há suposição ou exigência de acesso remoto, SSH ou runner ARM64 na máquina Windows de desenvolvimento.

### Como `DllStringPatch` funciona de forma portátil (`Mono.Cecil`)

- Código-fonte C#: `translation-work/dotnet/DllStringPatch/` (`net8.0`, pacote `Mono.Cecil 0.11.5`).
- Pacote gerenciado portátil pré-compilado: `translation-work/vendor/DllStringPatch/` contendo `DllStringPatch.dll`, `DllStringPatch.deps.json`, `DllStringPatch.runtimeconfig.json`, `Mono.Cecil.dll`, módulos transitivos (`Pdb`/`Rocks`/`Mdb`) e `LICENSE.Mono.Cecil.txt`.
- O lançador do toolkit invoca `dotnet translation-work/vendor/DllStringPatch/DllStringPatch.dll ...` diretamente com caminhos explícitos e determinísticos, sem depender de `MONO_PATH`, busca global no GAC ou recompilação pelo servidor ARM64.

---

## 2. Sequência padrão após `git clone` / `git pull` (servidor Ubuntu ARM64)

1. Entre na raiz do repositório clonado (`cd the-iron-oath-traducao-portugues-brasileiro`).
2. Execute o **único comando de preflight somente leitura** antes de iniciar a tradução em lote:
   ```bash
   python3 -B translation-work/toolkit.py doctor
   ```
   - Verifica Python >= 3.10, arquitetura (`x86_64` / `aarch64`), integridade em camadas (`runtime-support.json` → `toolchain.json` → `engine-shapes.json` → código-fonte e binários vendored usando normalização LF de Git), integridade dos arquivos originais em `EN/`, disponibilidade do runtime `dotnet`, carregamento de `Mono.Cecil.dll` contra `smoke-sample.dll` e ida-e-volta sintética de todos os 7 formatos nativos sem alterar nenhum arquivo do projeto.
   - Se `doctor` falhar (`MISSING_RUNTIME`, `MISSING_DEPENDENCY`, `CHECKSUM_MISMATCH`, `UNSUPPORTED_ARCHITECTURE`, `MALFORMED_TEST_DATA`), copie a saída exata e **pare antes de editar traduções** para correção no repositório.
3. Confira o progresso atual (`status` não requer `dotnet` nem altera arquivos):
   ```bash
   python3 -B translation-work/toolkit.py status
   ```
4. Leia os guias de trabalho em [translation-work/docs/briefing-do-jogo.md](./docs/briefing-do-jogo.md), [glossario-ptbr.md](./docs/glossario-ptbr.md) e [diretrizes-de-traducao.md](./docs/diretrizes-de-traducao.md).
5. Traduza as entradas pendentes (`PTBR: ""`) nos arquivos `translation-work/translations/batch-*.jsonl` ou monte payloads JSON por lote/arquivo e aplique com:
   ```bash
   python3 -B translation-work/toolkit.py apply --payload caminho/do/payload.json
   ```
   *(Adicione `--replace` apenas quando precisar sobrescrever intencionalmente uma entrada já traduzida.)*
6. Rode periodicamente a validação estrutural completa e de segurança da DLL:
   ```bash
   python3 -B translation-work/toolkit.py check
   ```
7. Se uma atualização do jogo chegar na Steam/GOG, atualize os originais `EN/` e sincronize os lotes com:
   ```bash
   python3 -B translation-work/toolkit.py update-sources --game-root "/caminho/de/The Iron Oath" --dry-run
   python3 -B translation-work/toolkit.py update-sources --game-root "/caminho/de/The Iron Oath"
   ```
   Consulte as diferenças em `python3 -B translation-work/toolkit.py diff`.
8. Quando todas as entradas pendentes atingirem 100% de conclusão (`pending == 0`) e `check` passar sem erros, construa a saída instalável final `PTBR/`:
   ```bash
   python3 -B translation-work/toolkit.py build
   ```
9. Execute a conferência final do artefato e empacote opcionalmente o `PTBR.zip` local para inspeção:
   ```bash
   python3 -B translation-work/toolkit.py verify-output
   python3 -B translation-work/toolkit.py package
   ```
10. Envie `translation-work/translations/`, `PTBR/`, `translation-work/build-manifest.json` e eventuais atualizações de `EN/` em commit e faça `git push` para disparar a publicação automática do lançamento no GitHub Releases.

---

## 3. Referência de comandos da CLI (`toolkit.py`)

| Comando | Ação | Requer `.NET`? |
| :--- | :--- | :---: |
| `doctor` | Preflight de leitura (hashes Git-LF, runtime, fixtures e parsers) | Sim |
| `status` / `resume` | Progresso de entradas, lotes, próxima pendência, estado de `PTBR/` e uso de `.state/` | Não |
| `extract` | Extração inicial de `EN/` para `translation-work/translations/` | Sim |
| `sync [--source-root \| --source-file \| --dry-run]` | Reconcilia nova fonte original preservando `PTBR` já traduzido | Sim |
| `update-sources [--game-root \| --dry-run]` (apelido `update`) | Autodetecta instalação Steam/GOG (ou valida `--game-root`) e chama `sync` | Sim |
| `diff [--source-root \| --source-file]` | Visualiza relatório de alterações de fontes sem escrever lotes | Sim quando informa fonte; Não sem fonte |
| `apply --payload <arquivo> [--replace]` | Aplica payload JSON apenas onde houve alteração, com snapshot para rollback | Não |
| `check [--skip-dll-usages]` | Valida hashes `EN/`, tokens/tags/delimitadores e ausência de comparações lógicas em `Assembly-CSharp.dll` | Sim (exceto `--skip-dll-usages`) |
| `build [--keep-diacritics]` | Exige 100% traduzido e constrói atomicamente `PTBR/` + `build-manifest.json` | Sim |
| `verify-output` | Guardião rápido de CI (hashes `EN/`, `translation-work/translations/`, `PTBR/` e sintaxe) | Não |
| `package [--output <zip>]` | Valida saída e gera `PTBR.zip` determinístico para distribuição | Não |
| `recover [--break-lock]` | Recupera transação interrompida a partir do jornal em `.state/` | Não |
| `rollback [--snapshot <txid>]` | Reverte a última transação completa registrada em `.state/` | Não |
| `gc [--dry-run]` | Poda snapshots antigos concluídos respeitando lock e limites locais | Não |

---

## 4. Transações, rollback e limites de armazenamento (`.state/`)

- Todas as operações de escrita (`extract`, `sync`, `apply`, `build`) usam escrita durável com lock (`translation-work/.state/lock.json`), jornal prévio e retenção automática limitada a **no máximo 10 snapshots concluídos** e **512 MiB** de teto total local (preservando margem mínima de **4 MiB** e bloqueando remoção de snapshots protegidos, jornais interrompidos, locks ativos ou recibos corrompidos).
- `apply` grava exclusivamente os arquivos `batch-*.jsonl` realmente modificados (evitando reescrever lotes intactos e mantendo diffs limpos no Git).
- `translation-work/.state/` é ignorado no Git (`.gitignore`) e nunca deve ser versionado.

---

## 5. Política de zero manutenção rotineira do `README.md`

- O `README.md` público na raiz usa redação atemporal voltada ao jogador final e links perenes para `/releases` e **`PTBR.zip`** (sem contagens internas de strings/lotes, sem URLs presas a tags `vX.Y.Z`, sem afirmações de "em andamento" e sem nomes de ferramentas ou agentes).
- Cada novo build completo em `PTBR/` publicado na branch principal aciona `.github/workflows/release.yml` e gera notas de versão automáticas no GitHub Releases via `translation-work/release.py`, sem precisar editar manualmente o `README.md` a cada atualização.

---

## 6. Estado atual migrado (`v1.0.021` de referência)

- **Entradas totais:** `15.794` em `32` lotes (`batch-0001.jsonl` a `batch-0032.jsonl`).
- **Migradas inicialmente do acervo pré-toolkit:** `14.779` concluídas (`i2_mono`: `2.211`, `scene_mono`: `199`, `dll`: `1.519`, `textasset_xml`: `36`, `hjson`: `1.403`, `yarn`: `9.411`).
- **Pendentes para tradução manual:** `1.015` entradas (`876` do `Codex/codex.xml`, `107` de `hjson` incluindo rótulos recém-expostos e 2 entradas com tokens corrigidos por omissão prévia, e `32` linhas/ações pendentes em `yarn`).
- **Dicas de acentuação em texto herdado (`check`):** `3.454` entradas herdadas da antiga compilação sem diacríticos trazem aviso diagnóstico não bloqueante até eventual revisão ortográfica.
