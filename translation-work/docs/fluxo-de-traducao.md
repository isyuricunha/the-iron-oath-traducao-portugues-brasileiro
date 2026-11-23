# Fluxo de Trabalho da Tradução — Passo a Passo

Este guia descreve a sequência operacional recomendada para conduzir a tradução manual em `translation-work/translations/` até a compilação final de `PTBR/`.

## 1. Preparação após `git clone` ou `git pull`

1. Entre na raiz do repositório e execute o diagnóstico único de leitura (`doctor`):
   ```bash
   python3 -B translation-work/toolkit.py doctor
   ```
   *(Equivalente direto: `python3 -B translation-work/tests/smoke-linux-arm64.py`.)*
2. Se `doctor` retornar código diferente de `0`, pare imediatamente e reporte a mensagem exata retornada (ex.: `MISSING_RUNTIME`, `MISSING_DEPENDENCY`, `CHECKSUM_MISMATCH`, `UNSUPPORTED_ARCHITECTURE`) antes de mexer nos lotes.
3. Consulte o andamento atual e a próxima entrada pendente (este comando não carrega o runtime `.NET` nem altera arquivos):
   ```bash
   python3 -B translation-work/toolkit.py status
   ```
   *(Apelido idêntico: `python3 -B translation-work/toolkit.py resume`.)*

## 2. Tradução manual e aplicação transacional (`apply`)

1. Abra apenas os lotes relevantes em `translation-work/translations/batch-*.jsonl` ou monte um arquivo JSON temporário de carga (`payload`) contendo apenas `generation` + lista `entries` (`id`, `source_fingerprint`, `PTBR`).
2. Formato do payload para `apply`:
   ```json
   {
     "generation": "8401583119a33309f88d5af54cbd7ac325d89bbff8857ec68e1f06a18b45318b",
     "entries": [
       {
         "id": "codex_xml:The Iron Oath_Data/StreamingAssets/Data/Codex/codex.xml:journals:name:0",
         "PTBR": "Diários"
       }
     ]
   }
   ```
3. Aplique as alterações de forma transacional (com snapshot de rollback automático e gravação apenas nos lotes alterados):
   ```bash
   python3 -B translation-work/toolkit.py apply --payload caminho/do/payload.json
   ```
   - Por padrão, entradas já preenchidas não são sobrescritas (`OVERWRITE_DENIED`); passe `--replace` somente quando quiser revisar e substituir uma tradução já existente.
   - Se editar algum arquivo `.jsonl` manualmente, **nunca** altere os campos `id`, `row`, `EN`, `key`, `format`, `source_file` ou `source_fingerprint`. Edite exclusivamente o valor de `"PTBR"`.

## 3. Verificação contínua e atualização do jogo base

1. Após traduzir um conjunto de lotes, valide integridade das fontes `EN/`, tokens protegidos, sintaxe e segurança IL na DLL:
   ```bash
   python3 -B translation-work/toolkit.py check
   ```
2. Quando o jogo receber uma atualização na Steam/GOG e os arquivos originais em inglês forem atualizados localmente, importe e sincronize os lotes sem perder nenhuma tradução já pronta:
   ```bash
   python3 -B translation-work/toolkit.py update-sources --game-root "/caminho/para/The Iron Oath" --dry-run
   python3 -B translation-work/toolkit.py update-sources --game-root "/caminho/para/The Iron Oath"
   ```
   Use `python3 -B translation-work/toolkit.py diff` sempre que quiser revisar o último relatório gerado em `translation-work/reports/sync-latest.md`.

## 4. Compilação final, conferência e publicação

1. Quando `status` confirmar `"pending": 0` (100% das entradas preenchidas) e `check` passar sem erros:
   ```bash
   python3 -B translation-work/toolkit.py build
   ```
   O comando valida 100% das entradas, aplica a conversão sem acentos para compatibilidade de fontes (use `--keep-diacritics` apenas para testes de fonte), gera todos os arquivos em `PTBR/` e grava o recibo `translation-work/build-manifest.json`.
2. Valide o pacote final e, se desejar inspecionar o arquivo compacto localmente:
   ```bash
   python3 -B translation-work/toolkit.py verify-output
   python3 -B translation-work/toolkit.py package
   ```
3. Faça o commit das alterações (`translation-work/translations/`, `PTBR/`, `translation-work/build-manifest.json` e eventuais atualizações de `EN/`) e envie com `git push`. O fluxo do GitHub Actions validará novamente com `verify-output` e publicará automaticamente uma nova Release SemVer com `PTBR.zip` e notas de versão categorizadas.
4. Opcionalmente, caso o diretório local `.state/transactions/` cresça após centenas de edições locais, você pode inspecionar ou podar snapshots antigos com segurança:
   ```bash
   python3 -B translation-work/toolkit.py gc --dry-run
   python3 -B translation-work/toolkit.py gc
   ```
